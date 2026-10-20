// Fork: odysseus-sound page — conversations list and "who is this?" cards.
// Data comes from routes/sound_module_routes.py, which calls the module's MCP tools.
// Transcript links are the module's signed /view links; they open in a new tab.

const $ = (sel) => document.querySelector(sel);

const OPEN_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5' +
  'a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" ' +
  'x2="21" y2="3"/></svg>';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null) continue;
    if (k === 'className') node.className = v;
    else if (k === 'html') node.innerHTML = v; // only for our own static SVG strings
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function safeLink(url) {
  return /^https?:\/\//.test(url || '') ? url : null;
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
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function status(target, text, error = false) {
  target.replaceChildren(el('div', { className: error ? 'bs-status error' : 'bs-status' }, text));
}

// ------------------------------------------------------------------ who is this?
function whoCard(s, onDone) {
  const input = el('input', { className: 'bs-input', placeholder: 'Name', 'aria-label': 'Name',
                              maxlength: '100' });
  const msg = el('div', { className: 'bs-meta' });
  const save = async (name) => {
    name = (name || '').trim();
    if (!name) {
      input.focus();
      return;
    }
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const r = await api('/api/modules/sound/name-speaker', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: s.conversation_id, speaker_label: s.label, name }),
      });
      msg.textContent = r.message;
      if (r.ok) setTimeout(onDone, 1500);
      else buttons.forEach((b) => { b.disabled = false; });
    } catch (e) {
      msg.textContent = `Could not save: ${e.message}`;
      buttons.forEach((b) => { b.disabled = false; });
    }
  };
  const buttons = [];
  if (s.guess) {
    buttons.push(el('button', { type: 'button', className: 'bs-btn primary',
                                onclick: () => save(s.guess) }, `It's ${s.guess}`));
  }
  buttons.push(el('button', { type: 'button', className: 'bs-btn', onclick: () => save(input.value) },
    'Save name'));
  buttons.push(el('button', { type: 'button', className: 'bs-btn',
                              onclick: () => card.remove() }, 'Skip'));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') save(input.value);
  });
  const quotes = s.quotes.map((q) => el('a', {
    className: 'bs-quote', href: safeLink(q.link), target: '_blank', rel: 'noopener',
    title: 'Open the transcript at this line',
  }, `“${q.text}”`));
  const card = el('div', { className: 'bs-card' },
    el('h2', {}, `${s.label}`),
    el('div', { className: 'bs-meta' },
      `${s.lines} lines in “${s.conversation_title}”, ${s.when}`
      + (s.guess ? ` · sounds like ${s.guess} (${s.guess_score.toFixed(2)})` : '')),
    ...quotes,
    el('div', { className: 'bs-row' }, input, ...buttons),
    msg);
  return card;
}

async function loadWho() {
  const box = $('#bs-who');
  try {
    const { speakers } = await api('/api/modules/sound/unnamed?limit=10');
    if (!speakers.length) {
      status(box, 'Everyone is named.');
      return;
    }
    box.replaceChildren(...speakers.map((s) => whoCard(s, () => { loadWho(); loadList(); })));
  } catch (e) {
    status(box, e.message, true);
  }
}

// ----------------------------------------------------------------- action items
// Live from odysseus-sound (no copies in Odysseus): Done / Dismiss call its
// update_action_item and the list is read again.
function itemCard(it) {
  const msg = el('span', { className: 'bs-meta' });
  const set = async (status) => {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const r = await api(`/api/modules/sound/action-items/${it.id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (r.ok) {
        card.remove();
        return;
      }
      msg.textContent = r.message;
    } catch (e) {
      msg.textContent = `Could not save: ${e.message}`;
    }
    buttons.forEach((b) => { b.disabled = false; });
  };
  const buttons = [
    el('button', { type: 'button', className: 'bs-btn primary', onclick: () => set('done') }, 'Done'),
    el('button', { type: 'button', className: 'bs-btn', onclick: () => set('dismissed') }, 'Dismiss'),
  ];
  const link = safeLink(it.link);
  const card = el('div', { className: 'bs-card' },
    el('h2', {}, it.text),
    el('div', { className: 'bs-meta' },
      [it.due ? `due ${it.due}` : null, it.owner ? `owner ${it.owner}` : null,
       `from “${it.conversation_title}”, ${it.when}`].filter(Boolean).join(' · ')),
    el('div', { className: 'bs-row' }, ...buttons,
      link ? el('a', { className: 'bs-link', href: link, target: '_blank', rel: 'noopener' },
        el('span', { html: OPEN_ICON }), 'Where it was said') : null,
      msg));
  return card;
}

async function loadItems() {
  const box = $('#bs-items');
  try {
    const { action_items: items } = await api('/api/modules/sound/action-items?status=open');
    if (!items.length) {
      status(box, 'No open action items.');
      return;
    }
    box.replaceChildren(...items.map(itemCard));
  } catch (e) {
    status(box, e.message, true);
  }
}

// ---------------------------------------------------------------- conversations
function convCard(c) {
  const link = safeLink(c.link);
  return el('div', { className: 'bs-card' },
    el('h2', {}, c.title),
    el('div', { className: 'bs-meta' },
      (c.voice_note ? 'Voice note · ' : '') +
      `${c.start}–${c.end}` + (c.people.length ? ` · ${c.people.join(', ')}` : '')),
    c.summary ? el('p', { className: 'bs-summary' }, c.summary) : null,
    link ? el('div', { className: 'bs-row' },
      el('a', { className: 'bs-link', href: link, target: '_blank', rel: 'noopener' },
        el('span', { html: OPEN_ICON }), 'Transcript')) : null);
}

async function loadList() {
  const box = $('#bs-list');
  const params = new URLSearchParams();
  for (const [k, v] of new FormData($('#bs-filter')).entries()) {
    if (String(v).trim()) params.set(k, String(v).trim());
  }
  params.set('limit', '30');
  status(box, 'Loading…');
  try {
    const { conversations } = await api(`/api/modules/sound/conversations?${params}`);
    if (!conversations.length) {
      status(box, params.get('kind') === 'note' ? 'No voice notes found.' : 'No conversations found.');
      return;
    }
    box.replaceChildren(...conversations.map(convCard));
  } catch (e) {
    status(box, e.message, true);
  }
}

$('#bs-filter').addEventListener('submit', (e) => {
  e.preventDefault();
  loadList();
});
$('#bs-refresh').addEventListener('click', () => {
  loadWho();
  loadItems();
  loadList();
});
loadWho();
loadItems();
loadList();
