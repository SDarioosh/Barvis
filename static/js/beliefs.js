// Fork: the belief review page, "What I know about you" — the belief ledger
// (src/evidence_intake.py) by status and subject, each belief with its sources and
// links, and the owner's buttons: confirm, edit (correct), forget, restore.
// Data: routes/beliefs_routes.py. Forget also asks odysseus-sound to retire the facts
// behind the belief; the toast says what the module did.

const $ = (sel) => document.querySelector(sel);

const TABS = [
  ['tentative', 'Tentative'],
  ['confirmed', 'Confirmed'],
  ['stale', 'Stale'],
  ['rejected', 'Rejected'],
];

const EMPTY = {
  tentative: 'Nothing tentative. Beliefs arrive from your recorded conversations and, when enabled, your notes.',
  confirmed: 'Nothing confirmed yet. Confirm a tentative belief here, or it confirms itself once it is heard often enough.',
  stale: 'Nothing stale. A belief goes stale when no source reports it any more or it has not been heard for a long time.',
  rejected: 'Nothing forgotten.',
};

const CONFIRMED_BY = {
  owner: 'Confirmed by you',
  source: 'Confirmed by its source',
  rule: 'Confirmed by the rule',
};

const SOURCE_NAMES = { 'odysseus-sound': 'Conversations', notes: 'Notes' };

const svg = (body) => '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
  `aria-hidden="true">${body}</svg>`;

const ICONS = {
  confirm: svg('<polyline points="20 6 9 17 4 12"/>'),
  edit: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
  forget: svg('<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>' +
              '<path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'),
  restore: svg('<polyline points="1 4 1 10 7 10"/><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/>'),
  link: svg('<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>' +
            '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>'),
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'className') node.className = v;
    else if (k === 'html') node.innerHTML = v; // only for our own static SVG strings
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('not logged in');
  }
  let data = {};
  try {
    data = await res.json();
  } catch (e) {}
  if (!res.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function postJSON(path, body) {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// ------------------------------------------------------------------ state
const state = { tab: 'tentative', beliefs: [], stats: null, filter: '' };
const TAB_KEY = 'fork-beliefs-tab';

try {
  const saved = sessionStorage.getItem(TAB_KEY);
  if (TABS.some(([key]) => key === saved)) state.tab = saved;
} catch (e) { /* storage blocked: start on Tentative */ }

let toastTimer = null;
function toast(text, error = false) {
  const t = $('#bl-toast');
  t.textContent = text;
  t.className = error ? 'bs-toast error' : 'bs-toast';
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, error ? 8000 : 5000);
}

function status(text, error = false) {
  $('#bl-main').replaceChildren(el('div', { className: error ? 'bs-status error' : 'bs-status' }, text));
}

// ------------------------------------------------------------------ formatting
function day(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return '';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

// Only web links and links into this app open; anything else is shown as text.
function safeHref(link) {
  const s = String(link || '').trim();
  if (/^https?:\/\//i.test(s)) return s;
  if (s.startsWith('/') && !s.startsWith('//')) return s;
  return null;
}

// ------------------------------------------------------------------ header
function renderCounts() {
  const s = state.stats || {};
  $('#bl-counts').textContent = TABS.map(([key, label]) => `${s[key] || 0} ${label.toLowerCase()}`)
    .join(' · ');
  const tabs = $('#bl-tabs');
  tabs.replaceChildren(...TABS.map(([key, label]) => el('button', {
    type: 'button', role: 'tab', className: 'bs-btn bs-tab', id: `bl-tab-${key}`,
    'aria-selected': String(key === state.tab),
    onclick: () => selectTab(key),
  }, label, ' ', el('span', { className: 'bs-count' }, String(s[key] || 0)))));
}

function selectTab(key) {
  if (key === state.tab) return;
  state.tab = key;
  try { sessionStorage.setItem(TAB_KEY, key); } catch (e) {}
  load();
}

// ------------------------------------------------------------------ cards
function statusChip(b) {
  if (b.status === 'confirmed') {
    return el('span', { className: 'bs-chip confirmed' },
      CONFIRMED_BY[b.confirmed_by] || 'Confirmed');
  }
  const label = { tentative: 'Tentative', stale: 'Stale', rejected: 'Forgotten' }[b.status];
  return el('span', { className: `bs-chip ${b.status}` }, label || b.status);
}

function sourceRow(e) {
  const name = SOURCE_NAMES[e.source] || e.source;
  const said = `${e.times || 1}× · ${plural(e.days || 1, 'day')}`;
  const when = [day(e.seen_first), day(e.seen_last)].filter(Boolean);
  const span = when.length === 2 && when[0] !== when[1] ? `${when[0]} – ${when[1]}` : (when[0] || '');
  const href = safeHref(e.link);
  return el('li', {},
    el('span', { className: 'bs-src', title: e.source }, name),
    el('span', {}, said),
    span ? el('span', {}, span) : null,
    e.gone ? el('span', { className: 'bs-gone' }, 'no longer reported') : null,
    href ? el('a', { href, target: '_blank', rel: 'noopener noreferrer',
                     title: 'Open where it came from (new tab)' },
              el('span', { html: ICONS.link }), 'Open') : null);
}

function button(icon, label, onclick, title) {
  return el('button', { type: 'button', className: 'bs-btn', title: title || label, onclick },
    el('span', { html: ICONS[icon] }), label);
}

async function act(card, path, body, done) {
  for (const b of card.querySelectorAll('button')) b.disabled = true;
  try {
    const result = await postJSON(path, body);
    if (done) done(result);
    await load(true);
  } catch (e) {
    for (const b of card.querySelectorAll('button')) b.disabled = false;
    toast(`Couldn't save: ${e.message}`, true);
  }
}

function forgetMessage(p) {
  const retired = p.retired || 0;
  const failed = p.failed || 0;
  const unsupported = p.unsupported || 0;
  const missed = failed + unsupported;
  if (!retired && !missed) return ['Forgotten.', false];
  if (retired && !missed) return [`Forgotten; the module retired ${plural(retired, 'fact')}.`, false];
  if (!retired && unsupported && !failed) {
    return ['Forgotten here; the module could not be reached or cannot retire facts yet. ' +
            'It stays forgotten here either way.', true];
  }
  if (!retired) return ['Forgotten here; the module could not retire it. It stays forgotten here.', true];
  return [`Forgotten; the module retired ${plural(retired, 'fact')}, ` +
          `${plural(missed, 'other')} could not be retired.`, true];
}

function forget(b, card) {
  const ok = window.confirm('Forget this? It leaves memory, and odysseus-sound is asked to ' +
                            'retire the facts behind it (that part cannot be undone).');
  if (!ok) return;
  act(card, `/api/beliefs/${b.id}/forget`, {}, (r) => {
    const [text, warn] = forgetMessage(r.propagated || {});
    toast(text, warn);
  });
}

function editMode(b, card, textNode) {
  const input = el('input', { type: 'text', className: 'bs-filter', value: b.shown_text,
                              'aria-label': 'Corrected text', maxlength: '500' });
  const save = () => {
    const text = input.value.trim();
    if (!text) {
      toast('A correction needs text.', true);
      return;
    }
    act(card, `/api/beliefs/${b.id}/correct`, { text }, () => toast('Saved; memory shows your text.'));
  };
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') save();
    if (ev.key === 'Escape') render();
  });
  const form = el('div', { className: 'bs-edit' }, input,
    el('button', { type: 'button', className: 'bs-btn', onclick: save }, 'Save'),
    el('button', { type: 'button', className: 'bs-btn', onclick: () => render() }, 'Cancel'));
  textNode.replaceWith(form);
  card.querySelector('.bs-actions').hidden = true;
  input.focus();
  input.select();
}

function actions(b, card, textNode) {
  const row = el('div', { className: 'bs-row bs-actions' });
  const confirm = (label) => button('confirm', label,
    () => act(card, `/api/beliefs/${b.id}/confirm`, {}, () => toast('Confirmed; it is in memory now.')));
  const edit = () => button('edit', 'Edit', () => editMode(b, card, textNode));
  const forgetBtn = () => button('forget', 'Forget', () => forget(b, card));
  if (b.status === 'tentative') row.append(confirm('Confirm'), edit(), forgetBtn());
  else if (b.status === 'confirmed') row.append(edit(), forgetBtn());
  else if (b.status === 'stale') {
    row.append(button('confirm', 'Confirm',
      () => act(card, `/api/beliefs/${b.id}/confirm`, {}, () => toast('Restored and confirmed.')),
      'Restore and keep it for good'), forgetBtn());
  } else if (b.status === 'rejected') {
    row.append(button('restore', 'Restore',
      () => act(card, `/api/beliefs/${b.id}/restore`, {}, () => toast('Restored as tentative.')),
      'Back to tentative; it is confirmed again once it is heard often enough'));
  }
  return row;
}

function beliefCard(b) {
  const card = el('div', { className: 'bs-card', id: `belief-${b.id}` });
  const textNode = el('p', { className: 'bs-text' }, b.shown_text,
    b.owner_text ? el('span', { className: 'bs-tag', title: `Originally: ${b.text}` }, 'edited') : null);
  const facts = el('div', { className: 'bs-facts' },
    statusChip(b),
    el('span', {}, b.category),
    b.first_seen ? el('span', {}, `first seen ${day(b.first_seen)}`) : null,
    b.status === 'stale'
      ? (b.last_seen ? el('span', {}, `not heard since ${day(b.last_seen)}`) : null)
      : (b.last_seen ? el('span', {}, `last seen ${day(b.last_seen)}`) : null),
    b.valid_until ? el('span', {}, `until ${day(b.valid_until)}`) : null,
    b.status === 'rejected' && b.note ? el('span', {}, `reason: ${b.note}`) : null);
  const evidence = Array.isArray(b.evidence) ? b.evidence : [];
  const sources = evidence.length
    ? el('ul', { className: 'bs-sources', 'aria-label': 'Sources' }, ...evidence.map(sourceRow))
    : null;
  card.append(textNode, facts, sources);
  card.append(actions(b, card, textNode));
  return card;
}

// ------------------------------------------------------------------ list
function render() {
  renderCounts();
  const main = $('#bl-main');
  const needle = state.filter.trim().toLowerCase();
  const rows = needle
    ? state.beliefs.filter(b => (b.subject || '').toLowerCase().includes(needle))
    : state.beliefs;
  if (!rows.length) {
    main.replaceChildren(el('div', { className: 'bs-empty' },
      needle && state.beliefs.length ? `No ${state.tab} beliefs about “${state.filter.trim()}”.`
                                     : EMPTY[state.tab]));
    return;
  }
  const groups = new Map();
  for (const b of rows) {
    const key = b.subject || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(b);
  }
  const parts = [];
  for (const [subject, items] of groups) {
    parts.push(el('div', { className: 'bs-section' }, subject || 'No subject'),
               ...items.map(beliefCard));
  }
  main.replaceChildren(...parts);
}

async function load(quiet = false) {
  if (!quiet) status('Loading…');
  try {
    const data = await api(`/api/beliefs?status=${encodeURIComponent(state.tab)}`);
    state.beliefs = Array.isArray(data.beliefs) ? data.beliefs : [];
    state.stats = data.stats || null;
    render();
  } catch (e) {
    renderCounts();
    status(`Couldn't load your beliefs: ${e.message}`, true);
  }
}

async function sync() {
  const btn = $('#bl-sync');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    const r = await api('/api/beliefs/run', { method: 'POST' });
    toast(r.summary || 'Synced.');
    await load(true);
  } catch (e) {
    toast(`Couldn't sync: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sync now';
  }
}

let filterTimer = null;
$('#bl-filter').addEventListener('input', (ev) => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => {
    state.filter = ev.target.value || '';
    render();
  }, 150);
});
$('#bl-sync').addEventListener('click', sync);
renderCounts();
load();
