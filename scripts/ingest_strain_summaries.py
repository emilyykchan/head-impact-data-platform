#!/usr/bin/env python3
"""Ingest precomputed strain SUMMARY tables, never voxel-level NIfTI data.

Safe defaults: source preflight only; database read-only unless --apply.

Inputs are the original source CSV/XLSX tables. Do not commit them to Git.
Percentile is inferred from source FILENAME: 95Perc => 95; otherwise 90.
Legacy 95Perc column headings still say '90PercStrain' and '90PercStrainRate'.

Usage:
  python scripts/ingest_strain_summaries.py --source-dir /path/to/private/strain-summaries
  python scripts/ingest_strain_summaries.py --source-dir ... --db local/head_impacts.duckdb
  python scripts/ingest_strain_summaries.py --source-dir ... --db local/head_impacts.duckdb \
    --model-name "IC FE model" --mpsr-unit 1/ms --exclude-qc-failures --apply

MPSR input unit was confirmed by the data owner as inverse milliseconds (1/ms).
Values are NOT rescaled.
No simulation runs are guessed or auto-registered by default; see explicit
--register-missing-simulations for summary-only registration after dry-run QC.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

VERSION = "selected-roi-source-summary-import-v3"

# Issue #4 scope: exactly WB, Brainstem, and three corpus-callosum regions.
# All other ROI values, including CC/CC_max and noLR averages, are ignored.
SELECTED_ROIS = frozenset({
    "Whole Brain", "Brainstem",
    "Corpus Callosum Genu", "Corpus Callosum Body", "Corpus Callosum Splenium",
})

DATASETS = {"2": "DS002", "3": "DS003", "4": "DS004", "5": "DS004",
            "9": "DS009", "10": "DS010", "12": "DS012", "13": "DS013"}
PERCENTILES = (90, 95)

# Preserve the distinction between combined CC, its three subregions,
# and CC_max; no averaging, reassignment, or numerical recalculation.
ROI_ALIASES = {
    "CCB": "Corpus Callosum Body", "CCG": "Corpus Callosum Genu",
    "CCS": "Corpus Callosum Splenium", "CGC": "Cingulum Cingulate",
    "CCH": "Cingulum Hippocampus", "UF": "Uncinate Fasciculus",
    "SLF": "Superior Longitudinal Fasciculus",
    "SLFT": "Superior Longitudinal Fasciculus Temporal",
    "IFOF": "Inferior Fronto-Occipital Fasciculus",
    "ILF": "Inferior Longitudinal Fasciculus",
    "CST": "Corticospinal Tract", "ATR": "Anterior Thalamic Radiation",
    "FMajor": "Forceps Major", "FMinor": "Forceps Minor",
    "meanSkeleton": "Mean Skeleton", "BrainStem": "Brainstem",
    "CC": "Corpus Callosum (combined, source CC)",
    "CC_max": "Corpus Callosum (source CC_max)",
}
NUMBERED_ROI_NAMES = {
    "Ant_Thalamic_Rad": "Anterior Thalamic Radiation",
    "Cingulum_Cingulate": "Cingulum Cingulate",
    "Cingulum_Hippocampus": "Cingulum Hippocampus",
    "Corticospinal": "Corticospinal Tract",
    "Inf_Fronto_Occipital_Fasc": "Inferior Fronto-Occipital Fasciculus",
    "Inf_Longitudinal_Fasc": "Inferior Longitudinal Fasciculus",
    "Sup_Longitudinal_Fasc": "Superior Longitudinal Fasciculus",
    "Sup_Longitudinal_Fasc_Temporal": "Superior Longitudinal Fasciculus Temporal",
    "Uncinate_Fasc": "Uncinate Fasciculus",
    "CorpusCallosum_Body": "Corpus Callosum Body",
    "CorpusCallosum_Genu": "Corpus Callosum Genu",
    "CorpusCallosum_Splenium": "Corpus Callosum Splenium",
    "Forceps_Major": "Forceps Major", "Forceps_Minor": "Forceps Minor",
    "Mean_Skeleton": "Mean Skeleton",
}


@dataclass(frozen=True)
class SourceRecord:
    dataset: str
    source_key: str
    alternate_key: str
    metric_name: str
    roi_name: str
    summary_method: str
    value: float
    unit: str
    source_file: str
    source_column: str
    source_sha256: str


@dataclass
class Scan:
    records: list[SourceRecord]
    issues: list[str]
    stats: list[dict]
    percentile_issues: list[tuple]


def normalise_roi(column: str) -> str:
    if column in ROI_ALIASES:
        return ROI_ALIASES[column]
    # Named numbered ROI (1_Ant_Thalamic_Rad_L, 24_Mean_Skeleton, etc.)
    body = re.sub(r"^\d+_", "", column)
    suffix = ""
    if body.endswith("_L") or body.endswith("_R"):
        suffix = " (Left)" if body.endswith("_L") else " (Right)"
        body = body[:-2]
    if body in NUMBERED_ROI_NAMES:
        return NUMBERED_ROI_NAMES[body] + suffix
    # Abbreviated (CGC_L, CST_R, etc.)
    m = re.fullmatch(r"([A-Za-z]+)_(L|R)", column)
    if m and m.group(1) in ROI_ALIASES:
        return ROI_ALIASES[m.group(1)] + (" (Left)" if m.group(2) == "L" else " (Right)")
    raise ValueError(f"Unrecognised ROI column {column!r}; mapping must be explicit")


def _column_number(ref: str) -> int:
    n = 0
    for c in re.match(r"^[A-Z]+", ref).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def xlsx_rows(path: Path):
    """Read the first sheet of simple summary XLSX without external Excel packages.

    Supports shared-string, inline-string and numeric cells, including blank cells.
    This is deliberately narrow: we only need the fixed-format supplied summaries.
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(path) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.iter(ns + "t"))
                       for si in root.findall(ns + "si")]
        sheets = sorted(p for p in z.namelist()
                        if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", p))
        if not sheets:
            raise ValueError(f"No simple worksheet XML in {path.name}")
        root = ET.fromstring(z.read(sheets[0]))
        for row in root.findall(".//" + ns + "sheetData/" + ns + "row"):
            vals: dict[int, str] = {}
            for cell in row.findall(ns + "c"):
                ref = cell.attrib.get("r", "")
                if not re.match(r"^[A-Z]+\d+$", ref):
                    continue
                idx = _column_number(ref)
                v = cell.find(ns + "v")
                if v is None and cell.attrib.get("t") == "inlineStr":
                    it = cell.find(ns + "is")
                    value = "".join(el.text or "" for el in it.iter(ns + "t")) if it is not None else ""
                elif v is None:
                    value = ""
                elif cell.attrib.get("t") == "s":
                    value = strings[int(v.text)]
                else:
                    value = v.text or ""
                vals[idx] = value
            if vals:
                yield [vals.get(i, "") for i in range(max(vals) + 1)]


def table_rows(path: Path):
    if path.suffix.lower() == ".csv":
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError(f"Empty CSV: {path.name}")
            for row in reader:
                yield row
    elif path.suffix.lower() == ".xlsx":
        rows = iter(xlsx_rows(path))
        header = next(rows)
        for row in rows:
            yield dict(zip(header, row + [""] * max(0, len(header) - len(row))))
    else:
        raise ValueError(f"Unsupported source type: {path}")


def source_key(dataset: str, file_prefix: str, row: dict) -> tuple[str, str]:
    s = str(row.get("subject", "")).strip()
    if not s:
        raise ValueError("Missing 'subject' impact identifier")
    alt = str(row.get("ImpactID", "")).strip()
    if dataset in {"DS003", "DS004", "DS002"}:
        pattern = r"Rugby\d+" if dataset.startswith("DS00") and dataset != "DS002" else r"MMA\d+"
        if not re.fullmatch(pattern, s):
            raise ValueError(f"Expected {pattern} identifier; got {s!r}")
    return s, alt


def files_in(directory: Path, include_nolr: bool = False) -> list[Path]:
    found = sorted(p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in (".csv", ".xlsx")
                   and re.match(r"^(?:2|3|4|5|9|10|12|13)_", p.name)
                   and (include_nolr or "nolr" not in p.stem.lower()))
    return found


def scan_sources(directory: Path, rate_unit: str | None = None) -> Scan:
    records: list[SourceRecord] = []
    issues: list[str] = []
    stats: list[dict] = []
    for path in files_in(directory):
        prefix = path.name.split("_", 1)[0]
        dataset = DATASETS[prefix]
        perc = 95 if re.search(r"95perc", path.stem, re.IGNORECASE) else 90
        method = f"source-reported {perc}th percentile"
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        n_rows = 0
        n_metrics = 0
        n_rate_omitted = 0
        n_skipped_cells = 0
        seen = set()
        for n_rows, row in enumerate(table_rows(path), start=1):
            try:
                key, alt = source_key(dataset, prefix, row)
                if key in seen:
                    raise ValueError(f"Duplicate subject {key!r}")
                seen.add(key)
            except ValueError as exc:
                issues.append(f"{path.name}:row {n_rows+1}: {exc}")
                continue
            for column, raw in row.items():
                if column in {"subject", "ImpactID"}:
                    continue
                if not column:
                    issues.append(f"{path.name}: unexpected blank column name")
                    continue
                if column in ("90PercStrain", "90PercStrainRate"):
                    roi = "Whole Brain"
                    is_rate = column.endswith("Rate")
                else:
                    try:
                        roi = normalise_roi(column)
                    except ValueError as exc:
                        issues.append(f"{path.name}:row {n_rows+1}: {exc}")
                        continue
                    is_rate = False
                if roi not in SELECTED_ROIS:
                    continue
                if is_rate and rate_unit is None:
                    n_rate_omitted += 1
                    continue
                value_string = str(raw or "").strip()
                if not value_string or value_string.lower() in {"nan", "na", "n/a", "null"}:
                    n_skipped_cells += 1
                    continue
                try:
                    value = float(value_string)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError()
                except (TypeError, ValueError):
                    issues.append(f"{path.name}:row {n_rows+1}: invalid value for {column}")
                    continue
                records.append(SourceRecord(dataset, key, alt,
                    f"MPSR{perc}" if is_rate else f"MPS{perc}", roi, method,
                    value, rate_unit if is_rate else "strain", path.name, column,
                    fingerprint))
                n_metrics += 1
        stats.append(dict(file=path.name, dataset=dataset, percentile=perc,
                          impacts=n_rows, metrics=n_metrics,
                          rate_omitted=n_rate_omitted,
                          blank_cells=n_skipped_cells))
    # For a matched impact/ROI, a 95th percentile should be >= the 90th
    # percentile if source/analysis definitions are the same. Flag exceptions,
    # without guessing the cause or changing any numerical source values.
    by_pair = defaultdict(dict)
    for rec in records:
        by_pair[(rec.dataset, rec.source_key, rec.roi_name, rec.metric_name.rstrip("0123456789"))][rec.metric_name] = rec
    percentile_issues = []
    for (ds, key, roi, kind), measures in by_pair.items():
        a, b = measures.get(kind + "90"), measures.get(kind + "95")
        if a and b and b.value + 1e-10 < a.value:
            percentile_issues.append((ds, key, roi, kind, a.value, b.value))
    return Scan(records, issues, stats, percentile_issues)



STAGING_COLUMNS = [
    "dataset_id", "source_key", "alternate_key", "metric_name", "roi_name",
    "summary_method", "value", "unit", "source_file", "source_column", "source_sha256",
]

def write_staging(scan: Scan, output: Path) -> None:
    """Create a PRIVATE, reusable long-format staging CSV, never for public Git."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(STAGING_COLUMNS)
        for r in scan.records:
            writer.writerow([
                r.dataset, r.source_key, r.alternate_key, r.metric_name, r.roi_name,
                r.summary_method, format(r.value, ".17g"), r.unit, r.source_file,
                r.source_column, r.source_sha256,
            ])

def read_staging(path: Path) -> Scan:
    """Read prepared metrics without needing the original private source tables.

    Enforce the same Issue #4 ROI scope and strict field validation on reload.
    """
    records = []
    issues = []
    stats = Counter()
    seen = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(STAGING_COLUMNS).issubset(reader.fieldnames):
            raise ValueError("Prepared CSV is missing required staging columns")
        for line, row in enumerate(reader, start=2):
            try:
                if row["roi_name"] not in SELECTED_ROIS:
                    raise ValueError("ROI is outside the approved Issue #4 scope")
                if row["dataset_id"] not in DATASETS.values():
                    raise ValueError("Unknown dataset")
                if row["metric_name"] not in ("MPS90", "MPS95", "MPSR90", "MPSR95"):
                    raise ValueError("Unknown metric")
                if row["metric_name"].startswith("MPSR") and row["roi_name"] != "Whole Brain":
                    raise ValueError("MPSR is only supported for Whole Brain")
                if row["metric_name"].startswith("MPSR") and row["unit"] not in ("1/s", "1/ms"):
                    raise ValueError("MPSR source unit is not verified")
                if row["metric_name"].startswith("MPS") and not row["metric_name"].startswith("MPSR") and row["unit"] != "strain":
                    raise ValueError("Unexpected MPS unit")
                if row["summary_method"] != f"source-reported {row['metric_name'][-2:]}th percentile":
                    raise ValueError("Percentile metadata inconsistent")
                value = float(row["value"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Nonfinite or negative metric")
                rec = SourceRecord(
                    row["dataset_id"], row["source_key"], row["alternate_key"],
                    row["metric_name"], row["roi_name"], row["summary_method"],
                    value, row["unit"], row["source_file"], row["source_column"],
                    row["source_sha256"],
                )
                key = (rec.dataset, rec.source_key, rec.roi_name, rec.metric_name)
                if key in seen:
                    raise ValueError("Duplicate (dataset, impact, ROI, metric) in staging")
                seen.add(key)
                records.append(rec)
                stats[(rec.source_file, rec.dataset, rec.metric_name[-2:])] += 1
            except (ValueError, TypeError) as exc:
                issues.append(f"{path.name}:row {line}: {exc}")
    # Recompute percentile QC from the staging file, never assume its QC sidecar is intact.
    by_pair = defaultdict(dict)
    for rec in records:
        kind = "MPSR" if rec.metric_name.startswith("MPSR") else "MPS"
        by_pair[(rec.dataset, rec.source_key, rec.roi_name, kind)][rec.metric_name] = rec
    inversions = []
    for (ds, key, roi, kind), measures in by_pair.items():
        a, b = measures.get(kind + "90"), measures.get(kind + "95")
        if a and b and b.value + 1e-10 < a.value:
            inversions.append((ds, key, roi, kind, a.value, b.value))
    source_stats = [dict(file=f, dataset=ds, percentile=int(p), impacts="from staging",
                         metrics=count, blank_cells=0, rate_omitted=0)
                    for (f, ds, p), count in sorted(stats.items())]
    return Scan(records, issues, source_stats, inversions)

def _ds004_impact_offset(impact_lookup) -> int:
    """Recognise only the two VERIFIED 1,155-impact DS004 numbering schemes.

    Original strain summaries label DS004 locally as Rugby1..Rugby1155.
    Some database versions store those same impacts with global Rugby547..Rugby1701.
    Detect the convention from the actual database IDs, never from source row order.
    Refuse unknown/partial conventions rather than silently misattribute results.
    """
    rows = impact_lookup.get("DS004", [])
    if not rows:
        return 0  # Test/minimal databases without DS004.
    suffixes = []
    for uid, _, _ in rows:
        match = re.search(r"(?:^|_)Rugby(\d+)$", str(uid))
        if match is None:
            raise ValueError("DS004 impact IDs do not end in Rugby<number>; manual mapping required")
        suffixes.append(int(match.group(1)))
    if len(suffixes) != 1155 or len(set(suffixes)) != 1155:
        raise ValueError("DS004 is not the expected complete 1,155-impact dataset; inspect ID convention")
    if set(suffixes) == set(range(1, 1156)):
        return 0
    if set(suffixes) == set(range(547, 1702)):
        return 546
    raise ValueError("Unrecognised DS004 Rugby numbering: inspect impact UID mapping before import")


def _match_impact(record: SourceRecord, impact_lookup, canonical: bool,
                  ds004_offset: int = 0):
    """Return unique matching impact; never infer a mapping from row order."""
    dataset = record.dataset
    name = record.source_key
    if canonical and dataset in {"DS003", "DS004"}:
        dataset = "DS003_RFU1701"
        if record.dataset == "DS004":
            name = "Rugby" + str(int(name.removeprefix("Rugby")) + 546)
    elif dataset == "DS004" and ds004_offset:
        name = "Rugby" + str(int(name.removeprefix("Rugby")) + ds004_offset)
    choices = impact_lookup.get(dataset, [])
    matches = []
    keys = [name]
    if record.alternate_key:
        keys.append(record.alternate_key)
        if dataset == "DS013":
            # Leeds Rhinos source IDs use e.g. 95959-0802, whereas
            # the existing DuckDB impact IDs use 95959-802.
            # Normalise only the numeric suffix; preserve the device ID.
            match = re.fullmatch(r"([0-9]+)-([0-9]+)", record.alternate_key)
            if match:
                keys.append(f"{match.group(1)}-{int(match.group(2))}")
    for impact_uid, source_impact_id, source_sample_id in choices:
        for key in keys:
            if (impact_uid == key or impact_uid.endswith("_" + key)
                or source_impact_id == key or source_sample_id == key):
                matches.append(impact_uid)
                break
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0], "matched"
    if len(matches) > 1:
        return None, "ambiguous_impact"
    return None, "no_impact_match"


def _key(rec: SourceRecord, sim: str):
    return sim, rec.roi_name, rec.metric_name, rec.summary_method


def _uid(key: tuple[str, ...]) -> str:
    return "BM_SUM_" + hashlib.sha256("\x1f".join(key).encode()).hexdigest()[:32]


def plan_db(scan: Scan, db_path: Path, model_name: str | None = None,
            model_version: str | None = None, exclude_qc_failures: bool = False,
            register_missing_simulations: bool = False):
    if register_missing_simulations and not model_name:
        raise ValueError("Registration needs an explicitly verified FE model name")
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("Database mode needs duckdb; activate the platform Python environment") from exc
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        impacts = con.execute("SELECT impact_uid,dataset_id,source_impact_id,source_sample_id FROM impacts").fetchall()
        sims = con.execute("SELECT simulation_uid, impact_uid, model_name, model_version FROM simulation_runs").fetchall()
        existing = con.execute('SELECT simulation_uid,roi_name,metric_name,summary_method,"value",unit FROM brain_metric_results').fetchall()
    finally:
        con.close()
    db_ds = {row[1] for row in impacts}
    canonical = "DS003_RFU1701" in db_ds
    lookup = defaultdict(list)
    for uid, ds, sid, sample in impacts:
        lookup[ds].append((uid, sid, sample))
    ds004_offset = 0 if canonical else _ds004_impact_offset(lookup)
    by_impact = defaultdict(list)
    all_simulations = defaultdict(list)
    for sid, impact_uid, model, version in sims:
        all_simulations[impact_uid].append((sid, model, version))
        if (model_name is None or model == model_name) and (model_version is None or version == model_version):
            by_impact[impact_uid].append(sid)
    existing_map = defaultdict(list)
    for sim, roi, metric, method, value, unit in existing:
        existing_map[(sim, roi, metric, method)].append((value, unit))
    plan = []
    simulation_plan = []
    issues = []
    statuses = Counter()
    unique = {}
    match_cache = {}
    failing = {(ds, src, roi, f"{kind}{p}") for ds, src, roi, kind, _, _
               in scan.percentile_issues for p in PERCENTILES}
    for rec in scan.records:
        if exclude_qc_failures and (rec.dataset, rec.source_key, rec.roi_name, rec.metric_name) in failing:
            statuses["excluded_percentile_qc"] += 1
            continue
        lookup_key = (rec.dataset, rec.source_key, rec.alternate_key)
        if lookup_key not in match_cache:
            match_cache[lookup_key] = _match_impact(rec, lookup, canonical,
                                                    ds004_offset=ds004_offset)
        uid, state = match_cache[lookup_key]
        if uid is None:
            statuses[state] += 1
            issues.append((state, rec.source_file, rec.source_key, rec.metric_name, rec.roi_name))
            continue
        candidates = by_impact.get(uid, [])
        if not candidates:
            if register_missing_simulations and all_simulations[uid]:
                # Existing run with a different model or version: never assume
                # it is unrelated or that a second run should be invented.
                state = "other_simulation_present"
            elif register_missing_simulations:
                # Deterministic summary-only registration, one per impact/model.
                sim = _uid(("simulation", uid, model_name, model_version or "")).replace("BM_SUM_", "SIM_SUM_", 1)
                state = "register_simulation"
            else:
                state = "no_simulation_match"
        elif len(candidates) > 1:
            state = "ambiguous_simulation"
        else:
            state = "matched"
        if state not in ("matched", "register_simulation"):
            statuses[state] += 1
            issues.append((state, rec.source_file, rec.source_key, rec.metric_name, rec.roi_name))
            continue
        if state == "matched":
            sim = candidates[0]
        elif not any(item[0] == sim for item in simulation_plan):
            simulation_plan.append((
                sim, uid, model_name, model_version, "summary_only",
                "Registered solely for source-reported strain summary import; no NIfTI or simulation artifacts linked"))
        key = _key(rec, sim)
        if key in unique:
            previous = unique[key]
            state = "duplicate_source_metric" if previous.value == rec.value else "conflicting_source_metric"
            statuses[state] += 1
            issues.append((state, rec.source_file, rec.source_key, rec.metric_name, rec.roi_name))
            continue
        unique[key] = rec
        db_vals = existing_map.get(key, [])
        if db_vals:
            if len(db_vals) == 1 and db_vals[0][0] is not None and math.isclose(float(db_vals[0][0]), rec.value, rel_tol=1e-10, abs_tol=1e-12) and db_vals[0][1] == rec.unit:
                statuses["already_present"] += 1
            else:
                statuses["conflict_with_database"] += 1
                issues.append(("conflict_with_database", rec.source_file, rec.source_key, rec.metric_name, rec.roi_name))
            continue
        statuses["ready_to_insert"] += 1
        plan.append((
            _uid(key), sim, rec.roi_name, rec.metric_name,
            rec.summary_method, rec.value, rec.unit,
            None, VERSION,
            f"Source summary {rec.source_file}; field {rec.source_column}; sha256={rec.source_sha256}"))
    statuses["simulations_to_register"] = len(simulation_plan)
    return plan, statuses, issues, simulation_plan


def do_apply(db_path: Path, plan, simulation_plan, allow_partial: bool, statuses: Counter,
             scan_issues, percentile_issues, exclude_qc_failures: bool):
    # Cannot safely auto-resolve ambiguous records or conflicting source values.
    blocking = {"ambiguous_impact", "ambiguous_simulation", "conflicting_source_metric", "conflict_with_database", "other_simulation_present"}
    partial = {"no_impact_match", "no_simulation_match", "duplicate_source_metric"}
    if percentile_issues and not exclude_qc_failures:
        raise SystemExit("Refusing --apply: percentile QC failures; review report or explicitly --exclude-qc-failures")
    if any(statuses[x] for x in blocking):
        raise SystemExit("Refusing --apply: ambiguity/conflict detected; inspect QC report")
    if (any(statuses[x] for x in partial) or scan_issues) and not allow_partial:
        raise SystemExit("Refusing --apply: incomplete mappings/source issues; review QC then --allow-partial")
    if scan_issues:
        raise SystemExit("Refusing --apply: malformed source/staging input; fix the input before writing")
    if not plan and not simulation_plan:
        return 0
    import duckdb
    con = duckdb.connect(str(db_path), read_only=False)
    try:
        con.execute("BEGIN TRANSACTION")
        if simulation_plan:
            con.executemany("""INSERT INTO simulation_runs
                (simulation_uid,impact_uid,model_name,model_version,simulation_status,notes)
                VALUES (?,?,?,?,?,?)""", simulation_plan)
        if plan:
            con.executemany("""INSERT INTO brain_metric_results
                (brain_metric_uid,simulation_uid,roi_name,metric_name,summary_method,
                 "value",unit,source_artifact_uid,extraction_version,notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", plan)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return len(plan)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path, help="Folder of original private CSV/XLSX inputs")
    source.add_argument("--staging-csv", type=Path, help="Previously prepared private long-format CSV")
    p.add_argument("--write-staging", type=Path, help="Save private selected-ROI long-format CSV")
    p.add_argument("--db", type=Path, help="DuckDB file; omitted => source-only preflight")
    p.add_argument("--mpsr-unit", choices=("1/s", "1/ms"), help="Required to import Whole Brain MPSR; no silent unit assumption")
    p.add_argument("--model-name", help="Exact existing FE model name, or explicit verified name for summary-only registration")
    p.add_argument("--register-missing-simulations", action="store_true",
                   help="Explicitly register summary-only IC FE simulation rows for matched impacts with no existing runs")
    p.add_argument("--model-version", help="Select FE version if multiple simulation runs")
    p.add_argument("--qc-dir", type=Path, help="Private local folder for preflight reports")
    p.add_argument("--apply", action="store_true", help="Actually write to DB (default: dry-run)")
    p.add_argument("--exclude-qc-failures", action="store_true",
                   help="Explicitly omit both 90 and 95 values for each ROI failing percentile QC")
    p.add_argument("--allow-partial", action="store_true", help="Skip unmatched inputs after reviewing QC")
    a = p.parse_args(argv)
    if a.apply and not a.db:
        p.error("--apply requires --db")
    if a.apply and not a.model_name:
        p.error("--apply requires --model-name to avoid attaching results to the wrong FE model")
    if a.register_missing_simulations and not a.model_name:
        p.error("--register-missing-simulations requires --model-name")
    if a.source_dir and not a.source_dir.is_dir():
        p.error(f"Not a directory: {a.source_dir}")
    if a.staging_csv and not a.staging_csv.is_file():
        p.error(f"Not a file: {a.staging_csv}")
    scan = scan_sources(a.source_dir, a.mpsr_unit) if a.source_dir else read_staging(a.staging_csv)
    if a.write_staging:
        write_staging(scan, a.write_staging)
        print(f"Prepared private staging: {a.write_staging}")
    print(f"Scanned {len(scan.stats)} files, {len(scan.records)} numeric metrics, "
          f"{len(scan.issues)} source issues, {len(scan.percentile_issues)} percentile QC inversions")
    for s in scan.stats:
        print("  {file}: {impacts} impacts, {metrics} metrics, {blank_cells} missing cells, {rate_omitted} rates deferred".format(**s))
    plan = []
    statuses = Counter()
    issues = []
    simulation_plan = []
    if a.db:
        if not a.db.is_file():
            p.error(f"Database does not exist: {a.db}")
        plan, statuses, issues, simulation_plan = plan_db(scan, a.db, a.model_name, a.model_version, a.exclude_qc_failures, a.register_missing_simulations)
        print("DB matching:", json.dumps(statuses, sort_keys=True))
    if a.qc_dir:
        a.qc_dir.mkdir(parents=True, exist_ok=True)
        (a.qc_dir / "strain_source_stats.json").write_text(json.dumps(scan.stats, indent=2))
        with (a.qc_dir / "strain_source_issues.txt").open("w") as f:
            f.write("\n".join(scan.issues) + "\n")
        with (a.qc_dir / "strain_percentile_qc.csv").open("w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["dataset", "subject", "roi_name", "metric_kind", "p90", "p95"])
            wr.writerows(scan.percentile_issues)
        with (a.qc_dir / "strain_db_issues.csv").open("w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["status", "source_file", "subject", "metric_name", "roi_name"])
            wr.writerows(issues)
    if a.apply:
        print("Inserted", do_apply(a.db, plan, simulation_plan, a.allow_partial, statuses,
                                    scan.issues, scan.percentile_issues, a.exclude_qc_failures), "rows")
    elif a.db:
        print("DRY RUN only; nothing written. Use --apply after QC review.")
    if scan.issues or scan.percentile_issues:
        print("WARNING: Source/percentile QC issues; run with --qc-dir for details", file=sys.stderr)
    if a.source_dir and a.mpsr_unit is None:
        print("Whole Brain MPSR rates omitted until their source unit is confirmed.")


if __name__ == "__main__":
    main()
