// GOSE storage auto-import offer (docs/25 §5.3).
// Polls /storage/pending; when an inserted SD/USB with ROMs is detected, fires a one-time toast
// (reusing the GOSE.notify path) AND shows a pad-navigable modal: [Add all] [Choose] [Not now].
// Input: the GOSE pad is translated to KEY events by gose-pad-nav.py, so we only need a
// capture-phase keydown handler. widget.js binds keydown in the bubble phase, so capturing here
// (and stopping propagation while the modal is up) takes nav priority cleanly.
(function () {
  if (window.__goseStorageOffer) return; window.__goseStorageOffer = 1;
  var SEEN = "gose-storage-seen";       // vol_ids we've already toasted (dedupe the notification)
  var cur = null;                        // the vol_id currently shown
  var foc = 0;                           // focused button index
  var BTNS = [
    { id: "add",  label: "Add all" },
    { id: "pick", label: "Choose" },
    { id: "no",   label: "Not now" }
  ];

  function seen() { try { return JSON.parse(localStorage.getItem(SEEN) || "[]"); } catch (e) { return []; } }
  function markSeen(v) { var a = seen(); if (a.indexOf(v) < 0) { a.push(v); localStorage.setItem(SEEN, JSON.stringify(a.slice(-40))); } }

  function ensureDom() {
    if (document.getElementById("gose-import-modal")) return;
    var st = document.createElement("style");
    st.textContent =
      "#gose-import-ov{position:fixed;inset:0;z-index:60;background:#05050bcc;backdrop-filter:blur(3px);" +
      "display:none;align-items:center;justify-content:center}" +
      "#gose-import-ov.on{display:flex}" +
      "#gose-import-modal{width:520px;max-width:92vw;background:#0d0d1e;border:1px solid #ffffff1f;" +
      "border-radius:18px;padding:24px 26px;box-shadow:0 24px 80px #000a}" +
      "#gose-import-modal .gi-ic{width:42px;height:42px;border-radius:12px;background:#6a4dff33;color:#dfe5f5;" +
      "display:flex;align-items:center;justify-content:center;margin-bottom:12px}" +
      "#gose-import-modal .gi-ic .ic{font-size:22px}" +
      "#gose-import-modal h2{margin:0 0 4px;font-size:19px}" +
      "#gose-import-modal .gi-sub{color:#aeb4d2;font-size:13px;line-height:1.5;margin-bottom:6px}" +
      "#gose-import-modal .gi-sys{color:#8b92b0;font-size:12px;margin-bottom:18px}" +
      "#gose-import-modal .gi-btns{display:flex;gap:10px}" +
      "#gose-import-modal .gi-b{flex:1;text-align:center;padding:12px 8px;border-radius:12px;" +
      "background:#ffffff0d;border:2px solid transparent;font-weight:600;font-size:14px;cursor:pointer}" +
      "#gose-import-modal .gi-b.focus{border-color:var(--accent);background:#5cd0ff14;" +
      "box-shadow:0 0 18px #5cd0ff55}" +
      "#gose-import-modal .gi-b.primary{background:#6a4dff2e}";
    document.head.appendChild(st);
    var ov = document.createElement("div");
    ov.id = "gose-import-ov";
    ov.innerHTML =
      '<div id="gose-import-modal" role="dialog" aria-modal="true">' +
      '<div class="gi-ic"><span class="ic" data-i="hard-drive"></span></div>' +
      '<h2 id="gi-title">ROMs found</h2>' +
      '<div class="gi-sub" id="gi-sub"></div>' +
      '<div class="gi-sys" id="gi-sys"></div>' +
      '<div class="gi-btns" id="gi-btns"></div></div>';
    document.body.appendChild(ov);
  }

  function paintIcons() {
    document.querySelectorAll("#gose-import-ov [data-i]").forEach(function (e) {
      e.style.setProperty("--u", "url(assets/icons/" + e.dataset.i + ".svg)");
    });
  }

  function renderBtns() {
    var html = BTNS.map(function (b, i) {
      return '<div class="gi-b ' + (i === 0 ? "primary " : "") + (i === foc ? "focus" : "") +
        '" data-bi="' + i + '">' + b.label + "</div>";
    }).join("");
    document.getElementById("gi-btns").innerHTML = html;
    document.querySelectorAll("#gi-btns .gi-b").forEach(function (el) {
      el.onclick = function () { foc = +el.dataset.bi; activate(); };
    });
  }

  function show(off) {
    ensureDom();
    cur = off.vol_id; foc = 0;
    var nSys = (off.systems || []).length;
    document.getElementById("gi-title").textContent = "ROMs found on " + (off.label || "this card");
    document.getElementById("gi-sub").textContent =
      off.rom_count + (off.rom_count === 1 ? " game" : " games") +
      " across " + nSys + (nSys === 1 ? " system" : " systems") + " — add to your Library?";
    var names = (off.systems || []).map(function (s) { return s.name + " (" + s.count + ")"; });
    document.getElementById("gi-sys").textContent =
      names.slice(0, 6).join("  ·  ") + (names.length > 6 ? "  …" : "") +
      (off.ambiguous ? ("   ·   " + off.ambiguous + " unrecognised (skipped)") : "");
    renderBtns(); paintIcons();
    document.getElementById("gose-import-ov").classList.add("on");
  }

  function hide() { var o = document.getElementById("gose-import-ov"); if (o) o.classList.remove("on"); cur = null; }
  function isOpen() { var o = document.getElementById("gose-import-ov"); return o && o.classList.contains("on"); }

  function toast(t, b) { if (window.GOSE && GOSE.notify) GOSE.notify({ title: t, body: b, icon: "hard-drive" }); }

  function activate() {
    var b = BTNS[foc], vol = cur;
    if (!vol) return;
    if (b.id === "no") {
      hide();
      fetch("/storage/dismiss", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vol_id: vol }) }).catch(function () {});
      return;
    }
    if (b.id === "pick") { location.href = "gose-import.html?vol=" + encodeURIComponent(vol); return; }
    // Add all
    hide();
    toast("Importing ROMs", "Copying games into your Library…");
    fetch("/storage/import", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vol_id: vol, all: true }) })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok) {
          var msg = j.imported + (j.imported === 1 ? " game added" : " games added") +
            (j.skipped ? (" · " + j.skipped + " already had") : "") +
            (j.aborted ? " · stopped (card removed)" : "");
          toast("Added to Library", msg);
        } else { toast("Import failed", (j && j.error) || "could not copy ROMs"); }
      }).catch(function () { toast("Import failed", "could not reach storage service"); });
  }

  function move(d) { foc = (foc + d + BTNS.length) % BTNS.length; renderBtns(); }

  // capture-phase: take input priority over widget.js (bubble) while the modal is open
  window.addEventListener("keydown", function (e) {
    if (!isOpen()) return;
    // WebKitGTK in the kiosk can deliver arrow keys as either "ArrowRight" or the bare "Right"
    // (older DOM naming) -- accept both so pad nav (gose-pad-nav emits the keysym) works.
    var k = e.key;
    if (k === "ArrowLeft" || k === "Left") move(-1);
    else if (k === "ArrowRight" || k === "Right" || k === "Tab") move(1);
    else if (k === "Enter" || k === " ") activate();
    else if (k === "Escape" || k === "Backspace") { foc = 2; activate(); }
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);

  function poll() {
    fetch("/storage/pending", { cache: "no-store" }).then(function (r) { return r.json(); })
      .then(function (j) {
        var off = j && j.offer;
        if (off && off.rom_count > 0) {
          if (seen().indexOf(off.vol_id) < 0) {
            markSeen(off.vol_id);
            toast("ROMs found on " + (off.label || "a card"),
              off.rom_count + " games detected — open to add them to your Library.");
          }
          if (!isOpen() || cur !== off.vol_id) show(off);
        } else if (isOpen()) {
          hide();   // offer cleared (ejected / imported / dismissed elsewhere)
        }
      }).catch(function () {});
  }

  function start() { poll(); setInterval(poll, 4000); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
