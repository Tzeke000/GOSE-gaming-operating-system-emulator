/* GOSE — Menu Music (looping shell soundtrack).
   A quiet ambient loop that plays ONLY on the menu SHELL pages (home, library, store,
   apps, settings, files, gallery, task manager, …) — never during boot, OOBE, the BIOS
   setup, the lock screen, the in-game overlay, or while a game is foreground.

   ONE control surface: it reuses GOSESOUND's 'music' category (sound.js) for volume +
   mute and the global quiet-mode, and the SAME /game/running game-gate the SFX duck +
   gose-pad-nav use, so Settings -> Sound stays the single place sound is controlled.
   Default: ON at ~20% (DEFV.music in sound.js). The OFF path is honored live —
   quiet-mode, category mute, or category volume 0 all silence it within one poll.

   ENGINE = an <audio> element (verified on the WebKit2GTK kiosk). NOT Web Audio: this
   build's AudioContext.decodeAudioData HANGS (no success/error callback), so it is
   unusable here. The <audio> element also can't load a *large* file over the shell's
   range-less HTTP server (a 3 MB src errors MEDIA_ERR_SRC_NOT_SUPPORTED), so the track
   must stay small — the placeholder is mono 16 kHz (~1.1 MB) and loads fine; a real
   track should be a small WAV or an OGG (this WebKit has NO MP3 decoder). See docs/26.

   AUTOPLAY: the kiosk gates audio autostart — <audio>.play() rejects NotAllowedError
   until a user gesture (verified). The shell is controller/key-driven, so the FIRST
   d-pad/key/pointer input starts the music (handled here). For true play-on-boot the
   kiosk would set media-playback-requires-user-gesture=FALSE (see docs/26).

   CONTINUITY across the shell's full-page navigations (every page nav is a real reload):
   the playback position + a wall-clock timestamp are persisted to localStorage
   (gose-music-pos) on pagehide and every 1.5 s; the next page resumes at
   position + elapsed (mod loop length) — home->store->home never restarts from zero.

   PLACEHOLDER TRACK: assets/sounds/menu-music-placeholder.wav — 100% synthesized here
   (no licensing) — a seamless C–G–C ambient loop, onyx/warm GOSE identity. Zeke's real
   track replaces it (docs/asset-prompts/06-menu-music.txt): drop it in and point TRACK
   at it (or set localStorage gose-music-src).                                          */
(function(){
  if(window.GOSEMUSIC) return; window.GOSEMUSIC = {};   // run once per page
  var CAT = 'music';

  // Shell vs not-shell: deny boot/auth/setup/overlay; everything else that loads this is shell.
  var EXCLUDE = { 'gose-boot':1, 'boot':1, 'bootmenu':1, 'gose-oobe':1, 'gose-setup':1,
                  'gose-lock':1, 'login':1, 'input-select':1, 'gose-overlay':1 };
  function page(){ var p=(location.pathname||'').split('/').pop()||''; return p.replace(/\.html$/,''); }
  if(EXCLUDE[page()]) return;   // not a menu page -> stay silent here

  // Track: placeholder by default; a localStorage override lets Zeke / a future "music pack"
  // picker swap it without editing code. (When the real track lands, repoint TRACK or set the key.)
  var TRACK = localStorage.getItem('gose-music-src') || 'assets/sounds/menu-music-placeholder.wav';

  var POS_KEY = 'gose-music-pos';
  var POLL_MS = 700;   // game-gate + live-settings cadence. Snappier than the 2s SFX duck so a
                       // game launch pauses the music near-instantly; still only ~1.4 fetch/s.

  function gs(){ return window.GOSESOUND; }
  function quiet(){ var s=gs(); if(s&&s.quiet) return s.quiet(); return localStorage.getItem('gose-snd-quiet')==='1'; }
  function muted(){ var s=gs(); if(s&&s.muted) return s.muted(CAT); return localStorage.getItem('gose-snd-mute-'+CAT)==='1'; }
  function volPct(){ var s=gs(); if(s&&s.vol){ var v=s.vol(CAT); if(typeof v==='number'&&!isNaN(v)) return v; }
    var v=parseInt(localStorage.getItem('gose-snd-vol-'+CAT)||'20',10); return isNaN(v)?20:Math.max(0,Math.min(100,v)); }
  function wantOn(){ return !quiet() && !muted() && volPct()>0; }   // category-vol 0 / mute / quiet = OFF

  function log(){ try{ var a=['[GOSEMUSIC]'].concat([].slice.call(arguments)); console.log.apply(console,a); }catch(e){} }

  // ---- the audio element ----
  var au=null, ready=false, dur=0, firstStart=true, lastOn=null;
  function buildAudio(){
    au = new Audio(); au.loop = true; au.preload = 'auto'; au.volume = 0; au.src = TRACK;
    au.addEventListener('loadedmetadata', function(){ ready=true; dur=au.duration||0; apply(); });
    au.addEventListener('error', function(){ log('track load error', TRACK, au.error&&au.error.code); });
    au.load();
  }

  // ---- position persistence (continuity across full-page navs) ----
  function savePos(){ try{ if(au && dur>0)
    localStorage.setItem(POS_KEY, JSON.stringify({ p:au.currentTime, at:Date.now() })); }catch(e){} }
  function resumeOffset(){ try{ var raw=localStorage.getItem(POS_KEY); if(!raw) return 0;
      var o=JSON.parse(raw); var t=(o.p||0)+(Date.now()-(o.at||Date.now()))/1000;   // advance through the nav gap
      if(dur>0){ t=t%dur; if(t<0) t+=dur; } return t>=0?t:0; }catch(e){ return 0; } }

  // ---- game gate (reuse the /game/running signal the SFX duck + pad-nav use) ----
  var _game=false;
  function pollGame(){ fetch('/game/running',{cache:'no-store'}).then(function(r){return r.json();})
      .then(function(d){ var g=!!(d&&d.running), was=_game; _game=g; if(g!==was) log(g?'game foreground -> pause':'game gone -> resume'); apply(); })
      .catch(function(){}); }

  // ---- play / pause control ----
  var armed=false;
  function apply(){
    if(!au) return;
    var on = wantOn() && !_game;
    if(on){
      au.volume = volPct()/100;
      if(au.paused){
        if(firstStart){ firstStart=false; var off=resumeOffset();
          try{ if(off>0) au.currentTime=Math.min(off, Math.max(0,dur-0.05)); }catch(e){}
          log('start', TRACK.split('/').pop(), 'off='+off.toFixed(1), 'vol='+volPct()); }
        var p=au.play(); if(p&&p.catch) p.catch(function(){ armGesture(); });   // gesture-gated -> arm
      }
    } else if(!au.paused){ savePos(); au.pause(); }
    if(on!==lastOn){ if(lastOn!==null) log(on?'on':'off (quiet/mute/vol0/game)'); lastOn=on; }
  }

  // autoplay-policy fallback: (re)start playback on the first real user gesture
  var GEST=['keydown','pointerdown','mousedown','touchstart'];
  function armGesture(){ if(armed) return; armed=true;
    function go(){ armed=false; GEST.forEach(function(ev){ document.removeEventListener(ev,go,true); }); apply(); }
    GEST.forEach(function(ev){ document.addEventListener(ev,go,true); }); }

  function boot(){
    buildAudio();
    var tries=0;                                  // apply once metadata (duration) is known
    (function wait(){ if(ready||tries>50){ apply(); } else { tries++; setTimeout(wait,80); } })();
    pollGame(); setInterval(pollGame, POLL_MS);   // game-gate + live settings re-check
    setInterval(savePos, 1500);                   // periodic offset save (covers abrupt teardown)
    window.addEventListener('pagehide', savePos);
    window.addEventListener('beforeunload', savePos);
    document.addEventListener('visibilitychange', function(){ if(document.hidden) savePos(); apply(); });
    armGesture();   // first input will start playback if the autoplay policy blocked it
  }

  // debug / future Settings "music" preview surface
  window.GOSEMUSIC = {
    isOn:function(){ return !!(au && !au.paused); }, pos:function(){ return au?au.currentTime:0; },
    dur:function(){ return dur; }, track:function(){ return TRACK; },
    wantOn:wantOn, apply:apply, savePos:savePos
  };

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
