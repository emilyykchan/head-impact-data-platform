-- head_impacts.main.biomarker_results definition

CREATE TABLE biomarker_results(biomarker_result_uid VARCHAR PRIMARY KEY, sample_uid VARCHAR NOT NULL, biomarker_name VARCHAR NOT NULL, "value" DOUBLE, unit VARCHAR, transformation VARCHAR, source_reported BOOLEAN, notes VARCHAR, FOREIGN KEY (sample_uid) REFERENCES biospecimen_samples(sample_uid));


-- head_impacts.main.biospecimen_samples definition

CREATE TABLE biospecimen_samples(sample_uid VARCHAR PRIMARY KEY, participant_uid VARCHAR NOT NULL, event_uid VARCHAR, impact_uid VARCHAR, source_sample_id VARCHAR, sample_type VARCHAR, timepoint_label VARCHAR, collection_timestamp TIMESTAMP, notes VARCHAR, FOREIGN KEY (participant_uid) REFERENCES participants(participant_uid), FOREIGN KEY (event_uid) REFERENCES events(event_uid), FOREIGN KEY (impact_uid) REFERENCES impacts(impact_uid));


-- head_impacts.main.brain_metric_results definition

CREATE TABLE brain_metric_results(brain_metric_uid VARCHAR PRIMARY KEY, simulation_uid VARCHAR NOT NULL, roi_name VARCHAR NOT NULL, metric_name VARCHAR NOT NULL, summary_method VARCHAR NOT NULL, "value" DOUBLE, unit VARCHAR, source_artifact_uid VARCHAR, extraction_version VARCHAR, notes VARCHAR, FOREIGN KEY (simulation_uid) REFERENCES simulation_runs(simulation_uid), FOREIGN KEY (source_artifact_uid) REFERENCES simulation_artifacts(artifact_uid));


-- head_impacts.main.datasets definition

CREATE TABLE datasets(dataset_id VARCHAR PRIMARY KEY, dataset_name VARCHAR NOT NULL, sport VARCHAR, device VARCHAR, source_institution VARCHAR, expected_impact_count INTEGER, raw_available BOOLEAN, processed_available BOOLEAN, default_sample_interval_ms DOUBLE, sample_interval_variable BOOLEAN DEFAULT(CAST('f' AS BOOLEAN)), default_window_length_samples INTEGER, window_length_variable BOOLEAN DEFAULT(CAST('f' AS BOOLEAN)), notes VARCHAR, default_window_length_ms DOUBLE);


-- head_impacts.main.event_participants definition

CREATE TABLE event_participants(event_participant_uid VARCHAR PRIMARY KEY, event_uid VARCHAR NOT NULL, participant_uid VARCHAR NOT NULL, weight_class VARCHAR, participant_event_number INTEGER, notes VARCHAR, FOREIGN KEY (event_uid) REFERENCES events(event_uid), FOREIGN KEY (participant_uid) REFERENCES participants(participant_uid), UNIQUE(event_uid, participant_uid));


-- head_impacts.main.events definition

CREATE TABLE events(event_uid VARCHAR PRIMARY KEY, dataset_id VARCHAR NOT NULL, source_event_id VARCHAR, event_type VARCHAR, event_label VARCHAR, event_date DATE, notes VARCHAR, FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id));


-- head_impacts.main.impact_annotations definition

CREATE TABLE impact_annotations(annotation_uid VARCHAR PRIMARY KEY, impact_uid VARCHAR NOT NULL, attribute_name VARCHAR NOT NULL, value_text VARCHAR, value_numeric DOUBLE, value_boolean BOOLEAN, annotation_scheme VARCHAR, annotation_source VARCHAR, annotation_version VARCHAR, notes VARCHAR, FOREIGN KEY (impact_uid) REFERENCES impacts(impact_uid));


-- head_impacts.main.impacts definition

CREATE TABLE impacts(impact_uid VARCHAR PRIMARY KEY, dataset_id VARCHAR NOT NULL, participant_uid VARCHAR, event_uid VARCHAR, source_impact_id VARCHAR, source_sample_id VARCHAR, impact_timestamp TIMESTAMP, impact_time TIME, source_row_number INTEGER, notes VARCHAR, FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id), FOREIGN KEY (participant_uid) REFERENCES participants(participant_uid), FOREIGN KEY (event_uid) REFERENCES events(event_uid));


-- head_impacts.main.kinematic_metrics definition

CREATE TABLE kinematic_metrics(metric_uid VARCHAR PRIMARY KEY, kinematic_uid VARCHAR NOT NULL, metric_name VARCHAR NOT NULL, "value" DOUBLE, unit VARCHAR, metric_source VARCHAR, calculation_method VARCHAR, calculation_version VARCHAR, notes VARCHAR, FOREIGN KEY (kinematic_uid) REFERENCES kinematic_records(kinematic_uid));


-- head_impacts.main.kinematic_records definition

CREATE TABLE kinematic_records(kinematic_uid VARCHAR PRIMARY KEY, impact_uid VARCHAR NOT NULL, data_stage VARCHAR NOT NULL, sample_interval_ms DOUBLE, n_samples INTEGER, linear_acc_unit VARCHAR, angular_vel_unit VARCHAR, angular_acc_unit VARCHAR, file_format VARCHAR, storage_root VARCHAR, relative_path VARCHAR, source_file_name VARCHAR, notes VARCHAR, FOREIGN KEY (impact_uid) REFERENCES impacts(impact_uid), CHECK(((sample_interval_ms IS NULL) OR (sample_interval_ms > 0))), CHECK(((n_samples IS NULL) OR (n_samples > 0))));


-- head_impacts.main.kinematic_waveforms definition

CREATE TABLE kinematic_waveforms(kinematic_uid VARCHAR PRIMARY KEY, linear_acc_x_g DOUBLE[], linear_acc_y_g DOUBLE[], linear_acc_z_g DOUBLE[], linear_acc_mag_g DOUBLE[], angular_vel_x_rad_s DOUBLE[], angular_vel_y_rad_s DOUBLE[], angular_vel_z_rad_s DOUBLE[], angular_vel_mag_rad_s DOUBLE[], angular_acc_x_rad_s2 DOUBLE[], angular_acc_y_rad_s2 DOUBLE[], angular_acc_z_rad_s2 DOUBLE[], angular_acc_mag_rad_s2 DOUBLE[], FOREIGN KEY (kinematic_uid) REFERENCES kinematic_records(kinematic_uid));


-- head_impacts.main.processing_runs definition

CREATE TABLE processing_runs(processing_run_uid VARCHAR PRIMARY KEY, input_kinematic_uid VARCHAR, output_kinematic_uid VARCHAR, pipeline_name VARCHAR, pipeline_version VARCHAR, code_commit VARCHAR, processed_at TIMESTAMP, notes VARCHAR, FOREIGN KEY (input_kinematic_uid) REFERENCES kinematic_records(kinematic_uid), FOREIGN KEY (output_kinematic_uid) REFERENCES kinematic_records(kinematic_uid));


-- head_impacts.main.simulation_artifacts definition

CREATE TABLE simulation_artifacts(artifact_uid VARCHAR PRIMARY KEY, simulation_uid VARCHAR NOT NULL, artifact_type VARCHAR NOT NULL, metric_name VARCHAR, file_format VARCHAR, storage_root VARCHAR, relative_path VARCHAR, size_bytes BIGINT, checksum_sha256 VARCHAR, notes VARCHAR, FOREIGN KEY (simulation_uid) REFERENCES simulation_runs(simulation_uid));


-- head_impacts.main.simulation_parameters definition

CREATE TABLE simulation_parameters(parameter_uid VARCHAR PRIMARY KEY, simulation_uid VARCHAR NOT NULL, parameter_name VARCHAR NOT NULL, value_numeric DOUBLE, value_text VARCHAR, unit VARCHAR, notes VARCHAR, FOREIGN KEY (simulation_uid) REFERENCES simulation_runs(simulation_uid));


-- head_impacts.main.simulation_runs definition

CREATE TABLE simulation_runs(simulation_uid VARCHAR PRIMARY KEY, impact_uid VARCHAR NOT NULL, input_kinematic_uid VARCHAR, model_name VARCHAR, model_version VARCHAR, scaling_method VARCHAR, subject_specific BOOLEAN, simulation_status VARCHAR, run_timestamp TIMESTAMP, notes VARCHAR, FOREIGN KEY (impact_uid) REFERENCES impacts(impact_uid), FOREIGN KEY (input_kinematic_uid) REFERENCES kinematic_records(kinematic_uid));


-- head_impacts.main.participants definition

CREATE TABLE participants(participant_uid VARCHAR PRIMARY KEY, dataset_id VARCHAR NOT NULL, participant_source_id VARCHAR, sex VARCHAR, notes VARCHAR, FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id), UNIQUE(dataset_id, participant_source_id));