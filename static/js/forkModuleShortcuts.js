// Fork: "Open Conversations" / "Open Today" in Settings → Shortcuts. Data only (no side
// effects), spread into upstream's tables by small hooks in static/js/settings.js and
// static/js/keyboard-shortcuts.js. Unbound by default, like upstream's other open-tool
// shortcuts. The buttons they click are the sidebar entries whose clicks
// static/js/forkModuleWindows.js turns into windows.

const MIC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" '
  + 'height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="17" x2="12" y2="22"/></svg>';
const SUN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/>'
  + '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4'
  + 'M17.7 6.3l1.4-1.4"/></svg>';

const SHORTCUTS = {
  open_conversations: { button: 'tool-sound-module-btn', window: 'fork-sound-modal',
                        label: 'Open Conversations', icon: MIC },
  open_today: { button: 'tool-today-insight-btn', window: 'fork-today-modal',
                label: 'Open Today', icon: SUN },
};

const pick = (field) => Object.fromEntries(
  Object.entries(SHORTCUTS).map(([action, s]) => [action, s[field]]));

/** action -> '' (unbound default) */
export const FORK_SHORTCUT_DEFAULTS = Object.fromEntries(Object.keys(SHORTCUTS).map((a) => [a, '']));
/** action -> sidebar button id it clicks */
export const FORK_SHORTCUT_BUTTONS = pick('button');
/** action -> label in Settings → Shortcuts */
export const FORK_SHORTCUT_LABELS = pick('label');
/** action -> icon in Settings → Shortcuts */
export const FORK_SHORTCUT_ICONS = pick('icon');
/** action names, for the "Open Tools" group */
export const FORK_SHORTCUT_ACTIONS = Object.keys(SHORTCUTS);
/** window id -> the button that opens it, for "Toggle Window" */
export const FORK_WINDOW_TRIGGERS = Object.fromEntries(
  Object.values(SHORTCUTS).map((s) => [s.window, s.button]));
