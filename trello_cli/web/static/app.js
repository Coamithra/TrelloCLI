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

  if (card.due) {
    const meta = document.createElement('div');
    meta.className = 'card-meta';
    const due = document.createElement('span');
    due.className = 'due' + (card.dueComplete ? ' done' : '');
    due.textContent = card.due.slice(0, 10);
    meta.appendChild(due);
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

  // Per-column sort picker (persisted auto-sort). Changing it persists the
  // setting AND re-sorts existing cards server-side, so we reload to reflect
  // the new order; every later add then auto-places per the saved sort.
  const sortSel = document.createElement('select');
  sortSel.className = 'column-sort';
  sortSel.title = 'Sort this column (saved; new cards auto-place)';
  [['manual', 'Manual'], ['newest', 'Newest'], ['oldest', 'Oldest'], ['name', 'Name']]
    .forEach(([value, label]) => {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = label;
      sortSel.appendChild(opt);
    });
  sortSel.value = listSort;
  sortSel.addEventListener('change', async () => {
    try {
      await patch(`/api/lists/${list.id}`, { sort: sortSel.value });
      setStatus(sortSel.value === 'manual' ? 'Sort cleared' : 'Column sorted: ' + sortSel.value);
      await loadBoard(picker.value);
    } catch (err) {
      setStatus('Sort failed: ' + err.message, true);
    }
  });

  // Delete (archive) the column. A small ⋯ menu keeps the affordance Trello-like
  // without crowding the header; the only action today is delete.
  const menuBtn = document.createElement('button');
  menuBtn.className = 'column-menu-btn';
  menuBtn.type = 'button';
  menuBtn.textContent = '⋯';
  menuBtn.title = 'List actions';
  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleColumnMenu(col, list);
  });

  header.append(name, count, sortSel, menuBtn);
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

// A tiny per-column actions menu (currently just Delete). Closes any other open
// menu first; an outside click / Escape closes it (wired once at boot).
function toggleColumnMenu(col, list) {
  const existing = col.querySelector('.column-menu');
  closeColumnMenus();
  if (existing) return;  // it was open → toggle shut
  const menu = document.createElement('div');
  menu.className = 'column-menu';
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

  // Reorder columns (grab by header only, so card drags don't trigger it).
  // `filter` keeps the "Add another list" affordance from being draggable.
  boardSortable = Sortable.create(boardEl, {
    group: 'columns',
    draggable: '.column',
    // Keep the add-list affordance non-draggable, and stop the header controls
    // (sort picker, actions menu) from initiating a column drag.
    filter: '.add-list, .column-sort, .column-menu-btn, .column-menu',
    handle: '.column-header',
    animation: 150,
    onStart: () => { liveDragging = true; },
    onEnd: async (evt) => {
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
      onStart: () => { liveDragging = true; },
      onEnd: async (evt) => {
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

// ── detail drawer (read-only) ──────────────────────────────────────

function closeDetail() {
  detailEl.classList.add('hidden');
  overlayEl.classList.add('hidden');
}

function heading(text) {
  const h = document.createElement('h3');
  h.textContent = text;
  return h;
}

async function openDetail(cardId) {
  overlayEl.classList.remove('hidden');
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = '<p class="loading">Loading…</p>';
  try {
    const card = await api(`/api/cards/${cardId}`);
    detailEl.innerHTML = '';

    const close = document.createElement('button');
    close.className = 'detail-close';
    close.setAttribute('aria-label', 'Close');
    close.textContent = '×';
    close.addEventListener('click', closeDetail);
    detailEl.appendChild(close);

    const title = document.createElement('h2');
    title.textContent = card.name;
    detailEl.appendChild(title);

    if ((card.labels || []).length) detailEl.appendChild(labelChips(card.labels));

    if (card.due) {
      const due = document.createElement('p');
      due.className = 'detail-due';
      due.textContent = 'Due: ' + card.due.slice(0, 10) + (card.dueComplete ? ' (done)' : '');
      detailEl.appendChild(due);
    }

    if ((card.desc || '').trim()) {
      detailEl.appendChild(heading('Description'));
      const desc = document.createElement('pre');
      desc.className = 'detail-desc';
      desc.textContent = card.desc;
      detailEl.appendChild(desc);
    }

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

    const comments = card.comments || [];
    detailEl.appendChild(heading(`Comments (${comments.length})`));
    comments.forEach((c) => {
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
      detailEl.appendChild(div);
    });
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
  if (e.key === 'Escape') { closeDetail(); closeColumnMenus(); }
});
// A click anywhere outside an open column menu (and not on its toggle) closes it.
document.addEventListener('click', (e) => {
  if (!e.target.closest('.column-menu') && !e.target.closest('.column-menu-btn')) {
    closeColumnMenus();
  }
});

// ── live refresh ───────────────────────────────────────────────────

// Reload the current board when the server signals a store change (a Dropbox
// sync, or another `--backend local` CLI mutation). EventSource auto-reconnects
// if the stream drops; skip the reload mid-drag so a card isn't yanked away.
function initLive() {
  if (typeof EventSource === 'undefined') return;
  const es = new EventSource(withToken('/api/events'));
  es.addEventListener('change', () => {
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
    picker.addEventListener('change', () => loadBoard(picker.value));
    loadBoard(boards[0].id);
    initLive();
  } catch (err) {
    setStatus('Could not load boards: ' + err.message, true);
  }
}

init();
