"""Session memory store (SQLite).

Tables:
  users         — profile per phone number
  sessions      — each call (channel, archetype, Cekura score)
  action_items  — 30/90/365 items with status tracking
  prompt_versions — Cekura auto-improvement history
"""

import json
import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "calls.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                phone           TEXT    UNIQUE,
                name            TEXT,
                role            TEXT,
                channels        TEXT    DEFAULT '["life"]',
                time_horizon    INTEGER DEFAULT 5,
                onboarding_done INTEGER DEFAULT 0,
                profile_summary TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER REFERENCES users(id),
                phone           TEXT,
                channel         TEXT    DEFAULT 'life',
                archetype       TEXT,
                answers         TEXT    DEFAULT '{}',
                action_plan     TEXT    DEFAULT '{}',
                transcript      TEXT    DEFAULT '',
                status          TEXT    DEFAULT 'active',
                created_at      TEXT    DEFAULT (datetime('now')),
                completed_at    TEXT,
                cekura_score    REAL
            );

            CREATE TABLE IF NOT EXISTS action_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER REFERENCES sessions(id),
                user_id     INTEGER REFERENCES users(id),
                channel     TEXT,
                horizon     TEXT,
                description TEXT,
                status      TEXT DEFAULT 'pending',
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS prompt_versions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                version_name    TEXT    NOT NULL,
                channel         TEXT    DEFAULT 'life',
                intake_prompt   TEXT    NOT NULL,
                simulation_note TEXT,
                cekura_score    REAL,
                created_at      TEXT    DEFAULT (datetime('now')),
                is_active       INTEGER DEFAULT 1
            );
        """)


# ── Users ─────────────────────────────────────────────────────────────────────

def get_user_by_phone(phone: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["channels"] = json.loads(d.get("channels") or '["life"]')
        return d


def create_user(phone: str, name: str, role: str, time_horizon: int, channel: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (phone, name, role, time_horizon, channels) VALUES (?,?,?,?,?)",
            (phone, name, role, time_horizon, json.dumps([channel])),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        return row[0]


def complete_onboarding(user_id: int, profile_summary: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET onboarding_done=1, profile_summary=? WHERE id=?",
            (profile_summary, user_id),
        )
        conn.commit()


def get_user(user_id: int) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["channels"] = json.loads(d.get("channels") or '["life"]')
        return d


# ── Sessions ──────────────────────────────────────────────────────────────────

def save_session(
    phone: str | None,
    channel: str,
    archetype: str,
    answers: dict,
    action_plan: dict,
    user_id: int | None = None,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (user_id, phone, channel, archetype, answers, action_plan) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, phone, channel, archetype, json.dumps(answers), json.dumps(action_plan)),
        )
        conn.commit()
        return cur.lastrowid


def update_session_transcript(session_id: int, transcript: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET transcript=?, status='completed', completed_at=datetime('now') WHERE id=?",
            (transcript, session_id),
        )
        conn.commit()


def get_session(session_id: int) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["answers"] = json.loads(d.get("answers") or "{}")
        d["action_plan"] = json.loads(d.get("action_plan") or "{}")
        return d


def get_sessions_for_user(user_id: int, channel: str, limit: int = 5) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id=? AND channel=? ORDER BY id DESC LIMIT ?",
            (user_id, channel, limit),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["answers"] = json.loads(d.get("answers") or "{}")
            d["action_plan"] = json.loads(d.get("action_plan") or "{}")
            result.append(d)
        return result


# ── Action items ──────────────────────────────────────────────────────────────

def save_action_items(session_id: int, user_id: int, channel: str, plan: dict) -> None:
    with _conn() as conn:
        for horizon, description in [
            ("30_days", plan.get("day_30", "")),
            ("90_days", plan.get("day_90", "")),
            ("365_days", plan.get("day_365", "")),
        ]:
            if description:
                conn.execute(
                    "INSERT INTO action_items (session_id, user_id, channel, horizon, description) "
                    "VALUES (?,?,?,?,?)",
                    (session_id, user_id, channel, horizon, description),
                )
        conn.commit()


def get_pending_action_items(user_id: int, channel: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM action_items WHERE user_id=? AND channel=? AND status='pending' "
            "ORDER BY created_at DESC LIMIT 6",
            (user_id, channel),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Prompt versions ───────────────────────────────────────────────────────────

def get_active_prompt_override(channel: str) -> Optional[str]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT intake_prompt FROM prompt_versions WHERE channel=? AND is_active=1 "
            "ORDER BY id DESC LIMIT 1",
            (channel,),
        ).fetchone()
        return row[0] if row else None


def save_prompt_version(
    version_name: str, channel: str, intake_prompt: str, score: float | None = None
) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE prompt_versions SET is_active=0 WHERE channel=?", (channel,)
        )
        conn.execute(
            "INSERT INTO prompt_versions (version_name, channel, intake_prompt, cekura_score) "
            "VALUES (?,?,?,?)",
            (version_name, channel, intake_prompt, score),
        )
        conn.commit()
