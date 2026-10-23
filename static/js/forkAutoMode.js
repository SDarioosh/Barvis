// Fork: "Auto mode" card in Settings → Tools (admin only). Loaded by one hook in
// static/index.html. The switch is GET/PUT /api/fork/auto-mode (routes/auto_mode_routes.py);
// what auto mode allows without asking is in src/auto_mode.py.

const CARD_ID = 'fork-auto-mode-card';

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (text !== undefined) node.textContent = text;
  return node;
}

function buildCard() {
  const card = el('div', { class: 'admin-card admin-only', id: CARD_ID, style: 'margin-bottom:12px;' });
  const head = el('div', { style: 'display:flex;align-items:center;gap:8px;' });
  const h2 = el('h2', { style: 'margin:0;' }, 'Auto mode');
  const spacer = el('span', { style: 'flex:1' });
  const sw = el('label', { class: 'admin-switch', title: 'Run safe tools without asking' });
  const input = el('input', { type: 'checkbox', id: 'fork-autoModeToggle' });
  sw.append(input, el('span', { class: 'admin-slider' }));
  head.append(h2, spacer, sw);
  const sub = el('div', { class: 'admin-toggle-sub', style: 'margin:6px 0 4px;' },
    'Runs safe tools without asking, even after the chat has read a web page: web search, '
    + 'reading a page you linked or one a search or page linked to, reading your own notes, '
    + 'calendar, tasks and documents, and a calculator for exact numbers.');
  const still = el('div', { class: 'admin-toggle-sub', style: 'margin:0 0 4px;' },
    'Still asks before: sending email, creating or changing notes, tasks or events, deleting, '
    + 'terminal and code, file writes, settings, and opening a web address the AI made up.');
  const msg = el('div', { style: 'font-size:11px;' });
  card.append(head, sub, still, msg);
  return { card, input, msg };
}

function mount() {
  if (document.getElementById(CARD_ID)) return null;
  const anchorInput = document.getElementById('set-agentMaxTools');
  const anchor = anchorInput && anchorInput.closest('.admin-card');
  if (!anchor) return null;
  const ui = buildCard();
  anchor.after(ui.card);
  return ui;
}

function show(ui, text, isError) {
  ui.msg.textContent = text || '';
  ui.msg.style.color = isError ? 'var(--red)' : 'var(--fg)';
  if (text && !isError) setTimeout(() => { if (ui.msg.textContent === text) ui.msg.textContent = ''; }, 2000);
}

async function load(ui) {
  try {
    const res = await fetch('/api/fork/auto-mode', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(res.status === 403 ? 'Admin only.' : `HTTP ${res.status}`);
    ui.input.checked = (await res.json()).enabled === true;
  } catch (e) {
    show(ui, e.message || 'Could not load', true);
  }
}

async function save(ui) {
  const want = ui.input.checked;
  ui.input.disabled = true;
  try {
    const res = await fetch('/api/fork/auto-mode', {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: want }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    ui.input.checked = body.enabled === true;
    show(ui, ui.input.checked ? 'Auto mode on' : 'Auto mode off');
  } catch (e) {
    ui.input.checked = !want;
    show(ui, e.message || 'Failed to save', true);
  } finally {
    ui.input.disabled = false;
  }
}

function init() {
  const ui = mount();
  if (!ui) return;
  ui.input.addEventListener('change', () => save(ui));
  let loaded = false;
  const refresh = () => { loaded = true; load(ui); };
  document.addEventListener('click', (ev) => {
    if (ev.target.closest && ev.target.closest('[data-settings-tab="tools"]')) refresh();
  });
  const panel = ui.card.closest('[data-settings-panel]');
  if (panel && !panel.classList.contains('hidden') && !loaded) refresh();
  if (panel && 'MutationObserver' in window) {
    new MutationObserver(() => {
      if (!panel.classList.contains('hidden') && !loaded) refresh();
    }).observe(panel, { attributes: true, attributeFilter: ['class'] });
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
