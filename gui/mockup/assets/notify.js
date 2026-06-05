// GOSE system notifications — shared across the OS via localStorage.
// Any page: GOSE.notify({title, body, icon}). The desktop shows a bell + center.
(function(){
  window.GOSE = window.GOSE || {};
  const KEY = "gose-notifs";
  function load(){ try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch(e){ return []; } }
  function save(a){ localStorage.setItem(KEY, JSON.stringify(a.slice(0, 50))); }
  GOSE.notify = function(n){
    const a = load();
    a.unshift({ title: n.title || "GOSE", body: n.body || "", icon: n.icon || "sparkles",
                read: false, id: Math.random().toString(36).slice(2), t: (n.t || "now") });
    save(a);
    try { window.dispatchEvent(new CustomEvent("gose-notify", { detail: n })); } catch(e){}
    return a;
  };
  GOSE.notifs = load;
  GOSE.unread = function(){ return load().filter(x => !x.read).length; };
  GOSE.markAllRead = function(){ const a = load(); a.forEach(x => x.read = true); save(a); };
  GOSE.clearNotifs = function(){ save([]); };
  // seed a welcome + the Steam milestone once, so the center isn't empty on first run
  GOSE.seed = function(){
    if (localStorage.getItem("gose-notifs-seeded")) return;
    localStorage.setItem("gose-notifs-seeded", "1");
    GOSE.notify({ title: "Steam installed", body: "Steam is ready in GOSE.", icon: "download" });
    GOSE.notify({ title: "Welcome to GOSE", body: "Press the Core button for Apps. Everything's live.", icon: "sparkles" });
  };
  // watch the download queue and fire a real notification when an install finishes/fails.
  // Runs everywhere (started from cursor.js); de-duped across page loads via localStorage.
  function pretty(id){ id = id || ""; var seg = id.split(".").pop(); return seg || id; }
  GOSE.watchQueue = function(){
    if (GOSE._qwatch) return; GOSE._qwatch = 1;
    var SEEN = "gose-q-seen";
    function seen(){ try { return JSON.parse(localStorage.getItem(SEEN) || '{"done":[],"failed":[]}'); }
                     catch(e){ return { done: [], failed: [] }; } }
    function tick(){
      fetch("/queue.json", { cache: "no-store" }).then(function(r){ return r.json(); }).then(function(q){
        if (!q || !q.ok) return;
        var s = seen(), first = !localStorage.getItem(SEEN), changed = false;
        (q.done || []).forEach(function(id){ if (s.done.indexOf(id) < 0){ s.done.push(id); changed = true;
          if (!first) GOSE.notify({ title: "Install complete", body: pretty(id) + " is ready to launch.", icon: "download" }); }});
        (q.failed || []).forEach(function(id){ if (s.failed.indexOf(id) < 0){ s.failed.push(id); changed = true;
          if (!first) GOSE.notify({ title: "Install failed", body: pretty(id) + " couldn't install — tap to retry.", icon: "triangle-alert" }); }});
        if (changed || first) localStorage.setItem(SEEN, JSON.stringify({ done: s.done.slice(-60), failed: s.failed.slice(-60) }));
      }).catch(function(){});
    }
    tick(); setInterval(tick, 5000);
  };
})();
