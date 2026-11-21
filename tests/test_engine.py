import random
import threading

import pytest

from auction.engine import AuctionEngine, RejectReason, Status


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t
        self._l = threading.Lock()

    def __call__(self):
        with self._l:
            return self.t

    def advance(self, dt):
        with self._l:
            self.t += dt


@pytest.fixture
def env():
    clock = FakeClock()
    eng = AuctionEngine(clock=clock)
    eng.create_auction("a1", seller="seller", start_price=100, min_increment=10, duration=120)
    return eng, clock


def test_accepts_valid_and_rejects_low(env):
    eng, _ = env
    assert eng.place_bid("a1", "alice", 100).accepted
    r = eng.place_bid("a1", "bob", 105)           # below 100 + 10
    assert not r.accepted and r.reason is RejectReason.BID_TOO_LOW
    assert r.min_next_bid == 110
    assert eng.place_bid("a1", "bob", 110).accepted
    assert eng.snapshot("a1")["leader"] == "bob"


def test_first_bid_must_meet_start_price(env):
    eng, _ = env
    assert eng.place_bid("a1", "alice", 99).reason is RejectReason.BID_TOO_LOW


def test_invalid_inputs(env):
    eng, _ = env
    assert eng.place_bid("a1", "alice", -5).reason is RejectReason.INVALID_BID
    assert eng.place_bid("a1", "alice", 100.5).reason is RejectReason.INVALID_BID
    assert eng.place_bid("a1", "", 100).reason is RejectReason.INVALID_BID
    assert eng.place_bid("nope", "alice", 100).reason is RejectReason.AUCTION_NOT_FOUND
    assert eng.place_bid("a1", "seller", 100).reason is RejectReason.SELLER_CANNOT_BID


def test_no_extension_outside_window(env):
    eng, clock = env
    clock.advance(60)                              # 60s left, window is 30s
    r = eng.place_bid("a1", "alice", 100)
    assert r.accepted and not r.extended
    assert r.end_time == 1120.0


def test_extension_in_last_30_seconds(env):
    eng, clock = env
    clock.advance(95)                              # 25s left
    r = eng.place_bid("a1", "alice", 100)
    assert r.accepted and r.extended
    assert r.end_time == 1150.0                    # 1120 + 30
    clock.advance(10)                              # 40s left again -> outside window
    r2 = eng.place_bid("a1", "bob", 110)
    assert r2.accepted and not r2.extended


def test_boundary_exactly_30s_extends(env):
    eng, clock = env
    clock.advance(90)                              # exactly 30s left
    assert eng.place_bid("a1", "alice", 100).extended


def test_snipe_chain_keeps_auction_alive(env):
    eng, clock = env
    clock.advance(100)
    price = 100
    for i in range(5):                             # each bid lands in the extended window
        assert eng.place_bid("a1", f"u{i}", price).extended
        price += 10
        clock.advance(30)                          # matches the 30s extension, so remaining time stays in-window
    assert eng.snapshot("a1")["status"] == "OPEN"
    assert eng.snapshot("a1")["extensions"] == 5


def test_bid_at_or_after_deadline_rejected_and_winner_fixed(env):
    eng, clock = env
    eng.place_bid("a1", "alice", 100)
    clock.advance(120)                             # now == end_time
    r = eng.place_bid("a1", "bob", 500)
    assert not r.accepted and r.reason is RejectReason.AUCTION_CLOSED
    snap = eng.snapshot("a1")
    assert snap["status"] == "CLOSED"
    assert snap["winner"] == "alice" and snap["winning_amount"] == 100


def test_no_bids_means_no_winner(env):
    eng, clock = env
    clock.advance(500)
    assert eng.sweep() == 1
    snap = eng.snapshot("a1")
    assert snap["status"] == "CLOSED" and snap["winner"] is None


def test_closes_exactly_once_and_emits_once(env):
    eng, clock = env
    events = []
    eng.subscribe(events.append)
    eng.place_bid("a1", "alice", 100)
    clock.advance(1000)
    results = []
    threads = [threading.Thread(target=lambda: results.append(eng.close_due("a1"))) for _ in range(50)]
    threads += [threading.Thread(target=lambda: eng.place_bid("a1", "x", 999999)) for _ in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for e in events if e["type"] == "closed") == 1
    assert eng.snapshot("a1")["winner"] == "alice"


def test_idempotent_retry_does_not_double_apply(env):
    eng, _ = env
    r1 = eng.place_bid("a1", "alice", 100, idempotency_key="k1")
    r2 = eng.place_bid("a1", "alice", 100, idempotency_key="k1")
    assert r1.accepted and r2.accepted and r2.duplicate
    assert eng.get("a1").bid_count == 1


def test_same_amount_race_exactly_one_wins(env):
    eng, _ = env
    barrier = threading.Barrier(200)
    results = []

    def go(i):
        barrier.wait()
        results.append(eng.place_bid("a1", f"u{i}", 100))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(200)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(r.accepted for r in results) == 1
    assert eng.get("a1").bid_count == 1


def test_heavy_contention_invariants(env):
    """Many bidders, random amounts. Accepted bids must form a strictly increasing,
    gap-respecting sequence, and the winner must be the last accepted bid."""
    eng, clock = env
    n_threads, per_thread = 64, 100
    barrier = threading.Barrier(n_threads)
    rng_seeds = list(range(n_threads))

    def go(i):
        rng = random.Random(rng_seeds[i])
        barrier.wait()
        for _ in range(per_thread):
            eng.place_bid("a1", f"u{i}", rng.randint(1, 20000))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(n_threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    a = eng.get("a1")
    hist = a.history
    assert hist[0].amount >= a.start_price
    for prev, cur in zip(hist, hist[1:]):
        assert cur.amount >= prev.amount + a.min_increment
        assert cur.seq == prev.seq + 1
    assert [b.seq for b in hist] == list(range(1, len(hist) + 1))

    clock.advance(10_000)
    eng.sweep()
    snap = eng.snapshot("a1")
    assert snap["winner"] == hist[-1].bidder
    assert snap["winning_amount"] == hist[-1].amount == max(b.amount for b in hist)


def test_final_seconds_storm_with_extensions(env):
    """Bids hammer the closing window while time advances. After close nothing is accepted,
    and every accepted bid was placed strictly before the (possibly extended) end."""
    eng, clock = env
    clock.advance(100)
    stop = threading.Event()
    accepted = []
    lock = threading.Lock()
    counter = [100]

    def bidder(i):
        while not stop.is_set():
            with lock:
                counter[0] += 10
                amt = counter[0]
            r = eng.place_bid("a1", f"u{i}", amt)
            if r.accepted:
                with lock:
                    accepted.append(r.bid)

    ts = [threading.Thread(target=bidder, args=(i,)) for i in range(16)]
    [t.start() for t in ts]
    for _ in range(200):
        clock.advance(1.0)                         # time marches; bids keep extending
        if eng.snapshot("a1")["status"] == "CLOSED":
            break
    stop.set()
    [t.join() for t in ts]

    a = eng.get("a1")
    for b in a.history:
        assert b.at < a.end_time
    final_winner = a.winner
    assert a.status is Status.CLOSED
    assert final_winner == a.history[-1].bidder
    before = a.bid_count
    assert not eng.place_bid("a1", "late", 10**9).accepted
    assert a.bid_count == before
