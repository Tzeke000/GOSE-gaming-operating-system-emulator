/* GOSE accessibility applier — one shared script, included on every page right after
   themes/cursor. Reads the gose-* a11y prefs from localStorage and applies them to <html>:
   text scale, high-contrast, bold, reduce-motion/transparency, larger focus, and colorblind
   palette/correction (public-domain libDaltonLens / hail2u feColorMatrix matrices). */
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
    setAttr('data-contrast', g('gose-contrast', 'off') === 'on' ? 'high' : null);
    setAttr('data-bold', g('gose-bold', 'off') === 'on' ? '1' : null);
    setAttr('data-opaque', g('gose-opaque', 'off') === 'on' ? '1' : null);
    setAttr('data-focus', g('gose-focus', 'off') === 'on' ? 'thick' : null);
    setAttr('data-motion', g('gose-motion', 'off') === 'on' ? 'reduce' : null);
    var cb = g('gose-cb', 'off');   // off | friendly | deut | prot | trit
    setAttr('data-cbpalette', cb === 'friendly' ? 'friendly' : null);
    root.style.filter = ({ deut: 'url(#cb-deut)', prot: 'url(#cb-prot)', trit: 'url(#cb-trit)' })[cb] || '';
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
  window.GOSE.a11y = { apply: apply, reduceMotion: function () { return g('gose-motion', 'off') === 'on'; } };
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
