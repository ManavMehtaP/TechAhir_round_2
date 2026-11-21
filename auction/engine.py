"""Core bidding engine.

Correctness model
-----------------
* Every auction owns its own lock. All state changes for one auction are
  serialized through it, so bids are totally ordered and "highest bid wins,
  earliest accepted bid breaks ties" is decided in exactly one place.
* Different auctions never contend with each other (50k auctions -> 50k
  independent locks / state machines).
* Time is read *inside* the lock, from an injectable clock, so the accept /
  reject / extend decision is made against one consistent instant.
* Closing is a one-way state transition (OPEN -> CLOSED) performed under the
  lock, and it records the winner at that moment. A bid that arrives after
  end_time is rejected even if the background sweeper hasn't run yet, and the
  first caller (bid or sweeper) to notice the deadline closes the auction.
* Bids carry an optional idempotency key so client retries never double-apply.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Status(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class RejectReason(str, Enum):
    AUCTION_NOT_FOUND = "AUCTION_NOT_FOUND"
    AUCTION_CLOSED = "AUCTION_CLOSED"
    NOT_STARTED = "NOT_STARTED"
    BID_TOO_LOW = "BID_TOO_LOW"
    SELLER_CANNOT_BID = "SELLER_CANNOT_BID"
    INVALID_BID = "INVALID_BID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class Bid:
    seq: int            # position in this auction's total order of accepted bids
    bidder: str
    amount: int         # minor units (e.g. paise/cents) - never floats for money
    at: float           # server time the bid was accepted


@dataclass
class BidResult:
    accepted: bool
    reason: Optional[RejectReason] = None
    message: str = ""
    bid: Optional[Bid] = None
    extended: bool = False
    end_time: Optional[float] = None
    current_price: Optional[int] = None
    min_next_bid: Optional[int] = None
    duplicate: bool = False


@dataclass
class Auction:
    id: str
    seller: str
    start_price: int
    min_increment: int
    start_time: float
    end_time: float
    extension_window: float = 30.0     # bid in the last N seconds ...
    extension_amount: float = 30.0     # ... pushes the end out by N seconds
    title: str = ""
    status: Status = Status.OPEN
    highest: Optional[Bid] = None
    bid_count: int = 0
    extensions: int = 0
    winner: Optional[str] = None
    winning_amount: Optional[int] = None
    closed_at: Optional[float] = None
    version: int = 0                   # bumps on every state change; watchers use it to drop stale updates
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    history: list = field(default_factory=list, repr=False)
    _idem: dict = field(default_factory=dict, repr=False)

    # ---- read model -------------------------------------------------
    def current_price(self) -> int:
        return self.highest.amount if self.highest else self.start_price

    def min_next_bid(self) -> int:
        return self.start_price if not self.highest else self.highest.amount + self.min_increment

    def snapshot(self) -> dict:
        return {
            "auction_id": self.id,
            "title": self.title or self.id,
            "seller": self.seller,
            "status": self.status.value,
            "current_price": self.current_price(),
            "min_next_bid": self.min_next_bid(),
            "min_increment": self.min_increment,
            "leader": self.highest.bidder if self.highest else None,
            "bid_count": self.bid_count,
            "end_time": self.end_time,
            "extensions": self.extensions,
            "winner": self.winner,
            "winning_amount": self.winning_amount,
            "version": self.version,
        }


Listener = Callable[[dict], None]


class AuctionEngine:
    def __init__(self, clock: Callable[[], float] = time.time, store=None):
        self._clock = clock
        self._store = store                       # optional durable store (see db.Database)
        self._auctions: dict[str, Auction] = {}
        self._registry_lock = threading.Lock()   # only guards the dict, never held during bidding
        self._listeners: list[Listener] = []

    # ---- persistence ------------------------------------------------
    def attach_store(self, store) -> None:
        self._store = store

    def load_from_store(self) -> int:
        """Rebuild in-memory state from the database after a restart. Returns auctions loaded."""
        if self._store is None:
            return 0
        rows, bids_by_auction = self._store.load_all()
        n = 0
        for r in rows:
            hist = [Bid(seq=b["seq"], bidder=b["bidder"], amount=b["amount"], at=b["at"])
                    for b in bids_by_auction.get(r["id"], [])]
            closed = r["status"] == "CLOSED"
            a = Auction(id=r["id"], seller=r["seller"], start_price=r["start_price"],
                        min_increment=r["min_increment"], start_time=r["start_time"], end_time=r["end_time"],
                        extension_window=r["extension_window"], extension_amount=r["extension_amount"],
                        title=r["title"], status=Status(r["status"]),
                        highest=hist[-1] if hist else None,     # amounts strictly increase, so last == highest
                        bid_count=len(hist), extensions=r["extensions"], winner=r["winner"],
                        winning_amount=r["winning_amount"], closed_at=r["closed_at"],
                        version=len(hist) + (1 if closed else 0), history=hist)
            self._auctions[a.id] = a
            n += 1
        return n

    # ---- wiring -----------------------------------------------------
    def subscribe(self, fn: Listener) -> None:
        """Register a callback invoked (outside any lock) with each state-change event."""
        self._listeners.append(fn)

    def _emit(self, event: dict) -> None:
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass  # a bad subscriber must never affect bidding

    # ---- auctions ---------------------------------------------------
    def create_auction(self, auction_id: str, seller: str, start_price: int, min_increment: int,
                       duration: float, start_time: Optional[float] = None,
                       extension_window: float = 30.0, extension_amount: float = 30.0,
                       title: str = "") -> Auction:
        if start_price < 0 or min_increment <= 0 or duration <= 0:
            raise ValueError("start_price>=0, min_increment>0, duration>0 required")
        now = self._clock()
        start = now if start_time is None else start_time
        a = Auction(id=auction_id, seller=seller, start_price=start_price, min_increment=min_increment,
                    start_time=start, end_time=start + duration,
                    extension_window=extension_window, extension_amount=extension_amount,
                    title=title)
        with self._registry_lock:
            if auction_id in self._auctions:
                raise ValueError(f"auction {auction_id} already exists")
            if self._store is not None:
                self._store.insert_auction({
                    "id": a.id, "title": a.title, "seller": a.seller, "start_price": a.start_price,
                    "min_increment": a.min_increment, "start_time": a.start_time, "end_time": a.end_time,
                    "extension_window": a.extension_window, "extension_amount": a.extension_amount})
            self._auctions[auction_id] = a
        return a

    def get(self, auction_id: str) -> Optional[Auction]:
        return self._auctions.get(auction_id)

    def snapshot(self, auction_id: str) -> Optional[dict]:
        a = self.get(auction_id)
        if a is None:
            return None
        with a.lock:
            self._close_if_due_locked(a, self._clock())
            return a.snapshot()

    def list_snapshots(self) -> list[dict]:
        out = []
        for aid in list(self._auctions):
            s = self.snapshot(aid)
            if s:
                out.append(s)
        return out

    def recent_bids(self, auction_id: str, n: int = 25) -> Optional[list[dict]]:
        a = self.get(auction_id)
        if a is None:
            return None
        with a.lock:
            return [{"seq": b.seq, "bidder": b.bidder, "amount": b.amount, "at": b.at}
                    for b in reversed(a.history[-n:])]

    # ---- the hot path -----------------------------------------------
    def place_bid(self, auction_id: str, bidder: str, amount: int,
                  idempotency_key: Optional[str] = None) -> BidResult:
        a = self._auctions.get(auction_id)
        if a is None:
            return BidResult(False, RejectReason.AUCTION_NOT_FOUND, "no such auction")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0 or not bidder:
            return BidResult(False, RejectReason.INVALID_BID, "amount must be a positive integer and bidder is required")

        events: list[dict] = []
        with a.lock:
            now = self._clock()            # one consistent instant for the whole decision

            if idempotency_key is not None and (bidder, idempotency_key) in a._idem:
                prev = a._idem[(bidder, idempotency_key)]
                return BidResult(**{**prev.__dict__, "duplicate": True})

            closed_now = self._close_if_due_locked(a, now)
            if closed_now:
                events.append(self._event("closed", a))

            result = self._decide_locked(a, bidder, amount, now, events)
            if idempotency_key is not None:
                a._idem[(bidder, idempotency_key)] = result

        for e in events:                   # broadcast outside the lock
            self._emit(e)
        return result

    def _decide_locked(self, a: Auction, bidder: str, amount: int, now: float, events: list) -> BidResult:
        def reject(reason: RejectReason, msg: str) -> BidResult:
            return BidResult(False, reason, msg, end_time=a.end_time,
                             current_price=a.current_price(), min_next_bid=a.min_next_bid())

        # The time check is independent of status: even if closing could not be persisted
        # yet, a bid at/after the deadline is never accepted.
        if a.status is Status.CLOSED or now >= a.end_time:
            return reject(RejectReason.AUCTION_CLOSED, "auction has ended")
        if now < a.start_time:
            return reject(RejectReason.NOT_STARTED, "auction has not started")
        if bidder == a.seller:
            return reject(RejectReason.SELLER_CANNOT_BID, "seller cannot bid on own auction")
        if amount < a.min_next_bid():
            return reject(RejectReason.BID_TOO_LOW, f"minimum acceptable bid is {a.min_next_bid()}")

        # accepted: decide, make it durable, THEN change memory -------
        seq = a.bid_count + 1
        extended = a.end_time - now <= a.extension_window
        new_end = a.end_time + a.extension_amount if extended else a.end_time
        if self._store is not None:
            try:
                self._store.record_bid(a.id, seq, bidder, amount, now, extended, new_end,
                                       a.extensions + (1 if extended else 0))
            except Exception:
                return reject(RejectReason.INTERNAL_ERROR, "could not record the bid, please retry")

        a.bid_count = seq
        bid = Bid(seq=seq, bidder=bidder, amount=amount, at=now)
        a.highest = bid
        a.history.append(bid)
        if extended:
            a.end_time = new_end
            a.extensions += 1
        a.version += 1
        events.append(self._event("bid", a, extended=extended))
        return BidResult(True, bid=bid, extended=extended, end_time=a.end_time,
                         current_price=a.current_price(), min_next_bid=a.min_next_bid())

    # ---- closing ----------------------------------------------------
    def _close_if_due_locked(self, a: Auction, now: float) -> bool:
        """Idempotent. Returns True only for the single call that performs the transition."""
        if a.status is Status.OPEN and now >= a.end_time:
            winner = a.highest.bidder if a.highest else None
            amount = a.highest.amount if a.highest else None
            if self._store is not None:
                try:
                    self._store.record_close(a.id, a.end_time, winner, amount)
                except Exception:
                    return False          # retried by the next sweep; bids are already refused by the time check
            a.status = Status.CLOSED
            a.closed_at = a.end_time
            a.winner, a.winning_amount = winner, amount
            a.version += 1
            return True
        return False

    def close_due(self, auction_id: str) -> bool:
        a = self._auctions.get(auction_id)
        if a is None:
            return False
        with a.lock:
            did = self._close_if_due_locked(a, self._clock())
            ev = self._event("closed", a) if did else None
        if ev:
            self._emit(ev)
        return did

    def sweep(self) -> int:
        """Close every auction whose deadline has passed. Cheap enough to run every ~100ms
        for the open set; at scale this is replaced by a deadline-ordered heap / timer wheel."""
        n = 0
        for aid in list(self._auctions):
            a = self._auctions[aid]
            if a.status is Status.OPEN and self._clock() >= a.end_time:
                n += self.close_due(aid)
        return n

    def _event(self, kind: str, a: Auction, **extra) -> dict:
        return {"type": kind, **a.snapshot(), **extra}
