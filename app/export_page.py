"""Streamlit build-export page: impact-level numeric CSV or data-bearing ZIP."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import data_exports as ex


@st.cache_data(ttl=60, show_spinner=False)
def _cached_metric_catalog(db_path: str, datasets: tuple[str, ...]) -> pd.DataFrame:
    return ex.metric_catalog(db_path, list(datasets))


@st.cache_data(ttl=60, show_spinner=False)
def _cached_channels(db_path: str) -> dict[str, str]:
    return ex.available_channels(db_path)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_dataset_columns(db_path: str) -> dict[str, str]:
    return ex.dataset_metadata_options(db_path)


def _filename(kind: str, built_at: datetime) -> str:
    return f"head_impact_export_{built_at.strftime('%Y-%m-%d_%H-%M-%S')}.{kind}"


def render_export(summary: pd.DataFrame, db_path: str, db_module) -> None:
    st.title("Build an export")
    st.write(
        "Choose datasets, impacts, metadata and metrics. Download one row "
        "per impact as CSV, or include kinematic traces / simulation files in a ZIP."
    )
    default = summary.loc[summary["status"] == "FINALISED", "dataset_id"].tolist()[:1]
    datasets = st.multiselect(
        "1. Datasets", summary["dataset_id"].tolist(), default=default,
        format_func=lambda x: f"{x} — {db_module.DATASET_LABELS.get(x, '')}",
    )
    if not datasets:
        st.info("Select at least one dataset to start building your export.")
        return
    if any(ds in db_module.PAUSED_DATASETS for ds in datasets):
        st.warning("A paused dataset is selected. Check its QC/provenance before using the data.")
    with st.expander("Dataset availability", expanded=False):
        availability = summary.loc[summary["dataset_id"].isin(datasets)].copy()
        display = ["dataset_id", "impacts", "raw_records", "processed_records",
                   "waveform_records", "simulations", "mps_fields", "mpsr_fields"]
        st.dataframe(availability[display].rename(columns={
            "raw_records": "Raw records (count)",
            "processed_records": "Processed records (count)",
            "waveform_records": "Waveform records (count)",
            "simulations": "Simulation runs (count)",
            "mps_fields": "MPS spatial files (count)",
            "mpsr_fields": "MPSR spatial files (count)",
        }), hide_index=True, use_container_width=True)
        st.caption("Values are record/file counts, not binary availability flags. "
                   "Zero means no registered records, not necessarily that the source data never existed.")

    left, right = st.columns(2)
    with left:
        sim_filter = st.selectbox("Simulation filter", ["Any", "Has simulation", "No simulation"])
    with right:
        search = st.text_input("Impact ID contains (optional)", placeholder="e.g. Rugby261")
    try:
        impacts = ex.impact_rows(db_path, datasets, sim_filter, search)
    except Exception as exc:
        st.error(f"Could not query impacts: {exc}")
        return
    st.caption(f"{len(impacts):,} matching impacts")
    if impacts.empty:
        st.warning("There are no matching impacts.")
        return
    with st.expander("Choose individual impacts (optional)"):
        st.caption("Leave this empty to export all matching impacts. "
                   "For ZIP downloads, narrow the selection to at most 100 impacts.")
        picks = st.multiselect("Impact UIDs", impacts["impact_uid"].tolist())
    if picks:
        impacts = impacts.loc[impacts["impact_uid"].isin(picks)].copy()
    st.caption(f"Export selection: {len(impacts):,} impacts")

    st.subheader("2. Select columns")
    st.caption("Dataset ID and impact UID are always included in your export.")
    columns = st.multiselect(
        "Impact metadata columns", [c for c in ex.BASE_COLUMNS
                                    if c not in ("dataset_id", "impact_uid")],
        default=["source_impact_id", "participant_uid"],
        format_func=lambda c: ex.BASE_COLUMNS[c],
        help="Additional identifiers and timestamps associated with each impact.",
    )
    try:
        dataset_options = _cached_dataset_columns(db_path)
        dataset_columns = st.multiselect(
            "Dataset metadata columns", list(dataset_options),
            format_func=lambda c: dataset_options[c],
            help="Dataset-level information (e.g. mouthguard model, sport, source, "
                 "sampling parameters) is repeated for each matching impact.",
        )
        catalog = _cached_metric_catalog(db_path, tuple(datasets))
    except Exception as exc:
        st.error(f"Could not read metadata/metric options: {exc}")
        return
    keys = list(dict.fromkeys(catalog["metric_key"].tolist())) if not catalog.empty else []
    key_to_label = dict(zip(catalog["metric_key"], catalog["label"]))
    metric_keys = st.multiselect(
        "Metrics",
        options=keys, format_func=lambda k: key_to_label.get(k, k),
        help=("Brain injury metrics are scalar measures of head loading or estimated "
              "brain response; they do not diagnose brain injury. Common terms:\n\n"
              "PLA — Peak Linear Acceleration; PRA/PAA — Peak Rotational/Angular "
              "Acceleration; PRV/PAV — Peak Rotational/Angular Velocity; "
              "BrIC — Brain Injury Criterion; HIC — Head Injury Criterion; "
              "DAMAGE — Diffuse Axonal Multi-Axis General Evaluation; "
              "MPS — Maximum Principal Strain; MPSR — Maximum Principal Strain "
              "Rate; MPS90/MPS95 — 90th/95th percentile MPS. "
              "Available selections depend on your datasets."),
    )
    if not catalog.empty:
        with st.expander("Metric coverage by dataset (N/A = no values recorded)"):
            coverage = catalog.pivot_table(index="label", columns="dataset_id",
                                           values="impacts_with_value", aggfunc="max")
            coverage = coverage.reindex(columns=datasets)
            for ds in datasets:
                coverage[ds] = coverage[ds].map(lambda x: "N/A" if pd.isna(x) or int(x) == 0
                                               else f"{int(x):,} impacts")
            st.dataframe(coverage.reset_index().rename(columns={"label": "Metric"}),
                         hide_index=True, use_container_width=True)
            st.caption("A blank cell in the resulting CSV means no measurement is available "
                       "for that impact. It is never interpreted as zero.")
    else:
        st.info("No metrics are registered for the selected datasets "
                "(N/A). Waveforms or NAS files may still be available.")
    if not summary.loc[summary["dataset_id"].isin(datasets), "simulations"].gt(0).any():
        st.caption("Simulation data: N/A in the currently selected datasets.")

    st.subheader("3. Additional files (optional)")
    waveform = st.checkbox("Include actual kinematic time-series CSVs")
    # Raw/processed applies to waveform export only, not scalar metrics.
    stages = ["processed"]
    channels: list[str] = []
    if waveform:
        stages = st.multiselect(
            "Kinematic time-series stage", ["processed", "raw"],
            default=["processed"],
            help="Choose raw and/or processed waveform recordings. "
                 "This does not change the selected scalar metrics.",
        )
        channel_map = _cached_channels(db_path)
        channels = st.multiselect(
            "Waveform channels", list(channel_map),
            default=list(channel_map), format_func=lambda c: channel_map[c],
        )
        if not stages:
            st.warning("Select at least one kinematic stage to include waveforms.")
    nifti = st.checkbox("Include registered simulation NIfTI spatial fields from NAS")
    source_files = st.checkbox(
        "Include registered source kinematic files from NAS",
        help="If time-series CSVs are also selected, the selected stages apply. "
             "Otherwise, processed source files are included where registered.",
    )
    use_zip = waveform or nifti or source_files
    if nifti or source_files:
        if os.environ.get("HEAD_IMPACT_ENABLE_NAS_DOWNLOADS") != "1":
            st.warning("NAS file downloads are disabled on this server. The administrator must "
                       "enable them after verifying user access and server network restrictions.")
        st.caption("Only catalogued, individual files are exported; entire simulation "
                   "directories are never crawled. Missing references are listed in the ZIP manifest.")
    if use_zip:
        st.info("ZIP mode: impact_summary.csv + selected files + export_manifest.csv. "
                "Interactive limit: 100 impacts, 100 MiB uncompressed and 500 files.")
    else:
        st.caption("CSV mode: one impact per row, selected metadata and metrics as columns.")

    # Keep stale exports from being downloaded after filters/selections change.
    signature = repr((tuple(datasets), sim_filter, search, tuple(picks),
                      tuple(columns), tuple(dataset_columns), tuple(metric_keys), tuple(stages),
                      waveform, tuple(channels), nifti, source_files))
    if st.session_state.get("export_v2_signature") != signature:
        st.session_state.pop("export_v2_result", None)
    if st.button("Build export", type="primary", use_container_width=True):
        try:
            if use_zip and len(impacts) > ex.MAX_ZIP_IMPACTS:
                raise ex.ExportError("For a ZIP, select no more than 100 impacts. "
                                     "Use the impact filter or individual-impact selector above.")
            if waveform and not channels:
                raise ex.ExportError("Select at least one waveform channel.")
            if waveform and not stages:
                raise ex.ExportError("Select a raw or processed kinematics stage.")
            with st.spinner("Retrieving the selected measurements and files…"):
                numeric_summary = ex.build_summary(
                    db_path, impacts, columns, metric_keys, dataset_columns,
                )
                if use_zip:
                    download, manifest = ex.build_zip(
                        db_path, impacts, numeric_summary, stages, channels,
                        include_waveforms=waveform,
                        include_source_files=source_files,
                        include_nifti=nifti,
                    )
                    result = ("zip", download, numeric_summary, manifest)
                else:
                    result = ("csv", numeric_summary.to_csv(index=False).encode("utf-8-sig"),
                              numeric_summary, None)
            built_at = datetime.now(ZoneInfo("Europe/London"))
            st.session_state["export_v2_result"] = (*result, _filename(result[0], built_at))
            st.session_state["export_v2_signature"] = signature
        except (ex.ExportError, PermissionError, OSError, ValueError) as exc:
            st.error(f"Export not built: {exc}")
    result = st.session_state.get("export_v2_result")
    if result and st.session_state.get("export_v2_signature") == signature:
        kind, download, summary_df, manifest, filename = result
        st.success(f"Built {len(summary_df):,} impact records.")
        st.dataframe(summary_df.head(100), hide_index=True, use_container_width=True)
        if manifest is not None:
            unexpected = manifest.loc[~manifest["status"].isin(["included", "duplicate"])]
            if not unexpected.empty:
                st.warning(f"{len(unexpected)} files/records could not be included. "
                           "Consult export_manifest.csv in the ZIP.")
                st.dataframe(unexpected, hide_index=True, use_container_width=True)
        st.download_button(
            f"Download {kind.upper()}", data=download,
            file_name=filename,
            mime="application/zip" if kind == "zip" else "text/csv",
            use_container_width=True,
        )
