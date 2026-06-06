/* GOSE — Desktop Widget Standard (behaviour).
   ONE contract so every desktop widget is built the same way and reacts the same:
   declare a widget with {id,title,icon,size,pos,load,render,onActivate,badge?} and
   the base gives it header + focusable items + identical keyboard/controller nav +
   the single BLUE focus/hover glow + hover-item naming + sizing/placement for free.
   A widget-wide requirement (e.g. "items need icons") is defined here ONCE and
   therefore applies to ALL widgets. See docs/21-widget-standard.md.            */
(function(){
  "use strict";
  var SPECS=[];                 // registered behavioural specs (home mounts these)
  var MOUNTED={};               // id -> {el, body, spec, data}
  var LAYOUT_V="gw1";           // bump to discard stale saved positions once

  /* ---- catalog: the SINGLE source for the widget list + defaults + grouping.
     The Widgets toggle page renders from this; the desktop mounts whatever is
     enabled. Keep ids in sync with the GW.define() calls. ---- */
  var CATALOG=[
    {id:"hub",        name:"Hub",            desc:"Clock, date & quick shortcuts",            icon:"layout-grid", group:"focal",   def:1},
    {id:"appsgames",  name:"Apps & Games",   desc:"All apps & games, plus your most recent",  icon:"layout-grid", group:"content", def:1},
    {id:"wemulators", name:"Emulators",      desc:"Most-played & recent systems — start one",  icon:"cpu",         group:"content", def:1},
    {id:"wlibrary",   name:"Library",        desc:"Recent & most-played games — launch one",   icon:"layout-grid", group:"content", def:1},
    {id:"wstore",     name:"Store",          desc:"Sample apps, emulators & games to grab",    icon:"download",    group:"content", def:1},
    {id:"aiplayers",  name:"AI Players",     desc:"Wren, Ava, Iris — status & access tier",    icon:"sparkles",    group:"content", def:1},
    {id:"steam",      name:"Steam",          desc:"Steam status & library when signed in",     icon:"gamepad-2",   group:"content", def:0},
    {id:"wterminal",  name:"Terminal",       desc:"Quick-launch the terminal",                 icon:"terminal",    group:"content", def:1},
    {id:"controllers",name:"Controllers",    desc:"Connected gamepads (count badge when live)",icon:"gamepad-2",   group:"status",  def:1},
    {id:"notifs",     name:"Notifications",  desc:"Recent notifications (unread-count badge)",  icon:"bell",        group:"status",  def:1},
    {id:"system",     name:"System",         desc:"Live laptop CPU / GPU / RAM / temp monitor",icon:"cpu",         group:"status",  def:1}
  ];
  function catMeta(id){for(var i=0;i<CATALOG.length;i++)if(CATALOG[i].id===id)return CATALOG[i];return null;}
  function defOn(id){var m=catMeta(id);return m?!!m.def:false;}

  /* ---- helpers ---- */
  function esc(s){return (s==null?'':(''+s)).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function fmtPlay(s){s=s||0;var h=Math.floor(s/3600),m=Math.floor(s%3600/60);
    return h?(h+'h'+(m?' '+m+'m':'')):(m?m+'m':'<1m');}
  function paintIcons(el){if(!el)return;el.querySelectorAll('[data-i]').forEach(function(e){
    e.style.setProperty('--u','url(assets/icons/'+e.dataset.i+'.svg)');});}
  function enabled(){var en={};try{en=JSON.parse(localStorage.getItem('gose-wenabled')||'{}');}catch(e){}
    return function(id){return en[id]!==undefined?!!en[id]:defOn(id);};}

  /* ---- build a focusable item element from an item spec ---- */
  function buildItem(it,wIcon){
    var el=document.createElement('div'); el.className='gw-item';
    el.dataset.label=it.label||it.title||'';
    // icon — every item gets one, with a fallback when missing (widget icon, else "square")
    var ico=document.createElement('span'); ico.className='gw-ic';
    var fall=it.icon||wIcon||'square';
    ico.innerHTML='<span class="ic" data-i="'+esc(fall)+'"></span>'+
      (it.img?'<img src="'+esc(it.img)+'" onerror="this.remove()">':'');
    el.appendChild(ico);
    var tx=document.createElement('div'); tx.className='gw-tx';
    tx.innerHTML='<div class="gw-nm">'+esc(it.label)+'</div>'+
      (it.sub?'<div class="gw-sub">'+esc(it.sub)+'</div>':'');
    el.appendChild(tx);
    if(it.chip){var c=document.createElement('span');
      c.className='gw-chip'+(it.chip.onActivate?' act':'');
      if(it.chip.color)c.style.color=it.chip.color;
      c.textContent=it.chip.text;
      if(it.chip.onActivate)c.addEventListener('click',function(e){e.stopPropagation();e.preventDefault();it.chip.onActivate();});
      el.appendChild(c);}
    wireAction(el,it);
    return el;
  }
  function buildAct(it){var el=document.createElement('div'); el.className='gw-act';
    el.dataset.label=it.label||'';
    el.innerHTML=(it.icon?'<span class="ic" data-i="'+esc(it.icon)+'"></span>':'')+esc(it.label);
    wireAction(el,it); return el;}
  function buildPin(it,wIcon){var el=document.createElement('div'); el.className='gw-pin';
    el.dataset.label=it.label||'';
    el.innerHTML='<span class="ic" data-i="'+esc(it.icon||wIcon||'square')+'"></span>'+esc(it.label);
    wireAction(el,it); return el;}
  function wireAction(el,it){
    if(it.go)el.dataset.go=it.go;
    if(it.launch)el.dataset.launch=(typeof it.launch==='string')?it.launch:JSON.stringify(it.launch);
    if(it.cmd)el.dataset.cmd=it.cmd;
    if(it.onActivate)el.__act=it.onActivate;
  }

  /* ---- render a widget's body from render(data) ---- */
  function renderBody(rec){
    var spec=rec.spec, body=rec.body, data=rec.data;
    var r=null; try{ r=spec.render?spec.render(data):null; }catch(e){ r={error:e}; }
    body.innerHTML='';
    if(r&&r.error){ body.innerHTML='<div class="gw-empty">Couldn’t load.</div>'; finishBody(rec,null); return; }
    if(Array.isArray(r))r={items:r};
    r=r||{};
    var any=false;
    // custom (non-focusable) body content first — for clock, stat rows, etc.
    if(r.body){var holder=document.createElement('div');
      if(typeof r.body==='function')r.body(holder); else holder.innerHTML=r.body;
      body.appendChild(holder); any=true;}
    // pinned shortcut chips
    if(r.pins&&r.pins.length){var pr=document.createElement('div'); pr.className='gw-pins';
      r.pins.forEach(function(p){pr.appendChild(buildPin(p,spec.icon));}); body.appendChild(pr); any=true;}
    // sections of focusable items
    if(r.sections)r.sections.forEach(function(sec){ if(!sec.items||!sec.items.length)return;
      if(sec.label){var h=document.createElement('div'); h.className='gw-sec'; h.textContent=sec.label; body.appendChild(h);}
      sec.items.forEach(function(it){body.appendChild(buildItem(it,spec.icon));}); any=true;});
    // flat items
    if(r.items&&r.items.length){r.items.forEach(function(it){body.appendChild(buildItem(it,spec.icon));}); any=true;}
    // empty state
    if(!any){var e=document.createElement('div'); e.className='gw-empty';
      e.textContent=r.empty||spec.empty||'Nothing here yet.'; body.appendChild(e);}
    // footer action (open X)
    if(r.footer)body.appendChild(buildAct(r.footer));
    finishBody(rec,r);
  }
  function finishBody(rec,r){
    // subtle state badge in the header — count/dot, NEVER a glow
    var b=rec.badgeEl, bd=(r&&r.badge!==undefined)?r.badge:(rec.spec.badge?safe(rec.spec.badge,rec.data):null);
    if(b){ if(bd&&(bd.text||bd.dot)){ b.className='gw-badge on'+(bd.muted?' muted':'');
        b.innerHTML=(bd.dot?'<span class="gw-dot"></span>':'')+(bd.text?esc(bd.text):''); }
      else b.className='gw-badge'; }
    paintIcons(rec.el);
    if(GW.nav)GW.nav.rebuild();
  }
  function safe(fn,d){try{return fn(d);}catch(e){return null;}}

  /* ---- load + (re)render loop ---- */
  function runLoad(rec){
    var spec=rec.spec;
    function go(){
      if(rec.el.hidden)return;
      if(!spec.load){renderBody(rec);return;}
      Promise.resolve().then(spec.load).then(function(d){rec.data=d;renderBody(rec);})
        .catch(function(){rec.data=null;renderBody(rec);});
    }
    go();
    if(spec.poll)rec.timer=setInterval(go,spec.poll);
  }

  /* ================= NAVIGATION — identical for every widget ================= */
  /* zones: [Menu(side)] + visible widgets (reading order) + [Dock]. Within a
     widget, items are .gw-item/.gw-act/.gw-pin; a widget with none becomes a
     single whole-widget target. Arrows/L1-R1 move, A/Enter activates, B/Esc per
     page. The SAME blue glow marks focus everywhere; the focused item's name is
     shown top-centre.                                                          */
  var nav={};
  (function(){
    var zones=[], z=0, ii=0, zlabel, toast, lblT;
    function curItem(){return zones[z]&&zones[z].items[ii];}
    function say(m){if(!toast)return;toast.textContent=m;toast.classList.add('on');
      clearTimeout(say._t);say._t=setTimeout(function(){toast.classList.remove('on');},1500);}
    function itemLabel(it){if(!it)return'';
      if(it.dataset&&it.dataset.label)return it.dataset.label;
      var t=(it.textContent||'').replace(/\s+/g,' ').trim(); if(!t)t=it.title||''; return t.slice(0,46);}
    function widgetOf(it){return it&&it.closest?it.closest('.gw'):null;}
    function wname(it){var w=widgetOf(it); if(w){var m=catMeta(w.dataset.wid); if(m)return m.name;
        var t=w.querySelector('.gw-hd-t'); if(t)return t.textContent;} return (zones[z]&&zones[z].name)||'';}
    function clearFocus(){document.querySelectorAll('.focus,.wfocus').forEach(function(e){e.classList.remove('focus','wfocus');});}
    function highlight(){clearFocus(); var it=curItem(); if(!it)return;
      var whole=it.classList&&it.classList.contains('gw');
      it.classList.add(whole?'wfocus':'focus'); it.scrollIntoView({block:'nearest'});
      if(whole)zlabel.innerHTML='◀&nbsp;&nbsp;'+esc(wname(it))+'&nbsp;&nbsp;▶';
      else zlabel.innerHTML='<span class="gw-ctx">'+esc(wname(it))+'</span>&nbsp;·&nbsp;<b>'+esc(itemLabel(it))+'</b>';
      zlabel.classList.add('on'); clearTimeout(lblT); lblT=setTimeout(function(){zlabel.classList.remove('on');},1400);}
    function activate(){var it=curItem(); if(!it)return;
      if(it.__act){it.__act();return;}
      if(it.dataset.go){location.href=it.dataset.go;return;}
      if(it.dataset.launch){try{var b=JSON.parse(it.dataset.launch);
        fetch('/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:it.dataset.launch});
        say('Launching '+(b.game||b.app||b.name||b.system||'…'));}catch(e){say('Launch failed');}return;}
      if(it.dataset.cmd){fetch('/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd:it.dataset.cmd})});say('Opening…');return;}
      if(it.click)it.click();}
    function visibleWidgets(){
      var ws=[].slice.call(document.querySelectorAll('.gw')).filter(function(w){return !w.hidden&&w.style.display!=='none';});
      // reading order: rows top→bottom, then left→right (so ←/→ feels natural)
      ws.sort(function(a,a2){var ra=a.getBoundingClientRect(),rb=a2.getBoundingClientRect();
        var row=Math.round(ra.top/90)-Math.round(rb.top/90); return row||(ra.left-rb.left);});
      return ws;}
    function build(){
      var cur=curItem(), Z=[];
      var side=[].slice.call(document.querySelectorAll('#side .nav')); if(side.length)Z.push({name:'Menu',items:side});
      visibleWidgets().forEach(function(w){
        var its=[].slice.call(w.querySelectorAll('.gw-item,.gw-act,.gw-pin'));
        if(!its.length)its=[w];                       // whole-widget single target
        Z.push({name:(catMeta(w.dataset.wid)||{}).name||'Widget',items:its});});
      var dock=[].slice.call(document.querySelectorAll('#dock .d')); if(dock.length)Z.push({name:'Dock',items:dock});
      zones=Z;
      if(cur){for(var a=0;a<zones.length;a++){var b=zones[a].items.indexOf(cur);if(b>=0){z=a;ii=b;break;}}}
      if(z>=zones.length)z=Math.max(0,zones.length-1);
      if(zones[z]&&ii>=zones[z].items.length)ii=0;
      zones.forEach(function(zo,za){zo.items.forEach(function(el,ib){el.style.cursor='pointer';
        el.onmouseenter=function(){z=za;ii=ib;highlight();}; el.onclick=function(){z=za;ii=ib;activate();};});});
      var it=curItem(); clearFocus(); if(it)it.classList.add(it.classList.contains('gw')?'wfocus':'focus');
    }
    function move(dz,di){if(!zones.length)return;
      if(dz){z=(z+dz+zones.length)%zones.length;ii=0;}
      if(di){var n=zones[z].items.length;ii=(ii+di+n)%n;} highlight();}
    nav.init=function(){
      zlabel=document.createElement('div'); zlabel.className='gw-name'; document.body.appendChild(zlabel);
      var st=document.createElement('style');
      st.textContent='.nav.focus,.dock .d.focus{box-shadow:0 0 0 2px var(--accent),0 0 18px #5cd0ff99;border-radius:11px}';
      document.head.appendChild(st);
      toast=document.createElement('div'); toast.className='gw-name'; toast.style.top='auto'; toast.style.bottom='80px'; toast.style.borderColor='var(--accent)';
      document.body.appendChild(toast);
      build();
      addEventListener('keydown',function(e){var k=e.key;
        if(/INPUT|TEXTAREA/.test((document.activeElement||{}).tagName||''))return;
        if(k==='ArrowLeft')move(-1,0); else if(k==='ArrowRight'||k==='Tab')move(1,0);
        else if(k==='ArrowUp')move(0,-1); else if(k==='ArrowDown')move(0,1);
        else if(k==='Enter'||k===' ')activate(); else return; e.preventDefault();});
      var prev={},primed=false;(function pad(){var gps=navigator.getGamepads&&[].slice.call(navigator.getGamepads()).filter(function(x){return x;});
        var gp=gps&&gps[0];
        if(gp){var tap=function(i){return primed&&gp.buttons[i]&&gp.buttons[i].pressed&&!prev[i];};
          if(tap(14)||tap(4))move(-1,0); if(tap(15)||tap(5))move(1,0);   // ←→ / L1 R1 between widgets
          if(tap(12))move(0,-1); if(tap(13))move(0,1);                    // ↑↓ within widget
          if(tap(0))activate(); if(tap(16)||tap(8))location.href='gose-apps.html';
          gp.buttons.forEach(function(b,i){prev[i]=b.pressed;}); primed=true;}
        requestAnimationFrame(pad);})();
      highlight();
    };
    nav.rebuild=function(){build();};
  })();
  /* ========================================================================= */

  /* ---- placement + drag (positions persist; layout version resets defaults) ---- */
  function place(rec){
    var spec=rec.spec, w=rec.el, p=spec.pos||{};
    var saved=rec._pos;
    if(saved){w.style.left=saved.x+'px';w.style.top=saved.y+'px';w.style.right='auto';w.style.bottom='auto';}
    else{ if(p.x!=null)w.style.left=p.x+'px'; if(p.y!=null)w.style.top=p.y+'px';
          if(p.right!=null)w.style.right=p.right+'px'; if(p.bottom!=null)w.style.bottom=p.bottom+'px';
          if(p.left!=null)w.style.left=p.left+'px'; if(p.top!=null)w.style.top=p.top+'px'; }
    if(spec.size&&spec.size.w)w.style.width=spec.size.w+'px';
    if(spec.size&&spec.size.h)w.style.minHeight=spec.size.h+'px';
  }
  function loadPositions(){
    if(localStorage.getItem('gose-wlayout-v')!==LAYOUT_V){
      localStorage.removeItem('gose-wpos'); localStorage.setItem('gose-wlayout-v',LAYOUT_V);}
    try{return JSON.parse(localStorage.getItem('gose-wpos')||'{}');}catch(e){return {};}}
  function savePositions(){var o={};Object.keys(MOUNTED).forEach(function(id){var w=MOUNTED[id].el;
    if(w.hidden)return; var r=w.getBoundingClientRect();o[id]={x:Math.round(r.left),y:Math.round(r.top)};});
    localStorage.setItem('gose-wpos',JSON.stringify(o));}
  function makeDraggable(rec){var w=rec.el,h=rec.el.querySelector('.gw-hd')||rec.el,dr=null;
    h.addEventListener('mousedown',function(e){var r=w.getBoundingClientRect();
      dr={dx:e.clientX-r.left,dy:e.clientY-r.top,m:false};
      w.style.left=r.left+'px';w.style.top=r.top+'px';w.style.right='auto';w.style.bottom='auto';e.preventDefault();});
    addEventListener('mousemove',function(e){if(!dr)return;dr.m=true;GW.dragged=true;
      w.style.left=Math.max(0,Math.min(innerWidth-50,e.clientX-dr.dx))+'px';
      w.style.top=Math.max(0,Math.min(innerHeight-30,e.clientY-dr.dy))+'px';});
    addEventListener('mouseup',function(){if(dr){if(dr.m)savePositions();dr=null;setTimeout(function(){GW.dragged=false;},60);}});}

  /* ---- public API ---- */
  var GW={
    catalog:CATALOG, esc:esc, fmtPlay:fmtPlay, paintIcons:paintIcons, dragged:false,
    isEnabled:function(id){return enabled()(id);},
    define:function(spec){SPECS.push(spec);return GW;},
    get:function(id){return MOUNTED[id];},
    mount:function(opts){
      opts=opts||{};
      var root=opts.root?document.querySelector(opts.root):document.body;
      var isOn=enabled();
      var forced=((location.hash.match(/enable=([^&]+)/)||[])[1]||'').split(',').filter(Boolean);
      var pos=loadPositions();
      SPECS.forEach(function(spec){
        var el=document.createElement('section'); el.className='gw'; el.dataset.wid=spec.id;
        var m=catMeta(spec.id)||{};
        el.innerHTML='<header class="gw-hd"><span class="gw-hd-ic ic" data-i="'+esc(spec.icon||m.icon||'square')+'"></span>'+
          '<span class="gw-hd-t">'+esc((spec.title||m.name||'').toUpperCase())+'</span>'+
          '<span class="gw-badge"></span></header>'+
          '<div class="gw-body"><div class="gw-load">Loading</div></div>';
        var on=forced.indexOf(spec.id)>=0?true:isOn(spec.id);
        el.hidden=!on;
        root.appendChild(el);
        var rec={spec:spec,el:el,body:el.querySelector('.gw-body'),badgeEl:el.querySelector('.gw-badge'),
                 data:null,_pos:pos[spec.id]};
        // whole-widget action (single-action widgets like Terminal/System)
        if(spec.onActivate)el.__act=spec.onActivate;
        MOUNTED[spec.id]=rec;
        place(rec); makeDraggable(rec); paintIcons(el);
        if(on)runLoad(rec);
      });
      nav.init();
    },
    nav:nav,
    // let a widget force an out-of-band refresh (e.g. after an action)
    refresh:function(id){var r=MOUNTED[id]; if(r){ if(!r.spec.load){renderBody(r);return;}
      Promise.resolve().then(r.spec.load).then(function(d){r.data=d;renderBody(r);}).catch(function(){});}}
  };
  window.GW=GW;
})();
