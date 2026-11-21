/* Reusable UI building blocks. All dynamic text goes through textContent (no HTML injection). */

const CATEGORIES = ['Electronics', 'Watches', 'Collectibles', 'Art', 'Sneakers', 'Vehicles'];
const CATEGORY_BLURB = {
  Electronics: 'Cameras, audio, laptops and gadgets', Watches: 'Timepieces from vintage to modern',
  Collectibles: 'Rare finds and curiosities', Art: 'Paintings, prints and originals',
  Sneakers: 'Limited drops and grails', Vehicles: 'Cars and motorcycles',
};

/* tiny hyperscript helper */
function h(tag, attrs, ...kids){
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)){
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;                       // trusted static markup only
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)){
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const money = n => '₹' + Number(n).toLocaleString('en-IN');
const fmtDate = ts => new Date(ts * 1000).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
const go = path => { location.hash = '#' + path; };

/* ---------- countdowns: ONE global ticker updates every .cd element ---------- */
function fmtRemaining(s){
  if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor(s % 86400 / 3600)}h`;
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}m`;
  if (s >= 60) return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  return `${s.toFixed(1)}s`;
}
function countdown(a, cls = ''){
  const el = h('span', {class: ('cd ' + cls).trim()});
  setCountdown(el, a);
  tickOne(el);
  return el;
}
function setCountdown(el, a){
  if (a.status === 'CLOSED'){ el.dataset.closed = '1'; delete el.dataset.end; }
  else { delete el.dataset.closed; el.dataset.end = a.end_time; }
}
function tickOne(el){
  if (el.dataset.closed){ el.textContent = 'Ended';el.classList.remove('hot', 'soon'); return; }
  const rem = Number(el.dataset.end) - Clock.now();
  if (rem <= 0){ el.textContent = 'Closing…'; el.classList.remove('hot', 'soon'); return; }
  el.textContent = fmtRemaining(rem);
    el.classList.toggle('hot', rem <= 30);
    el.classList.toggle('soon', rem > 30 && rem <= 300);       // last 5 minutes: amber
}
setInterval(() => document.querySelectorAll('.cd').forEach(tickOne), 200);

/* ---------- toasts ---------- */
function toast(message, type = 'info', title = ''){
  const box = document.getElementById('toasts');
  const t = h('div', {class: 'toast ' + type, role: 'status'}, title ? h('b', null, title) : null, message);
  box.append(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); }, 4200);
}

/* ---------- product images with a built-in fallback ---------- */
function categoryArt(cat){
  const g = '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#23232a"/><stop offset="1" stop-color="#0c0c0f"/></linearGradient></defs><rect width="400" height="300" fill="url(#g)"/>';
  const s = 'fill="none" stroke="#d4af37" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"';
  const icons = {
    Watches: `<circle cx="200" cy="150" r="58" ${s}/><circle cx="200" cy="150" r="48" ${s} opacity=".4"/><path d="M200 150V118M200 150l22 14" ${s}/><path d="M178 94l6-34h32l6 34M178 206l6 34h32l6-34" ${s}/>`,
    Electronics: `<rect x="120" y="96" width="160" height="104" rx="8" ${s}/><path d="M92 214h216l-14 -14H106z" ${s}/><path d="M178 214v6h44v-6" ${s}/>`,
    Collectibles: `<path d="M200 78l62 38-62 112-62-112z" ${s}/><path d="M138 116h124M200 78l-26 38 26 112 26-112z" ${s} opacity=".7"/>`,
    Art: `<rect x="112" y="84" width="176" height="132" rx="4" ${s}/><rect x="128" y="100" width="144" height="100" ${s} opacity=".5"/><path d="M128 190l40-44 28 28 22-20 54 36" ${s}/><circle cx="232" cy="128" r="10" ${s}/>`,
    Sneakers: `<path d="M96 196c2-34 8-62 20-66l34 22 36-16c20 24 62 28 94 44v20z" ${s}/><path d="M96 216h210" ${s}/><path d="M168 150l12 14M190 142l12 14" ${s} opacity=".7"/>`,
    Vehicles: `<path d="M92 180l22-46c4-8 10-12 20-12h132c10 0 18 4 24 12l24 34 14 6v26H92z" ${s}/><circle cx="144" cy="206" r="20" fill="#0c0c0f" stroke="#d4af37" stroke-width="3"/><circle cx="268" cy="206" r="20" fill="#0c0c0f" stroke="#d4af37" stroke-width="3"/><path d="M130 134l-10 28h150l-20-28" ${s} opacity=".6"/>`,
  };
  const icon = icons[cat] || `<path d="M200 82l20 44 48 6-36 32 10 48-42-24-42 24 10-48-36-32 48-6z" ${s}/>`;
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">${g}${icon}</svg>`);
}
function productImg(a, alt){
  const img = h('img', {alt: alt || a.title, loading: 'lazy', src: a.image_url || categoryArt(a.category)});
  img.onerror = () => { img.onerror = null; img.src = categoryArt(a.category); };       // broken URL -> elegant fallback
  return img;
}

/* ---------- state blocks ---------- */
function loadingGrid(n = 6){ return h('div', {class: 'grid'}, Array.from({length: n}, () => h('div', {class: 'skeleton'}))); }
function emptyBlock(title, text, actionLabel, actionFn){
  return h('div', {class: 'state'}, h('h3', null, title), h('div', null, text),
    actionLabel ? h('button', {class: 'btn btn-gold', onclick: actionFn}, actionLabel) : null);
}
function errorBlock(text, retry){
  return h('div', {class: 'state'}, h('h3', null, 'Something went wrong'), h('div', null, text),
    retry ? h('button', {class: 'btn btn-ghost', onclick: retry}, 'Try again') : null);
}

/* ---------- components ---------- */
function liveBadge(a){ return h('span', {class: 'badge ' + (a.status === 'OPEN' ? 'live' : 'ended')}, a.status === 'OPEN' ? 'LIVE' : 'ENDED'); }

function AuctionCard(a){
  const open = a.status === 'OPEN';
  const view = () => go('/auction/' + encodeURIComponent(a.auction_id));
  return h('article', {class: 'card auction', onclick: view, tabindex: '0', onkeydown: e => { if (e.key === 'Enter') view(); }},
    h('div', {class: 'media'}, productImg(a),
      h('div', {class: 'tl'}, liveBadge(a)),
      h('div', {class: 'tr'}, h('span', {class: 'chip plain'}, a.category))),
    h('div', {class: 'body'},
      h('h3', null, a.title),
      h('div', {class: 'prices'},
        h('div', null, h('small', null, a.bid_count ? 'Current bid' : 'Opening bid'), h('b', null, money(a.current_price))),
        h('div', {style: 'text-align:right'}, h('small', null, 'Starting price'), h('span', {class: 'v'}, money(a.start_price)))),
      h('div', {class: 'meta'},
        h('span', null, '⏱ ', countdown(a)),
        h('span', null, `${a.bid_count} ${a.bid_count === 1 ? 'bid' : 'bids'}`)),
      h('a', {class: 'btn btn-gold block', href: '#/auction/' + encodeURIComponent(a.auction_id), onclick: e => e.stopPropagation()},
        open ? 'View Auction' : 'View Result')));
}

function CategoryTile(cat, count){
  return h('a', {class: 'cat', href: '#/auctions?cat=' + encodeURIComponent(cat)},
    h('img', {src: categoryArt(cat), alt: cat, loading: 'lazy'}),
    h('div', {class: 'label'}, h('b', null, cat), h('span', null, `${count} live`)));
}

function sectionHead(title, sub, linkText, linkHref){
  return h('div', {class: 'section-head'},
    h('div', null, h('h2', null, title), sub ? h('p', {class: 'sub'}, sub) : null),
    linkText ? h('a', {class: 'link-gold', href: linkHref}, linkText + ' →') : null);
}

/* a list signature: re-render only when something visible actually changed */
const listSig = list => JSON.stringify(list.map(a => [a.auction_id, a.current_price, a.bid_count, a.status, a.end_time, a.participants]));
