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

// The same float-midpoint rule the CLI uses for `card pos` / `list pos`:
// land between the new DOM neighbours, or send the "top"/"bottom" keyword at
// an edge so the backend resolves it against the destination's current bounds.
function neighborPos(el) {
  const prev = el.previousElementSibling;
  const next = el.nextElementSibling;
  const pp = prev ? parseFloat(prev.dataset.pos) : null;
  const np = next ? parseFloat(next.dataset.pos) : null;
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

  const header = document.createElement('div');
  header.className = 'column-header';
  const name = document.createElement('span');
  name.className = 'column-name';
  name.textContent = list.name;
  const count = document.createElement('span');
  count.className = 'column-count';
  count.textContent = cards.length;
  header.append(name, count);
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
      cardsWrap.appendChild(cardEl(card));
      countFor(cardsWrap);
      setStatus('Card added');
    } catch (err) {
      setStatus('Add failed: ' + err.message, true);
    }
  });
  composer.appendChild(input);
  col.appendChild(composer);
  return col;
}

function initDragging() {
  if (boardSortable) boardSortable.destroy();
  cardSortables.forEach((s) => s.destroy());
  cardSortables = [];

  // Reorder columns (grab by header only, so card drags don't trigger it).
  boardSortable = Sortable.create(boardEl, {
    group: 'columns',
    draggable: '.column',
    handle: '.column-header',
    animation: 150,
    onStart: () => { liveDragging = true; },
    onEnd: async (evt) => {
      const col = evt.item;
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
        let rebalanced = false;
        try {
          const updated = await patch(`/api/cards/${item.dataset.id}`, {
            idList: toList,
            pos: neighborPos(item),
          });
          item.dataset.pos = updated.pos;
          item.dataset.list = updated.idList;
          rebalanced = !!updated.rebalanced;
          setStatus('Card moved');
        } catch (err) {
          setStatus('Move failed: ' + err.message, true);
        } finally {
          liveDragging = false;
        }
        // A server-side rebalance respread the *other* cards too, so their DOM
        // data-pos is now stale; reload to refresh every position. Done after the
        // finally clears liveDragging so we don't tear down this Sortable mid-onEnd.
        if (rebalanced) await loadBoard(picker.value);
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
  initDragging();
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
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

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
