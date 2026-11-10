from __future__ import annotations

from pathlib import Path

import plotly.express as px
import streamlit as st

import db
from config import DB_PATH, STORAGE_ROOTS


NAS_ROOT = STORAGE_ROOTS["NAS_HEAD_IMPACT"]

st.set_page_config(
    page_title="Head Impact Data Platform",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def rerun():
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


def nav_to(page: str):
    st.session_state["page"] = page
    rerun()


def dataframe(df, **kwargs):
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        **kwargs,
    )


def badge(text: str):
    if text == "PAUSED":
        st.warning(
            "DS009 is currently **paused / under reconciliation**. "
            "Existing records are browseable, but the dataset is not "
            "considered finalised."
        )
    elif text == "FINALISED":
        st.success("Dataset status: **FINALISED**")


@st.cache_data(ttl=60)
def cached_summary(path: str):
    return db.dataset_summary(path)


def render_home(summary):
    st.title("Head Impact Data Platform")
    st.caption(
        "Browse curated head-impact datasets, inspect individual impacts, "
        "view kinematics, and build research exports."
    )

    finalised = int((summary["status"] == "FINALISED").sum())
    impacts = int(
        summary.loc[summary["status"] == "FINALISED", "impacts"].sum()
    )
    sims = int(
        summary.loc[summary["status"] == "FINALISED", "simulations"].sum()
    )
    waveforms = int(
        summary.loc[
            summary["status"] == "FINALISED",
            "waveform_records",
        ].sum()
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Finalised datasets", finalised)
    c2.metric("Finalised impacts", f"{impacts:,}")
    c3.metric("Simulation runs", f"{sims:,}")
    c4.metric("Waveform records", f"{waveforms:,}")

    st.subheader("What do you want to do?")
    a, b, c = st.columns(3)

    with a:
        st.markdown("#### Browse datasets")
        st.write(
            "See coverage, completeness, simulations and spatial-field availability."
        )
        if st.button("Browse datasets", use_container_width=True):
            nav_to("Browse datasets")

    with b:
        st.markdown("#### Find an impact")
        st.write("Search by logical ID, source impact ID or source sample ID.")
        if st.button("Find an impact", use_container_width=True):
            nav_to("Find an impact")

    with c:
        st.markdown("#### Build an export")
        st.write(
            "Create an impact-level inventory for selected datasets and "
            "availability filters."
        )
        if st.button("Build an export", use_container_width=True):
            nav_to("Build an export")

    st.subheader("Dataset status")
    display = summary.rename(
        columns={
            "dataset_id": "Dataset",
            "label": "Name",
            "status": "Status",
            "impacts": "Impacts",
            "raw_records": "Raw",
            "processed_records": "Processed",
            "waveform_records": "Waveforms",
            "simulations": "Simulations",
            "mps_fields": "MPS",
            "mpsr_fields": "MPSR",
        }
    )

    dataframe(
        display[
            [
                "Dataset",
                "Name",
                "Status",
                "Impacts",
                "Raw",
                "Processed",
                "Waveforms",
                "Simulations",
                "MPS",
                "MPSR",
            ]
        ]
    )

    st.info(
        "DS009 is intentionally excluded from the finalised-data counts while "
        "its historical deliveries and FE-input provenance are reconciled."
    )


def render_datasets(summary):
    st.title("Browse datasets")

    ds_options = summary["dataset_id"].tolist()
    chosen = st.selectbox(
        "Dataset",
        ds_options,
        format_func=lambda x: f"{x} — {db.DATASET_LABELS.get(x, '')}",
    )

    row = summary.loc[summary["dataset_id"] == chosen].iloc[0]
    badge(row["status"])

    cols = st.columns(6)
    cols[0].metric("Impacts", f"{int(row.impacts):,}")
    cols[1].metric("Raw records", f"{int(row.raw_records):,}")
    cols[2].metric("Processed", f"{int(row.processed_records):,}")
    cols[3].metric("Simulations", f"{int(row.simulations):,}")
    cols[4].metric("MPS fields", f"{int(row.mps_fields):,}")
    cols[5].metric("MPSR fields", f"{int(row.mpsr_fields):,}")

    st.subheader("Impact inventory")
    inv = db.dataset_impacts(chosen, DB_PATH, limit=10000)

    search = st.text_input(
        "Filter IDs in this dataset",
        placeholder="e.g. Rugby261",
    )
    if search:
        mask = (
            inv["impact_uid"].astype(str).str.contains(
                search, case=False, na=False
            )
            | inv["source_impact_id"].astype(str).str.contains(
                search, case=False, na=False
            )
            | inv["source_sample_id"].astype(str).str.contains(
                search, case=False, na=False
            )
        )
        inv = inv.loc[mask]

    dataframe(inv)

    if not inv.empty:
        st.download_button(
            "Download this inventory as CSV",
            inv.to_csv(index=False).encode(),
            file_name=f"{chosen}_impact_inventory.csv",
            mime="text/csv",
        )


def render_impact(summary):
    st.title("Find an impact")

    ds_filter = st.selectbox(
        "Dataset filter",
        ["All"] + summary["dataset_id"].tolist(),
        format_func=lambda x: (
            x if x == "All"
            else f"{x} — {db.DATASET_LABELS.get(x, '')}"
        ),
    )

    term = st.text_input(
        "Search",
        placeholder="Logical ID, source impact ID, or sample ID",
    )

    if not term.strip():
        st.info("Enter part of an impact ID or sample ID.")
        return

    results = db.find_impacts(
        term,
        None if ds_filter == "All" else ds_filter,
        DB_PATH,
        limit=100,
    )

    if results.empty:
        st.warning("No matching impacts found.")
        return

    dataframe(results)

    impact_uid = st.selectbox(
        "Open impact",
        results["impact_uid"].tolist(),
    )

    impact, kine, sims, artifacts = db.impact_detail(
        impact_uid,
        DB_PATH,
    )

    if impact.empty:
        return

    ds_id = impact.iloc[0]["dataset_id"]
    st.subheader(impact_uid)

    if ds_id in db.PAUSED_DATASETS:
        badge("PAUSED")

    left, right = st.columns([1, 1])

    with left:
        st.markdown("#### Impact metadata")
        md = impact.T.reset_index()
        md.columns = ["Field", "Value"]
        dataframe(md)

    with right:
        st.markdown("#### Availability")
        st.metric("Kinematic records", len(kine))
        st.metric("Simulation runs", len(sims))
        st.metric("Simulation artifacts", len(artifacts))

    st.markdown("#### Kinematic records")
    if kine.empty:
        st.info("No kinematic records.")
    else:
        show_cols = [
            c
            for c in [
                "kinematic_uid",
                "data_stage",
                "sample_interval_ms",
                "n_samples",
                "linear_acc_unit",
                "angular_vel_unit",
                "angular_acc_unit",
                "storage_root",
                "relative_path",
                "waveform_in_duckdb",
            ]
            if c in kine.columns
        ]
        dataframe(kine[show_cols])

        with_waveform = kine.loc[
            kine["waveform_in_duckdb"] == True
        ]

        if not with_waveform.empty:
            st.markdown("#### Waveform viewer")
            kuid = st.selectbox(
                "Kinematic record",
                with_waveform["kinematic_uid"].tolist(),
            )

            _, wf = db.waveform(kuid, DB_PATH)

            if not wf.empty:
                time_col = wf.columns[0]
                groups = {
                    "Linear acceleration": [
                        c for c in wf.columns if c.startswith("LA ")
                    ],
                    "Angular velocity": [
                        c for c in wf.columns if c.startswith("AV ")
                    ],
                    "Angular acceleration": [
                        c for c in wf.columns if c.startswith("AA ")
                    ],
                }

                tabs = st.tabs(list(groups.keys()))

                for tab, (_, plot_cols) in zip(tabs, groups.items()):
                    with tab:
                        if plot_cols:
                            long = wf[[time_col] + plot_cols].melt(
                                id_vars=[time_col],
                                var_name="Channel",
                                value_name="Value",
                            )
                            fig = px.line(
                                long,
                                x=time_col,
                                y="Value",
                                color="Channel",
                            )
                            fig.update_layout(
                                height=420,
                                margin=dict(l=20, r=20, t=25, b=20),
                                legend_title_text="",
                            )
                            st.plotly_chart(
                                fig,
                                use_container_width=True,
                            )
                        else:
                            st.info(
                                "This waveform record has no channels "
                                "in this group."
                            )

    st.markdown("#### Simulation runs")
    if sims.empty:
        st.info("No simulation runs catalogued for this impact.")
    else:
        dataframe(sims)

    st.markdown("#### Simulation artifacts")
    if artifacts.empty:
        st.info("No simulation artifacts catalogued for this impact.")
    else:
        dataframe(artifacts)


def render_export(summary):
    st.title("Build an export")

    finalised = summary.loc[
        summary["status"] == "FINALISED",
        "dataset_id",
    ].tolist()

    selected = st.multiselect(
        "Datasets",
        summary["dataset_id"].tolist(),
        default=finalised,
        format_func=lambda x: (
            f"{x} — {db.DATASET_LABELS.get(x, '')}"
        ),
    )

    if "DS009" in selected:
        st.warning(
            "DS009 is paused. It can be included deliberately, "
            "but it is not considered finalised."
        )

    stages = st.multiselect(
        "Kinematic stages counted in selected_kinematic_records",
        ["raw", "processed"],
        default=["raw", "processed"],
    )

    sim_filter = st.radio(
        "Simulation availability",
        ["Any", "Has simulation", "No simulation"],
        horizontal=True,
    )

    export = db.impact_export(
        selected,
        stages,
        sim_filter,
        DB_PATH,
    )

    st.caption(f"{len(export):,} impacts")
    dataframe(export)

    st.download_button(
        "Download CSV",
        export.to_csv(index=False).encode(),
        file_name="head_impact_export.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.caption(
        "This starter exports an impact-level inventory/availability table. "
        "It does not copy restricted NAS files."
    )


def render_advanced():
    st.title("Advanced")
    st.caption(
        "Schema and database inspection for administrators/developers."
    )

    st.markdown("#### Connection")
    st.code(f"DB: {DB_PATH}\nNAS root: {NAS_ROOT}")
    st.write("DuckDB is opened read-only by this application.")

    tables = db.schema_tables(DB_PATH)
    st.markdown("#### Tables")
    dataframe(tables)

    table = st.selectbox(
        "Inspect table",
        tables["table_name"].tolist(),
    )

    cols = db.table_columns(table, DB_PATH)
    st.write("Columns:", ", ".join(cols))

    preview = db.table_preview(
        table,
        DB_PATH,
        100,
    )
    dataframe(preview)


def main():
    try:
        summary = cached_summary(DB_PATH)
    except Exception as exc:
        st.error("Could not open the head-impact DuckDB.")
        st.code(str(exc))
        st.info(
            "Set HEAD_IMPACT_DB to the QC-passed DuckDB path, "
            "then restart Streamlit."
        )
        st.stop()

    if "page" not in st.session_state:
        st.session_state.page = "Home"

    st.sidebar.title("Head Impact")

    pages = [
        "Home",
        "Browse datasets",
        "Find an impact",
        "Build an export",
        "Advanced",
    ]

    current = st.session_state.page
    idx = pages.index(current) if current in pages else 0

    selected = st.sidebar.radio(
        "Navigation",
        pages,
        index=idx,
    )

    st.session_state.page = selected

    st.sidebar.divider()
    st.sidebar.caption("Read-only interface")
    st.sidebar.caption(Path(DB_PATH).name)
    st.sidebar.caption("DS009: paused")

    if selected == "Home":
        render_home(summary)
    elif selected == "Browse datasets":
        render_datasets(summary)
    elif selected == "Find an impact":
        render_impact(summary)
    elif selected == "Build an export":
        render_export(summary)
    else:
        render_advanced()


if __name__ == "__main__":
    main()
