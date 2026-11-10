from __future__ import annotations

from typing import Iterable

import duckdb
import pandas as pd

from config import DB_PATH

DATASET_LABELS = {
    "DS002": "MMA485",
    "DS003": "RFU546",
    "DS004": "RFU1155",
    "DS006": "BoxingWQT",
    "DS008": "RFL-LBU",
    "DS009": "AFL-VAFA",
    "DS010": "MMA-Pro",
    "DS012": "Boxing558",
    "DS013": "RFL-LR",
}

PAUSED_DATASETS = {"DS009"}


def connect(db_path: str = DB_PATH):
    return duckdb.connect(db_path, read_only=True)


def query_df(
    sql: str,
    params: Iterable | None = None,
    db_path: str = DB_PATH,
) -> pd.DataFrame:
    with connect(db_path) as con:
        return con.execute(sql, list(params or [])).fetchdf()


def table_columns(table: str, db_path: str = DB_PATH) -> list[str]:
    with connect(db_path) as con:
        return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()]


def dataset_summary(db_path: str = DB_PATH) -> pd.DataFrame:
    sql = """
    WITH
    impact_counts AS (
      SELECT dataset_id, COUNT(*) AS impacts
      FROM impacts
      GROUP BY dataset_id
    ),
    kine AS (
      SELECT
        i.dataset_id,
        COUNT(*) FILTER (WHERE kr.data_stage='raw') AS raw_records,
        COUNT(*) FILTER (WHERE kr.data_stage='processed') AS processed_records,
        COUNT(*) AS kinematic_records
      FROM kinematic_records kr
      JOIN impacts i ON i.impact_uid=kr.impact_uid
      GROUP BY i.dataset_id
    ),
    wf AS (
      SELECT i.dataset_id, COUNT(*) AS waveform_records
      FROM kinematic_waveforms kw
      JOIN kinematic_records kr ON kr.kinematic_uid=kw.kinematic_uid
      JOIN impacts i ON i.impact_uid=kr.impact_uid
      GROUP BY i.dataset_id
    ),
    sims AS (
      SELECT i.dataset_id, COUNT(*) AS simulations
      FROM simulation_runs sr
      JOIN impacts i ON i.impact_uid=sr.impact_uid
      GROUP BY i.dataset_id
    ),
    arts AS (
      SELECT
        i.dataset_id,
        COUNT(*) FILTER (
          WHERE sa.artifact_type='run_directory'
        ) AS run_directories,
        COUNT(*) FILTER (
          WHERE sa.artifact_type='spatial_field' AND sa.metric_name='MPS'
        ) AS mps_fields,
        COUNT(*) FILTER (
          WHERE sa.artifact_type='spatial_field' AND sa.metric_name='MPSR'
        ) AS mpsr_fields
      FROM simulation_artifacts sa
      JOIN simulation_runs sr ON sr.simulation_uid=sa.simulation_uid
      JOIN impacts i ON i.impact_uid=sr.impact_uid
      GROUP BY i.dataset_id
    )
    SELECT
      ic.dataset_id,
      ic.impacts,
      COALESCE(k.raw_records,0) AS raw_records,
      COALESCE(k.processed_records,0) AS processed_records,
      COALESCE(k.kinematic_records,0) AS kinematic_records,
      COALESCE(w.waveform_records,0) AS waveform_records,
      COALESCE(s.simulations,0) AS simulations,
      COALESCE(a.run_directories,0) AS run_directories,
      COALESCE(a.mps_fields,0) AS mps_fields,
      COALESCE(a.mpsr_fields,0) AS mpsr_fields
    FROM impact_counts ic
    LEFT JOIN kine k USING(dataset_id)
    LEFT JOIN wf w USING(dataset_id)
    LEFT JOIN sims s USING(dataset_id)
    LEFT JOIN arts a USING(dataset_id)
    ORDER BY ic.dataset_id
    """
    df = query_df(sql, db_path=db_path)
    df.insert(1, "label", df["dataset_id"].map(DATASET_LABELS).fillna(""))
    df.insert(
        2,
        "status",
        df["dataset_id"].map(
            lambda x: "PAUSED" if x in PAUSED_DATASETS else "FINALISED"
        ),
    )
    return df


def dataset_impacts(
    dataset_id: str,
    db_path: str = DB_PATH,
    limit: int = 5000,
) -> pd.DataFrame:
    sql = """
    SELECT
      i.impact_uid,
      i.source_impact_id,
      i.source_sample_id,
      i.impact_timestamp,
      i.participant_uid,
      COUNT(DISTINCT kr.kinematic_uid) AS kinematic_records,
      COUNT(DISTINCT CASE WHEN kr.data_stage='raw' THEN kr.kinematic_uid END)
        AS raw_records,
      COUNT(DISTINCT CASE WHEN kr.data_stage='processed' THEN kr.kinematic_uid END)
        AS processed_records,
      COUNT(DISTINCT sr.simulation_uid) AS simulations,
      COUNT(DISTINCT CASE WHEN sa.metric_name='MPS' THEN sa.artifact_uid END)
        AS mps_fields,
      COUNT(DISTINCT CASE WHEN sa.metric_name='MPSR' THEN sa.artifact_uid END)
        AS mpsr_fields
    FROM impacts i
    LEFT JOIN kinematic_records kr ON kr.impact_uid=i.impact_uid
    LEFT JOIN simulation_runs sr ON sr.impact_uid=i.impact_uid
    LEFT JOIN simulation_artifacts sa ON sa.simulation_uid=sr.simulation_uid
    WHERE i.dataset_id=?
    GROUP BY ALL
    ORDER BY impact_timestamp NULLS FIRST, source_impact_id
    LIMIT ?
    """
    return query_df(sql, [dataset_id, limit], db_path)


def find_impacts(
    term: str,
    dataset_id: str | None = None,
    db_path: str = DB_PATH,
    limit: int = 100,
) -> pd.DataFrame:
    term = term.strip()
    if not term:
        return pd.DataFrame()

    like = f"%{term.lower()}%"
    where = """
      (
        LOWER(i.impact_uid) LIKE ?
        OR LOWER(COALESCE(i.source_impact_id,'')) LIKE ?
        OR LOWER(COALESCE(i.source_sample_id,'')) LIKE ?
      )
    """
    params = [like, like, like]

    if dataset_id:
        where += " AND i.dataset_id=?"
        params.append(dataset_id)

    params.append(limit)

    sql = f"""
    SELECT
      i.dataset_id,
      i.impact_uid,
      i.source_impact_id,
      i.source_sample_id,
      i.impact_timestamp,
      i.participant_uid
    FROM impacts i
    WHERE {where}
    ORDER BY i.dataset_id, i.source_impact_id
    LIMIT ?
    """
    return query_df(sql, params, db_path)


def impact_detail(impact_uid: str, db_path: str = DB_PATH):
    impact = query_df(
        "SELECT * FROM impacts WHERE impact_uid=?",
        [impact_uid],
        db_path,
    )

    kine = query_df(
        """
        SELECT
          kr.*,
          CASE WHEN kw.kinematic_uid IS NULL THEN FALSE ELSE TRUE END
            AS waveform_in_duckdb
        FROM kinematic_records kr
        LEFT JOIN kinematic_waveforms kw
          ON kw.kinematic_uid=kr.kinematic_uid
        WHERE kr.impact_uid=?
        ORDER BY kr.data_stage, kr.kinematic_uid
        """,
        [impact_uid],
        db_path,
    )

    sims = query_df(
        """
        SELECT *
        FROM simulation_runs
        WHERE impact_uid=?
        ORDER BY simulation_uid
        """,
        [impact_uid],
        db_path,
    )

    artifacts = query_df(
        """
        SELECT sa.*
        FROM simulation_artifacts sa
        JOIN simulation_runs sr
          ON sr.simulation_uid=sa.simulation_uid
        WHERE sr.impact_uid=?
        ORDER BY sa.simulation_uid, sa.artifact_type, sa.metric_name
        """,
        [impact_uid],
        db_path,
    )

    return impact, kine, sims, artifacts


def waveform(kinematic_uid: str, db_path: str = DB_PATH):
    meta = query_df(
        "SELECT * FROM kinematic_records WHERE kinematic_uid=?",
        [kinematic_uid],
        db_path,
    )
    wf = query_df(
        "SELECT * FROM kinematic_waveforms WHERE kinematic_uid=?",
        [kinematic_uid],
        db_path,
    )

    if meta.empty or wf.empty:
        return meta, pd.DataFrame()

    row = wf.iloc[0]
    meta_row = meta.iloc[0]

    n = int(meta_row["n_samples"]) if pd.notna(meta_row["n_samples"]) else None
    dt = (
        float(meta_row["sample_interval_ms"])
        if pd.notna(meta_row["sample_interval_ms"])
        else None
    )

    candidates = {
        "LA X (g)": "linear_acc_x_g",
        "LA Y (g)": "linear_acc_y_g",
        "LA Z (g)": "linear_acc_z_g",
        "LA |a| (g)": "linear_acc_mag_g",
        "AV X (rad/s)": "angular_vel_x_rad_s",
        "AV Y (rad/s)": "angular_vel_y_rad_s",
        "AV Z (rad/s)": "angular_vel_z_rad_s",
        "AV |ω| (rad/s)": "angular_vel_mag_rad_s",
        "AA X (rad/s²)": "angular_acc_x_rad_s2",
        "AA Y (rad/s²)": "angular_acc_y_rad_s2",
        "AA Z (rad/s²)": "angular_acc_z_rad_s2",
        "AA |α| (rad/s²)": "angular_acc_mag_rad_s2",
    }

    arrays = {}
    for label, col in candidates.items():
        if col in wf.columns and row[col] is not None:
            try:
                arrays[label] = list(row[col])
            except TypeError:
                pass

    if not arrays:
        return meta, pd.DataFrame()

    inferred_n = min(len(v) for v in arrays.values())
    n = inferred_n if n is None or n <= 0 else min(n, inferred_n)

    if dt is None or dt <= 0:
        x = list(range(n))
        x_label = "Sample"
    else:
        x = [i * dt for i in range(n)]
        x_label = "Time (ms)"

    data = {x_label: x}
    for label, values in arrays.items():
        data[label] = values[:n]

    return meta, pd.DataFrame(data)


def impact_export(
    dataset_ids: list[str],
    stages: list[str],
    sim_filter: str,
    db_path: str = DB_PATH,
) -> pd.DataFrame:
    where_clauses = []
    stage_params: list[str] = []
    where_params: list[str] = []

    stage_expr = "TRUE"
    if stages:
        placeholders = ",".join(["?"] * len(stages))
        stage_expr = f"kr.data_stage IN ({placeholders})"
        stage_params.extend(stages)

    if dataset_ids:
        placeholders = ",".join(["?"] * len(dataset_ids))
        where_clauses.append(f"i.dataset_id IN ({placeholders})")
        where_params.extend(dataset_ids)

    if sim_filter == "Has simulation":
        where_clauses.append(
            "EXISTS (SELECT 1 FROM simulation_runs sx WHERE sx.impact_uid=i.impact_uid)"
        )
    elif sim_filter == "No simulation":
        where_clauses.append(
            "NOT EXISTS (SELECT 1 FROM simulation_runs sx WHERE sx.impact_uid=i.impact_uid)"
        )

    where = " AND ".join(where_clauses) if where_clauses else "TRUE"

    sql = f"""
    SELECT
      i.dataset_id,
      i.impact_uid,
      i.source_impact_id,
      i.source_sample_id,
      i.impact_timestamp,
      i.participant_uid,
      COUNT(DISTINCT CASE WHEN {stage_expr} THEN kr.kinematic_uid END)
        AS selected_kinematic_records,
      COUNT(DISTINCT CASE WHEN kr.data_stage='raw' THEN kr.kinematic_uid END)
        AS raw_records,
      COUNT(DISTINCT CASE WHEN kr.data_stage='processed' THEN kr.kinematic_uid END)
        AS processed_records,
      COUNT(DISTINCT sr.simulation_uid) AS simulations,
      COUNT(DISTINCT CASE WHEN sa.metric_name='MPS' THEN sa.artifact_uid END)
        AS mps_fields,
      COUNT(DISTINCT CASE WHEN sa.metric_name='MPSR' THEN sa.artifact_uid END)
        AS mpsr_fields
    FROM impacts i
    LEFT JOIN kinematic_records kr ON kr.impact_uid=i.impact_uid
    LEFT JOIN simulation_runs sr ON sr.impact_uid=i.impact_uid
    LEFT JOIN simulation_artifacts sa ON sa.simulation_uid=sr.simulation_uid
    WHERE {where}
    GROUP BY ALL
    ORDER BY i.dataset_id, i.source_impact_id
    """

    # SQL placeholder order is stage expression first, then WHERE filters.
    params = stage_params + where_params
    return query_df(sql, params, db_path)


def schema_tables(db_path: str = DB_PATH) -> pd.DataFrame:
    return query_df(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='main' AND table_type='BASE TABLE'
        ORDER BY table_name
        """,
        db_path=db_path,
    )


def table_preview(
    table: str,
    db_path: str = DB_PATH,
    limit: int = 100,
) -> pd.DataFrame:
    valid = set(schema_tables(db_path)["table_name"].tolist())
    if table not in valid:
        raise ValueError("Unknown table")

    return query_df(
        f'SELECT * FROM "{table}" LIMIT ?',
        [limit],
        db_path,
    )
