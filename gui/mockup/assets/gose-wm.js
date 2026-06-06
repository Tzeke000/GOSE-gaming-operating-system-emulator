/* GOSE — web-layer window manager (docs/23, wave 1 / chunk A).
   A THIN wrapper that binds WinBox frames (assets/vendor/winbox, Apache-2.0) to the
   GW widget base (docs/21). Additive: loads alongside widget.js on the desktop and
   touches nothing inside it.

   What it does:
   * WEB WINDOWS — opens an existing gose-*.html page as an iframe inside a WinBox
     frame (create/close/focus/minimize/maximize/move/resize/snap). The blue focus
     glow (docs/21 §1.4 — focus-only, same blue everywhere) marks the focused frame.
   * REGISTRY — every open window is mirrored to the server registry: the full list
     is POSTed to /windows/sync on every change + a heartbeat, so GET /windows sees
     the web windows; the sync response carries queued /wm/<verb> commands which are
     executed here (the server→WebView transport — the kiosk polls, nothing can push
     into it). In-page, iframes may also postMessage({gose:"wm", verb, ...}) up.
   * WIDGET→WINDOW (docs/23 §5) — "maximizing" a widget re-parents its LIVE body
     node into a WinBox frame via mount() (NO reload — same DOM node, polls keep
     running), leaving a ghost slot on the home canvas; closing the window returns
     the node to the widget. Trigger: the ⤢ header button, the M key on the focused
     widget, or POST /wm/winify {id:"<widgetId>"}.
   * LAUNCHER INTERCEPT — on the desktop, activating a windowable page (Files,
     Store, Terminal, …) opens it as a window instead of a full-page navigation.
     Non-windowable targets (Lock, Home, …) keep navigating. If WinBox is missing,
     everything falls back to plain navigation.
   * DOCK — running-window tiles appended into the existing #dock bar (already a GW
     nav zone, so arrows reach them and A/Enter focuses; docs/23 §4.4).
   Out of scope here (chunk B): carousel, snap chooser UI, act-out/re-summon
   descriptors, the pad-nav WM modal layer.                                       */
(function(){
  "use strict";
  if(!window.WinBox){ return; }                 // no frame engine -> desktop behaves as before

  /* ---- the windowable app set: url -> {title, icon}. Anything NOT here keeps its
     normal full-page navigation (lock/home/boot/oobe are deliberately absent). ---- */
  var APPS={
    "gose-files.html":      {title:"Files",        icon:"folder"},
    "gose-store.html":      {title:"Store",        icon:"download"},
    "gose-term.html":       {title:"Terminal",     icon:"terminal"},
    "gose-taskman.html":    {title:"Task Manager", icon:"cpu"},
    "gose-gallery.html":    {title:"Gallery",      icon:"image"},
    "gose-library.html":    {title:"Library",      icon:"layout-grid"},
    "gose-settings.html":   {title:"Settings",     icon:"settings"},
    "gose-ai.html":         {title:"AI Players",   icon:"sparkles"},
    "gose-apps.html":       {title:"Apps",         icon:"layout-grid"},
    "gose-widgets.html":    {title:"Widgets",      icon:"palette"},
    "gose-licenses.html":   {title:"Licenses",     icon:"file-text"},
    "gose-peripherals.html":{title:"Peripherals",  icon:"usb"},
    "gose-storage.html":    {title:"Storage",      icon:"hard-drive"},
    "gose-bluetooth.html":  {title:"Bluetooth",    icon:"bluetooth"},
    "gose-wifi.html":       {title:"Wi-Fi",        icon:"wifi"},
    "gose-remap.html":      {title:"Remap",        icon:"gamepad-2"},
    "gose-splice.html":     {title:"Splice",       icon:"scissors"}
  };
  function appOf(url){ return APPS[(url||"").split("?")[0].split("#")[0]] || null; }

  /* ---- the blue focus glow (docs/21: SAME blue everywhere, focus-only) + frame
     theming, injected once. The glow values mirror widget.js's nav style. ---- */
  var css=document.createElement("style");
  css.textContent=
    ".winbox{background:#141831;border-radius:12px;box-shadow:0 18px 44px #000a}"+
    ".winbox.focus{box-shadow:0 0 0 2px var(--accent,#5cd0ff),0 0 18px #5cd0ff99,0 18px 44px #000a}"+
    ".winbox .wb-body{background:#0d1020;border-radius:0 0 12px 12px}"+
    ".winbox .wb-body iframe{background:#0d1020}"+
    ".winbox .wb-title{font:600 12.5px Inter,system-ui,sans-serif;letter-spacing:.04em}"+
    ".gw-ghost{opacity:.45;border:1px dashed #ffffff2e;border-radius:10px;padding:14px 12px;"+
      "font-size:12px;color:#aab1d6;text-align:center;cursor:pointer}"+
    ".gw-winify{margin-left:auto;opacity:.4;cursor:pointer;font-size:13px;line-height:1;padding:2px 4px}"+
    ".gw-winify:hover{opacity:1}"+
    ".gw-hd .gw-badge{margin-left:6px}"+
    ".dock .wm-sep{width:1px;height:30px;background:#ffffff22;margin:0 4px;align-self:center}"+
    ".dock .d.wm-d{position:relative}"+
    ".dock .d.wm-d .wm-dot{position:absolute;bottom:3px;left:50%;transform:translateX(-50%);"+
      "width:5px;height:5px;border-radius:50%;background:var(--accent,#5cd0ff)}"+
    ".dock .d.wm-d.wm-min{opacity:.5}";
  document.head.appendChild(css);

  /* =============================== state =============================== */
  var WINS={};                 // id -> {wb, kind:"page"|"widget", title, icon, url, widget, state, token}
  var ORDER=[];                // open order (next/prev cycling)
  var seq=0;
  var syncT=null;
  // one id per page LOAD — lets the server (and a debugging Wren) tell which shell
  // instance the registry cache is coming from, and spot a second/stale consumer.
  var INST=(navigator.userAgent.indexOf("Chrome")>=0?"chrome-":"webkit-")+
           Math.random().toString(36).slice(2,8)+"-"+Date.now().toString(36);

  function focusedId(){
    for(var id in WINS){ if(WINS[id].wb && WINS[id].wb.focused) return id; }
    return null;
  }
  function list(){
    return Object.keys(WINS).map(function(id){
      var w=WINS[id], wb=w.wb, mem;
      if(w.widget){
        // live no-reload PROOF, re-read from the DOM each sync: the widget's body is
        // the SAME node (it still carries the token stamped at winify time) and it is
        // physically mounted inside the WinBox frame right now.
        var rec=window.GW&&GW.get&&GW.get(w.widget), b=rec&&rec.body;
        mem={ widget:w.widget, token:w.token,
              token_live:(b&&b.dataset.wmToken)||null,
              mounted_in:(b&&b.closest&&b.closest(".winbox"))?"winbox":"canvas" };
      } else mem={url:w.url};
      return { id:id, kind:"web", title:w.title, icon:w.icon,
               url:w.url||null, widget:w.widget||null,
               geom:wb?{x:wb.x,y:wb.y,w:wb.width,h:wb.height}:{},
               state:w.state, group:null, focused:!!(wb&&wb.focused), mem:mem };
    });
  }

  /* ---- registry sync: full list on every change + 4s heartbeat; the response
     piggybacks queued /wm commands, which are executed right here. ---- */
  function sync(){
    clearTimeout(syncT); syncT=setTimeout(sync,4000);
    try{
      fetch("/windows/sync",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({windows:list(),inst:INST})})
        .then(function(r){return r.json();})
        .then(function(d){ (d&&d.commands||[]).forEach(run); })
        .catch(function(){});
    }catch(e){}
  }
  function syncSoon(){ clearTimeout(syncT); syncT=setTimeout(sync,120); }

  /* ---- execute one /wm command (server queue or postMessage) ---- */
  function run(c){
    if(!c||!c.verb)return;
    var w=c.id?WINS[c.id]:null, wb=w&&w.wb;
    switch(c.verb){
      case "open":    if(c.url)openApp(c.url,c.title,c.icon); break;
      case "winify":  if(c.id)winify(c.id); break;
      case "focus":   if(wb){ if(w.state==="min")wb.minimize(false); wb.focus(); } break;
      case "close":
      case "free":    if(wb)wb.close(); break;        // chunk-A tier: free == close (JS GC);
                                                      // descriptor re-summon lands in chunk B
      case "minimize":if(wb)wb.minimize(true); break;
      case "restore": if(wb){ wb.minimize(false); wb.maximize(false); wb.focus(); } break;
      case "maximize":if(wb)wb.maximize(true); break;
      case "move":    if(wb)wb.move(num(c.x,wb.x),num(c.y,wb.y)); break;
      case "resize":  if(wb)wb.resize(num(c.w,wb.width),num(c.h,wb.height)); break;
      case "snap":    if(wb)snap(wb,c.zone); break;
      case "next":    cycle(1); break;
      case "prev":    cycle(-1); break;
    }
    syncSoon();
  }
  function num(v,d){ v=parseInt(v,10); return isNaN(v)?d:v; }
  function cycle(dir){
    var ids=ORDER.filter(function(id){return WINS[id];});
    if(!ids.length)return;
    var i=ids.indexOf(focusedId());
    var id=ids[(i+dir+ids.length)%ids.length];
    run({verb:"focus",id:id});
  }
  function snap(wb,zone){
    var W=innerWidth,H=innerHeight,Z={
      left:[0,0,W/2,H], right:[W/2,0,W/2,H], top:[0,0,W,H/2], bottom:[0,H/2,W,H/2],
      tl:[0,0,W/2,H/2], tr:[W/2,0,W/2,H/2], bl:[0,H/2,W/2,H/2], br:[W/2,H/2,W/2,H/2],
      "col-l":[0,0,W/3,H], "col-c":[W/3,0,W/3,H], "col-r":[2*W/3,0,W/3,H],
      main:[0,0,2*W/3,H], side:[2*W/3,0,W/3,H], full:[0,0,W,H] };
    var r=Z[zone]; if(!r)return;
    wb.minimize(false); wb.maximize(false);
    wb.move(Math.round(r[0]),Math.round(r[1])); wb.resize(Math.round(r[2]),Math.round(r[3]));
    wb.focus();
  }

  /* ---- shared WinBox wiring (state mirror + glow comes free via .winbox.focus) ---- */
  function wire(id,wb,onclose){
    var w=WINS[id];
    wb.onfocus=function(){ w.state=(w.state==="max")?"max":"normal"; syncSoon(); };
    wb.onblur=function(){ syncSoon(); };
    wb.onminimize=function(){ w.state="min"; syncSoon(); };
    wb.onmaximize=function(){ w.state="max"; syncSoon(); };
    wb.onrestore=function(){ w.state="normal"; syncSoon(); };
    wb.onmove=function(){ syncSoon(); };
    wb.onresize=function(){ syncSoon(); };
    wb.onclose=function(){
      if(onclose)onclose();
      delete WINS[id]; ORDER=ORDER.filter(function(x){return x!==id;});
      syncSoon(); dockRender();
      return false;                              // false = allow the close
    };
  }

  /* =================== web windows: a page in a frame =================== */
  function openApp(url,title,icon){
    var meta=appOf(url)||{};
    title=title||meta.title||url.replace(/^gose-|\.html.*$/g,"");
    icon=icon||meta.icon||"square";
    var id="win-"+url.replace(/^gose-|\.html.*$/g,"").replace(/[^a-z0-9]+/gi,"")+"-"+(++seq);
    var W=innerWidth,H=innerHeight;
    var n=ORDER.length;
    var wb=new WinBox({
      title:title, url:url,
      x:Math.min(120+n*48,W-940), y:Math.min(90+n*40,H-700),
      width:Math.min(920,W-160), height:Math.min(640,H-200)
    });
    WINS[id]={wb:wb,kind:"page",title:title,icon:icon,url:url,state:"normal"};
    ORDER.push(id);
    wire(id,wb,null);
    wb.focus(); syncSoon(); dockRender();
    return id;
  }

  /* ============ widget -> window (docs/23 §5, NO-reload re-parent) ============ */
  function winify(widgetId){
    // already windowed? just focus it
    for(var id0 in WINS){ if(WINS[id0].widget===widgetId){ run({verb:"focus",id:id0}); return id0; } }
    var rec=window.GW&&GW.get&&GW.get(widgetId);
    if(!rec||!rec.body||rec.el.hidden)return null;
    var meta=(GW.catalog||[]).filter(function(c){return c.id===widgetId;})[0]||{};
    var title=meta.name||widgetId, icon=meta.icon||"square";
    var body=rec.body;
    // stamp the node so "same node, no reload" is provable after the move
    var token=body.dataset.wmToken||("t"+Math.random().toString(36).slice(2,10));
    body.dataset.wmToken=token;
    // ghost slot stays on the canvas
    var ghost=document.createElement("div");
    ghost.className="gw-ghost"; ghost.textContent="⊡ "+title+" — open as window";
    ghost.onclick=function(){ var i=findWidgetWin(widgetId); if(i)run({verb:"focus",id:i}); };
    rec.el.appendChild(ghost);
    var id="win-widget-"+widgetId;
    var W=innerWidth,H=innerHeight;
    var wb=new WinBox({
      title:title.toUpperCase(),
      mount:body,                                 // MOVES the live node — no reload
      x:Math.round(W/2-460), y:Math.round(H/2-330),
      width:Math.min(920,W-160), height:Math.min(660,H-180)
    });
    WINS[id]={wb:wb,kind:"widget",title:title,icon:icon,widget:widgetId,state:"normal",token:token};
    ORDER.push(id);
    wire(id,wb,function(){                        // close = return the node to the widget
      ghost.remove();
      rec.el.appendChild(body);
      if(GW.reflow)GW.reflow(); if(GW.nav)GW.nav.rebuild();
    });
    if(GW.reflow)GW.reflow(); if(GW.nav)GW.nav.rebuild();
    wb.focus(); syncSoon(); dockRender();
    return id;
  }
  function findWidgetWin(widgetId){
    for(var id in WINS){ if(WINS[id].widget===widgetId)return id; } return null;
  }

  /* clicks inside a windowed widget body: the GW nav's index-closures go stale once
     the node leaves its zone, so handle item actions here (capture) instead. */
  document.addEventListener("click",function(e){
    var wbod=e.target.closest&&e.target.closest(".winbox .wb-body"); if(!wbod)return;
    var it=e.target.closest(".gw-item,.gw-act,.gw-pin"); if(!it)return;
    e.stopPropagation(); e.preventDefault();
    if(it.__act)return it.__act();
    if(it.dataset.go)return openOrGo(it.dataset.go);
    if(it.dataset.launch)return void fetch("/launch",{method:"POST",
      headers:{"Content-Type":"application/json"},body:it.dataset.launch});
    if(it.dataset.cmd)return void fetch("/launch",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({cmd:it.dataset.cmd})});
  },true);

  /* ================= launcher intercept (desktop only) ================= */
  function openOrGo(url){ if(appOf(url))openApp(url); else location.href=url; }
  // keyboard/pad path: GW's activate() prefers __act, so stamp windowable [data-go] targets
  function applyIntercepts(){
    var els=document.querySelectorAll("[data-go]");
    for(var i=0;i<els.length;i++)(function(el){
      if(el.__wmHooked)return;
      var url=el.dataset.go;
      if(!appOf(url))return;
      el.__wmHooked=true;
      el.__act=function(){ openApp(url); };
    })(els[i]);
  }
  // mouse path: the page wires direct location.href click handlers — beat them in capture
  document.addEventListener("click",function(e){
    if(e.target.closest&&e.target.closest(".winbox"))return;   // window content handled above
    var el=e.target.closest&&e.target.closest("[data-go]"); if(!el)return;
    if(!appOf(el.dataset.go))return;
    e.stopPropagation(); e.preventDefault();
    openApp(el.dataset.go);
  },true);
  // re-stamp after every nav rebuild (widgets re-render on poll)
  if(window.GW&&GW.nav&&GW.nav.rebuild){
    var _rb=GW.nav.rebuild;
    GW.nav.rebuild=function(){ applyIntercepts(); winifyButtons(); _rb(); };
  }

  /* ---- ⤢ header button on every widget + M key on the focused widget ---- */
  function winifyButtons(){
    var ws=document.querySelectorAll(".gw");
    for(var i=0;i<ws.length;i++)(function(w){
      var hd=w.querySelector(".gw-hd");
      if(!hd||hd.querySelector(".gw-winify"))return;
      var b=document.createElement("span");
      b.className="gw-winify"; b.textContent="⤢"; b.title="Open as window (M)";
      b.addEventListener("mousedown",function(e){e.stopPropagation();});  // don't start a drag
      b.addEventListener("click",function(e){e.stopPropagation();e.preventDefault();winify(w.dataset.wid);});
      hd.appendChild(b);
    })(ws[i]);
  }
  addEventListener("keydown",function(e){
    if(/INPUT|TEXTAREA/.test((document.activeElement||{}).tagName||""))return;
    if(e.key!=="m"&&e.key!=="M")return;
    var f=document.querySelector(".focus,.wfocus");
    var w=f&&f.closest&&f.closest(".gw");
    if(w&&w.dataset.wid){ winify(w.dataset.wid); e.preventDefault(); }
  });

  /* ============ dock: running-window tiles in the existing #dock zone ============ */
  var dockSep=null;
  function dockRender(){
    var dock=document.getElementById("dock"); if(!dock)return;
    // clear previous window tiles
    var old=dock.querySelectorAll(".wm-d,.wm-sep");
    for(var i=0;i<old.length;i++)old[i].remove();
    var ids=ORDER.filter(function(id){return WINS[id];});
    if(ids.length){
      dockSep=document.createElement("span"); dockSep.className="wm-sep"; dock.appendChild(dockSep);
      ids.forEach(function(id){
        var w=WINS[id];
        var d=document.createElement("div");
        d.className="d wm-d"+(w.state==="min"?" wm-min":"");
        d.title=w.title; d.dataset.label=w.title+(w.state==="min"?" (minimized)":"");
        d.innerHTML='<span class="ic" data-i="'+(w.icon||"square")+'"></span><span class="wm-dot"></span>';
        d.__act=function(){ run({verb:"focus",id:id}); };
        d.addEventListener("click",function(e){e.stopPropagation();e.preventDefault();run({verb:"focus",id:id});},true);
        dock.appendChild(d);
      });
    }
    if(window.GW){ if(GW.paintIcons)GW.paintIcons(dock); if(GW.nav)GW.nav.rebuild(); }
  }

  /* ---- postMessage path for pages living inside a window frame ---- */
  addEventListener("message",function(e){
    var d=e.data;
    if(!d||d.gose!=="wm"||!d.verb)return;
    if(!d.id){ // a frame talking about itself: find which window holds the sender
      for(var id in WINS){
        var w=WINS[id], fr=w.wb&&w.wb.body&&w.wb.body.querySelector("iframe");
        if(fr&&fr.contentWindow===e.source){ d.id=id; break; }
      }
    }
    run(d);
  });

  /* ---- public API + boot ---- */
  window.GoseWM={ open:openApp, winify:winify, focus:function(id){run({verb:"focus",id:id});},
                  close:function(id){run({verb:"close",id:id});},
                  minimize:function(id){run({verb:"minimize",id:id});},
                  snap:function(id,zone){run({verb:"snap",id:id,zone:zone});},
                  list:list, run:run };
  applyIntercepts(); winifyButtons(); dockRender();
  sync();                                          // first heartbeat (also drains any queue)
})();
