/* GOSE — web-layer window manager (docs/23, wave 1 / chunks A+B).
   A THIN wrapper that binds WinBox frames (assets/vendor/winbox, Apache-2.0) to the
   GW widget base (docs/21). Additive: loads alongside widget.js on the desktop and
   touches nothing inside it.

   Chunk A:
   * WEB WINDOWS — opens an existing gose-*.html page as an iframe inside a WinBox
     frame (create/close/focus/minimize/maximize/move/resize/snap). The blue focus
     glow (docs/21 §1.4 — focus-only, same blue everywhere) marks the focused frame.
   * REGISTRY — every open window is mirrored to the server registry: the full list
     is POSTed to /windows/sync on every change + a heartbeat; the sync response
     carries queued /wm/<verb> commands which are executed here.
   * WIDGET→WINDOW (docs/23 §5) — "maximizing" a widget re-parents its LIVE body
     node into a WinBox frame via mount() (NO reload), leaving a ghost slot.
   * LAUNCHER INTERCEPT — windowable pages open as windows, not navigations.
   * DOCK — running-window tiles in the existing #dock nav zone.

   Chunk B (docs/23 §4.3/§4.4/§5/§7 + docs/25 §5b):
   * LONG-POLL transport — the shell holds one hanging GET /wm/poll?wait=20 open, so
     pad-bridge /wm/event semantic events land in milliseconds instead of riding the
     4s sync heartbeat (which stretches to 10-90s under load — chunk A finding).
   * WINDOW CAROUSEL (hold Guide) — horizontal cards of all windows (title+kind+state,
     incl. freed descriptors); L1/R1/stick cycles, A / Guide-release selects, B
     cancels, Y → OVERVIEW grid (d-pad to cell, A focus).
   * CONTROLLER SNAP (L2+d-pad) — Snap Layout chooser (zone grid, d-pad highlight,
     A places the focused window), then SNAP-ASSIST fill (remaining zones offered
     the other windows as cards). Zone rects mirror the server's _snap_rect.
   * ACT-OUT → RE-SUMMON (§5, §10.3) — the three HONEST tiers for web windows:
       minimize  (still live)
       suspend   (iframe src dropped; node + descriptor kept; JS heap collectible;
                  resumes by reload at saved route+scroll)
       free      (node torn down; descriptor only, persisted in localStorage
                  gose-wdesc; re-summon from dock/launcher reloads it)
     Every transition is LABELLED with its tier (toast + registry mem.tier), and
     minimized page-windows AUTO-SUSPEND after a short grace (the cheap default —
     live iframes are what made the kiosk heartbeat stretch).
   * MODAL KEY LAYER — while a WM modal is open a capture-phase handler owns the
     keys; same-origin iframes forward their keys up, so a focused web window can
     no longer trap the WM layer (the chunk-A kiosk gap).                        */
(function(){
  "use strict";
  if(!window.WinBox){ return; }                 // no frame engine -> desktop behaves as before

  /* ---- APP-CLASS POLICY (docs/23 §4.5, owner-set 2026-06-07) ----
     Two classes:
     * FULLSCREEN-NATIVE — Library / Emulators (the Library page) / AI Players /
       Settings: direct full-page navigation, NEVER a window. Not in APPS, and a
       frame that navigates to one of them escapes its window (hookFrame watch).
     * WINDOW class — everything in APPS opens as a WinBox window, MAXIMIZED by
       default (fullscreen-maximized; snap/restore still re-place it). Native
       X apps (Firefox/VLC/Chromium/...) get the same maximized default from the
       bridge (gose-pad-nav.py NativeWatch).
     Anything in neither set keeps normal full-page navigation
     (lock/home/boot/oobe are deliberately absent). ---- */
  var FS_NATIVE={
    "gose-library.html":1,   // Library + Emulators surface
    "gose-settings.html":1,
    "gose-ai.html":1,        // AI Players
    "gose-lock.html":1       // lock must never render inside a frame
  };
  var APPS={
    "gose-files.html":      {title:"Files",        icon:"folder"},
    "gose-store.html":      {title:"Store",        icon:"download"},
    "gose-term.html":       {title:"Terminal",     icon:"terminal"},
    "gose-taskman.html":    {title:"Task Manager", icon:"cpu"},
    "gose-gallery.html":    {title:"Gallery",      icon:"image"},
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
  function baseUrl(u){ return (u||"").split("?")[0].split("#")[0]; }
  function appOf(url){ return APPS[baseUrl(url)] || null; }

  /* ---- styles: blue focus glow (docs/21), frames, dock tiles, WM modals, toast ---- */
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
    ".dock .d.wm-d.wm-min{opacity:.5}"+
    ".dock .d.wm-d.wm-freed{opacity:.35;border:1px dashed #ffffff2e}"+
    /* WM modal layer (carousel / overview / snap chooser / assist) */
    ".wm-modal{position:fixed;inset:0;z-index:2147480000;display:flex;flex-direction:column;"+
      "align-items:center;justify-content:center;background:#05060ecc;backdrop-filter:blur(8px)}"+
    ".wm-mtitle{color:#aab1d6;font:600 13px Inter,system-ui;letter-spacing:.14em;margin-bottom:22px}"+
    ".wm-strip{display:flex;gap:18px;align-items:stretch;max-width:92vw;overflow:hidden;padding:6px 30px}"+
    ".wm-card{width:190px;flex:none;background:#141831;border:1px solid #ffffff1c;border-radius:14px;"+
      "padding:18px 14px;text-align:center;color:#cdd2ea;transition:transform .12s}"+
    ".wm-card .ic{font-size:34px;display:block;margin:6px auto 10px;width:34px;height:34px}"+
    ".wm-card .ti{font:600 13px Inter,system-ui;letter-spacing:.03em}"+
    ".wm-card .st{font-size:10.5px;color:#8b92b0;margin-top:6px;letter-spacing:.06em;text-transform:uppercase}"+
    ".wm-card.sel{box-shadow:0 0 0 2px var(--accent,#5cd0ff),0 0 22px #5cd0ff99;transform:scale(1.06);color:#fff}"+
    ".wm-grid{display:grid;gap:14px;padding:6px 26px;max-width:92vw}"+
    ".wm-grid .wm-card{width:170px}"+
    ".wm-rows{display:flex;gap:30px;align-items:flex-end}"+
    ".wm-lay{display:flex;flex-direction:column;align-items:center;gap:8px;color:#8b92b0;"+
      "font:500 11px Inter,system-ui;letter-spacing:.06em}"+
    ".wm-zonebox{position:relative;width:150px;height:84px;background:#0d1020;"+
      "border:1px solid #ffffff22;border-radius:8px}"+
    ".wm-zone{position:absolute;background:#ffffff10;border:1px solid #ffffff28;border-radius:4px}"+
    ".wm-zone.sel{background:#5cd0ff33;border-color:var(--accent,#5cd0ff);box-shadow:0 0 12px #5cd0ff66}"+
    ".wm-hint{color:#7f86a6;font-size:12px;margin-top:24px}"+
    ".wm-toast{position:fixed;left:50%;transform:translateX(-50%);bottom:86px;z-index:2147480001;"+
      "background:#0c0c1ef0;border:1px solid var(--accent,#5cd0ff);border-radius:10px;color:#dfe5f5;"+
      "font:500 12.5px Inter,system-ui;padding:9px 16px;opacity:0;transition:opacity .18s;"+
      "pointer-events:none;max-width:72vw;text-align:center}"+
    ".wm-toast.on{opacity:1}";
  document.head.appendChild(css);

  /* =============================== state =============================== */
  var WINS={};                 // id -> {wb, kind:"page"|"widget", title, icon, url, widget, state, token, tier}
  var ORDER=[];                // open order (next/prev cycling)
  var DESC={};                 // id -> persisted window-memory descriptor {id,title,icon,url,route,scroll,geom,state}
  var MODAL=null;              // open WM modal: {type:"carousel"|"overview"|"snap"|"assist", ...}
  var AUTOSUSPEND=true;        // minimized page-windows auto-suspend (cheap default — chunk A perf finding)
  var AUTOSUSPEND_MS=2500;
  var seq=0;
  var syncT=null;
  // one id per page LOAD — lets the server (and a debugging AI agent) tell which shell
  // instance the registry cache is coming from, and spot a second/stale consumer.
  var INST=(navigator.userAgent.indexOf("Chrome")>=0?"chrome-":"webkit-")+
           Math.random().toString(36).slice(2,8)+"-"+Date.now().toString(36);

  /* ---- descriptor persistence (localStorage gose-wdesc, alongside gose-wpos) ---- */
  function descSave(){ try{localStorage.setItem("gose-wdesc",JSON.stringify(DESC));}catch(e){} }
  (function descLoad(){
    try{ DESC=JSON.parse(localStorage.getItem("gose-wdesc")||"{}")||{}; }catch(e){ DESC={}; }
    // a fresh shell has no live nodes: anything that was merely "suspended" when the
    // page died is now honestly "freed" (descriptor only).
    for(var k in DESC){ if(DESC[k])DESC[k].state="freed"; else delete DESC[k]; }
    descSave();
  })();

  /* ---- the honest-tier toast (docs/23 §10.3: the model LABELS which tier happened) ---- */
  var toastEl=document.createElement("div"); toastEl.className="wm-toast";
  document.body.appendChild(toastEl);
  var toastT=null;
  function label(msg){
    toastEl.textContent=msg; toastEl.classList.add("on");
    clearTimeout(toastT); toastT=setTimeout(function(){toastEl.classList.remove("on");},2600);
  }

  function focusedId(){
    for(var id in WINS){ if(WINS[id].wb && WINS[id].wb.focused) return id; }
    return null;
  }
  function frameOf(w){
    return (w&&w.wb&&w.wb.body&&w.wb.body.querySelector("iframe"))||null;
  }
  function list(){
    var out=Object.keys(WINS).map(function(id){
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
      if(w.tier)mem.tier=w.tier;
      return { id:id, kind:"web", title:w.title, icon:w.icon,
               url:w.url||null, widget:w.widget||null,
               geom:wb?{x:wb.x,y:wb.y,w:wb.width,h:wb.height}:{},
               state:w.state, group:null, focused:!!(wb&&wb.focused), mem:mem };
    });
    // freed windows live on as descriptors — visible in the registry + dock so they
    // can be re-summoned (docs/23 §5: "window memory").
    Object.keys(DESC).forEach(function(id){
      var d=DESC[id]; if(!d||WINS[id]||d.state!=="freed")return;
      out.push({ id:id, kind:"web", title:d.title, icon:d.icon, url:d.url, widget:null,
                 geom:d.geom||{}, state:"freed", group:null, focused:false,
                 mem:{url:d.url,route:d.route,scroll:d.scroll,tier:"freed (descriptor only — re-summon reloads)"} });
    });
    return out;
  }
  function modalUI(){
    if(!MODAL)return null;
    var u={modal:MODAL.type};
    if(MODAL.type==="carousel"||MODAL.type==="overview"){
      var it=MODAL.items[MODAL.sel]; u.sel=it?it.id:null; u.count=MODAL.items.length;
    } else if(MODAL.type==="snap"){
      u.zone=curSnapZone(); u.target=MODAL.target;
    } else if(MODAL.type==="assist"){
      u.zone=MODAL.zones[MODAL.zi]; var c=MODAL.cands[MODAL.sel]; u.sel=c||null;
      u.zones_left=MODAL.zones.length-MODAL.zi;
    }
    return u;
  }

  /* ---- registry sync: full list on every change + 4s heartbeat; the response
     piggybacks queued /wm commands. Modal/UI state rides along so the server (and
     a text-first verifier) can see what the carousel is showing. ---- */
  function sync(){
    clearTimeout(syncT); syncT=setTimeout(sync,4000);
    try{
      fetch("/windows/sync",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({windows:list(),inst:INST,ui:modalUI(),
          // the live zone walk order + current focus (docs/25 §5b/§5c verification surface)
          nav:(window.GW&&GW.nav&&GW.nav.order)?{order:GW.nav.order(),
            cur:GW.nav.current?GW.nav.current():null}:null})})
        .then(function(r){return r.json();})
        .then(function(d){ (d&&d.commands||[]).forEach(run); })
        .catch(function(){});
    }catch(e){}
  }
  function syncSoon(){ clearTimeout(syncT); syncT=setTimeout(sync,120); }

  /* ---- LONG-POLL: one hanging GET so server-queued commands (the pad bridge's
     /wm/event path) arrive in ms, not on the 4s heartbeat. ---- */
  var lpFails=0;
  function longPoll(){
    fetch("/wm/poll?wait=20",{cache:"no-store"})
      .then(function(r){return r.json();})
      .then(function(d){ lpFails=0; ((d&&d.commands)||[]).forEach(run); setTimeout(longPoll,25); })
      .catch(function(){ lpFails++; setTimeout(longPoll,Math.min(5000,400*lpFails)); });
  }

  /* ---- execute one /wm command (server queue, long-poll, or postMessage) ---- */
  function run(c){
    if(!c||!c.verb)return;
    var w=c.id?WINS[c.id]:null, wb=w&&w.wb;
    switch(c.verb){
      case "open":    if(c.url)openApp(c.url,c.title,c.icon); break;
      case "winify":  if(c.id)winify(c.id); break;
      case "focus":
        if(w){ if(w.state==="suspended")resume(c.id); else { if(w.state==="min")wb.minimize(false); wb.focus(); } }
        else if(c.id&&DESC[c.id])resummon(c.id);
        break;
      case "close":   if(wb)wb.close(); break;               // plain close drops the descriptor too
      case "minimize":if(wb&&w.state!=="suspended")wb.minimize(true); break;
      case "restore": if(w){ if(w.state==="suspended")resume(c.id); else {wb.minimize(false); wb.maximize(false); wb.focus();} } break;
      case "maximize":if(wb)wb.maximize(true); break;
      case "move":    if(wb)wb.move(num(c.x,wb.x),num(c.y,wb.y)); break;
      case "resize":  if(wb)wb.resize(num(c.w,wb.width),num(c.h,wb.height)); break;
      case "snap":    if(wb)snap(wb,c.zone); break;
      case "suspend": if(c.id)suspendWin(c.id); break;
      case "free":    if(c.id)freeWin(c.id); break;
      case "resummon":if(c.id)resummon(c.id); break;
      case "next":    if(MODAL)modalMove(1); else cycle(1); break;
      case "prev":    if(MODAL)modalMove(-1); else cycle(-1); break;
      /* ---- WM modal layer (pad bridge semantic events; docs/23 §7) ---- */
      case "carousel":openCarousel(); break;
      case "overview":openOverview(); break;
      case "snapmode":openSnap(); break;
      case "select":  if(MODAL)modalSelect(); break;
      case "cancel":  if(MODAL)closeModal(); break;
      case "left":    if(MODAL)modalMove(-1,"x"); break;
      case "right":   if(MODAL)modalMove(1,"x"); break;
      case "up":      if(MODAL)modalMove(-1,"y"); break;
      case "down":    if(MODAL)modalMove(1,"y"); break;
      case "act":     actOut(modalSelId()||focusedId()); break;
      /* full shell refresh (asset deploys / recovery) without killing the kiosk */
      case "reload":  location.reload(); break;
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

  /* ---- snap zones: fractional rects, single source for placement AND the chooser
     thumbnails; mirrors the server's _snap_rect (docs/23 §4.3). ---- */
  var ZONE_FRACS={
    left:[0,0,.5,1], right:[.5,0,.5,1], top:[0,0,1,.5], bottom:[0,.5,1,.5],
    tl:[0,0,.5,.5], tr:[.5,0,.5,.5], bl:[0,.5,.5,.5], br:[.5,.5,.5,.5],
    "col-l":[0,0,1/3,1], "col-c":[1/3,0,1/3,1], "col-r":[2/3,0,1/3,1],
    main:[0,0,2/3,1], side:[2/3,0,1/3,1], full:[0,0,1,1] };
  function snap(wb,zone){
    var f=ZONE_FRACS[zone]; if(!f)return;
    var W=innerWidth,H=innerHeight;
    wb.minimize(false); wb.maximize(false);
    wb.move(Math.round(f[0]*W),Math.round(f[1]*H));
    wb.resize(Math.round(f[2]*W),Math.round(f[3]*H));
    wb.focus();
  }

  /* ============== act-out tiers + window memory (docs/23 §5, §10.3) ==============
     minimize (live) → suspend (src dropped, node+descriptor kept) → free (node gone,
     descriptor only). Each transition is labelled with its honest tier. */
  function captureDesc(id,w){
    var fr=frameOf(w), route=w.url, scroll=0;
    try{
      if(fr&&fr.contentWindow&&fr.contentWindow.location.href!=="about:blank"){
        route=fr.contentWindow.location.href.replace(location.origin+"/","");
        scroll=fr.contentWindow.scrollY||
               (fr.contentWindow.document&&fr.contentWindow.document.documentElement.scrollTop)||0;
      }
    }catch(e){}
    var wb=w.wb;
    return { id:id, title:w.title, icon:w.icon, url:w.url, route:route,
             scroll:Math.round(scroll), state:"suspended",
             geom:wb?{x:wb.x,y:wb.y,w:wb.width,h:wb.height}:null, ts:Date.now() };
  }
  function suspendWin(id){
    var w=WINS[id]; if(!w||!w.wb)return;
    if(w.kind==="widget"){
      // a widget window's body IS the widget (same live node) — tearing its src down
      // would kill the widget. Honest tier for it: minimize only.
      if(w.state!=="min")w.wb.minimize(true);
      label(w.title+" — minimized (widget windows stay LIVE: their body is the widget itself)");
      syncSoon(); dockRender(); return;
    }
    if(w.state==="suspended")return;
    var d=captureDesc(id,w); d.state="suspended";
    DESC[id]=d; descSave();
    var fr=frameOf(w);
    if(fr){ try{ fr.src="about:blank"; }catch(e){} }
    w.state="suspended"; w.tier="suspended (iframe src dropped; node+descriptor kept; JS heap collectible)";
    clearTimeout(w._asT);
    w.wb.minimize(true);
    label(w.title+" — SUSPENDED: iframe released (RAM back to the shell heap); focus reloads it at the saved spot");
    syncSoon(); dockRender();
  }
  function resume(id,skipRaise){
    var w=WINS[id]; if(!w||!w.wb)return null;
    if(w.state==="suspended"){
      var d=DESC[id]||{}, fr=frameOf(w);
      if(fr){
        fr.dataset.restoreScroll=String(d.scroll||0);
        try{ fr.src=d.route||w.url; }catch(e){ fr.src=w.url; }
      }
      delete DESC[id]; descSave();
      w.state="normal"; w.tier="live (resumed from suspend — src reloaded at saved route/scroll)";
      label(w.title+" — resumed from SUSPEND (reloaded at the saved route + scroll)");
    }
    if(!skipRaise){ w.wb.minimize(false); w.wb.focus(); }
    syncSoon(); dockRender(); return id;
  }
  function freeWin(id){
    var w=WINS[id];
    if(!w){ return; }                                     // already freed (descriptor only)
    if(w.kind==="widget"){
      w.wb.close();                                       // returns the node to the widget slot
      label(w.title+" — window closed: body returned to its widget slot (widgets free by un-mounting)");
      return;
    }
    var d=(w.state==="suspended"&&DESC[id])?DESC[id]:captureDesc(id,w);
    d.state="freed"; DESC[id]=d; descSave();
    w._keepDesc=true;
    clearTimeout(w._asT);
    w.wb.close();
    label(d.title+" — FREED: window torn down, descriptor only (dock tile / launcher re-summons it)");
    syncSoon(); dockRender();
  }
  function resummon(id){
    if(WINS[id]){ var w=WINS[id];
      if(w.state==="suspended")return resume(id);
      w.wb.minimize(false); w.wb.focus(); syncSoon(); return id; }
    var d=DESC[id]; if(!d)return null;
    delete DESC[id]; descSave();
    var nid=openApp(d.route||d.url,d.title,d.icon,{geom:d.geom,scroll:d.scroll,from:"descriptor",force:true});
    label((d.title||"window")+" — RE-SUMMONED from its descriptor (was freed; reloaded where it was)");
    return nid;
  }
  // the act-out ladder: live → minimize(+auto-suspend) → suspend → free (docs/23 §11 Q2)
  function actOut(id){
    if(!id)return;
    var w=WINS[id];
    if(!w){ if(DESC[id])label((DESC[id].title||"window")+" — already freed (descriptor only)"); return; }
    if(w.kind==="widget"){ suspendWin(id); modalRefresh(); return; }   // = minimize (labelled)
    if(w.state==="normal"||w.state==="max"){
      w.wb.minimize(true);
      label(w.title+" — minimized (STILL LIVE"+(AUTOSUSPEND?"; auto-suspends in "+(AUTOSUSPEND_MS/1000)+"s":"")+")");
    } else if(w.state==="min"){ suspendWin(id); }
    else if(w.state==="suspended"){ freeWin(id); }
    modalRefresh();
  }
  function ensureLive(id){
    if(WINS[id]){
      if(WINS[id].state==="suspended")resume(id,true);
      else if(WINS[id].state==="min")WINS[id].wb.minimize(false);
      return id;
    }
    if(DESC[id])return resummon(id);
    return null;
  }

  /* ---- shared WinBox wiring (state mirror + glow comes free via .winbox.focus) ---- */
  function wire(id,wb,onclose){
    var w=WINS[id];
    wb.onfocus=function(){ if(w.state!=="suspended")w.state=(w.state==="max")?"max":"normal"; clearTimeout(w._asT); syncSoon(); };
    wb.onblur=function(){ syncSoon(); };
    wb.onminimize=function(){
      if(w.state!=="suspended"){
        w.state="min";
        // cheap default: a minimized page-window's iframe is dropped after a grace
        // period (suspend tier) — live background iframes are what made the kiosk
        // crawl in chunk A. Restoring/focusing cancels it; resume reloads.
        if(AUTOSUSPEND&&w.kind==="page"){
          clearTimeout(w._asT);
          w._asT=setTimeout(function(){
            if(WINS[id]&&WINS[id].state==="min"){
              suspendWin(id);
              label(w.title+" — minimized → AUTO-SUSPENDED (cheap default; focus reloads it)");
            }
          },AUTOSUSPEND_MS);
        }
      }
      syncSoon(); dockRender();
    };
    wb.onmaximize=function(){ w.state="max"; syncSoon(); };
    wb.onrestore=function(){
      if(w.state==="suspended"){ resume(id,true); }
      else w.state="normal";
      clearTimeout(w._asT); syncSoon(); dockRender();
    };
    wb.onmove=function(){ syncSoon(); };
    wb.onresize=function(){ syncSoon(); };
    wb.onclose=function(){
      if(onclose)onclose();
      clearTimeout(w._asT);
      if(!w._keepDesc&&DESC[id]){ delete DESC[id]; descSave(); }   // user close = no ghost
      delete WINS[id]; ORDER=ORDER.filter(function(x){return x!==id;});
      syncSoon(); dockRender(); modalRefresh();
      return false;                              // false = allow the close
    };
  }

  /* ---- iframe key forwarding: a focused web window must not trap the WM layer.
     All gose pages are same-origin, so hook each frame's window and forward
     WM-relevant keys to the shell document (capture handler below). ---- */
  function hookFrame(id,wb){
    function arm(){
      try{
        var fr=wb.body&&wb.body.querySelector("iframe");
        if(!fr||!fr.contentWindow)return;
        var cw=fr.contentWindow;
        /* FRAME-ESCAPE WATCH (docs/23 §4.5): a windowed page that navigates to a
           FULLSCREEN-NATIVE page (Library/Settings/AI/lock — e.g. the Apps grid's
           Library card, or Quick-Access links) must not render it inside the
           frame: close the window and navigate the REAL page there. A frame that
           navigates to the desktop itself (gose-home.html — e.g. Quick-Access
           "Exit to Desktop") just closes: the desktop is already underneath
           (016c6cb's nested-desktop bug, killed for the navigation path too). */
        var fpath=((cw.location&&cw.location.pathname)||"").split("/").pop();
        if(fpath==="gose-home.html"){ run({verb:"close",id:id}); return; }
        if(FS_NATIVE[fpath]){
          var dest=fpath+((cw.location&&cw.location.search)||"")+((cw.location&&cw.location.hash)||"");
          run({verb:"close",id:id});
          location.href=dest;
          return;
        }
        // scroll restore (suspend/free re-summon path)
        if(fr.dataset.restoreScroll){
          var sy=parseInt(fr.dataset.restoreScroll,10)||0;
          delete fr.dataset.restoreScroll;
          if(sy)try{ cw.scrollTo(0,sy); }catch(e){}
        }
        if(cw.__wmFwd)return; cw.__wmFwd=true;
        cw.addEventListener("keydown",function(e){
          var wm=MODAL||(e.ctrlKey&&e.key==="Tab");
          if(wm){
            e.preventDefault(); e.stopImmediatePropagation();
            try{
              document.body.dispatchEvent(new KeyboardEvent("keydown",
                {key:e.key,ctrlKey:e.ctrlKey,bubbles:true,cancelable:true}));
            }catch(err){}
            return;
          }
          /* Escape inside a windowed page = close THIS window (shell-owned window op —
             same rule as the shell-side handler below). Needed when real DOM focus sits
             INSIDE the iframe (mouse click / page-side .focus()): keys then land here
             directly and the shell capture handler never sees them, so without this the
             page's own Escape→"go to desktop" navigation fires inside the frame.
             LAYERED-ESCAPE CONTRACT (docs/27 §3.10): while the page has a modal /
             sub-layer open it sets body[data-gose-modal="1"] — then Escape belongs
             to the PAGE (close the modal, one layer at a time), not the window. */
          if(e.key==="Escape"&&!e.__wmFwd){
            var pm=false;
            try{ pm=!!(cw.document&&cw.document.body&&cw.document.body.dataset.goseModal==="1"); }catch(err){}
            if(pm)return;                       // page modal owns this Escape
            var w=WINS[id];
            if(w&&(w.state==="normal"||w.state==="max")){
              e.preventDefault(); e.stopImmediatePropagation();
              run({verb:"close",id:id});
            }
          }
        },true);
      }catch(e){}
    }
    var fr=wb.body&&wb.body.querySelector("iframe");
    if(fr)fr.addEventListener("load",arm);
    arm();
  }

  /* =================== web windows: a page in a frame =================== */
  function openApp(url,title,icon,opts){
    opts=opts||{};
    var base=baseUrl(url);
    /* fullscreen-native class (docs/23 §4.5): never a window — direct page
       navigation, even when asked via /wm open or a stale freed descriptor. */
    if(FS_NATIVE[base]){ location.href=url; return null; }
    if(!opts.force){
      // RE-SUMMON path: an existing window for this app gets focused/resumed instead
      // of duplicated; a freed descriptor for it gets remounted (docs/23 §5).
      for(var id0 in WINS){ var w0=WINS[id0];
        if(w0.kind==="page"&&baseUrl(w0.url)===base){
          if(w0.state==="suspended")resume(id0);
          else { w0.wb.minimize(false); w0.wb.focus(); }
          return id0;
        } }
      for(var dk in DESC){ if(DESC[dk].state==="freed"&&baseUrl(DESC[dk].url)===base)return resummon(dk); }
    }
    var meta=appOf(url)||{};
    title=title||meta.title||url.replace(/^gose-|\.html.*$/g,"");
    icon=icon||meta.icon||"square";
    var id="win-"+base.replace(/^gose-|\.html.*$/g,"").replace(/[^a-z0-9]+/gi,"")+"-"+(++seq);
    var W=innerWidth,H=innerHeight;
    var n=ORDER.length;
    var g=opts.geom||{};
    var wb=new WinBox({
      title:title, url:url,
      x:(g.x!=null?g.x:Math.min(120+n*48,W-940)), y:(g.y!=null?g.y:Math.min(90+n*40,H-700)),
      width:(g.w||Math.min(920,W-160)), height:(g.h||Math.min(640,H-200))
    });
    // canonical app url = base page; any route/query lives in the descriptor only
    WINS[id]={wb:wb,kind:"page",title:title,icon:icon,url:base,state:"normal",tier:"live"};
    ORDER.push(id);
    wire(id,wb,null);
    if(opts.scroll){ var fr=frameOf(WINS[id]); if(fr)fr.dataset.restoreScroll=String(opts.scroll); }
    hookFrame(id,wb);
    /* window class opens FULLSCREEN-MAXIMIZED by default (docs/23 §4.5);
       a descriptor re-summon (opts.geom) restores its saved placement instead. */
    if(!opts.geom)wb.maximize(true);
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
    WINS[id]={wb:wb,kind:"widget",title:title,icon:icon,widget:widgetId,state:"normal",token:token,
              tier:"live (widget body — same DOM node, no reload)"};
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
    if(MODAL)return;
    var f=document.querySelector(".focus,.wfocus");
    var w=f&&f.closest&&f.closest(".gw");
    if(w&&w.dataset.wid){ winify(w.dataset.wid); e.preventDefault(); }
  });

  /* ====================== WM MODAL LAYER (docs/23 §4.3/§4.4) ======================
     One overlay hosts four modes:
       carousel — hold-Guide horizontal cards; L1/R1/d-pad cycle; A/release select;
                  B cancel; Y → overview.
       overview — grid of all windows; d-pad 2D; A focus.
       snap     — L2+d-pad zone chooser (layout thumbnails); A places focused window.
       assist   — Snap-Assist fill: remaining zones offered the other windows.     */
  var SNAP_LAYOUTS=[
    {name:"HALVES",   zones:["left","right"]},
    {name:"QUARTERS", zones:["tl","tr","bl","br"]},
    {name:"THIRDS",   zones:["col-l","col-c","col-r"]},
    {name:"MAIN + SIDE", zones:["main","side"]}
  ];
  var modalEl=null;
  function stateTag(st){
    return {normal:"live",max:"live · max",min:"minimized (live)",
            suspended:"suspended",freed:"freed"}[st]||st;
  }
  function carouselItems(){
    var arr=[];
    ORDER.forEach(function(id){ var w=WINS[id]; if(!w)return;
      arr.push({id:id,title:w.title,icon:w.icon,state:w.state,kind:w.kind}); });
    Object.keys(DESC).forEach(function(id){
      var d=DESC[id]; if(!d||WINS[id]||d.state!=="freed")return;
      arr.push({id:id,title:d.title,icon:d.icon,state:"freed",kind:"page"}); });
    return arr;
  }
  function modalSelId(){
    if(!MODAL)return null;
    if(MODAL.type==="carousel"||MODAL.type==="overview"){
      var it=MODAL.items[MODAL.sel]; return it?it.id:null; }
    if(MODAL.type==="snap")return MODAL.target;
    if(MODAL.type==="assist")return MODAL.cands[MODAL.sel]||null;
    return null;
  }
  function closeModal(){
    MODAL=null;
    if(modalEl){ modalEl.remove(); modalEl=null; }
    syncSoon();
  }
  function modalRoot(title,hint){
    if(modalEl)modalEl.remove();
    modalEl=document.createElement("div"); modalEl.className="wm-modal";
    modalEl.innerHTML='<div class="wm-mtitle">'+title+'</div><div class="wm-bodyz"></div>'+
      '<div class="wm-hint">'+hint+'</div>';
    document.body.appendChild(modalEl);
    return modalEl.querySelector(".wm-bodyz");
  }
  function cardHTML(it,sel){
    return '<div class="wm-card'+(sel?" sel":"")+'">'+
      '<span class="ic" data-i="'+(it.icon||"square")+'"></span>'+
      '<div class="ti">'+(it.title||it.id)+'</div>'+
      '<div class="st">'+(it.kind==="widget"?"widget · ":"")+stateTag(it.state)+'</div></div>';
  }
  function paint(el){ if(window.GW&&GW.paintIcons)GW.paintIcons(el); }

  function openCarousel(){
    var items=carouselItems();
    if(!items.length){ label("No windows open — nothing to switch to"); closeModal(); return; }
    var sel=0, fid=focusedId();
    items.forEach(function(it,i){ if(it.id===fid)sel=i; });
    if(MODAL&&MODAL.type==="carousel"){ MODAL.items=items; MODAL.sel=Math.min(MODAL.sel,items.length-1); }
    else MODAL={type:"carousel",items:items,sel:sel};
    renderModal();
  }
  function openOverview(){
    var items=carouselItems();
    if(!items.length){ label("No windows open"); closeModal(); return; }
    var sel=(MODAL&&(MODAL.type==="carousel"||MODAL.type==="overview"))?Math.min(MODAL.sel,items.length-1):0;
    MODAL={type:"overview",items:items,sel:sel,cols:Math.max(2,Math.min(4,Math.ceil(Math.sqrt(items.length))))};
    renderModal();
  }
  function openSnap(){
    var target=focusedId();
    if(!target){ // nothing focused: fall back to the most recent window
      var ids=ORDER.filter(function(id){return WINS[id];});
      target=ids[ids.length-1]||null;
    }
    if(!target){ label("No window to snap — open one first"); closeModal(); return; }
    if(MODAL&&MODAL.type==="snap")return renderModal();        // idempotent re-open
    MODAL={type:"snap",row:0,idx:0,target:target};
    renderModal();
  }
  function curSnapZone(){
    if(!MODAL||MODAL.type!=="snap")return null;
    return SNAP_LAYOUTS[MODAL.row].zones[MODAL.idx];
  }
  function renderModal(){
    if(!MODAL)return;
    if(MODAL.type==="carousel"){
      var bz=modalRoot("WINDOWS — L1/R1 cycle · A / release select · Y overview · B cancel","");
      var strip=document.createElement("div"); strip.className="wm-strip";
      strip.innerHTML=MODAL.items.map(function(it,i){return cardHTML(it,i===MODAL.sel);}).join("");
      bz.appendChild(strip); paint(strip);
      var c=strip.children[MODAL.sel]; if(c&&c.scrollIntoView)c.scrollIntoView({inline:"center",block:"nearest"});
    } else if(MODAL.type==="overview"){
      var bz2=modalRoot("ALL WINDOWS — d-pad to a cell · A focus · B back","");
      var grid=document.createElement("div"); grid.className="wm-grid";
      grid.style.gridTemplateColumns="repeat("+MODAL.cols+",1fr)";
      grid.innerHTML=MODAL.items.map(function(it,i){return cardHTML(it,i===MODAL.sel);}).join("");
      bz2.appendChild(grid); paint(grid);
    } else if(MODAL.type==="snap"){
      var t=WINS[MODAL.target];
      var bz3=modalRoot("SNAP — d-pad picks a zone for “"+((t&&t.title)||"window")+"” · A place · B cancel","");
      var rows=document.createElement("div"); rows.className="wm-rows";
      rows.innerHTML=SNAP_LAYOUTS.map(function(L,r){
        return '<div class="wm-lay"><div class="wm-zonebox">'+
          L.zones.map(function(z,i){
            var f=ZONE_FRACS[z];
            return '<div class="wm-zone'+((r===MODAL.row&&i===MODAL.idx)?" sel":"")+'" style="left:'+
              (f[0]*100)+'%;top:'+(f[1]*100)+'%;width:'+(f[2]*100)+'%;height:'+(f[3]*100)+'%"></div>';
          }).join("")+'</div>'+L.name+'</div>';
      }).join("");
      bz3.appendChild(rows);
    } else if(MODAL.type==="assist"){
      var bz4=modalRoot("SNAP ASSIST — fill “"+MODAL.zones[MODAL.zi]+"” · L/R pick · A place · B done","");
      var strip2=document.createElement("div"); strip2.className="wm-strip";
      strip2.innerHTML=MODAL.cands.map(function(id,i){
        var w=WINS[id], d=DESC[id];
        var it=w?{id:id,title:w.title,icon:w.icon,state:w.state,kind:w.kind}
                :{id:id,title:(d&&d.title)||id,icon:(d&&d.icon)||"square",state:"freed",kind:"page"};
        return cardHTML(it,i===MODAL.sel);
      }).join("");
      bz4.appendChild(strip2); paint(strip2);
    }
    syncSoon();
  }
  function modalRefresh(){ if(MODAL&&(MODAL.type==="carousel"||MODAL.type==="overview")){
    MODAL.items=carouselItems();
    if(!MODAL.items.length)return closeModal();
    MODAL.sel=Math.min(MODAL.sel,MODAL.items.length-1);
    renderModal(); } }
  function modalMove(d,axis){
    if(!MODAL)return;
    if(MODAL.type==="carousel"||MODAL.type==="assist"){
      var n=(MODAL.type==="carousel"?MODAL.items:MODAL.cands).length;
      if(!n)return;
      MODAL.sel=((MODAL.sel+d)%n+n)%n;
    } else if(MODAL.type==="overview"){
      var N=MODAL.items.length, c=MODAL.cols;
      var s=MODAL.sel+(axis==="y"?d*c:d);
      if(s>=0&&s<N)MODAL.sel=s;
      else if(axis!=="y"){ s=((MODAL.sel+d)%N+N)%N; MODAL.sel=s; }
    } else if(MODAL.type==="snap"){
      if(axis==="y"){ MODAL.row=((MODAL.row+d)%SNAP_LAYOUTS.length+SNAP_LAYOUTS.length)%SNAP_LAYOUTS.length;
        MODAL.idx=Math.min(MODAL.idx,SNAP_LAYOUTS[MODAL.row].zones.length-1); }
      else { var zn=SNAP_LAYOUTS[MODAL.row].zones.length;
        MODAL.idx=((MODAL.idx+d)%zn+zn)%zn; }
    }
    renderModal();
  }
  function modalSelect(){
    if(!MODAL)return;
    if(MODAL.type==="carousel"||MODAL.type==="overview"){
      var it=MODAL.items[MODAL.sel];
      closeModal();
      if(it)run({verb:"focus",id:it.id});
    } else if(MODAL.type==="snap"){
      var zone=curSnapZone(), layout=SNAP_LAYOUTS[MODAL.row], target=MODAL.target;
      var t=WINS[target];
      if(!t){ closeModal(); return; }
      snap(t.wb,zone);
      var remaining=layout.zones.filter(function(z){return z!==zone;});
      var cands=carouselItems().map(function(x){return x.id;})
        .filter(function(id){return id!==target;});
      if(remaining.length&&cands.length){
        MODAL={type:"assist",zones:remaining,zi:0,cands:cands,sel:0};
        renderModal();
      } else closeModal();
    } else if(MODAL.type==="assist"){
      var cid=MODAL.cands[MODAL.sel], z=MODAL.zones[MODAL.zi];
      if(cid){
        var lid=ensureLive(cid);
        var lw=lid&&WINS[lid];
        if(lw&&lw.wb)snap(lw.wb,z);
        MODAL.cands=MODAL.cands.filter(function(x){return x!==cid;});
      }
      MODAL.zi++;
      MODAL.sel=0;
      if(MODAL.zi>=MODAL.zones.length||!MODAL.cands.length)closeModal();
      else renderModal();
    }
    syncSoon();
  }

  /* ---- modal keyboard layer: capture on WINDOW so it fires before GW's nav
     (bubble) handler; iframes forward via hookFrame. Ctrl+Tab opens the carousel
     from a keyboard anywhere (incl. inside a web window). ---- */
  addEventListener("keydown",function(e){
    if(e.ctrlKey&&e.key==="Tab"){
      e.preventDefault(); e.stopImmediatePropagation();
      if(!MODAL)openCarousel(); else modalMove(1);
      return;
    }
    if(!MODAL)return;
    var map={ArrowLeft:["left"],ArrowRight:["right"],ArrowUp:["up"],ArrowDown:["down"],
             Enter:["select"]," ":["select"],Escape:["cancel"],
             "[":["prev"],"]":["next"],y:["overview"],Y:["overview"],x:["act"],X:["act"]};
    var v=map[e.key]; if(!v)return;
    e.preventDefault(); e.stopImmediatePropagation();
    run({verb:v[0]});
  },true);

  /* ---- shell→window key delivery (the missing half of the iframe key bridge).
     The pad bridge synthesizes X keys onto the SINGLE kiosk window; they land on
     whichever frame holds DOM focus. WinBox.focus() is z-order + glow only — it
     never moves DOM focus INTO the iframe — so a focused web window's keys hit the
     desktop shell (widget.js nav) instead of the window's own content, and you
     can't drive/launch inside a windowed app (e.g. the Library card). hookFrame
     (chunk B) only forwards keys the OTHER way (iframe→shell, WM-modal only).
     Fix: when a web window is focused AND no WM modal is open, forward the key
     INTO that same-origin frame's document so its own nav handles it exactly like
     on the desktop, and stop the shell nav from also acting. This is the docs/23
     §7 "Normal (in a window/app)" column — every pad op reaches the focused app.
     Focus-ownership is self-resolving: this handler only runs when the SHELL holds
     DOM focus; if focus is already inside the frame the key drives it natively and
     this stays dormant (no double-input). ---- */
  addEventListener("keydown",function(e){
    if(MODAL)return;                         // WM layer owns keys (handled above)
    if(e.__wmFwd)return;                     // never re-forward a synthetic
    if(e.ctrlKey&&e.key==="Tab")return;      // WM carousel shortcut stays with the shell
    var id=focusedId(); if(!id)return;       // no focused web window -> desktop nav, unchanged
    var w=WINS[id]; if(!w)return;
    /* Escape (pad B) on a FOCUSED window is a WINDOW op owned by the SHELL — it must
       NEVER be forwarded into the embedded page, whose own Escape handler is the
       fullscreen "back to desktop" navigation (e.g. gose-library.html:
       location.href="gose-home.html") and would render a desktop INSIDE the window.
       docs/23 §7: in a window, "back" backs out of THE WINDOW; docs/27 §3.2 "never
       traps" is satisfied at the window boundary. Per window type (docs/23 chunk B):
       app (page) windows CLOSE; widget windows are minimize-only (their body IS the
       live widget) so Escape MINIMIZES. Fullscreen (non-windowed) pages keep their
       own Escape semantics — this handler only runs when a window holds focus.
       LAYERED-ESCAPE CONTRACT (docs/27 §3.10): body[data-gose-modal="1"] means a
       modal/sub-layer is open and owns this Escape —
       * on the SHELL's own body (Quick-Access / OSK / notification center over the
         desktop): leave the key alone; the shell page's modal handler closes it.
       * on the focused FRAME's body (e.g. the Wi-Fi password picker): forward the
         Escape INTO the page (fall through to the forwarder below) so the modal
         closes; only the NEXT Escape closes the window. One layer at a time. */
    if(e.key==="Escape"&&(w.state==="normal"||w.state==="max")){
      if(document.body.dataset.goseModal==="1")return;   // shell modal owns it
      var pmod=false;
      try{
        var pfr=frameOf(w), pcw=pfr&&pfr.contentWindow;
        pmod=!!(pcw&&pcw.document&&pcw.document.body&&pcw.document.body.dataset.goseModal==="1");
      }catch(err){}
      if(!pmod){
        e.preventDefault(); e.stopImmediatePropagation();
        run({verb:(w.kind==="widget"?"minimize":"close"),id:id});
        return;
      }
      /* page modal open -> fall through to the forwarder: Escape goes INTO the frame */
    }
    if(w.kind!=="page"||w.state==="suspended"||w.state==="min")return;
    var fr=frameOf(w), cw=fr&&fr.contentWindow;
    if(!cw||!cw.document)return;             // frame not ready -> let the shell have it
    e.preventDefault(); e.stopImmediatePropagation();
    try{
      var KE=cw.KeyboardEvent||window.KeyboardEvent;
      var ke=new KE("keydown",{key:e.key,code:e.code,keyCode:e.keyCode,
        which:e.which,bubbles:true,cancelable:true,
        ctrlKey:e.ctrlKey,shiftKey:e.shiftKey,altKey:e.altKey,metaKey:e.metaKey});
      ke.__wmFwd=true;
      var tgt=cw.document.activeElement||cw.document.body||cw.document.documentElement;
      tgt.dispatchEvent(ke);
    }catch(err){}
  },true);

  /* ============ dock: running-window tiles in the existing #dock zone ============ */
  function dockRender(){
    var dock=document.getElementById("dock"); if(!dock)return;
    // clear previous window tiles
    var old=dock.querySelectorAll(".wm-d,.wm-sep");
    for(var i=0;i<old.length;i++)old[i].remove();
    var ids=ORDER.filter(function(id){return WINS[id];});
    var freed=Object.keys(DESC).filter(function(k){return !WINS[k]&&DESC[k]&&DESC[k].state==="freed";});
    if(ids.length||freed.length){
      var sep=document.createElement("span"); sep.className="wm-sep"; dock.appendChild(sep);
      ids.forEach(function(id){
        var w=WINS[id];
        var d=document.createElement("div");
        var mincls=(w.state==="min"||w.state==="suspended")?" wm-min":"";
        d.className="d wm-d"+mincls;
        d.title=w.title; d.dataset.label=w.title+
          (w.state==="min"?" (minimized)":w.state==="suspended"?" (suspended — A resumes)":"");
        d.innerHTML='<span class="ic" data-i="'+(w.icon||"square")+'"></span><span class="wm-dot"></span>';
        d.__act=function(){ run({verb:"focus",id:id}); };
        d.addEventListener("click",function(e){e.stopPropagation();e.preventDefault();run({verb:"focus",id:id});},true);
        dock.appendChild(d);
      });
      freed.forEach(function(id){
        var ds=DESC[id];
        var d=document.createElement("div");
        d.className="d wm-d wm-freed";
        d.title=ds.title; d.dataset.label=ds.title+" (freed — A re-summons)";
        d.innerHTML='<span class="ic" data-i="'+(ds.icon||"square")+'"></span>';
        d.__act=function(){ resummon(id); };
        d.addEventListener("click",function(e){e.stopPropagation();e.preventDefault();resummon(id);},true);
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
        var w=WINS[id], fr=frameOf(w);
        if(fr&&fr.contentWindow===e.source){ d.id=id; break; }
      }
    }
    run(d);
  });

  /* ---- widget toggle (gose-wenabled, docs/23 §4.5): when the Widgets page —
     typically open as a WINDOW over this very desktop — turns a widget OFF, a
     window holding that widget's live body must close (the body returns to its
     slot; widget.js then hides the slot via its own storage listener). ---- */
  addEventListener("storage",function(ev){
    if(ev.key!=="gose-wenabled")return;
    var en={}; try{ en=JSON.parse(ev.newValue||"{}")||{}; }catch(e){}
    Object.keys(WINS).forEach(function(id){
      var w=WINS[id];
      if(w.kind==="widget"&&en[w.widget]===false)w.wb.close();
    });
  });

  /* ---- public API + boot ---- */
  window.GoseWM={ open:openApp, winify:winify, focus:function(id){run({verb:"focus",id:id});},
                  close:function(id){run({verb:"close",id:id});},
                  minimize:function(id){run({verb:"minimize",id:id});},
                  snap:function(id,zone){run({verb:"snap",id:id,zone:zone});},
                  suspend:suspendWin, free:freeWin, resummon:resummon, act:actOut,
                  carousel:openCarousel, overview:openOverview, snapmode:openSnap,
                  modal:function(){return modalUI();}, descriptors:function(){return DESC;},
                  list:list, run:run };
  addEventListener("gose-nav",syncSoon);           // GW focus moved -> mirror it promptly
  applyIntercepts(); winifyButtons(); dockRender();
  sync();                                          // first heartbeat (also drains any queue)
  longPoll();                                      // the ms-latency command path
})();
