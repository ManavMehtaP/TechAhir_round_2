"""Everything important must survive a restart, and a failing database must never
produce an acknowledged-but-lost bid."""
import threading

import pytest
from fastapi.testclient import TestClient

from auction import auth
from auction.api import create_app
from auction.db import Database
from auction.engine import AuctionEngine, RejectReason, Status
from tests.test_engine import FakeClock


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


def boot(path, clock):
    """Simulate (re)starting the server: fresh engine, same database file."""
    db = Database(str(path))
    eng = AuctionEngine(clock=clock, store=db)
    eng.load_from_store()
    return eng, db


def test_state_survives_restart(tmp_path):
    clock = FakeClock()
    path = tmp_path / "a.db"
    eng, db = boot(path, clock)
    eng.create_auction("a1", "seller", 100, 10, duration=120, title="Lamp")
    eng.place_bid("a1", "alice", 100)
    clock.advance(95)                                   # 25s left -> next bid extends
    r = eng.place_bid("a1", "bob", 110)
    assert r.extended
    before = eng.snapshot("a1")
    db.close()

    eng2, db2 = boot(path, clock)
    after = eng2.snapshot("a1")
    for k in ("title", "seller", "status", "current_price", "min_next_bid", "leader", "bid_count",
              "end_time", "extensions"):
        assert after[k] == before[k], k
    assert [b["bidder"] for b in eng2.recent_bids("a1")] == ["bob", "alice"]
    # and it keeps working: the restored auction accepts the right next bid and rejects a low one
    assert eng2.place_bid("a1", "carol", 115).reason is RejectReason.BID_TOO_LOW
    assert eng2.place_bid("a1", "carol", 120).accepted
    db2.close()


def test_winner_is_persisted_and_survives_restart(tmp_path):
    clock = FakeClock()
    path = tmp_path / "a.db"
    eng, db = boot(path, clock)
    eng.create_auction("a1", "seller", 100, 10, duration=60)
    eng.place_bid("a1", "alice", 100)
    eng.place_bid("a1", "bob", 150)
    clock.advance(500)
    assert eng.sweep() == 1
    db.close()

    eng2, db2 = boot(path, clock)
    s = eng2.snapshot("a1")
    assert s["status"] == "CLOSED" and s["winner"] == "bob" and s["winning_amount"] == 150
    assert not eng2.place_bid("a1", "carol", 10**6).accepted
    db2.close()


def test_auction_that_ended_while_server_was_down_closes_with_correct_winner(tmp_path):
    clock = FakeClock()
    path = tmp_path / "a.db"
    eng, db = boot(path, clock)
    eng.create_auction("a1", "seller", 100, 10, duration=60)
    eng.place_bid("a1", "alice", 100)
    db.close()                                          # "crash" with the auction still OPEN in the DB

    clock.advance(10_000)                               # server comes back long after the deadline
    eng2, db2 = boot(path, clock)
    assert not eng2.place_bid("a1", "late", 999).accepted
    eng2.sweep()
    s = eng2.snapshot("a1")
    assert s["status"] == "CLOSED" and s["winner"] == "alice"
    row = db2._one("SELECT status, winner, winning_amount FROM auctions WHERE id='a1'")
    assert (row["status"], row["winner"], row["winning_amount"]) == ("CLOSED", "alice", 100)
    db2.close()


class FlakyStore:
    """Wraps a real store but can be told to fail writes."""
    def __init__(self, real):
        self.real, self.fail_bids, self.fail_close = real, False, False

    def insert_auction(self, a):
        return self.real.insert_auction(a)

    def record_bid(self, *args, **kw):
        if self.fail_bids:
            raise RuntimeError("disk full")
        return self.real.record_bid(*args, **kw)

    def record_close(self, *args, **kw):
        if self.fail_close:
            raise RuntimeError("disk full")
        return self.real.record_close(*args, **kw)

    def load_all(self):
        return self.real.load_all()


def test_failed_db_write_means_bid_is_rejected_and_nothing_changes():
    clock = FakeClock()
    store = FlakyStore(Database(":memory:"))
    eng = AuctionEngine(clock=clock, store=store)
    eng.create_auction("a1", "seller", 100, 10, duration=120)
    assert eng.place_bid("a1", "alice", 100).accepted

    store.fail_bids = True
    r = eng.place_bid("a1", "bob", 500)
    assert not r.accepted and r.reason is RejectReason.INTERNAL_ERROR
    s = eng.snapshot("a1")
    assert s["current_price"] == 100 and s["leader"] == "alice" and s["bid_count"] == 1

    store.fail_bids = False
    assert eng.place_bid("a1", "bob", 110).accepted          # recovers cleanly, seq stays contiguous
    assert [b["seq"] for b in eng.recent_bids("a1")] == [2, 1]


def test_failed_close_write_is_retried_and_late_bids_are_still_refused():
    clock = FakeClock()
    store = FlakyStore(Database(":memory:"))
    eng = AuctionEngine(clock=clock, store=store)
    eng.create_auction("a1", "seller", 100, 10, duration=60)
    eng.place_bid("a1", "alice", 100)
    clock.advance(100)

    store.fail_close = True
    assert eng.sweep() == 0 and eng.get("a1").status is Status.OPEN        # couldn't persist yet
    assert not eng.place_bid("a1", "late", 999).accepted                   # deadline passed: still refused

    store.fail_close = False
    assert eng.sweep() == 1
    assert eng.snapshot("a1")["winner"] == "alice"


def test_concurrent_bids_database_matches_memory(tmp_path):
    clock = FakeClock()
    eng, db = boot(tmp_path / "a.db", clock)
    eng.create_auction("a1", "seller", 100, 10, duration=1000)
    n = 40
    barrier = threading.Barrier(n)
    results = []

    def go(i):
        barrier.wait()
        for k in range(10):
            results.append(eng.place_bid("a1", f"u{i}", 100 + (i * 10 + k) * 10))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    accepted = sum(r.accepted for r in results)
    rows = db._all("SELECT seq, amount FROM bids WHERE auction_id='a1' ORDER BY seq")
    assert len(rows) == accepted == eng.get("a1").bid_count
    assert [r["seq"] for r in rows] == list(range(1, accepted + 1))
    amounts = [r["amount"] for r in rows]
    assert all(b >= a + 10 for a, b in zip(amounts, amounts[1:]))          # strictly increasing in the DB too
    db.close()


def test_accounts_and_auctions_survive_api_restart(tmp_path):
    path = str(tmp_path / "app.db")
    with TestClient(create_app(db_path=path)) as c:
        tok = c.post("/auth/register", json={"username": "alice", "password": "password123"}).json()["token"]
        sel = c.post("/auth/register", json={"username": "seller", "password": "password123"}).json()["token"]
        c.post("/auctions", headers={"Authorization": "Bearer " + sel},
               json={"id": "p1", "title": "Persist me", "start_price": 50, "min_increment": 5, "duration_seconds": 300})
        assert c.post("/auctions/p1/bids", headers={"Authorization": "Bearer " + tok},
                      json={"amount": 55}).status_code == 200

    # brand-new app object and engine, same file
    with TestClient(create_app(db_path=path)) as c2:
        assert c2.get("/auth/me", headers={"Authorization": "Bearer " + tok}).json()["username"] == "alice"  # still logged in
        login = c2.post("/auth/login", json={"username": "alice", "password": "password123"})
        assert login.status_code == 200
        s = c2.get("/auctions/p1").json()
        assert s["title"] == "Persist me" and s["current_price"] == 55 and s["leader"] == "alice"
        assert c2.get("/me/summary", headers={"Authorization": "Bearer " + tok}).json()["bids_placed"] == 1
