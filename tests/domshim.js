// DOM shim for tests/test_render_state.py.
//
// A real .js file rather than a Python string because it is big enough to want
// syntax highlighting, and it is loaded off disk the same way test_markdown.py
// loads the vendored parser. It supplies what the three app.js slices under test
// touch and nothing more: no createElement, no text nodes, no event dispatch,
// because none of those three needs them. That is the point -- an unsupported
// selector throws, and a missing method is a TypeError, so a slice that outgrows
// the shim fails loudly instead of quietly matching nothing (which would read as
// "the state was never captured").
//
// The other two JS test files (test_linkify.py, test_markdown.py) keep their own
// smaller shims on purpose; see tests/jsrunner.py for why.

// ── elements ───────────────────────────────────────────────────────

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this._classes = new Set();
    this._text = '';
    // Form-field state. Present on every element for simplicity; only the
    // inputs the tests build ever read it.
    this.value = '';
    this.placeholder = '';
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollTop = 0;
    this.scrollLeft = 0;
    const self = this;
    this.classList = {
      add(...names) { names.forEach((n) => self._classes.add(n)); },
      remove(...names) { names.forEach((n) => self._classes.delete(n)); },
      contains(name) { return self._classes.has(name); },
    };
  }

  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }

  get className() { return [...this._classes].join(' '); }

  set textContent(v) { this._text = String(v); }

  get textContent() { return this._text; }

  // The one use in renderBoard is `boardEl.innerHTML = ''` (wipe and rebuild).
  // Refusing anything else keeps the shim from pretending it can parse HTML.
  set innerHTML(v) {
    if (v !== '') throw new Error('domshim: innerHTML only supports clearing ("")');
    this.children.forEach((c) => { c.parentNode = null; });
    this.children = [];
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }

  focus() { document.activeElement = this; }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  querySelector(sel) { return queryAll(this, sel)[0] || null; }

  querySelectorAll(sel) { return queryAll(this, sel); }

  closest(sel) {
    const parts = parseSelector(sel);
    let node = this;
    while (node) {
      if (matchChain(node, parts, parts.length - 1)) return node;
      node = node.parentNode;
    }
    return null;
  }
}

// ── selector engine ────────────────────────────────────────────────
//
// Descendant combinators over compound selectors of tag / .class /
// [attr="value"]. That covers every selector the slices use:
//   .composer-input        .cards        .add-list-form
//   .column[data-list-id="…"] .composer-input
//   .cards[data-list-id="…"]

const COMPOUND_RE = /^(?:[a-zA-Z][\w-]*|\.[\w-]+|\[[\w-]+="[^"]*"\])+$/;
const TOKEN_RE = /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w-]+)="([^"]*)"\]/g;

function parseCompound(text) {
  if (!COMPOUND_RE.test(text)) {
    throw new Error(`domshim: unsupported selector fragment ${JSON.stringify(text)}`);
  }
  const out = { tag: null, classes: [], attrs: [] };
  TOKEN_RE.lastIndex = 0;
  let m;
  while ((m = TOKEN_RE.exec(text)) !== null) {
    if (m[1] !== undefined) out.tag = m[1].toUpperCase();
    else if (m[2] !== undefined) out.classes.push(m[2]);
    else out.attrs.push([m[3], m[4]]);
  }
  return out;
}

function parseSelector(sel) {
  if (sel.includes(',')) {
    throw new Error('domshim: selector lists are not supported');
  }
  const parts = sel.trim().split(/\s+/).filter(Boolean).map(parseCompound);
  if (!parts.length) throw new Error('domshim: empty selector');
  return parts;
}

// `data-list-id` reads off `dataset.listId`, matching how columnEl writes it;
// anything else is a plain property.
function attrValue(el, name) {
  if (name.startsWith('data-')) {
    const key = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return el.dataset[key];
  }
  return el[name];
}

function matchesCompound(el, comp) {
  if (comp.tag && el.tagName !== comp.tag) return false;
  if (!comp.classes.every((c) => el._classes.has(c))) return false;
  return comp.attrs.every(([k, v]) => String(attrValue(el, k)) === v);
}

function matchChain(el, parts, i) {
  if (!matchesCompound(el, parts[i])) return false;
  if (i === 0) return true;
  let ancestor = el.parentNode;
  while (ancestor) {
    if (matchChain(ancestor, parts, i - 1)) return true;
    ancestor = ancestor.parentNode;
  }
  return false;
}

// Descendants of `root` in document order. `root` itself is excluded, matching
// the real querySelectorAll.
function descendants(root, out = []) {
  root.children.forEach((child) => {
    out.push(child);
    descendants(child, out);
  });
  return out;
}

function queryAll(root, sel) {
  const parts = parseSelector(sel);
  return descendants(root).filter((el) => matchChain(el, parts, parts.length - 1));
}

// ── document ───────────────────────────────────────────────────────

// Only what the slices read. `createElement` is deliberately absent: none of the
// three builds an element, and the fixture below constructs `El` directly.
const document = { activeElement: null };

// ── fake timers ────────────────────────────────────────────────────
//
// Module-level declarations shadow node's globals, so the sliced code's
// setTimeout/clearTimeout are these. Nothing sleeps: `clock.advance(ms)` moves
// virtual time and fires whatever is due, including timers scheduled by the
// callbacks it runs.

const clock = {
  now: 0,
  seq: 0,
  timers: new Map(),
  set(fn, delay) {
    const id = ++this.seq;
    this.timers.set(id, { fn, at: this.now + delay, delay });
    return id;
  },
  clear(id) { this.timers.delete(id); },
  advance(ms) {
    const target = this.now + ms;
    for (;;) {
      let due = null;
      for (const [id, t] of this.timers) {
        if (t.at > target) continue;
        if (!due || t.at < due[1].at || (t.at === due[1].at && id < due[0])) due = [id, t];
      }
      if (!due) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].fn();
    }
    this.now = target;
  },
  // Delays of the timers still armed, oldest first.
  pending() { return [...this.timers.values()].map((t) => t.delay); },
};

function setTimeout(fn, delay) { return clock.set(fn, delay); }
function clearTimeout(id) { clock.clear(id); }

// ── board fixture ──────────────────────────────────────────────────
//
// The markup columnEl()/addListEl() produce, reduced to the parts the slices
// look at. tests/test_render_state.py checks these class names and dataset keys
// against the real builders, plus the two nestings the slices navigate -- enough
// to catch a rename or either containment being dropped, not an arbitrary
// restructure.

function makeBoard(listIds) {
  // No class on the root: it stands in for `#board`, which app.js addresses by
  // id. Every class name below IS one app.js writes, and the drift guard in
  // test_render_state.py checks that against the real columnEl/addListEl.
  const board = new El('div');
  listIds.forEach((id) => {
    const col = new El('section');
    col.className = 'column';
    col.dataset.listId = id;

    const cards = new El('div');
    cards.className = 'cards';
    cards.dataset.listId = id;

    const composer = new El('div');
    composer.className = 'composer';
    const input = new El('input');
    input.className = 'composer-input';
    input.placeholder = '+ Add a card';
    composer.appendChild(input);

    col.append(cards, composer);
    board.appendChild(col);
  });

  const addList = new El('div');
  addList.className = 'add-list';
  const placeholder = new El('button');
  placeholder.className = 'add-list-placeholder';
  const form = new El('div');
  form.className = 'add-list-form hidden';
  const addInput = new El('input');
  addInput.className = 'add-list-input';
  form.appendChild(addInput);
  addList.append(placeholder, form);
  board.appendChild(addList);

  return board;
}

// Convenience accessors for the drivers.
function composerIn(board, listId) {
  return board.querySelector(`.column[data-list-id="${listId}"] .composer-input`);
}

function cardsIn(board, listId) {
  return board.querySelector(`.cards[data-list-id="${listId}"]`);
}
