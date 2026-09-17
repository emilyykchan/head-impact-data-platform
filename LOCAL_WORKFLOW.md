# HEAD Impact Data Platform — Local Workflow

[internal] Steps for starting and managing the project locally.

## Project location

```bash
~/Projects/head-impact-data-platform
```

## Start a work session

Open Terminal and move into the repository:

```bash
cd ~/Projects/head-impact-data-platform
```

Activate the Python virtual environment:

```bash
source .venv/bin/activate
```

The Terminal prompt should now begin with:

```text
(.venv)
```

Open the project in VS Code:

```bash
code .
```

Before making changes, update the local copy from GitHub:

```bash
git pull
```

---

## Check the Python environment

Check Python:

```bash
python --version
```

Check that the main packages are available:

```bash
python -c "import duckdb, pandas, pyarrow, streamlit; print('Environment OK')"
```

---

## Run the Streamlit app

When the dashboard has been created:

```bash
streamlit run app/streamlit_app.py
```

It should open locally in a browser, normally at:

```text
http://localhost:8501
```

Stop Streamlit with:

```text
Ctrl + C
```

---

## Open the database

The production research database should remain outside GitHub or inside a git-ignored local directory.

Example local path:

```text
local/head_impacts.duckdb
```

The database can be inspected using DBeaver.

Never commit research data, real databases, restricted metadata, Box file paths, NIfTI files, kinematic traces, or other project data to the public repository.



## Project structure

Planned structure:

```text
head-impact-data-platform/
│
├── README.md
├── LOCAL_WORKFLOW.md
├── requirements.txt
├── .gitignore
│
├── schema/
│   ├── schema.sql
│   └── data_dictionary.md
│
├── src/
│   ├── create_database.py
│   ├── import_master.py
│   ├── validate.py
│   └── queries.py
│
├── app/
│   └── streamlit_app.py
│
├── tests/
│
├── docs/
│
└── local/
    └── head_impacts.duckdb
```

The `local/` directory and all research data must remain excluded from Git.

---

## Core architecture reminder

The project separates software from restricted research data:

```text
GitHub
    schema
    Python code
    validation
    Streamlit app
    documentation
        │
        ▼
DuckDB
    structured metadata
    relationships
    metrics
    provenance
        │
        ├── Parquet / kinematic traces
        │
        └── Box / large research files and NIfTI
```

The GitHub repository contains the software and data model, not the confidential research dataset.

