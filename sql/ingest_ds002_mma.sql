-- =====================================================================
-- DS002 ingestion
-- Prepared staging root: /path/to/_db_staging/ds002
--
-- Run scripts/prepare_remaining_datasets.py before this SQL.
-- Run migrations/001_add_linear_velocity.sql and
-- migrations/002_add_raw_kinematic_channels.sql before ingesting DS008.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS staging;

CREATE OR REPLACE TABLE staging.ds002_impacts AS
SELECT * FROM read_csv_auto(
    '/path/to/_db_staging/ds002/impacts.csv',
    header = true,
    all_varchar = true
);

CREATE OR REPLACE TABLE staging.ds002_annotations AS
SELECT * FROM read_csv_auto(
    '/path/to/_db_staging/ds002/annotations.csv',
    header = true,
    all_varchar = true
);

CREATE OR REPLACE TABLE staging.ds002_kinematics AS
SELECT * FROM read_csv_auto(
    '/path/to/_db_staging/ds002/kinematics.csv',
    header = true,
    all_varchar = true
);

CREATE OR REPLACE TABLE staging.ds002_metrics AS
SELECT * FROM read_csv_auto(
    '/path/to/_db_staging/ds002/metrics.csv',
    header = true,
    all_varchar = true
);


-- ---------------------------------------------------------------------
-- Register dataset
-- ---------------------------------------------------------------------
INSERT INTO datasets (
    dataset_id, dataset_name, sport, device, source_institution,
    expected_impact_count, raw_available, processed_available,
    default_sample_interval_ms, sample_interval_variable,
    default_window_length_samples, window_length_variable,
    received_month, notes, default_window_length_ms
)
VALUES (
    'DS002', 'MMA485', 'MMA', 'Protecht v2', 'SWA',
    485, FALSE, TRUE,
    1.0, FALSE,
    104, FALSE,
    DATE '2023-03-01',
    'MMA impact dataset. Continuous IDs MMA1-MMA485 recovered by local waveform matching; standalone XLSX is the authoritative processed trace; master-sheet waveform is not ingested.', 104.0)
ON CONFLICT (dataset_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Participants
-- ---------------------------------------------------------------------
INSERT INTO participants (
    participant_uid, dataset_id, participant_source_id, sex, notes
)
WITH p AS (
    SELECT
        participant_source_id,
        MAX(NULLIF(participant_sex, '')) AS sex,
        MAX(NULLIF(participant_notes, '')) AS notes
    FROM staging.ds002_impacts
    WHERE NULLIF(participant_source_id, '') IS NOT NULL
    GROUP BY participant_source_id
)
SELECT
    printf('PT_DS002_%04d', ROW_NUMBER() OVER (ORDER BY participant_source_id)),
    'DS002',
    participant_source_id,
    sex,
    notes
FROM p
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Events (optional; datasets without event information simply insert none)
-- ---------------------------------------------------------------------
INSERT INTO events (
    event_uid, dataset_id, source_event_id, event_type, event_label, event_date, notes
)
WITH e AS (
    SELECT
        event_source_id,
        MAX(NULLIF(event_type, '')) AS event_type,
        MAX(NULLIF(event_label, '')) AS event_label,
        MAX(TRY_CAST(NULLIF(event_date, '') AS DATE)) AS event_date,
        MAX(NULLIF(event_notes, '')) AS notes
    FROM staging.ds002_impacts
    WHERE NULLIF(event_source_id, '') IS NOT NULL
    GROUP BY event_source_id
)
SELECT
    printf('EVT_DS002_%04d', ROW_NUMBER() OVER (ORDER BY event_source_id)),
    'DS002',
    event_source_id,
    event_type,
    event_label,
    event_date,
    notes
FROM e
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Event-participant links
-- ---------------------------------------------------------------------
INSERT INTO event_participants (
    event_participant_uid, event_uid, participant_uid,
    weight_class, participant_event_number, notes
)
SELECT
    'EVP_DS002_' ||
      printf('%06d', ROW_NUMBER() OVER (ORDER BY e.event_uid, p.participant_uid)),
    e.event_uid,
    p.participant_uid,
    MAX(NULLIF(s.event_weight_class, '')),
    NULL,
    NULL
FROM staging.ds002_impacts s
JOIN participants p
  ON p.dataset_id = 'DS002'
 AND p.participant_source_id = s.participant_source_id
JOIN events e
  ON e.dataset_id = 'DS002'
 AND e.source_event_id = s.event_source_id
WHERE NULLIF(s.event_source_id, '') IS NOT NULL
  AND NULLIF(s.participant_source_id, '') IS NOT NULL
GROUP BY e.event_uid, p.participant_uid
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Impacts
-- ---------------------------------------------------------------------
INSERT INTO impacts (
    impact_uid, dataset_id, participant_uid, event_uid,
    source_impact_id, source_sample_id, impact_timestamp, impact_time,
    source_row_number, notes
)
SELECT
    'IMP_DS002_' || s.impact_key,
    'DS002',
    p.participant_uid,
    e.event_uid,
    NULLIF(s.source_impact_id, ''),
    NULLIF(s.source_sample_id, ''),
    TRY_CAST(NULLIF(s.impact_timestamp, '') AS TIMESTAMP),
    TRY_CAST(NULLIF(s.impact_time, '') AS TIME),
    TRY_CAST(NULLIF(s.source_row_number, '') AS INTEGER),
    NULLIF(s.notes, '')
FROM staging.ds002_impacts s
LEFT JOIN participants p
  ON p.dataset_id = 'DS002'
 AND p.participant_source_id = s.participant_source_id
LEFT JOIN events e
  ON e.dataset_id = 'DS002'
 AND e.source_event_id = s.event_source_id
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Flexible impact annotations
-- ---------------------------------------------------------------------
INSERT INTO impact_annotations (
    annotation_uid, impact_uid, attribute_name,
    value_text, value_numeric, value_boolean,
    annotation_scheme, annotation_source, annotation_version, notes
)
WITH a AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            ORDER BY impact_key, attribute_name,
                     COALESCE(value_text, ''), COALESCE(value_numeric, ''),
                     COALESCE(value_boolean, '')
        ) AS rn
    FROM staging.ds002_annotations
)
SELECT
    'ANN_DS002_' || printf('%07d', rn),
    'IMP_DS002_' || impact_key,
    attribute_name,
    NULLIF(value_text, ''),
    TRY_CAST(NULLIF(value_numeric, '') AS DOUBLE),
    TRY_CAST(NULLIF(value_boolean, '') AS BOOLEAN),
    NULLIF(annotation_scheme, ''),
    NULLIF(annotation_source, ''),
    NULLIF(annotation_version, ''),
    NULLIF(notes, '')
FROM a
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Kinematic record metadata
-- ---------------------------------------------------------------------
INSERT INTO kinematic_records (
    kinematic_uid, impact_uid, data_stage,
    sample_interval_ms, n_samples,
    linear_acc_unit, angular_vel_unit, angular_acc_unit,
    file_format, storage_root, relative_path, source_file_name, notes,
    linear_vel_unit
)
SELECT
    'KIN_IMP_DS002_' || impact_key || '_' || UPPER(data_stage),
    'IMP_DS002_' || impact_key,
    data_stage,
    TRY_CAST(NULLIF(sample_interval_ms, '') AS DOUBLE),
    TRY_CAST(NULLIF(n_samples, '') AS INTEGER),
    NULLIF(linear_acc_unit, ''),
    NULLIF(angular_vel_unit, ''),
    NULLIF(angular_acc_unit, ''),
    NULLIF(file_format, ''),
    NULLIF(storage_root, ''),
    NULLIF(relative_path, ''),
    NULLIF(source_file_name, ''),
    NULLIF(notes, ''),
    NULLIF(linear_vel_unit, '')
FROM staging.ds002_kinematics
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Standardized physical waveforms
-- ---------------------------------------------------------------------
INSERT INTO kinematic_waveforms (
    kinematic_uid,
    linear_acc_x_g, linear_acc_y_g, linear_acc_z_g, linear_acc_mag_g,
    angular_vel_x_rad_s, angular_vel_y_rad_s, angular_vel_z_rad_s, angular_vel_mag_rad_s,
    angular_acc_x_rad_s2, angular_acc_y_rad_s2, angular_acc_z_rad_s2, angular_acc_mag_rad_s2,
    linear_vel_x_m_s, linear_vel_y_m_s, linear_vel_z_m_s, linear_vel_mag_m_s
)
SELECT
    'KIN_IMP_DS002_' || impact_key || '_' || UPPER(data_stage),

    list_transform(string_split(NULLIF(linear_acc_x_g, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_acc_y_g, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_acc_z_g, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_acc_mag_g, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),

    list_transform(string_split(NULLIF(angular_vel_x_rad_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_vel_y_rad_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_vel_z_rad_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_vel_mag_rad_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),

    list_transform(string_split(NULLIF(angular_acc_x_rad_s2, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_acc_y_rad_s2, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_acc_z_rad_s2, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(angular_acc_mag_rad_s2, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),

    list_transform(string_split(NULLIF(linear_vel_x_m_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_vel_y_m_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_vel_z_m_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(NULLIF(linear_vel_mag_m_s, ''), ','), x -> TRY_CAST(TRIM(x) AS DOUBLE))
FROM staging.ds002_kinematics
WHERE COALESCE(linear_acc_x_g, '') <> ''
   OR COALESCE(linear_acc_y_g, '') <> ''
   OR COALESCE(linear_acc_z_g, '') <> ''
   OR COALESCE(linear_acc_mag_g, '') <> ''
   OR COALESCE(angular_vel_x_rad_s, '') <> ''
   OR COALESCE(angular_acc_x_rad_s2, '') <> ''
   OR COALESCE(linear_vel_x_m_s, '') <> ''
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------
-- Source-reported kinematic metrics
-- ---------------------------------------------------------------------
INSERT INTO kinematic_metrics (
    metric_uid, kinematic_uid, metric_name, "value", unit,
    metric_source, calculation_method, calculation_version, notes
)
WITH m AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            ORDER BY impact_key, data_stage, metric_name, COALESCE("value", '')
        ) AS rn
    FROM staging.ds002_metrics
)
SELECT
    'MET_DS002_' || printf('%07d', rn),
    'KIN_IMP_DS002_' || impact_key || '_' || UPPER(data_stage),
    metric_name,
    TRY_CAST(NULLIF("value", '') AS DOUBLE),
    NULLIF(unit, ''),
    NULLIF(metric_source, ''),
    NULLIF(calculation_method, ''),
    NULLIF(calculation_version, ''),
    NULLIF(notes, '')
FROM m
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------
-- Final QC summary
-- ---------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM staging.ds002_impacts) AS staging_impacts,
    (SELECT COUNT(*) FROM participants WHERE dataset_id = 'DS002') AS participants,
    (SELECT COUNT(*) FROM events WHERE dataset_id = 'DS002') AS events,
    (SELECT COUNT(*) FROM impacts WHERE dataset_id = 'DS002') AS impacts,
    (SELECT COUNT(*)
       FROM kinematic_records kr
       JOIN impacts i ON i.impact_uid = kr.impact_uid
      WHERE i.dataset_id = 'DS002') AS kinematic_records,
    (SELECT COUNT(*)
       FROM kinematic_waveforms kw
       JOIN kinematic_records kr ON kr.kinematic_uid = kw.kinematic_uid
       JOIN impacts i ON i.impact_uid = kr.impact_uid
      WHERE i.dataset_id = 'DS002') AS waveform_records,
    (SELECT COUNT(*)
       FROM kinematic_metrics km
       JOIN kinematic_records kr ON kr.kinematic_uid = km.kinematic_uid
       JOIN impacts i ON i.impact_uid = kr.impact_uid
      WHERE i.dataset_id = 'DS002') AS kinematic_metric_rows;

SELECT
    kr.data_stage,
    COUNT(*) AS n_records,
    MIN(kr.n_samples) AS min_samples,
    MAX(kr.n_samples) AS max_samples,
    MIN(kr.sample_interval_ms) AS min_dt_ms,
    MAX(kr.sample_interval_ms) AS max_dt_ms
FROM kinematic_records kr
JOIN impacts i ON i.impact_uid = kr.impact_uid
WHERE i.dataset_id = 'DS002'
GROUP BY kr.data_stage
ORDER BY kr.data_stage;
