# Issue #4 — Import source-reported strain metrics (WB, BS, CCG, CCB, CCS)

This offline importer loads precomputed numerical strain summaries from the supplied
CSV/XLSX files, **not** NIfTI data. No schema migration is required.

## Scope and units (confirmed by data owner)

- `WB_MPS90`, `WB_MPS95` — source `90PercStrain`, unit `strain` (dimensionless).
- `WB_MPSR90`, `WB_MPSR95` — source `90PercStrainRate`, unit **`1/ms`**, stored unchanged.
- `BS_MPS90/95`, `CCG_MPS90/95`, `CCB_MPS90/95`, `CCS_MPS90/95` — source ROI values, unit `strain`.
- All data are from the IC FE model. No unverified FE model version is assigned.
- Other ROIs, `CC`, `CC_max`, and `noLR` are deliberately excluded.
- The 95th-percentile source files still use `90PercStrain` and
  `90PercStrainRate` headers: **filename** determines whether the value is MPS90/95
  or MPSR90/95. No recalculation of percentiles, spatial ROIs or time histories.

Database fields remain long-format:
`brain_metric_results(simulation_uid,roi_name,metric_name,summary_method,value,unit,...)`.
Actual names in `metric_name` are `MPS90`, `MPS95`, `MPSR90`, `MPSR95`;
`roi_name` determines WB/BS/CCG/CCB/CCS. The optional export patch in this bundle
makes downloaded columns uppercase e.g. `WB_MPS95` and `WB_MPSR90`.

## Verified source-only counts

The original 14 uploaded files included one `noLR` file. The importer reads
13 actual source files, yielding:

- **30,605 MPS** values across WB/BS/CCG/CCB/CCS
- **6,118 WB MPSR** values at `1/ms`
- **36,723** total numeric observations, no parser errors
- **7 percentile inversions** (5 MPS + 2 MPSR), all in DS009

The abnormal DS009 pairs require source review. `--exclude-qc-failures`
excludes **both** p90 and p95 numbers for each failing (impact, ROI, metric kind)
pair. This skips 14 individual numeric records across two source impacts.
It does not guess or alter their values. The other DS009 observations can be
loaded if their impact and simulation IDs match.

## Private input and repository boundaries

Never commit source data, the staging CSV, participant-level results or DuckDB.
The source-only staging CSV should be outside the repo at:

`~/headimpact-db-work/strain-summaries/headimpact_strain_metrics_with_MPSR_PRIVATE.csv`

Copy the bundle's `scripts/`, `tests/`, `docs/`, and optionally `patches/`
into `head-impact-data-platform` on a separate feature branch.

Source-to-dataset mapping: `2_*`→DS002, `3_*`→DS003,
`4_*` + `5_*`→DS004, `9_*`→DS009, `10_*`→DS010,
`12_*`→DS012, `13_*`→DS013. When DB uses merged `DS003_RFU1701`,
legacy DS004 Rugby IDs are offset by 546; otherwise legacy DS003/DS004
remain separate. No mappings are guessed from row order.

## 1. Verify the local database and Python environment

```
DB="$HOME/headimpact-db-work/head_impacts.duckdb"
ls -lh "$DB"
python3 -c 'import duckdb; print("DuckDB", duckdb.__version__)'
```

If `duckdb` is missing, create an isolated Python virtual environment:

```
python3 -m venv "$HOME/headimpact-issue4-venv"
source "$HOME/headimpact-issue4-venv/bin/activate"
python -m pip install duckdb
```

## 2. Test, then dry-run (READ ONLY)

From the repository root:

```
python -m unittest discover -s tests -p 'test_ingest_strain_summaries.py' -v

python scripts/ingest_strain_summaries.py \
  --staging-csv "$HOME/headimpact-db-work/strain-summaries/headimpact_strain_metrics_with_MPSR_PRIVATE.csv" \
  --db "$HOME/headimpact-db-work/head_impacts.duckdb" \
  --model-name 'IC FE model' \
  --exclude-qc-failures \
  --qc-dir "$HOME/headimpact-db-work/strain-qc"
```

This **does not change** DuckDB. Examine `DB matching: { ... }` and the QC reports.
Important statuses include `ready_to_insert`, `already_present`,
`no_impact_match`, `no_simulation_match`, `ambiguous_simulation`,
`other_simulation_present`, `conflict_with_database`, `simulations_to_register`,
and `excluded_percentile_qc`.

Inspect the FE model labels already in the database:

```
python - <<'PY'
import os, duckdb
p=os.path.expanduser('~/headimpact-db-work/head_impacts.duckdb')
with duckdb.connect(p,read_only=True) as c:
    print('Simulation runs:',c.sql('SELECT COUNT(*) FROM simulation_runs').fetchone()[0])
    print('Existing models:',c.sql('SELECT model_name, model_version, COUNT(*) FROM simulation_runs GROUP BY 1,2 ORDER BY 1,2').fetchall())
    print('Existing brain metrics:',c.sql('SELECT COUNT(*) FROM brain_metric_results').fetchone()[0])
PY
```

If `simulation_runs` is empty for matched impacts, you may repeat the **dry run**
with `--register-missing-simulations` **after checking the IDs/model**.
This adds deterministic *planned* `summary_only` simulation registrations;
only an explicit `--apply` commits them. If another FE model/version is already
registered for an impact, registration is blocked rather than assuming which
simulation produced these summaries. `model_version` remains NULL unless verified.

## 3. Apply ONLY after review and backup

Close DBeaver/Streamlit or other DB writers. Copy the development DB from a
consistent stopped state (ensure no uncheckpointed `.wal` remains):

```
DB="$HOME/headimpact-db-work/head_impacts.duckdb"
cp -p "$DB" "$HOME/headimpact-db-work/head_impacts.before-issue4.duckdb"
```

Then repeat the successful dry-run command with `--apply`.
If the reviewed QC includes unmatched source impacts, add `--allow-partial` to
skip *only* those unmatched source records. If registered simulation records are
missing and you explicitly approved summary-only registration, also add
`--register-missing-simulations`.

Example (not universal: review your own dry-run first):

```
python scripts/ingest_strain_summaries.py \
  --staging-csv "$HOME/headimpact-db-work/strain-summaries/headimpact_strain_metrics_with_MPSR_PRIVATE.csv" \
  --db "$DB" \
  --model-name 'IC FE model' \
  --exclude-qc-failures --allow-partial \
  --register-missing-simulations \
  --qc-dir "$HOME/headimpact-db-work/strain-qc" \
  --apply
```

The importer is transactional, has deterministic IDs and is idempotent.
It blocks ambiguous matches, conflicting existing values and unsafe
simulation registrations. Inspect counts before using any optional override.

## 4. Production is separate

The iMac Streamlit reads
`/Users/headimpact-db/deployment/head_impacts.duckdb`.
Git merge/deploying code will **not** copy the new numeric values into the iMac DB.
After successful development QC, back up and update production DB in an
explicit maintenance window. Do not overwrite the iMac DB with an unrelated
copy, or add private summaries to GitHub.
