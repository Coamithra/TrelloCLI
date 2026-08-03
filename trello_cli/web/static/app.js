'use strict';

const boardEl = document.getElementById('board');
const picker = document.getElementById('board-picker');
const navEl = document.getElementById('board-nav');
const starToggle = document.getElementById('star-toggle');
const statusEl = document.getElementById('status');
const detailEl = document.getElementById('detail');
const overlayEl = document.getElementById('overlay');

let cardSortables = [];
let boardSortable = null;
let liveDragging = false;  // true mid-drag, so a live refresh won't yank a card
let pendingReload = false;  // a live change arrived mid-drag; consumed in onEnd

let allBoards = [];        // every board from GET /api/boards, in API order
let currentBoardId = null; // the board currently rendered (drives every reload)

function setStatus(msg, isError) {
  statusEl.textContent = msg || '';
  statusEl.classList.toggle('error', !!isError);
}

// When the server is started on a non-loopback host it gates the API behind a
// token, handed to the page as ?token=… on the URL. XHRs send it as an
// `Authorization: Bearer` header (see api()); only the channels that can't set a
// header — browser navigation, EventSource, and attachment hrefs/img srcs — fall
// back to ?token= via withToken().
const AUTH_TOKEN = new URLSearchParams(location.search).get('token');

// Append `key=value` to a path's query string (picking ? or & correctly).
function withQuery(path, key, value) {
  return path + (path.includes('?') ? '&' : '?') + key + '=' + encodeURIComponent(value);
}

// Append the auth token as a query param. Reserved for the header-less channels
// above; keeping it off XHRs keeps the secret out of access logs and shareable
// URLs (the server accepts both header and query param).
function withToken(path) {
  return AUTH_TOKEN ? withQuery(path, 'token', AUTH_TOKEN) : path;
}

// Reflect the selected board in the URL (?board=<id>) so a reload, bookmark, or
// shared link reopens it instead of snapping back to the first board. Preserves
// any existing query params (notably ?token=) and doesn't add a history entry.
function setBoardInUrl(boardId) {
  const url = new URL(location.href);
  url.searchParams.set('board', boardId);
  history.replaceState(null, '', url);
}

// >>> card-url (sliced by tests/test_card_url.py) >>>
// Reflect the open card in the URL (?card=<id>), so F5 or a bookmark reopens the
// drawer instead of dropping you back on the bare board. Same contract as
// setBoardInUrl: replaceState (no history entry — the drawer is not a page) and
// every other param preserved, ?board= and ?token= included.
//
// This param is for THIS browser. The thing you hand to another agent is the
// magnet (see openLinkPopover): a URL is only usable by someone who can already
// reach this server and holds a token, while a magnet resolves with no shared
// state at all.
function setCardInUrl(cardId) {
  const url = new URL(location.href);
  if (cardId) url.searchParams.set('card', cardId);
  else url.searchParams.delete('card');
  history.replaceState(null, '', url);
}
// <<< card-url <<<

// ── board navigation: starred quick-swap buttons + dropdown ─────────
// Stars are a client-side preference (per-browser), kept in localStorage as a
// JSON array of board ids. Starred boards get a quick-swap button in the top
// bar; the rest stay in the dropdown.
const STAR_KEY = 'trellno:starredBoards';

function getStarredSet() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STAR_KEY));
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch (e) { return new Set(); }
}

function saveStarred(arr) {
  try { localStorage.setItem(STAR_KEY, JSON.stringify(arr)); return true; }
  catch (e) { return false; }  // private mode / quota — caller surfaces it
}

// Rebuild the top-bar nav from allBoards + the starred set + currentBoardId.
// Pure DOM — no fetch, no board reload — so it's cheap to call after a star
// toggle or a board switch.
function renderNav() {
  const starred = getStarredSet();

  // Quick-swap buttons, one per starred board (board order), active one lit.
  navEl.innerHTML = '';
  allBoards.filter((b) => starred.has(b.id)).forEach((b) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'board-nav-btn' + (b.id === currentBoardId ? ' active' : '');
    btn.textContent = b.name;
    btn.title = b.name;
    btn.addEventListener('click', () => selectBoard(b.id));
    navEl.appendChild(btn);
  });
  // Hide the landmark entirely when nothing is starred (the common first-run
  // state) so screen readers don't announce an empty "Starred boards" nav.
  navEl.classList.toggle('hidden', navEl.children.length === 0);

  // Dropdown holds the non-starred boards. A disabled "More boards…"
  // placeholder is the selected value when the current board is starred (so it
  // isn't an option here); the whole select hides when nothing is left in it.
  const nonStarred = allBoards.filter((b) => !starred.has(b.id));
  picker.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.disabled = true;
  placeholder.textContent = nonStarred.length ? 'More boards…' : 'No other boards';
  picker.appendChild(placeholder);
  nonStarred.forEach((b) => {
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = b.name;
    picker.appendChild(opt);
  });
  picker.value = starred.has(currentBoardId) ? '' : currentBoardId;
  picker.classList.toggle('hidden', nonStarred.length === 0);

  // ★/☆ toggle reflects whether the current board is starred.
  const on = starred.has(currentBoardId);
  starToggle.textContent = on ? '★' : '☆';
  starToggle.classList.toggle('on', on);
  starToggle.title = on ? 'Unstar this board' : 'Star this board';
  starToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
}

// Switch the rendered board: update state + URL, re-skin the nav, then reload
// the board and re-point the live stream. No-op for an empty/same selection.
function selectBoard(boardId) {
  if (!boardId || boardId === currentBoardId) return;
  // A card open in the drawer belongs to the board we're leaving — keeping it
  // would leave the URL claiming the new board plus a card that isn't on it.
  // Gated on `openCard` so the manage-boards panel, which shares the drawer and
  // nulls `openCard`, survives its own reloadBoardsNav() board switch.
  if (openCard) closeDetail();
  currentBoardId = boardId;
  setBoardInUrl(boardId);
  renderNav();
  loadBoard(boardId);
  initLive(boardId);
}

// Star/unstar the current board, then re-skin the nav (the board view is
// unchanged, so no reload).
function toggleStarCurrent() {
  if (!currentBoardId) return;
  const set = getStarredSet();
  if (set.has(currentBoardId)) set.delete(currentBoardId);
  else set.add(currentBoardId);
  if (!saveStarred([...set])) setStatus('Could not save stars (storage unavailable)', true);
  renderNav();
}

async function api(path, opts) {
  const options = opts ? { ...opts } : {};
  if (AUTH_TOKEN) {
    // Bearer header instead of ?token= — keeps the secret out of the server's
    // access log and out of any URL. Merge so callers' headers are preserved
    // (and FormData uploads still let the browser set their multipart boundary).
    options.headers = { ...(options.headers || {}), Authorization: 'Bearer ' + AUTH_TOKEN };
  }
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON body */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function patch(path, body) {
  return api(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function del(path) {
  return api(path, { method: 'DELETE' });
}

// Attachment helpers. isImageAtt / sizeStr mirror fmt.py's is_image / size_str
// so the UI matches the CLI. attachmentHref: uploaded blobs are served
// (token-gated) through the proxy endpoint; external URL attachments are linked
// to directly (the browser fetches them — the server never proxies an arbitrary
// URL on a request's behalf).
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
  '.tif', '.tiff', '.heic'];

function isImageAtt(att) {
  const mime = (att.mimeType || '').toLowerCase();
  if (mime) return mime.startsWith('image/');
  const n = (att.name || att.url || '').toLowerCase();
  return IMAGE_EXTS.some((e) => n.endsWith(e));
}

function sizeStr(bytes) {
  if (!bytes) return '';
  let n = bytes;
  const units = ['B', 'KB', 'MB', 'GB'];
  for (let i = 0; i < units.length; i++) {
    if (n < 1024 || i === units.length - 1) {
      return (units[i] === 'B' ? Math.round(n) : n.toFixed(1)) + units[i];
    }
    n /= 1024;
  }
  return '';
}

function attachmentHref(cardId, att) {
  if (att.isUpload) return withToken(`/api/cards/${cardId}/attachments/${att.id}/raw`);
  return att.url;
}

// The Trello label palette the chip CSS (.label[data-color=…]) already styles,
// offered when creating a new label on the fly. '' is the colorless/grey label.
const LABEL_COLORS = ['green', 'yellow', 'orange', 'red', 'purple', 'blue',
  'sky', 'lime', 'pink', 'black', ''];

// The same float-midpoint rule the CLI uses for `card pos` / `list pos`:
// land between the new DOM neighbours, or send the "top"/"bottom" keyword at
// an edge so the backend resolves it against the destination's current bounds.
// Skips siblings without a numeric data-pos (e.g. the "Add another list"
// affordance that sits after the last column).
function siblingPos(el, dir) {
  let s = el[dir];
  while (s) {
    const p = parseFloat(s.dataset.pos);
    if (!Number.isNaN(p)) return p;
    s = s[dir];
  }
  return null;
}

function neighborPos(el) {
  const pp = siblingPos(el, 'previousElementSibling');
  const np = siblingPos(el, 'nextElementSibling');
  if (pp === null && np === null) return 'bottom';
  if (pp === null) return 'top';
  if (np === null) return 'bottom';
  return (pp + np) / 2;
}

// ── rendering ──────────────────────────────────────────────────────

function labelChips(labels) {
  const wrap = document.createElement('div');
  wrap.className = 'labels';
  (labels || []).forEach((lb) => {
    const chip = document.createElement('span');
    chip.className = 'label';
    if (lb.color) chip.dataset.color = lb.color;
    chip.textContent = lb.name || lb.color || '';
    chip.title = [lb.name, lb.color].filter(Boolean).join(' ');
    wrap.appendChild(chip);
  });
  return wrap;
}

function cardEl(card) {
  const el = document.createElement('div');
  el.className = 'card';
  el.dataset.id = card.id;
  el.dataset.pos = card.pos;
  el.dataset.list = card.idList;

  if ((card.labels || []).length) el.appendChild(labelChips(card.labels));

  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = card.name;
  el.appendChild(title);

  const attCount = (card.attachments || []).length;
  if (card.due || attCount) {
    const meta = document.createElement('div');
    meta.className = 'card-meta';
    if (card.due) {
      const due = document.createElement('span');
      due.className = 'due' + (card.dueComplete ? ' done' : '');
      due.textContent = card.due.slice(0, 10);
      meta.appendChild(due);
    }
    if (attCount) {
      const att = document.createElement('span');
      att.className = 'card-attach';
      att.textContent = `📎 ${attCount}`;
      att.title = `${attCount} attachment${attCount === 1 ? '' : 's'}`;
      meta.appendChild(att);
    }
    el.appendChild(meta);
  }

  el.addEventListener('click', () => openDetail(card.id));
  return el;
}

function countFor(cardsWrap) {
  const col = cardsWrap.closest('.column');
  const count = col && col.querySelector('.column-count');
  if (count) count.textContent = cardsWrap.querySelectorAll('.card').length;
}

// Re-render a card's face in place after a detail-panel edit (title, labels, due),
// preserving its DOM position. No-op if the card isn't currently on the board.
function refreshCardFace(card) {
  const old = boardEl.querySelector(`.card[data-id="${card.id}"]`);
  if (!old) return;
  old.replaceWith(cardEl(card));
}

// Remove a card's face from the board (after a delete) and fix its column count.
function removeCardFace(cardId) {
  const el = boardEl.querySelector(`.card[data-id="${cardId}"]`);
  if (!el) return;
  const wrap = el.closest('.cards');
  el.remove();
  if (wrap) countFor(wrap);
}

function columnEl(list, cards) {
  const col = document.createElement('section');
  col.className = 'column';
  col.dataset.listId = list.id;
  col.dataset.pos = list.pos;

  const listSort = list.sort || 'manual';
  col.dataset.sort = listSort;

  const header = document.createElement('div');
  header.className = 'column-header';
  const name = document.createElement('span');
  name.className = 'column-name';
  name.textContent = list.name;
  const count = document.createElement('span');
  count.className = 'column-count';
  count.textContent = cards.length;

  // The actions menu (the small⋯ button) holds Sort by and Delete list,
  // keeping the column header uncluttered, Trello-style.
  const menuBtn = document.createElement('button');
  menuBtn.className = 'column-menu-btn';
  menuBtn.type = 'button';
  menuBtn.textContent = '⋯';
  menuBtn.title = 'List actions';
  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleColumnMenu(col, list);
  });

  header.append(name, count, menuBtn);
  col.appendChild(header);

  const cardsWrap = document.createElement('div');
  cardsWrap.className = 'cards';
  cardsWrap.dataset.listId = list.id;
  cards.forEach((c) => cardsWrap.appendChild(cardEl(c)));
  col.appendChild(cardsWrap);

  const composer = document.createElement('div');
  composer.className = 'composer';
  const input = document.createElement('input');
  input.className = 'composer-input';
  input.placeholder = '+ Add a card';
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    const name = input.value.trim();
    if (!name) return;
    input.value = '';
    try {
      const card = await post(`/api/lists/${list.id}/cards`, { name });
      // On a sorted column the new card lands in its sorted slot (anywhere, not
      // the bottom), so reload to place it correctly; manual columns keep the
      // cheap append.
      if (listSort !== 'manual') {
        // Quiet + state-preserving: the composer keeps its focus across the
        // rebuild, so you can keep typing the next card.
        await loadBoard(currentBoardId, { quiet: true });
      } else {
        cardsWrap.appendChild(cardEl(card));
        countFor(cardsWrap);
      }
      setStatus('Card added');
    } catch (err) {
      setStatus('Add failed: ' + err.message, true);
    }
  });
  composer.appendChild(input);
  col.appendChild(composer);
  return col;
}

// A per-column actions menu: Sort by + Delete list. Closes any other open
// menu first; an outside click / Escape closes it (wired once at boot).
function toggleColumnMenu(col, list) {
  const existing = col.querySelector('.column-menu');
  closeColumnMenus();
  if (existing) return;  // it was open → toggle shut
  const menu = document.createElement('div');
  menu.className = 'column-menu';

  // Sort by (persisted auto-sort): picking a sort re-sorts the column's cards
  // server-side, and every later add auto-places into the saved order. The
  // current choice is marked active.
  const sortHead = document.createElement('div');
  sortHead.className = 'column-menu-label';
  sortHead.textContent = 'Sort by';
  menu.appendChild(sortHead);
  // "Newest" means two different clocks and users mean both, so the menu spells
  // out which one each entry uses. The server reports a pre-split store's
  // `newest`/`oldest` in its canonical `activity-*` spelling, so `current`
  // always matches one of these values.
  const current = list.sort || 'manual';
  [['manual', 'Manual'],
   ['created-newest', 'Newest first (created)'],
   ['created-oldest', 'Oldest first (created)'],
   ['activity-newest', 'Newest first (updated)'],
   ['activity-oldest', 'Oldest first (updated)'],
   ['name', 'Card name']]
    .forEach(([value, label]) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'column-menu-item' + (value === current ? ' active' : '');
      item.textContent = label;
      item.addEventListener('click', async (e) => {
        e.stopPropagation();
        closeColumnMenus();
        if (value === current) return;  // already this sort
        try {
          await patch(`/api/lists/${list.id}`, { sort: value });
          // Quiet: the re-sort rewrote the column's cards, so the watchdog will
          // also fire a live `change` — one visible refresh between them, not a
          // string of "Loading…" flashes.
          await loadBoard(currentBoardId, { quiet: true });
          setStatus(value === 'manual' ? 'Sort cleared' : 'Sorted by ' + label.toLowerCase());
        } catch (err) {
          setStatus('Sort failed: ' + err.message, true);
        }
      });
      menu.appendChild(item);
    });

  const sep = document.createElement('div');
  sep.className = 'column-menu-sep';
  menu.appendChild(sep);

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'column-menu-item danger';
  del.textContent = 'Delete list';
  del.addEventListener('click', async (e) => {
    e.stopPropagation();
    closeColumnMenus();
    const n = col.querySelectorAll('.card').length;
    const warn = n
      ? `Delete "${list.name}" and archive its ${n} card${n === 1 ? '' : 's'}?`
      : `Delete "${list.name}"?`;
    if (!window.confirm(warn)) return;
    try {
      await patch(`/api/lists/${list.id}`, { closed: true });
      setStatus('List deleted');
      await loadBoard(currentBoardId);
    } catch (err) {
      setStatus('Delete failed: ' + err.message, true);
    }
  });
  menu.appendChild(del);
  col.querySelector('.column-header').appendChild(menu);
}

function closeColumnMenus() {
  document.querySelectorAll('.column-menu').forEach((m) => m.remove());
}

function initDragging() {
  if (boardSortable) boardSortable.destroy();
  cardSortables.forEach((s) => s.destroy());
  cardSortables = [];
  // destroy() skips onEnd, so clear the drag class here too in case a reload
  // ever tears a Sortable down mid-drag — otherwise body.dragging could stick
  // and permanently hide the columns' scrollbars.
  document.body.classList.remove('dragging');

  // Reorder columns (grab by header only, so card drags don't trigger it).
  // `filter` keeps the "Add another list" affordance from being draggable.
  boardSortable = Sortable.create(boardEl, {
    group: 'columns',
    draggable: '.column',
    // Keep the add-list affordance non-draggable, and stop the header controls
    // (sort picker, actions menu) from initiating a column drag.
    filter: '.add-list, .column-menu-btn, .column-menu',
    // Filter alone stops these from starting a drag, but SortableJS still
    // preventDefault()s the pointer event on them by default — which blocks the
    // native <select> dropdown from opening and eats the menu button's click,
    // since both live inside the .column-header drag handle. Turn that off.
    preventOnFilter: false,
    handle: '.column-header',
    animation: 150,
    onStart: () => { liveDragging = true; document.body.classList.add('dragging'); },
    onEnd: async (evt) => {
      document.body.classList.remove('dragging');
      const col = evt.item;
      // Keep the add-list affordance pinned to the end if a column was dropped
      // to its right.
      const addList = boardEl.querySelector('.add-list');
      if (addList && addList.nextElementSibling) boardEl.appendChild(addList);
      let rebalanced = false;
      let failed = false;
      try {
        const updated = await patch(`/api/lists/${col.dataset.listId}`, { pos: neighborPos(col) });
        col.dataset.pos = updated.pos;
        rebalanced = !!updated.rebalanced;
        setStatus('Column moved');
      } catch (err) {
        setStatus('Move failed: ' + err.message, true);
        failed = true;
      } finally {
        liveDragging = false;
      }
      // Reload when: the PATCH failed (roll the DOM back to the server's truth —
      // nothing else corrects the wrong drop), a rebalance respread the *other*
      // columns' data-pos, or a live change arrived mid-drag and was deferred.
      // Done after the finally clears liveDragging so we don't tear down this
      // Sortable mid-onEnd.
      const reload = failed || rebalanced || pendingReload;
      pendingReload = false;
      if (reload) await loadBoard(currentBoardId);
    },
  });

  // Drag cards within and between columns.
  document.querySelectorAll('.cards').forEach((wrap) => {
    cardSortables.push(Sortable.create(wrap, {
      group: 'cards',
      animation: 150,
      onStart: () => { liveDragging = true; document.body.classList.add('dragging'); },
      onEnd: async (evt) => {
        document.body.classList.remove('dragging');
        const item = evt.item;
        const toList = evt.to.dataset.listId;
        if (evt.from !== evt.to) { countFor(evt.from); countFor(evt.to); }
        // A manual hand-placement takes the destination column off auto-sort —
        // otherwise the saved sort would fight the user on the next add. Detect
        // a non-manual destination before the move so we can clear it after.
        const destCol = evt.to.closest('.column');
        const clearSort = destCol && destCol.dataset.sort && destCol.dataset.sort !== 'manual';
        let rebalanced = false;
        let sortCleared = false;
        let failed = false;
        try {
          const updated = await patch(`/api/cards/${item.dataset.id}`, {
            idList: toList,
            pos: neighborPos(item),
          });
          item.dataset.pos = updated.pos;
          item.dataset.list = updated.idList;
          rebalanced = !!updated.rebalanced;
          // Clear the destination's auto-sort in a separate try: the move already
          // committed, so a failed sort-clear must not be reported as a failed
          // move (and only a real clear should drive the reload below).
          if (clearSort) {
            try {
              await patch(`/api/lists/${toList}`, { sort: 'manual' });
              destCol.dataset.sort = 'manual';
              sortCleared = true;
            } catch (err) {
              setStatus('Card moved, but clearing the column sort failed: ' + err.message, true);
            }
          }
          if (!clearSort || sortCleared) setStatus(sortCleared ? 'Card moved (sort cleared)' : 'Card moved');
        } catch (err) {
          setStatus('Move failed: ' + err.message, true);
          failed = true;
        } finally {
          liveDragging = false;
        }
        // Reload when: the move PATCH failed (roll the card back to its real
        // position — no SSE will fix a failed write), a rebalance respread the
        // *other* cards' data-pos, the destination's auto-sort was cleared (so
        // its menu resets), or a live change arrived mid-drag and was deferred.
        // Done after finally clears liveDragging so we don't tear down mid-onEnd.
        const reload = failed || rebalanced || sortCleared || pendingReload;
        pendingReload = false;
        if (reload) await loadBoard(currentBoardId);
      },
    }));
  });
}

// >>> render-state (sliced by tests/test_render_state.py) >>>
// What a re-render must not destroy: half-typed composer text, the caret, and
// where the user had scrolled to. A board reload rebuilds every column from
// scratch, and reloads are not all user-initiated — a live change, or another
// agent's CLI write, can land while you are mid-sentence in "+ Add a card".
// Losing the text then reads as the app eating your input, which is what made
// re-sorting a column feel broken.
function captureBoardState() {
  const composers = {};
  boardEl.querySelectorAll('.composer-input').forEach((input) => {
    const listId = input.closest('.column')?.dataset.listId;
    if (!listId) return;
    if (!input.value && document.activeElement !== input) return;
    composers[listId] = {
      value: input.value,
      focused: document.activeElement === input,
      start: input.selectionStart,
      end: input.selectionEnd,
    };
  });
  const scrollTops = {};
  boardEl.querySelectorAll('.cards').forEach((wrap) => {
    if (wrap.scrollTop) scrollTops[wrap.dataset.listId] = wrap.scrollTop;
  });
  const addInput = boardEl.querySelector('.add-list-input');
  const addForm = boardEl.querySelector('.add-list-form');
  return {
    composers,
    scrollTops,
    scrollLeft: boardEl.scrollLeft,
    addList: addForm && !addForm.classList.contains('hidden')
      ? { value: addInput ? addInput.value : '',
          focused: document.activeElement === addInput }
      : null,
  };
}

function restoreBoardState(state) {
  Object.entries(state.composers).forEach(([listId, s]) => {
    const input = boardEl.querySelector(
      `.column[data-list-id="${listId}"] .composer-input`);
    if (!input) return;  // its column is gone (archived, or another board)
    input.value = s.value;
    if (!s.focused) return;
    input.focus();
    // setSelectionRange throws on an input whose type doesn't support it; this
    // one is a plain text input, but guard the caret restore anyway so a browser
    // quirk can't take the whole render down with it.
    try { input.setSelectionRange(s.start, s.end); } catch (_) { /* caret is best-effort */ }
  });
  Object.entries(state.scrollTops).forEach(([listId, top]) => {
    const wrap = boardEl.querySelector(`.cards[data-list-id="${listId}"]`);
    if (wrap) wrap.scrollTop = top;
  });
  boardEl.scrollLeft = state.scrollLeft;
  if (state.addList) {
    const form = boardEl.querySelector('.add-list-form');
    const placeholder = boardEl.querySelector('.add-list-placeholder');
    const input = boardEl.querySelector('.add-list-input');
    if (!form || !input) return;
    form.classList.remove('hidden');
    if (placeholder) placeholder.classList.add('hidden');
    input.value = state.addList.value;
    if (state.addList.focused) input.focus();
  }
}
// <<< render-state <<<

function renderBoard(data) {
  const state = captureBoardState();
  boardEl.innerHTML = '';
  const byList = {};
  (data.cards || []).forEach((c) => { (byList[c.idList] = byList[c.idList] || []).push(c); });
  Object.values(byList).forEach((arr) => arr.sort((a, b) => (Number(a.pos) || 0) - (Number(b.pos) || 0)));
  (data.lists || []).forEach((list) => boardEl.appendChild(columnEl(list, byList[list.id] || [])));
  boardEl.appendChild(addListEl(data.board.id));
  initDragging();
  restoreBoardState(state);
}

// Trello-style "Add another list" affordance — a placeholder that swaps to an
// inline composer on click. Lives after the last column; not draggable.
function addListEl(boardId) {
  const wrap = document.createElement('div');
  wrap.className = 'add-list';

  const placeholder = document.createElement('button');
  placeholder.type = 'button';
  placeholder.className = 'add-list-placeholder';
  placeholder.textContent = '+ Add another list';

  const form = document.createElement('div');
  form.className = 'add-list-form hidden';
  const input = document.createElement('input');
  input.className = 'add-list-input';
  input.placeholder = 'Enter list name…';
  form.appendChild(input);

  let submitting = false;  // guards against Enter + blur double-firing the POST
  const reset = () => {
    input.value = '';
    form.classList.add('hidden');
    placeholder.classList.remove('hidden');
  };
  placeholder.addEventListener('click', () => {
    placeholder.classList.add('hidden');
    form.classList.remove('hidden');
    input.focus();
  });
  const submit = async () => {
    if (submitting) return;
    const name = input.value.trim();
    if (!name) { reset(); return; }
    submitting = true;
    try {
      await post(`/api/boards/${boardId}/lists`, { name });
      setStatus('List added');
      await loadBoard(currentBoardId);  // re-renders, discarding this affordance
    } catch (err) {
      setStatus('Add list failed: ' + err.message, true);
      submitting = false;  // allow a retry only on failure (success re-renders)
    }
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submit();
    else if (e.key === 'Escape') reset();
  });
  input.addEventListener('blur', submit);

  wrap.append(placeholder, form);
  return wrap;
}

// >>> board-load (sliced by tests/test_render_state.py) >>>
// Monotonic token so a slow board response can't render over a newer one. Every
// board switch/reload calls loadBoard and bumps it; a response whose token is no
// longer current is stale (the user navigated on) and is dropped before render.
let boardReqSeq = 0;
// Serialized payload of the board as currently drawn, so a quiet reload can tell
// "nothing actually changed" from "something did". Reset by every render, not
// just the skipping one, and never used to skip a reload the user asked for.
let lastBoardSig = null;

// `quiet` suppresses the status churn for a refresh the user did not ask for
// (a live change, or the reload that follows one of their own edits): the board
// still re-renders, but the top-right doesn't flash "Loading…" → board name each
// time. Errors are never quiet.
async function loadBoard(boardId, { quiet = false } = {}) {
  const seq = ++boardReqSeq;
  if (!quiet) setStatus('Loading…');
  try {
    const data = await api(`/api/boards/${boardId}`);
    if (seq !== boardReqSeq) return;  // a newer load superseded this one
    // A quiet reload that would draw exactly what's already on screen is
    // skipped. Every write the user makes here reaches the store, so the
    // watchdog reports it back as a live change and we'd re-render the board we
    // just rendered — and a sync client replaying those same file writes a
    // moment later says it again. Comparing the payload keeps the refresh
    // single-step without ever second-guessing WHOSE change it was.
    const sig = JSON.stringify(data);
    if (quiet && sig === lastBoardSig) return;
    lastBoardSig = sig;
    renderBoard(data);
    if (!quiet) setStatus(data.board.name);
  } catch (err) {
    if (seq !== boardReqSeq) return;
    setStatus('Load failed: ' + err.message, true);
  }
}
// <<< board-load <<<

// ── detail drawer (editable, Trello-style) ─────────────────────────

let openCard = null;       // the card dict currently shown in the detail panel
let openPopover = null;    // the floating popover element (label/due), if any
// Token guarding the drawer against a stale detail/manage response rendering
// after the user opened a different card (or the manage panel). Bumped by every
// openDetail/openManageBoards; a response whose token went stale is dropped.
let detailReqSeq = 0;

function closePopover() {
  if (openPopover) { openPopover.remove(); openPopover = null; }
}

function closeDetail() {
  closePopover();
  openCard = null;
  setCardInUrl(null);
  detailEl.classList.add('hidden');
  overlayEl.classList.add('hidden');
}

function heading(text) {
  const h = document.createElement('h3');
  h.textContent = text;
  return h;
}

// ── linkify ────────────────────────────────────────────────────────
// Render user text with bare http(s) URLs turned into real links.
//
// Returns a DocumentFragment of alternating text nodes and <a> elements, so the
// non-URL parts never pass through an HTML parser and stay escaped BY
// CONSTRUCTION — the same safety property `textContent` gives, which is why
// this is not an innerHTML + escape() pass. Card text is user-authored and (on
// the local backend) syncs between machines, so a stored-XSS hole here would be
// a real one.
//
// http/https only: an href built from this regex is always an absolute http(s)
// URL, so `javascript:` / `data:` can't be smuggled in. Bare `www.` and
// `mailto:` are deliberately not matched.
const URL_RE = /\bhttps?:\/\/[^\s<>"']+/gi;

const CLOSERS = { ')': '(', ']': '[', '}': '{' };

// `[^\s]+` eats sentence punctuation, so "see https://x.com/a)." would link the
// `).` too. Walk back over trailing punctuation, but keep a closing bracket the
// URL itself opened — https://en.wikipedia.org/wiki/Foo_(bar) must stay whole.
function trimUrlTail(url) {
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1];
    if ('.,;:!?\'"'.includes(ch)) { end -= 1; continue; }
    const open = CLOSERS[ch];
    if (open) {
      const slice = url.slice(0, end);
      const opens = slice.split(open).length - 1;
      const closes = slice.split(ch).length - 1;
      if (closes > opens) { end -= 1; continue; }
    }
    break;
  }
  return url.slice(0, end);
}

function linkify(text) {
  const frag = document.createDocumentFragment();
  const src = text || '';
  let last = 0;
  URL_RE.lastIndex = 0;  // the regex is module-level + /g — reset per call
  let m;
  while ((m = URL_RE.exec(src)) !== null) {
    const href = trimUrlTail(m[0]);
    // Trimming can eat the entire host: prose like "URLs must start with
    // https://." leaves a bare scheme, which would render as an underlined
    // link to nowhere. Require at least one host character; otherwise leave the
    // whole match as plain text (skipping the append leaves it to the next
    // slice). This also guarantees lastIndex advances, so the loop terminates.
    if (!/^https?:\/\/[^\s/?#]/i.test(href)) {
      URL_RE.lastIndex = m.index + m[0].length;
      continue;
    }
    if (m.index > last) frag.appendChild(document.createTextNode(src.slice(last, m.index)));
    const a = document.createElement('a');
    a.href = href;
    a.textContent = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    // The description's read view carries title="Click to edit", which a
    // tooltip lookup would inherit onto these anchors — advertising exactly the
    // action the inlineEditable guard suppresses. An explicit empty title stops
    // the walk up the ancestors.
    a.title = '';
    frag.appendChild(a);
    last = m.index + href.length;
    URL_RE.lastIndex = last;  // re-scan the trimmed tail as ordinary text
  }
  if (last < src.length) frag.appendChild(document.createTextNode(src.slice(last)));
  return frag;
}

// >>> markdown-render (sliced by tests/test_markdown.py) >>>
// Render user text as Markdown, for card descriptions and comment bodies.
//
// markdown-it is used as a PARSER ONLY: `md.parse()` returns a flat token
// stream and the walker below turns it into DOM nodes with
// document.createElement. Its HTML renderer (`md.render`) is never called and
// nothing here touches innerHTML, so user text still never passes through an
// HTML parser -- the same escape-by-construction property linkify() has, and
// the reason no sanitizer is vendored alongside it. (A sanitizer would also be
// untestable here: DOMPurify needs a real DOM, and tests/ exercises this code
// under node against a shim.)
//
// `html: false` means raw HTML in card text is never tokenised at all -- it
// arrives as a `text` token and ends up a text node. `breaks: true` maps a
// single newline to <br>, so a plain non-Markdown description still reads the
// way it did under `white-space: pre-wrap`. `linkify: false` because we run our
// own linkify() over text tokens instead, keeping one URL semantics across
// every render path (http(s) only, with trimUrlTail's punctuation rules)
// rather than adding markdown-it's bundled matcher as a second one.
const MD_OPTS = { html: false, linkify: false, breaks: true, typographer: false };

// This whitelist IS the security boundary for element types: EVERY element the
// walker creates goes through mdEl(), so a tag that is not listed cannot be
// produced by either path (container tokens in mdWalk, leaf tokens in mdLeaf).
// A refused tag drops no content -- the token's children or text render into
// the parent instead, so no user text ever disappears. ADDING A TAG? Add it
// here and give it a rule under `.markdown` in style.css.
const MD_TAGS = new Set([
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'strong', 'em', 's', 'code', 'pre', 'blockquote',
  'ul', 'ol', 'li', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
]);

// The one place an element is created FROM A TOKEN. Returns null for a tag off
// MD_TAGS, so the whitelist governs the leaf tokens in mdLeaf as well as
// mdWalk's containers, and callers fall back to plain text.
function mdEl(tag) {
  return MD_TAGS.has(tag) ? document.createElement(tag) : null;
}

function mdAttr(token, name) {
  const attrs = token.attrs || [];
  for (const pair of attrs) if (pair[0] === name) return pair[1];
  return null;
}

// Only an absolute http(s) URL may become an href. Applied AFTER markdown-it's
// own normalisation (it percent-decodes/encodes and runs validateLink first),
// so this sees the final string. Anything else -- a relative path, a bare
// fragment, javascript:/data:/vbscript: -- yields null and the link's label
// renders as plain text instead.
function mdSafeHref(url) {
  const u = (url == null ? '' : String(url)).trim();
  return /^https?:\/\/[^\s/?#]/i.test(u) ? u : null;
}

function mdAnchor(href, title) {
  const a = mdEl('a');
  if (!a) return null;
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  // As in linkify(): an explicit empty title stops the read view's
  // title="Click to edit" tooltip being inherited by the anchor.
  a.title = title || '';
  return a;
}

// The ONLY attributes ever copied off a non-anchor token. Nothing else on the
// token is read, so an `on*` handler cannot end up on a produced element.
function mdDecorate(el, t) {
  if (t.tag === 'ol') {
    const start = mdAttr(t, 'start');
    if (start && /^[0-9]+$/.test(String(start))) el.setAttribute('start', String(start));
  } else if (t.tag === 'th' || t.tag === 'td') {
    // Column alignment arrives as style="text-align:left"; re-derive it rather
    // than copying the style string through.
    const m = /text-align:\s*(left|right|center)/i.exec(mdAttr(t, 'style') || '');
    if (m) el.style.textAlign = m[1].toLowerCase();
  }
}

function mdLeaf(parent, t, ctx) {
  switch (t.type) {
    case 'text':
      // Bare URLs in prose still become links -- but never inside an anchor's
      // own label, which would nest <a> elements.
      parent.appendChild(ctx.inLink
        ? document.createTextNode(t.content)
        : linkify(t.content));
      return;
    case 'softbreak':
    case 'hardbreak': {
      const br = mdEl('br');
      parent.appendChild(br || document.createTextNode('\n'));
      return;
    }
    case 'code_inline': {
      const code = mdEl('code');
      if (!code) { parent.appendChild(document.createTextNode(t.content)); return; }
      code.textContent = t.content;
      parent.appendChild(code);
      return;
    }
    case 'fence':
    case 'code_block': {
      // The fence's info string (```js) is dropped: there is no highlighting,
      // and it keeps user-controlled text off the element as a class.
      const pre = mdEl('pre');
      const code = mdEl('code');
      if (!pre || !code) { parent.appendChild(document.createTextNode(t.content)); return; }
      code.textContent = t.content;
      pre.appendChild(code);
      parent.appendChild(pre);
      return;
    }
    case 'hr': {
      const hr = mdEl('hr');
      if (hr) parent.appendChild(hr);
      return;
    }
    case 'image': {
      // Images are deliberately NOT rendered as <img>. A card description
      // should not make every viewer's browser fetch a remote URL (tracking
      // pixel / referrer leak) just by being opened. Show the alt text,
      // linked to the source when that is an http(s) URL.
      const src = mdSafeHref(mdAttr(t, 'src'));
      const label = t.content || mdAttr(t, 'src') || '';
      // `[![alt](img)](target)` is a linked image: inside an anchor already, so
      // linking the alt text too would nest <a> elements. createElement builds
      // the tree directly, with no HTML parser to un-nest them afterwards.
      const a = src && !ctx.inLink ? mdAnchor(src, mdAttr(t, 'title')) : null;
      if (a) {
        a.textContent = label || src;
        parent.appendChild(a);
      } else if (label) {
        parent.appendChild(document.createTextNode(label));
      }
      return;
    }
    default:
      // Unknown leaf -- including html_inline/html_block, which `html: false`
      // should never produce. Keep the text, drop the markup.
      if (t.content) parent.appendChild(document.createTextNode(t.content));
  }
}

// Walk a flat markdown-it token stream into `parent`. Tokens nest via
// `nesting` (+1 open / -1 close), so a stack tracks the current container;
// `inline` tokens carry their own child stream. `ctx.inLink` rides the same
// stack so it is restored exactly when its anchor closes.
function mdWalk(parent, tokens, ctx) {
  let cur = parent;
  const stack = [];
  for (const t of tokens) {
    if (t.type === 'inline') {
      mdWalk(cur, t.children || [], ctx);
    } else if (t.nesting === 1) {
      stack.push({ el: cur, inLink: ctx.inLink });
      let el = null;
      if (t.tag === 'a') {
        const href = mdSafeHref(mdAttr(t, 'href'));
        if (href) {
          el = mdAnchor(href, mdAttr(t, 'title'));
          if (el) ctx.inLink = true;
        }
      } else {
        el = mdEl(t.tag);
        if (el) mdDecorate(el, t);
      }
      // No element (tag off the whitelist, or an href that failed the gate):
      // `cur` stays put, so the children render into the parent as content --
      // for a refused link that means its label renders as ordinary prose,
      // linkified like any other text.
      if (el) {
        cur.appendChild(el);
        cur = el;
      }
    } else if (t.nesting === -1) {
      const prev = stack.pop();
      if (prev) {
        cur = prev.el;
        ctx.inLink = prev.inLink;
      }
    } else {
      mdLeaf(cur, t, ctx);
    }
  }
}

let mdParser;  // built on first use; `false` once we know the vendor script is missing

function markdownParser() {
  if (mdParser === undefined) {
    mdParser = typeof window.markdownit === 'function' ? window.markdownit(MD_OPTS) : false;
  }
  return mdParser;
}

// Drop-in replacement for linkify() on the description/comment paths: same
// DocumentFragment contract. If the vendored parser failed to load, degrade to
// linkify() rather than leaving the panel blank.
function renderMarkdown(text) {
  const src = text || '';
  const md = markdownParser();
  if (!md) {
    // Degraded: linkify() returns bare text nodes carrying the source's real
    // newlines, and the hosts dropped `white-space: pre-wrap` because rendered
    // Markdown does not want it. Wrap the fallback in something that restores
    // it, or a whole description reflows onto one line.
    const frag = document.createDocumentFragment();
    const span = document.createElement('span');
    span.className = 'md-fallback';
    span.appendChild(linkify(src));
    frag.appendChild(span);
    return frag;
  }
  const frag = document.createDocumentFragment();
  if (src) mdWalk(frag, md.parse(src, {}), { inLink: false });
  return frag;
}
// <<< markdown-render <<<

// Clamp a popover under its anchor within the viewport. Measured against the
// popover's *current* size, so callers re-run it after async content lands.
function positionPopover(pop, anchor) {
  const rect = anchor.getBoundingClientRect();
  let top = rect.bottom + 6;
  let left = rect.left;
  const pr = pop.getBoundingClientRect();
  if (left + pr.width > window.innerWidth - 8) left = window.innerWidth - pr.width - 8;
  if (left < 8) left = 8;
  if (top + pr.height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - pr.height - 6);
  }
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
}

// Float a popover anchored under a trigger button, clamped to the viewport. Only
// one is open at a time; clicking elsewhere (outside the popover) closes it.
function openPopoverAt(anchor, title, buildBody) {
  closePopover();
  const pop = document.createElement('div');
  pop.className = 'popover';

  const head = document.createElement('div');
  head.className = 'popover-head';
  const h = document.createElement('span');
  h.textContent = title;
  const x = document.createElement('button');
  x.className = 'popover-close';
  x.setAttribute('aria-label', 'Close');
  x.textContent = '×';
  x.addEventListener('click', closePopover);
  head.append(h, x);
  pop.appendChild(head);

  const body = document.createElement('div');
  body.className = 'popover-body';
  const built = buildBody(body);
  pop.appendChild(body);

  // Swallow clicks inside so the document-level outside-click handler doesn't fire.
  pop.addEventListener('click', (e) => e.stopPropagation());
  document.body.appendChild(pop);
  openPopover = pop;

  positionPopover(pop, anchor);
  // An async builder (e.g. the labels popover fetching board labels) is measured
  // at its "Loading…" size above; re-clamp once its content lands so a tall
  // popover doesn't overflow the viewport.
  if (built && typeof built.then === 'function') {
    built.then(() => { if (openPopover === pop) positionPopover(pop, anchor); });
  }
}

// Click-to-edit a single-line title or multi-line description. `render` shows the
// read view, `save(value)` persists. `container` stays stable across edit cycles:
// only its single child (`current`) is swapped between the read view and editor.
function inlineEditable(container, { value, multiline, render, save }) {
  // `current` is always the one element living inside `container`.
  let current = null;

  function showView() {
    const view = render();
    view.classList.add('editable');
    view.title = 'Click to edit';
    // A linkified description holds real <a>s (see linkify) — clicking one must
    // follow the link, not drop the box into edit mode. No-op for the title,
    // which is never linkified.
    view.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      showEditor();
    });
    swap(view);
  }

  function showEditor() {
    const editor = multiline
      ? document.createElement('textarea')
      : document.createElement('input');
    editor.className = multiline ? 'inline-textarea' : 'inline-input';
    if (!multiline) editor.type = 'text';
    editor.value = value;

    const actions = document.createElement('div');
    actions.className = 'inline-actions';
    const ok = document.createElement('button');
    ok.className = 'btn-primary';
    ok.textContent = 'Save';
    const cancel = document.createElement('button');
    cancel.className = 'btn';
    cancel.textContent = 'Cancel';
    actions.append(ok, cancel);

    const wrap = document.createElement('div');
    wrap.append(editor, actions);

    async function commit() {
      const next = editor.value;
      if (next === value) { showView(); return; }
      try {
        await save(next);
        value = next;
        showView();
      } catch (err) {
        setStatus('Save failed: ' + err.message, true);
      }
    }

    ok.addEventListener('click', commit);
    cancel.addEventListener('click', showView);
    // Enter saves a single-line title; Cmd/Ctrl-Enter saves a description.
    editor.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        // Close only THIS editor (revert to the read view). stopPropagation so
        // the document-level Escape handler doesn't also close the whole drawer.
        e.preventDefault();
        e.stopPropagation();
        showView();
      } else if (e.key === 'Enter' && (!multiline || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        commit();
      }
    });

    swap(wrap);
    editor.focus();
    if (multiline) editor.style.height = editor.scrollHeight + 'px';
  }

  function swap(el) {
    if (current) current.replaceWith(el);
    else container.appendChild(el);
    current = el;
  }

  showView();
}

// ── label popover ──────────────────────────────────────────────────

async function openLabelPopover(anchor) {
  const card = openCard;
  if (!card) return;
  openPopoverAt(anchor, 'Labels', async (body) => {
    body.appendChild(Object.assign(document.createElement('p'),
      { className: 'loading', textContent: 'Loading…' }));
    let labels;
    try {
      labels = await api(`/api/boards/${card.idBoard}/labels`);
    } catch (err) {
      body.innerHTML = '';
      body.appendChild(Object.assign(document.createElement('p'),
        { className: 'error', textContent: 'Failed to load labels: ' + err.message }));
      return;
    }
    body.innerHTML = '';

    const list = document.createElement('div');
    list.className = 'label-list';
    const applied = new Set((card.labels || []).map((l) => l.id));

    labels.forEach((lb) => {
      const row = document.createElement('button');
      row.className = 'label-row';
      const chip = document.createElement('span');
      chip.className = 'label label-row-chip';
      if (lb.color) chip.dataset.color = lb.color;
      chip.textContent = lb.name || lb.color || '(no name)';
      const check = document.createElement('span');
      check.className = 'label-check';
      check.textContent = applied.has(lb.id) ? '✓' : '';
      row.append(chip, check);
      row.addEventListener('click', async () => {
        const on = applied.has(lb.id);
        try {
          const updated = on
            ? await del(`/api/cards/${card.id}/labels/${lb.id}`)
            : await post(`/api/cards/${card.id}/labels`, { idLabel: lb.id });
          if (on) applied.delete(lb.id); else applied.add(lb.id);
          check.textContent = on ? '' : '✓';
          applyCardUpdate(updated);
          renderDetailLabels();
        } catch (err) {
          setStatus('Label update failed: ' + err.message, true);
        }
      });
      list.appendChild(row);
    });
    if (!labels.length) {
      list.appendChild(Object.assign(document.createElement('p'),
        { className: 'loading', textContent: 'No labels yet — create one below.' }));
    }
    body.appendChild(list);

    // ── create a brand-new label on the fly ──
    body.appendChild(heading('Create a new label'));
    const form = document.createElement('div');
    form.className = 'label-create';
    const nameIn = document.createElement('input');
    nameIn.className = 'inline-input';
    nameIn.type = 'text';
    nameIn.placeholder = 'Label name';

    const swatches = document.createElement('div');
    swatches.className = 'swatches';
    let chosenColor = LABEL_COLORS[0];
    LABEL_COLORS.forEach((color) => {
      const sw = document.createElement('button');
      sw.className = 'swatch label';
      sw.type = 'button';
      if (color) sw.dataset.color = color; else sw.classList.add('swatch-none');
      sw.title = color || 'no color';
      if (color === chosenColor) sw.classList.add('selected');
      sw.addEventListener('click', () => {
        chosenColor = color;
        swatches.querySelectorAll('.swatch').forEach((s) => s.classList.remove('selected'));
        sw.classList.add('selected');
      });
      swatches.appendChild(sw);
    });

    const create = document.createElement('button');
    create.className = 'btn-primary';
    create.textContent = 'Create + apply';
    create.addEventListener('click', async () => {
      const name = nameIn.value.trim();
      if (!name && !chosenColor) {
        setStatus('Give the label a name or a color', true);
        return;
      }
      try {
        const lb = await post(`/api/boards/${card.idBoard}/labels`,
          { name, color: chosenColor });
        const updated = await post(`/api/cards/${card.id}/labels`, { idLabel: lb.id });
        applyCardUpdate(updated);
        renderDetailLabels();
        openLabelPopover(anchor);  // reopen with the new label in the list
      } catch (err) {
        setStatus('Create label failed: ' + err.message, true);
      }
    });

    form.append(nameIn, swatches, create);
    body.appendChild(form);
  });
}

// ── due-date popover ───────────────────────────────────────────────

function openDuePopover(anchor) {
  const card = openCard;
  if (!card) return;
  openPopoverAt(anchor, 'Due date', (body) => {
    const dateIn = document.createElement('input');
    dateIn.type = 'date';
    dateIn.className = 'inline-input';
    if (card.due) dateIn.value = card.due.slice(0, 10);

    const doneRow = document.createElement('label');
    doneRow.className = 'due-done-row';
    const doneBox = document.createElement('input');
    doneBox.type = 'checkbox';
    doneBox.checked = !!card.dueComplete;
    doneRow.append(doneBox, document.createTextNode(' Mark complete'));

    const actions = document.createElement('div');
    actions.className = 'inline-actions';
    const save = document.createElement('button');
    save.className = 'btn-primary';
    save.textContent = 'Save';
    const clear = document.createElement('button');
    clear.className = 'btn';
    clear.textContent = 'Remove';
    actions.append(save, clear);

    save.addEventListener('click', async () => {
      if (!dateIn.value) { setStatus('Pick a date first', true); return; }
      // Anchor the picked calendar date at midday UTC so the day reads back the
      // same after the .slice(0,10) render in any timezone within ±12h of UTC.
      const due = dateIn.value + 'T12:00:00.000Z';
      try {
        const updated = await patch(`/api/cards/${card.id}`,
          { due, dueComplete: doneBox.checked });
        applyCardUpdate(updated);
        renderDetailDue();
        closePopover();
        setStatus('Due date set');
      } catch (err) {
        setStatus('Save failed: ' + err.message, true);
      }
    });

    clear.addEventListener('click', async () => {
      try {
        const updated = await patch(`/api/cards/${card.id}`,
          { due: '', dueComplete: false });
        applyCardUpdate(updated);
        renderDetailDue();
        closePopover();
        setStatus('Due date cleared');
      } catch (err) {
        setStatus('Clear failed: ' + err.message, true);
      }
    });

    body.append(dateIn, doneRow, actions);
  });
}

// ── link popover (the card's magnet) ───────────────────────────────
// The one string worth copying out of this UI: a magnet resolves on any machine
// with no shared state, which is what makes it the thing you paste into an
// agent's prompt. The page URL is deliberately not offered — it only works for
// someone already on this server, holding a token.
//
// `_magnet` is a transient key on the card-detail response (see server.py); the
// server builds it so magnet.py stays the only implementation of the grammar.
// It carries the card NAME as a trailing #slug, which parse() ignores — so a
// rename in this panel leaves a stale slug on an already-copied token and it
// still resolves.
function openLinkPopover(anchor) {
  const card = openCard;
  if (!card) return;
  openPopoverAt(anchor, 'Card link', (body) => {
    if (!card._magnet) {
      const p = document.createElement('p');
      p.className = 'popover-note';
      p.textContent = 'No link available for this card — the server could not '
        + 'build one (an http backend with no configured server URL, or a card '
        + 'with no board id).';
      body.appendChild(p);
      return;
    }

    const field = document.createElement('input');
    field.type = 'text';
    field.className = 'inline-input link-field';
    field.readOnly = true;
    field.value = card._magnet;
    field.addEventListener('focus', () => field.select());

    const actions = document.createElement('div');
    actions.className = 'inline-actions';
    const copy = document.createElement('button');
    copy.className = 'btn-primary';
    copy.textContent = 'Copy';
    actions.appendChild(copy);

    copy.addEventListener('click', async () => {
      field.select();
      try {
        // Needs a secure context: fine on localhost and on the https deploy,
        // absent on a plain-http LAN bind — where the text is selected above and
        // Ctrl+C still works, so say so rather than failing silently.
        await navigator.clipboard.writeText(card._magnet);
        setStatus('Link copied');
      } catch (err) {
        setStatus('Could not copy automatically — press Ctrl+C', true);
      }
    });

    body.append(field, actions);
    // Selected on open: the popover is useful even where the clipboard API is
    // unavailable, and the token is long enough that hand-selecting it is a
    // chore. select() leaves the caret at the end, which scrolls a nowrap field
    // to its tail — 24 characters of hex, telling you nothing about what you're
    // looking at. Scroll back so it opens on the `trello://card/<backend>/` end.
    setTimeout(() => { field.focus(); field.select(); field.scrollLeft = 0; }, 0);
  });
}

// ── detail sub-renderers (keep the panel in sync after an edit) ─────

// Merge a server card response into the open card + refresh its board face.
function applyCardUpdate(updated) {
  if (!updated || !openCard || updated.id !== openCard.id) return;
  openCard = { ...openCard, ...updated };
  refreshCardFace(openCard);
}

function renderDetailLabels() {
  const slot = detailEl.querySelector('#detail-labels');
  if (!slot) return;
  slot.innerHTML = '';
  if ((openCard.labels || []).length) slot.appendChild(labelChips(openCard.labels));
}

function renderDetailDue() {
  const slot = detailEl.querySelector('#detail-due');
  if (!slot) return;
  slot.innerHTML = '';
  if (openCard.due) {
    const due = document.createElement('span');
    due.className = 'due' + (openCard.dueComplete ? ' done' : '');
    due.textContent = openCard.due.slice(0, 10) + (openCard.dueComplete ? ' ✓' : '');
    slot.appendChild(due);
  } else {
    slot.appendChild(Object.assign(document.createElement('span'),
      { className: 'detail-due', textContent: 'No due date' }));
  }
}

// ── attachments (live slot, like labels/due) ───────────────────────

function attachmentRow(att) {
  const card = openCard;
  const href = attachmentHref(card.id, att);
  const row = document.createElement('div');
  row.className = 'attachment';

  const thumb = document.createElement('a');
  thumb.className = 'attachment-thumb';
  thumb.href = href;
  thumb.target = '_blank';
  thumb.rel = 'noopener';
  if (isImageAtt(att)) {
    const img = document.createElement('img');
    img.src = href;
    img.alt = att.name || '';
    img.loading = 'lazy';
    thumb.appendChild(img);
  } else {
    thumb.classList.add('generic');
    thumb.textContent = '📎';
  }

  const meta = document.createElement('div');
  meta.className = 'attachment-meta';
  const link = document.createElement('a');
  link.className = 'attachment-name';
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = att.name || att.url || '(unnamed)';
  const sub = document.createElement('div');
  sub.className = 'attachment-sub';
  const bits = [att.isUpload ? 'file' : 'link'];
  const sz = sizeStr(att.bytes);
  if (sz) bits.push(sz);
  sub.textContent = bits.join(' · ');
  meta.append(link, sub);

  const rm = document.createElement('button');
  rm.className = 'attachment-del';
  rm.type = 'button';
  rm.title = 'Remove attachment';
  rm.textContent = '×';
  rm.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!window.confirm(`Remove attachment "${att.name || att.url}"?`)) return;
    try {
      const updated = await del(`/api/cards/${card.id}/attachments/${att.id}`);
      applyCardUpdate(updated);
      renderDetailAttachments();
      setStatus('Attachment removed');
    } catch (err) {
      setStatus('Remove failed: ' + err.message, true);
    }
  });

  row.append(thumb, meta, rm);
  return row;
}

function renderDetailAttachments() {
  const slot = detailEl.querySelector('#detail-attachments');
  if (!slot) return;
  const atts = openCard.attachments || [];
  const head = detailEl.querySelector('#detail-attachments-head');
  if (head) head.textContent = `Attachments (${atts.length})`;
  slot.innerHTML = '';
  if (!atts.length) {
    slot.appendChild(Object.assign(document.createElement('p'),
      { className: 'detail-empty', textContent: 'No attachments yet.' }));
    return;
  }
  atts.forEach((a) => slot.appendChild(attachmentRow(a)));
}

// Popover to add an attachment: upload a file (multipart) or paste a link.
function openAttachmentPopover(anchor) {
  const card = openCard;
  if (!card) return;
  openPopoverAt(anchor, 'Add attachment', (body) => {
    body.appendChild(heading('Upload a file'));
    const fileIn = document.createElement('input');
    fileIn.type = 'file';
    fileIn.className = 'attachment-file-input';
    fileIn.addEventListener('change', async () => {
      const f = fileIn.files && fileIn.files[0];
      if (!f) return;
      try {
        const fd = new FormData();
        fd.append('file', f);
        // No Content-Type header — the browser sets the multipart boundary.
        const updated = await api(`/api/cards/${card.id}/attachments/file`,
          { method: 'POST', body: fd });
        applyCardUpdate(updated);
        renderDetailAttachments();
        closePopover();
        setStatus('Attachment uploaded');
      } catch (err) {
        setStatus('Upload failed: ' + err.message, true);
      } finally {
        // Clear the selection so picking the same file again re-fires `change`
        // (e.g. retrying after a failed upload).
        fileIn.value = '';
      }
    });
    body.appendChild(fileIn);

    body.appendChild(heading('Or paste a link'));
    const form = document.createElement('div');
    form.className = 'attachment-link-form';
    const urlIn = document.createElement('input');
    urlIn.type = 'text';
    urlIn.className = 'inline-input';
    urlIn.placeholder = 'https://…';
    const nameIn = document.createElement('input');
    nameIn.type = 'text';
    nameIn.className = 'inline-input';
    nameIn.placeholder = 'Display name (optional)';
    const add = document.createElement('button');
    add.className = 'btn-primary';
    add.textContent = 'Attach link';
    add.addEventListener('click', async () => {
      const url = urlIn.value.trim();
      if (!url) { setStatus('Enter a URL', true); return; }
      try {
        const updated = await post(`/api/cards/${card.id}/attachments`,
          { url, name: nameIn.value.trim() });
        applyCardUpdate(updated);
        renderDetailAttachments();
        closePopover();
        setStatus('Attachment added');
      } catch (err) {
        setStatus('Attach failed: ' + err.message, true);
      }
    });
    form.append(urlIn, nameIn, add);
    body.appendChild(form);
  });
}

function commentEl(c) {
  const who = (c.memberCreator && c.memberCreator.username) || '?';
  const date = (c.date || '').slice(0, 10);
  const div = document.createElement('div');
  div.className = 'comment';
  const meta = document.createElement('div');
  meta.className = 'comment-meta';
  meta.textContent = `@${who} · ${date}`;
  const body = document.createElement('div');
  body.className = 'comment-body markdown';
  body.appendChild(renderMarkdown((c.data && c.data.text) || ''));
  div.append(meta, body);
  return div;
}

// `fromUrl` marks the boot-time restore of ?card=<id>. That id is whatever was
// last in the address bar — a card since deleted, one hand-edited to junk, or one
// belonging to a different board — and none of those deserve an error panel over
// a board that loaded fine. So a restore that misses closes the drawer and drops
// the param; a click, which can only name a card on screen, still reports.
async function openDetail(cardId, { fromUrl = false } = {}) {
  const seq = ++detailReqSeq;
  closePopover();
  overlayEl.classList.remove('hidden');
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = '<p class="loading">Loading…</p>';
  try {
    const card = await api(`/api/cards/${cardId}`);
    if (seq !== detailReqSeq) return;  // a different card/panel was opened since (openDetail)
    // A restored id from another board would render a card the board behind it
    // doesn't contain (and refreshCardFace would have nothing to patch).
    if (fromUrl && card.idBoard && card.idBoard !== currentBoardId) {
      closeDetail();
      return;
    }
    openCard = card;
    setCardInUrl(card.id);
    detailEl.innerHTML = '';

    const close = document.createElement('button');
    close.className = 'detail-close';
    close.setAttribute('aria-label', 'Close');
    close.textContent = '×';
    close.addEventListener('click', closeDetail);
    detailEl.appendChild(close);

    // ── editable title ──
    const titleBox = document.createElement('div');
    titleBox.className = 'detail-title-box';
    inlineEditable(titleBox, {
      value: card.name,
      multiline: false,
      render: () => {
        const h = document.createElement('h2');
        h.textContent = openCard.name;
        return h;
      },
      save: async (name) => {
        const trimmed = name.trim();
        if (!trimmed) throw new Error('Title cannot be empty');
        const updated = await patch(`/api/cards/${card.id}`, { name: trimmed });
        applyCardUpdate(updated);
      },
    });
    detailEl.appendChild(titleBox);

    // ── action toolbar (labels / due / delete) ──
    const toolbar = document.createElement('div');
    toolbar.className = 'detail-toolbar';
    const labelBtn = document.createElement('button');
    labelBtn.className = 'btn';
    labelBtn.textContent = '🏷 Labels';
    labelBtn.addEventListener('click', (e) => { e.stopPropagation(); openLabelPopover(labelBtn); });
    const dueBtn = document.createElement('button');
    dueBtn.className = 'btn';
    dueBtn.textContent = '📅 Due date';
    dueBtn.addEventListener('click', (e) => { e.stopPropagation(); openDuePopover(dueBtn); });
    const attBtn = document.createElement('button');
    attBtn.className = 'btn';
    attBtn.textContent = '📎 Attach';
    attBtn.addEventListener('click', (e) => { e.stopPropagation(); openAttachmentPopover(attBtn); });
    const linkBtn = document.createElement('button');
    linkBtn.className = 'btn';
    linkBtn.textContent = '🔗 Link';
    linkBtn.addEventListener('click', (e) => { e.stopPropagation(); openLinkPopover(linkBtn); });
    const delBtn = document.createElement('button');
    delBtn.className = 'btn btn-danger';
    delBtn.textContent = '🗑 Delete';
    delBtn.addEventListener('click', async () => {
      if (!window.confirm('Delete this card? It will be archived.')) return;
      try {
        await del(`/api/cards/${card.id}`);
        removeCardFace(card.id);
        closeDetail();
        setStatus('Card deleted');
      } catch (err) {
        setStatus('Delete failed: ' + err.message, true);
      }
    });
    toolbar.append(labelBtn, dueBtn, attBtn, linkBtn, delBtn);
    detailEl.appendChild(toolbar);

    // ── labels (live slot) ──
    const labelsSlot = document.createElement('div');
    labelsSlot.id = 'detail-labels';
    detailEl.appendChild(labelsSlot);
    renderDetailLabels();

    // ── due (live slot) ──
    detailEl.appendChild(heading('Due'));
    const dueSlot = document.createElement('div');
    dueSlot.id = 'detail-due';
    detailEl.appendChild(dueSlot);
    renderDetailDue();

    // ── editable description ──
    detailEl.appendChild(heading('Description'));
    const descBox = document.createElement('div');
    descBox.className = 'detail-desc-box';
    inlineEditable(descBox, {
      value: card.desc || '',
      multiline: true,
      render: () => {
        // A <div>, not a <pre>: the rendered Markdown is block-level elements,
        // and `white-space: pre` would wreck their layout. `breaks: true` in
        // renderMarkdown keeps single newlines showing as line breaks.
        const box = document.createElement('div');
        box.className = 'detail-desc markdown';
        if ((openCard.desc || '').trim()) {
          box.appendChild(renderMarkdown(openCard.desc));
        } else {
          box.textContent = 'Add a more detailed description…';
          box.classList.add('placeholder');
        }
        return box;
      },
      save: async (desc) => {
        const updated = await patch(`/api/cards/${card.id}`, { desc });
        applyCardUpdate(updated);
      },
    });
    detailEl.appendChild(descBox);

    // ── checklists (read-only) ──
    (card.checklists || []).forEach((cl) => {
      const items = cl.checkItems || [];
      const done = items.filter((it) => it.state === 'complete').length;
      detailEl.appendChild(heading(`${cl.name} (${done}/${items.length})`));
      const ul = document.createElement('ul');
      ul.className = 'checklist';
      items.forEach((it) => {
        const li = document.createElement('li');
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = it.state === 'complete';
        box.disabled = true;
        const span = document.createElement('span');
        span.appendChild(linkify(it.name));
        if (it.state === 'complete') span.className = 'done';
        li.append(box, span);
        ul.appendChild(li);
      });
      detailEl.appendChild(ul);
    });

    // ── attachments (live slot) ──
    const aHead = heading('Attachments');
    aHead.id = 'detail-attachments-head';
    detailEl.appendChild(aHead);
    const attSlot = document.createElement('div');
    attSlot.id = 'detail-attachments';
    detailEl.appendChild(attSlot);
    renderDetailAttachments();

    // ── comments (composer on top, list below — newest first) ──
    const comments = card.comments || [];
    const cHead = heading(`Comments (${comments.length})`);
    cHead.id = 'detail-comments-head';
    detailEl.appendChild(cHead);

    // Build the list first so the composer's send handler can prepend into it,
    // even though the composer is appended above it in the DOM.
    const commentsList = document.createElement('div');
    commentsList.id = 'detail-comments';
    comments.forEach((c) => commentsList.appendChild(commentEl(c)));

    const composer = document.createElement('div');
    composer.className = 'comment-composer';
    const ta = document.createElement('textarea');
    ta.className = 'inline-textarea';
    ta.placeholder = 'Write a comment…';
    const send = document.createElement('button');
    send.className = 'btn-primary';
    send.textContent = 'Comment';
    send.addEventListener('click', async () => {
      const text = ta.value.trim();
      if (!text) return;
      send.disabled = true;
      try {
        const action = await post(`/api/cards/${card.id}/comments`, { text });
        ta.value = '';
        commentsList.insertBefore(commentEl(action), commentsList.firstChild);
        cHead.textContent = `Comments (${commentsList.children.length})`;
        setStatus('Comment added');
      } catch (err) {
        setStatus('Comment failed: ' + err.message, true);
      } finally {
        send.disabled = false;
      }
    });
    ta.addEventListener('keydown', (e) => {
      // Escape must not close the drawer — that would discard the in-progress
      // comment draft. Swallow it here so the document handler never sees it.
      if (e.key === 'Escape') { e.stopPropagation(); return; }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send.click(); }
    });
    composer.append(ta, send);
    detailEl.appendChild(composer);
    detailEl.appendChild(commentsList);
  } catch (err) {
    if (seq !== detailReqSeq) return;
    if (fromUrl) { closeDetail(); return; }
    detailEl.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'error';
    p.textContent = 'Failed to load card: ' + err.message;
    detailEl.appendChild(p);
  }
}

// ── manage-boards panel (rename / archive / restore / purge) ────────
// Reuses the detail drawer + overlay as a generic right-side panel. Lists every
// board (open + archived) from GET /api/boards?include_closed=true, splitting on
// `closed`: active boards rename-in-place + Archive; archived boards Restore or
// permanently Delete — the recycling bin.

// Refresh the top-bar nav after a board mutation. If the current board was
// archived or purged it's no longer open, so switch to the first remaining one.
async function reloadBoardsNav() {
  try {
    allBoards = await api('/api/boards');
  } catch (err) {
    setStatus('Could not refresh boards: ' + err.message, true);
    return;
  }
  if (allBoards.some((b) => b.id === currentBoardId)) {
    renderNav();
  } else if (allBoards.length) {
    const next = allBoards[0].id;
    currentBoardId = null;       // so selectBoard doesn't no-op on the stale id
    selectBoard(next);
  } else {
    // The last open board just went away — drop the now-stale board view and
    // null the id (which also disables the live-refresh reload) so nothing
    // points at a closed board behind the panel.
    currentBoardId = null;
    boardEl.innerHTML = '';
    renderNav();
    setStatus('No open boards left — restore one below or create one from the CLI.', true);
  }
}

async function openManageBoards() {
  ++detailReqSeq;  // invalidate any in-flight card detail load for this drawer
  closePopover();
  openCard = null;
  setCardInUrl(null);  // the panel takes over the drawer; no card is open behind it
  overlayEl.classList.remove('hidden');
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = '<p class="loading">Loading…</p>';
  await renderManagePanel();
}

async function renderManagePanel() {
  let boards;
  try {
    boards = await api('/api/boards?include_closed=true');
  } catch (err) {
    detailEl.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'error';
    p.textContent = 'Failed to load boards: ' + err.message;
    detailEl.appendChild(p);
    return;
  }
  detailEl.innerHTML = '';

  const close = document.createElement('button');
  close.className = 'detail-close';
  close.setAttribute('aria-label', 'Close');
  close.textContent = '×';
  close.addEventListener('click', closeDetail);
  detailEl.appendChild(close);

  const title = document.createElement('h2');
  title.textContent = 'Manage boards';
  detailEl.appendChild(title);

  const active = boards.filter((b) => !b.closed);
  const archived = boards.filter((b) => b.closed);

  detailEl.appendChild(heading('Active boards'));
  detailEl.appendChild(boardList(active, activeBoardRow, 'No active boards.'));

  detailEl.appendChild(heading('Archived'));
  const hint = document.createElement('p');
  hint.className = 'board-mgmt-hint';
  hint.textContent = 'Archived boards are hidden but their files are kept — '
    + 'restore one to bring it back, or permanently delete it to remove it for good.';
  detailEl.appendChild(hint);
  detailEl.appendChild(boardList(archived, archivedBoardRow, 'Nothing archived.'));
}

function boardList(boards, rowFn, emptyText) {
  const list = document.createElement('div');
  list.className = 'board-mgmt-list';
  if (!boards.length) {
    const empty = document.createElement('p');
    empty.className = 'board-mgmt-empty';
    empty.textContent = emptyText;
    list.appendChild(empty);
    return list;
  }
  boards.forEach((b) => list.appendChild(rowFn(b)));
  return list;
}

// Row scaffold: name (click-to-rename) + an empty actions slot the caller fills.
function boardRowShell(b) {
  const row = document.createElement('div');
  row.className = 'board-mgmt-row';
  const nameBox = document.createElement('div');
  nameBox.className = 'board-mgmt-name';
  inlineEditable(nameBox, {
    value: b.name,
    multiline: false,
    render: () => {
      const span = document.createElement('span');
      span.textContent = b.name;
      if (b.id === currentBoardId) span.classList.add('board-mgmt-current');
      return span;
    },
    save: async (name) => {
      const trimmed = name.trim();
      if (!trimmed) throw new Error('Board name cannot be empty');
      const updated = await patch(`/api/boards/${b.id}`, { name: trimmed });
      b.name = updated.name;
      await reloadBoardsNav();
    },
  });
  const actions = document.createElement('div');
  actions.className = 'board-mgmt-actions';
  row.append(nameBox, actions);
  return { row, actions };
}

// Run a board mutation, then re-skin the nav and rebuild the panel. `btn` is
// re-enabled on failure (on success the row is replaced by the re-render).
async function boardAction(btn, fn) {
  btn.disabled = true;
  try {
    await fn();
    await reloadBoardsNav();
    await renderManagePanel();
  } catch (err) {
    setStatus(err.message, true);
    btn.disabled = false;
  }
}

function activeBoardRow(b) {
  const { row, actions } = boardRowShell(b);
  const archiveBtn = document.createElement('button');
  archiveBtn.className = 'btn';
  archiveBtn.textContent = 'Archive';
  archiveBtn.addEventListener('click', () =>
    boardAction(archiveBtn, () => patch(`/api/boards/${b.id}`, { closed: true })));
  actions.appendChild(archiveBtn);
  return row;
}

function archivedBoardRow(b) {
  const { row, actions } = boardRowShell(b);
  const restoreBtn = document.createElement('button');
  restoreBtn.className = 'btn';
  restoreBtn.textContent = 'Restore';
  restoreBtn.addEventListener('click', () =>
    boardAction(restoreBtn, () => patch(`/api/boards/${b.id}`, { closed: false })));
  const delBtn = document.createElement('button');
  delBtn.className = 'btn-danger';
  delBtn.textContent = 'Delete';
  delBtn.title = 'Permanently delete — cannot be undone';
  delBtn.addEventListener('click', () => {
    if (!window.confirm(`Permanently delete "${b.name}"? This removes the board `
      + `and all its files and cannot be undone.`)) return;
    boardAction(delBtn, () => del(`/api/boards/${b.id}?confirm=true`));
  });
  actions.append(restoreBtn, delBtn);
  return row;
}

overlayEl.addEventListener('click', closeDetail);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  // Escape dismisses, in order of nesting: an open popover, then any open
  // column menu, then the detail panel.
  if (openPopover) closePopover();
  else if (document.querySelector('.column-menu')) closeColumnMenus();
  else closeDetail();
});
// A click anywhere outside an open popover dismisses it (the popover itself
// stops propagation; the overlay/Escape paths handle the panel). A click
// outside an open column menu (and not on its toggle) closes the menu too.
document.addEventListener('click', (e) => {
  if (openPopover) closePopover();
  if (!e.target.closest('.column-menu') && !e.target.closest('.column-menu-btn')) {
    closeColumnMenus();
  }
});

// ── live refresh ───────────────────────────────────────────────────

let liveSource = null;
let liveErrorCount = 0;  // consecutive SSE failures; reset on a successful open
let liveReloadTimer = null;

// >>> live-debounce (sliced by tests/test_render_state.py) >>>
// One store write is rarely one `change`: re-sorting a column rewrites every card
// file in it, and a Dropbox sync replays those writes again a moment later. The
// server already coalesces per 1s poll tick, but a burst that straddles ticks
// still arrives as several events — and each one used to be a full board reload.
// Collapse them into one, a beat after the last event.
const LIVE_DEBOUNCE_MS = 350;

function scheduleLiveReload() {
  if (liveReloadTimer) clearTimeout(liveReloadTimer);
  liveReloadTimer = setTimeout(() => {
    liveReloadTimer = null;
    if (!currentBoardId) return;
    // A drag that started during the wait: hand it back to onEnd rather than
    // yanking the card out from under the pointer.
    if (liveDragging) { pendingReload = true; return; }
    // Quiet — nobody asked for this refresh, so it shouldn't narrate itself.
    loadBoard(currentBoardId, { quiet: true });
  }, LIVE_DEBOUNCE_MS);
}
// <<< live-debounce <<<

// Reload the current board when the server signals a change. For the local
// backend that's a store file change (a Dropbox sync, or another
// `--backend local` CLI mutation); for the Trello backend the server polls the
// board's latest action, which is why the connection carries the board id —
// reconnect when the selected board changes so it polls the right one. The local
// backend ignores the board param. EventSource auto-reconnects if the stream
// drops; skip the reload mid-drag so a card isn't yanked away.
function initLive(boardId) {
  if (typeof EventSource === 'undefined') return;
  if (liveSource) liveSource.close();
  // Drop a debounced reload queued for the board we're leaving — selectBoard
  // loads the new one itself.
  if (liveReloadTimer) { clearTimeout(liveReloadTimer); liveReloadTimer = null; }
  liveErrorCount = 0;
  liveSource = new EventSource(withToken(withQuery('/api/events', 'board', boardId)));
  liveSource.addEventListener('open', () => { liveErrorCount = 0; });
  liveSource.addEventListener('change', () => {
    if (!currentBoardId) return;
    // Defer a mid-drag change rather than drop it: reloading now would yank the
    // dragged card away, but ignoring it entirely would hide another agent's
    // edit until an unrelated later reload. onEnd consumes pendingReload.
    if (liveDragging) { pendingReload = true; return; }
    scheduleLiveReload();
  });
  liveSource.addEventListener('error', () => {
    // EventSource auto-reconnects on a dropped stream; count consecutive
    // failures (a successful 'open' resets this). If they pile up — server
    // stopped, token rotated — stop the reconnect storm and surface a manual
    // -reload hint instead of hammering silently and freezing the board.
    liveErrorCount += 1;
    if (liveErrorCount >= 5) {
      liveSource.close();
      liveSource = null;
      setStatus('Live refresh disconnected — reload the page to reconnect.', true);
    }
  });
}

// ── boot ───────────────────────────────────────────────────────────

async function init() {
  // Wire the topbar controls BEFORE any early return so they work even with zero
  // open boards — the ⚙ manage-boards panel is the only way to restore an
  // archived board, so it must never depend on a board being loaded first.
  picker.addEventListener('change', () => selectBoard(picker.value));
  starToggle.addEventListener('click', toggleStarCurrent);
  document.getElementById('manage-boards-btn').addEventListener('click', openManageBoards);
  try {
    const boards = await api('/api/boards');
    allBoards = boards;
    if (!boards.length) {
      // Empty state, not a dead end: render the (empty) nav and point the user
      // at ⚙ to restore an archived board or the CLI to create one.
      renderNav();
      setStatus('No open boards. Use ⚙ to restore an archived board, '
        + 'or create one from the CLI.', true);
      return;
    }
    // Restore the board from ?board=<id> on reload/bookmark; fall back to the
    // first board if it's absent or no longer exists for this backend.
    const params = new URLSearchParams(location.search);
    const requested = params.get('board');
    currentBoardId = boards.some((b) => b.id === requested) ? requested : boards[0].id;
    renderNav();
    setBoardInUrl(currentBoardId);
    initLive(currentBoardId);
    await loadBoard(currentBoardId);
    // Then reopen the card the URL names, if any. After the board, so its face
    // is on screen for the patch-in-place path (refreshCardFace) — and only if
    // it's on this board, which openDetail checks. The board it names comes from
    // ?board= alone: every link this UI produces carries both, so inferring the
    // board from the card would only serve a hand-edited URL, at the price of a
    // fetch before the first render.
    const card = params.get('card');
    if (card) openDetail(card, { fromUrl: true });
  } catch (err) {
    setStatus('Could not load boards: ' + err.message, true);
  }
}

init();
