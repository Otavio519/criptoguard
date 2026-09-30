"""Persistência simples em SQLite: estado do robô, operações, patrimônio e log."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, side TEXT, time TEXT,
                price REAL, qty REAL, fee REAL, pnl REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS equity (time TEXT, mode TEXT, value REAL);
            CREATE TABLE IF NOT EXISTS logs (time TEXT, level TEXT, msg TEXT);
            """)
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(trades)")]
            if "symbol" not in cols:  # bancos antigos (uma moeda só)
                self.conn.execute("ALTER TABLE trades ADD COLUMN symbol TEXT")

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def add_trade(self, mode, side, price, qty, fee, pnl=None, reason="", symbol=None) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO trades (mode, side, time, price, qty, fee, pnl, reason, symbol) VALUES (?,?,?,?,?,?,?,?,?)",
                (mode, side, now_iso(), price, qty, fee, pnl, reason, symbol))

    def delete_like(self, pattern: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM state WHERE key LIKE ? ESCAPE '\\'", (pattern,))

    def add_equity(self, mode: str, value: float) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO equity VALUES (?,?,?)", (now_iso(), mode, value))

    def log(self, msg: str, level: str = "info") -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO logs VALUES (?,?,?)", (now_iso(), level, msg))

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
