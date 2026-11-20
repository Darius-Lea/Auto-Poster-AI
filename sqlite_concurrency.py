"""Preserve the app database while allowing readers and writers to coexist.

WAL is configured once during startup, before background work starts. It does
not permit concurrent writers or bypass SQLite's locks. Database files and WAL
sidecars must live together on the same persistent local filesystem.
"""
from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback normally, then promptly release the connection."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def prepare_wal(database, *, timeout=2):
    """Enable WAL without replacing storage or weakening durability.

    Existing contents remain in the configured database. Missing directories,
    read-only/full storage, unsupported journal modes and active writer locks
    remain errors for the operator; no temporary database is substituted.
    """
    with sqlite3.connect(database, timeout=timeout, factory=ClosingConnection) as connection:
        if str(database) == ':memory:':
            return 'memory'
        mode = connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode.lower() != 'wal':
            raise sqlite3.OperationalError('Concurrent account storage is unavailable')
        connection.execute('PRAGMA synchronous=FULL')
        return mode.lower()
