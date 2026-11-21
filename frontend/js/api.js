/* API layer. The backend is the single source of truth: this file only transports requests. */

const Auth = {
  token: localStorage.getItem('token'),
  username: localStorage.getItem('username') || '',
  clear(){ localStorage.removeItem('token'); localStorage.removeItem('username'); this.token = null; },
};

// Server clock offset, so countdowns follow the server's time, not the user's laptop clock.
const Clock = {
  offset: 0,
  sync(serverTime){ if (serverTime) this.offset = serverTime - Date.now() / 1000; },
  now(){ return Date.now() / 1000 + this.offset; },
};

async function api(path, opts = {}){
  const headers = {...(opts.headers || {})};
  if (Auth.token) headers.Authorization = 'Bearer ' + Auth.token;
  let res;
  try { res = await fetch(path, {...opts, headers}); }
  catch (e) { return {ok: false, status: 0, body: null, networkError: true}; }
  if (res.status === 401 && Auth.token){ Auth.clear(); location.replace('/'); return {ok: false, status: 401, body: null}; }
  let body = null; try { body = await res.json(); } catch (e) {}
  return {ok: res.ok, status: res.status, body};
}
const apiPost = (path, data) => api(path, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(data)});

function errMsg(r, fallback){
  if (r.networkError) return 'Cannot reach the server. Check that it is running.';
  const b = r.body; if (!b) return fallback;
  let d = b.message || b.detail;
  if (Array.isArray(d)) d = d.map(x => x.msg).join('; ');
  return d || fallback;
}

const uid = () => (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
  : Date.now().toString(36) + Math.random().toString(36).slice(2);

/* Live updates over the existing WebSocket endpoint, with automatic reconnect. */
function openSocket(auctionId, {onMessage, onStatus}){
  let ws, closed = false, retry = 0, timer;
  function connect(){
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/auctions/${encodeURIComponent(auctionId)}/ws`);
    ws.onopen = () => { retry = 0; onStatus('live'); };
    ws.onmessage = m => { try { onMessage(JSON.parse(m.data)); } catch (e) {} };
    ws.onclose = () => {
      if (closed) return;
      onStatus('reconnecting');
      retry++; timer = setTimeout(connect, Math.min(5000, 300 * retry));
    };
  }
  connect();
  return { close(){ closed = true; clearTimeout(timer); if (ws){ ws.onclose = null; try { ws.close(); } catch (e) {} } } };
}
