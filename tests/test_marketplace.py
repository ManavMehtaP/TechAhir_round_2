"""Marketplace additions: listing metadata, participants, My Bids, seller profiles, DB upgrade.
None of this touches bidding rules; test_engine.py still guards those."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from auction import auth
from auction.api import create_app
from auction.db import Database
from auction.engine import AuctionEngine
from tests.test_engine import FakeClock


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


def signup(c, name):
    r = c.post("/auth/register", json={"username": name, "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def make(c, h, aid="a1", **kw):
    body = {"id": aid, "title": "Lamp", "start_price": 100, "min_increment": 10, "duration_seconds": 300, **kw}
    return c.post("/auctions", headers=h, json=body)


def test_listing_metadata_roundtrip_and_defaults():
    with TestClient(create_app(db_path=":memory:")) as c:
        seller = signup(c, "seller")
        r = make(c, seller, description="Nice lamp", category="Watches", image_url="https://example.com/a.jpg")
        assert r.status_code == 201
        s = c.get("/auctions/a1").json()
        assert (s["description"], s["category"], s["image_url"]) == ("Nice lamp", "Watches", "https://example.com/a.jpg")
        assert s["start_price"] == 100 and s["participants"] == 0
        d = make(c, seller, "a2").json()                       # defaults when omitted
        assert d["category"] == "Collectibles" and d["image_url"] == "" and d["description"] == ""


def test_listing_validation():
    with TestClient(create_app(db_path=":memory:")) as c:
        seller = signup(c, "seller")
        assert make(c, seller, "x1", category="Boats").status_code == 422                       # unknown category
        assert make(c, seller, "x2", image_url="javascript:alert(1)").status_code == 422        # only http(s) images
        assert make(c, seller, "x3", image_url="ftp://a/b.png").status_code == 422
        assert make(c, seller, "x4", description="d" * 2001).status_code == 422
        assert make(c, seller, "x5", image_url="http://ok.example/a.png").status_code == 201


def test_participants_counts_distinct_bidders():
    with TestClient(create_app(db_path=":memory:")) as c:
        seller, a, b = signup(c, "seller"), signup(c, "alice"), signup(c, "bob")
        make(c, seller)
        for h, amt in ((a, 100), (b, 110), (a, 120), (a, 130)):
            assert c.post("/auctions/a1/bids", headers=h, json={"amount": amt}).status_code == 200
        s = c.get("/auctions/a1").json()
        assert s["participants"] == 2 and s["bid_count"] == 4


def test_public_profile():
    with TestClient(create_app(db_path=":memory:")) as c:
        seller = signup(c, "seller")
        make(c, seller, "a1"); make(c, seller, "a2")
        p = c.get("/users/seller").json()
        assert p["username"] == "seller" and p["auctions_listed"] == 2 and p["member_since"] > 0
        assert "password_hash" not in p
        assert c.get("/users/ghost").status_code == 404


def test_me_bids_leading_outbid_and_requires_login():
    with TestClient(create_app(db_path=":memory:")) as c:
        assert c.get("/me/bids").status_code == 401
        seller, alice, bob = signup(c, "seller"), signup(c, "alice"), signup(c, "bob")
        make(c, seller, "a1", title="First"); make(c, seller, "a2", title="Second")
        c.post("/auctions/a1/bids", headers=alice, json={"amount": 100})
        c.post("/auctions/a2/bids", headers=alice, json={"amount": 100})
        c.post("/auctions/a2/bids", headers=bob, json={"amount": 110})          # alice outbid on a2 only

        rows = {r["auction_id"]: r for r in c.get("/me/bids", headers=alice).json()["bids"]}
        assert set(rows) == {"a1", "a2"}
        assert rows["a1"]["outcome"] == "leading" and rows["a2"]["outcome"] == "outbid"
        assert rows["a2"]["my_amount"] == 100 and rows["a2"]["current_price"] == 110
        assert c.get("/me/bids", headers=signup(c, "carol")).json()["bids"] == []     # only my own


def test_me_bids_won_and_lost_after_close():
    with TestClient(create_app(db_path=":memory:", sweep_interval=0.02)) as c:
        seller, alice, bob = signup(c, "seller"), signup(c, "alice"), signup(c, "bob")
        make(c, seller, "a1", duration_seconds=0.4, extension_window_seconds=0)
        c.post("/auctions/a1/bids", headers=alice, json={"amount": 100})
        c.post("/auctions/a1/bids", headers=bob, json={"amount": 110})
        import time
        time.sleep(0.8)
        assert c.get("/me/bids", headers=bob).json()["bids"][0]["outcome"] == "won"
        assert c.get("/me/bids", headers=alice).json()["bids"][0]["outcome"] == "lost"


def test_metadata_survives_restart(tmp_path):
    path = tmp_path / "a.db"
    clock = FakeClock()
    db = Database(str(path)); eng = AuctionEngine(clock=clock, store=db)
    eng.create_auction("a1", "seller", 100, 10, duration=300, title="Lamp", description="Nice",
                       image_url="https://x/y.png", category="Art")
    eng.place_bid("a1", "alice", 100); eng.place_bid("a1", "bob", 110)
    db.close()
    db2 = Database(str(path)); eng2 = AuctionEngine(clock=clock, store=db2); eng2.load_from_store()
    s = eng2.snapshot("a1")
    assert (s["description"], s["image_url"], s["category"], s["participants"]) == ("Nice", "https://x/y.png", "Art", 2)
    db2.close()


def test_old_database_is_upgraded_in_place(tmp_path):
    """A database created by the previous version (no listing columns) must keep working."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE auctions (id TEXT PRIMARY KEY, title TEXT NOT NULL, seller TEXT NOT NULL, start_price INTEGER NOT NULL,
        min_increment INTEGER NOT NULL, start_time REAL NOT NULL, end_time REAL NOT NULL, extension_window REAL NOT NULL,
        extension_amount REAL NOT NULL, extensions INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'OPEN',
        winner TEXT, winning_amount INTEGER, closed_at REAL, created_at REAL NOT NULL);
      INSERT INTO auctions VALUES ('old1','Old lamp','seller',100,10,0,9999999999,30,30,0,'OPEN',NULL,NULL,NULL,0);
    """)
    con.commit(); con.close()
    db = Database(str(path))                                   # migration runs here
    eng = AuctionEngine(store=db); assert eng.load_from_store() == 1
    s = eng.snapshot("old1")
    assert s["title"] == "Old lamp" and s["category"] == "Collectibles" and s["description"] == "" and s["image_url"] == ""
    assert eng.place_bid("old1", "alice", 100).accepted
    db.close()


def test_pages_are_served_and_login_is_the_landing_page():
    with TestClient(create_app(db_path=":memory:")) as c:
        root = c.get("/")
        assert root.status_code == 200 and "Sign in" in root.text and "BID" in root.text      # login first
        app = c.get("/app.html")
        assert app.status_code == 200 and "/js/app.js" in app.text
        for asset in ("/css/styles.css", "/js/api.js", "/js/ui.js", "/js/app.js"):
            assert c.get(asset).status_code == 200, asset
