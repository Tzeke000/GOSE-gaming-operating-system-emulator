/* GOSE icon resolver.
   Most icons are monochrome SVG masks tinted with currentColor
   (assets/icons/<name>.svg). A small set of APP-IDENTITY icons are Zeke's
   full-color hand-made PNGs (assets/icons/brand/<file>.png) — a coloured PNG
   can't be a mask (a mask would flatten it to the accent colour), so those render
   as a contained background-image instead. Brand tokens are namespaced so they
   never collide with the lucide line-icon names (only `settings`/`terminal` would,
   hence the -app suffix). Focus glow lives on the item element, so swapping the
   icon render path leaves docs/21 styling intact. */
(function(){
  var BRAND = {
    ai:'ai', apps:'apps', emulators:'emulators', files:'files', games:'games',
    library:'library', notifications:'notifications', gallery:'gallery', store:'store',
    'settings-app':'settings', 'terminal-app':'terminal'
  };
  function apply(e){
    var n = e.dataset.i; if(n==null) return;
    if(BRAND[n]){
      e.style.webkitMaskImage='none'; e.style.maskImage='none';
      e.style.background='url(assets/icons/brand/'+BRAND[n]+'.png) center/contain no-repeat';
      e.classList.add('bic');
    } else {
      if(e.classList.contains('bic')){ e.classList.remove('bic'); e.style.background='';
        e.style.removeProperty('-webkit-mask-image'); e.style.removeProperty('mask-image'); }
      e.style.setProperty('--u','url(assets/icons/'+n+'.svg)');
    }
  }
  function paint(root){ (root||document).querySelectorAll('[data-i]').forEach(apply); }
  window.GICON = { apply:apply, paint:paint, isBrand:function(n){return !!BRAND[n];}, BRAND:BRAND };
})();
