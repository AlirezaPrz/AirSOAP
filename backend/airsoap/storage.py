"""The server-controlled directory that holds temporary audio.

Only files this module names (``rec-<32 hex>.wav``) are ever created, read or
deleted, so a misconfigured directory cannot cause unrelated files to be removed.
"""

import os
import re
import stat
import uuid
from pathlib import Path

AUDIO_FILE_PATTERN = re.compile(r"^rec-[0-9a-f]{32}\.wav$")


class UnsafePathError(ValueError):
    """Raised for any path that is not a file this module created in the work dir."""


class WorkDir:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def prepare(self) -> None:
        """Create the directory with owner-only permissions and refuse anything suspicious."""
        if self._root.is_symlink():
            raise UnsafePathError("work directory must not be a symlink")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self._root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafePathError("work directory is not a directory")
        if info.st_uid != os.getuid():
            raise UnsafePathError("work directory is owned by another user")
        os.chmod(self._root, 0o700)
        self._root = self._root.resolve()

    def new_audio_path(self) -> Path:
        return self._root / f"rec-{uuid.uuid4().hex}.wav"

    def resolve(self, path: Path) -> Path:
        """Return the path if it names one of our audio files directly inside the work dir."""
        if not AUDIO_FILE_PATTERN.fullmatch(path.name):
            raise UnsafePathError("not an AirSOAP audio file")
        if path.is_absolute():
            candidate = path
        elif path == Path(path.name):
            candidate = self._root / path.name
        else:
            raise UnsafePathError("relative paths with directories are not accepted")
        if candidate.parent.resolve() != self._root.resolve() or candidate.is_symlink():
            raise UnsafePathError("path is outside the work directory")
        return candidate

    def delete(self, path: Path) -> bool:
        target = self.resolve(path)
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True

    def sweep(self) -> int:
        """Delete every leftover audio file, e.g. after a crash. Returns the count removed."""
        if not self._root.is_dir():
            return 0
        removed = 0
        for entry in self._root.iterdir():
            if AUDIO_FILE_PATTERN.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink():
                entry.unlink(missing_ok=True)
                removed += 1
        return removed
