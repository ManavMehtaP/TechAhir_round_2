"""SQLite persistence: accounts, login sessions, auctions and bids.

Why SQLite: zero setup (it's a file), ACID, and fast enough for the write-through
pattern used here. The same SQL moves to Postgres with minor changes.

Concurrency: one connection shared by all threads, every operation serialized by a
lock. Each write is a single short transaction. synchronous=FULL means a bid is
fsync'd to disk *before* it is acknowledged to the bidder.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,                 -- sha256 of the token; the raw token is never stored
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS auctions (
  id               TEXT PRIMARY KEY,
  title            TEXT NOT NULL,
  seller           TEXT NOT NULL,
  start_price      INTEGER NOT NULL,
  min_increment    INTEGER NOT NULL,
  start_time       REAL NOT NULL,
  end_time         REAL NOT NULL,              -- moves forward when a late bid extends the auction
  extension_window REAL NOT NULL,
  extension_amount REAL NOT NULL,
  extensions       INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'OPEN',
  winner           TEXT,
  winning_amount   INTEGER,
  closed_at        REAL,
  description      TEXT NOT NULL DEFAULT '',
  image_url        TEXT NOT NULL DEFAULT '',
  category         TEXT NOT NULL DEFAULT 'Collectibles',
  created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bids (
  auction_id TEXT NOT NULL REFERENCES auctions(id),
  seq        INTEGER NOT NULL,                 -- 1,2,3... total order of accepted bids in this auction
  bidder     TEXT NOT NULL,
  amount     INTEGER NOT NULL,
  at         REAL NOT NULL,
  extended   INTEGER NOT NULL DEFAULT 0,       -- 1 if this bid extended the auction
  PRIMARY KEY (auction_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_bids_bidder ON bids(bidder);
"""


class UserExists(Exception):
    pass


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Upgrade a database created by an older version (adds listing columns)."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(auctions)")}
        for col, ddl in (("description", "TEXT NOT NULL DEFAULT ''"),
                         ("image_url", "TEXT NOT NULL DEFAULT ''"),
                         ("category", "TEXT NOT NULL DEFAULT 'Collectibles'")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE auctions ADD COLUMN {col} {ddl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _one(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # ---- accounts ----------------------------------------------------
    def create_user(self, username: str, password_hash: str) -> dict:
        try:
            with self._tx() as c:
                cur = c.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
                                (username, password_hash, time.time()))
                uid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise UserExists(username)
        return {"id": uid, "username": username}

    def get_user_by_name(self, username: str) -> Optional[dict]:
        r = self._one("SELECT id, username, password_hash, created_at FROM users WHERE username = ?", (username,))
        return dict(r) if r else None

    # ---- sessions ----------------------------------------------------
    def create_session(self, token_hash: str, user_id: int, expires_at: float) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))   # opportunistic cleanup
            c.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                      (token_hash, user_id, time.time(), expires_at))

    def user_for_token(self, token_hash: str, now: Optional[float] = None) -> Optional[dict]:
        now = time.time() if now is None else now
        r = self._one("""SELECT u.id, u.username, u.created_at FROM sessions s
                         JOIN users u ON u.id = s.user_id
                         WHERE s.token_hash = ? AND s.expires_at > ?""", (token_hash, now))
        return dict(r) if r else None

    def delete_session(self, token_hash: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # ---- auctions and bids (called by the engine) --------------------
    def insert_auction(self, a: dict) -> None:
        with self._tx() as c:
            c.execute("""INSERT INTO auctions(id, title, seller, start_price, min_increment, start_time, end_time,
                                              extension_window, extension_amount, description, image_url,
                                              category, created_at)
                         VALUES (:id,:title,:seller,:start_price,:min_increment,:start_time,:end_time,
                                 :extension_window,:extension_amount,:description,:image_url,
                                 :category,:created_at)""",
                      {**a, "created_at": time.time()})

    def record_bid(self, auction_id: str, seq: int, bidder: str, amount: int, at: float,
                   extended: bool, new_end_time: float, extensions: int) -> None:
        """Insert the bid and move the auction's end time in ONE transaction."""
        with self._tx() as c:
            c.execute("INSERT INTO bids(auction_id, seq, bidder, amount, at, extended) VALUES (?,?,?,?,?,?)",
                      (auction_id, seq, bidder, amount, at, 1 if extended else 0))
            c.execute("UPDATE auctions SET end_time = ?, extensions = ? WHERE id = ? AND status = 'OPEN'",
                      (new_end_time, extensions, auction_id))

    def record_close(self, auction_id: str, closed_at: float, winner: Optional[str],
                     winning_amount: Optional[int]) -> None:
        with self._tx() as c:
            c.execute("""UPDATE auctions SET status='CLOSED', closed_at=?, winner=?, winning_amount=?
                         WHERE id = ? AND status = 'OPEN'""", (closed_at, winner, winning_amount, auction_id))

    def load_all(self):
        """Everything needed to rebuild engine state after a restart."""
        auctions = [dict(r) for r in self._all("SELECT * FROM auctions ORDER BY created_at")]
        bids: dict[str, list[dict]] = {}
        for r in self._all("SELECT auction_id, seq, bidder, amount, at FROM bids ORDER BY auction_id, seq"):
            bids.setdefault(r["auction_id"], []).append(dict(r))
        return auctions, bids

    # ---- my bids / seller profile -----------------------------------
    def user_bid_stats(self, username: str) -> list[dict]:
        """One row per auction the user has bid on: their highest bid, bid count, last bid time."""
        rows = self._all("""SELECT auction_id, MAX(amount) AS my_amount, COUNT(*) AS my_bids, MAX(at) AS last_at
                            FROM bids WHERE bidder = ? GROUP BY auction_id ORDER BY last_at DESC""", (username,))
        return [dict(r) for r in rows]

    def public_profile(self, username: str) -> Optional[dict]:
        u = self.get_user_by_name(username)
        if not u:
            return None
        listed = self._one("SELECT COUNT(*) n FROM auctions WHERE seller = ?", (u["username"],))["n"]
        return {"username": u["username"], "member_since": u["created_at"], "auctions_listed": listed}

    # ---- account summary --------------------------------------------
    def user_summary(self, username: str) -> dict:
        u = self.get_user_by_name(username)
        placed = self._one("SELECT COUNT(*) n FROM bids WHERE bidder = ?", (username,))["n"]
        created = self._one("SELECT COUNT(*) n FROM auctions WHERE seller = ?", (username,))["n"]
        won = self._all("""SELECT id AS auction_id, title, winning_amount AS amount, closed_at
                           FROM auctions WHERE winner = ? ORDER BY closed_at DESC LIMIT 50""", (username,))
        recent = self._all("""SELECT b.auction_id, a.title, b.amount, b.at FROM bids b
                              JOIN auctions a ON a.id = b.auction_id
                              WHERE b.bidder = ? ORDER BY b.at DESC LIMIT 25""", (username,))
        return {
            "username": username,
            "member_since": u["created_at"] if u else None,
            "bids_placed": placed,
            "auctions_created": created,
            "auctions_won": [dict(r) for r in won],
            "recent_bids": [dict(r) for r in recent],
        }
