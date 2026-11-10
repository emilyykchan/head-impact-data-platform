# Contributing and Local Development

This document describes the current local development workflow for the Head Impact Data Platform.

The GitHub repository contains the software, schema and documentation only. Research data, DuckDB databases, NAS files and credentials must remain outside Git.

## Repository location

On the current development Mac:

```bash
cd /Users/yc4421/Library/CloudStorage/Box-Box/Github/head-impact-data-platform
```

Before starting work, check the current branch and repository status:

```bash
git status
```

## Python environment

Use the local virtual environment stored outside Box:

```bash
source ~/headimpact-streamlit-venv/bin/activate
```

Confirm that the environment is active:

```bash
which python
python --version
```

The Python path should point to:

```text
~/headimpact-streamlit-venv/
```

Check the main dependencies:

```bash
python -c "import streamlit, duckdb, pandas, plotly; print('Environment OK')"
```

If packages are missing or the environment needs updating:

```bash
python -m pip install -r requirements.txt
```

Do not create the active virtual environment inside the Box-synchronised repository.

## Local DuckDB database

The active DuckDB database is stored outside the Box-synchronised Git repository:

```text
~/headimpact-db-work/head_impacts.duckdb
```

This is intentional. DuckDB performs direct file access and memory mapping, which can behave poorly inside cloud-synchronised folders such as Box.

For convenience, the repository contains a local symbolic link:

```text
local/head_impacts.duckdb
```

which points to:

```text
~/headimpact-db-work/head_impacts.duckdb
```

The structure is therefore:

```text
head-impact-data-platform/
└── local/
    └── head_impacts.duckdb
             │
             └────────► ~/headimpact-db-work/head_impacts.duckdb
```

There is only **one real DuckDB database file**. The file under `local/` is a symbolic link and does not create another copy.

The entire `local/` directory is excluded from Git.

### Create or recreate the symbolic link

From the repository root:

```bash
mkdir -p local

ln -s "$HOME/headimpact-db-work/head_impacts.duckdb" \
  local/head_impacts.duckdb
```

Check the link:

```bash
ls -l local/head_impacts.duckdb
```

The output should look similar to:

```text
local/head_impacts.duckdb -> /Users/yc4421/headimpact-db-work/head_impacts.duckdb
```

Do not replace this symbolic link with a copied DuckDB file inside the Box repository.

## NAS storage

The canonical research data and large artefacts are stored on the NAS:

```text
/Volumes/Public/HeadImpactDatabase
```

The database does not store this machine-specific absolute path.

Instead, NAS-backed records use:

```text
storage_root = NAS_HEAD_IMPACT
relative_path = DSxxx_.../...
```

The application resolves `NAS_HEAD_IMPACT` to the correct filesystem location through environment configuration.

This allows the same database to work later on another machine or server, for example:

```text
Mac development:
NAS_HEAD_IMPACT -> /Volumes/Public/HeadImpactDatabase

Future Linux deployment:
NAS_HEAD_IMPACT -> /mnt/headimpact
```

## Run the Streamlit application locally

From the repository root, activate the environment:

```bash
source ~/headimpact-streamlit-venv/bin/activate
```

Configure the local database and NAS:

```bash
export HEAD_IMPACT_DB="$PWD/local/head_impacts.duckdb"
export HEAD_IMPACT_NAS_ROOT="/Volumes/Public/HeadImpactDatabase"
```

Run Streamlit:

```bash
python -m streamlit run app/streamlit_app.py
```

Open the application in a browser:

```text
http://localhost:8501
```

Stop the application with:

```text
Ctrl+C
```

## Quick start

For normal local development, the full workflow is:

```bash
cd /Users/yc4421/Library/CloudStorage/Box-Box/Github/head-impact-data-platform

source ~/headimpact-streamlit-venv/bin/activate

export HEAD_IMPACT_DB="$PWD/local/head_impacts.duckdb"
export HEAD_IMPACT_NAS_ROOT="/Volumes/Public/HeadImpactDatabase"

python -m streamlit run app/streamlit_app.py
```

## Check the Python source

Before committing application changes:

```bash
python -m py_compile app/*.py
```

Then check:

```bash
git status
```

## Git workflow

Development should normally be performed on a feature branch rather than directly on `main`.

Create a branch:

```bash
git checkout -b my-feature-name
```

Review changes:

```bash
git status
git diff
```

Stage the intended files:

```bash
git add <files>
```

Commit:

```bash
git commit -m "Describe the change"
```

Push the branch:

```bash
git push -u origin my-feature-name
```

## Update the README schema diagram

The database schema diagram in `README.md` is generated automatically from:

```text
schema/schema.sql
```

After changing tables or foreign-key relationships in `schema.sql`, regenerate the Mermaid diagram with:

```bash
python scripts/generate_schema_mermaid.py
```

The script reads the tables and foreign-key relationships from `schema.sql` and replaces the Mermaid diagram inside the reserved section of `README.md`.

After running it, review the changes:

```bash
git diff README.md
```

The generated Mermaid block should not normally be edited manually.


## Repository boundaries

The repository may contain:

- database schema;
- SQL ingestion and QC scripts;
- Streamlit application code;
- storage and artefact resolver code;
- export logic;
- Docker and deployment configuration templates;
- tests;
- documentation.

The repository must not contain:

- DuckDB database files;
- participant-level research data;
- raw or processed kinematic datasets;
- NIfTI or simulation output files;
- NAS data;
- passwords;
- authentication tokens;
- API secrets;
- `.env` files containing real configuration;
- Streamlit secrets.

The database and NAS are external data resources used by the software; they are not part of the Git repository.