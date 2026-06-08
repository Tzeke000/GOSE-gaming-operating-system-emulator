/* GOSE accessibility applier — one shared script, included on every page right after
   themes/cursor. Reads the gose-* a11y prefs from localStorage and applies them to <html>:
   text scale, high-contrast, bold, reduce-motion/transparency, larger focus, colorblind
   palette/correction (public-domain libDaltonLens / hail2u feColorMatrix matrices),
   colorblind-safe accent presets (per-CVD-type safe hue sets), and hold-alternatives. */
(function () {
  var root = document.documentElement, LS = localStorage;
  function g(k, d) { var v = LS.getItem(k); return v == null ? d : v; }
  function setAttr(a, v) { if (v == null) root.removeAttribute(a); else root.setAttribute(a, v); }

  // CVD correction matrices (linearRGB) — public domain
  var SVG = '<svg id="gose-a11y-filters" aria-hidden="true" style="position:absolute;width:0;height:0;overflow:hidden">' +
    '<defs>' +
    '<filter id="cb-deut" color-interpolation-filters="linearRGB"><feColorMatrix type="matrix" values="0.367 0.861 -0.228 0 0  0.280 0.673 0.047 0 0  -0.012 0.043 0.969 0 0  0 0 0 1 0"/></filter>' +
    '<filter id="cb-prot" color-interpolation-filters="linearRGB"><feColorMatrix type="matrix" values="0.152 1.053 -0.205 0 0  0.115 0.786 0.099 0 0  -0.004 -0.048 1.052 0 0  0 0 0 1 0"/></filter>' +
    '<filter id="cb-trit" color-interpolation-filters="linearRGB"><feColorMatrix type="matrix" values="1.256 -0.077 -0.179 0 0  -0.078 0.931 0.148 0 0  0.005 0.691 0.304 0 0  0 0 0 1 0"/></filter>' +
    '</defs></svg>';
  function ensureFilters() {
    if (!document.getElementById('gose-a11y-filters') && document.body) {
      document.body.insertAdjacentHTML('beforeend', SVG);
    }
  }

  function apply() {
    ensureFilters();
    // theme + accent — the canonical values live server-side (/ui/prefs); localStorage is
    // the per-page cache this reads. Accent overrides the theme token so it shows on EVERY
    // page (incl. lock); empty/invalid accent = the theme's own color.
    root.dataset.theme = g('gose-theme', 'onyx');
    var acc = g('gose-accent', '');
    if (/^#[0-9a-fA-F]{6}$/.test(acc)) {
      root.style.setProperty('--accent', acc);
      root.style.setProperty('--focusglow', '0 0 0 2px ' + acc + ', 0 0 18px ' + acc + '55');
    } else {
      root.style.removeProperty('--accent');
      root.style.removeProperty('--focusglow');
    }
    root.style.setProperty('--ui-scale', g('gose-ui-scale', '1'));
    // UI Scale (Display > UI scale): shell-wide zoom — 4K TV vs 1080p monitor vs handheld.
    // Stored as a fractional multiplier (0.9/1/1.1/1.25); applied as CSS zoom on <html>
    // so the whole page viewport scales uniformly without touching rem/font-size (text-size
    // a11y is a separate control). Default 1 = zoom:1 = no change from today.
    root.style.zoom = g('gose-uiscale', '1');
    setAttr('data-contrast', g('gose-contrast', 'off') === 'on' ? 'high' : null);
    setAttr('data-bold', g('gose-bold', 'off') === 'on' ? '1' : null);
    setAttr('data-opaque', g('gose-opaque', 'off') === 'on' ? '1' : null);
    setAttr('data-focus', g('gose-focus', 'off') === 'on' ? 'thick' : null);
    setAttr('data-motion', g('gose-motion', 'off') === 'on' ? 'reduce' : null);
    var cb = g('gose-cb', 'off');   // off | friendly | deut | prot | trit
    setAttr('data-cbpalette', cb === 'friendly' ? 'friendly' : null);
    root.style.filter = ({ deut: 'url(#cb-deut)', prot: 'url(#cb-prot)', trit: 'url(#cb-trit)' })[cb] || '';
    // Colorblind-safe accent palette preset (gose-cb-palette): off | deut | prot | trit
    // Sets accent/focusglow/status colors to CVD-safe hue sets (Okabe-Ito / Wong 2011).
    // Orthogonal to gose-cb (the CVD correction filter above): these presets swap the accent
    // colors themselves so the UI is inherently distinguishable — useful even without the
    // full-screen filter. Applied via data-cbpalette-type on <html>; CSS vars in a11y.css.
    // Colorblind-safe accent palette preset (gose-cb-palette): off | deut | prot | trit
    // Sets accent/focusglow/status tokens to CVD-safe hue sets (Okabe-Ito / Wong 2011).
    // Applied as inline style so it outranks the per-accent override above — if both are set,
    // the palette wins (it's the a11y-safe set; the accent picker is a "colour decoration" choice).
    // CSS also carries data-cbpalette-type for pages that want to further style chord hints etc.
    var cbp = g('gose-cb-palette', 'off');   // off | deut | prot | trit
    var CB_PALETTES = {
      // Deuteranopia (green-weak): sky-blue accent + orange accent2; vermillion error hue
      deut: { accent: '#56B4E9', accent2: '#E69F00', fg: '0 0 0 2px #56B4E9, 0 0 18px #56B4E955',
              ok: '#0072B2', warn: '#E69F00', err: '#D55E00' },
      // Protanopia (red-weak): blue accent + yellow accent2; orange warn, blue ok
      prot: { accent: '#0072B2', accent2: '#F0E442', fg: '0 0 0 2px #0072B2, 0 0 18px #0072B255',
              ok: '#56B4E9', warn: '#E69F00', err: '#D55E00' },
      // Tritanopia (blue-yellow blind): teal accent + vermillion accent2; pink warn
      trit: { accent: '#009E73', accent2: '#D55E00', fg: '0 0 0 2px #009E73, 0 0 18px #009E7355',
              ok: '#009E73', warn: '#CC79A7', err: '#D55E00' }
    };
    var pal = CB_PALETTES[cbp];
    if (pal) {
      root.style.setProperty('--accent', pal.accent);
      root.style.setProperty('--focusglow', pal.fg);
      root.style.setProperty('--cb-ok', pal.ok);
      root.style.setProperty('--cb-warn', pal.warn);
      root.style.setProperty('--cb-err', pal.err);
    } else {
      root.style.removeProperty('--cb-ok');
      root.style.removeProperty('--cb-warn');
      root.style.removeProperty('--cb-err');
    }
    setAttr('data-cbpalette-type', pal ? cbp : null);
    // Hold-alternatives (gose-hold-alt): off | on
    // Signals to the OS that hold-based chords (Guide-hold carousel, L2+dpad snap, hold-A)
    // should work as tap-sequences instead of holds. UI-only flag: sets data-holdalt on <html>
    // so pages can update their chord-hint labels. The mechanical tap-sequence behaviour for
    // bridge-synthesized chords (Guide, L2 threshold, screenshot) requires a bridge build —
    // that work is scoped separately (see hold-alt build note in docs/27 §1 and Settings row).
    setAttr('data-holdalt', g('gose-hold-alt', 'off') === 'on' ? '1' : null);
    // GOSE Core centerpiece glow (Settings > Personalization) — .core only exists on the
    // desktop; everywhere else this is a no-op. Lives here so the desktop page stays untouched.
    var core = document.querySelector('.core'), glow = g('gose-glow', 'soft');
    if (core) {
      if (glow === 'off') { core.style.filter = 'none'; core.style.opacity = '.35'; }
      else if (glow === 'bright') { core.style.filter = 'drop-shadow(0 0 70px #7b5cffd9)'; core.style.opacity = '.85'; }
      else { core.style.filter = ''; core.style.opacity = ''; }   // soft = the page default
    }
  }
  // ---- canonical prefs sync (Settings overhaul, task 14) ----
  // The server's /ui/prefs is CANONICAL (survives kiosk reloads / wiped localStorage);
  // localStorage is the cache. On every page load: pull, mirror, re-apply on change.
  // Writers must use GOSE.prefs.set() so the server learns the change — a localStorage-only
  // write goes stale server-side and gets overwritten on the next sync.
  function syncFromServer() {
    try {
      fetch('/ui/prefs', { cache: 'no-store' }).then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || !d.ok || !d.prefs) return;
          // a write in the last 10s wins over the (possibly not-yet-landed) server copy
          var fresh = (Date.now() - (parseInt(LS.getItem('gose-prefs-w'), 10) || 0)) < 10000;
          var chg = false;
          for (var k in d.prefs) {
            var v = String(d.prefs[k]);
            if (LS.getItem(k) !== v && !(fresh && LS.getItem(k) != null)) { LS.setItem(k, v); chg = true; }
          }
          if (chg) apply();
        }).catch(function () {});
    } catch (e) {}
  }
  // expose for Settings (live apply) + reduce-motion checks elsewhere
  window.GOSE = window.GOSE || {};
  window.GOSE.a11y = {
    apply: apply,
    reduceMotion: function () { return g('gose-motion', 'off') === 'on'; },
    holdAlt: function () { return g('gose-hold-alt', 'off') === 'on'; }
  };
  window.GOSE.prefs = {
    set: function (map) {            // write-through: localStorage now, server (canonical) async
      for (var k in map) { if (map[k] == null) LS.removeItem(k); else LS.setItem(k, String(map[k])); }
      LS.setItem('gose-prefs-w', String(Date.now()));
      apply();
      try {
        fetch('/ui/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          keepalive: true, body: JSON.stringify({ set: map }) }).catch(function () {});
      } catch (e) {}
    },
    sync: syncFromServer
  };
  if (document.readyState !== 'loading') apply();
  else document.addEventListener('DOMContentLoaded', apply);
  syncFromServer();
})();
