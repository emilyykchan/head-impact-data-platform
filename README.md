# Head Impact Data Platform

**Last updated: 10 November 2026**

Research data platform developed within the [HEAD Lab](https://headlab.org/) at Imperial College London for organising and linking multimodal head-impact data.

The platform uses:

- **DuckDB** for structured metadata, IDs, relationships and provenance
- **NAS storage** for large research files and simulation artefacts
- **Streamlit** as a read-only interface for browsing, checking data availability and exporting clean linked data

The main aims are to:

- maintain consistent and unique IDs across datasets;
- link kinematics, simulations, brain metrics and other associated data;
- identify missing or incomplete data;
- provide a single canonical source for clean research exports.

## Repository structure

```text
app/      Streamlit interface and application code
schema/   DuckDB schema
sql/      ingestion and QC scripts
```

Research data and DuckDB database files are not stored in this repository.

For local setup and running the Streamlit app, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Architecture

The platform separates software, structured metadata and large research files.
                     Head Impact Data Platform

                              Streamlit
                           read-only interface
                                  │
                                  ▼
                               DuckDB
                    metadata · IDs · relationships
                     provenance · availability · QC
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
          Embedded structured data       External research storage
           e.g. selected waveforms       e.g. kinematics, FE runs,
                                         NIfTI spatial fields
External files are referenced using logical storage locations rather than machine-specific absolute paths.

For example:
storage_root = NAS_HEAD_IMPACT
relative_path = DS003_RFU546/...
This allows the same database to be used across local development machines, servers and future institutional infrastructure without rewriting stored file paths.

## Database schema

The diagram below is generated automatically from [`schema/schema.sql`](schema/schema.sql). 
Full table definitions are available in [`schema/schema.sql`](schema/schema.sql).

<!-- SCHEMA_MERMAID_START -->
```mermaid
erDiagram
    biomarker_results
    biospecimen_samples
    brain_metric_results
    datasets
    event_participants
    events
    impact_annotations
    impacts
    kinematic_metrics
    kinematic_records
    kinematic_waveforms
    participants
    processing_runs
    simulation_artifacts
    simulation_parameters
    simulation_runs

    biospecimen_samples ||--o{ biomarker_results : sample_uid
    participants ||--o{ biospecimen_samples : participant_uid
    events ||--o{ biospecimen_samples : event_uid
    impacts ||--o{ biospecimen_samples : impact_uid
    simulation_runs ||--o{ brain_metric_results : simulation_uid
    simulation_artifacts ||--o{ brain_metric_results : source_artifact_uid
    events ||--o{ event_participants : event_uid
    participants ||--o{ event_participants : participant_uid
    datasets ||--o{ events : dataset_id
    impacts ||--o{ impact_annotations : impact_uid
    datasets ||--o{ impacts : dataset_id
    participants ||--o{ impacts : participant_uid
    events ||--o{ impacts : event_uid
    kinematic_records ||--o{ kinematic_metrics : kinematic_uid
    impacts ||--o{ kinematic_records : impact_uid
    kinematic_records ||--o| kinematic_waveforms : kinematic_uid
    kinematic_records ||--o{ processing_runs : input_kinematic_uid
    kinematic_records ||--o{ processing_runs : output_kinematic_uid
    simulation_runs ||--o{ simulation_artifacts : simulation_uid
    simulation_runs ||--o{ simulation_parameters : simulation_uid
    impacts ||--o{ simulation_runs : impact_uid
    kinematic_records ||--o{ simulation_runs : input_kinematic_uid
    datasets ||--o{ participants : dataset_id
```
<!-- SCHEMA_MERMAID_END -->


## Local development

See CONTRIBUTING.md for the current local development workflow, Python environment setup and instructions for running the Streamlit interface.
