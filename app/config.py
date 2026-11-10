from __future__ import annotations

import os
from pathlib import Path


def expand_path(value: str) -> str:
    return str(Path(os.path.expanduser(value)).resolve())


DB_PATH = expand_path(
    os.environ.get(
        "HEAD_IMPACT_DB",
        "~/headimpact-db-work/head_impacts.duckdb",
    )
)

STORAGE_ROOTS = {
    "NAS_HEAD_IMPACT": expand_path(
        os.environ.get(
            "HEAD_IMPACT_NAS_ROOT",
            "/Volumes/Public/HeadImpactDatabase",
        )
    ),
}

EXPORT_DIR = expand_path(
    os.environ.get(
        "HEAD_IMPACT_EXPORT_DIR",
        "./local/exports",
    )
)
