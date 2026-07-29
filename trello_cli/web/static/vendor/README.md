# Vendored third-party assets

There is no build step for `trello_cli/web/static/` — the browser loads these files
directly via `<script>` tags in `index.html`, and `pyproject.toml` ships them as package
data (`"trello_cli.web" = [..., "static/vendor/*"]`).

Every file here is a **pinned release artifact pulled from the npm registry** and committed
verbatim (bar the noted edits). Do not `npm install` to update them, and do not run package
install scripts: fetch the tarball, verify its sha256, extract the single dist file.

Hashes below are of the **upstream bytes**. A working-tree copy may differ by line endings
(git's autocrlf); compare after normalising `\r\n` → `\n`.

---

## markdown-it 14.3.0

- **Used for:** parsing Markdown in card descriptions and comments. **Parser only** — its
  HTML renderer is never called. `app.js` walks the token stream from `md.parse()` and
  builds DOM nodes itself, so user text never passes through an HTML parser and no
  sanitizer is needed. See the `renderMarkdown` block in `app.js`.
- **License:** MIT (full text below)
- **Source tarball:** https://registry.npmjs.org/markdown-it/-/markdown-it-14.3.0.tgz
  - sha256 `c68c4479fee440ed43f82f9807f1777afc1595fc5c9adec4317da230e12643e6`
- **File taken:** `package/dist/markdown-it.min.js` (124,782 bytes, UMD, self-contained —
  bundles linkify-it, mdurl, punycode.js, uc.micro, entities)
  - sha256 `70fe17bd06c7fa819f03a1ed10957904318103624198845dc893b309bf495e28`
- **Local modification:** the trailing `//# sourceMappingURL=markdown-it.min.js.map` comment
  was removed (the `.map` file is not vendored, so it would 404 in devtools). Nothing else.
- **Project:** https://github.com/markdown-it/markdown-it

```
Copyright (c) 2014 Vitaly Puzrin, Alex Kocharin.

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```

---

## Sortable 1.15.6

Recorded retroactively — this file predates this note.

- **Used for:** drag-and-drop of cards and columns on the board.
- **License:** MIT (full text below)
- **Source tarball:** https://registry.npmjs.org/sortablejs/-/sortablejs-1.15.6.tgz
  - sha256 `7a5de41e4a184f43178f3b7cfd4e22f9ac2945a4b7c715ddc3e1c752b5a631dd`
- **File taken:** `package/Sortable.min.js` (45,092 bytes, UMD)
  - sha256 `6d0a831fc19b4bae851797ad3393157e861afb7862459c11226359b27e2c4337`
- **Local modification:** none (the committed copy differs from upstream only by a trailing
  CRLF; byte-identical after line-ending normalisation).
- **Project:** https://github.com/SortableJS/Sortable

```
MIT License

Copyright (c) 2019 All contributors to Sortable

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
