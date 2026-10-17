"""The SQLite connection: owner-only files, WAL mode, foreign keys, migrations at open.

One connection is shared and guarded by a lock. Every call is short, and callers
run in FastAPI's thread pool (sync dependencies and routes), so the event loop
never blocks on the database.
"""

import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .migrations import migrate


class DatabaseError(RuntimeError):
    """The database file or its directory is unsafe or unusable."""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        """Create the directory (0700) and file (0600) if needed, then apply migrations."""
        directory = self._path.parent
        if directory.is_symlink() or self._path.is_symlink():
            raise DatabaseError("the database path must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.stat().st_uid != os.getuid():
            raise DatabaseError("the database directory is owned by another user")
        os.chmod(directory, 0o700)
        if self._path.exists() and self._path.stat().st_uid != os.getuid():
            raise DatabaseError("the database file is owned by another user")
        # Create the file ourselves so it is owner-only from the first byte.
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self._path, 0o600)

        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            migrate(conn)
        except BaseException:
            conn.close()
            raise
        self._conn = conn
        self._secure_sidecars()

    def _secure_sidecars(self) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self._path}{suffix}")
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements atomically. Rolls back on any error."""
        with self._lock:
            if self._conn is None:
                raise DatabaseError("the database is not open")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def backup(self, destination: Path) -> None:
        """A consistent copy while the service runs (SQLite's online backup), owner-only."""
        if destination.exists():
            raise DatabaseError("the backup destination already exists")
        fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        target = sqlite3.connect(destination)
        try:
            with self._lock:
                if self._conn is None:
                    raise DatabaseError("the database is not open")
                self._conn.backup(target)
        finally:
            target.close()

    def file_mode(self) -> int:
        return stat.S_IMODE(self._path.stat().st_mode)
