/* Local stand-in for the capabilities the published page gets from Claude.
   Loaded only by dev/serve.py. Data lives in this browser's localStorage, so it
   survives a reload and is shared between tabs, but it is NOT the team's real data.

   Pretend to be someone else:  http://localhost:8800/?as=bat
   Start clean:                 http://localhost:8800/?reset=1                      */
(() => {
  const q = new URLSearchParams(location.search);
  const KEY = 'cubic-dev-db';
  if (q.get('reset')) localStorage.removeItem(KEY);
  const me = (q.get('as') || 'obama').toLowerCase().replace(/[^a-z0-9]/g, '') || 'obama';
  const load = () => { try { return new Map(Object.entries(JSON.parse(localStorage.getItem(KEY) || '{}'))); } catch (e) { return new Map(); } };
  let docs = load();
  const persist = () => localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(docs)));
  const subs = [];
  const meta = { fromCache: false, hasPendingWrites: false };
  const docSnap = path => ({ id: path.split('/').pop(), exists: docs.has(path), data: () => docs.get(path), metadata: meta });
  const colSnap = path => {
    const depth = path.split('/').length + 1;
    const list = [...docs.keys()].filter(k => k.startsWith(path + '/') && k.split('/').length === depth).sort().map(docSnap);
    return { docs: list, size: list.length, empty: !list.length, docChanges: () => [], metadata: meta };
  };
  const fire = () => subs.forEach(s => s.cb(s.kind === 'doc' ? docSnap(s.path) : colSnap(s.path)));
  window.addEventListener('storage', e => { if (e.key === KEY) { docs = load(); fire(); } });
  const clone = v => JSON.parse(JSON.stringify(v));
  const merge = (a, b) => { for (const [k, v] of Object.entries(b)) a[k] = v && typeof v === 'object' && !Array.isArray(v) ? merge(a[k] && typeof a[k] === 'object' ? a[k] : {}, v) : v; return a; };
  let n = 0;
  const doc = path => ({
    id: path.split('/').pop(), path,
    get: async () => docSnap(path),
    set: async data => { docs.set(path, clone(data)); persist(); fire(); },
    update: async data => { if (!docs.has(path)) throw { code: 'invalid_argument', message: 'no such document' }; docs.set(path, merge(clone(docs.get(path)), clone(data))); persist(); fire(); },
    delete: async () => { docs.delete(path); persist(); fire(); },
    onSnapshot: cb => { const s = { kind: 'doc', path, cb }; subs.push(s); setTimeout(() => cb(docSnap(path)), 30); return () => subs.splice(subs.indexOf(s), 1); },
    collection: sub => collection(`${path}/${sub}`),
  });
  const collection = path => ({
    path,
    doc: id => doc(`${path}/${id || `dev${Date.now().toString(36)}${(++n).toString(36)}`}`),
    add: async data => { const d = collection(path).doc(); await d.set(data); return d; },
    get: async () => colSnap(path),
    onSnapshot: cb => { const s = { kind: 'col', path, cb }; subs.push(s); setTimeout(() => cb(colSnap(path)), 30); return () => subs.splice(subs.indexOf(s), 1); },
  });
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  const user = {
    id: async () => me, me: async () => ({ id: me, name: cap(me) }),
    isOwner: () => me === 'obama', canEdit: () => true, can: async () => (q.get('readonly') ? false : true),
    profiles: async ids => Object.fromEntries(ids.map(i => [i, { id: i, name: cap(i) }])),
  };
  // ?dl=1 pretends the downloads capability is granted; saved files are kept in window.__saved.
  const downloads = q.get('dl') ? { save: async f => { (window.__saved = window.__saved || []).push(f); return { status: 'saved' }; } } : null;
  window.claude = { use: async name => (name === 'db' ? { doc, collection } : name === 'user' ? user : name === 'downloads' ? downloads : null) };
  console.info(`[cubic dev] local data, signed in as "${me}". Add ?as=name to switch person, ?reset=1 to clear.`);
})();
