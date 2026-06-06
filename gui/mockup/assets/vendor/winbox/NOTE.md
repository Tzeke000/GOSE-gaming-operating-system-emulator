# Vendored: WinBox.js

- **What:** WinBox.js v0.2.82, the single-file bundle (`winbox.bundle.min.js` = JS with the
  stylesheet inlined — one `<script>` tag, no separate CSS link needed).
- **Source:** https://github.com/nextapps-de/winbox (fetched from the official npm dist via
  unpkg: `https://unpkg.com/winbox@0.2.82/dist/winbox.bundle.min.js`, 2026-06-06).
- **License:** Apache-2.0 (full text in `LICENSE`, fetched from the upstream repo).
- **Why:** the web-window frame mechanics for the GOSE windowing system (docs/23 §8) —
  drag/resize/min/max/focus frames + `mount()`/iframe windows, ~6 KB gzip, zero deps.
  GOSE supplies its own dock/controller UX on top (`assets/gose-wm.js`); WinBox is the
  frame engine only.
- **Local changes:** none. Vendored verbatim.
