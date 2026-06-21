'use strict';

const boardEl = document.getElementById('board');
const picker = document.getElementById('board-picker');
const statusEl = document.getElementById('status');
const detailEl = document.getElementById('detail');
const overlayEl = document.getElementById('overlay');

let cardSortables = [];
let boardSortable = null;
let liveDragging = false;  // true mid-drag, so a live refresh won't yank a card

function setStatus(msg, isError) {
  statusEl.textContent = msg || '';
  statusEl.classList.toggle('error', !!isError);
}

// When the server is started on a non-loopback host it gates the API behind a
// token, handed to the page as ?token=… on the URL. Thread it onto every API
// request and the SSE stream — neither browser navigation nor EventSource can
// set an Authorization header, so the query param is the only channel.
const AUTH_TOKEN = new URLSearchParams(location.search).get('token');

function withToken(path) {
  if (!AUTH_TOKEN) return path;
  return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(AUTH_TOKEN);
}

function withParam(path, key, value) {
  return path + (path.includes('?') ? '&' : '?') + key + '=' + encodeURIComponent(value);
}

// Reflect the selected board in the URL (?board=<id>) so a reload, bookmark, or
// shared link reopens it instead of snapping back to the first board. Preserves
// any existing query params (notably ?token=) and doesn't add a history entry.
function setBoardInUrl(boardId) {
  const url = new URL(location.href);
  url.searchParams.set('board', boardId);
  history.replaceState(null, '', url);
}

async function api(path, opts) {
  const res = await fetch(withToken(path), opts);
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
      const card = await api(`/api/lists/${list.id}/cards`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      // On a sorted column the new card lands in its sorted slot (anywhere, not
      // the bottom), so reload to place it correctly; manual columns keep the
      // cheap append.
      if (listSort !== 'manual') {
        await loadBoard(picker.value);
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
  const current = list.sort || 'manual';
  [['manual', 'Manual'], ['newest', 'Newest first'], ['oldest', 'Oldest first'], ['name', 'Card name']]
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
          setStatus(value === 'manual' ? 'Sort cleared' : 'Column sorted: ' + value);
          await loadBoard(picker.value);
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
      await loadBoard(picker.value);
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
      try {
        const updated = await patch(`/api/lists/${col.dataset.listId}`, { pos: neighborPos(col) });
        col.dataset.pos = updated.pos;
        rebalanced = !!updated.rebalanced;
        setStatus('Column moved');
      } catch (err) {
        setStatus('Move failed: ' + err.message, true);
      } finally {
        liveDragging = false;
      }
      // A server-side rebalance respread the *other* columns too, so their DOM
      // data-pos is now stale; reload to refresh every position. Done after the
      // finally clears liveDragging so we don't tear down this Sortable mid-onEnd.
      if (rebalanced) await loadBoard(picker.value);
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
        } finally {
          liveDragging = false;
        }
        // A server-side rebalance respread the *other* cards too, so their DOM
        // data-pos is now stale; reload to refresh every position. A cleared sort
        // also needs a reload so the destination column's sort <select> resets.
        // Done after finally clears liveDragging so we don't tear down mid-onEnd.
        if (rebalanced || sortCleared) await loadBoard(picker.value);
      },
    }));
  });
}

function renderBoard(data) {
  boardEl.innerHTML = '';
  const byList = {};
  (data.cards || []).forEach((c) => { (byList[c.idList] = byList[c.idList] || []).push(c); });
  Object.values(byList).forEach((arr) => arr.sort((a, b) => (Number(a.pos) || 0) - (Number(b.pos) || 0)));
  (data.lists || []).forEach((list) => boardEl.appendChild(columnEl(list, byList[list.id] || [])));
  boardEl.appendChild(addListEl(data.board.id));
  initDragging();
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
      await api(`/api/boards/${boardId}/lists`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      setStatus('List added');
      await loadBoard(picker.value);  // re-renders, discarding this affordance
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

async function loadBoard(boardId) {
  setStatus('Loading…');
  try {
    const data = await api(`/api/boards/${boardId}`);
    renderBoard(data);
    setStatus(data.board.name);
  } catch (err) {
    setStatus('Load failed: ' + err.message, true);
  }
}

// ── detail drawer (editable, Trello-style) ─────────────────────────

let openCard = null;       // the card dict currently shown in the detail panel
let openPopover = null;    // the floating popover element (label/due), if any

function closePopover() {
  if (openPopover) { openPopover.remove(); openPopover = null; }
}

function closeDetail() {
  closePopover();
  openCard = null;
  detailEl.classList.add('hidden');
  overlayEl.classList.add('hidden');
}

function heading(text) {
  const h = document.createElement('h3');
  h.textContent = text;
  return h;
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
  buildBody(body);
  pop.appendChild(body);

  // Swallow clicks inside so the document-level outside-click handler doesn't fire.
  pop.addEventListener('click', (e) => e.stopPropagation());
  document.body.appendChild(pop);
  openPopover = pop;

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
    view.addEventListener('click', showEditor);
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
      if (e.key === 'Escape') { e.preventDefault(); showView(); }
      else if (e.key === 'Enter' && (!multiline || e.metaKey || e.ctrlKey)) {
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
  body.className = 'comment-body';
  body.textContent = (c.data && c.data.text) || '';
  div.append(meta, body);
  return div;
}

async function openDetail(cardId) {
  closePopover();
  overlayEl.classList.remove('hidden');
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = '<p class="loading">Loading…</p>';
  try {
    const card = await api(`/api/cards/${cardId}`);
    openCard = card;
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
    toolbar.append(labelBtn, dueBtn, attBtn, delBtn);
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
        const pre = document.createElement('pre');
        pre.className = 'detail-desc';
        pre.textContent = (openCard.desc || '').trim()
          ? openCard.desc
          : 'Add a more detailed description…';
        if (!(openCard.desc || '').trim()) pre.classList.add('placeholder');
        return pre;
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
        span.textContent = it.name;
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
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send.click(); }
    });
    composer.append(ta, send);
    detailEl.appendChild(composer);
    detailEl.appendChild(commentsList);
  } catch (err) {
    detailEl.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'error';
    p.textContent = 'Failed to load card: ' + err.message;
    detailEl.appendChild(p);
  }
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
  liveSource = new EventSource(withToken(withParam('/api/events', 'board', boardId)));
  liveSource.addEventListener('change', () => {
    if (liveDragging || !picker.value) return;
    loadBoard(picker.value);
  });
}

// ── boot ───────────────────────────────────────────────────────────

async function init() {
  try {
    const boards = await api('/api/boards');
    if (!boards.length) {
      setStatus('No boards found for this backend.', true);
      return;
    }
    picker.innerHTML = '';
    boards.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = b.name;
      picker.appendChild(opt);
    });
    picker.addEventListener('change', () => {
      setBoardInUrl(picker.value);
      loadBoard(picker.value);
      initLive(picker.value);
    });
    // Restore the board from ?board=<id> on reload/bookmark; fall back to the
    // first board if it's absent or no longer exists for this backend.
    const requested = new URLSearchParams(location.search).get('board');
    const initial = boards.some((b) => b.id === requested) ? requested : boards[0].id;
    picker.value = initial;
    setBoardInUrl(initial);
    loadBoard(initial);
    initLive(initial);
  } catch (err) {
    setStatus('Could not load boards: ' + err.message, true);
  }
}

init();
