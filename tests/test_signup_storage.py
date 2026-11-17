"""Real SQLite failures must not half-create accounts or hang account forms."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app_control
import customer_accounts as accounts


class SignupStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment=patch.dict(os.environ,{
            'AUTOPOST_WEB_DB':self.tmp.name+'/accounts.db',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED':'true',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        accounts.init()
        self.password='synthetic-customer-password123'

    def register(self,email='fixture@example.test'):
        return accounts.register(email,self.password,'A Business Owner')

    def test_standalone_signup_initializes_controls_and_returns_safe_identity(self):
        user=self.register()
        self.assertNotIn('password_hash',user)
        with accounts.connect() as c:
            account=c.execute('SELECT * FROM customer_accounts WHERE id=?',(user['id'],)).fetchone()
            settings=c.execute('SELECT * FROM publishing_controls WHERE workspace_id=?',(user['workspace_id'],)).fetchone()
        self.assertTrue(account['password_hash'].startswith('scrypt:'))
        self.assertNotIn(self.password,account['password_hash'])
        self.assertEqual(settings['enabled'],0)
        self.assertEqual(accounts.login(user['email'],self.password),user)

    def test_control_insert_failure_rolls_back_customer_insert(self):
        app_control.init()
        with accounts.connect() as c:
            c.executescript('''CREATE TRIGGER fixture_control_failure
                BEFORE INSERT ON publishing_controls WHEN NEW.workspace_id LIKE 'ws_%'
                BEGIN SELECT * FROM synthetic_missing_control_table; END;''')
        with self.assertRaises(accounts.AuthStorageUnavailable) as failure:
            self.register()
        self.assertIsInstance(failure.exception.__cause__,sqlite3.OperationalError)
        self.assertNotIn('synthetic_missing_control_table',str(failure.exception))
        with accounts.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0],0)

    def test_schema_setup_failure_occurs_before_any_customer_is_created(self):
        with patch.object(app_control,'init',side_effect=sqlite3.OperationalError('synthetic migration unavailable')):
            with self.assertRaises(accounts.AuthStorageUnavailable):
                self.register()
        with accounts.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0],0)

    def test_signup_and_login_do_not_need_a_fallible_followup_identity_read(self):
        app_control.init()
        with patch.object(accounts,'get',side_effect=sqlite3.OperationalError('synthetic second read blocked')):
            user=self.register()
            self.assertEqual(accounts.login(user['email'],self.password),user)
        self.assertEqual(accounts.get(user['id']),user)

    def test_lock_wait_is_bounded_and_busy_errors_are_safe(self):
        app_control.init()
        original_connect=accounts.connect
        with original_connect() as c:
            self.assertEqual(c.execute('PRAGMA busy_timeout').fetchone()[0],2000)
        held=sqlite3.connect(os.environ['AUTOPOST_WEB_DB'])
        self.addCleanup(held.close)
        held.execute('BEGIN IMMEDIATE')
        def fast_busy_connection():
            c=original_connect()
            # A real locked SQLite operation, with no wall-clock assertion.
            c.execute('PRAGMA busy_timeout=1')
            return c
        with patch.object(accounts,'connect',side_effect=fast_busy_connection):
            for operation in (lambda:accounts.attempt('fixture','address'),self.register):
                with self.assertRaises(accounts.AuthStorageUnavailable) as failure:
                    operation()
                self.assertIsInstance(failure.exception.__cause__,sqlite3.OperationalError)
                self.assertNotIn(self.tmp.name,str(failure.exception))
        held.rollback()
        with original_connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0],0)

    def test_password_resource_failure_does_not_downgrade_hash_or_create_account(self):
        for failure in (MemoryError('synthetic private details'),ValueError('synthetic crypto details')):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(accounts,'generate_password_hash',side_effect=failure) as hashing:
                    with self.assertRaises(accounts.AuthStorageUnavailable) as unavailable:
                        self.register()
                    self.assertEqual(hashing.call_args.kwargs['method'],'scrypt')
                    self.assertNotIn('synthetic',str(unavailable.exception))
        with accounts.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
