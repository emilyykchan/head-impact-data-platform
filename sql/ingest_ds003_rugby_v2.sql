-- =====================================================================
-- DS003 Rugby / Protecht v2 ingestion
-- Source: SWA
-- Sport/category: RFU
-- Expected impacts: 546
-- Sampling interval: 1 ms
-- Window length: 104 ms / 104 samples
-- Kinematics: processed only
-- ContinuousID is absent from the DS003 source CSV and is generated
-- during staging from VALID impact rows only (nonblank ImpactCode), as Rugby1 ... Rugby546.
-- This avoids importing trailing blank rows present in the CSV export.
--
-- NOTE:
-- This script assumes DS003 has the same dataset-level metadata as DS004:
-- RFU / Protecht v2 / SWA / 1 ms / 104 ms / 104 samples / processed only.
-- If only the column structure is the same but any of those metadata differ,
-- edit Section 2 before running.
--
-- IMPORTANT
-- 1. Edit the source CSV path below before running on another machine.
-- 2. The staging table is recreated from the source CSV.
-- 3. Main-table inserts use ON CONFLICT DO NOTHING where possible.
-- 4. QC SELECT statements are safe to rerun.
-- =====================================================================

-- =====================================================================
-- 0. STAGING: load the original CSV as text
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS staging;

CREATE OR REPLACE TABLE staging.rugby_ds003_raw AS
WITH valid_source_rows AS (
    SELECT *
    FROM read_csv_auto(
        '/Users/yc4421/Library/CloudStorage/Box-Box/Github/head-impact-data-platform/data/RugbyMatchImpacts160523e_previously_sent.csv',
        header = true,
        all_varchar = true
    )
    WHERE "ImpactCode" IS NOT NULL
      AND TRIM("ImpactCode") <> ''
),
numbered_rows AS (
    SELECT
        ROW_NUMBER() OVER () AS source_row_number_generated,
        *
    FROM valid_source_rows
)
SELECT
    'Rugby' || CAST(source_row_number_generated AS VARCHAR) AS "ContinuousID",
    * EXCLUDE (source_row_number_generated)
FROM numbered_rows;

SELECT COUNT(*) AS n_source_rows
FROM staging.rugby_ds003_raw;

SELECT *
FROM staging.rugby_ds003_raw
LIMIT 5;

DESCRIBE staging.rugby_ds003_raw;

-- =====================================================================
-- 1. DATASET-LEVEL SCHEMA REFINEMENT + QC
-- =====================================================================

ALTER TABLE datasets
ADD COLUMN IF NOT EXISTS default_window_length_ms DOUBLE;

SELECT
    MIN(array_length(string_split("linear_acc_x", ','))) AS min_samples,
    MAX(array_length(string_split("linear_acc_x", ','))) AS max_samples,
    COUNT(DISTINCT array_length(string_split("linear_acc_x", ','))) AS n_unique_lengths
FROM staging.rugby_ds003_raw;

-- =====================================================================
-- 2. REGISTER DATASET DS003
-- =====================================================================

INSERT INTO datasets (
    dataset_id,
    dataset_name,
    sport,
    device,
    source_institution,
    expected_impact_count,
    raw_available,
    processed_available,
    default_sample_interval_ms,
    sample_interval_variable,
    default_window_length_samples,
    window_length_variable,
    notes,
    default_window_length_ms
)
VALUES (
    'DS003',
    'Protecht v2',
    'RFU',
    'Protecht v2',
    'SWA',
    (SELECT COUNT(*) FROM staging.rugby_ds003_raw),
    FALSE,
    TRUE,
    1.0,
    FALSE,
    104,
    FALSE,
    'Rugby match impact dataset; processed Protecht v2 kinematics',
    104.0
)
ON CONFLICT (dataset_id) DO NOTHING;

SELECT *
FROM datasets
WHERE dataset_id = 'DS003';

-- =====================================================================
-- 3. PARTICIPANTS
-- =====================================================================

SELECT
    COUNT(DISTINCT TRIM("Player ID")) AS n_participants,
    COUNT(*) FILTER (
        WHERE "Player ID" IS NULL
           OR TRIM("Player ID") = ''
    ) AS n_missing_player_id
FROM staging.rugby_ds003_raw;

SELECT DISTINCT TRIM("Player ID") AS player_id
FROM staging.rugby_ds003_raw
WHERE "Player ID" IS NOT NULL
  AND TRIM("Player ID") <> ''
ORDER BY player_id;

INSERT INTO participants (
    participant_uid,
    dataset_id,
    participant_source_id
)
SELECT
    printf(
        'PT_DS003_%04d',
        ROW_NUMBER() OVER (ORDER BY player_id)
    ) AS participant_uid,
    'DS003' AS dataset_id,
    player_id AS participant_source_id
FROM (
    SELECT DISTINCT TRIM("Player ID") AS player_id
    FROM staging.rugby_ds003_raw
    WHERE "Player ID" IS NOT NULL
      AND TRIM("Player ID") <> ''
)
ON CONFLICT DO NOTHING;

SELECT *
FROM participants
WHERE dataset_id = 'DS003'
ORDER BY participant_uid;

SELECT COUNT(*) AS n_inserted_participants
FROM participants
WHERE dataset_id = 'DS003';

-- =====================================================================
-- 4. EVENTS
-- =====================================================================

SELECT
    COUNT(DISTINCT TRIM("Game ID")) AS n_events,
    COUNT(*) FILTER (
        WHERE "Game ID" IS NULL
           OR TRIM("Game ID") = ''
    ) AS n_missing_game_id
FROM staging.rugby_ds003_raw;

SELECT DISTINCT TRIM("Game ID") AS game_id
FROM staging.rugby_ds003_raw
WHERE "Game ID" IS NOT NULL
  AND TRIM("Game ID") <> ''
ORDER BY game_id;

INSERT INTO events (
    event_uid,
    dataset_id,
    source_event_id,
    event_type
)
SELECT
    printf(
        'EVT_DS003_%04d',
        ROW_NUMBER() OVER (ORDER BY game_id)
    ) AS event_uid,
    'DS003' AS dataset_id,
    game_id AS source_event_id,
    'Match' AS event_type
FROM (
    SELECT DISTINCT TRIM("Game ID") AS game_id
    FROM staging.rugby_ds003_raw
    WHERE "Game ID" IS NOT NULL
      AND TRIM("Game ID") <> ''
)
ON CONFLICT DO NOTHING;

SELECT *
FROM events
WHERE dataset_id = 'DS003'
ORDER BY event_uid;

-- =====================================================================
-- 5. PRE-IMPACT LINKAGE QC
-- =====================================================================

SELECT
    r."ImpactCode",
    r."Player ID",
    p.participant_uid,
    r."Game ID",
    e.event_uid,
    r."Sample ID",
    r."Time"
FROM staging.rugby_ds003_raw r
LEFT JOIN participants p
    ON p.dataset_id = 'DS003'
   AND p.participant_source_id = TRIM(r."Player ID")
LEFT JOIN events e
    ON e.dataset_id = 'DS003'
   AND e.source_event_id = TRIM(r."Game ID")
LIMIT 20;

SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (
        WHERE p.participant_uid IS NULL
    ) AS unmatched_participants,
    COUNT(*) FILTER (
        WHERE e.event_uid IS NULL
    ) AS unmatched_events
FROM staging.rugby_ds003_raw r
LEFT JOIN participants p
    ON p.dataset_id = 'DS003'
   AND p.participant_source_id = TRIM(r."Player ID")
LEFT JOIN events e
    ON e.dataset_id = 'DS003'
   AND e.source_event_id = TRIM(r."Game ID");

SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT TRIM("ContinuousID")) AS unique_continuous_ids,
    COUNT(DISTINCT TRIM("ImpactCode")) AS unique_impact_codes
FROM staging.rugby_ds003_raw;

SELECT
    COUNT(*) FILTER (
        WHERE "ContinuousID" IS NULL
           OR TRIM("ContinuousID") = ''
    ) AS missing_continuous_id,
    COUNT(*) FILTER (
        WHERE "ImpactCode" IS NULL
           OR TRIM("ImpactCode") = ''
    ) AS missing_impact_code
FROM staging.rugby_ds003_raw;

-- =====================================================================
-- 6. IMPACTS
-- =====================================================================

INSERT INTO impacts (
    impact_uid,
    dataset_id,
    participant_uid,
    event_uid,
    source_impact_id,
    source_sample_id,
    impact_time,
    notes
)
SELECT
    'IMP_DS003_' || TRIM(r."ContinuousID") AS impact_uid,
    'DS003' AS dataset_id,
    p.participant_uid,
    e.event_uid,
    TRIM(r."ImpactCode") AS source_impact_id,
    TRIM(r."Sample ID") AS source_sample_id,
    TRY_CAST(TRIM(r."Time") AS TIME) AS impact_time,
    'Source ContinuousID: ' || TRIM(r."ContinuousID") AS notes
FROM staging.rugby_ds003_raw r
LEFT JOIN participants p
    ON p.dataset_id = 'DS003'
   AND p.participant_source_id = TRIM(r."Player ID")
LEFT JOIN events e
    ON e.dataset_id = 'DS003'
   AND e.source_event_id = TRIM(r."Game ID")
ON CONFLICT DO NOTHING;

SELECT COUNT(*) AS n_inserted_impacts
FROM impacts
WHERE dataset_id = 'DS003';

SELECT
    i.impact_uid,
    i.source_impact_id,
    p.participant_source_id AS player_id,
    e.source_event_id AS game_id,
    i.source_sample_id,
    i.impact_time
FROM impacts i
LEFT JOIN participants p
    ON i.participant_uid = p.participant_uid
LEFT JOIN events e
    ON i.event_uid = e.event_uid
WHERE i.dataset_id = 'DS003'
ORDER BY i.impact_uid
LIMIT 30;

-- =====================================================================
-- 7. IMPACT ANNOTATIONS
-- =====================================================================

SELECT
    i.impact_uid,
    r."With Ball / Without Ball",
    r."Impact Event",
    r."Simplified Impact Event",
    r."Anatomical Location of Contact1",
    r."Anterior/Posterior1",
    r."Side1",
    r."Contact type"
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
LIMIT 20;

INSERT INTO impact_annotations (
    annotation_uid,
    impact_uid,
    attribute_name,
    value_text,
    annotation_scheme,
    annotation_source
)

SELECT
    'ANN_' || i.impact_uid || '_ball_possession',
    i.impact_uid,
    'ball_possession',
    TRIM(r."With Ball / Without Ball"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."With Ball / Without Ball" IS NOT NULL
  AND TRIM(r."With Ball / Without Ball") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_impact_event',
    i.impact_uid,
    'impact_event',
    TRIM(r."Impact Event"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Impact Event" IS NOT NULL
  AND TRIM(r."Impact Event") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_simplified_impact_event',
    i.impact_uid,
    'simplified_impact_event',
    TRIM(r."Simplified Impact Event"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Simplified Impact Event" IS NOT NULL
  AND TRIM(r."Simplified Impact Event") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_contact_anatomical_location',
    i.impact_uid,
    'contact_anatomical_location',
    TRIM(r."Anatomical Location of Contact1"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Anatomical Location of Contact1" IS NOT NULL
  AND TRIM(r."Anatomical Location of Contact1") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_contact_ap_location',
    i.impact_uid,
    'contact_ap_location',
    TRIM(r."Anterior/Posterior1"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Anterior/Posterior1" IS NOT NULL
  AND TRIM(r."Anterior/Posterior1") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_contact_side',
    i.impact_uid,
    'contact_side',
    TRIM(r."Side1"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Side1" IS NOT NULL
  AND TRIM(r."Side1") <> ''

UNION ALL

SELECT
    'ANN_' || i.impact_uid || '_contact_type',
    i.impact_uid,
    'contact_type',
    TRIM(r."Contact type"),
    'SWA_rugby',
    'source_reported'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE r."Contact type" IS NOT NULL
  AND TRIM(r."Contact type") <> ''

ON CONFLICT DO NOTHING;

SELECT
    attribute_name,
    COUNT(*) AS n_values,
    COUNT(DISTINCT value_text) AS n_categories
FROM impact_annotations ia
JOIN impacts i
    ON ia.impact_uid = i.impact_uid
WHERE i.dataset_id = 'DS003'
GROUP BY attribute_name
ORDER BY attribute_name;

SELECT
    i.source_impact_id,
    ia.attribute_name,
    ia.value_text
FROM impact_annotations ia
JOIN impacts i
    ON ia.impact_uid = i.impact_uid
WHERE i.dataset_id = 'DS003'
ORDER BY i.source_impact_id, ia.attribute_name
LIMIT 50;

-- =====================================================================
-- 8. KINEMATIC WAVEFORM TABLE
-- =====================================================================

CREATE TABLE IF NOT EXISTS kinematic_waveforms (
    kinematic_uid VARCHAR PRIMARY KEY,

    linear_acc_x_g DOUBLE[],
    linear_acc_y_g DOUBLE[],
    linear_acc_z_g DOUBLE[],
    linear_acc_mag_g DOUBLE[],

    angular_vel_x_rad_s DOUBLE[],
    angular_vel_y_rad_s DOUBLE[],
    angular_vel_z_rad_s DOUBLE[],
    angular_vel_mag_rad_s DOUBLE[],

    angular_acc_x_rad_s2 DOUBLE[],
    angular_acc_y_rad_s2 DOUBLE[],
    angular_acc_z_rad_s2 DOUBLE[],
    angular_acc_mag_rad_s2 DOUBLE[],

    FOREIGN KEY (kinematic_uid)
        REFERENCES kinematic_records(kinematic_uid)
);

-- =====================================================================
-- 9. KINEMATIC RECORDS
-- =====================================================================

INSERT INTO kinematic_records (
    kinematic_uid,
    impact_uid,
    data_stage,
    sample_interval_ms,
    n_samples,
    linear_acc_unit,
    angular_vel_unit,
    angular_acc_unit,
    file_format,
    notes
)
SELECT
    'KIN_' || i.impact_uid,
    i.impact_uid,
    'processed',
    1.0,
    array_length(
        string_split(TRIM(r."linear_acc_x"), ',')
    ) AS n_samples,
    'g',
    'rad/s',
    'rad/s^2',
    'DuckDB LIST',
    'Processed Protecht v2 kinematics'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
ON CONFLICT DO NOTHING;

SELECT
    COUNT(*) AS n_records,
    MIN(n_samples) AS min_samples,
    MAX(n_samples) AS max_samples
FROM kinematic_records kr
JOIN impacts i
    ON kr.impact_uid = i.impact_uid
WHERE i.dataset_id = 'DS003';

-- =====================================================================
-- 10. PARSE SOURCE WAVEFORMS INTO NUMERIC LISTS
-- =====================================================================

INSERT INTO kinematic_waveforms (
    kinematic_uid,

    linear_acc_x_g,
    linear_acc_y_g,
    linear_acc_z_g,
    linear_acc_mag_g,

    angular_vel_x_rad_s,
    angular_vel_y_rad_s,
    angular_vel_z_rad_s,
    angular_vel_mag_rad_s,

    angular_acc_x_rad_s2,
    angular_acc_y_rad_s2,
    angular_acc_z_rad_s2,
    angular_acc_mag_rad_s2
)

SELECT
    'KIN_' || i.impact_uid,

    list_transform(string_split(r."linear_acc_x", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."linear_acc_y", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."linear_acc_z", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."linear_acc_mag", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),

    list_transform(string_split(r."angular_vel_x", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_vel_y", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_vel_z", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_vel_mag", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),

    list_transform(string_split(r."angular_acc_x", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_acc_y", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_acc_z", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE)),
    list_transform(string_split(r."angular_acc_mag", ','), x -> TRY_CAST(TRIM(x) AS DOUBLE))

FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
ON CONFLICT DO NOTHING;

SELECT
    kw.kinematic_uid,
    array_length(kw.linear_acc_x_g) AS n_ax,
    array_length(kw.angular_vel_x_rad_s) AS n_avx,
    array_length(kw.angular_acc_x_rad_s2) AS n_aax,
    kw.linear_acc_x_g[1] AS first_ax,
    kw.linear_acc_x_g[2] AS second_ax
FROM kinematic_waveforms kw
JOIN kinematic_records kr
    ON kw.kinematic_uid = kr.kinematic_uid
JOIN impacts i
    ON kr.impact_uid = i.impact_uid
WHERE i.dataset_id = 'DS003'
LIMIT 20;

-- =====================================================================
-- 11. SOURCE-REPORTED KINEMATIC METRICS
-- =====================================================================

INSERT INTO kinematic_metrics (
    metric_uid,
    kinematic_uid,
    metric_name,
    value,
    unit,
    metric_source,
    calculation_method
)

SELECT
    'KM_' || i.impact_uid || '_PLA',
    'KIN_' || i.impact_uid,
    'PLA',
    TRY_CAST(TRIM(r."Linear") AS DOUBLE),
    'g',
    'source_reported',
    'source supplied'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE TRY_CAST(TRIM(r."Linear") AS DOUBLE) IS NOT NULL

UNION ALL

SELECT
    'KM_' || i.impact_uid || '_PAA',
    'KIN_' || i.impact_uid,
    'PAA',
    TRY_CAST(TRIM(r."Rotational") AS DOUBLE),
    'rad/s^2',
    'source_reported',
    'source supplied'
FROM staging.rugby_ds003_raw r
JOIN impacts i
    ON i.dataset_id = 'DS003'
   AND i.source_impact_id = TRIM(r."ImpactCode")
WHERE TRY_CAST(TRIM(r."Rotational") AS DOUBLE) IS NOT NULL

ON CONFLICT DO NOTHING;

-- =====================================================================
-- 12. KINEMATIC METRIC QC
-- =====================================================================

SELECT
    i.source_impact_id,
    km.value AS source_pla_g,
    list_max(kw.linear_acc_mag_g) AS calculated_pla_g,
    km.value - list_max(kw.linear_acc_mag_g) AS difference_g
FROM impacts i
JOIN kinematic_records kr
    ON i.impact_uid = kr.impact_uid
JOIN kinematic_waveforms kw
    ON kr.kinematic_uid = kw.kinematic_uid
JOIN kinematic_metrics km
    ON kr.kinematic_uid = km.kinematic_uid
   AND km.metric_name = 'PLA'
WHERE i.dataset_id = 'DS003'
LIMIT 30;

-- =====================================================================
-- 13. FINAL DS003 SUMMARY QC
-- =====================================================================

SELECT
    (SELECT COUNT(*) FROM staging.rugby_ds003_raw) AS staging_rows,

    (SELECT COUNT(*)
     FROM participants
     WHERE dataset_id = 'DS003') AS participants,

    (SELECT COUNT(*)
     FROM events
     WHERE dataset_id = 'DS003') AS events,

    (SELECT COUNT(*)
     FROM impacts
     WHERE dataset_id = 'DS003') AS impacts,

    (SELECT COUNT(*)
     FROM kinematic_records kr
     JOIN impacts i ON kr.impact_uid = i.impact_uid
     WHERE i.dataset_id = 'DS003') AS kinematic_records,

    (SELECT COUNT(*)
     FROM kinematic_waveforms kw
     JOIN kinematic_records kr ON kw.kinematic_uid = kr.kinematic_uid
     JOIN impacts i ON kr.impact_uid = i.impact_uid
     WHERE i.dataset_id = 'DS003') AS waveform_records,

    (SELECT COUNT(*)
     FROM kinematic_metrics km
     JOIN kinematic_records kr ON km.kinematic_uid = kr.kinematic_uid
     JOIN impacts i ON kr.impact_uid = i.impact_uid
     WHERE i.dataset_id = 'DS003') AS kinematic_metric_rows;
