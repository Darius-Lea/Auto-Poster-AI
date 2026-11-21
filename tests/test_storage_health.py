"""Real storage errors are distinguished without leaking private details."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage_health import classify_storage_error


class StorageFailureTests(unittest.TestCase):
    def test_real_missing_table_is_not_reported_as_temporary_contention(self):
        with sqlite3.connect(':memory:') as connection:
            try:
                connection.execute('SELECT * FROM private_account_table')
            except sqlite3.OperationalError as cause:
                wrapped = RuntimeError('safe wrapper')
                wrapped.__cause__ = cause
                result = classify_storage_error(wrapped)
        self.assertEqual(result.code, 'storage_schema')
        self.assertFalse(result.retryable)
        self.assertNotIn('private_account_table', result.message)

    def test_real_write_lock_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'private.db')
            first = sqlite3.connect(path)
            second = sqlite3.connect(path, timeout=0)
            try:
                first.execute('CREATE TABLE sample(value)')
                first.execute('BEGIN IMMEDIATE')
                try:
                    second.execute('INSERT INTO sample VALUES(1)')
                except sqlite3.OperationalError as cause:
                    result = classify_storage_error(cause)
                self.assertEqual(result.code, 'storage_busy')
                self.assertTrue(result.retryable)
                self.assertNotIn(path, result.message)
            finally:
                first.close()
                second.close()

    def test_unopenable_path_is_configuration_not_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'missing' / 'private.db')
            try:
                sqlite3.connect(path)
            except sqlite3.OperationalError as cause:
                result = classify_storage_error(cause)
        self.assertEqual(result.code, 'storage_configuration')
        self.assertFalse(result.retryable)
        self.assertNotIn(path, result.message)

    def test_crypto_failure_is_not_labeled_database_contention(self):
        for cause in (MemoryError('private details'), ValueError('private hash')):
            wrapped = RuntimeError('safe wrapper')
            wrapped.__cause__ = cause
            result = classify_storage_error(wrapped)
            self.assertEqual(result.code, 'account_resources')
            self.assertFalse(result.retryable)
            self.assertNotIn('private', result.message)

    def test_real_readonly_storage_is_configuration_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.db'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE sample(value)')
            connection.close()
            readonly = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                try:
                    readonly.execute('INSERT INTO sample VALUES(1)')
                except sqlite3.OperationalError as cause:
                    result = classify_storage_error(cause)
                self.assertEqual(result.code, 'storage_configuration')
                self.assertFalse(result.retryable)
                self.assertEqual(result.status, 503)
            finally:
                readonly.close()

    def test_corrupt_database_error_has_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.db'
            path.write_bytes(b'private content that is not a SQLite database' * 20)
            connection = sqlite3.connect(path)
            try:
                try:
                    connection.execute('SELECT * FROM sqlite_master').fetchall()
                except sqlite3.DatabaseError as cause:
                    result = classify_storage_error(cause)
                self.assertEqual(result.code, 'storage_corrupt')
                self.assertFalse(result.retryable)
                self.assertNotIn('private', result.message)
            finally:
                connection.close()

    def test_only_explicit_application_lock_timeout_defaults_to_retryable(self):
        wrapped = RuntimeError('safe wrapper')
        self.assertFalse(classify_storage_error(wrapped).retryable)
        self.assertTrue(classify_storage_error(wrapped, default_retryable=True).retryable)

    def test_wrapped_unknown_sqlite_failure_never_inherits_busy_default(self):
        wrapped = RuntimeError('safe wrapper')
        wrapped.__cause__ = sqlite3.OperationalError('private unknown failure')
        result = classify_storage_error(wrapped, default_retryable=True)
        self.assertEqual(result.code, 'storage_unavailable')
        self.assertFalse(result.retryable)
        self.assertNotIn('private', result.message)
