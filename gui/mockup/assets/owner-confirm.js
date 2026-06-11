/* GOSE owner physical-presence confirm - shared by the AI Hub pages.
   Replaces the dead device-PIN prompt for owner-gated AI actions. The owner proves presence by
   HOLDING the X / South button on the OS-admin controller; the server reads that hold straight
   from the pad and mints a short-lived confirm token. No PIN, no dev token, no typing.
   API:  GOSE_OWNER.ownerPost(path, body, summary)  -> Promise<serverJSON>
         GOSE_OWNER.confirm(summary)                -> Promise<token|null>
*/
(function(){
  "use strict";
  // Owner-elevation SESSION: one hold-✕ opens a ~5-min sliding window so routine admin doesn't
  // re-prompt (Mac/sudo/polkit model). Stored in sessionStorage so it survives a page navigation
  // (Hub -> detail, Settings -> a tool) but dies when the tab/kiosk closes. The SERVER also slides
  // its own copy of the token on each use, so client + server windows stay in sync. Fails safe:
  // if the session is missing/expired, ownerPost just re-runs the hold flow — never locks the owner out.
  var SKEY="gose_owner_session", SESSION_MS=300000;   // 5 min, matches server _CONFIRM_TOKEN_TTL
  function getSession(){ try{ var s=JSON.parse(sessionStorage.getItem(SKEY)||"null");
    return (s&&s.token&&Date.now()<s.exp)?s:null; }catch(e){ return null; } }
  function setSession(tok){ try{ sessionStorage.setItem(SKEY,JSON.stringify({token:tok,exp:Date.now()+SESSION_MS})); }catch(e){} }
  function slideSession(){ var s=getSession(); if(s) setSession(s.token); }   // refresh window on use
  function clearSession(){ try{ sessionStorage.removeItem(SKEY); }catch(e){} }
  var XGLYPH="✕";

  function post(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body||{})}).then(function(r){return r.json();}).catch(function(){return {ok:false};});}
  function getJSON(path){return fetch(path,{cache:"no-store"}).then(function(r){return r.json();}).catch(function(){return {ok:false};});}
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];});}

  function showOverlay(summary){
    hideOverlay();
    var o=document.createElement("div"); o.id="ocmask";
    o.style.cssText="position:fixed;inset:0;background:#06060ce8;display:flex;align-items:center;"+
      "justify-content:center;z-index:300;font-family:inherit";
    o.innerHTML='<div style="background:#11131b;border:1px solid #2a2e3c;border-radius:16px;'+
      'padding:26px 28px;width:380px;max-width:92vw;text-align:center;color:#e9edf6">'+
      '<h3 style="margin:0 0 8px;font-size:18px">Confirm it’s you</h3>'+
      '<div style="color:#9aa3b2;font-size:13px;line-height:1.5">'+(summary?esc(summary)+'<br>':'')+
      'Hold <b style="color:#5cd0ff">'+XGLYPH+'</b> on your controller to confirm.</div>'+
      '<div style="margin:18px auto 4px;width:100%;height:10px;background:#222634;border-radius:6px;overflow:hidden">'+
      '<div id="ocbar" style="height:100%;width:0%;background:#5cd0ff;transition:width .12s"></div></div>'+
      '<div id="octxt" style="color:#9aa3b2;font-size:12px;margin-top:10px">Waiting for your controller…</div>'+
      '<div style="color:#5a6072;font-size:11px;margin-top:12px">Press B / Esc to cancel</div></div>';
    document.body.appendChild(o);
  }
  function hideOverlay(){var o=document.getElementById("ocmask");if(o)o.remove();}
  function setBar(p){var b=document.getElementById("ocbar");if(b)b.style.width=Math.round((p||0)*100)+"%";}
  function setTxt(t){var e=document.getElementById("octxt");if(e)e.textContent=t;}

  // confirm(summary) -> Promise<token|null>. Shows the hold overlay; the SERVER read of the
  // OS-admin pad is the authority (this overlay is only UX). Esc cancels; Enter is swallowed so the
  // held X (which pad-nav also maps to Enter) cannot activate anything behind the modal.
  function confirm(summary){
    return post("/owner/confirm/begin",{summary:summary||""}).then(function(d){
      showOverlay(summary);
      if(!d||!d.ok){ setTxt(d&&d.error?d.error:"No controller detected"); setTimeout(hideOverlay,1900); return null; }
      var id=d.id, cancelled=false;
      function onKey(e){ if(e.key==="Escape")cancelled=true;
        if(e.key==="Enter"){e.preventDefault();e.stopPropagation();} }
      document.addEventListener("keydown",onKey,true);
      function cleanup(){ document.removeEventListener("keydown",onKey,true); hideOverlay(); }
      return new Promise(function(resolve){
        var deadline=Date.now()+28000;
        (function loop(){
          if(cancelled){ post("/owner/confirm/cancel",{id:id}); cleanup(); resolve(null); return; }
          if(Date.now()>deadline){ cleanup(); resolve(null); return; }
          getJSON("/owner/confirm/poll?id="+encodeURIComponent(id)).then(function(s){
            if(!s||!s.ok){ setTimeout(loop,300); return; }
            setBar(s.progress||0);
            if(s.state==="confirmed"){ setSession(s.confirm_token);
              setBar(1); setTxt("Confirmed ✓"); setTimeout(function(){cleanup();resolve(s.confirm_token);},350); return; }
            if(s.state==="timeout"||s.state==="cancelled"||s.state==="error"||s.state==="unknown"){
              setTxt(s.state==="timeout"?"Timed out — try again":"Cancelled");
              setTimeout(function(){cleanup();resolve(null);},900); return; }
            if((s.progress||0)>0) setTxt("Hold…");
            setTimeout(loop,250);
          });
        })();
      });
    });
  }

  // ownerPost(path, body, summary) -> Promise<json>. Injects a cached confirm token; on
  // ERR_NOT_OWNER runs the controller confirm and retries the call exactly once.
  function ownerPost(path,body,summary){
    function attempt(){ var b={}; for(var k in body)b[k]=body[k];
      var s=getSession(); if(s)b.confirm_token=s.token; return post(path,b); }
    return attempt().then(function(d){
      if(d&&d.code==="ERR_NOT_OWNER"){ clearSession();
        return confirm(summary).then(function(tok){ if(!tok)return d;
          return attempt().then(function(d2){ if(d2&&d2.code==="ERR_NOT_OWNER")clearSession(); else slideSession(); return d2; }); }); }
      if(d&&d.ok)slideSession();   // a served request keeps the elevation window alive
      return d;
    });
  }

  // True if an elevation session is currently live (for pages that want to show an "elevated" hint).
  function isElevated(){ return !!getSession(); }
  window.GOSE_OWNER={confirm:confirm, ownerPost:ownerPost, isElevated:isElevated, clear:clearSession};
})();
