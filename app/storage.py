from __future__ import annotations

from pathlib import Path, PurePosixPath

from config import STORAGE_ROOTS


class StorageResolutionError(RuntimeError):
    pass


def resolve_storage_path(storage_root: str, relative_path: str) -> Path:
    """
    Convert a logical database path into a machine-specific filesystem path.

    Example DB record:
        storage_root = "NAS_HEAD_IMPACT"
        relative_path = "DS003_RFU546/simulation/..."

    The database never needs to know whether the application runs on a Mac,
    Linux server, container, or HPC node.
    """
    if storage_root not in STORAGE_ROOTS:
        raise StorageResolutionError(f"Unknown storage_root: {storage_root}")

    if not relative_path:
        raise StorageResolutionError("Missing relative_path")

    rel = PurePosixPath(str(relative_path))

    if rel.is_absolute() or ".." in rel.parts:
        raise StorageResolutionError(f"Unsafe relative_path: {relative_path}")

    base = Path(STORAGE_ROOTS[storage_root]).resolve()
    resolved = (base / Path(*rel.parts)).resolve()

    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise StorageResolutionError(
            f"Resolved path escapes storage root: {relative_path}"
        ) from exc

    return resolved
