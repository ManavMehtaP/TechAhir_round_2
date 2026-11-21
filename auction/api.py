"""HTTP + WebSocket API (also serves the web UI from /frontend).

Accounts (public)
  POST /auth/register                  create account, returns a session token
  POST /auth/login                     returns a session token
Accounts (need  Authorization: Bearer <token>)
  POST /auth/logout
  GET  /auth/me
  GET  /me/summary                     my bids, wins and auctions created

Auctions
  GET  /auctions                       list (public)
  GET  /auctions/{id}                  current state (public)
  GET  /auctions/{id}/bids             recent accepted bids (public)
  WS   /auctions/{id}/ws               live price stream (public)
  POST /auctions                       create     (login required; you become the seller)
  POST /auctions/{id}/bids             place bid  (login required; you are the bidder)

Fan-out: the engine emits events; the hub keeps ONE size-1 slot per watcher and
coalesces (drops stale intermediate prices, keeps only the latest) so a slow
client can never slow the bidding path or grow memory without bound.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from .db import Database, UserExists
from .engine import AuctionEngine, RejectReason

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_SECONDS = 300


class Register(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CreateAuction(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=120)
    start_price: int = Field(ge=0)
    min_increment: int = Field(gt=0, default=1)
    duration_seconds: float = Field(gt=0)
    extension_window_seconds: float = 30.0
    extension_amount_seconds: float = 30.0


class PlaceBid(BaseModel):
    amount: int
    idempotency_key: Optional[str] = None


class Hub:
    """Per-auction watcher registry. Each watcher holds a size-1 'latest state' slot."""

    def __init__(self):
        self.watchers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def on_event(self, event: dict) -> None:           # called from engine threads
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: dict) -> None:
        for q in self.watchers.get(event["auction_id"], ()):
            if q.full():
                try:
                    q.get_nowait()                      # drop stale, keep newest
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def create_app(engine: Optional[AuctionEngine] = None, db_path: Optional[str] = None,
               sweep_interval: float = 0.1) -> FastAPI:
    engine = engine or AuctionEngine()
    path = db_path or os.environ.get("AUCTION_DB", "auction.db")
    ctx = SimpleNamespace(db=None)                       # opened at startup, not at import time
    hub = Hub()
    engine.subscribe(hub.on_event)
    fails: dict[str, list[float]] = {}                   # username(lower) -> recent failed-login times
    fails_lock = threading.Lock()
    dummy_hash = auth.hash_password("not-a-real-password")   # equalizes timing for unknown usernames

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.db = Database(path)
        engine.attach_store(ctx.db)
        engine.load_from_store()                         # resume auctions exactly where they were
        hub.loop = asyncio.get_running_loop()

        async def sweeper():
            while True:
                await asyncio.sleep(sweep_interval)
                engine.sweep()

        task = asyncio.create_task(sweeper())
        try:
            yield
        finally:
            task.cancel()
            ctx.db.close()

    app = FastAPI(title="Live Auction Platform", lifespan=lifespan)
    app.state.engine = engine
    app.state.ctx = ctx

    # ---- auth helpers -----------------------------------------------
    def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
        token = _bearer(authorization)
        user = ctx.db.user_for_token(auth.hash_token(token)) if token else None
        if not user:
            raise HTTPException(401, "login required")
        return user

    def issue_token(user_id: int) -> str:
        token = auth.new_token()
        ctx.db.create_session(auth.hash_token(token), user_id, time.time() + auth.SESSION_TTL_SECONDS)
        return token

    # ---- accounts ---------------------------------------------------
    @app.post("/auth/register", status_code=201)
    def register(req: Register):
        if not auth.USERNAME_RE.match(req.username):
            raise HTTPException(422, "username must be 3-32 characters: letters, digits, underscore")
        try:
            user = ctx.db.create_user(req.username, auth.hash_password(req.password))
        except UserExists:
            raise HTTPException(409, "that username is taken")
        return {"token": issue_token(user["id"]), "user": {"username": user["username"]}}

    @app.post("/auth/login")
    def login(req: Login):
        key = req.username.lower()
        now = time.time()
        with fails_lock:
            recent = [t for t in fails.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
            fails[key] = recent
            if len(recent) >= LOGIN_MAX_FAILURES:
                raise HTTPException(429, "too many failed attempts, try again in a few minutes")
        user = ctx.db.get_user_by_name(req.username)
        ok = auth.verify_password(req.password, user["password_hash"] if user else dummy_hash)
        if not (user and ok):
            with fails_lock:
                fails.setdefault(key, []).append(now)
            raise HTTPException(401, "invalid username or password")
        with fails_lock:
            fails.pop(key, None)
        return {"token": issue_token(user["id"]), "user": {"username": user["username"]}}

    @app.post("/auth/logout")
    def logout(authorization: Optional[str] = Header(default=None), user: dict = Depends(current_user)):
        ctx.db.delete_session(auth.hash_token(_bearer(authorization)))
        return {"ok": True}

    @app.get("/auth/me")
    def me(user: dict = Depends(current_user)):
        return {"username": user["username"], "member_since": user["created_at"]}

    @app.get("/me/summary")
    def my_summary(user: dict = Depends(current_user)):
        return ctx.db.user_summary(user["username"])

    # ---- auctions ---------------------------------------------------
    @app.get("/auctions")
    def list_auctions():
        items = engine.list_snapshots()
        items.sort(key=lambda s: (s["status"] != "OPEN", s["end_time"] if s["status"] == "OPEN" else -s["end_time"]))
        return {"server_time": time.time(), "auctions": items}

    @app.post("/auctions", status_code=201)
    def create(req: CreateAuction, user: dict = Depends(current_user)):
        try:
            a = engine.create_auction(req.id, user["username"], req.start_price, req.min_increment,
                                      req.duration_seconds,
                                      extension_window=req.extension_window_seconds,
                                      extension_amount=req.extension_amount_seconds,
                                      title=req.title)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return a.snapshot()

    @app.get("/auctions/{auction_id}")
    def state(auction_id: str):
        snap = engine.snapshot(auction_id)
        if snap is None:
            raise HTTPException(404, "no such auction")
        snap["server_time"] = time.time()
        return snap

    @app.get("/auctions/{auction_id}/bids")
    def bids(auction_id: str, limit: int = 25):
        rows = engine.recent_bids(auction_id, max(1, min(limit, 200)))
        if rows is None:
            raise HTTPException(404, "no such auction")
        return {"bids": rows}

    @app.post("/auctions/{auction_id}/bids")
    def bid(auction_id: str, req: PlaceBid, user: dict = Depends(current_user)):
        # The bidder is the logged-in account, never a name supplied in the request body.
        r = engine.place_bid(auction_id, user["username"], req.amount, req.idempotency_key)
        if r.reason is RejectReason.AUCTION_NOT_FOUND:
            raise HTTPException(404, r.message)
        body = {
            "accepted": r.accepted,
            "reason": r.reason.value if r.reason else None,
            "message": r.message,
            "extended": r.extended,
            "end_time": r.end_time,
            "current_price": r.current_price,
            "min_next_bid": r.min_next_bid,
            "bid_seq": r.bid.seq if r.bid else None,
            "duplicate": r.duplicate,
        }
        if not r.accepted:
            if r.reason is RejectReason.INTERNAL_ERROR:
                code = 503
            elif r.reason in (RejectReason.BID_TOO_LOW, RejectReason.AUCTION_CLOSED):
                code = 409
            else:
                code = 400
            return JSONResponse(body, status_code=code)
        return body

    @app.websocket("/auctions/{auction_id}/ws")
    async def watch(ws: WebSocket, auction_id: str):
        snap = engine.snapshot(auction_id)
        if snap is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        hub.watchers[auction_id].add(q)
        try:
            await ws.send_json({"type": "snapshot", **snap, "server_time": time.time()})
            if snap["status"] == "CLOSED":
                return
            while True:
                ev = await q.get()
                await ws.send_json(ev)
                if ev["type"] == "closed":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            hub.watchers[auction_id].discard(q)
            try:
                await ws.close()
            except Exception:
                pass

    # Web UI last, so it never shadows the API routes.
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
