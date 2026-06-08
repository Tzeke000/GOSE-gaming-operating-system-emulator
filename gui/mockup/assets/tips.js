/* GOSE — first-session contextual tips (task 48).
   One-time, low-friction hints that teach the controller as you first hit each
   situation. Each tip fires ONCE (remembered in localStorage "gose-tips"), shows
   as a single accent toast, and is dismissed with A or fades after 8s. Never more
   than one on screen at a time; suppressed while a game owns the screen (the same
   /game/running gate sound.js / the pad bridge use) — except the game-bar tip,
   which is the one you want exactly when a game just started. Honours reduce-motion
   and a global off switch (localStorage "gose-tips-off").

   The tips and their triggers (docs/27 vocabulary, in plain words):
     windows  first time >=2 windows are open  -> "Guide switches windows"
     game     first game launch                -> "Guide opens the game bar"
     field    first text-field focus           -> "Enter chains to the next field"
     welcome  first desktop after first-boot   -> "Press Help to learn the controls"
   Wiring (the smallest possible hooks): gose-wm.js dispatches window "gose-windows"
   {open:N} on every window change; this file also polls /game/running and watches
   focusin, so nothing else has to change to feed it.                              */
(function(){
  if(window.__goseTips) return; window.__goseTips=true;
  window.GOSE = window.GOSE || {};
  var LS=localStorage, SEEN_KEY="gose-tips", OFF_KEY="gose-tips-off";

  var TIPS={
    windows:{ msg:"Two windows open. Tap Guide to switch between them." },
    game:{ msg:"In a game? Tap Guide to open the game bar.", bypassGame:true },
    field:{ msg:"Typing? Enter jumps to the next field — no mouse needed." },
    welcome:{ msg:"New here? Press Help any time to learn the controls.",
              action:{ label:"Open Help", go:"gose-help.html" } }
  };

  /* ---- once-only memory ---- */
  function seenMap(){ try{ return JSON.parse(LS.getItem(SEEN_KEY)||"{}")||{}; }catch(e){ return {}; } }
  function isSeen(k){ return !!seenMap()[k]; }
  function markSeen(k){ var m=seenMap(); m[k]=1; try{ LS.setItem(SEEN_KEY,JSON.stringify(m)); }catch(e){} }
  function resetSeen(){ try{ LS.removeItem(SEEN_KEY); }catch(e){} }
  function tipsOff(){ var v=LS.getItem(OFF_KEY); return v==="1"||v==="on"||v==="true"; }
  function reduceMotion(){
    if(window.GOSE&&GOSE.a11y&&GOSE.a11y.reduceMotion){ try{ return GOSE.a11y.reduceMotion(); }catch(e){} }
    return document.documentElement.getAttribute("data-motion")==="reduce";
  }

  /* ---- game-foreground gate (mirrors assets/sound.js): poll /game/running, cache
     ~1.5s; the rising edge (no game -> game) is also the "first game launch" trigger. ---- */
  var _game=false,_gameAt=0,_gameWas=false;
  function applyGame(running){
    _game=!!running;
    if(_game&&!_gameWas) show("game");          // first launch -> game-bar tip (bypasses the gate)
    _gameWas=_game;
  }
  function pollGame(){ _gameAt=Date.now();
    fetch("/game/running",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(d){ applyGame(d&&d.running); }).catch(function(){}); }
  function gameForeground(){ if(Date.now()-_gameAt>1500) pollGame(); return _game; }
  try{ pollGame(); setInterval(pollGame,2000); }catch(e){}

  /* ---- the toast: one slot, a small queue, accent-bordered (accent-aware via --accent) ---- */
  var st=document.createElement("style");
  st.textContent=
    "#gose-tip{position:fixed;left:50%;transform:translateX(-50%);bottom:86px;z-index:2147479500;"+
      "background:var(--surface2,#161a2e);border:1px solid var(--accent,#5cd0ff);border-radius:11px;"+
      "color:var(--text,#eaf0ff);font:500 13px Inter,system-ui,sans-serif;padding:11px 15px;"+
      "display:none;align-items:center;gap:12px;opacity:0;transition:opacity .2s;max-width:74vw;"+
      "box-shadow:0 10px 30px #0007}"+
    "#gose-tip.on{opacity:1}"+
    "#gose-tip .gt-dot{flex:none;width:9px;height:9px;border-radius:50%;background:var(--accent,#5cd0ff);"+
      "box-shadow:0 0 8px var(--accent,#5cd0ff)}"+
    "#gose-tip .gt-msg{line-height:1.35}"+
    "#gose-tip .gt-hint{flex:none;color:var(--muted,#9aa0c0);font-size:11px;font-weight:600;letter-spacing:.02em;"+
      "border-left:1px solid var(--line,#ffffff1c);padding-left:11px;margin-left:2px}"+
    "html[data-motion=\"reduce\"] #gose-tip{transition:none}";
  function mount(){ if(document.getElementById("gose-tip"))return;
    var el=document.createElement("div"); el.id="gose-tip";
    el.innerHTML='<span class="gt-dot"></span><span class="gt-msg"></span><span class="gt-hint"></span>';
    (document.body||document.documentElement).appendChild(el); }
  if(document.documentElement){ (document.head||document.documentElement).appendChild(st); }

  var queue=[], showing=null, hideT=null;
  function el(){ return document.getElementById("gose-tip"); }
  function renderToast(item){
    mount(); var e=el(); if(!e)return;
    var t=item.t;
    e.querySelector(".gt-msg").textContent=t.msg;
    e.querySelector(".gt-hint").textContent=t.action?("A — "+t.action.label):"A — got it";
    e.style.display="flex";
    // force reflow so the opacity transition runs from 0 (skipped under reduce-motion)
    void e.offsetWidth; e.classList.add("on");
    clearTimeout(hideT); hideT=setTimeout(function(){ dismiss(false); }, 8000);
  }
  function hideToast(){ var e=el(); if(!e)return; e.classList.remove("on");
    setTimeout(function(){ if(!showing&&e){ e.style.display="none"; } }, reduceMotion()?0:220); }
  function dismiss(activate){
    if(!showing)return;
    var item=showing; showing=null; clearTimeout(hideT);
    if(activate&&item.t.action&&item.t.action.go){ location.href=item.t.action.go; return; }
    hideToast();
    setTimeout(pump, reduceMotion()?0:240);
  }
  function pump(){ if(showing||!queue.length)return; showing=queue.shift(); renderToast(showing); }

  function show(key){
    if(tipsOff())return;
    var t=TIPS[key]; if(!t)return;
    if(isSeen(key))return;
    if(gameForeground()&&!t.bypassGame)return;      // a game owns the screen — hold non-game tips
    markSeen(key);                                   // once-only: claim it the moment we decide to show
    queue.push({key:key,t:t}); pump();
  }

  // A dismisses (or, for a tip with an action, activates it); capture phase so it
  // doesn't also drive whatever is focused underneath.
  document.addEventListener("keydown",function(e){
    if(!showing)return;
    if(e.key==="Enter"){ e.preventDefault(); e.stopPropagation(); dismiss(true); }
  },true);

  /* ---- triggers ---- */
  // >=2 windows: gose-wm.js dispatches this on every window change (its only hook).
  window.addEventListener("gose-windows",function(e){
    var n=(e&&e.detail&&e.detail.open)||0; if(n>=2) show("windows");
  });
  // first text-field focus (the OSK / Enter-chaining surface, docs/27 §3.6)
  var TEXT_T={text:1,search:1,email:1,url:1,tel:1,password:1,number:1,"":1};
  document.addEventListener("focusin",function(e){
    var t=e.target; if(!t)return;
    var tag=(t.tagName||"").toUpperCase();
    if(tag==="TEXTAREA"||(tag==="INPUT"&&TEXT_T[(t.type||"text").toLowerCase()]&&!t.readOnly&&!t.disabled))
      show("field");
  });

  // first desktop after first boot: tips.js only mounts on the desktop, and the
  // desktop only ever renders post-OOBE, so "first time the dock exists" == first
  // post-OOBE desktop. A short delay lets the desktop settle first.
  function welcome(){ if(document.getElementById("dock")) show("welcome"); }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",function(){ setTimeout(welcome,700); });
  else setTimeout(welcome,700);

  // small public surface (mirrors GOSE.notify): show() to fire a tip, reset() for a
  // "show tips again" settings affordance, dismiss() to clear the current toast (e.g.
  // on navigation), seen()/off() to read state. Not test-only.
  GOSE.tips={ show:show, reset:resetSeen, dismiss:function(){ dismiss(false); }, seen:seenMap, off:tipsOff };
})();
