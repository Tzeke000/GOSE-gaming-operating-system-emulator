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
    root.style.setProperty('--ui-scale', g('gose-ui-scale', '1'));
    setAttr('data-contrast', g('gose-contrast', 'off') === 'on' ? 'high' : null);
    setAttr('data-bold', g('gose-bold', 'off') === 'on' ? '1' : null);
    setAttr('data-opaque', g('gose-opaque', 'off') === 'on' ? '1' : null);
    setAttr('data-focus', g('gose-focus', 'off') === 'on' ? 'thick' : null);
    setAttr('data-motion', g('gose-motion', 'off') === 'on' ? 'reduce' : null);
    var cb = g('gose-cb', 'off');   // off | friendly | deut | prot | trit
    setAttr('data-cbpalette', cb === 'friendly' ? 'friendly' : null);
    root.style.filter = ({ deut: 'url(#cb-deut)', prot: 'url(#cb-prot)', trit: 'url(#cb-trit)' })[cb] || '';
  }
  // expose for Settings (live apply) + reduce-motion checks elsewhere
  window.GOSE = window.GOSE || {};
  window.GOSE.a11y = { apply: apply, reduceMotion: function () { return g('gose-motion', 'off') === 'on'; } };
  if (document.readyState !== 'loading') apply();
  else document.addEventListener('DOMContentLoaded', apply);
})();
