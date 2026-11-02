"""A replay store that survives restarts and is safe across processes.

`ReplayGuard` in verify.py is in-process memory: restart the seller, or run two workers,
and every spent block is spendable again. `SqliteReplayGuard` is the drop-in for it:
one file, standard library, and `claim` is a single atomic INSERT so that two requests
presenting the same block at the same instant cannot both win.

A block is spent ONCE, for whatever resource first claimed it: one payment buys one call.
(The in-memory guard keyed on resource too; keying on the block alone is strictly
stricter, so it refuses more and admits less.)
"""
import os
import sqlite3
import threading
import time


class SqliteReplayGuard:
    def __init__(self, path: str):
        if not path:
            raise ValueError("SqliteReplayGuard needs a file path")
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("CREATE TABLE IF NOT EXISTS spent ("
                         "block_hash TEXT PRIMARY KEY, resource TEXT NOT NULL, at INTEGER NOT NULL)")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def seen(self, resource, block_hash) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM spent WHERE block_hash=?",
                                   (block_hash.upper(),)).fetchone()
        return row is not None

    def claim(self, resource, block_hash) -> bool:
        """True exactly once per block, across threads and processes."""
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO spent VALUES (?,?,?)",
                                   (block_hash.upper(), resource or "", int(time.time())))
            return cur.rowcount == 1

    def remember(self, resource, block_hash) -> None:
        self.claim(resource, block_hash)

    def close(self):
        self._db.close()
