# BIDZONE: Live Auction Platform

A live auction marketplace with accounts. Bidders sign up and log in, every bid is accepted or rejected by the server, a bid in the last 30 seconds extends the auction by 30 seconds, many simultaneous bids stay correct, and each auction ends with exactly one winner. Everything important is stored in a database, so restarts lose nothing. The web UI is a dark charcoal and gold marketplace with live prices over WebSocket.

## Run it

```bash
pip install -r requirements.txt
pytest -q                              # 44 tests
uvicorn auction.api:app --reload       # then open http://127.0.0.1:8000
```

The **login page is always the first page** at `http://127.0.0.1:8000`. Create an account, and you are taken into the marketplace. (If you are already signed in you will see a "Continue to BIDZONE" button.) Open the address in the browser; do not double-click the HTML files, because the pages need the server.

**Want it to look alive straight away?** With the server running, open a second terminal in the same folder and run:

```bash
python seed_demo.py
```

This adds about a dozen demo auctions with product photos, descriptions and categories, plus a few bids. It creates two demo accounts, `demo_seller` and `demo_bidder` (password `demo-password-123`, demo only). The product photos are loaded from Unsplash by URL, so they need an internet connection; if a photo fails to load the page shows a built-in category illustration instead.

To see live bidding between two people, open a second browser window (or a private window) and sign up as a second user.

## The website

| Page | What it does |
|---|---|
| **Login** (`/`) | sign in or create an account |
| **Home** | hero ("Bid. Win. Own Something Extraordinary."), featured live auctions, categories, ending soon |
| **Live Auctions** | all auctions with search, category filters, sorting and a "show ended" toggle |
| **Categories** | Electronics, Watches, Collectibles, Art, Sneakers, Vehicles, each linking to a filtered list |
| **Auction page** | large image, description, current and minimum next bid, countdown, participants, bid history, seller details, bid box |
| **My Bids** | auctions you are bidding on with your latest bid, current highest bid, Leading / Outbid status and countdown; plus Won and Lost / ended |
| **Sell** (`+ Sell`) | create an auction: title, description, category, start price, minimum increment, end date/time, image URL |
| **Navbar** | search, notifications bell (outbid / won / lost alerts), avatar menu, mobile menu |

The frontend never decides whether a bid is valid. It sends the bid to the server, shows whatever the server answers (accepted or rejected, with the reason), and updates the price from the live WebSocket stream. The only checks done before sending are simple input guards (empty, not a whole number, below the minimum next bid the server reported) to save a round trip.

## Database setup

**There is nothing to install or configure.** The project uses SQLite, which ships with Python. On first start the file `auction.db` is created in the folder you ran `uvicorn` from, with all tables. A database created by an earlier version is upgraded automatically on start.

| Need | How |
|---|---|
| Use a different file | `AUCTION_DB=/path/to/data.db uvicorn auction.api:app` |
| Look at the data | `sqlite3 auction.db` then `.tables`, `SELECT * FROM bids;` (or open the file in the free "DB Browser for SQLite" app) |
| Reset everything | stop the server and delete `auction.db`, `auction.db-wal`, `auction.db-shm` |
| Back it up | stop the server and copy `auction.db` |

Tables:

- `users`: username (unique, case-insensitive), salted password hash, created time
- `sessions`: hashed login tokens with an expiry (7 days)
- `auctions`: title, description, image URL, category, seller, prices, current end time, extension count, status, winner, winning amount
- `bids`: every accepted bid, numbered 1, 2, 3... per auction, with bidder, amount and time

Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a random salt, and login tokens are stored only as SHA-256 hashes, so a leaked database cannot be used to sign in. Repeated failed logins for one username are rate limited.

### How bidding and the database fit together

Auction state is kept in memory for speed (one lock per auction), and **the database is written before a bid is acknowledged**: the bid and the new end time are saved in one transaction, and only then does the bid count. If the write fails, the bid is rejected (`INTERNAL_ERROR`, HTTP 503) and nothing changes, so a bidder is never told "accepted" for a bid that was lost. On startup the server reloads all auctions and bids from the database. An auction whose deadline passed while the server was down is closed on startup with the correct winner. The listing fields (description, image, category) are display data only and are never read by the bidding logic.

## Project layout

```
auction/engine.py       bidding core: state machine, locking, anti-sniping, closing, restore
auction/db.py           SQLite storage (accounts, sessions, auctions, bids)
auction/auth.py         password hashing and session tokens
auction/api.py          FastAPI: accounts, auctions, WebSocket, serves the UI
frontend/index.html     login / create account page (landing page)
frontend/app.html       marketplace shell
frontend/css/styles.css shared theme
frontend/js/api.js      API calls, server clock, WebSocket with reconnect
frontend/js/ui.js       reusable components (cards, countdowns, toasts, state blocks)
frontend/js/app.js      navbar, notifications, router and the pages
seed_demo.py            fills a running server with demo auctions
tests/                  engine concurrency, API/WebSocket, auth, persistence, marketplace
```

## API

Browsing is public. Creating auctions, bidding and the account endpoints need `Authorization: Bearer <token>` (the UI handles this).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | no | create account, returns token |
| POST | `/auth/login` | no | returns token |
| POST | `/auth/logout` | yes | end this session |
| GET | `/auth/me` | yes | who am I |
| GET | `/me/summary` | yes | my bids, wins, auctions created |
| GET | `/me/bids` | yes | auctions I bid on: my latest bid + live state + outcome (`leading`, `outbid`, `won`, `lost`) |
| GET | `/users/{username}` | no | public seller profile (member since, auctions listed) |
| GET | `/auctions` | no | list auctions (open first) |
| POST | `/auctions` | yes | create: `title, description, category, image_url, start_price, min_increment, duration_seconds` (you become the seller) |
| GET | `/auctions/{id}` | no | current state, including `participants` |
| GET | `/auctions/{id}/bids` | no | recent accepted bids, newest first |
| POST | `/auctions/{id}/bids` | yes | place bid `{amount, idempotency_key?}` (you are the bidder) |
| WS | `/auctions/{id}/ws` | no | live price stream |

The bidder is always the logged-in account; a name in the request body is ignored. Rejections return a reason: `BID_TOO_LOW`, `AUCTION_CLOSED` (HTTP 409), `INVALID_BID`, `SELLER_CANNOT_BID`, `NOT_STARTED` (400), `INTERNAL_ERROR` (503). Amounts are whole numbers (shown as ₹), never floats. Categories are restricted to the six above, and `image_url` must be `http://` or `https://`.

## Design note (50,000 auctions, up to 200,000 watchers on one)

- **One writer per auction.** Each auction is a small state machine with a single owner, and all of its bids pass through one lock, giving them a total order. "Highest bid wins, earliest accepted bid breaks ties" is decided in exactly one place, so there is no distributed locking. Different auctions never contend. At scale, auctions are partitioned by `auction_id` (consistent hashing) across bid-processing nodes. 50,000 auction states are tiny; the real load is one hot auction in its last ten seconds, where each bid costs microseconds under the lock.
- **Server-side time and atomic extension.** The clock is read inside the lock, so accepting a bid and deciding whether it extends the auction use one consistent instant. A bid at or after `end_time` is rejected even if the background closer has not run yet. Clients show a countdown from a server-time offset, but the server is the only authority, so nobody wins by bidding in the last instant.
- **Exactly one winner.** Closing is a one-way `OPEN -> CLOSED` transition made under the lock; the first caller (a late bid or the sweeper) performs it and records the winner as the highest accepted bid at that moment, in memory and in the database. Idempotency keys make client retries safe. Tests hammer close and bid concurrently and assert a single close event and a single winner.
- **Fan-out to 200k watchers.** Bidding never waits on watchers. The engine emits one event per state change; stateless gateway nodes hold the WebSocket connections (~10k each, so ~20 for the hottest auction) and subscribe to a per-auction pub/sub channel (Redis or NATS). Each watcher gets a size-1 "latest state" slot, so a slow client only ever receives the newest price and memory stays bounded. At peak, updates are batched into ~50-100 ms ticks instead of one push per bid. New or reconnecting watchers fetch a snapshot, and a version number lets them drop stale events.
- **Durability and failover.** Every accepted bid is written to the database before it is acknowledged (built here with SQLite); state is rebuilt by replay on restart (built). At scale the bid log moves to Postgres or a Kafka partition keyed by auction, with a lease and fencing token so only one node owns an auction after a failover (not built).
- **Closing at scale.** The 100 ms sweeper here is replaced by a deadline-ordered heap or timer wheel per node, so cost scales with the number of auctions closing, not the number open.

## Limits of this version

- SQLite is a single file with one writer: fine for one server and a demo, not for 50,000 busy auctions. All writes share one lock, so a bid waits behind other auctions' writes. The production path is above (Postgres/Kafka, one owner node per auction).
- It runs as a single process; there is no sharding or failover yet.
- Search and category filtering happen in the browser over the full auction list, which is fine for a demo; at scale they would be server-side queries with paging.
- Notifications are produced by the browser polling `/me/bids` every 5 seconds while the site is open; there is no push or email when the site is closed.
- Product photos are not uploaded: a seller supplies an image URL (or gets a built-in category illustration).
- Idempotency keys are remembered in memory only. After a restart a retried bid is re-evaluated (it can never apply twice: the amount would now be too low).
- Login sessions last 7 days and there is no password reset or email verification. The failed-login limiter is per process and in memory.

## What the tests cover

- **Engine:** 200 threads bidding the same amount (exactly one accepted); heavy random contention with ordering invariants; a storm of bids during the closing window with extensions; the exact 30 s boundary; chained last-second bids; close exactly once under races; idempotent retries.
- **API:** REST and WebSocket flows including the close event; list/history endpoints; the login page is served at `/` and the app at `/app.html`.
- **Accounts:** register/login/logout, validation, duplicate names, wrong passwords, lockout after repeated failures, 401 on protected routes, bidder identity taken from the token and not the body, seller cannot bid, only hashes stored, expired sessions.
- **Persistence:** state, extensions and winners survive a restart; an auction that ended while the server was down closes with the right winner; a failing database write rejects the bid and changes nothing; concurrent bids leave the database identical to memory; accounts, sessions and auctions survive an API restart.
- **Marketplace:** listing fields and defaults, validation (unknown category, non-http image URL), distinct participant count, public seller profile, `/me/bids` outcomes (leading, outbid, won, lost), and in-place upgrade of an old database.

## Assumptions

- "Extends by 30 seconds" means `end_time += 30`, applied whenever a bid arrives with 30 seconds or less remaining (the boundary is inclusive).
- The seller cannot bid on their own auction.
