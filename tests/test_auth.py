import time

import pytest
from fastapi.testclient import TestClient

from auction import auth
from auction.api import LOGIN_MAX_FAILURES, create_app


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)


@pytest.fixture
def client():
    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        c.app_ = app
        yield c


def reg(c, u="alice", p="password123"):
    return c.post("/auth/register", json={"username": u, "password": p})


def hdr(token):
    return {"Authorization": "Bearer " + token}


def test_password_hash_roundtrip_and_salting():
    h1, h2 = auth.hash_password("secret-pass"), auth.hash_password("secret-pass")
    assert h1 != h2                                         # random salt per hash
    assert auth.verify_password("secret-pass", h1) and not auth.verify_password("wrong", h1)
    assert "secret-pass" not in h1
    assert not auth.verify_password("x", "garbage")


def test_register_login_me_logout(client):
    r = reg(client)
    assert r.status_code == 201 and r.json()["user"]["username"] == "alice"
    token = r.json()["token"]
    assert client.get("/auth/me", headers=hdr(token)).json()["username"] == "alice"

    login = client.post("/auth/login", json={"username": "ALICE", "password": "password123"})   # case-insensitive
    assert login.status_code == 200 and login.json()["user"]["username"] == "alice"

    assert client.post("/auth/logout", headers=hdr(token)).status_code == 200
    assert client.get("/auth/me", headers=hdr(token)).status_code == 401      # token dead after logout
    assert client.get("/auth/me", headers=hdr(login.json()["token"])).status_code == 200   # other session unaffected


def test_register_validation(client):
    assert reg(client, "ab").status_code == 422                     # too short
    assert reg(client, "bad name!").status_code == 422              # illegal characters
    assert reg(client, "okname", "short").status_code == 422        # password too short
    assert reg(client, "carol").status_code == 201
    assert reg(client, "CAROL").status_code == 409                  # duplicate, case-insensitive


def test_wrong_password_and_unknown_user(client):
    reg(client)
    assert client.post("/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"username": "ghost", "password": "password123"}).status_code == 401


def test_login_lockout_after_repeated_failures(client):
    reg(client)
    for _ in range(LOGIN_MAX_FAILURES):
        assert client.post("/auth/login", json={"username": "alice", "password": "bad"}).status_code == 401
    assert client.post("/auth/login", json={"username": "alice", "password": "bad"}).status_code == 429
    assert client.post("/auth/login", json={"username": "alice", "password": "password123"}).status_code == 429


def test_protected_endpoints_need_login(client):
    assert client.post("/auctions", json={"id": "a", "start_price": 1, "duration_seconds": 10}).status_code == 401
    assert client.post("/auctions/a/bids", json={"amount": 5}).status_code == 401
    assert client.get("/me/summary").status_code == 401
    assert client.get("/auth/me", headers=hdr("not-a-real-token")).status_code == 401
    assert client.get("/auctions").status_code == 200               # browsing is public


def test_bidder_is_the_logged_in_user_not_the_request_body(client):
    seller = hdr(reg(client, "seller").json()["token"])
    mallory = hdr(reg(client, "mallory").json()["token"])
    client.post("/auctions", headers=seller, json={"id": "a", "start_price": 10, "duration_seconds": 100})
    r = client.post("/auctions/a/bids", headers=mallory, json={"amount": 10, "bidder": "someone_else"})
    assert r.status_code == 200
    assert client.get("/auctions/a").json()["leader"] == "mallory"   # spoofed name ignored


def test_seller_cannot_bid_on_own_auction(client):
    seller = hdr(reg(client, "seller").json()["token"])
    client.post("/auctions", headers=seller, json={"id": "a", "start_price": 10, "duration_seconds": 100})
    r = client.post("/auctions/a/bids", headers=seller, json={"amount": 10})
    assert r.status_code == 400 and r.json()["reason"] == "SELLER_CANNOT_BID"


def test_database_stores_only_hashes(client):
    token = reg(client, "dave", "super-secret-1").json()["token"]
    db = client.app_.state.ctx.db
    user = db.get_user_by_name("dave")
    assert "super-secret-1" not in user["password_hash"]
    rows = db._all("SELECT token_hash FROM sessions")
    assert rows and all(token != r["token_hash"] for r in rows)       # raw token never stored


def test_expired_session_is_rejected(client):
    token = reg(client).json()["token"]
    db = client.app_.state.ctx.db
    assert db.user_for_token(auth.hash_token(token)) is not None
    assert db.user_for_token(auth.hash_token(token), now=time.time() + auth.SESSION_TTL_SECONDS + 5) is None


def test_my_summary(client):
    seller = hdr(reg(client, "seller").json()["token"])
    alice = hdr(reg(client, "alice").json()["token"])
    client.post("/auctions", headers=seller, json={"id": "a", "title": "Lamp", "start_price": 10,
                                                   "min_increment": 5, "duration_seconds": 100})
    client.post("/auctions/a/bids", headers=alice, json={"amount": 10})
    client.post("/auctions/a/bids", headers=alice, json={"amount": 20})
    s = client.get("/me/summary", headers=alice).json()
    assert s["bids_placed"] == 2 and s["auctions_created"] == 0
    assert [b["amount"] for b in s["recent_bids"]] == [20, 10] and s["recent_bids"][0]["title"] == "Lamp"
    assert client.get("/me/summary", headers=seller).json()["auctions_created"] == 1
