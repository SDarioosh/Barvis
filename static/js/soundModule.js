// Fork: odysseus-sound page — conversations list, "who is this?" cards and a
// transcript reader (#c=<conversation id>) with click-to-fix speakers.
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
// Text for the main page's ?ask= handler (static/js/forkChatAsk.js): it fills a new
// chat's composer and never sends by itself.
function askHref(title, id, start) {
  const text = `/sound About the conversation "${title}" (id ${id}, ${start}): `;
  return `/?ask=${encodeURIComponent(text)}`;
}

function askButton(title, id, start) {
  return el('a', { className: 'bs-btn', href: askHref(title, id, start),
                   title: 'Start a new chat about this conversation' }, 'Ask in chat');
}

function convCard(c) {
  const link = safeLink(c.link);
  return el('div', { className: 'bs-card' },
    el('h2', {}, c.title),
    el('div', { className: 'bs-meta' },
      (c.voice_note ? 'Voice note · ' : '') +
      `${c.start}–${c.end}` + (c.people.length ? ` · ${c.people.join(', ')}` : '')),
    c.summary ? el('p', { className: 'bs-summary' }, c.summary) : null,
    el('div', { className: 'bs-row' },
      el('button', { type: 'button', className: 'bs-btn primary',
                     onclick: () => openReader(c.id, c.start) }, 'Open'),
      askButton(c.title, c.id, c.start),
      link ? el('a', { className: 'bs-link', href: link, target: '_blank', rel: 'noopener' },
        el('span', { html: OPEN_ICON }), 'Transcript') : null));
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

// ------------------------------------------------------------------- reader
// One conversation: its detail (get_conversation) and transcript (get_transcript),
// deep-linkable as #c=<id> so reload and the browser back button work.
const SPEAKER_LABEL = /^Speaker \d+$/;
const reader = {
  id: null,        // conversation shown
  start: '',       // its start ("YYYY-MM-DD HH:MM"), for "Ask in chat"
  gen: 0,          // bumps on every open/reload; late answers of older ones are dropped
  detail: null,
  lines: [],
  seen: new Set(), // line ids already shown (load more dedupes by id)
  nextStart: null,
  pages: 0,
  notice: '',
  listScroll: 0,
  fromList: false,
};

function hashId() {
  const m = /^#c=(.+)$/.exec(window.location.hash || '');
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch (e) {
    return null;
  }
}

function openReader(id, start = '') {
  reader.listScroll = window.scrollY;
  reader.fromList = true;
  reader.start = start;
  window.location.hash = `c=${encodeURIComponent(id)}`; // -> route()
}

function closeReader() {
  if (reader.fromList) {
    window.history.back(); // -> hashchange -> route()
    return;
  }
  window.history.pushState(null, '', window.location.pathname + window.location.search);
  route();
}

function linkSmall(url, label = 'transcript') {
  const href = safeLink(url);
  return href ? el('a', { className: 'bs-small', href, target: '_blank', rel: 'noopener',
                          title: 'Open the module transcript here' }, label) : null;
}

function startOf(when) {
  const m = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.exec(when || '');
  return m ? m[0] : '';
}

function namesOf(d) {
  const names = new Set([...(d ? d.speakers : []), ...(d ? d.people : [])]);
  for (const ln of reader.lines) names.add(ln.speaker);
  return [...names].filter((n) => n && n !== '—' && !SPEAKER_LABEL.test(n)).sort();
}

function renderReader() {
  const box = $('#bs-reader');
  const d = reader.detail;
  const title = d ? d.title : 'Conversation';
  const start = reader.start || (d ? startOf(d.when) : '');
  const top = el('div', { className: 'bs-reader-top' },
    el('button', { type: 'button', className: 'bs-btn', onclick: closeReader }, '← Back'),
    el('h2', {}, title));
  const actions = el('div', { className: 'bs-row' },
    d ? askButton(d.title, d.id, start) : null,
    d && safeLink(d.link) ? el('a', { className: 'bs-link', href: safeLink(d.link), target: '_blank',
                                      rel: 'noopener' }, el('span', { html: OPEN_ICON }), 'Transcript')
      : null);
  const parts = [top];
  if (reader.notice) parts.push(el('div', { className: 'bs-notice', role: 'status' }, reader.notice));
  if (d) {
    const people = d.people.filter((p) => p !== '—');
    const card = el('div', { className: 'bs-card' },
      el('div', { className: 'bs-meta' }, d.when + (people.length ? ` · ${people.join(', ')}` : '')),
      d.summary ? el('p', { className: 'bs-summary' }, d.summary) : null,
      actions);
    if (d.key_points.length) {
      card.append(el('div', { className: 'bs-sub' }, 'Key points'),
        el('ul', { className: 'bs-list' }, ...d.key_points.map((k) => el('li', {}, k))));
    }
    if (d.action_items.length) {
      card.append(el('div', { className: 'bs-sub' }, 'Action items'),
        el('ul', { className: 'bs-list' }, ...d.action_items.map((it) => el('li', {},
          `[${it.status}] ${it.text}`
          + (it.owner ? ` (owner ${it.owner})` : '') + (it.due ? ` · due ${it.due}` : ''),
          linkSmall(it.link)))));
    }
    if (d.facts.length) {
      card.append(el('div', { className: 'bs-sub' }, 'Facts'),
        el('ul', { className: 'bs-list' }, ...d.facts.map((f) => el('li', {},
          f.subject ? `${f.subject}: ${f.fact}` : f.fact, linkSmall(f.link)))));
    }
    parts.push(card);
  } else {
    parts.push(el('div', { id: 'bs-detail-status' }, el('div', { className: 'bs-status' }, 'Loading…')));
  }
  parts.push(el('div', { className: 'bs-section' }, 'Transcript'));
  parts.push(el('div', { className: 'bs-meta' }, 'Tap a speaker name to fix who said a line.'));
  const datalist = el('datalist', { id: 'bs-names' }, ...namesOf(d).map((n) => el('option', { value: n })));
  const lines = el('div', { className: 'bs-lines', id: 'bs-lines' });
  if (reader.lines.length) lines.append(...reader.lines.map(lineRow));
  else lines.append(el('div', { className: 'bs-status' },
    reader.pages ? 'No transcript lines.' : 'Loading…'));
  parts.push(datalist, lines);
  if (reader.nextStart !== null) {
    parts.push(el('div', { className: 'bs-row' },
      el('button', { type: 'button', className: 'bs-btn', onclick: (e) => loadMore(e.currentTarget) },
        'Load more')));
  }
  parts.push(el('div', { id: 'bs-reader-status' }));
  box.replaceChildren(...parts);
}

function lineRow(ln) {
  const href = safeLink(ln.link);
  const spk = el('button', { type: 'button', className: 'bs-spk', 'aria-expanded': 'false',
                             title: 'Fix who said this' }, ln.speaker);
  const row = el('div', { className: 'bs-line' },
    href ? el('a', { className: 'bs-time', href, target: '_blank', rel: 'noopener',
                     title: 'Open the module transcript here' }, ln.time)
      : el('span', { className: 'bs-time' }, ln.time),
    spk,
    el('span', { className: 'bs-text' }, ln.text));
  spk.addEventListener('click', () => {
    const open = row.querySelector('.bs-edit');
    document.querySelectorAll('.bs-edit').forEach((n) => n.remove());
    document.querySelectorAll('.bs-spk[aria-expanded="true"]')
      .forEach((b) => b.setAttribute('aria-expanded', 'false'));
    if (open) return; // second tap closes
    spk.setAttribute('aria-expanded', 'true');
    const ed = speakerEditor(ln, () => {
      ed.remove();
      spk.setAttribute('aria-expanded', 'false');
      spk.focus();
    });
    row.append(ed);
    ed.querySelector('input').focus();
  });
  return row;
}

function speakerEditor(ln, close) {
  const label = ln.speaker;
  const unnamed = SPEAKER_LABEL.test(label);
  const input = el('input', { className: 'bs-input', list: 'bs-names', maxlength: '100',
                              placeholder: unnamed ? 'Name' : `Name (now ${label})`,
                              'aria-label': `New name for ${label}`, autocomplete: 'off' });
  const msg = el('div', { className: 'bs-meta', role: 'status' });
  const buttons = [];
  const run = async (kind) => {
    const name = input.value.trim();
    if (!name) {
      msg.textContent = 'Type a name first.';
      input.focus();
      return;
    }
    let path;
    let body;
    if (kind === 'line') {
      path = `/api/modules/sound/lines/${encodeURIComponent(ln.id)}`;
      body = { name };
    } else if (kind === 'label') {
      path = '/api/modules/sound/name-speaker';
      body = { conversation_id: reader.id, speaker_label: label, name };
    } else {
      if (name === label) {
        msg.textContent = `That is already "${label}".`;
        return;
      }
      const sure = window.confirm(
        `Rename "${label}" to "${name}" in ALL conversations?\n\n`
        + `If "${name}" already exists, the two people are merged into one.\n`
        + 'This can\'t be undone.');
      if (!sure) return;
      path = '/api/modules/sound/rename-speaker';
      body = { old_name: label, new_name: name };
    }
    buttons.forEach((b) => { b.disabled = true; });
    msg.textContent = 'Saving…';
    try {
      const r = await api(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (r.ok) {
        reader.notice = r.message;
        await reloadReader();
        return;
      }
      msg.textContent = r.message;
    } catch (e) {
      msg.textContent = `Could not save: ${e.message}`;
    }
    buttons.forEach((b) => { b.disabled = false; });
  };
  buttons.push(el('button', { type: 'button', className: 'bs-btn primary', onclick: () => run('line') },
    'Only this line'));
  if (unnamed) {
    buttons.push(el('button', { type: 'button', className: 'bs-btn', onclick: () => run('label') },
      `Every line of ${label} here`));
  } else {
    buttons.push(el('button', { type: 'button', className: 'bs-btn', onclick: () => run('everywhere'),
                                title: 'Renames this speaker in all conversations' }, 'Rename everywhere'));
  }
  buttons.push(el('button', { type: 'button', className: 'bs-btn', onclick: close }, 'Cancel'));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') run('line');
    else if (e.key === 'Escape') close();
  });
  return el('div', { className: 'bs-edit' },
    el('div', { className: 'bs-meta' }, `Who says this instead of ${label}?`),
    el('div', { className: 'bs-row' }, input, ...buttons),
    msg);
}

async function fetchPage(id, start) {
  return api(`/api/modules/sound/conversations/${encodeURIComponent(id)}/transcript`
    + `?start_seconds=${encodeURIComponent(start)}`);
}

function addLines(page) {
  for (const ln of page.lines) {
    if (reader.seen.has(ln.id)) continue;
    reader.seen.add(ln.id);
    reader.lines.push(ln);
  }
  reader.nextStart = page.more && page.next_start !== null && page.next_start !== undefined
    ? page.next_start : null;
  reader.pages += 1;
}

async function loadMore(btn) {
  if (reader.nextStart === null) return;
  const gen = reader.gen;
  btn.disabled = true;
  btn.textContent = 'Loading…';
  try {
    const before = reader.lines.length;
    const page = await fetchPage(reader.id, reader.nextStart);
    if (gen !== reader.gen) return;
    addLines(page);
    // no new line although the module says there are more: stop instead of looping
    if (reader.lines.length === before) reader.nextStart = null;
    const y = window.scrollY;
    renderReader();
    window.scrollTo(0, y);
  } catch (e) {
    if (gen !== reader.gen) return;
    btn.disabled = false;
    btn.textContent = 'Load more';
    status($('#bs-reader-status'), e.message, true);
  }
}

// Loads detail and the transcript; keepPages > 1 reloads as many pages as were shown.
async function loadReader(id, keepPages = 1) {
  const gen = ++reader.gen;
  const [detail, transcript] = await Promise.allSettled([
    api(`/api/modules/sound/conversations/${encodeURIComponent(id)}`),
    (async () => {
      const acc = { lines: [], seen: new Set(), next: null, pages: 0 };
      let start = 0;
      do {
        const page = await fetchPage(id, start);
        for (const ln of page.lines) {
          if (!acc.seen.has(ln.id)) {
            acc.seen.add(ln.id);
            acc.lines.push(ln);
          }
        }
        acc.pages += 1;
        const next = page.more && page.next_start !== null && page.next_start !== undefined
          ? page.next_start : null;
        acc.next = next === start && acc.pages > 1 ? null : next;
        start = acc.next;
      } while (acc.next !== null && acc.pages < keepPages);
      return acc;
    })(),
  ]);
  if (gen !== reader.gen) return;
  reader.detail = detail.status === 'fulfilled' ? detail.value.conversation : null;
  if (transcript.status === 'fulfilled') {
    reader.lines = transcript.value.lines;
    reader.seen = transcript.value.seen;
    reader.nextStart = transcript.value.next;
    reader.pages = transcript.value.pages;
  }
  const y = window.scrollY;
  renderReader();
  if (keepPages > 1) window.scrollTo(0, y);
  if (detail.status === 'rejected') {
    const err = detail.reason.message;
    const box = $('#bs-detail-status');
    if (box) status(box, err, true);
  }
  if (transcript.status === 'rejected') {
    status($('#bs-lines'), transcript.reason.message, true);
  }
}

function showReader(id) {
  const changed = id !== reader.id;
  reader.id = id;
  if (changed) {
    reader.detail = null;
    reader.lines = [];
    reader.seen = new Set();
    reader.nextStart = null;
    reader.pages = 0;
    reader.notice = '';
  }
  $('#bs-home').hidden = true;
  $('#bs-reader').hidden = false;
  renderReader();
  window.scrollTo(0, 0);
  loadReader(id);
}

function reloadReader() {
  return loadReader(reader.id, Math.max(1, reader.pages));
}

function route() {
  const id = hashId();
  if (id && /^[A-Za-z0-9_.:-]{1,200}$/.test(id)) {
    showReader(id);
    return;
  }
  reader.gen += 1;
  reader.id = null;
  reader.start = '';
  $('#bs-reader').hidden = true;
  $('#bs-reader').replaceChildren();
  $('#bs-home').hidden = false;
  window.scrollTo(0, reader.fromList ? reader.listScroll : 0);
  reader.fromList = false;
  if (id) status($('#bs-list'), 'That conversation link is not valid.', true);
}

window.addEventListener('hashchange', route);

$('#bs-filter').addEventListener('submit', (e) => {
  e.preventDefault();
  loadList();
});
$('#bs-refresh').addEventListener('click', () => {
  if (reader.id) {
    reader.notice = '';
    reloadReader();
  }
  loadWho();
  loadItems();
  loadList();
});
loadWho();
loadItems();
loadList();
route();
