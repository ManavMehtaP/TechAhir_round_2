/* BIDZONE single-page app: navbar, notifications, router and pages.
   Bidding rules (increments, closing, the 30s extension, winner) all live on the server;
   this file only displays server state and sends bids. */

if (!Auth.token) location.replace('/');

const wrap = (...kids) => h('div', {class: 'container'}, ...kids);
let auctionsCache = [];
async function fetchAuctions(){
  const r = await api('/auctions');
  if (!r.ok) throw new Error(errMsg(r, 'Could not load auctions'));
  Clock.sync(r.body.server_time);
  auctionsCache = r.body.auctions;
  return auctionsCache;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* =====================================================================
   Notifications: polls /me/bids and raises "outbid" / "won" / "lost"
   ===================================================================== */
const Notifier = {
  prev: new Map(), latest: null, error: null, listeners: new Set(), timer: null,
  items: (() => { try { return JSON.parse(localStorage.getItem('notifs:' + Auth.username) || '[]'); } catch (e) { return []; } })(),
  unread: 0,
  start(){ this.unread = this.items.filter(n => n.unread).length; renderBell(); this.poll(); this.timer = setInterval(() => this.poll(), 5000); },
  push(text, href){
    this.items.unshift({text, href, at: Date.now() / 1000, unread: true});
    this.items = this.items.slice(0, 30);
    this.unread = this.items.filter(n => n.unread).length;
    try { localStorage.setItem('notifs:' + Auth.username, JSON.stringify(this.items)); } catch (e) {}
    toast(text, 'info', 'Notification');
    renderBell();
  },
  markRead(){
    this.items.forEach(n => n.unread = false); this.unread = 0;
    try { localStorage.setItem('notifs:' + Auth.username, JSON.stringify(this.items)); } catch (e) {}
    renderBell();
  },
  async poll(){
    const r = await api('/me/bids');
    if (!r.ok){ this.error = errMsg(r, 'Could not load your bids'); this.listeners.forEach(fn => fn(this.latest, this.error)); return; }
    this.error = null; Clock.sync(r.body.server_time);
    const rows = r.body.bids; this.latest = rows;
    for (const a of rows){
      const before = this.prev.get(a.auction_id), href = '#/auction/' + encodeURIComponent(a.auction_id);
      if (before){
        if (before.outcome === 'leading' && a.outcome === 'outbid') this.push(`You were outbid on "${a.title}". Current bid is ${money(a.current_price)}.`, href);
        else if (before.status === 'OPEN' && a.status === 'CLOSED'){
          this.push(a.outcome === 'won' ? `You won "${a.title}" for ${money(a.winning_amount)}! 🎉` : `Auction ended: "${a.title}" went to ${a.winner || 'no one'}.`, href);
        }
      }
      this.prev.set(a.auction_id, {outcome: a.outcome, status: a.status});
    }
    this.listeners.forEach(fn => fn(rows, null));
  },
};

/* =====================================================================
   Navbar
   ===================================================================== */
const ICON = {
  search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  bell: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0"/></svg>',
  burger: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
};
const NAV_LINKS = [['/', 'Home'], ['/auctions', 'Live Auctions'], ['/categories', 'Categories'], ['/my-bids', 'My Bids']];
let navEls = {};

function parseHash(){
  const raw = location.hash.replace(/^#/, '') || '/';
  const i = raw.indexOf('?');
  return {path: i < 0 ? raw : raw.slice(0, i), params: new URLSearchParams(i < 0 ? '' : raw.slice(i + 1))};
}
function doSearch(q){
  const {path, params} = parseHash();
  const p = new URLSearchParams(path === '/auctions' ? params : '');
  q = q.trim(); if (q) p.set('q', q); else p.delete('q');
  const s = p.toString();
  if (path === '/auctions' && (params.get('q') || '') === q) return;
  go('/auctions' + (s ? '?' + s : ''));
}
const searchLater = debounce(doSearch, 280);

function closeMenus(){ navEls.bellMenu && (navEls.bellMenu.hidden = true); navEls.userMenu && (navEls.userMenu.hidden = true); navEls.mobile && navEls.mobile.classList.remove('open'); }

function renderBell(){
  if (!navEls.bellMenu) return;
  navEls.dot.hidden = Notifier.unread === 0;
  navEls.dot.textContent = Notifier.unread > 9 ? '9+' : Notifier.unread;
  const list = navEls.bellList; list.replaceChildren();
  if (!Notifier.items.length) list.append(h('div', {class: 'notif muted'}, 'No notifications yet. You will be alerted when you are outbid or an auction you bid on ends.'));
  for (const n of Notifier.items){
    list.append(h('a', {class: 'notif' + (n.unread ? ' unread' : ''), href: n.href || '#/my-bids', onclick: () => closeMenus()},
      h('div', null, n.text), h('div', {class: 'muted small'}, new Date(n.at * 1000).toLocaleTimeString())));
  }
}

function renderNav(){
  const nav = document.getElementById('nav');
  const mkInput = id => h('input', {type: 'search', id, placeholder: 'Search auctions…', 'aria-label': 'Search auctions', autocomplete: 'off',
    oninput: e => searchLater(e.target.value)});
  const mkSearch = id => h('form', {class: 'search', onsubmit: e => { e.preventDefault(); doSearch(e.target.querySelector('input').value); }},
    h('span', {html: ICON.search}), mkInput(id));
  const linkEls = NAV_LINKS.map(([p, t]) => h('a', {href: '#' + p, 'data-p': p}, t));
  const mobileLinks = NAV_LINKS.map(([p, t]) => h('a', {href: '#' + p}, t));

  const dot = h('span', {class: 'dot', hidden: true});
  const bellList = h('div', {class: 'list'});
  const bellMenu = h('div', {class: 'menu', hidden: true},
    h('div', {class: 'head'}, 'Notifications', h('button', {class: 'btn btn-ghost small', onclick: () => Notifier.markRead()}, 'Mark all read')), bellList);
  const bell = h('button', {class: 'icon-btn', 'aria-label': 'Notifications', onclick: e => {
    e.stopPropagation(); const show = bellMenu.hidden; closeMenus(); bellMenu.hidden = !show; if (show) renderBell();
  }}, h('span', {html: ICON.bell}), dot);

  const initial = (Auth.username || '?')[0].toUpperCase();
  const userMenu = h('div', {class: 'menu', hidden: true},
    h('div', {class: 'head'}, h('span', null, Auth.username)),
    h('a', {href: '#/my-bids'}, 'My Bids'),
    h('a', {href: '#/create'}, 'Create an auction'),
    h('button', {class: 'item', onclick: logout}, 'Log out'));
  const avatar = h('button', {class: 'avatar', 'aria-label': 'Account menu', onclick: e => {
    e.stopPropagation(); const show = userMenu.hidden; closeMenus(); userMenu.hidden = !show;
  }}, initial);

  const mobile = h('div', {class: 'mobile-menu'}, mkSearch('qm'), ...mobileLinks,
    h('a', {href: '#/create'}, '+ Create an auction'));
  const burger = h('button', {class: 'icon-btn burger', 'aria-label': 'Menu', onclick: e => { e.stopPropagation(); const open = mobile.classList.contains('open'); closeMenus(); if (!open) mobile.classList.add('open'); }},
    h('span', {html: ICON.burger}));

  nav.replaceChildren(
    h('div', {class: 'nav-inner'},
      h('a', {class: 'logo', href: '#/'}, 'BID', h('span', null, 'ZONE')),
      h('nav', {class: 'links'}, ...linkEls),
      mkSearch('q'),
      h('div', {class: 'nav-actions'}, h('a', {class: 'btn btn-gold small', href: '#/create'}, '+ Sell'), bell, bellMenu, avatar, userMenu, burger)),
    mobile);
  navEls = {linkEls, bellMenu, bellList, dot, userMenu, mobile};
  document.addEventListener('click', e => { if (!e.target.closest('.menu')) closeMenus(); });
  renderBell();
}
function setActive(path){
  navEls.linkEls.forEach(a => {
    const p = a.dataset.p;
    a.classList.toggle('active', p === '/' ? path === '/' : path === p || (p === '/auctions' && path.startsWith('/auction/')));
  });
}
function logout(){
  fetch('/auth/logout', {method: 'POST', headers: {Authorization: 'Bearer ' + Auth.token}})
    .finally(() => { Auth.clear(); location.replace('/'); });
}

/* =====================================================================
   Pages. Each returns {el, destroy?, update?}
   ===================================================================== */

/* ---------- Home ---------- */
function HomePage(){
  const root = h('div', null, wrap(h('div', {style: 'padding:40px 0'}, loadingGrid(3))));
  let sig = '', timer = null;

  function draw(list){
    const open = list.filter(a => a.status === 'OPEN');
    const popular = [...open].sort((a, b) => b.bid_count - a.bid_count || a.end_time - b.end_time);
    const ending = [...open].sort((a, b) => a.end_time - b.end_time).slice(0, 4);
    const featured = popular[0];
    const totalBids = list.reduce((n, a) => n + a.bid_count, 0);

    const heroRight = featured
      ? h('a', {class: 'feature', href: '#/auction/' + encodeURIComponent(featured.auction_id)},
          h('div', {class: 'media'}, productImg(featured), h('div', {class: 'tl'}, liveBadge(featured)),
            h('div', {class: 'tr'}, h('span', {class: 'chip'}, 'Featured'))),
          h('div', {class: 'info'},
            h('div', null, h('h3', null, featured.title), h('span', {class: 'muted small'}, `${featured.bid_count} bids · ends in `, countdown(featured))),
            h('div', {style: 'text-align:right'}, h('div', {class: 'muted small'}, featured.bid_count ? 'Current bid' : 'Opening bid'),
              h('b', {class: 'gold serif', style: 'font-size:24px'}, money(featured.current_price)))))
      : h('div', {class: 'feature'}, h('div', {class: 'media'}, h('img', {src: categoryArt(''), alt: ''})),
          h('div', {class: 'info'}, h('div', null, h('h3', null, 'No live auctions yet'), h('span', {class: 'muted small'}, 'Be the first to list something extraordinary.'))));

    const hero = h('section', {class: 'hero'}, wrap(h('div', {class: 'hero-grid'},
      h('div', null,
        h('span', {class: 'eyebrow'}, '● Live auction marketplace'),
        h('h1', null, 'Bid. Win. Own Something ', h('em', null, 'Extraordinary.')),
        h('p', {class: 'lead'}, 'Real-time auctions where every price updates instantly and every finish is fair. A late bid extends the clock, so nobody wins by sniping the last second.'),
        h('div', {class: 'hero-actions'},
          h('a', {class: 'btn btn-gold', href: '#/auctions'}, 'Browse Auctions'),
          h('a', {class: 'btn btn-ghost', href: '#/create'}, 'Sell an item')),
        h('div', {class: 'hero-stats'},
          h('div', null, h('b', null, open.length), h('span', null, 'Live now')),
          h('div', null, h('b', null, totalBids), h('span', null, 'Bids placed')),
          h('div', null, h('b', null, list.length), h('span', null, 'Total auctions')))),
      heroRight)));

    const featuredSec = h('section', {class: 'section'}, wrap(
      sectionHead('Featured Live Auctions', 'The most active auctions right now', 'View all', '#/auctions'),
      popular.length ? h('div', {class: 'grid'}, popular.slice(0, 6).map(AuctionCard))
                     : emptyBlock('No live auctions right now', 'Create the first one and start the bidding.', 'Create an auction', () => go('/create'))));

    const counts = Object.fromEntries(CATEGORIES.map(c => [c, open.filter(a => a.category === c).length]));
    const catSec = h('section', {class: 'section'}, wrap(
      sectionHead('Shop by Category', 'Find exactly what you collect', 'All categories', '#/categories'),
      h('div', {class: 'cats'}, CATEGORIES.map(c => CategoryTile(c, counts[c])))));

    const endSec = ending.length ? h('section', {class: 'section'}, wrap(
      sectionHead('Ending Soon', 'Last chance. Remember: a bid in the final 30 seconds extends the auction.', 'See all', '#/auctions'),
      h('div', {class: 'grid'}, ending.map(AuctionCard)))) : null;

    root.replaceChildren(...[hero, featuredSec, catSec, endSec].filter(Boolean));
  }
  async function load(){
    try {
      const list = await fetchAuctions(), s = listSig(list);
      if (s === sig) return; sig = s; draw(list);
    } catch (e) {
      if (!sig) root.replaceChildren(wrap(h('div', {style: 'padding:40px 0'}, errorBlock(e.message, () => { sig = ''; load(); }))));
    }
  }
  load(); timer = setInterval(load, 4000);
  return {el: root, destroy(){ clearInterval(timer); }};
}

/* ---------- Live Auctions (search + category filters) ---------- */
function AuctionsPage(params){
  let q = params.get('q') || '', cat = params.get('cat') || '';
  let sort = 'ending', showEnded = false, list = null, sig = '', timer = null;
  const root = wrap(h('h1', {class: 'page-title'}, 'Live Auctions'));
  const sub = h('p', {class: 'muted', style: 'margin:0'});
  const pills = h('div', {class: 'filters', style: 'margin-bottom:10px'});
  const tools = h('div', {class: 'filters', style: 'margin-top:0'});
  const results = h('div', null, loadingGrid(6));

  const sortSel = h('select', {class: 'input', 'aria-label': 'Sort auctions', onchange: e => { sort = e.target.value; renderResults(true); }},
    h('option', {value: 'ending'}, 'Ending soonest'), h('option', {value: 'highest'}, 'Highest bid'), h('option', {value: 'popular'}, 'Most bids'));
  const endedChk = h('input', {type: 'checkbox', onchange: e => { showEnded = e.target.checked; renderResults(true); }});
  tools.append(sortSel, h('label', {class: 'toggle'}, endedChk, 'Show ended auctions'));
  root.append(sub, pills, tools, results);

  function setParams(next){
    const p = new URLSearchParams();
    if (next.q) p.set('q', next.q); if (next.cat) p.set('cat', next.cat);
    go('/auctions' + (p.toString() ? '?' + p.toString() : ''));
  }
  function renderPills(){
    pills.replaceChildren(...['', ...CATEGORIES].map(c =>
      h('button', {class: 'pill' + (c === cat ? ' on' : ''), onclick: () => setParams({q, cat: c})}, c || 'All')));
  }
  function filtered(){
    const needle = q.toLowerCase();
    let rows = (list || []).filter(a => (showEnded || a.status === 'OPEN')
      && (!cat || a.category === cat)
      && (!needle || [a.title, a.description, a.category, a.seller].some(t => (t || '').toLowerCase().includes(needle))));
    const open = (a, b) => (a.status === 'OPEN' ? 0 : 1) - (b.status === 'OPEN' ? 0 : 1);
    if (sort === 'highest') rows.sort((a, b) => open(a, b) || b.current_price - a.current_price);
    else if (sort === 'popular') rows.sort((a, b) => open(a, b) || b.bid_count - a.bid_count);
    else rows.sort((a, b) => open(a, b) || (a.status === 'OPEN' ? a.end_time - b.end_time : b.end_time - a.end_time));
    return rows;
  }
  let lastKey = '';
  function renderResults(force){
    if (list === null) return;
    const rows = filtered();
    const key = sig + '|' + q + '|' + cat + '|' + sort + '|' + showEnded;
    if (!force && key === lastKey) return;
    lastKey = key;
    sub.textContent = `${rows.length} ${rows.length === 1 ? 'auction' : 'auctions'}` + (q ? ` matching "${q}"` : '') + (cat ? ` in ${cat}` : '');
    results.replaceChildren(rows.length ? h('div', {class: 'grid'}, rows.map(AuctionCard))
      : emptyBlock('No auctions found', q || cat ? 'Try a different search or category.' : 'There are no live auctions right now.',
          q || cat ? 'Clear filters' : 'Create an auction', q || cat ? () => setParams({q: '', cat: ''}) : () => go('/create')));
  }
  async function load(){
    try { list = await fetchAuctions(); sig = listSig(list); renderResults(); }
    catch (e) { if (list === null) results.replaceChildren(errorBlock(e.message, load)); }
  }
  function syncSearchBox(){ for (const id of ['q', 'qm']){ const el = document.getElementById(id); if (el && el.value !== q) el.value = q; } }
  renderPills(); syncSearchBox(); load(); timer = setInterval(load, 3000);
  return {
    el: root, destroy(){ clearInterval(timer); },
    update(p){ q = p.get('q') || ''; cat = p.get('cat') || ''; renderPills(); syncSearchBox(); renderResults(); },
  };
}

/* ---------- Categories ---------- */
function CategoriesPage(){
  const root = wrap(h('h1', {class: 'page-title'}, 'Categories'), h('p', {class: 'muted'}, 'Pick a category to browse its live auctions.'));
  const body = h('div', {style: 'margin-top:24px'}, loadingGrid(6)); root.append(body);
  let timer;
  async function load(){
    try {
      const list = await fetchAuctions(), open = list.filter(a => a.status === 'OPEN');
      body.replaceChildren(h('div', {class: 'cats'}, CATEGORIES.map(c => CategoryTile(c, open.filter(a => a.category === c).length))),
        h('div', {class: 'grid', style: 'margin-top:26px'}, CATEGORIES.map(c => h('div', {class: 'panel'}, h('b', {class: 'serif'}, c), h('div', {class: 'muted small', style: 'margin-top:4px'}, CATEGORY_BLURB[c])))));
    } catch (e) { body.replaceChildren(errorBlock(e.message, load)); }
  }
  load(); timer = setInterval(load, 6000);
  return {el: root, destroy(){ clearInterval(timer); }};
}

/* ---------- Auction details + live bidding ---------- */
function AuctionDetailPage(id){
  const root = wrap(h('div', {style: 'padding:40px 0'}, loadingGrid(2)));
  const me = Auth.username;
  let state = null, sock = null, histTimer = null, alive = true, submitting = false, live = false, R = null;

  async function init(){
    root.replaceChildren(h('div', {style: 'padding:40px 0'}, loadingGrid(2)));
    const r = await api('/auctions/' + encodeURIComponent(id));
    if (!alive) return;
    if (!r.ok){
      root.replaceChildren(h('div', {style: 'padding:40px 0'}, r.status === 404
        ? emptyBlock('Auction not found', 'This auction does not exist or the link is wrong.', 'Browse auctions', () => go('/auctions'))
        : errorBlock(errMsg(r, 'Could not load this auction'), init)));
      return;
    }
    Clock.sync(r.body.server_time);
    build(r.body); apply(r.body); loadHistory(); loadSeller(r.body.seller);
    sock = openSocket(id, {
      onStatus: setLive,
      onMessage: ev => {
        apply(ev);
        if (ev.type !== 'snapshot') scheduleHistory();
        if (ev.status === 'CLOSED'){ sock.close(); setLive('finished'); loadHistory(); }
      },
    });
  }

  function build(s){
    R = {};
    R.badge = h('div', {class: 'tl'});
    R.banner = h('div', {hidden: true});
    R.price = h('div', {class: 'bigprice'}); R.priceCap = h('div', {class: 'muted small', style: 'margin-top:4px'});
    R.start = h('b'); R.minNext = h('b'); R.parts = h('b'); R.bids = h('b'); R.leader = h('b');
    R.cd = h('span', {class: 'cd timer'}); R.ext = h('span', {class: 'ext'});
    R.live = h('span', {class: 'muted small'}, 'connecting…');
    R.note = h('div', {class: 'muted small', style: 'margin-top:10px'});
    R.input = h('input', {class: 'input', type: 'number', inputmode: 'numeric', min: '1', step: '1', placeholder: 'Enter your bid',
      'aria-label': 'Your bid amount', onkeydown: e => { if (e.key === 'Enter') submit(); }, oninput: () => { R.input.dataset.touched = '1'; setMsg(''); }});
    R.btnLabel = h('span', null, 'Place Bid'); R.btn = h('button', {class: 'btn btn-gold', onclick: submit}, R.btnLabel);
    R.quick = h('div', {class: 'quick'});
    R.msg = h('div', {class: 'err', role: 'alert'});
    R.hist = h('tbody'); R.histEmpty = h('p', {class: 'muted small', style: 'margin:12px 0 0'}, 'No bids yet. Be the first!');
    R.sellerName = h('b'); R.sellerMeta = h('div', {class: 'muted small'}); R.sellerAv = h('div', {class: 'avatar'}, (s.seller || '?')[0].toUpperCase());
    R.title = h('h1'); R.chip = h('span', {class: 'chip'});
    R.desc = h('div', {class: 'desc'});

    const left = h('div', null,
      h('div', {class: 'hero-img media'}, productImg(s), R.badge),
      h('div', {class: 'panel', style: 'margin-top:18px'}, h('h3', {class: 'sec'}, 'Description'), R.desc),
      h('div', {class: 'panel'}, h('h3', {class: 'sec'}, 'Seller'),
        h('div', {class: 'seller'}, R.sellerAv, h('div', null, R.sellerName, R.sellerMeta))));
    const right = h('div', {class: 'sticky'}, h('div', {class: 'panel'},
      R.chip, R.title, h('div', {class: 'muted small'}, R.live),
      h('div', {style: 'margin-top:20px'}, h('div', {class: 'muted small'}, 'CURRENT HIGHEST BID'), R.price, R.priceCap),
      h('div', {class: 'kv'},
        h('div', null, h('small', null, 'Starting price'), R.start), h('div', null, h('small', null, 'Minimum next bid'), R.minNext),
        h('div', null, h('small', null, 'Participants'), R.parts), h('div', null, h('small', null, 'Total bids'), R.bids),
        h('div', {style: 'grid-column:1/-1'}, h('small', null, 'Leading bidder'), R.leader)),
      h('div', {style: 'padding:14px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin-bottom:18px'},
        h('div', {class: 'muted small'}, 'TIME REMAINING', R.ext), R.cd),
      h('div', {class: 'bidrow'}, h('div', {class: 'input-prefix'}, h('span', null, '₹'), R.input), R.btn),
      R.quick, R.msg, R.note));
    const history = h('div', {class: 'panel', style: 'margin-top:26px'}, h('h3', {class: 'sec'}, 'Bid history'),
      h('table', null, h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'Bidder'), h('th', {class: 'r'}, 'Amount'), h('th', {class: 'r'}, 'Time'))), R.hist), R.histEmpty);

    root.replaceChildren(
      h('button', {class: 'back', onclick: () => go('/auctions')}, '← Back to auctions'),
      R.banner,
      h('div', {class: 'detail'}, left, right),
      history);
    R.title.textContent = s.title; R.chip.textContent = s.category; R.desc.textContent = s.description || 'The seller has not added a description.';
    if (!s.description) R.desc.classList.add('muted');
    R.sellerName.textContent = s.seller + (s.seller === me ? ' (you)' : '');
  }

  function apply(s){
    if (!R) return;
    if (s.server_time) Clock.sync(s.server_time);
    if (state && s.version < state.version) return;                      // ignore stale updates
    const prev = state; state = s;
    const open = s.status === 'OPEN', isSeller = s.seller === me;

    R.badge.replaceChildren(liveBadge(s));
    R.price.textContent = s.bid_count ? money(s.current_price) : '—';
    R.priceCap.textContent = s.bid_count ? '' : `No bids yet. Opening bid is ${money(s.start_price)}.`;
    R.start.textContent = money(s.start_price); R.minNext.textContent = money(s.min_next_bid);
    R.parts.textContent = s.participants; R.bids.textContent = s.bid_count;
    R.leader.textContent = s.leader ? (s.leader === me ? s.leader + ' (you)' : s.leader) : 'No bids yet';
    R.leader.className = s.leader === me ? 'good' : '';
    setCountdown(R.cd, s); tickOne(R.cd);

    if (prev && prev.current_price !== s.current_price){ R.price.classList.remove('flash'); void R.price.offsetWidth; R.price.classList.add('flash'); }
    if (prev && s.end_time > prev.end_time){
      R.ext.textContent = '+' + Math.round(s.end_time - prev.end_time) + 's extended'; R.ext.classList.add('show');
      toast('A late bid extended this auction. The clock moved forward.', 'info', 'Time extended');
      setTimeout(() => R.ext.classList.remove('show'), 3000);
    }
    if (!R.input.dataset.touched || Number(R.input.value) < s.min_next_bid) R.input.value = s.min_next_bid;
    R.input.min = s.min_next_bid;

    const canBid = open && !isSeller && !submitting;
    R.btn.disabled = !canBid; R.input.disabled = !open || isSeller;
    R.quick.replaceChildren(...[['Min', 0], ['+1 step', 1], ['+5 steps', 5]].map(([label, k]) =>
      h('button', {class: 'btn btn-ghost small', disabled: !open || isSeller, onclick: () => { R.input.value = s.min_next_bid + k * s.min_increment; R.input.dataset.touched = '1'; setMsg(''); }},
        k ? `${label} (${money(s.min_next_bid + k * s.min_increment)})` : `${label} (${money(s.min_next_bid)})`)));
    R.note.textContent = isSeller ? "You are the seller, so you can't bid on your own auction."
      : !open ? 'This auction has ended.' : 'A bid in the final 30 seconds extends the auction by 30 seconds.';

    if (!open){
      R.banner.hidden = false;
      R.banner.className = 'banner' + (s.winner ? '' : ' none');
      R.banner.textContent = s.winner ? `🏆 Sold! ${s.winner}${s.winner === me ? ' (you)' : ''} won with a bid of ${money(s.winning_amount)}.` : 'This auction ended with no bids.';
    }
  }

  function setLive(status){
    live = status === 'live';
    if (!R) return;
    R.live.textContent = status === 'live' ? '● Live updates on' : status === 'finished' ? 'Auction finished' : '○ Reconnecting…';
    R.live.style.color = status === 'live' ? 'var(--good)' : '';
  }
  function setMsg(text, ok){ R.msg.className = ok ? 'ok' : 'err'; R.msg.textContent = text; }

  async function submit(){
    if (submitting || !state) return;
    const raw = R.input.value.trim(), amount = Number(raw);
    if (!raw){ setMsg('Enter a bid amount.'); return; }
    if (!Number.isInteger(amount) || amount <= 0){ setMsg('Enter a whole-number amount greater than zero.'); return; }
    if (amount < state.min_next_bid){ setMsg(`Your bid must be at least ${money(state.min_next_bid)}.`); return; }
    submitting = true; R.btn.disabled = true; R.btnLabel.replaceChildren(h('span', {class: 'spinner'}), ' Placing…'); setMsg('');
    const r = await apiPost('/auctions/' + encodeURIComponent(id) + '/bids', {amount, idempotency_key: uid()});
    submitting = false; R.btnLabel.textContent = 'Place Bid';
    if (!alive) return;
    if (r.body && r.body.accepted){
      toast(`Your bid of ${money(amount)} was accepted.`, 'success', 'Bid placed');
      if (r.body.extended) toast('Your bid extended the auction by 30 seconds.', 'info');
      setMsg(`Bid accepted at ${money(amount)}.`, true);
      R.input.dataset.touched = '';
      if (!live) refresh();                                                // no WebSocket: pull the new state
    } else {
      const m = errMsg(r, 'Your bid was rejected.');
      toast(m, 'error', 'Bid rejected'); setMsg(m);
      refresh();                                                           // show the latest price the server knows
    }
    if (state) apply(state);
  }
  async function refresh(){
    const r = await api('/auctions/' + encodeURIComponent(id));
    if (alive && r.ok){ state = state && {...state, version: -1}; apply(r.body); scheduleHistory(); }
  }
  async function loadHistory(){
    if (!alive) return;
    const r = await api('/auctions/' + encodeURIComponent(id) + '/bids?limit=30');
    if (!alive || !r.ok || !R) return;
    R.histEmpty.hidden = r.body.bids.length > 0;
    R.hist.replaceChildren(...r.body.bids.map(b => h('tr', null, h('td', null, b.seq),
      h('td', null, b.bidder === me ? b.bidder + ' (you)' : b.bidder), h('td', {class: 'r gold'}, money(b.amount)),
      h('td', {class: 'r muted'}, new Date(b.at * 1000).toLocaleTimeString()))));
  }
  function scheduleHistory(){ clearTimeout(histTimer); histTimer = setTimeout(loadHistory, 250); }
  async function loadSeller(name){
    const r = await api('/users/' + encodeURIComponent(name));
    if (!alive || !R) return;
    R.sellerMeta.textContent = r.ok
      ? `Member since ${new Date(r.body.member_since * 1000).toLocaleDateString([], {month: 'short', year: 'numeric'})} · ${r.body.auctions_listed} ${r.body.auctions_listed === 1 ? 'auction' : 'auctions'} listed`
      : 'Seller details unavailable';
  }

  init();
  return {el: root, destroy(){ alive = false; sock && sock.close(); clearTimeout(histTimer); }};
}

/* ---------- My Bids ---------- */
function MyBidsPage(){
  const root = wrap(h('h1', {class: 'page-title'}, 'My Bids'), h('p', {class: 'muted'}, 'Everything you are bidding on, with live status.'));
  const body = h('div', {style: 'margin-top:18px'}, loadingGrid(2)); root.append(body);
  let sig = null;

  const chip = a => ({leading: ['Leading', 'good'], outbid: ['Outbid', 'bad'], won: ['Won', 'good'], lost: ['Lost', 'plain']}[a.outcome]);
  function row(a){
    const open = a.status === 'OPEN', [label, cls] = chip(a), href = '/auction/' + encodeURIComponent(a.auction_id);
    return h('div', {class: 'card mybid', onclick: () => go(href)},
      h('div', {class: 'thumb'}, productImg(a)),
      h('div', null, h('h4', null, a.title),
        h('div', {class: 'cols'},
          h('span', null, 'Your latest bid ', h('b', null, money(a.my_amount))),
          h('span', null, open ? 'Current highest ' : 'Final price ', h('b', null, money(a.current_price))),
          open ? h('span', null, 'Ends in ', countdown(a)) : h('span', null, a.winner ? `Winner: ${a.winner}` : ''))),
      h('div', {class: 'side'}, h('span', {class: 'chip ' + cls}, label),
        open ? h('button', {class: 'btn btn-gold small', onclick: e => { e.stopPropagation(); go(href); }}, a.outcome === 'outbid' ? 'Bid again' : 'View') : null));
  }
  function group(title, rows, empty){
    return h('div', {style: 'margin-top:30px'}, h('h3', {class: 'sec serif'}, `${title} (${rows.length})`),
      rows.length ? rows.map(row) : h('p', {class: 'muted'}, empty));
  }
  function draw(rows, err){
    if (err && rows === null){ body.replaceChildren(errorBlock(err, () => Notifier.poll())); return; }
    if (rows === null) return;
    const s = rows.map(a => [a.auction_id, a.my_amount, a.current_price, a.outcome, a.end_time]).join('|');
    if (s === sig) return; sig = s;
    if (!rows.length){ body.replaceChildren(emptyBlock("You haven't placed any bids yet", 'Find something you love and place your first bid.', 'Browse auctions', () => go('/auctions'))); return; }
    const active = rows.filter(a => a.status === 'OPEN').sort((a, b) => (a.outcome === 'outbid' ? 0 : 1) - (b.outcome === 'outbid' ? 0 : 1) || a.end_time - b.end_time);
    const won = rows.filter(a => a.outcome === 'won'), lost = rows.filter(a => a.outcome === 'lost');
    const stat = (n, l) => h('div', {class: 'card stat'}, h('b', null, n), h('span', {class: 'muted small'}, l));
    body.replaceChildren(
      h('div', {class: 'stats'}, stat(active.length, 'Active'), stat(active.filter(a => a.outcome === 'leading').length, 'Leading'),
        stat(active.filter(a => a.outcome === 'outbid').length, 'Outbid'), stat(won.length, 'Won')),
      group('Active auctions', active, 'No active auctions right now.'),
      group('Won auctions', won, 'No wins yet. Your first one is coming.'),
      group('Lost / ended', lost, 'Nothing here yet.'));
  }
  const listener = (rows, err) => draw(rows, err);
  Notifier.listeners.add(listener);
  if (Notifier.latest) draw(Notifier.latest, null); else draw(null, Notifier.error);
  Notifier.poll();
  return {el: root, destroy(){ Notifier.listeners.delete(listener); }};
}

/* ---------- Create auction ---------- */
function CreatePage(){
  const pad = n => String(n).padStart(2, '0');
  const toLocal = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const f = {};
  f.title = h('input', {class: 'input', maxlength: '120', placeholder: 'e.g. Vintage automatic dive watch'});
  f.desc = h('textarea', {class: 'input', maxlength: '2000', placeholder: 'Condition, history, what is included…'});
  f.cat = h('select', {class: 'input'}, CATEGORIES.map(c => h('option', {value: c}, c)));
  f.price = h('input', {class: 'input', type: 'number', min: '0', step: '1', value: '1000'});
  f.inc = h('input', {class: 'input', type: 'number', min: '1', step: '1', value: '100'});
  f.end = h('input', {class: 'input', type: 'datetime-local', value: toLocal(new Date(Date.now() + 3600e3))});
  f.img = h('input', {class: 'input', type: 'url', placeholder: 'https://… (optional)'});
  const preview = h('div', {class: 'preview'}, 'Image preview');
  const endNote = h('div', {class: 'muted small'});
  const msg = h('div', {class: 'err', role: 'alert'});
  const label = h('span', null, 'Create auction');
  const btn = h('button', {class: 'btn btn-gold block', type: 'submit', style: 'margin-top:8px'}, label);

  function updateEndNote(){
    const secs = new Date(f.end.value).getTime() / 1000 - Clock.now();
    endNote.textContent = isFinite(secs) ? (secs > 0 ? `Runs for ${fmtRemaining(secs)} from now. A bid in the last 30 seconds extends it.` : 'End time must be in the future.') : '';
  }
  const showPreview = debounce(() => {
    const u = f.img.value.trim();
    if (!u){ preview.replaceChildren('Image preview'); return; }
    const img = h('img', {src: u, alt: 'Preview'});
    img.onerror = () => preview.replaceChildren("Couldn't load that image. A category image will be used instead.");
    preview.replaceChildren(img);
  }, 400);
  f.img.addEventListener('input', showPreview); f.end.addEventListener('input', updateEndNote);

  async function onSubmit(e){
    e.preventDefault(); msg.textContent = '';
    const title = f.title.value.trim(), price = Number(f.price.value), inc = Number(f.inc.value);
    const secs = new Date(f.end.value).getTime() / 1000 - Clock.now(), img = f.img.value.trim();
    if (title.length < 3) return void (msg.textContent = 'Give your auction a title (at least 3 characters).');
    if (!Number.isInteger(price) || price < 0) return void (msg.textContent = 'Starting price must be a whole number, 0 or more.');
    if (!Number.isInteger(inc) || inc < 1) return void (msg.textContent = 'Minimum increment must be a whole number, 1 or more.');
    if (!isFinite(secs) || secs < 60) return void (msg.textContent = 'Pick an end time at least 1 minute from now.');
    if (img && !/^https?:\/\//i.test(img)) return void (msg.textContent = 'Image URL must start with http:// or https://');
    btn.disabled = true; label.replaceChildren(h('span', {class: 'spinner'}), ' Creating…');
    const auctionId = (title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'auction').slice(0, 40) + '-' + Math.random().toString(36).slice(2, 6);
    const r = await apiPost('/auctions', {id: auctionId, title, description: f.desc.value.trim(), category: f.cat.value, image_url: img,
      start_price: price, min_increment: inc, duration_seconds: secs});
    btn.disabled = false; label.textContent = 'Create auction';
    if (r.ok){ toast('Your auction is live.', 'success', 'Created'); go('/auction/' + encodeURIComponent(auctionId)); }
    else { msg.textContent = errMsg(r, 'Could not create the auction.'); toast(msg.textContent, 'error'); }
  }
  const fld = (l, el, extra, cls) => h('div', {class: 'field ' + (cls || '')}, h('label', null, l), el, extra);
  const money$ = el => h('div', {class: 'input-prefix'}, h('span', null, '₹'), el);

  updateEndNote();
  const root = wrap(h('h1', {class: 'page-title'}, 'Create an auction'),
    h('p', {class: 'muted'}, 'You become the seller. Bidding rules are enforced by the server once the auction starts.'),
    h('form', {class: 'panel', style: 'margin-top:22px;max-width:820px', onsubmit: onSubmit, novalidate: true},
      h('div', {class: 'form-grid'},
        fld('Auction title', f.title, null, 'full'), fld('Description', f.desc, null, 'full'),
        fld('Category', f.cat), fld('Starting price', money$(f.price)),
        fld('Minimum bid increment', money$(f.inc)), fld('End date & time', f.end, endNote),
        fld('Product image URL', f.img, null, 'full'), h('div', {class: 'field full'}, preview)),
      btn, msg));
  return {el: root};
}

/* =====================================================================
   Router
   ===================================================================== */
const ROUTES = [
  {name: 'home', re: /^\/$/, make: () => HomePage()},
  {name: 'auctions', re: /^\/auctions$/, make: (m, p) => AuctionsPage(p)},
  {name: 'categories', re: /^\/categories$/, make: () => CategoriesPage()},
  {name: 'detail', re: /^\/auction\/(.+)$/, make: m => AuctionDetailPage(decodeURIComponent(m[1]))},
  {name: 'mybids', re: /^\/my-bids$/, make: () => MyBidsPage()},
  {name: 'create', re: /^\/create$/, make: () => CreatePage()},
];
let current = null, currentName = null;

function route(){
  const {path, params} = parseHash();
  const r = ROUTES.find(x => x.re.test(path));
  setActive(path); closeMenus();
  if (r && r.name === 'auctions' && currentName === 'auctions' && current && current.update){ current.update(params); return; }
  if (current && current.destroy) current.destroy();
  const view = document.getElementById('view');
  view.replaceChildren(); window.scrollTo(0, 0);
  if (!r){
    current = null; currentName = null;
    view.append(wrap(h('div', {style: 'padding:60px 0'}, emptyBlock('Page not found', "We couldn't find that page.", 'Go home', () => go('/')))));
    return;
  }
  current = r.make(path.match(r.re), params); currentName = r.name;
  view.append(current.el);
}

if (Auth.token){
  renderNav();
  Notifier.start();
  window.addEventListener('hashchange', route);
  route();
}
