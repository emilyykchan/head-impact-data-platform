#!/usr/bin/env python3
"""Migrate DS013 canonical impact IDs to match the original 4-digit source ImpactID.

Safe modes:
  --dry-run (default): inspect existing development DuckDB, never write.
  --create-copy: clone the closed/checkpointed database and migrate ONLY the clone.

Use the private Issue #4 staging CSV to obtain exact source IDs; no guessing
based on row ordering or global numeric padding. DuckDB's FK limitations mean
this is a controlled data migration, not a single UPDATE of a primary key.

Never point this script at the live iMac database. Validate the copied database
before separately deploying the migration to production.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from pathlib import Path
import re
import shutil
import tempfile
import os

DATASET = 'DS013'
EXPECTED_IMPACTS = 492
PREFIX = 'IMP_DS013_'
ALLOWED_CHILD_TABLES = (
    'impact_annotations', 'kinematic_records', 'simulation_runs',
    'biospecimen_samples',
)
ORIGINAL_ID_RE = re.compile(r'^(\d+)-(\d{4})$')
DATABASE_ID_RE = re.compile(r'^(\d+)-(\d+)$')


def canonicalize_identifier(value: str) -> str:
    """Canonicalise comparison key only, never use it as the stored source ID."""
    match = DATABASE_ID_RE.fullmatch(value)
    if not match:
        raise ValueError(f'Unexpected DS013 impact identifier: {value!r}')
    return match.group(1) + '-' + str(int(match.group(2)))


def source_ids_from_staging(path: Path) -> dict[str, str]:
    """{normalised source key: exact zero-padded ImpactID in source Excel}."""
    originals = {}
    seen_subjects = {}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'dataset_id', 'alternate_key', 'source_key'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f'Missing columns: {sorted(required)}')
        for record in reader:
            if record['dataset_id'] != DATASET:
                continue
            original = record['alternate_key'].strip()
            subject = record['source_key'].strip()
            if not ORIGINAL_ID_RE.fullmatch(original):
                raise ValueError(f'Source Excel ImpactID is not XXXX-NNNN: {original!r}')
            if not subject.startswith('MG' + original + '-'):
                raise ValueError(f'Inconsistent source subject and ImpactID: {subject!r}, {original!r}')
            key = canonicalize_identifier(original)
            if key in originals and originals[key] != original:
                raise ValueError(f'Ambiguous original source IDs for {key}: {originals[key]} and {original}')
            if key in seen_subjects and seen_subjects[key] != subject:
                raise ValueError(f'Source ID {key} has multiple subjects: {seen_subjects[key]} vs {subject}')
            originals[key] = original
            seen_subjects[key] = subject
    if len(originals) != EXPECTED_IMPACTS:
        raise ValueError(f'Expected {EXPECTED_IMPACTS} unique source IDs; found {len(originals)}')
    return originals


def plan_mapping(db_rows: list[tuple[str, str | None]], originals: dict[str, str],
                 existing_ids: set[str], expected_impacts: int = EXPECTED_IMPACTS):
    """Validate one-to-one source matching and build the impact UID rename plan."""
    if len(db_rows) != expected_impacts or len(originals) != expected_impacts:
        raise ValueError(f'DS013 must contain exactly {expected_impacts} impacts; found {len(db_rows)}')
    found = set()
    plan = []
    for old_uid, old_source in db_rows:
        if not old_uid.startswith(PREFIX):
            raise ValueError(f'Unexpected DS013 UID format: {old_uid}')
        old_key = canonicalize_identifier(old_uid[len(PREFIX):])
        if old_source is None or canonicalize_identifier(str(old_source)) != old_key:
            raise ValueError(f'UID/source_impact_id conflict: {old_uid} vs {old_source}')
        if old_key in found:
            raise ValueError(f'Duplicate DS013 canonical source key: {old_key}')
        found.add(old_key)
        original = originals.get(old_key)
        if original is None:
            raise ValueError(f'DB ID has no match in exact source IDs: {old_uid}')
        new_uid = PREFIX + original
        if new_uid != old_uid and new_uid in existing_ids:
            raise ValueError(f'Target UID already exists in DB: {new_uid}')
        if new_uid != old_uid or str(old_source) != original:
            plan.append((old_uid, new_uid, original))
    if found != set(originals):
        missing = sorted(set(originals) - found)
        raise ValueError(f'Unmatched original source IDs: {missing[:10]}')
    targets = [p[1] for p in plan]
    if len(targets) != len(set(targets)):
        raise ValueError('New UID collision detected')
    return plan


def q(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ValueError(f'Invalid SQL identifier: {name!r}')
    return '"' + name + '"'


def main_tables(con) -> list[str]:
    return [x[0] for x in con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='main' AND table_type='BASE TABLE'
        ORDER BY table_name
    """).fetchall()]


def dependent_tables(con, tables: list[str]) -> list[str]:
    refs = [r[0] for r in con.execute("""
        SELECT table_name FROM information_schema.columns
        WHERE table_schema='main' AND column_name='impact_uid'
        ORDER BY table_name
    """).fetchall() if r[0] != 'impacts']
    unknown = sorted(set(refs) - set(ALLOWED_CHILD_TABLES))
    if unknown:
        raise ValueError('Unknown tables contain impact_uid; review FK mapping manually: ' + ', '.join(unknown))
    # Check for impacts references whose FK column might not be named impact_uid.
    try:
        constraints = con.execute("""
            SELECT table_name, constraint_text
            FROM duckdb_constraints()
            WHERE constraint_type='FOREIGN KEY'
        """).fetchall()
        for table, constraint in constraints:
            if re.search(r'REFERENCES\s+(?:"?main"?\.)?"?impacts"?\b', constraint, re.I):
                if table not in ALLOWED_CHILD_TABLES or not re.search(r'FOREIGN\s+KEY\s*\(\s*"?impact_uid"?\s*\)', constraint, re.I):
                    raise ValueError(f'Unrecognised impact FK: {table}: {constraint}')
    except (RuntimeError, AttributeError):
        # Older DuckDB versions may not expose the same catalog function.
        # The column guard above still checks all known first-order impact links.
        raise
    return [table for table in ALLOWED_CHILD_TABLES if table in tables]


def table_counts(con, tables: list[str]) -> dict[str, int]:
    return {name: con.execute(f'SELECT count(*) FROM {q(name)}').fetchone()[0] for name in tables}


def audit(con, originals: dict[str, str]):
    tables = main_tables(con)
    if 'impacts' not in tables:
        raise ValueError('Database missing impacts table')
    children = dependent_tables(con, tables)
    db_rows = con.execute("""
        SELECT impact_uid, source_impact_id FROM impacts WHERE dataset_id='DS013'
    """).fetchall()
    all_ids = {r[0] for r in con.execute('SELECT impact_uid FROM impacts').fetchall()}
    plan = plan_mapping(db_rows, originals, all_ids)
    counts = table_counts(con, tables)
    affected = {}
    for table in children:
        affected[table] = con.execute(f"""
            SELECT count(*) FROM {q(table)} AS t
            JOIN impacts AS i ON i.impact_uid=t.impact_uid
            WHERE i.dataset_id='DS013'
        """).fetchone()[0]
    return plan, tables, children, counts, affected


def sql_literal(value: str | Path) -> str:
    """Quote a file path for a DuckDB SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def exported_parquet_paths(export_dir: Path) -> dict[str, Path]:
    """Resolve *main* table Parquet files from DuckDB's generated load.sql.

    Never guess which file corresponds to a table: export names depend on
    DuckDB versions and schemas. Reject unknown formats rather than guessing.
    """
    load = export_dir / 'load.sql'
    if not load.is_file():
        raise ValueError(f'DuckDB export missing load.sql: {load}')
    sql = load.read_text(encoding='utf-8')
    ident = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)'
    patt = re.compile(
        r'\bCOPY\s+(' + ident + r'(?:\s*\.\s*' + ident +
        r')?)\s+FROM\s+\'((?:\'\'|[^\'])+)\'',
        flags=re.I,
    )
    result = {}
    root = export_dir.resolve()
    for match in patt.finditer(sql):
        table_ident = match.group(1).replace('"', '').replace(' ', '')
        parts = table_ident.split('.')
        if len(parts) == 2:
            schema, table = parts
        elif len(parts) == 1:
            schema, table = 'main', parts[0]
        else:
            raise ValueError(f'Unexpected exported table identifier: {table_ident}')
        if schema.lower() != 'main':
            continue
        filename = match.group(2).replace("''", "'")
        file = Path(filename)
        if not file.is_absolute():
            file = export_dir / file
        file = file.resolve()
        if not file.is_relative_to(root):
            raise ValueError(f'Export path outside export directory: {file}')
        if file.suffix.lower() != '.parquet' or not file.is_file():
            raise ValueError(f'Expected exported Parquet file does not exist: {file}')
        table = table.lower()
        if table in result:
            raise ValueError(f'Multiple export files detected for main.{table}')
        result[table] = file
    return result


def corrected_projection(columns: list[str], table: str) -> str:
    """Keep every field unchanged except the source-defined DS013 IDs."""
    if 'impact_uid' not in columns:
        raise ValueError(f'{table} has no impact_uid column')
    out = []
    for col in columns:
        if col == 'impact_uid':
            expr = 'COALESCE(m.new_uid, t.impact_uid)'
        elif table == 'impacts' and col == 'source_impact_id':
            expr = 'COALESCE(m.new_source, t.source_impact_id)'
        else:
            expr = 't.' + q(col)
        out.append(expr + ' AS ' + q(col))
    return ', '.join(out)


def validate_rebuilt(con, originals, before_counts, before_refs,
                     before_constraints, before_indexes):
    remaining, tables, children, counts_after, refs_after = audit(con, originals)
    if remaining:
        raise ValueError(f'Noncanonical IDs remain: {remaining[:5]}')
    # Main counts are checked in audit; separately protect staging/other schemas.
    after_counts = full_table_counts(con)
    if before_counts != after_counts:
        raise ValueError('Table counts changed after rebuild: ' +
                         str({t:(before_counts.get(t),after_counts.get(t))
                              for t in set(before_counts) | set(after_counts)
                              if before_counts.get(t) != after_counts.get(t)}))
    if before_refs != refs_after:
        raise ValueError(f'DS013 impact references changed: {before_refs} vs {refs_after}')
    if before_constraints != constraint_summary(con):
        raise ValueError('Constraint definitions/types changed after rebuild')
    if before_indexes != index_summary(con):
        raise ValueError('Index definitions changed after rebuild')
    for table in children:
        missing = con.execute(f"""
            SELECT count(*) FROM {q(table)} t LEFT JOIN impacts i
              ON t.impact_uid=i.impact_uid
            WHERE t.impact_uid IS NOT NULL AND i.impact_uid IS NULL
        """).fetchone()[0]
        if missing:
            raise ValueError(f'{table} has {missing} orphan impact references')
    print('VERIFIED: all tables, constraints, indexes, DS013 references and source IDs agree.')
    print('DS013 link counts:', refs_after)


def full_table_counts(con):
    return {(schema, table): con.execute(f'SELECT count(*) FROM {q(schema)}.{q(table)}').fetchone()[0]
            for schema, table in con.execute("""
                SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_type='BASE TABLE'
                  AND table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name
            """).fetchall()}


def constraint_summary(con):
    return sorted(con.execute("""
        SELECT schema_name, table_name, constraint_type, constraint_text
        FROM duckdb_constraints()
    """).fetchall())


def index_summary(con):
    return sorted(con.execute("""
        SELECT schema_name, table_name, index_name, sql FROM duckdb_indexes()
    """).fetchall())


def migrate_copy(candidate: Path, originals: dict[str, str], expected_plan):
    """Rebuild a new candidate from a full Parquet export, not FK UPDATEs.

    An UPDATE on an FK-dependent row is rewritten by DuckDB as DELETE+INSERT;
    this can fail when kinematic_waveforms / metrics reference kinematic_uid.
    Export -> rewrite Parquet -> import builds all keys with their final values.
    Original and copied source file are not modified on failure.
    """
    import duckdb
    with tempfile.TemporaryDirectory(prefix='ds013_rebuild_', dir=str(candidate.parent)) as tmp:
        root = Path(tmp)
        export = root / 'export'
        export.mkdir()
        rebuilt = root / 'rebuilt.duckdb'
        con = duckdb.connect(str(candidate))
        try:
            plan, tables, children, _main_counts, refs_before = audit(con, originals)
            if plan != expected_plan:
                raise ValueError('Migration plan changed after database copy; aborting')
            if not plan:
                print('Already canonical; no migration necessary.')
                return
            before_counts = full_table_counts(con)
            before_constraints = constraint_summary(con)
            before_indexes = index_summary(con)
            con.execute('EXPORT DATABASE ' + sql_literal(export) + ' (FORMAT PARQUET)')
            paths = exported_parquet_paths(export)
            changed_tables = ['impacts', *children]
            missing = sorted(set(changed_tables) - set(paths))
            if missing:
                raise ValueError(f'Export missing tables: {missing}; cannot safely rebuild')
            con.execute('CREATE TEMP TABLE ds013_id_map(old_uid VARCHAR, new_uid VARCHAR, new_source VARCHAR)')
            con.executemany('INSERT INTO ds013_id_map VALUES (?, ?, ?)', plan)
            for table in changed_tables:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info({sql_literal(table)})').fetchall()]
                select_columns = corrected_projection(cols, table)
                tmp_parquet = root / f'corrected_{table}.parquet'
                con.execute(
                    'COPY (SELECT ' + select_columns +
                    ' FROM main.' + q(table) + ' t LEFT JOIN ds013_id_map m ON t.impact_uid=m.old_uid)' +
                    ' TO ' + sql_literal(tmp_parquet) + ' (FORMAT PARQUET)'
                )
                os.replace(tmp_parquet, paths[table])
                print(f'Exported corrected main.{table} (other columns unchanged)')
        finally:
            con.close()
        # This is a brand-new DB, so constraints are built over canonical IDs,
        # rather than relying on DuckDB's restricted in-place FK updates.
        with duckdb.connect(str(rebuilt)) as out:
            out.execute('IMPORT DATABASE ' + sql_literal(export))
            validate_rebuilt(out, originals, before_counts, refs_before,
                             before_constraints, before_indexes)
            out.execute('CHECKPOINT')
        # Successful audited replacement of the *disposable candidate* only.
        # The original user DB has still never been opened for writes.
        os.replace(rebuilt, candidate)
        print(f'VERIFIED: {len(plan)} DS013 IDs canonicalised in candidate: {candidate}')

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True, help='Closed local development DuckDB file')
    p.add_argument('--staging-csv', type=Path, required=True, help='Private Issue #4 staging CSV')
    p.add_argument('--create-copy', action='store_true', help='Migrate a new copy, NEVER the original')
    p.add_argument('--mapping-report', type=Path, help='Optional PRIVATE audit CSV for old-to-new IDs')
    args = p.parse_args()
    db = args.db.expanduser().resolve()
    src = args.staging_csv.expanduser().resolve()
    if not db.is_file() or not src.is_file():
        p.error('DB and staging CSV must both exist')
    originals = source_ids_from_staging(src)
    import duckdb
    with duckdb.connect(str(db), read_only=True) as con:
        plan, tables, children, counts, refcounts = audit(con, originals)
    print('DS013 source IDs:', len(originals))
    print('DS013 IDs requiring canonical correction:', len(plan))
    print('Impacts before migration:', counts['impacts'])
    print('Linked rows by table:', refcounts)
    print('Examples:', plan[:5])
    if args.mapping_report:
        report = args.mapping_report.expanduser()
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(['old_impact_uid', 'new_impact_uid', 'new_source_impact_id'])
            w.writerows(plan)
        print('PRIVATE mapping audit:', report)
    if not args.create_copy:
        print('DRY RUN: original database unchanged. Use --create-copy to generate and check a migrated COPY.')
        return
    if not plan:
        print('Nothing to migrate; database already matches source.')
        return
    wal = db.with_name(db.name + '.wal')
    if wal.exists():
        raise SystemExit(f'WAL file exists ({wal}). Close DBeaver and all writers; checkpoint before copying.')
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    candidate = db.with_name(f'{db.stem}_DS013_padded_candidate_{timestamp}{db.suffix}')
    shutil.copy2(db, candidate)
    print('Migrating a disposable copy:', candidate)
    try:
        migrate_copy(candidate, originals, plan)
    except Exception:
        print('FAILED: candidate copy did not pass migration. Original database is unchanged.')
        raise
    print('SUCCESS: original database is UNCHANGED. Candidate:', candidate)
    print('Do not replace the original or update iMac production until reviewing the candidate QC.')


if __name__ == '__main__':
    main()
