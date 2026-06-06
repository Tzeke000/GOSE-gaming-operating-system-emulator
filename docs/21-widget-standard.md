# 21 — Desktop Widget Standard

Status: implemented (2026-06-06). Base: `gui/mockup/assets/widget.js` +
`gui/mockup/assets/widget.css`. Consumers: `gui/mockup/gose-home.html` (mounts the
widgets) and `gui/mockup/gose-widgets.html` (the enable/disable toggles).

> One contract for every desktop widget, so when you build a new widget it is
> *built the same way and reacts the same* as every other. A widget-wide
> requirement (e.g. "every item needs an icon") is defined **once** in the base
> and therefore applies to **all** widgets — no per-widget hiccups.

This doc is Zeke's to review and extend.

---

## 1. What the base gives every widget for free

Declare a widget with a plain object and the base (`GW`) supplies, identically:

1. **Header** — icon + title, consistent styling, doubles as the drag handle.
2. **A body of focusable items** — each item is `icon (+ fallback) + label (+ sub) + action`.
   Whole-widget single-action (e.g. Terminal, System) is also supported.
3. **Identical keyboard + controller nav** — the live bridge maps arrows→move,
   A→Enter, B→Esc, L1/R1→`[`/`]`. Entering a widget focuses its first item;
   arrows move; A activates. Same for every widget.
4. **The BLUE focus glow** — one blue highlight applied on focus/hover. It is the
   **same blue everywhere**, including the Controllers and Notifications widgets.
   The glow is **reserved for focus**. There are **no state-coloured glows**
   (no green "controller connected", no amber "notification") — state is shown by
   a subtle header **badge** instead.
5. **Hover-item naming** — focusing/hovering an item shows its name top-centre as
   `‹widget name› · ‹item name›` (whole-widget targets show `◀ name ▶`).
6. **Declared grid size + non-overlapping placement** — `size` + `pos`; the Hub
   defaults to the top-centre focal point (see §5).
7. **Consistent loading / empty / focus states** — a `Loading…` placeholder while
   `load` runs; a declared `empty` message when there's nothing; uniform focus CSS.

---

## 2. The declaration shape

```js
GW.define({
  id:        "wlibrary",            // unique; must match a GW.catalog entry
  title:     "Library",            // header text (upper-cased automatically)
  icon:      "layout-grid",        // header icon (assets/icons/<name>.svg); also item-icon fallback
  size:      { w:300, h:undefined },// width in px; height auto unless h given
  pos:       { x:770, y:180 },     // default placement (px). Also {right,top,bottom,left}
  poll:      6000,                 // optional: re-run load+render every N ms (omit = render once)
  load:      () => fetch('/widgets/library',{cache:'no-store'}).then(r=>r.json()),
  render:    (data) => ({ /* see §3 */ }),
  onActivate:() => { location.href='gose-taskman.html'; }, // optional: whole-widget action
  empty:     "No games played yet.",   // optional default empty message
  badge:     (data) => null,           // optional: subtle header badge (NOT a glow)
});
```

- `load` is optional and may return a Promise (use `Promise.all([...])` for
  multiple endpoints). Its resolved value is passed to `render`. Errors fall back
  to the empty state — `render` is always called defensively.
- `render(data)` is pure: it returns a description (see §3); the base turns that
  into DOM, wires actions, paints icons, and rebuilds nav. Never touch the DOM
  directly from `render`.
- `onActivate` makes the **whole widget** a single focus target (used when there
  are no inner items). If the widget also has items, `onActivate` is ignored.

### Item shape (what `render` returns inside `items`/`sections`/`footer`/`pins`)

```js
{
  icon:  "gamepad-2",          // optional; falls back to the widget icon, then "square"
  img:   "/fs/file?path=...",  // optional cover art layered over the icon; self-removes on error
  label: "Donkey Kong",        // required — shown, and used for top-centre naming
  sub:   "SNES · 27m",         // optional secondary line
  // exactly one action:
  go:        "gose-library.html",        // navigate
  launch:    { system:"snes", game:"…" },// POST /launch (object or pre-stringified)
  cmd:       "flatpak run org.x",        // POST /launch {cmd}
  onActivate:() => {…},                  // arbitrary callback
  // optional trailing chip (tier / "OS" / "Revoke"):
  chip: { text:"Admin", color:"#ffb74d", onActivate:()=>{…} }
}
```

> **Single-definition example:** the "every item has an icon (with a fallback)"
> rule lives in exactly one place — `buildItem()` in `widget.js`. Change it there
> and every widget, present and future, obeys.

---

## 3. What `render(data)` may return

Return an **array** (treated as `items`) or an **object** with any of:

| field      | type                                   | purpose                                         |
|------------|----------------------------------------|-------------------------------------------------|
| `items`    | `Item[]`                               | a flat list of focusable items                  |
| `sections` | `[{label, items:Item[]}]`              | labelled groups (e.g. MOST PLAYED / RECENT)     |
| `pins`     | `Item[]`                               | compact focusable chips (quick shortcuts)       |
| `footer`   | `Item`                                 | a full-width "Open X" action button             |
| `body`     | `string` \| `(el)=>void`               | custom **non-focusable** content (clock, stats) |
| `badge`    | `{text?, dot?, muted?}` \| `null`      | subtle header badge (overrides `spec.badge`)    |
| `empty`    | `string`                               | empty-state text for this render                |

If nothing focusable and no `body` is produced, the `empty` message shows.

### State → badge, never a glow

State indicators are **subtle header badges**, e.g.:
- Controllers: `{text:"1", dot:true}` (count + a small dot)
- Notifications: `{text:"3 new", dot:true}`
- System: `{text:"live", dot:true}` / `{text:"offline", muted:true}`

The blue glow stays reserved for focus. (Earlier green/amber state-glows were
removed by request.)

---

## 4. Lifecycle

```
GW.define(spec)         → registers the spec
GW.mount()              → for each spec: build panel (header+body), place it,
                          honour enabled/disabled + saved position, show Loading…,
                          start the load/poll loop, then init nav once.
load()  → render(data)  → base builds DOM, paints icons, sets badge, rebuilds nav
poll                    → re-runs load()+render() every N ms (if visible)
GW.refresh(id)          → force one out-of-band reload+render (e.g. after an action)
drag header             → updates and persists position (localStorage gose-wpos)
```

- **Enable/disable** uses `localStorage["gose-wenabled"]`; defaults come from
  `GW.catalog[].def`. The Widgets app (`gose-widgets.html`) renders straight from
  `GW.catalog`, so a new widget appears there automatically.
- **Placement** persists per-widget; bumping `LAYOUT_V` in `widget.js` discards
  stale saved positions once so new defaults take effect.
- **Navigation zones** = `[Menu (sidebar)] + visible widgets (reading order) + [Dock]`.
  Reading order is top→bottom rows, then left→right, so ←/→ feels natural.

---

## 5. Default layout (1920×1080)

Hub is the **top-centre focal point**; content widgets are grouped in a
left/centre block; status widgets are grouped as a **right-edge dock**;
Terminal sits under the launcher. Everything is non-overlapping.

```
 Hub (800,54) ── top-centre focal (clock + date + shortcuts)

 CONTENT (left/centre block)              STATUS DOCK (right edge, x≈1650)
  Apps & Games (110,180, tall)             Controllers (1650,54)
  Emulators    (440,180)                   Notifications(1650,196)
  Library      (770,180)                   System       (1650,430)
  Store        (1100,180)
  AI Players   (770,440)
  Steam        (1100,440, opt-in)
  Terminal     (110,600, under launcher)
```

---

## 6. Worked example — add a new widget

Say we want a **Downloads** widget showing in-progress installs from `/downloads`.

**1. Add a catalog entry** (single source for the list + default) in
`assets/widget.js` → `CATALOG`:

```js
{id:"downloads", name:"Downloads", desc:"In-progress installs & updates",
 icon:"download", group:"content", def:0},
```

**2. Declare it** in `gose-home.html` (any sensible non-overlapping `pos`):

```js
GW.define({
  id:"downloads", title:"Downloads", icon:"download",
  size:{w:300}, pos:{x:1100,y:700}, poll:4000,
  load:()=>fetch('/downloads',{cache:'no-store'}).then(r=>r.json()),
  empty:"Nothing downloading.",
  render:(d)=>{ d=d||{};
    const items=(d.jobs||[]).map(j=>({
      icon:"download", label:j.name, sub:Math.round(j.pct)+"% · "+j.eta,
      onActivate:()=>fetch('/downloads/open',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({id:j.id})})
    }));
    return { items, badge: items.length?{text:String(items.length),dot:true}:null };
  }
});
```

That's it. The widget now has the header, the blue focus glow, controller/keyboard
nav, top-centre item naming, loading/empty states, drag-to-move, and a toggle in
the Widgets app — all inherited, all identical to every other widget. No glow code,
no nav code, no placement code to write.
