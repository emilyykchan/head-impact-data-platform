from __future__ import annotations

import pandas as pd


MANIFEST_COLUMNS = [
    "dataset_id",
    "impact_uid",
    "simulation_uid",
    "artifact_uid",
    "artifact_type",
    "metric_name",
    "file_format",
    "storage_root",
    "relative_path",
    "size_bytes",
]


def artifact_manifest(con, dataset_ids: list[str] | None = None) -> pd.DataFrame:
    """
    Export canonical artifact references without copying the underlying files.
    """
    params: list[str] = []
    where = "TRUE"

    if dataset_ids:
        placeholders = ",".join(["?"] * len(dataset_ids))
        where = f"i.dataset_id IN ({placeholders})"
        params.extend(dataset_ids)

    sql = f"""
    SELECT
        i.dataset_id,
        i.impact_uid,
        sr.simulation_uid,
        sa.artifact_uid,
        sa.artifact_type,
        sa.metric_name,
        sa.file_format,
        sa.storage_root,
        sa.relative_path,
        sa.size_bytes
    FROM simulation_artifacts sa
    JOIN simulation_runs sr
      ON sr.simulation_uid = sa.simulation_uid
    JOIN impacts i
      ON i.impact_uid = sr.impact_uid
    WHERE {where}
    ORDER BY
        i.dataset_id,
        i.impact_uid,
        sr.simulation_uid,
        sa.artifact_type,
        sa.metric_name,
        sa.artifact_uid
    """

    return con.execute(sql, params).fetchdf()[MANIFEST_COLUMNS]
