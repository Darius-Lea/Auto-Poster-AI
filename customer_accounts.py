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
          key TEXT PRIMARY KEY,count INTEGER NOT NULL,until_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS customer_password_resets(
          token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS customer_email_verifications(
          token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,email TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at INTEGER NOT NULL);''')
        if 'auth_version' not in {r[1] for r in c.execute('PRAGMA table_info(customer_accounts)')}:
            c.execute('ALTER TABLE customer_accounts ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0')
        if 'email_verified' not in {r[1] for r in c.execute('PRAGMA table_info(customer_accounts)')}:
            c.execute('ALTER TABLE customer_accounts ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0')


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
            c.execute('INSERT INTO customer_accounts(id,workspace_id,email,name,password_hash,created_at) VALUES(?,?,?,?,?,?)', (user_id,workspace_id,email.strip().lower(),name.strip(),generate_password_hash(password,method='scrypt'),int(time.time())))
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
        row=c.execute('SELECT id,workspace_id,email,name,created_at,auth_version,email_verified FROM customer_accounts WHERE id=?',(user_id,)).fetchone()
    if not row:return None
    user=dict(row);user['email_verified']=bool(user['email_verified']);return user


def workspace_exists(workspace_id):
    with connect() as c:
        return bool(c.execute('SELECT 1 FROM customer_accounts WHERE workspace_id=?',(workspace_id,)).fetchone())


def verify_password(user_id, password):
    if not isinstance(password,str) or not 1<=len(password)<=256:
        return False
    with connect() as c:
        row=c.execute('SELECT password_hash FROM customer_accounts WHERE id=?',(user_id,)).fetchone()
    return bool(row and check_password_hash(row['password_hash'],password))


def _password_hash(password):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Use a password of 12–256 characters.')
    return generate_password_hash(password,method='scrypt')


def create_reset_token(email, now=None):
    """Internal delivery helper. Callers must never return this token to a browser."""
    now=int(time.time()) if now is None else int(now)
    if not isinstance(email,str) or len(email)>254:
        return None
    with connect() as c:
        c.execute('DELETE FROM customer_password_resets WHERE expires_at<=?',(now,))
        row=c.execute('SELECT id,email FROM customer_accounts WHERE email=?',(email.strip().lower(),)).fetchone()
        if not row:
            return None
        token=secrets.token_hex(32)
        c.execute('DELETE FROM customer_password_resets WHERE user_id=?',(row['id'],))
        c.execute('INSERT INTO customer_password_resets VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],now+1800,now))
    return {'token':token,'email':row['email'],'expires_at':now+1800}


def reset_password(token,password,now=None):
    now=int(time.time()) if now is None else int(now)
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):
        raise ValueError('This password-reset link is invalid or expired. Request a new one.')
    password_hash=_password_hash(password)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT user_id FROM customer_password_resets WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),now)).fetchone()
        if not row:
            raise ValueError('This password-reset link is invalid or expired. Request a new one.')
        changed=c.execute('UPDATE customer_accounts SET password_hash=?,auth_version=auth_version+1 WHERE id=?',(password_hash,row['user_id']))
        if not changed.rowcount:
            raise ValueError('This password-reset link is invalid or expired. Request a new one.')
        c.execute('DELETE FROM customer_password_resets WHERE user_id=?',(row['user_id'],))
    return True


def change_password(user_id,current_password,new_password):
    if not isinstance(current_password,str) or not 1<=len(current_password)<=256:
        raise ValueError('Your current password did not match.')
    with connect() as c:
        row=c.execute('SELECT password_hash FROM customer_accounts WHERE id=?',(user_id,)).fetchone()
    if not row or not check_password_hash(row['password_hash'],current_password):
        raise ValueError('Your current password did not match.')
    password_hash=_password_hash(new_password)
    with connect() as c:
        changed=c.execute('UPDATE customer_accounts SET password_hash=?,auth_version=auth_version+1 WHERE id=? AND password_hash=?',(password_hash,user_id,row['password_hash']))
        if not changed.rowcount:
            raise ValueError('Your password changed during this request. Sign in again before trying.')
        c.execute('DELETE FROM customer_password_resets WHERE user_id=?',(user_id,))
    return True


def create_verification_token(user_id,now=None):
    """Internal-only token for the account's current email, separate from resets."""
    now=int(time.time()) if now is None else int(now)
    with connect() as c:
        c.execute('DELETE FROM customer_email_verifications WHERE expires_at<=?',(now,))
        user=c.execute('SELECT id,email,email_verified FROM customer_accounts WHERE id=?',(user_id,)).fetchone()
        if not user or user['email_verified']:
            return None
        token=secrets.token_hex(32)
        c.execute('DELETE FROM customer_email_verifications WHERE user_id=?',(user_id,))
        c.execute('INSERT INTO customer_email_verifications VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,user['email'],now+1800,now))
    return {'token':token,'email':user['email'],'expires_at':now+1800}


def verify_email(token,now=None):
    now=int(time.time()) if now is None else int(now)
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):
        raise ValueError('This email confirmation link is invalid or expired. Request a new one in Account settings.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT user_id,email FROM customer_email_verifications WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),now)).fetchone()
        if not row:
            raise ValueError('This email confirmation link is invalid or expired. Request a new one in Account settings.')
        changed=c.execute('UPDATE customer_accounts SET email_verified=1 WHERE id=? AND email=?',(row['user_id'],row['email']))
        if not changed.rowcount:
            raise ValueError('This email confirmation link is invalid or expired. Request a new one in Account settings.')
        c.execute('DELETE FROM customer_email_verifications WHERE user_id=?',(row['user_id'],))
    return True
