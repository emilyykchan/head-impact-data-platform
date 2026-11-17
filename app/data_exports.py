"""Read-only research exports from DuckDB and registered NAS artifacts.

The interface builds an impact-level numeric CSV. Optional waveform CSVs and
NAS files make the output a ZIP. This module neither changes research data nor
accepts user-defined filesystem paths.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from storage import StorageResolutionError, resolve_storage_path

# The initial Streamlit path deliberately caps interactive archives. Larger
# research exports should use a disk-backed authenticated export service.
MAX_ZIP_IMPACTS = 100
MAX_ZIP_FILES = 500
MAX_ZIP_BYTES = 100 * 1024 * 1024

BASE_COLUMNS = {
    "dataset_id": "Dataset",
    "impact_uid": "Impact UID",
    "source_impact_id": "Source impact ID",
    "source_sample_id": "Source sample ID",
    "participant_uid": "Participant UID",
    "event_uid": "Event UID",
    "impact_timestamp": "Timestamp",
}

# Unlike numeric metrics, waveform channels follow a fixed physical schema.
# The available columns are discovered from the deployed database at runtime.
CHANNEL_LABELS = {
    "linear_acc_x_g": "Linear acceleration X (g)",
    "linear_acc_y_g": "Linear acceleration Y (g)",
    "linear_acc_z_g": "Linear acceleration Z (g)",
    "linear_acc_mag_g": "Linear acceleration magnitude (g)",
    "angular_vel_x_rad_s": "Angular velocity X (rad/s)",
    "angular_vel_y_rad_s": "Angular velocity Y (rad/s)",
    "angular_vel_z_rad_s": "Angular velocity Z (rad/s)",
    "angular_vel_mag_rad_s": "Angular velocity magnitude (rad/s)",
    "angular_acc_x_rad_s2": "Angular acceleration X (rad/s²)",
    "angular_acc_y_rad_s2": "Angular acceleration Y (rad/s²)",
    "angular_acc_z_rad_s2": "Angular acceleration Z (rad/s²)",
    "angular_acc_mag_rad_s2": "Angular acceleration magnitude (rad/s²)",
    "linear_vel_x_m_s": "Linear velocity X (m/s)",
    "linear_vel_y_m_s": "Linear velocity Y (m/s)",
    "linear_vel_z_m_s": "Linear velocity Z (m/s)",
    "linear_vel_mag_m_s": "Linear velocity magnitude (m/s)",
}


class ExportError(ValueError):
    """Invalid, ambiguous, or oversized export specification."""


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _require_datasets(dataset_ids: list[str]):
    if not dataset_ids:
        raise ExportError("Choose at least one dataset.")


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _metric_key(kind: str, stage: str | None, roi: str | None,
                name: str, method: str | None, unit: str | None,
                model: str | None, version: str | None) -> str:
    # DuckDB nulls can arrive as None, NaN or pandas.NA. Normalise all of them.
    def normalise(value):
        if value is None or pd.isna(value):
            return ""
        return str(value)

    # JSON encoding avoids delimiter collisions between real metric names.
    return json.dumps([normalise(x) for x in (
        kind, stage, roi, name, method, unit, model, version
    )], ensure_ascii=False, separators=(",", ":"))


def _metric_label(key: str) -> str:
    kind, stage, roi, name, method, unit, model, version = json.loads(key)
    if kind == "kinematic":
        parts = ["Kinematics", name]
    else:
        parts = ["Brain", roi, name, method]
        if model or version:
            parts.append(" / ".join(x for x in (model, version) if x))
    label = " · ".join(x for x in parts if x)
    return label + (f" [{unit}]" if unit else "")


def _slug(value: object) -> str:
    """Spreadsheet-friendly column component; no dots, spaces, or slashes."""
    cleaned = (str(value).lower().replace("²", "2").replace("³", "3")
               .replace("µ", "u").replace("μ", "u").replace("°", "deg")
               .replace("%", "pct"))
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", cleaned)).strip("_")


def _metric_column(key: str) -> str:
    """Stable metric header; approved brain-strain metrics use uppercase labels."""
    kind, stage, roi, name, method, unit, model, version = json.loads(key)
    roi_abbreviation = {
        "Whole Brain": "WB",
        "Brainstem": "BS",
        "Corpus Callosum Genu": "CCG",
        "Corpus Callosum Body": "CCB",
        "Corpus Callosum Splenium": "CCS",
    }
    if kind == "brain" and roi in roi_abbreviation and name in {
        "MPS90", "MPS95", "MPSR90", "MPSR95"
    }:
        return f"{roi_abbreviation[roi]}_{name}"
    parts = (["kinematics", name, unit] if kind == "kinematic"
             else ["brain", roi, name, method, model, version, unit])
    return "_".join(filter(None, (_slug(x) for x in parts)))[:160]


def _metric_columns(keys: list[str]) -> dict[str, str]:
    """Disambiguate names which normalize to the same snake_case header."""
    bases = {k: _metric_column(k) for k in keys}
    counts = {name: list(bases.values()).count(name) for name in set(bases.values())}
    return {k: (name if counts[name] == 1 else
                name[:150] + "_" + hashlib.sha256(k.encode("utf-8")).hexdigest()[:8])
            for k, name in bases.items()}


def dataset_metadata_options(db_path: str) -> dict[str, str]:
    """Expose registered dataset-wide scalar metadata without hardcoding a schema."""
    with duckdb.connect(db_path, read_only=True) as con:
        rows = con.execute("PRAGMA table_info('datasets')").fetchall()
    # Dataset ID is already a required identifier in each export.
    safe = [str(r[1]) for r in rows if r[1] != "dataset_id" and
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(r[1])) and
            not any(x in str(r[2]).upper() for x in ("[]", "STRUCT", "BLOB"))]
    return {"dataset_" + c: "Dataset · " + c.replace("_", " ").capitalize()
            for c in safe}


def _dataset_metadata_values(db_path: str, impact_ids: list[str],
                             columns: list[str]) -> pd.DataFrame:
    if not columns:
        return pd.DataFrame()
    # Called after dataset_metadata_options() validates the source fields.
    quoted = ", ".join(f'd."{c[8:]}" AS "{c}"' for c in columns)
    with duckdb.connect(db_path, read_only=True) as con:
        return con.execute(f"""
            SELECT i.impact_uid, {quoted}
            FROM impacts i LEFT JOIN datasets d ON d.dataset_id=i.dataset_id
            WHERE i.impact_uid IN ({_placeholders(impact_ids)})
        """, impact_ids).fetchdf()


def impact_rows(db_path: str, dataset_ids: list[str], sim_filter: str = "Any",
                search: str = "") -> pd.DataFrame:
    """One row per impact. Avoids multiplying impacts by record joins."""
    _require_datasets(dataset_ids)
    where = [f"i.dataset_id IN ({_placeholders(dataset_ids)})"]
    params = list(dataset_ids)
    if sim_filter == "Has simulation":
        where.append("EXISTS (SELECT 1 FROM simulation_runs s WHERE s.impact_uid=i.impact_uid)")
    elif sim_filter == "No simulation":
        where.append("NOT EXISTS (SELECT 1 FROM simulation_runs s WHERE s.impact_uid=i.impact_uid)")
    elif sim_filter != "Any":
        raise ExportError("Unknown simulation filter.")
    if search.strip():
        like = "%" + search.strip().lower() + "%"
        where.append("(LOWER(i.impact_uid) LIKE ? OR LOWER(COALESCE(i.source_impact_id,'')) LIKE ?)")
        params.extend([like, like])
    sql = f"""
        SELECT i.dataset_id, i.impact_uid, i.source_impact_id,
               i.source_sample_id, i.participant_uid, i.event_uid,
               i.impact_timestamp
        FROM impacts i
        WHERE {' AND '.join(where)}
        ORDER BY i.dataset_id, i.source_impact_id NULLS LAST, i.impact_uid
    """
    with duckdb.connect(db_path, read_only=True) as con:
        return con.execute(sql, params).fetchdf()


def _numeric_rows(con, dataset_ids: list[str]) -> pd.DataFrame:
    """Read observed scalar values. Metrics use processed records only.

    Raw/processed is a time-series export choice, not a scalar metric choice.
    """
    ds = _placeholders(dataset_ids)
    parts = []
    kinematic = con.execute(f"""
            SELECT i.dataset_id, i.impact_uid, 'kinematic' AS data_kind,
                   kr.data_stage, NULL::VARCHAR AS roi_name, km.metric_name,
                   NULL::VARCHAR AS summary_method, km."value" AS metric_value,
                   km.unit, NULL::VARCHAR AS model_name,
                   NULL::VARCHAR AS model_version, km.metric_uid AS source_uid
            FROM kinematic_metrics km
            JOIN kinematic_records kr ON kr.kinematic_uid=km.kinematic_uid
            JOIN impacts i ON i.impact_uid=kr.impact_uid
            WHERE i.dataset_id IN ({ds}) AND kr.data_stage='processed'
              AND km.metric_name IS NOT NULL AND km."value" IS NOT NULL
        """, list(dataset_ids)).fetchdf()
    parts.append(kinematic)
    brain = con.execute(f"""
        SELECT i.dataset_id, i.impact_uid, 'brain' AS data_kind,
               NULL::VARCHAR AS data_stage, bm.roi_name, bm.metric_name,
               bm.summary_method, bm."value" AS metric_value,
               bm.unit, sr.model_name, sr.model_version,
               bm.brain_metric_uid AS source_uid
        FROM brain_metric_results bm
        JOIN simulation_runs sr ON sr.simulation_uid=bm.simulation_uid
        JOIN impacts i ON i.impact_uid=sr.impact_uid
        WHERE i.dataset_id IN ({ds})
          AND bm.metric_name IS NOT NULL AND bm."value" IS NOT NULL
    """, list(dataset_ids)).fetchdf()
    parts.append(brain)
    if not parts:
        return pd.DataFrame()
    result = pd.concat(parts, ignore_index=True)
    if result.empty:
        return result.assign(metric_key=pd.Series(dtype="str"))
    result["metric_key"] = [
        _metric_key(str(r.data_kind), r.data_stage, r.roi_name,
                    str(r.metric_name), r.summary_method, r.unit,
                    r.model_name, r.model_version)
        for r in result.itertuples(index=False)
    ]
    return result


def metric_catalog(db_path: str, dataset_ids: list[str]) -> pd.DataFrame:
    """Discover scalar variables and per-dataset impact coverage dynamically."""
    _require_datasets(dataset_ids)
    with duckdb.connect(db_path, read_only=True) as con:
        measurements = _numeric_rows(con, dataset_ids)
    cols = ["metric_key", "label", "dataset_id", "impacts_with_value"]
    if measurements.empty:
        return pd.DataFrame(columns=cols)
    catalog = (measurements.groupby(["metric_key", "dataset_id"], dropna=False)
               ["impact_uid"].nunique().reset_index(name="impacts_with_value"))
    catalog["label"] = catalog["metric_key"].map(_metric_label)
    return catalog[cols].sort_values(["label", "dataset_id"]).reset_index(drop=True)


def build_summary(db_path: str, impacts: pd.DataFrame,
                  base_columns: list[str], metric_keys: list[str],
                  dataset_columns: list[str] | None = None) -> pd.DataFrame:
    """One row per impact, selected scalar metrics as actual numeric columns.

    Refuses ambiguous duplicate values (multiple runs/processing versions)
    instead of silently averaging or selecting an arbitrary measurement.
    """
    dataset_columns = list(dict.fromkeys(dataset_columns or []))
    valid_dataset_cols = dataset_metadata_options(db_path)
    if any(c not in valid_dataset_cols for c in dataset_columns):
        raise ExportError("Unknown dataset-level metadata column selected.")
    # Stable identifier order, regardless of which optional columns were chosen.
    base_columns = ["dataset_id", "impact_uid"] + [
        c for c in base_columns if c not in ("dataset_id", "impact_uid")
    ]
    base_columns = list(dict.fromkeys(base_columns))
    if any(c not in BASE_COLUMNS for c in base_columns):
        raise ExportError("Unknown impact-level column selected.")
    metric_names = _metric_columns(metric_keys)
    if impacts.empty:
        return pd.DataFrame(columns=base_columns + dataset_columns + list(metric_names.values()))
    base = impacts[base_columns].copy()
    if dataset_columns:
        meta = _dataset_metadata_values(db_path, impacts["impact_uid"].tolist(), dataset_columns)
        # A many-to-one JOIN from impacts to datasets preserves impact granularity.
        base = base.merge(meta, on="impact_uid", how="left", validate="one_to_one")
    if not metric_keys:
        return base
    with duckdb.connect(db_path, read_only=True) as con:
        values = _numeric_rows(con, impacts["dataset_id"].unique().tolist())
    selected_ids = set(impacts["impact_uid"].tolist())
    values = values.loc[values["impact_uid"].isin(selected_ids)
                        & values["metric_key"].isin(metric_keys)].copy()
    if not values.empty:
        duplicate = values.duplicated(["impact_uid", "metric_key"], keep=False)
        if duplicate.any():
            examples = values.loc[duplicate, "impact_uid"].drop_duplicates().head(5).tolist()
            raise ExportError(
                "Multiple scalar records map to the same impact/variable "
                f"(e.g. {', '.join(str(x) for x in examples)}). "
                "Resolve versions or duplicate runs before a one-row-per-impact export."
            )
        for key in metric_keys:
            value_map = values.loc[values["metric_key"] == key].set_index("impact_uid")["metric_value"]
            base[metric_names[key]] = base["impact_uid"].map(value_map)
    else:
        for key in metric_keys:
            base[metric_names[key]] = pd.NA
    return base


def available_channels(db_path: str) -> dict[str, str]:
    with duckdb.connect(db_path, read_only=True) as con:
        present = {r[1] for r in con.execute("PRAGMA table_info('kinematic_waveforms')").fetchall()}
    return {k: v for k, v in CHANNEL_LABELS.items() if k in present}


def _waveforms(db_path: str, impact_ids: list[str], stages: list[str],
               channels: list[str]) -> pd.DataFrame:
    if not stages or not channels:
        return pd.DataFrame()
    present = available_channels(db_path)
    if any(c not in present for c in channels):
        raise ExportError("Selected waveform channel does not exist in the database.")
    # Avoid unnecessary loading of unselected waveform arrays.
    selected_fields = ", ".join('kw."' + c + '"' for c in channels)
    sql = f"""
        SELECT i.dataset_id, kr.impact_uid, kr.kinematic_uid,
               kr.data_stage, kr.sample_interval_ms, kr.n_samples,
               {selected_fields}
        FROM kinematic_waveforms kw
        JOIN kinematic_records kr ON kr.kinematic_uid=kw.kinematic_uid
        JOIN impacts i ON i.impact_uid=kr.impact_uid
        WHERE kr.impact_uid IN ({_placeholders(impact_ids)})
          AND kr.data_stage IN ({_placeholders(stages)})
        ORDER BY i.dataset_id, kr.impact_uid, kr.data_stage, kr.kinematic_uid
    """
    with duckdb.connect(db_path, read_only=True) as con:
        return con.execute(sql, impact_ids + stages).fetchdf()


def _artifact_references(db_path: str, impact_ids: list[str],
                         include_sources: bool, include_nifti: bool,
                         stages: list[str]) -> pd.DataFrame:
    parts = []
    with duckdb.connect(db_path, read_only=True) as con:
        if include_sources and stages:
            sql = f"""
              SELECT i.dataset_id, i.impact_uid,
                     kr.kinematic_uid AS record_uid, 'kinematic_source' AS category,
                     kr.storage_root, kr.relative_path
              FROM kinematic_records kr
              JOIN impacts i ON i.impact_uid=kr.impact_uid
              WHERE i.impact_uid IN ({_placeholders(impact_ids)})
                AND kr.data_stage IN ({_placeholders(stages)})
                AND kr.storage_root IS NOT NULL AND kr.relative_path IS NOT NULL
            """
            parts.append(con.execute(sql, impact_ids + stages).fetchdf())
        if include_nifti:
            sql = f"""
              SELECT i.dataset_id, i.impact_uid,
                     sa.artifact_uid AS record_uid, 'nifti' AS category,
                     sa.storage_root, sa.relative_path
              FROM simulation_artifacts sa
              JOIN simulation_runs sr ON sr.simulation_uid=sa.simulation_uid
              JOIN impacts i ON i.impact_uid=sr.impact_uid
              WHERE i.impact_uid IN ({_placeholders(impact_ids)})
                AND sa.artifact_type='spatial_field'
                AND sa.storage_root IS NOT NULL AND sa.relative_path IS NOT NULL
                AND (LOWER(sa.relative_path) LIKE '%.nii' OR
                     LOWER(sa.relative_path) LIKE '%.nii.gz')
            """
            parts.append(con.execute(sql, impact_ids).fetchdf())
    cols = ["dataset_id", "impact_uid", "record_uid", "category", "storage_root", "relative_path"]
    return (pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols))


def _safe_component(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))[:96].strip(".") or "unknown"


def _list_values(value: object) -> list:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    return value if isinstance(value, (list, tuple)) else []


def _waveform_csv(row: pd.Series, channels: list[str]) -> bytes:
    arrays = {c: _list_values(row.get(c)) for c in channels}
    arrays = {c: v for c, v in arrays.items() if v}
    if not arrays:
        return b""
    n = max(len(v) for v in arrays.values())
    dt = row.get("sample_interval_ms")
    dt = None if dt is None or pd.isna(dt) or float(dt) <= 0 else float(dt)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["sample_index", "time_ms"] + list(arrays))
    for j in range(n):
        writer.writerow([j, (j * dt if dt is not None else "")] + [
            arr[j] if j < len(arr) and arr[j] is not None else "" for arr in arrays.values()
        ])
    return out.getvalue().encode("utf-8-sig")


def build_zip(db_path: str, impacts: pd.DataFrame, summary: pd.DataFrame,
              stages: list[str], channels: list[str],
              include_waveforms: bool, include_source_files: bool,
              include_nifti: bool) -> tuple[bytes, pd.DataFrame]:
    """Package selected impact-level CSV and actual data, with inclusion manifest.

    NAS access is gated by server configuration and all paths are re-resolved
    against a fixed registered root. Directories are never recursively exported.
    """
    if impacts.empty:
        raise ExportError("No impacts selected.")
    if len(impacts) > MAX_ZIP_IMPACTS:
        raise ExportError(f"ZIP exports support at most {MAX_ZIP_IMPACTS} impacts per batch.")
    if (include_source_files or include_nifti) and (
        os.environ.get("HEAD_IMPACT_ENABLE_NAS_DOWNLOADS") != "1"
    ):
        raise PermissionError("NAS downloads are disabled on this server. Ask the administrator to enable them after checking access controls.")
    ids = impacts["impact_uid"].tolist()
    manifest_rows = []
    seen = set()
    total = 0
    count = 0
    with tempfile.TemporaryFile(mode="w+b") as tmp:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4) as zf:
            def add_bytes(name: str, content: bytes, category: str, uid: str):
                nonlocal total, count
                if name in seen:
                    manifest_rows.append((category, uid, name, "duplicate", 0))
                    return
                if total + len(content) > MAX_ZIP_BYTES or count >= MAX_ZIP_FILES:
                    raise ExportError("ZIP exceeds the 100 MiB / 500 file interactive limit. Reduce the selection.")
                zf.writestr(name, content)
                seen.add(name)
                total += len(content)
                count += 1
                manifest_rows.append((category, uid, name, "included", len(content)))

            add_bytes("impact_summary.csv", _csv(summary), "summary", "all")
            if include_waveforms:
                waveforms = _waveforms(db_path, ids, stages, channels)
                available_impact_ids = (set(waveforms["impact_uid"].tolist())
                                        if not waveforms.empty else set())
                for missing_uid in sorted(set(ids) - available_impact_ids):
                    manifest_rows.append(("waveform", str(missing_uid), "",
                                          "no_catalogued_waveform", 0))
                for _, row in waveforms.iterrows():
                    raw = _waveform_csv(row, channels)
                    if not raw:
                        manifest_rows.append(("waveform", str(row["kinematic_uid"]), "", "no_selected_channels", 0))
                        continue
                    filename = (f"waveforms/{_safe_component(row['dataset_id'])}/"
                                f"{_safe_component(row['impact_uid'])}/"
                                f"{_safe_component(row['data_stage'])}_"
                                f"{_safe_component(row['kinematic_uid'])}.csv")
                    add_bytes(filename, raw, "waveform", str(row["kinematic_uid"]))
            if include_source_files or include_nifti:
                refs = _artifact_references(db_path, ids, include_source_files, include_nifti, stages)
                for requested, category in ((include_nifti, "nifti"),
                                            (include_source_files, "kinematic_source")):
                    if requested:
                        available_ids = set(refs.loc[
                            refs["category"] == category, "impact_uid"
                        ].tolist())
                        for missing_uid in sorted(set(ids) - available_ids):
                            manifest_rows.append((category, str(missing_uid), "",
                                                  "no_catalogued_file_reference", 0))
                for _, ref in refs.iterrows():
                    uid, category = str(ref["record_uid"]), str(ref["category"])
                    root, relative = str(ref["storage_root"]), str(ref["relative_path"])
                    if "\\" in relative:
                        manifest_rows.append((category, uid, relative, "unsafe_reference", 0))
                        continue
                    try:
                        path = resolve_storage_path(root, relative)
                        if not path.is_file() or path.is_symlink():
                            manifest_rows.append((category, uid, relative, "missing_or_not_file", 0))
                            continue
                        size = path.stat().st_size
                        if size > MAX_ZIP_BYTES - total or count >= MAX_ZIP_FILES:
                            raise ExportError("NAS selection exceeds the 100 MiB / 500 file limit. Reduce the selection.")
                        # Rebuild an archive path from sanitised components, not
                        # arbitrary path strings read from a metadata database.
                        arc = (f"nas/{_safe_component(ref['dataset_id'])}/"
                               f"{_safe_component(ref['impact_uid'])}/"
                               f"{_safe_component(uid)}_"
                               f"{_safe_component(path.name)}")
                        if arc in seen:
                            manifest_rows.append((category, uid, arc, "duplicate", 0))
                            continue
                        # zipfile writes directly from disk, not via read_bytes().
                        zf.write(path, arcname=arc)
                        seen.add(arc)
                        count += 1
                        total += size
                        manifest_rows.append((category, uid, arc, "included", size))
                    except ExportError:
                        raise
                    except (StorageResolutionError, OSError, ValueError):
                        manifest_rows.append((category, uid, relative, "missing_or_unavailable", 0))
            manifest = pd.DataFrame(manifest_rows, columns=[
                "category", "record_uid", "archive_path", "status", "size_bytes"
            ])
            zf.writestr("export_manifest.csv", _csv(manifest))
        tmp.seek(0)
        archive = tmp.read()
        if len(archive) > MAX_ZIP_BYTES:
            raise ExportError("Compressed ZIP exceeds interactive download limit.")
    return archive, manifest
