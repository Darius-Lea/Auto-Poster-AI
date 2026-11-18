"""Classify storage failures for safe UI messages and operator diagnostics.

Never return exception text: it may contain a database path, SQL or account data.
Classification is diagnostic only and must not retry writes or modify storage.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class StorageFailure:
    code: str
    retryable: bool
    message: str
    status: int = 503


_BUSY = StorageFailure(
    'storage_busy', True,
    'The service is busy right now. Please wait a moment and try again. '
    'If you already created an account, sign in with the same email.')
_UNAVAILABLE = 'We couldn’t access your account right now. The service needs attention. Please try again later.'


def classify_storage_error(exc: BaseException, *, default_retryable=False) -> StorageFailure:
    """Inspect a wrapped cause without exposing its private details.

    ``default_retryable`` is appropriate only for an explicitly timed-out
    application lock with no underlying storage error.
    """
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.Error):
            code = getattr(current, 'sqlite_errorcode', None)
            base = code & 255 if isinstance(code, int) else None
            if base in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return _BUSY
            if base in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_PERM):
                return StorageFailure('storage_configuration', False, _UNAVAILABLE)
            if base == sqlite3.SQLITE_FULL:
                return StorageFailure('storage_capacity', False, _UNAVAILABLE)
            if base == sqlite3.SQLITE_IOERR:
                return StorageFailure('storage_io', False, _UNAVAILABLE)
            if base in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                return StorageFailure('storage_corrupt', False, _UNAVAILABLE)
            # Synthetic errors (including older runtimes) may not carry codes.
            # Match only known SQLite diagnostics, never display their text.
            detail = str(current).lower()
            if 'database is locked' in detail or 'database table is locked' in detail or 'database is busy' in detail:
                return _BUSY
            if any(part in detail for part in ('no such table:', 'no such column:', 'has no column named')):
                return StorageFailure('storage_schema', False, _UNAVAILABLE)
            if 'unable to open database file' in detail or 'readonly database' in detail:
                return StorageFailure('storage_configuration', False, _UNAVAILABLE)
            if 'database or disk is full' in detail:
                return StorageFailure('storage_capacity', False, _UNAVAILABLE)
            return StorageFailure('storage_unavailable', False, _UNAVAILABLE)
        if isinstance(current, (MemoryError, ValueError)):
            # Auth wrappers preserve crypto resource/invalid-hash exceptions.
            return StorageFailure('account_resources', False, _UNAVAILABLE)
        current = current.__cause__ or current.__context__
    return _BUSY if default_retryable else StorageFailure('storage_unavailable', False, _UNAVAILABLE)
