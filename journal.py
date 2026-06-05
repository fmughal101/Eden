"""
Signal & Trade Journal
======================
SQLite-backed log of every webhook signal received (and, later, every order placed).
Single file: journal.db. No setup required — sqlite3 is in the stdlib.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path("journal.db")


def _conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at   TEXT    NOT NULL,
                source        TEXT    NOT NULL,
                symbol        TEXT,
                action        TEXT,
                price         REAL,
                stop          REAL,
                strategy      TEXT,
                raw_payload   TEXT    NOT NULL,
                status        TEXT    NOT NULL,
                notes         TEXT,
                order_id      TEXT,
                fill_price    REAL,
                shares        INTEGER
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_received_at ON signals(received_at DESC)")


def log_signal(source, payload, status, notes=None, **fields):
    """Insert a signal row. Returns the new row id."""
    row = {
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source":      source,
        "symbol":      fields.get("symbol"),
        "action":      fields.get("action"),
        "price":       fields.get("price"),
        "stop":        fields.get("stop"),
        "strategy":    fields.get("strategy"),
        "raw_payload": json.dumps(payload),
        "status":      status,
        "notes":       notes,
        "order_id":    fields.get("order_id"),
        "fill_price":  fields.get("fill_price"),
        "shares":      fields.get("shares"),
    }
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO signals
               (received_at, source, symbol, action, price, stop, strategy,
                raw_payload, status, notes, order_id, fill_price, shares)
               VALUES (:received_at, :source, :symbol, :action, :price, :stop,
                       :strategy, :raw_payload, :status, :notes, :order_id,
                       :fill_price, :shares)""",
            row,
        )
        return cur.lastrowid


def update_signal(signal_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = signal_id
    with _conn() as c:
        c.execute(f"UPDATE signals SET {cols} WHERE id = :id", fields)


def get_recent_signals(limit=100):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM signals ORDER BY received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_signal_stats():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        by_status = dict(
            c.execute(
                "SELECT status, COUNT(*) FROM signals GROUP BY status"
            ).fetchall()
        )
    return {"total": total, "by_status": by_status}
