"""Customer identity and authentication; credentials never grant owner access."""
from __future__ import annotations
import hashlib
import os
import re
import secrets
import sqlite3
import time
from werkzeug.security import generate_password_hash, check_password_hash


def connect():
    c = sqlite3.connect(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db'), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS customer_accounts(
          id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL UNIQUE,email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,password_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS customer_auth_limits(
          key TEXT PRIMARY KEY,count INTEGER NOT NULL,until_at INTEGER NOT NULL);''')


def attempt(action, identity, limit=12, window=300, now=None):
    now = int(time.time()) if now is None else now
    key = hashlib.sha256((action+':'+identity).encode()).hexdigest()
    with connect() as c:
        c.execute('DELETE FROM customer_auth_limits WHERE until_at<=?', (now,))
        c.execute('BEGIN IMMEDIATE') if not c.in_transaction else None
        row = c.execute('SELECT count FROM customer_auth_limits WHERE key=?', (key,)).fetchone()
        if row and row['count'] >= limit:
            raise PermissionError('Too many attempts. Wait a few minutes before trying again.')
        c.execute('INSERT INTO customer_auth_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key,now+window))


def register(email, password, name=''):
    if os.getenv('AUTOPOST_CUSTOMER_SIGNUP_ENABLED','true').lower() != 'true':
        raise PermissionError('New accounts are temporarily unavailable.')
    if not isinstance(email,str) or not 3<=len(email.strip())<=254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email.strip()):
        raise ValueError('Enter a valid email address.')
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Use a password of 12–256 characters.')
    if not isinstance(name,str) or not name.strip() or len(name.strip())>80:
        raise ValueError('Enter your name (up to 80 characters).')
    user_id=secrets.token_hex(16); workspace_id='ws_'+secrets.token_hex(16)
    with connect() as c:
        try:
            c.execute('INSERT INTO customer_accounts VALUES(?,?,?,?,?,?)', (user_id,workspace_id,email.strip().lower(),name.strip(),generate_password_hash(password,method='scrypt'),int(time.time())))
        except sqlite3.IntegrityError:
            raise ValueError('Unable to create that account. Try signing in instead.') from None
    import app_control
    app_control.init(workspace_id)
    return get(user_id)


def login(email,password):
    if not isinstance(email,str) or not isinstance(password,str) or len(email)>254 or len(password)>256:
        return None
    with connect() as c:
        row=c.execute('SELECT * FROM customer_accounts WHERE email=?',(email.strip().lower(),)).fetchone()
    # Use the same expensive password calculation when the email is unknown.
    if not row:
        generate_password_hash(password,method='scrypt')
        return None
    return get(row['id']) if check_password_hash(row['password_hash'],password) else None


def get(user_id):
    if not isinstance(user_id,str):return None
    with connect() as c:
        row=c.execute('SELECT id,workspace_id,email,name,created_at FROM customer_accounts WHERE id=?',(user_id,)).fetchone()
    return dict(row) if row else None


def workspace_exists(workspace_id):
    with connect() as c:
        return bool(c.execute('SELECT 1 FROM customer_accounts WHERE workspace_id=?',(workspace_id,)).fetchone())
