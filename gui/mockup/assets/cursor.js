// GOSE shared cursor — a crisp, clearly-visible pointer for the dark UI.
// The VM's native X cursor is near-invisible; this renders our own and hides the native one.
(function(){
  if(window.__goseCursor) return; window.__goseCursor=true;
  function init(){
    const s=document.createElement('style');
    s.textContent='html,body,*{cursor:none!important}'+
      '#gose-cursor{position:fixed;left:0;top:0;width:24px;height:24px;z-index:2147483647;pointer-events:none;'+
      'will-change:transform;filter:drop-shadow(0 2px 3px rgba(0,0,0,.55))}'+
      '#gose-cursor svg{display:block;transition:transform .06s ease}'+
      '#gose-cursor.down svg{transform:scale(.8)}'+
      '#gose-cursor.text svg{display:none}#gose-cursor.text:after{content:"";display:block;width:2px;height:20px;'+
      'background:#5cd0ff;box-shadow:0 0 6px #5cd0ff;margin-left:6px}';
    document.head.appendChild(s);
    const c=document.createElement('div'); c.id='gose-cursor';
    c.innerHTML='<svg width="24" height="24" viewBox="0 0 24 24">'+
      '<path d="M3 2 L3 19 L7.6 14.6 L10.8 21 L13.7 19.6 L10.6 13.4 L17 13.4 Z" '+
      'fill="#f3f6ff" stroke="#5cd0ff" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    let x=innerWidth/2, y=innerHeight/2;
    function place(){ c.style.transform='translate('+x+'px,'+y+'px)'; }
    addEventListener('mousemove',function(e){
      x=e.clientX; y=e.clientY; place();
      // text-caret look over editable/text fields
      const t=e.target;
      const editable = t && (t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable);
      c.classList.toggle('text', !!editable);
    },{passive:true});
    addEventListener('mousedown',function(){c.classList.add('down');});
    addEventListener('mouseup',function(){c.classList.remove('down');});
    place();
  }
  if(document.body) init(); else addEventListener('DOMContentLoaded',init);
})();

// GOSE on-screen keyboard — auto-shows in any text field; K key or PS touchpad toggles it.
(function(){
  if(window.__goseOSK) return; window.__goseOSK=true;
  function init(){
    var st=document.createElement('style');
    st.textContent=
      '#osk-btn{position:fixed;right:16px;bottom:60px;z-index:2147483640;width:42px;height:42px;border-radius:12px;'+
      'background:#161826ee;border:1px solid #ffffff26;color:#cdd2ea;display:flex;align-items:center;justify-content:center;cursor:none;font-size:19px}'+
      '#osk-btn:hover{border-color:#5cd0ff}'+
      '#osk{position:fixed;left:50%;bottom:0;transform:translateX(-50%) translateY(110%);z-index:2147483641;'+
      'width:780px;max-width:98vw;background:#0c0c1ef7;border:1px solid #ffffff26;border-bottom:none;border-radius:16px 16px 0 0;'+
      'padding:14px;backdrop-filter:blur(14px);transition:transform .2s ease;box-shadow:0 -8px 40px #000a}'+
      '#osk.on{transform:translateX(-50%) translateY(0)}'+
      '#osk .row{display:flex;gap:7px;justify-content:center;margin:6px 0}'+
      '#osk .k{flex:1;max-width:62px;height:48px;border-radius:10px;background:#1a1d2b;border:1px solid #ffffff16;'+
      'color:#eaf0ff;display:flex;align-items:center;justify-content:center;font-size:17px;cursor:none;user-select:none}'+
      '#osk .k.wide{max-width:150px;flex:2}#osk .k.focus{border-color:#5cd0ff;box-shadow:0 0 0 2px #5cd0ff66;background:#22273c}';
    document.head.appendChild(st);
    var osk=document.createElement('div'); osk.id='osk'; document.body.appendChild(osk);
    var last=null;
    function isField(t){return t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable);}
    document.addEventListener('focusin',function(e){ if(isField(e.target)){ last=e.target; show(); }});   // auto-appears in any text field (terminal, URL bars, search)
    document.addEventListener('focusout',function(e){ if(isField(e.target)) setTimeout(function(){ if(!isField(document.activeElement)) hide(); }, 150); });
    var rows=['1234567890'.split(''),'qwertyuiop'.split(''),'asdfghjkl'.split(''),'zxcvbnm'.split('')];
    var shift=false, fr=0, fc=0, grid=[];
    function build(){ osk.innerHTML=''; grid=[];
      rows.forEach(function(r){ var rd=document.createElement('div'); rd.className='row'; var gr=[];
        r.forEach(function(ch){ var k=document.createElement('div'); k.className='k'; k.textContent=shift?ch.toUpperCase():ch; k.dataset.ch=ch; rd.appendChild(k); gr.push(k); });
        osk.appendChild(rd); grid.push(gr); });
      var fnr=document.createElement('div'); fnr.className='row'; var gr=[];
      [['tab','Tab'],['shift','⇧'],['space','Space'],['back','⌫'],['enter','Enter'],['hide','Hide ▾']].forEach(function(d){
        var k=document.createElement('div'); k.className='k'+((d[0]==='space')?' wide':''); k.textContent=d[1]; k.dataset.fn=d[0]; fnr.appendChild(k); gr.push(k); });
      osk.appendChild(fnr); grid.push(gr);
      grid.forEach(function(row,ri){ row.forEach(function(k,ci){ k.onclick=function(){fr=ri;fc=ci;press(k);}; k.onmouseenter=function(){fr=ri;fc=ci;mark();}; }); });
      mark();
    }
    function mark(){ grid.forEach(function(row){row.forEach(function(k){k.classList.remove('focus');});}); if(grid[fr]&&grid[fr][fc])grid[fr][fc].classList.add('focus'); }
    function insert(s){ if(!last)return; if(last.isContentEditable){document.execCommand('insertText',false,s);return;}
      var a=last.selectionStart, b=last.selectionEnd, v=last.value; last.value=v.slice(0,a)+s+v.slice(b); last.selectionStart=last.selectionEnd=a+s.length; last.dispatchEvent(new Event('input',{bubbles:true})); }
    function backspace(){ if(!last)return; var a=last.selectionStart, b=last.selectionEnd, v=last.value;
      if(a===b&&a>0){last.value=v.slice(0,a-1)+v.slice(b); last.selectionStart=last.selectionEnd=a-1;} else {last.value=v.slice(0,a)+v.slice(b); last.selectionStart=last.selectionEnd=a;} last.dispatchEvent(new Event('input',{bubbles:true})); }
    function press(k){ if(k.dataset.fn){ var fn=k.dataset.fn;
        if(fn==='shift'){shift=!shift;build();} else if(fn==='space')insert(' '); else if(fn==='back')backspace();
        else if(fn==='tab')tabKey(); else if(fn==='enter')commitField(); else if(fn==='hide')hide(); return; }
      insert(shift?k.dataset.ch.toUpperCase():k.dataset.ch); }
    // ---- Enter-chaining (docs/27 §3.6) ----
    // Enter COMMITS the field (the synthesized keydown lets single-field modals submit). On a
    // multi-field form it then auto-advances to the NEXT text input (DOM order = visual order on
    // GOSE pages) with the OSK kept open; on the LAST field it closes the OSK and hands focus to
    // the page's primary continue/submit control ([data-osk-primary], else a real submit button).
    // Events the OSK emits are marked __goseOSK so its own capture handler never re-handles them
    // (an unmarked synthetic Enter would re-trigger press() on the focused Enter key — recursion).
    function sendKey(field,key){ var ev=new KeyboardEvent('keydown',{key:key,bubbles:true,cancelable:true});
      ev.__goseOSK=true; field.dispatchEvent(ev); return ev; }
    var _NOTTEXT={hidden:1,checkbox:1,radio:1,button:1,submit:1,reset:1,range:1,file:1,color:1,image:1};
    function textTargets(){ return [].filter.call(document.querySelectorAll('input,textarea'),function(el){
        if(el.disabled||el.readOnly) return false;
        if(el.tagName==='INPUT'&&_NOTTEXT[(el.getAttribute('type')||'text').toLowerCase()]) return false;
        return el.offsetParent!==null; }); }
    function primaryControl(){ return document.querySelector('[data-osk-primary]')
      || document.querySelector('button[type="submit"],input[type="submit"]'); }
    function gone(el){ return !el||!document.contains(el)||el.offsetParent===null; }
    function commitField(){
      if(gone(last)){ hide(); return; }
      var fld=last, ev=sendKey(fld,'Enter');
      if(ev.defaultPrevented){ if(gone(fld)) hide(); return; }  // page consumed the commit -> it owns what happens next
      if(gone(fld)){ hide(); return; }          // the commit closed its surface -> the page owns focus
      var t=textTargets(), i=t.indexOf(fld);
      if(i>-1&&i<t.length-1){ var nx=t[i+1]; nx.focus(); last=nx;   // chain: next field, OSK stays
        try{ nx.selectionStart=nx.selectionEnd=nx.value.length; }catch(e){} return; }
      hide(); fld.blur();                       // last field -> primary continue/submit control
      var prim=primaryControl();
      fld.dispatchEvent(new CustomEvent('gose-osk-chain-end',{bubbles:true,detail:{primary:prim||null}}));
      if(prim&&(prim.tagName==='BUTTON'||prim.tagName==='INPUT'||prim.tagName==='A'||prim.hasAttribute('tabindex'))){
        try{ prim.focus(); }catch(e){} } }
    function tabKey(){ if(gone(last)) return;
      var ev=sendKey(last,'Tab'); if(ev.defaultPrevented) return;  // page consumed it (e.g. completion)
      var t=textTargets(), i=t.indexOf(last); if(t.length<2) return;
      var nx=t[(i+1+t.length)%t.length]; nx.focus(); last=nx;       // wraps; OSK stays open
      try{ nx.selectionStart=nx.selectionEnd=nx.value.length; }catch(e){} }
    function show(){osk.classList.add('on');mark();} function hide(){osk.classList.remove('on');}
    function toggle(){ if(osk.classList.contains('on'))hide(); else show(); }
    window.GOSE=window.GOSE||{}; window.GOSE.osk={show:show,hide:hide,toggle:toggle};
    document.addEventListener('keydown',function(e){
      if(e.__goseOSK) return;   // OSK-emitted commit/Tab events are for the page, not the OSK itself
      if((e.key==='k'||e.key==='K') && !isField(document.activeElement)){toggle();e.preventDefault();return;}  // K brings it up (when not typing)
      if(!osk.classList.contains('on'))return;
      var k=e.key;
      if(k==='Escape'){hide();}
      else if(k==='ArrowRight'){fc=Math.min(grid[fr].length-1,fc+1);mark();}
      else if(k==='ArrowLeft'){fc=Math.max(0,fc-1);mark();}
      else if(k==='ArrowDown'){fr=Math.min(grid.length-1,fr+1);fc=Math.min(grid[fr].length-1,fc);mark();}
      else if(k==='ArrowUp'){fr=Math.max(0,fr-1);fc=Math.min(grid[fr].length-1,fc);mark();}
      else if(k==='Enter'||k===' '){press(grid[fr][fc]);}
      else return;
      e.preventDefault(); e.stopPropagation();   // capture phase: page nav won't also fire
    }, true);
    // No raw-gamepad poll — the bridge (gose-pad-nav.py) synthesizes the arrow/Enter/Escape
    // keys the handler above consumes; a page-level getGamepads() loop is a second input
    // path that double-fires (docs/27). Pad paths in: focusin auto-show + K toggle.
    // ---- opt-in auto-open for roving-focus pages (docs/25 §3 OOBE) ----
    // Pages whose pad nav moves a roving .focus CLASS (not DOM focus) can set data-osk-auto on
    // <html>/<body>: when the roving focus lands on a text field, the OSK DOM-focuses it, which
    // fires the normal focusin auto-show — no manual summon. Opt-in only: pages without the
    // attribute keep their existing behaviour (field opens the OSK on activate/DOM focus).
    if(document.documentElement.hasAttribute('data-osk-auto')||
       (document.body&&document.body.hasAttribute('data-osk-auto'))){
      new MutationObserver(function(ms){ ms.forEach(function(m){ var el=m.target;
          if(el&&el.classList&&el.classList.contains('focus')&&isField(el)&&document.activeElement!==el) el.focus(); });
      }).observe(document.documentElement,{attributes:true,attributeFilter:['class'],subtree:true});
    }
    build();
    if(location.hash.indexOf('osk')>=0) setTimeout(show, 500);   // deep-link to preview the keyboard
  }
  if(document.body) init(); else addEventListener('DOMContentLoaded',init);
})();

// GOSE numpad-as-controller — works on EVERY screen, NumLock on or off (matches by e.code).
// Numpad keys are translated into the arrow/Enter/Escape events every GOSE page already understands.
(function(){
  if(window.__goseNumpad) return; window.__goseNumpad=true;
  function send(key){ document.dispatchEvent(new KeyboardEvent('keydown',{key:key,code:key,bubbles:true,cancelable:true})); }
  // Files treats the shoulders as copy/cut/paste/delete and B/0 as "back one folder"
  // (NOT exit — Esc/Home exit). Elsewhere the shoulders are widget nav and 0 = Escape/back.
  var onFiles = /gose-files/.test(location.pathname);
  document.addEventListener('keydown', function(e){
    // ONLY when NumLock is on (numpad sends digits). With NumLock off the numpad already
    // sends arrows/Enter natively — translating then would double-fire (skip every other).
    if(!/^[0-9]$/.test(e.key)) return;
    switch(e.code){
      case 'Numpad8': send('ArrowUp'); break;
      case 'Numpad2': send('ArrowDown'); break;
      case 'Numpad4': send('ArrowLeft'); break;
      case 'Numpad6': send('ArrowRight'); break;
      case 'Numpad7': send(onFiles ? 'GoseL1' : 'ArrowLeft'); break;    // L1
      case 'Numpad9': send(onFiles ? 'GoseR1' : 'ArrowRight'); break;   // R1
      case 'Numpad1': if(onFiles){send('GoseL2'); break;} return;       // L2 (reserved elsewhere)
      case 'Numpad3': if(onFiles){send('GoseR2'); break;} return;       // R2 (reserved elsewhere)
      case 'Numpad0': send(onFiles ? 'Backspace' : 'Escape'); break;    // B / back — Files: up a folder
      // Numpad5 (Home) is handled by the Guide overlay below: tap = overlay, 3s hold = desktop
      default: return;
    }
    e.preventDefault();
  }, true);
  // NumpadEnter always reports key="Enter" (NumLock-independent) → let the page handle it natively (no double)
})();

// GOSE Guide / Quick-Access overlay — the console "home button" panel. Lives on EVERY page
// (injected here like the OSK). Numpad5 / gamepad-guide: a TAP toggles it; a 3-second HOLD
// returns to the desktop. Volume/brightness/performance/power are live (server endpoints).
(function(){
  if(window.__goseGuide) return; window.__goseGuide=true;
  function init(){
    var st=document.createElement('style');
    st.textContent=
      '#gg-scrim{position:fixed;inset:0;z-index:2147483644;background:#0006;opacity:0;transition:opacity .18s;pointer-events:none}'+
      '#gg-scrim.on{opacity:1;pointer-events:auto}'+
      '#gg{position:fixed;top:0;right:0;height:100%;width:390px;max-width:92vw;z-index:2147483645;'+
      'background:#0b0d18f2;border-left:1px solid #ffffff22;box-shadow:-18px 0 60px #000b;backdrop-filter:blur(16px);'+
      'transform:translateX(102%);transition:transform .2s ease;display:flex;flex-direction:column;padding:18px 18px 14px;color:#eaf0ff;'+
      'font-family:Inter,system-ui,sans-serif}'+
      '#gg.on{transform:translateX(0)}'+
      '#gg .hd{display:flex;align-items:center;gap:10px;padding-bottom:12px;border-bottom:1px solid #ffffff16;margin-bottom:8px}'+
      '#gg .hd .t{font-weight:700;font-size:15px}#gg .hd .meta{margin-left:auto;text-align:right;font-size:12px;color:#aeb6d4;line-height:1.4}'+
      '#gg .items{flex:1;overflow:auto;display:flex;flex-direction:column;gap:7px;padding-top:4px}'+
      '#gg .it{background:#161a2b;border:2px solid transparent;border-radius:12px;padding:11px 13px;cursor:pointer}'+
      '#gg .it.focus{border-color:#5cd0ff;box-shadow:0 0 0 3px #5cd0ff33;background:#1d2238}'+
      '#gg .it .lab{display:flex;align-items:center;gap:9px;font-size:13.5px;font-weight:600}'+
      '#gg .it .lab .v{margin-left:auto;color:#9fb8e8;font-weight:700}'+
      '#gg .it .ic{width:17px;height:17px;color:#7cc4ff}'+
      '#gg .bar{height:8px;border-radius:5px;background:#ffffff1a;margin-top:9px;overflow:hidden}'+
      '#gg .bar>i{display:block;height:100%;background:linear-gradient(90deg,#5cd0ff,#6a4dff);width:0}'+
      '#gg .seg{display:flex;gap:6px;margin-top:9px}'+
      '#gg .seg>span{flex:1;text-align:center;font-size:11.5px;font-weight:700;padding:6px 0;border-radius:8px;background:#ffffff12;color:#aeb6d4}'+
      '#gg .seg>span.sel{background:#5cd0ff;color:#06121a}'+
      '#gg .row2{display:grid;grid-template-columns:1fr 1fr;gap:7px}'+
      '#gg .hint{padding-top:9px;color:#8a90a6;font-size:11px;text-align:center}'+
      '#gg .it.danger.focus{border-color:#ff8e8e;box-shadow:0 0 0 3px #ff8e8e33}';
    document.head.appendChild(st);
    var scrim=document.createElement('div'); scrim.id='gg-scrim'; document.body.appendChild(scrim);
    var gg=document.createElement('div'); gg.id='gg'; document.body.appendChild(gg);
    scrim.onclick=close;

    var open=false, foc=0, items=[], st_={vol:50,mute:false,bri:null,briHas:true,perf:'balanced',
                                          batt:null,charging:false,net:'—',powerMode:false};
    function api(path,body){ return fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)}:{cache:'no-store'}).then(function(r){return r.json();}).catch(function(){return {ok:false};}); }

    function build(){
      items=[
        {key:'vol',type:'slider'},
        {key:'bri',type:'slider'},
        {key:'perf',type:'seg'},
        {key:'wifi',type:'act',ic:'wifi',lab:'Wi-Fi',go:'gose-settings.html#net'},
        {key:'bt',type:'act',ic:'bluetooth',lab:'Bluetooth',sub:'Setup',go:'gose-bluetooth.html'},
        {key:'settings',type:'act',ic:'settings',lab:'Settings',go:'gose-settings.html'},
        {key:'power',type:'act',ic:'power',lab:'Power',sub:'›',power:true}];
      if(st_.powerMode){
        items.push({key:'p-sleep',type:'act',ic:'moon',lab:'Sleep',act:'sleep',indent:true});
        items.push({key:'p-restart',type:'act',ic:'rotate-ccw',lab:'Restart',act:'restart',indent:true});
        items.push({key:'p-shutdown',type:'act',ic:'power',lab:'Shut down',act:'shutdown',indent:true,danger:true});
      }
      items.push({key:'desktop',type:'act',ic:'layout-grid',lab:'Exit to Desktop',go:'gose-home.html',danger:true});
      if(foc>=items.length)foc=items.length-1;
      render();
    }
    function ico(n){return '<span class="ic" style="-webkit-mask:url(assets/icons/'+n+'.svg) center/contain no-repeat;mask:url(assets/icons/'+n+'.svg) center/contain no-repeat;background:currentColor;display:inline-block"></span>';}
    function render(){
      var now=new Date(); var hh=now.getHours(), mm=now.getMinutes();
      var ap=hh>=12?'PM':'AM'; var h12=((hh+11)%12)+1;
      var batt=st_.batt==null?'':(st_.batt+'%'+(st_.charging?' (charging)':''));
      gg.innerHTML='<div class="hd"><span class="t">Quick Access</span>'+
        '<div class="meta">'+h12+':'+(mm<10?'0':'')+mm+' '+ap+'<br>'+(st_.net||'—')+(batt?(' · '+batt):'')+'</div></div>'+
        '<div class="items" id="gg-items"></div>'+
        '<div class="hint"><b>↑↓</b> move · <b>←→</b> adjust · <b>A</b> select · <b>5</b>/<b>B</b> close · hold <b>5</b> → desktop</div>';
      var box=gg.querySelector('#gg-items');
      items.forEach(function(it,i){
        var el=document.createElement('div'); el.className='it'+(i===foc?' focus':'')+(it.danger?' danger':'');
        if(it.indent)el.style.marginLeft='14px';
        if(it.type==='slider'){
          var val = it.key==='vol'? st_.vol : st_.bri;
          var dis = it.key==='bri' && !st_.briHas;
          var label = it.key==='vol' ? (st_.mute?'Volume (muted)':'Volume') : 'Brightness';
          el.innerHTML='<div class="lab">'+ico(it.key==='vol'?'volume-2':'sun')+label+
            '<span class="v">'+(dis?'n/a':(val==null?'…':val+'%'))+'</span></div>'+
            '<div class="bar"><i style="width:'+(dis?0:(val||0))+'%"></i></div>';
        } else if(it.type==='seg'){
          var modes=[['battery','Battery'],['balanced','Balanced'],['performance','Performance']];
          el.innerHTML='<div class="lab">'+ico('cpu')+'Performance</div>'+
            '<div class="seg">'+modes.map(function(m){return '<span class="'+(st_.perf===m[0]?'sel':'')+'">'+m[1]+'</span>';}).join('')+'</div>';
        } else {   // act row
          var sub = it.key==='wifi' ? (st_.net||'—') : (it.sub||'');
          el.innerHTML='<div class="lab">'+ico(it.ic)+it.lab+(sub?'<span class="v">'+sub+'</span>':'')+'</div>';
        }
        el.onclick=function(){foc=i; render(); enter();};
        box.appendChild(el);
      });
    }
    function refresh(){
      api('/status.json').then(function(d){ if(d){ if(d.battery_pct!=null)st_.batt=d.battery_pct; st_.charging=!!d.charging;} render(); });
      api('/net.json').then(function(d){ if(d&&d.ok){ st_.net=d.connection||(d.online?'Online':'Offline'); } render(); });
      api('/sys/audio').then(function(d){ if(d&&d.ok){ st_.vol=d.volume==null?st_.vol:d.volume; st_.mute=!!d.mute; } render(); });
      api('/sys/brightness').then(function(d){ if(d){ st_.briHas=d.has!==false; if(d.ok)st_.bri=d.value; } render(); });
    }
    function setVol(v){ v=Math.max(0,Math.min(100,v)); st_.vol=v; render(); clearTimeout(setVol._t);
      setVol._t=setTimeout(function(){api('/sys/audio',{volume:v});},120); }
    function setBri(v){ if(!st_.briHas)return; v=Math.max(0,Math.min(100,v)); st_.bri=v; render(); clearTimeout(setBri._t);
      setBri._t=setTimeout(function(){api('/sys/brightness',{value:v});},120); }
    function setPerf(dir){ var m=['battery','balanced','performance']; var i=m.indexOf(st_.perf);
      i=Math.max(0,Math.min(2,i+dir)); st_.perf=m[i]; render(); api('/sys/perf',{mode:st_.perf}); }
    function doPower(a){ api('/sys/power',{action:a}); }
    function move(d){ foc=(foc+d+items.length)%items.length; render();
      var f=gg.querySelector('.it.focus'); if(f)f.scrollIntoView({block:'nearest'}); }
    function adjust(dir){ var it=items[foc]; if(!it)return;
      if(it.key==='vol')setVol(st_.vol+dir*5);
      else if(it.key==='bri')setBri((st_.bri||0)+dir*5);
      else if(it.type==='seg')setPerf(dir); }
    function enter(){ var it=items[foc]; if(!it)return;
      if(it.act){ doPower(it.act); close(); return; }
      if(it.power){ st_.powerMode=!st_.powerMode; build(); return; }
      if(it.go){ location.href=it.go; return; }
      if(it.key==='vol'){ st_.mute=!st_.mute; api('/sys/audio',{mute:st_.mute}); render(); return; } }
    function show(){ if(open)return; open=true; foc=0; st_.powerMode=false; build(); refresh();
      scrim.classList.add('on'); gg.classList.add('on'); }
    function close(){ if(!open)return; open=false; scrim.classList.remove('on'); gg.classList.remove('on'); }
    function toggle(){ open?close():show(); }
    window.GOSEGUIDE={show:show,close:close,toggle:toggle,isOpen:function(){return open;}};

    // keys while open (capture so the page underneath doesn't also act)
    document.addEventListener('keydown',function(e){
      if(!open)return; var k=e.key;
      if(k==='ArrowDown')move(1); else if(k==='ArrowUp')move(-1);
      else if(k==='ArrowRight')adjust(1); else if(k==='ArrowLeft')adjust(-1);
      else if(k==='Enter'||k===' ')enter();
      else if(k==='Escape'||k==='Backspace')close();
      else return; e.preventDefault(); e.stopPropagation();
    },true);

    // Numpad5 / Home overlay. GOSE screens: the kiosk grabs the keyboard, so cursor.js shows the
    // instant IN-PAGE overlay here (tap = toggle, 3s hold = desktop). Over a running game the kiosk
    // isn't focused → Openbox's global KP_5 binding fires guide_toggle.sh → the external overlay
    // window instead. The two are mutually exclusive by focus, so there's never a double.
    var fiveDown=false, holdT=null;
    document.addEventListener('keydown',function(e){
      if(e.code!=='Numpad5' && e.key!=='Home') return;
      e.preventDefault();
      if(fiveDown) return;                       // ignore auto-repeat
      fiveDown=true;
      holdT=setTimeout(function(){ holdT=null; fiveDown='done'; location.href='gose-home.html'; },3000);
    },true);
    document.addEventListener('keyup',function(e){
      if(e.code!=='Numpad5' && e.key!=='Home') return;
      e.preventDefault();
      if(holdT){ clearTimeout(holdT); holdT=null; if(fiveDown===true) fetch('/guide/toggle',{method:'POST'}).catch(function(){}); }
      fiveDown=false;
    },true);

    // No raw-gamepad poll — the bridge (gose-pad-nav.py) owns the Guide button (WM layer)
    // and synthesizes the arrow/Enter/Escape keys the handler above consumes; a page-level
    // getGamepads() loop is a second input path that double-fires (docs/27). The pad path
    // to this overlay is Numpad5/Home (above) + the bridge/Openbox guide_toggle route.
  }
  if(document.body) init(); else addEventListener('DOMContentLoaded',init);
})();

// Ensure system notifications + the download-queue watcher run on EVERY page (notify.js is
// only linked on home/widgets; inject it elsewhere so "install complete" fires from anywhere).
(function(){
  function start(){ try{ if(window.GOSE){ GOSE.seed&&GOSE.seed(); GOSE.watchQueue&&GOSE.watchQueue(); } }catch(e){} }
  if(window.GOSE && window.GOSE.watchQueue){ start(); return; }
  var s=document.createElement('script'); s.src='assets/notify.js'; s.onload=start; s.onerror=function(){};
  (document.head||document.documentElement).appendChild(s);
})();

// GOSE UI sounds — subtle nav/select/back blips on EVERY screen (the owner supplies the .wav set in
// assets/sounds/). Mute via Settings → Sound → UI sounds (localStorage gose-sounds='off').
(function(){
  if(window.__goseSound) return; window.__goseSound=true;
  window.GOSE=window.GOSE||{};
  // Bring up the full sound manager (per-category volume/mute + quiet-mode + game-duck).
  if(!window.GOSESOUND){ var ss=document.createElement('script'); ss.src='assets/sound.js';
    (document.head||document.documentElement).appendChild(ss); }
  var cache={};
  function enabled(){ return localStorage.getItem('gose-sounds')!=='off'; }
  GOSE.sound=function(name){
    // Route through the manager when present so the ui-category volume/mute, the
    // global quiet-mode and the game-duck all apply from one place.
    if(window.GOSESOUND && GOSESOUND.catOf(name)){ return GOSESOUND.play(name); }
    if(!enabled()) return;                                  // fallback (manager not loaded yet)
    try{
      var a=cache[name]; if(!a){ a=cache[name]=new Audio('assets/sounds/'+name+'.wav'); a.volume=0.5; }
      a.currentTime=0; var p=a.play(); if(p&&p.catch)p.catch(function(){});
    }catch(e){}
  };
  function isField(t){return t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable);}
  document.addEventListener('keydown',function(e){
    if(isField(document.activeElement)) return;
    var k=e.key;
    if(k==='ArrowUp'||k==='ArrowDown'||k==='ArrowLeft'||k==='ArrowRight') GOSE.sound('nav');
    else if(k==='Enter'||k===' ') GOSE.sound('select');
    else if(k==='Escape'||k==='Backspace') GOSE.sound('back');
  });
})();
