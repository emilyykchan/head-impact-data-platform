-- Author: Emily Chan
-- Date: 22/09/2026

-- INGESTION QUALITY CHECK

-- ======= dataset integrity check ========
SELECT
    d.dataset_id,
    d.dataset_name,
    d.sport,
    d.device,
    d.received_month,

    (
        SELECT COUNT(*)
        FROM participants p
        WHERE p.dataset_id = d.dataset_id
    ) AS participants,

    (
        SELECT COUNT(*)
        FROM events e
        WHERE e.dataset_id = d.dataset_id
    ) AS events,

    (
        SELECT COUNT(*)
        FROM impacts i
        WHERE i.dataset_id = d.dataset_id
    ) AS impacts,

    (
        SELECT COUNT(*)
        FROM kinematic_records kr
        JOIN impacts i
            ON kr.impact_uid = i.impact_uid
        WHERE i.dataset_id = d.dataset_id
          AND kr.data_stage = 'raw'
    ) AS raw_kinematics,

    (
        SELECT COUNT(*)
        FROM kinematic_records kr
        JOIN impacts i
            ON kr.impact_uid = i.impact_uid
        WHERE i.dataset_id = d.dataset_id
          AND kr.data_stage = 'processed'
    ) AS processed_kinematics

FROM datasets d
ORDER BY d.dataset_id;

-- ======= waveform integrity check ========
SELECT
    i.dataset_id,
    kr.data_stage,
    COUNT(*) AS n_records,
    MIN(kr.n_samples) AS min_samples,
    MAX(kr.n_samples) AS max_samples,
    MIN(kr.sample_interval_ms) AS min_dt_ms,
    MAX(kr.sample_interval_ms) AS max_dt_ms
FROM kinematic_records kr
JOIN impacts i
    ON kr.impact_uid = i.impact_uid
GROUP BY
    i.dataset_id,
    kr.data_stage
ORDER BY
    i.dataset_id,
    kr.data_stage;

-- ======= Check impacts_without_kinematics; expect empty =====
SELECT
    i.dataset_id,
    COUNT(*) AS impacts_without_kinematics
FROM impacts i
LEFT JOIN kinematic_records kr
    ON i.impact_uid = kr.impact_uid
WHERE kr.kinematic_uid IS NULL
GROUP BY i.dataset_id
ORDER BY i.dataset_id;

-- ======= Check waveform array; expect empty =======
SELECT
    i.dataset_id,
    COUNT(*) AS kinematics_without_waveform
FROM kinematic_records kr
JOIN impacts i
    ON kr.impact_uid = i.impact_uid
LEFT JOIN kinematic_waveforms kw
    ON kr.kinematic_uid = kw.kinematic_uid
WHERE kr.data_stage = 'processed'
  AND kw.kinematic_uid IS NULL
GROUP BY i.dataset_id
ORDER BY i.dataset_id;