/* GOSE — Sound Manager.
   ONE place that owns the system sound set (the owner's clips in assets/sounds/*.ogg +
   the UI tick .wav set). Exposes GOSESOUND.play(event) with:
     - per-category volume (0..100) + per-category mute
     - a global quiet-mode
     - all of the above persisted in localStorage (survives navigation/reboot)
     - auto-duck: suppresses sounds while a game is foreground, using the SAME
       game-gate the pad-nav uses (the /game/running signal the server derives from
       the NCI GET_STATUS that gose-pad-nav.py reads to fall silent under a game).
   cursor.js routes the legacy GOSE.sound(nav/select/back/launch) through this so
   there's a single control surface (Settings > Sound).                          */
(function(){
  if(window.GOSESOUND) return;
  window.GOSE = window.GOSE || {};

  // event -> category. Files are assets/sounds/<event>.<ext>; remaps below where the
  // audio file name differs from the event name.
  var EVENTS = {
    // system / power (the brand moments)
    boot:'system', login:'system', shutdown:'system', restart:'system', sleep:'system', wake:'system',
    welcome:'system',                 // OOBE finale -> reuses the boot flourish (no separate clip yet)
    // notifications
    notify:'notify', 'download-done':'notify', error:'notify', warning:'notify',
    // battery / power state
    charging:'battery', 'battery-low':'battery', 'battery-critical':'battery',
    // UI ticks (the existing .wav set)
    nav:'ui', select:'ui', back:'ui', launch:'ui',
    'step-done':'ui'                  // OOBE per-step advance -> reuses the login confirm clip
  };
  var FILE = { welcome:'boot', 'step-done':'login' };   // event -> actual file stem
  var WAV  = { nav:1, select:1, back:1, launch:1 };      // these are .wav; the rest are the owner's .ogg
  // The owner's clips ship as .ogg (Vorbis), NOT .mp3: this VM's WebKit2GTK build has NO
  // MP3 decoder (an <audio> .mp3 src errors MEDIA_ERR_SRC_NOT_SUPPORTED = silent). OGG/Vorbis
  // decodes here and is far smaller than WAV. Converted from the owner's .mp3 set. (docs/26)
  // important alerts still fire while a game is foreground (don't duck these)
  var BYPASS_DUCK = { 'battery-critical':1, 'battery-low':1, error:1, warning:1 };
  // per-category default volume 0..100: UI ticks quiet (they fire constantly), alerts loud.
  // Round to the Settings volume steps (25/50/75/100) so the picker matches the stored value.
  // 'music' = the looping menu soundtrack (assets/music.js): default ON but QUIET (~20%);
  // it has no one-shot events here (no EVENTS entry) — it's a continuous category the
  // player reads via vol('music')/muted('music')/quiet(). 20 rounds to the 25% picker step.
  var DEFV = { system:75, notify:75, battery:100, ui:50, music:20 };
  var CATS = ['system','notify','battery','ui','music'];

  function lsGet(k,d){ var v=localStorage.getItem(k); return v==null?d:v; }
  function vol(cat){ var v=parseInt(lsGet('gose-snd-vol-'+cat, DEFV[cat]),10);
    return isNaN(v)?DEFV[cat]:Math.max(0,Math.min(100,v)); }
  function setVol(cat,v){ localStorage.setItem('gose-snd-vol-'+cat, Math.max(0,Math.min(100,Math.round(v)))); }
  function muted(cat){ return lsGet('gose-snd-mute-'+cat,'0')==='1'; }
  function setMute(cat,on){ localStorage.setItem('gose-snd-mute-'+cat, on?'1':'0'); }
  function quiet(){ return lsGet('gose-snd-quiet','0')==='1'; }
  function setQuiet(on){ localStorage.setItem('gose-snd-quiet', on?'1':'0'); }
  // legacy single toggle (Settings > Sound > UI sounds) keeps muting the UI ticks
  function uiOff(){ return localStorage.getItem('gose-sounds')==='off'; }

  // ---- game-foreground gate (duck) — poll /game/running, cache ~1.5s ----
  var _game=false, _gameAt=0;
  function pollGame(){ _gameAt=Date.now();
    fetch('/game/running',{cache:'no-store'}).then(function(r){return r.json();})
      .then(function(d){ _game=!!(d&&d.running); }).catch(function(){}); }
  function gameForeground(){ if(Date.now()-_gameAt>1500) pollGame(); return _game; }
  try{ pollGame(); setInterval(pollGame, 2000); }catch(e){}

  // ---- #57 Sound Packs: a named sub-folder of assets/sounds/ (e.g. "default", "retro", "minimal").
  // The special name "default" (or empty) resolves to assets/sounds/ (the built-in owner clips).
  // A pack folder must have the same event filenames; missing files fall back to the default.
  // Pack name is persisted in localStorage gose-sound-pack and live-read each play().
  function currentPack(){
    var p=(localStorage.getItem('gose-sound-pack')||'').trim();
    return (p&&p!=='default')?p:'';
  }
  function packPath(ev){
    var pack=currentPack(), stem=FILE[ev]||ev, ext=WAV[ev]?'wav':'ogg';
    if(!pack) return 'assets/sounds/'+stem+'.'+ext;
    return 'assets/sounds/'+pack+'/'+stem+'.'+ext;
  }

  var cache={}, cacheKey={};  // keyed by "pack:event"
  function audio(ev){
    var key=(currentPack()||'default')+':'+ev;
    var a=cache[key];
    if(!a){
      var src=packPath(ev);
      a=new Audio(src); a.preload='auto';
      // fallback to default if the pack file fails to load
      if(currentPack()){
        var defSrc='assets/sounds/'+(FILE[ev]||ev)+'.'+(WAV[ev]?'wav':'ogg');
        a.addEventListener('error',function(){
          a.src=defSrc; try{a.load();}catch(e){}
        },{once:true});
      }
      cache[key]=a;
    }
    return a;
  }

  function play(ev, opts){ opts=opts||{};
    var cat=EVENTS[ev]; if(!cat) return false;
    if(quiet() && !opts.force) return false;                                   // global quiet mode
    if(muted(cat)) return false;                                               // per-category mute
    if(cat==='ui' && uiOff()) return false;                                    // legacy UI-sounds toggle
    if(gameForeground() && !BYPASS_DUCK[ev] && !opts.force) return false;      // duck under a game
    var v=vol(cat)/100; if(v<=0) return false;
    try{ var a=audio(ev); a.volume=Math.max(0,Math.min(1,v));
      a.currentTime=0; var p=a.play(); if(p&&p.catch)p.catch(function(){}); return true;
    }catch(e){ return false; }
  }

  // #57 setPack — switch the active sound pack and clear the audio cache so
  // the next play() loads from the new pack directory.
  function setPack(name){
    var n=(name||'').trim()||'default';
    localStorage.setItem('gose-sound-pack', n);
    cache={};   // clear cache so next play() reloads from new pack path
  }
  function getPack(){ return localStorage.getItem('gose-sound-pack')||'default'; }

  window.GOSESOUND = {
    play:play, cats:CATS, events:Object.keys(EVENTS),
    catOf:function(e){return EVENTS[e];},
    vol:vol, setVol:setVol, muted:muted, setMute:setMute,
    quiet:quiet, setQuiet:setQuiet,
    defaultVol:function(c){return DEFV[c];},
    isGame:function(){return _game;},
    // #57 sound packs
    setPack:setPack, getPack:getPack
  };

  // Menu music (looping shell soundtrack) lives in its own player module so this file
  // stays the one-shot SFX manager. Inject it once; it rides this loader onto every
  // shell page (cursor.js auto-loads sound.js everywhere), reuses the 'music' category
  // above for volume/mute + quiet-mode, and the SAME /game/running gate for its pause.
  try{ if(!window.GOSEMUSIC){ var _ms=document.createElement('script');
    _ms.src='assets/music.js'; (document.head||document.documentElement).appendChild(_ms); } }catch(e){}
})();
