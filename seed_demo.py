"""Fill a RUNNING server with demo auctions so the marketplace looks alive.

    1. start the server:   uvicorn auction.api:app --reload
    2. in another terminal: python seed_demo.py            (or: python seed_demo.py http://127.0.0.1:8000)

It creates two demo accounts (demo_seller and demo_bidder, password below), about a dozen
auctions with product photos, and a few bids so history and participant counts have content.
Running it again just adds another batch. Demo data only: don't use this password anywhere real.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
PASSWORD = "demo-password-123"


def img(photo_id):
    return f"https://images.unsplash.com/photo-{photo_id}?auto=format&fit=crop&w=1200&q=80"


# (title, category, start_price, increment, minutes, photo, description)
ITEMS = [
    ("Vintage Automatic Dive Watch", "Watches", 24000, 1000, 6, img("1523170335258-f5ed11844a49"),
     "Automatic movement, stainless steel case, 200 m water resistance. Serviced last year and keeping excellent time. Comes with a spare leather strap."),
    ("Heritage Chronograph, Steel Bracelet", "Watches", 38000, 2000, 95, img("1524592094714-0f0654e20314"),
     "Classic chronograph with a champagne dial. Light wear on the clasp, crystal is flawless."),
    ("Limited Edition Red Runners, Size 9", "Sneakers", 9000, 500, 9, img("1542291026-7eec264c27ff"),
     "Deadstock pair from a limited release. Original box and receipt included."),
    ("Street Classic Sneakers, Collector Pair", "Sneakers", 6500, 500, 150, img("1549298916-b41d501d3772"),
     "Worn twice, cleaned and stored. A reliable everyday grail."),
    ("Studio Noise-Cancelling Headphones", "Electronics", 7000, 250, 4, img("1505740420928-5e560c06d30e"),
     "Over-ear wireless headphones with 30-hour battery. Includes case and cable."),
    ("Slim 14-inch Ultrabook, 16 GB RAM", "Electronics", 42000, 1500, 240, img("1496181133206-80ce9b88a853"),
     "Lightly used, battery health excellent. Fresh OS install. Original charger included."),
    ("Mirrorless Camera Body + Prime Lens", "Electronics", 31000, 1000, 70, img("1516035069371-29a1b244cc32"),
     "Low shutter count, sensor recently cleaned. Two batteries and a charger."),
    ("Abstract Oil on Canvas, 'Golden Hour'", "Art", 15000, 1000, 180, img("1541961017774-22349e4a1262"),
     "Original oil painting, 90 x 70 cm, signed on the reverse. Ships ready to hang."),
    ("Contemporary Gallery Print, Framed", "Art", 5500, 250, 55, img("1579783902614-a3fb3927b6a5"),
     "Numbered print 12/50, museum glass, archival mount."),
    ("Vintage Film Camera Collection", "Collectibles", 8000, 500, 120, img("1495121605193-b116b5b9c5fe"),
     "Three working film cameras with straps and a leather case. A great starter collection."),
    ("1990s Sports Coupe, Restored", "Vehicles", 850000, 10000, 600, img("1503376780353-7e6692767b70"),
     "Fully restored, matching numbers, service history available. Viewing by appointment."),
    ("Classic Cafe Racer Motorcycle", "Vehicles", 240000, 5000, 360, img("1558981806-ec527fa84c39"),
     "Custom build on a reliable donor bike. New tyres and chain, runs beautifully."),
]


def call(method, path, data=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(data).encode() if data is not None else None)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {BASE}. Start the server first: uvicorn auction.api:app --reload\n({e.reason})")


def login(username):
    code, body = call("POST", "/auth/register", {"username": username, "password": PASSWORD})
    if code == 409:                                   # already exists -> sign in
        code, body = call("POST", "/auth/login", {"username": username, "password": PASSWORD})
    if code not in (200, 201):
        sys.exit(f"Could not sign in as {username}: {body}")
    return body["token"]


def main():
    seller, bidder = login("demo_seller"), login("demo_bidder")
    made = []
    for n, (title, cat, price, inc, minutes, photo, desc) in enumerate(ITEMS):
        aid = f"demo-{n}-{abs(hash(title)) % 10000}"
        code, body = call("POST", "/auctions", {
            "id": aid, "title": title, "category": cat, "start_price": price, "min_increment": inc,
            "duration_seconds": minutes * 60, "image_url": photo, "description": desc}, seller)
        if code != 201:
            print("skip:", title, body)
            continue
        made.append((aid, price, inc))
    # a few bids so history, participants and "ending soon" have something to show
    for i, (aid, price, inc) in enumerate(made[:6]):
        call("POST", f"/auctions/{aid}/bids", {"amount": price}, bidder)
        if i % 2 == 0:
            call("POST", f"/auctions/{aid}/bids", {"amount": price + inc * 2}, bidder)
    print(f"Created {len(made)} demo auctions at {BASE}")
    print("Demo accounts: demo_seller / demo_bidder   password:", PASSWORD)


if __name__ == "__main__":
    main()
