"""Parse and resolve bulk impact identifiers against the currently filtered rows."""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd


def parse_impact_ids(text: str) -> list[str]:
    """Ordered unique tokens from pasted IDs; separators include Excel rows."""
    return list(dict.fromkeys(
        token for token in re.split(r"[,;\s]+", text.strip()) if token
    ))


def resolve_impact_ids(text: str, impacts: pd.DataFrame) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Return (matched UIDs, missing tokens, ambiguous tokens -> candidate UIDs).

    Match case-insensitive complete canonical UID, source ID, or canonical UID
    suffix; never substring-match. Resolution is limited to filtered impacts.
    """
    lookup: dict[str, set[str]] = defaultdict(set)
    for row in impacts.itertuples(index=False):
        uid = str(row.impact_uid)
        dataset = str(row.dataset_id)
        candidates = [uid, row.source_impact_id]
        prefix = f"IMP_{dataset}_"
        if uid.upper().startswith(prefix.upper()):
            candidates.append(uid[len(prefix):])
        # Also accept the last component for composite canonical IDs.
        candidates.append(uid.rsplit("_", 1)[-1])
        for candidate in candidates:
            if candidate is not None and not pd.isna(candidate) and str(candidate).strip():
                lookup[str(candidate).strip().casefold()].add(uid)
    matched, missing, ambiguous = [], [], {}
    for token in parse_impact_ids(text):
        choices = lookup.get(token.casefold(), set())
        if not choices:
            missing.append(token)
        elif len(choices) > 1:
            ambiguous[token] = sorted(choices)
        else:
            matched.append(next(iter(choices)))
    return list(dict.fromkeys(matched)), missing, ambiguous
