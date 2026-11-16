'use strict';
/* vCenter Folder Replication - GUI. No framework, no build step. All text from vCenter
   is inserted with textContent (never innerHTML), so odd folder names can't inject markup. */

const TOKEN = new URLSearchParams(location.search).get('t') || '';
let S = null;                       // server state
let view = 'connect';               // connect | capture | plan | apply
const ui = { form: { source: { port: 443 }, target: { port: 443 } }, mode: { source: 'live' }, dc: null, sel: null,
             q: '', filter: 'all', busy: null, confirm: '', undoConfirm: '' };

const el = (tag, props, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== false && c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
};

async function api(path, body) {
  const opt = { headers: { 'X-VCFA-Token': TOKEN } };
  if (body !== undefined) { opt.method = 'POST'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ('Request failed (' + r.status + ')'));
  return data;
}
function toast(msg, good) {
  const t = el('div', { class: 'toast' + (good ? ' good' : '') }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), good ? 3500 : 9000);
}
async function act(label, fn) {
  ui.busy = label; render();
  try { const r = await fn(); if (r && r.version) S = r; }
  catch (e) { toast(e.message); }
  finally { ui.busy = null; }
  await refresh();
}
async function refresh() { S = await api('/api/state'); render(); if (S.job && S.job.state === 'running') setTimeout(refresh, 400); }

/* ---------- helpers ---------- */
const BADGE = { create: ['+', 'will be created'], exists: ['=', 'already exists'], conflict: ['!', 'conflict'],
                blocked: ['–', 'blocked'], missing_dc: ['✕', 'datacenter missing'], extra: ['•', 'only on target'] };
const badge = (s, text) => el('span', { class: 'badge b-' + s, title: BADGE[s][1] }, BADGE[s][0] + ' ' + (text || BADGE[s][1]));
const pstr = p => p.join('/');
const key = (dc, p) => JSON.stringify([dc, p]);
const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
function buildTree(paths) {
  const root = { children: new Map(), path: [] };
  for (const p of paths) { let n = root;
    p.forEach((seg, i) => { if (!n.children.has(seg)) n.children.set(seg, { name: seg, path: p.slice(0, i + 1), children: new Map() }); n = n.children.get(seg); }); }
  return root;
}
const sortedKids = n => [...n.children.values()].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

/* ---------- stepper ---------- */
const STEPS = [['connect', 'Connect'], ['capture', 'Capture'], ['plan', 'Review plan'], ['apply', 'Replicate']];
function reach(v) {
  const ready = S.source.connected && S.target.connected;
  return { connect: true, capture: ready, plan: ready && !!S.source.captured, apply: !!S.plan }[v];
}
function renderStepper() {
  const nav = document.getElementById('stepper'); nav.replaceChildren();
  STEPS.forEach(([v, name], i) => nav.append(el('button', {
    class: 'step' + (view === v ? ' active' : '') + (STEPS.findIndex(s => s[0] === view) > i && reach(v) ? ' done' : ''),
    disabled: !reach(v), 'aria-current': view === v ? 'step' : false, onclick: () => { view = v; render(); }
  }, el('span', { class: 'n' }, i + 1), name)));
}

/* ---------- connect ---------- */
function endpointCard(role) {
  const s = S[role], f = ui.form[role], isSrc = role === 'source';
  const title = isSrc ? 'Source vCenter' : 'Target vCenter';
  const card = el('div', { class: 'card' }, el('h3', {}, (isSrc ? '① ' : '② ') + title),
    el('p', { class: 'hint' }, isSrc ? 'Where the folder structure is read FROM (read-only).' : 'Where the folders will be created.'));
  if (s.connected) {
    const i = s.info || {};
    card.append(el('dl', { class: 'kv' },
      el('dt', {}, 'Host'), el('dd', {}, i.host || ''),
      el('dt', {}, 'Version'), el('dd', {}, el('span', { class: 'badge b-ver' }, 'vCenter ' + (i.version || '?')), i.build ? ' build ' + i.build : ''),
      el('dt', {}, 'Source'), el('dd', {}, s.kind === 'live' ? 'Live connection' : s.kind === 'demo' ? 'Simulated (demo)' : 'Capture file')),
      ...(i.warnings || []).map(w => el('div', { class: 'status warn' }, w)));
    if (!S.demo) card.append(el('div', { class: 'row' }, el('button', { class: 'btn', onclick: () => act('d', () => api('/api/disconnect', { role })) }, 'Disconnect')));
    return card;
  }
  if (isSrc) card.append(el('div', { class: 'tabs' },
    ['live', 'file'].map(m => el('button', { class: 'tab' + (ui.mode.source === m ? ' on' : ''), onclick: () => { ui.mode.source = m; render(); } },
      m === 'live' ? 'Connect to vCenter' : 'Load a capture file'))));
  if (isSrc && ui.mode.source === 'file') {
    card.append(el('p', { class: 'hint' }, 'Use a .json file saved earlier with the Export button (handy when the two vCenters cannot be reached from the same place).'),
      el('input', { type: 'file', accept: '.json,application/json', 'aria-label': 'Capture file', onchange: async e => {
        const file = e.target.files[0]; if (!file) return;
        let inv; try { inv = JSON.parse(await file.text()); } catch { return toast('That file is not valid JSON.'); }
        act('load', () => api('/api/load-capture', { inventory: inv })); } }));
    return card;
  }
  const inp = (k, lbl, type, ph) => [el('label', { for: role + k }, lbl),
    el('input', { id: role + k, type: type || 'text', value: f[k] ?? '', placeholder: ph || '', autocomplete: k === 'password' ? 'off' : 'on',
      oninput: e => { f[k] = e.target.value; } })];
  card.append(...inp('host', 'vCenter address', 'text', 'vcenter.example.com'), ...inp('user', 'Username', 'text', 'administrator@vsphere.local'),
    ...inp('password', 'Password', 'password'),
    el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!f.insecure, onchange: e => { f.insecure = e.target.checked; } }),
      el('span', {}, 'Accept self-signed certificate ', el('span', { class: 'muted' }, '(only if you trust this vCenter; the connection is still encrypted)'))),
    el('div', { class: 'row', style: 'margin-top:.9rem' }, el('button', { class: 'btn primary', disabled: !!ui.busy, onclick: () => {
      const body = { role, host: f.host, user: f.user, password: f.password, port: f.port, insecure: !!f.insecure };
      act('c-' + role, async () => { const r = await api('/api/connect', body); f.password = ''; return r; }); } },
      ui.busy === 'c-' + role ? [el('span', { class: 'spin' }), ' Connecting…'] : 'Connect')),
    el('p', { class: 'hint' }, 'Tip: use an account with only read access for the source, and a dedicated account with just the "Folder › Create folder" privilege for the target.'));
  return card;
}
function renderConnect(m) {
  if (!S.demo) m.append(el('div', { class: 'card hero' }, el('h2', {}, 'Welcome'),
    el('p', {}, 'This tool reads the VM folder tree from one vCenter and recreates it, one-to-one, on another — vCenter 8.0 or 9.0, in either direction. ',
      'It shows you exactly what it will do before it does anything.'),
    el('div', { class: 'row' }, el('button', { class: 'btn primary', onclick: () => act('demo', () => api('/api/demo', { enable: true })) }, '▶ Try demo mode'),
      el('span', { class: 'muted' }, 'No vCenter needed — two simulated environments with a guided walkthrough.'))));
  m.append(el('div', { class: 'grid2' }, endpointCard('source'), endpointCard('target')));
  const ready = S.source.connected && S.target.connected;
  m.append(el('div', { class: 'row end' }, ready ? el('span', { class: 'badge b-ok' }, '✓ Both sides ready') : el('span', { class: 'muted' }, 'Connect both sides to continue'),
    el('button', { class: 'btn primary', disabled: !ready, onclick: () => { view = 'capture'; render(); } }, 'Next: Capture →')));
}

/* ---------- capture ---------- */
function statsRow(sum) {
  return el('div', { class: 'stats' }, [['datacenters', 'datacenter'], ['folders', 'folder'], ['vms', 'VM']].map(([k, w]) =>
    el('div', { class: 'stat' }, el('b', {}, sum[k]), el('span', {}, k === 'vms' ? 'virtual machines' : w + 's'))));
}
let invCache = null;
async function loadInv(role) { invCache = await api('/api/inventory?role=' + role); }

function treeView(dcInv, opts) {
  /* opts: statusOf(path)->{status,reason}|null, onSelect(path), q, vmCount(path) */
  const q = (opts.q || '').toLowerCase();
  const vmsBy = new Map(); for (const v of dcInv.vms) { const k = pstr(v.path); (vmsBy.get(k) || vmsBy.set(k, []).get(k)).push(v); }
  let paths = dcInv.folders;
  if (opts.extraPaths) paths = paths.concat(opts.extraPaths);
  let keep = null;
  if (q) { keep = new Set();
    for (const p of paths) { const hit = pstr(p).toLowerCase().includes(q) || (vmsBy.get(pstr(p)) || []).some(v => v.name.toLowerCase().includes(q));
      if (hit) for (let i = 1; i <= p.length; i++) keep.add(pstr(p.slice(0, i))); } }
  const root = buildTree(paths), box = el('div', { class: 'tree', role: 'tree' });
  const draw = (n, into, depth) => {
    for (const k of sortedKids(n)) {
      if (keep && !keep.has(pstr(k.path))) continue;
      const st = opts.statusOf && opts.statusOf(k.path); if (opts.hide && st && opts.hide(st)) { if (!k.children.size) continue; }
      const vc = (vmsBy.get(pstr(k.path)) || []).length;
      const label = [el('span', { class: 'nm' }, '📁 ' + k.name), vc ? el('span', { class: 'cnt' }, plural(vc, 'VM')) : null,
        st ? badge(st.status) : null, st && st.reason ? el('span', { class: 'why' }, st.reason) : null];
      const selected = ui.sel && pstr(ui.sel) === pstr(k.path);
      const hasKids = [...k.children.keys()].length > 0;
      if (hasKids) { const d = el('details', { open: depth < 2 || !!q }, el('summary', { class: selected ? 'sel' : '', onclick: () => opts.onSelect && opts.onSelect(k.path) }, label));
        draw(k, d, depth + 1); into.append(d); }
      else into.append(el('div', { class: 'leaf' + (selected ? ' sel' : ''), onclick: () => opts.onSelect && opts.onSelect(k.path) }, label));
    }
  };
  draw(root, box, 0);
  const rootVms = vmsBy.get('') || [];
  if (rootVms.length && !q) box.prepend(el('div', { class: 'leaf', style: 'margin-left:0', onclick: () => opts.onSelect && opts.onSelect([]) },
    el('span', { class: 'nm' }, '🗂 (datacenter root)'), el('span', { class: 'cnt' }, plural(rootVms.length, 'VM'))));
  if (!box.children.length) box.append(el('p', { class: 'muted' }, q ? 'Nothing matches your search.' : 'No folders.'));
  return box;
}
function vmPanel(dcInv) {
  const here = ui.sel ? dcInv.vms.filter(v => pstr(v.path) === pstr(ui.sel)) : [];
  const head = el('h3', {}, ui.sel ? (ui.sel.length ? pstr(ui.sel) : '(datacenter root)') : 'Select a folder');
  if (!ui.sel) return el('div', {}, head, el('p', { class: 'muted' }, 'Click a folder on the left to see the virtual machines inside it.'));
  if (!here.length) return el('div', {}, head, el('p', { class: 'muted' }, 'No VMs directly in this folder.'));
  return el('div', {}, head, el('div', { class: 'scroll' }, el('table', {}, el('thead', {}, el('tr', {}, ['Name', 'Power', 'Guest OS'].map(h => el('th', {}, h)))),
    el('tbody', {}, here.map(v => el('tr', {}, el('td', {}, v.name + (v.template ? ' (template)' : '')), el('td', {}, (v.power || '').replace('powered', '')), el('td', {}, v.guest || '')))))));
}
function dcTabs(inv, rerender) {
  if (!ui.dc || !inv.datacenters.some(d => d.name === ui.dc)) ui.dc = inv.datacenters[0] && inv.datacenters[0].name;
  return el('div', { class: 'tabs' }, inv.datacenters.map(d => el('button', { class: 'tab' + (d.name === ui.dc ? ' on' : ''),
    onclick: () => { ui.dc = d.name; ui.sel = null; rerender(); } }, '🏢 ' + d.name)));
}
function renderCapture(m) {
  const s = S.source;
  m.append(el('div', { class: 'card' }, el('h2', {}, 'Capture the source inventory'),
    el('p', {}, 'Reads every datacenter’s VM folders and VMs from ', el('b', {}, s.info.host), '. This is ', el('b', {}, 'read-only'), ' — nothing is changed.'),
    el('div', { class: 'row' },
      s.kind !== 'file' && el('button', { class: 'btn primary', disabled: !!ui.busy, onclick: () => act('cap', async () => { const r = await api('/api/capture', { role: 'source' }); await loadInv('source'); return r; }) },
        ui.busy === 'cap' ? [el('span', { class: 'spin' }), ' Capturing…'] : (s.captured ? 'Capture again' : 'Capture inventory')),
      s.captured && [el('a', { class: 'btn', href: '/api/export?role=source&format=json&t=' + TOKEN, download: true }, '⬇ Save capture (.json)'),
        el('a', { class: 'btn', href: '/api/export?role=source&format=csv&t=' + TOKEN, download: true }, '⬇ Spreadsheet (.csv)')])));
  if (s.captured) {
    if (!invCache) { loadInv('source').then(render); return; }
    const card = el('div', { class: 'card' }, el('h3', {}, 'What was found'), statsRow(s.captured));
    ((s.info && s.info.warnings) || []).forEach(w => card.append(el('div', { class: 'status warn' }, w)));
    card.append(dcTabs(invCache, render),
      el('input', { type: 'search', placeholder: 'Search folders or VMs…', 'aria-label': 'Search', value: ui.q, oninput: e => { ui.q = e.target.value; const c = e.target.selectionStart; render(); const n = document.querySelector('input[type=search]'); if (n) { n.focus(); n.setSelectionRange(c, c); } } }));
    const dc = invCache.datacenters.find(d => d.name === ui.dc);
    if (dc) card.append(el('div', { class: 'tree-wrap', style: 'margin-top:.7rem' },
      treeView(dc, { q: ui.q, onSelect: p => { ui.sel = p; render(); } }), vmPanel(dc)));
    m.append(card);
  }
  m.append(el('div', { class: 'row end' }, el('button', { class: 'btn', onclick: () => { view = 'connect'; render(); } }, '← Back'),
    el('button', { class: 'btn primary', disabled: !s.captured, onclick: () => act('plan', async () => { const r = await api('/api/plan', {}); view = 'plan'; return r; }) },
      ui.busy === 'plan' ? [el('span', { class: 'spin' }), ' Reading target…'] : 'Next: Build plan →')));
}

/* ---------- plan ---------- */
function renderPlan(m) {
  const p = S.plan;
  if (!p) { m.append(el('div', { class: 'card' }, el('p', {}, 'No plan yet.'), el('button', { class: 'btn primary', onclick: () => act('plan', () => api('/api/plan', {})) }, 'Build plan'))); return; }
  const c = p.summary;
  const card = el('div', { class: 'card' }, el('h2', {}, 'Review the plan'),
    el('p', {}, 'Comparing ', el('b', {}, p.source_host), ' → ', el('b', {}, p.target_host), '. The target was just re-read, so this is up to date. ', el('b', {}, 'Nothing has been changed yet.')),
    el('div', { class: 'stats' }, [['create', 'to create'], ['exists', 'already exist'], ['conflict', 'conflicts'], ['blocked', 'blocked'], ['missing_dc', 'datacenters missing'], ['extra', 'only on target']].map(([k, l]) =>
      el('div', { class: 'stat s-' + k }, el('b', {}, c[k]), el('span', {}, l)))));
  p.datacenters.filter(d => d.status === 'missing_dc').forEach(d => card.append(el('div', { class: 'status warn' }, badge('missing_dc', d.name), ' ' + d.reason)));
  if (c.conflict || c.blocked) card.append(el('div', { class: 'status warn' }, 'Conflicts and blocked folders are ', el('b', {}, 'skipped'), ' (the tool never renames or merges). Fix the name on the target in vCenter, then rebuild the plan.'));
  const filt = [['all', 'Everything'], ['create', 'To create'], ['exists', 'Existing'], ['issues', 'Problems']];
  card.append(el('div', { class: 'tabs' }, filt.map(([k, l]) => el('button', { class: 'tab' + (ui.filter === k ? ' on' : ''), onclick: () => { ui.filter = k; render(); } }, l))));
  const dcs = p.datacenters.filter(d => d.status === 'ok').map(d => d.name);
  if (!dcs.length) card.append(el('p', { class: 'muted' }, 'No datacenter exists on both sides.'));
  else {
    if (!ui.dc || !dcs.includes(ui.dc)) ui.dc = dcs[0];
    card.append(el('div', { class: 'tabs' }, dcs.map(n => el('button', { class: 'tab' + (n === ui.dc ? ' on' : ''), onclick: () => { ui.dc = n; render(); } }, '🏢 ' + n))));
    const smap = new Map(p.actions.filter(a => a.dc === ui.dc).map(a => [pstr(a.path), a]));
    const extras = p.extras.filter(e => e.dc === ui.dc);
    const emap = new Map(extras.map(e => [pstr(e.path), { status: 'extra', reason: '' }]));
    const fakeDc = { folders: p.actions.filter(a => a.dc === ui.dc).map(a => a.path), vms: [] };
    const show = st => ui.filter === 'all' ? true : ui.filter === 'issues' ? ['conflict', 'blocked'].includes(st.status) : st.status === ui.filter;
    const statusOf = path => smap.get(pstr(path)) || emap.get(pstr(path));
    card.append(treeView(fakeDc, { statusOf, extraPaths: extras.map(e => e.path), hide: st => !show(st), onSelect: () => {} }));
  }
  m.append(card);
  m.append(el('div', { class: 'row end' }, el('button', { class: 'btn', onclick: () => { view = 'capture'; render(); } }, '← Back'),
    el('button', { class: 'btn', onclick: () => act('plan', () => api('/api/plan', {})) }, '↻ Rebuild plan'),
    el('button', { class: 'btn primary', onclick: () => { view = 'apply'; render(); } }, 'Next: Replicate →')));
}

/* ---------- apply ---------- */
function logBox(job) {
  const box = el('div', { class: 'log', role: 'log' }, (job.log || []).map(l => el('div', { class: l.result }, l.message)));
  setTimeout(() => { box.scrollTop = box.scrollHeight; }); return box;
}
function renderApply(m) {
  const p = S.plan, n = p ? p.summary.create : 0, job = S.job, running = job && job.state === 'running';
  const card = el('div', { class: 'card' }, el('h2', {}, 'Replicate the folders'));
  if (!p) { card.append(el('p', {}, 'Build a plan first.')); m.append(card); return; }
  card.append(el('p', {}, n ? [el('b', {}, plural(n, 'folder')), ' will be created on ', el('b', {}, p.target_host), '.'] : 'Nothing to create — the target already matches the source (for everything that can be replicated).'),
    el('ul', {}, el('li', {}, 'Only empty folders are created. Nothing is renamed, moved or deleted. No VM is touched.'),
      el('li', {}, 'Right before the run the target is re-read; anything that appeared in the meantime is skipped, never duplicated.'),
      el('li', {}, 'Every change is recorded in an audit log, and Undo can remove exactly what this run created (only while still empty).')));
  if (n && !running) {
    card.append(el('div', { class: 'row' }, el('button', { class: 'btn', disabled: !!ui.busy, onclick: () => act('dry', () => api('/api/apply', { dry_run: true })) }, '1. Dry run (changes nothing)')));
    card.append(el('label', { for: 'cf' }, '2. When you are happy, type REPLICATE to enable the real run'),
      el('div', { class: 'row' }, el('input', { id: 'cf', type: 'text', value: ui.confirm, placeholder: 'REPLICATE', style: 'max-width:14rem', autocomplete: 'off',
        oninput: e => { ui.confirm = e.target.value; document.getElementById('go').disabled = ui.confirm.trim() !== 'REPLICATE'; } }),
        el('button', { id: 'go', class: 'btn primary', disabled: ui.confirm.trim() !== 'REPLICATE', onclick: () => act('apply', async () => { const r = await api('/api/apply', { dry_run: false, confirm: ui.confirm.trim() }); ui.confirm = ''; return r; }) },
          'Create ' + plural(n, 'folder'))));
  }
  if (job && job.kind) {
    const pct = job.total ? Math.round(100 * job.done / job.total) : (job.state === 'done' ? 100 : 0);
    const title = job.kind === 'undo' ? 'Undo' : (job.result && job.result.dry_run) ? 'Dry run' : 'Replication';
    card.append(el('h3', { style: 'margin-top:1.1rem' }, title, running ? ' — working…' : ''),
      el('div', { class: 'bar', role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, el('i', { style: 'width:' + pct + '%' })), logBox(job));
    const r = job.result;
    if (job.state === 'error') card.append(el('div', { class: 'status warn' }, 'Stopped: ' + r.error));
    else if (r && job.state === 'done') {
      const k = r.counts, ok = r.ok !== false;
      card.append(el('div', { class: 'status' + (ok ? '' : ' warn') }, ok ? '✓ ' : '⚠ ',
        r.kind === 'rollback' ? `${k.removed} removed, ${k.kept} kept (not empty), ${k.gone} already gone, ${k.failed} failed.`
          : r.dry_run ? `Dry run: ${k.would_create} folders would be created. Nothing was changed.`
          : `${k.created} created, ${k.skipped} skipped, ${k.failed} failed. ${r.aborted || ''}`,
        r.log_file ? el('div', { class: 'hint' }, 'Audit log: ' + r.log_file) : null));
      if (r.kind === 'apply' && !r.dry_run && ok) card.append(el('p', { class: 'hint' }, 'Tip: the plan above has been refreshed from the target — go back to "Review plan" to confirm everything now shows as existing.'));
    }
  }
  m.append(card);
  if (S.can_undo && !running) m.append(el('div', { class: 'card' }, el('h3', {}, 'Undo this run'),
    el('p', {}, 'Removes only the folders the last run created, and only if they are still empty. Anything that has since received VMs or sub-folders is kept.'),
    el('div', { class: 'row' }, el('input', { type: 'text', placeholder: 'type UNDO', style: 'max-width:12rem', value: ui.undoConfirm, autocomplete: 'off',
      oninput: e => { ui.undoConfirm = e.target.value; document.getElementById('undo').disabled = ui.undoConfirm.trim() !== 'UNDO'; } }),
      el('button', { id: 'undo', class: 'btn danger', disabled: ui.undoConfirm.trim() !== 'UNDO', onclick: () => act('undo', async () => { const r = await api('/api/undo', { confirm: 'UNDO' }); ui.undoConfirm = ''; return r; }) }, 'Undo'))));
  m.append(el('div', { class: 'row end' }, el('button', { class: 'btn', onclick: () => { view = 'plan'; render(); } }, '← Back to plan')));
}

/* ---------- demo coach ---------- */
const COACH = {
  connect: ['Welcome to demo mode 👋', [
    'Both vCenters here are <b>simulated in memory</b>. The left one pretends to be <b>vCenter 8.0</b> (source) and the right one <b>vCenter 9.0</b> (target). No network traffic, nothing real is touched.',
    'In real use you would type an address, username and password into these two cards. Credentials are kept in memory only.',
    'The simulated source has 3 datacenters, 25+ folders and 20 VMs. The target is half-built on purpose, so you will see every kind of result.'],
    'Click <b>Next: Capture</b> to continue.'],
  capture: ['Step 1: capture (read-only)', [
    'Press <b>Capture inventory</b>. The tool lists every datacenter, every VM folder and every VM — it only <i>reads</i>.',
    'Click folders to see the VMs inside. Try the search box (e.g. <code>sql</code>).',
    '<b>Save capture</b> writes a .json file you can keep as documentation, or load later on another machine. The .csv opens in Excel.'],
    'When it looks right, click <b>Next: Build plan</b>.'],
  plan: ['Step 2: the plan (still read-only)', [
    'The tool re-reads the target and compares. Each folder gets one label:',
    '<b>+ create</b> – missing on target.<br><b>= exists</b> – already there.<br><b>! conflict</b> – e.g. target has <code>development</code> but source has <code>Development</code>. It is skipped and never renamed.<br><b>– blocked</b> – inside a conflict, so skipped too.',
    '<b>✕ datacenter missing</b> – <code>DC-Branch</code> isn’t on the target. The tool never creates datacenters.',
    '<b>• only on target</b> – <code>Lab-Sandbox</code> is left completely alone.'],
    'Use the filter tabs, then continue to <b>Replicate</b>.'],
  apply: ['Step 3: replicate (the only step that writes)', [
    'Do the <b>Dry run</b> first — it lists exactly what would happen and changes nothing.',
    'Then type <code>REPLICATE</code> and press <b>Create folders</b>. Watch the progress bar and log.',
    'Afterwards go back to <b>Review plan</b>: everything should now say "exists" (conflicts remain because they need a human decision).',
    'Finally try <b>Undo</b> — it removes just the folders this run created. Use <b>Reset demo</b> any time to start over.'],
    'On a real vCenter the exact same screens appear.']
};
function renderCoach() {
  const c = document.getElementById('coach'), lay = document.querySelector('.layout');
  c.hidden = !S.demo; lay.classList.toggle('has-coach', S.demo);
  document.getElementById('demoPill').hidden = !S.demo;
  if (!S.demo) return;
  const [title, paras, next] = COACH[view];
  c.replaceChildren(el('h3', {}, '🎓 ', title));
  const html = (tag, h) => { const e = el(tag); e.innerHTML = h; return e; };  // static strings written above only
  paras.forEach(h => c.append(html('p', h)));
  c.append(html('div', '<b>Next:</b> ' + next));
  c.lastChild.className = 'peek';
  c.append(el('div', { class: 'row' }, el('button', { class: 'btn', onclick: () => { invCache = null; ui.sel = null; act('demo', () => api('/api/demo', { enable: true })).then(() => { view = 'connect'; render(); }); } }, '↺ Reset demo'),
    el('button', { class: 'btn', onclick: () => { invCache = null; act('demo', () => api('/api/demo', { enable: false })).then(() => { view = 'connect'; render(); }); } }, 'Exit demo')));
}

/* ---------- main ---------- */
function render() {
  if (!S) return;
  document.getElementById('ver').textContent = 'v' + S.version;
  if (!reach(view)) view = 'connect';
  if (!S.source.captured) invCache = null;
  renderStepper(); renderCoach();
  const m = document.getElementById('main'); m.replaceChildren();
  ({ connect: renderConnect, capture: renderCapture, plan: renderPlan, apply: renderApply })[view](m);
}
refresh().catch(e => { document.getElementById('main').textContent = 'Cannot reach the app: ' + e.message + '. Open the exact link printed in the terminal.'; });
