"""SQLite ticket store. Every statement is parameterised; every input is validated before it is stored.

The store is plain Python with no MCP dependency, so it is unit-tested directly and the MCP layer
(`server.py`) stays a thin adapter.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import similarity as sim

STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("low", "medium", "high", "critical")
MAX_TITLE, MAX_BODY, MAX_COMMENT, MAX_SHORT = 200, 5000, 2000, 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open'   CHECK (status IN ('open','in_progress','resolved','closed')),
    priority    TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
    component   TEXT NOT NULL DEFAULT 'general',
    reporter    TEXT NOT NULL DEFAULT 'unknown',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
"""


class TicketError(ValueError):
    """A request the store refuses (bad input, unknown ticket). The message is safe to show the model."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise TicketError(f"{field} must be a string")
    value = value.replace("\x00", "").strip()
    if required and not value:
        raise TicketError(f"{field} must not be empty")
    if len(value) > max_len:
        raise TicketError(f"{field} is too long ({len(value)} > {max_len} characters)")
    return value


def _choice(value: str | None, field: str, allowed: tuple[str, ...]) -> str:
    v = (value or "").strip().lower()
    if v not in allowed:
        raise TicketError(f"{field} must be one of {list(allowed)}, got {value!r}")
    return v


def _ticket_id(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise TicketError(f"ticket_id must be an integer, got {value!r}") from None
    if n < 1:
        raise TicketError("ticket_id must be >= 1")
    return n


class TicketStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)  # waits on the lock if another server process writes
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # ------------------------------------------------------------------ reads
    def count(self) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

    def get(self, ticket_id: Any) -> dict:
        tid = _ticket_id(ticket_id)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
            if row is None:
                raise TicketError(f"ticket {tid} does not exist")
            comments = db.execute(
                "SELECT id, author, body, created_at FROM comments WHERE ticket_id = ? ORDER BY id", (tid,)
            ).fetchall()
        return {**dict(row), "comments": [dict(c) for c in comments]}

    def _candidates(self, status: str | None, component: str | None, priority: str | None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM tickets WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(_choice(status, "status", STATUSES))
        if priority:
            sql += " AND priority = ?"
            args.append(_choice(priority, "priority", PRIORITIES))
        if component:
            sql += " AND lower(component) = ?"
            args.append(_clean(component, "component", MAX_SHORT).lower())
        with closing(self._connect()) as db:
            return db.execute(sql + " ORDER BY id", args).fetchall()

    def search(self, query: str = "", status: str | None = None, component: str | None = None,
               priority: str | None = None, limit: int = 10) -> list[dict]:
        """Keyword search ranked by query coverage; with an empty query it lists tickets by recency."""
        query = _clean(query, "query", MAX_BODY)
        limit = max(1, min(int(limit), 50))
        rows = self._candidates(status, component, priority)
        if not query:
            return [self._summary(r) for r in sorted(rows, key=lambda r: -r["id"])[:limit]]
        q = sim.term_counts(query)
        scored = []
        for r in rows:
            doc = sim.term_counts(r["title"], r["description"])
            cov = sim.coverage(q, doc)
            if cov > 0:
                scored.append((0.7 * cov + 0.3 * sim.cosine(q, doc), r, sim.shared_terms(q, doc)))
        scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
        return [{**self._summary(r), "score": round(s, 3), "matched_terms": terms} for s, r, terms in scored[:limit]]

    def find_duplicates(self, title: str, description: str = "", threshold: float = 0.35, limit: int = 5) -> list[dict]:
        """Existing tickets that look like the same problem, best first, with the terms that matched."""
        title = _clean(title, "title", MAX_TITLE, required=True)
        description = _clean(description, "description", MAX_BODY)
        threshold = max(0.0, min(float(threshold), 1.0))
        limit = max(1, min(int(limit), 20))
        mine = sim.term_counts(title, description)
        out = []
        for r in self._candidates(None, None, None):
            theirs = sim.term_counts(r["title"], r["description"])
            score = sim.cosine(mine, theirs)
            if score >= threshold:
                out.append({**self._summary(r), "score": round(score, 3), "matched_terms": sim.shared_terms(mine, theirs)})
        out.sort(key=lambda x: (-x["score"], -x["id"]))
        return out[:limit]

    @staticmethod
    def _summary(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["description"] = d["description"][:240] + ("..." if len(d["description"]) > 240 else "")
        return d

    # ----------------------------------------------------------------- writes
    def create(self, title: str, description: str = "", priority: str = "medium", component: str = "general",
               reporter: str = "bug-assistant") -> dict:
        title = _clean(title, "title", MAX_TITLE, required=True)
        description = _clean(description, "description", MAX_BODY)
        priority = _choice(priority, "priority", PRIORITIES)
        component = _clean(component, "component", MAX_SHORT) or "general"
        reporter = _clean(reporter, "reporter", MAX_SHORT) or "unknown"
        now = _now()
        with closing(self._connect()) as db, db:
            cur = db.execute(
                "INSERT INTO tickets (title, description, status, priority, component, reporter, created_at, updated_at)"
                " VALUES (?, ?, 'open', ?, ?, ?, ?, ?)",
                (title, description, priority, component, reporter, now, now),
            )
            tid = cur.lastrowid
        return self.get(tid)

    def update(self, ticket_id: Any, status: str | None = None, priority: str | None = None,
               component: str | None = None, title: str | None = None, description: str | None = None) -> dict:
        tid = _ticket_id(ticket_id)
        sets: dict[str, str] = {}
        if status is not None:
            sets["status"] = _choice(status, "status", STATUSES)
        if priority is not None:
            sets["priority"] = _choice(priority, "priority", PRIORITIES)
        if component is not None:
            sets["component"] = _clean(component, "component", MAX_SHORT, required=True)
        if title is not None:
            sets["title"] = _clean(title, "title", MAX_TITLE, required=True)
        if description is not None:
            sets["description"] = _clean(description, "description", MAX_BODY)
        if not sets:
            raise TicketError("nothing to update: pass at least one of status, priority, component, title, description")
        sets["updated_at"] = _now()
        cols = ", ".join(f"{c} = ?" for c in sets)  # column names come from the fixed dict keys above, never from input
        with closing(self._connect()) as db, db:
            cur = db.execute(f"UPDATE tickets SET {cols} WHERE id = ?", (*sets.values(), tid))
            if cur.rowcount == 0:
                raise TicketError(f"ticket {tid} does not exist")
        return self.get(tid)

    def add_comment(self, ticket_id: Any, body: str, author: str = "bug-assistant") -> dict:
        tid = _ticket_id(ticket_id)
        body = _clean(body, "body", MAX_COMMENT, required=True)
        author = _clean(author, "author", MAX_SHORT) or "unknown"
        with closing(self._connect()) as db, db:
            if db.execute("SELECT 1 FROM tickets WHERE id = ?", (tid,)).fetchone() is None:
                raise TicketError(f"ticket {tid} does not exist")
            db.execute("INSERT INTO comments (ticket_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                       (tid, author, body, _now()))
            db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (_now(), tid))
        return self.get(tid)

    # ------------------------------------------------------------------- seed
    def seed(self, rows: list[dict]) -> int:
        """Insert demo tickets (with comments) only into an empty database. Returns how many were added."""
        # One IMMEDIATE transaction: if two server processes start at once (the agent launches one per
        # toolset), the second waits for the lock, sees a non-empty table and adds nothing.
        db = self._connect()
        try:
            db.isolation_level = None  # manual transaction control for this connection
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]:
                db.execute("ROLLBACK")
                return 0
            now = _now()
            for row in rows:
                cur = db.execute(
                    "INSERT INTO tickets (title, description, status, priority, component, reporter, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_clean(row["title"], "title", MAX_TITLE, required=True), _clean(row.get("description"), "description", MAX_BODY),
                     _choice(row.get("status", "open"), "status", STATUSES), _choice(row.get("priority", "medium"), "priority", PRIORITIES),
                     _clean(row.get("component", "general"), "component", MAX_SHORT), _clean(row.get("reporter", "seed"), "reporter", MAX_SHORT),
                     now, now),
                )
                for c in row.get("comments", []):
                    db.execute("INSERT INTO comments (ticket_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                               (cur.lastrowid, c.get("author", "seed"), _clean(c["body"], "body", MAX_COMMENT, required=True), now))
            db.execute("COMMIT")
            return len(rows)
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

