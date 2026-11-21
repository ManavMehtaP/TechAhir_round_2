import pytest
from fastapi.testclient import TestClient

from auction import auth
from auction.api import create_app


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)        # keep tests quick; production uses 240k


def signup(c, name):
    r = c.post("/auth/register", json={"username": name, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def new_app(**kw):
    return create_app(db_path=":memory:", **kw)


def test_http_flow_and_websocket():
    with TestClient(new_app()) as c:
        seller, alice, bob = signup(c, "seller"), signup(c, "alice"), signup(c, "bob")
        r = c.post("/auctions", headers=seller, json={"id": "x", "seller": "ignored", "start_price": 100,
                                                      "min_increment": 10, "duration_seconds": 100})
        assert r.status_code == 201 and r.json()["seller"] == "seller"     # seller comes from the login
        assert c.post("/auctions", headers=seller, json={"id": "x", "start_price": 1,
                                                         "duration_seconds": 5}).status_code == 409

        with c.websocket_connect("/auctions/x/ws") as ws:                  # watching needs no login
            first = ws.receive_json()
            assert first["type"] == "snapshot" and first["current_price"] == 100

            ok = c.post("/auctions/x/bids", headers=alice, json={"amount": 120})
            assert ok.status_code == 200 and ok.json()["accepted"]
            ev = ws.receive_json()
            assert ev["type"] == "bid" and ev["current_price"] == 120 and ev["leader"] == "alice"

        low = c.post("/auctions/x/bids", headers=bob, json={"amount": 121})
        assert low.status_code == 409 and low.json()["reason"] == "BID_TOO_LOW"
        assert c.get("/auctions/x").json()["current_price"] == 120
        assert c.get("/auctions/nope").status_code == 404
        assert c.post("/auctions/nope/bids", headers=bob, json={"amount": 5}).status_code == 404


def test_websocket_receives_close_event():
    with TestClient(new_app(sweep_interval=0.02)) as c:
        seller, alice = signup(c, "seller"), signup(c, "alice")
        c.post("/auctions", headers=seller, json={"id": "y", "start_price": 10, "duration_seconds": 0.3,
                                                  "extension_window_seconds": 0})
        with c.websocket_connect("/auctions/y/ws") as ws:
            ws.receive_json()                                              # snapshot
            c.post("/auctions/y/bids", headers=alice, json={"amount": 10})
            assert ws.receive_json()["type"] == "bid"
            end = ws.receive_json()
            assert end["type"] == "closed" and end["winner"] == "alice"


def test_list_history_and_ui():
    with TestClient(new_app()) as c:
        seller, alice, bob = signup(c, "seller"), signup(c, "alice"), signup(c, "bob")
        c.post("/auctions", headers=seller, json={"id": "l1", "title": "Watch", "start_price": 10,
                                                  "min_increment": 5, "duration_seconds": 100})
        c.post("/auctions/l1/bids", headers=alice, json={"amount": 10})
        c.post("/auctions/l1/bids", headers=bob, json={"amount": 15})

        lst = c.get("/auctions").json()
        assert lst["auctions"][0]["title"] == "Watch" and lst["auctions"][0]["current_price"] == 15
        assert lst["auctions"][0]["min_increment"] == 5

        hist = c.get("/auctions/l1/bids").json()["bids"]
        assert [b["bidder"] for b in hist] == ["bob", "alice"]              # newest first
        assert c.get("/auctions/zzz/bids").status_code == 404

        assert c.get("/").status_code == 200 and "Sign in" in c.get("/").text   # login page is the landing page
        assert c.get("/app.html").status_code == 200                            # marketplace app served
        assert c.get("/auctions/l1").json()["title"] == "Watch"             # API not shadowed by UI mount
