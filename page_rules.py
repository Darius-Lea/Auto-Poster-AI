"""Explicit content-source-to-Facebook-Page routing for future automations.

A saved rule DOES NOT create a collector, grant scraping rights, or start a
worker. It only records approved destination intent for the workspace.
"""
from __future__ import annotations

import re
import sqlite3

import meta_accounts

SOURCE_KEY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}\Z")
MODES = ("review", "autopilot")


def init():
    with meta_accounts._db() as c:
        c.execute("""
          CREATE TABLE IF NOT EXISTS facebook_content_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            source_key TEXT NOT NULL,
            page_id TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'review' CHECK(mode IN ('review','autopilot')),
            enabled INTEGER NOT NULL DEFAULT 0,
            UNIQUE(workspace_id,source_key,page_id)
          )
        """)


def add_route(workspace_id, source_key, page_id, *, mode="review", enabled=False):
    """Save an explicit route. Enable/autopilot must be a deliberate user choice."""
    if not isinstance(source_key, str) or not SOURCE_KEY.fullmatch(source_key):
        raise ValueError("Use a short source identifier (letters, numbers, dots, dashes)")
    if mode not in MODES or not isinstance(enabled, bool):
        raise ValueError("Unsupported rule mode")
    allowed = {p["page_id"] for p in meta_accounts.list_pages(workspace_id)
               if p["can_publish"]}
    if page_id not in allowed:
        raise PermissionError("Connect and authorize that Page before assigning rules")
    with meta_accounts._db() as c:
        c.execute("""
            INSERT INTO facebook_content_routes
            (workspace_id,source_key,page_id,mode,enabled)
            VALUES(?,?,?,?,?)
            ON CONFLICT(workspace_id,source_key,page_id)
            DO UPDATE SET mode=excluded.mode,enabled=excluded.enabled
        """, (workspace_id, source_key, page_id, mode, int(enabled)))


def list_routes(workspace_id):
    with meta_accounts._db() as c:
        rows = c.execute("""
            SELECT r.id,r.source_key,r.page_id,r.mode,r.enabled,p.name
            FROM facebook_content_routes r
            JOIN connected_facebook_pages p
              ON p.workspace_id=r.workspace_id AND p.page_id=r.page_id
            WHERE r.workspace_id=?
            ORDER BY r.source_key,p.name
        """, (workspace_id,)).fetchall()
    return [dict(row) for row in rows]


def eligible_destinations(workspace_id, source_key, *, require_auto=False):
    """A source can route only to explicitly enabled and still-connected Pages."""
    with meta_accounts._db() as c:
        rows = c.execute("""
            SELECT r.page_id FROM facebook_content_routes r
            JOIN connected_facebook_pages p
              ON p.workspace_id=r.workspace_id AND p.page_id=r.page_id
            WHERE r.workspace_id=? AND r.source_key=? AND r.enabled=1
              AND (?=0 OR r.mode='autopilot')
            ORDER BY r.page_id
        """, (workspace_id, source_key, int(require_auto))).fetchall()
    return [row["page_id"] for row in rows]


def delete_route(workspace_id, rule_id):
    with meta_accounts._db() as c:
        c.execute("DELETE FROM facebook_content_routes WHERE workspace_id=? AND id=?",
                  (workspace_id, rule_id))


def remove_page_routes(workspace_id, page_id):
    """Disconnect permanently removes automation rules; reconnect stays opt-in."""
    with meta_accounts._db() as c:
        c.execute("DELETE FROM facebook_content_routes WHERE workspace_id=? AND page_id=?",
                  (workspace_id, page_id))
