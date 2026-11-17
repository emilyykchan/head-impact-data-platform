#!/usr/bin/env python3
"""One-time source-control fix: canonical DS013 IDs on all future SQL ingestion runs.

Default: --check reports whether file needs the fix. --apply patches the SQL.
Never changes any database or private data.
"""
from __future__ import annotations
import argparse
from pathlib import Path

OLD_TO_NEW = {
    "'IMP_DS013_' || s.impact_key":
        "'IMP_DS013_' || split_part(s.impact_key, '-', 1) || '-' || lpad(split_part(s.impact_key, '-', 2), 4, '0')",
    "'IMP_DS013_' || impact_key":
        "'IMP_DS013_' || split_part(impact_key, '-', 1) || '-' || lpad(split_part(impact_key, '-', 2), 4, '0')",
    "NULLIF(s.source_impact_id, '')":
        "CASE WHEN NULLIF(s.source_impact_id, '') IS NULL THEN NULL "
        "ELSE split_part(s.source_impact_id, '-', 1) || '-' || lpad(split_part(s.source_impact_id, '-', 2), 4, '0') END",
}
EXPECTED = (1, 2, 1)


def transform(sql: str) -> tuple[str, bool]:
    counts = tuple(sql.count(old) for old in OLD_TO_NEW)
    if counts[:2] == (0, 0) and all(new in sql for new in OLD_TO_NEW.values()):
        return sql, False
    if counts != EXPECTED:
        raise ValueError(f'Unexpected DS013 SQL file; match counts {counts} vs expected {EXPECTED}, abort.')
    for old, new in OLD_TO_NEW.items():
        sql = sql.replace(old, new)
    return sql, True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sql', type=Path, default=Path('sql/ingest_ds013_rfl.sql'))
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    sql = args.sql.read_text(encoding='utf-8')
    revised, changed = transform(sql)
    if not changed:
        print('Already padded; no changes needed.')
    elif not args.apply:
        print('CHECK OK: DS013 SQL has unpadded impact UID construction. Use --apply to fix (no DB writes).')
    else:
        args.sql.write_text(revised, encoding='utf-8')
        print('Updated DS013 future-ingestion SQL. Review with: git diff sql/ingest_ds013_rfl.sql')

if __name__ == '__main__':
    main()
