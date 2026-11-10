from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from storage import resolve_storage_path


@dataclass(frozen=True)
class ArtifactRef:
    storage_root: str
    relative_path: str
    file_format: str | None = None
    size_bytes: int | None = None

    @property
    def path(self) -> Path:
        return resolve_storage_path(self.storage_root, self.relative_path)

    def exists(self) -> bool:
        return self.path.exists()

    def is_file(self) -> bool:
        return self.path.is_file()

    def actual_size_bytes(self) -> int | None:
        if not self.is_file():
            return None
        return self.path.stat().st_size

    def open_binary(self):
        if not self.is_file():
            raise FileNotFoundError(self.path)
        return self.path.open("rb")
