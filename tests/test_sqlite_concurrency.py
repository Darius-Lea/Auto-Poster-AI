"""Real SQLite readers must not prevent new accounts after WAL preparation."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app_control
import customer_accounts as accounts
from sqlite_concurrency import ClosingConnection, prepare_wal


class AccountConcurrencyTests(unittest.TestCase):
    password = 'synthetic-concurrent-account-password123'

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / 'accounts.db'
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': str(self.database),
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
        })
        environment.start()
        self.addCleanup(environment.stop)
        accounts.init()
        app_control.init()

    def reader(self):
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        connection.execute('BEGIN')
        connection.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()
        return connection

    def short_busy(self):
        connection = self.original_connect()
        connection.execute('PRAGMA busy_timeout=1')
        return connection

    def test_real_reader_blocks_signup_in_delete_mode_but_not_in_wal_mode(self):
        self.original_connect = accounts.connect
        reader = self.reader()
        self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        with patch.object(accounts, 'connect', side_effect=self.short_busy):
            with self.assertRaises(accounts.AuthStorageUnavailable) as failure:
                accounts.attempt('register', 'synthetic-address')
            self.assertIsInstance(failure.exception.__cause__, sqlite3.OperationalError)
        reader.rollback()
        self.assertEqual(prepare_wal(self.database), 'wal')
        reader.execute('BEGIN')
        self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 0)
        with patch.object(accounts, 'connect', side_effect=self.short_busy):
            accounts.attempt('register', 'synthetic-address')
            user = accounts.register('new@example.test', self.password, 'Business Owner')
            self.assertEqual(accounts.login(user['email'], self.password)['id'], user['id'])
        # The same held reader keeps its snapshot while the new account commits.
        self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 0)
        reader.rollback()
        self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 1)

    def test_an_active_writer_still_blocks_auth_in_wal_mode(self):
        prepare_wal(self.database)
        writer = sqlite3.connect(self.database)
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        self.original_connect = accounts.connect
        with patch.object(accounts, 'connect', side_effect=self.short_busy):
            with self.assertRaises(accounts.AuthStorageUnavailable):
                accounts.attempt('login', 'synthetic-address')
        writer.rollback()
        accounts.attempt('login', 'synthetic-address')

    def test_existing_identity_and_durable_settings_are_preserved(self):
        original = accounts.register('existing@example.test', self.password, 'Business Owner')
        with sqlite3.connect(self.database) as connection:
            before = connection.execute('SELECT password_hash FROM customer_accounts').fetchone()[0]
        self.assertEqual(prepare_wal(self.database), 'wal')
        self.assertEqual(prepare_wal(self.database), 'wal')
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('PRAGMA synchronous').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT password_hash FROM customer_accounts').fetchone()[0], before)
        self.assertEqual(accounts.login(original['email'], self.password), original)

    def test_missing_parent_is_not_recreated_or_replaced(self):
        missing = self.database.parent / 'missing-volume' / 'accounts.db'
        with self.assertRaises(sqlite3.OperationalError):
            prepare_wal(missing, timeout=0)
        self.assertFalse(missing.parent.exists())

    def test_context_closes_after_commit_and_rollback(self):
        connection = sqlite3.connect(self.database, factory=ClosingConnection)
        with connection:
            connection.execute('CREATE TABLE closing_fixture(value)')
            connection.execute('INSERT INTO closing_fixture VALUES(1)')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        failed = sqlite3.connect(self.database, factory=ClosingConnection)
        with self.assertRaisesRegex(ValueError, 'synthetic failure'):
            with failed:
                failed.execute('INSERT INTO closing_fixture VALUES(2)')
                raise ValueError('synthetic failure')
        with self.assertRaises(sqlite3.ProgrammingError):
            failed.execute('SELECT 1')
        with sqlite3.connect(self.database) as check:
            self.assertEqual(check.execute('SELECT value FROM closing_fixture').fetchall(), [(1,)])
