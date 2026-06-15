/* GOSE i18n — shell string table + runtime.
   Two locales ship: "en" (English US, full) + "es" (Spanish, scaffold — proves the
   pipeline end-to-end; strings marked null fall back to en). More locales can be added
   by extending LOCALES below.

   API (available as GOSE.i18n after load):
     GOSE.t(key)              — translate key in the current locale (falls back to en)
     GOSE.i18n.locale()       — current locale code ("en" / "es" / …)
     GOSE.i18n.setLocale(lc)  — switch locale, write to localStorage, update html[lang],
                                 and re-apply the current page's [data-i18n] elements
     GOSE.i18n.applyPage()    — walk every [data-i18n] element and set its textContent
     GOSE.i18n.LOCALES        — the full table (read-only reference)

   Markup convention:
     <span data-i18n="home.nav.library">Library</span>
   The element's initial textContent is the en fallback rendered at build time; i18n.js
   overwrites it at runtime when the locale is non-en.  This means pages are still
   perfectly readable with JS disabled or before i18n.js loads.
*/
(function () {
  /* ===================================================================
     STRING TABLE
     Keys are dot-separated namespaces: <page|shared>.<section>.<name>
     null means "use the en value" — intentional scaffold sentinel.
     =================================================================== */
  var LOCALES = {
    en: {
      /* ---------- shared chrome ---------- */
      "app.name":          "GOSE",
      "app.tagline":       "Game Operating System Emulator",
      "app.back":          "B / Esc — back",
      "app.close":         "Esc — desktop",
      "app.online":        "Connected",
      "app.offline":       "No internet",
      "app.charging":      "Charging",
      "app.on_battery":    "On battery",
      "app.no_battery":    "No battery",
      "app.optimal":       "Optimal",
      "app.busy":          "Busy",
      "app.offline_badge": "offline",
      "app.live_badge":    "live",
      "app.ac_badge":      "AC",
      "app.low_badge":     "low",

      /* ---------- nav sidebar ---------- */
      "nav.library":       "Library",
      "nav.files":         "Files",
      "nav.terminal":      "Terminal",
      "nav.emulators":     "Emulators",
      "nav.ai_players":    "AI Players",
      "nav.settings":      "Settings",
      "nav.help":          "Help",

      /* ---------- dock ---------- */
      "dock.apps":         "Apps",
      "dock.store":        "Store",
      "dock.taskman":      "Task Manager",
      "dock.widgets":      "Widgets",
      "dock.lock":         "Lock",

      /* ---------- home page ---------- */
      "home.notifications":       "Notifications",
      "home.clear_all":           "Clear all",
      "home.no_notifications":    "No notifications",
      "home.dnd_game":            "Do Not Disturb — a game is running",
      "home.switch_profile":      "Switch profile",
      "home.recent_games":        "RECENT GAMES",
      "home.recent_apps":         "RECENT APPS",
      "home.all_apps":            "All Apps",
      "home.all_games":           "All Games",
      "home.gallery":             "Gallery",
      "home.search_apps":         "Search apps & games…",
      "home.no_games_yet":        "No games yet — open Library to add ROMs.",
      "home.no_emu_history":      "No emulator history yet — play a game to populate this.",
      "home.no_games_played":     "No games played yet. Open the Library to add ROMs.",
      "home.store_unavailable":   "Store unavailable.",
      "home.no_controllers":      "No controllers connected.",
      "home.open_taskman":        "A — open Task Manager ›",
      "home.open_notifications":  "Open notifications",
      "home.host_unreachable":    "Host telemetry unreachable.",
      "home.open_store":          "Open Store",
      "home.get_steam_store":     "Get Steam from the Store",
      "home.open_steam":          "Open Steam",
      "home.open_terminal":       "Open Terminal",
      "home.suspend":             "Suspend",
      "home.suspend_sub":         "Sleep (resume on input)",
      "home.restart":             "Restart",
      "home.restart_sub":         "Reboot the system",
      "home.shut_down":           "Shut Down",
      "home.shut_down_sub":       "Power off",
      "home.no_battery_row":      "Plugged-in / desktop",
      "home.most_played":         "MOST PLAYED",
      "home.recent":              "RECENT",
      "home.apps_section":        "APPS",
      "home.emulators_section":   "EMULATORS",
      "home.games_section":       "GAMES",
      "home.signed_in":           "SIGNED IN",
      "home.steam_running_nosign":"Steam is running — not signed in",
      "home.steam_not_installed": "Steam isn’t installed",
      "home.steam_not_running":   "Steam — not running / not signed in",
      "home.launch_steam":        "Launch Steam",
      "home.steam_open_lib":      "Steam is running — open it for your library.",

      /* ---------- low-battery notifications ---------- */
      "notif.low_battery":        "Low battery",
      "notif.low_battery_body":   "% remaining — plug in soon.",
      "notif.critical_suffix":    " Critical.",
      "notif.ai_access":          "AI access",
      "notif.revoked_suffix":     " revoked → Observe only",
      "notif.power":              "Power",

      /* ---------- library page ---------- */
      "library.title":            "Library",
      "library.search_ph":        "Search games…",
      "library.all_systems":      "All Systems",
      "library.collections":      "COLLECTIONS",
      "library.favorites":        "Favorites",
      "library.recent":           "Recent",
      "library.sort_az":          "A – Z",
      "library.sort_za":          "Z – A",
      "library.sort_recent":      "Recently played",
      "library.sort_playtime":    "Most played",
      "library.no_games":         "No games found.",
      "library.add_roms":         "Add ROMs via the Store, SMB share, or USB drive.",
      "library.import":           "Import…",
      "library.launch":           "Launch",
      "library.achievements":     "Achievements",
      "library.playtime":         "Playtime",
      "library.game_info":        "Game info",
      "library.b_home":           "B — Home",
      "library.filter_all":       "All",
      "library.cheevos":          "Achievements",
      "library.netplay":          "Netplay",
      "library.back":             "Back",

      /* ---------- apps page ---------- */
      "apps.title":               "Apps",
      "apps.search_ph":           "Search apps…",
      "apps.all_apps":            "ALL APPS",
      "apps.sleep":               "Sleep",
      "apps.restart":             "Restart",
      "apps.shut_down":           "Shut down",
      "apps.footer":              "↑↓←→ move · Enter open · type to search · Esc desktop",

      /* ---------- apps — app names & descriptions ---------- */
      "apps.app.library.nm":      "Library",
      "apps.app.library.ds":      "Your games, by system",
      "apps.app.files.nm":        "Files",
      "apps.app.files.ds":        "Browse, open & edit files + storage map",
      "apps.app.taskman.nm":      "Task Manager",
      "apps.app.taskman.ds":      "Processes, performance, kill tasks",
      "apps.app.peripherals.nm":  "Peripherals",
      "apps.app.peripherals.ds":  "Controllers, USB, Wi-Fi & Bluetooth",
      "apps.app.settings.nm":     "Settings",
      "apps.app.settings.ds":     "Personalize the whole OS",
      "apps.app.gallery.nm":      "Gallery",
      "apps.app.gallery.ds":      "Screenshots & game clips",
      "apps.app.saves.nm":        "Save Data",
      "apps.app.saves.ds":        "Back up, export & sync your game saves",
      "apps.app.splice.nm":       "Splice",
      "apps.app.splice.ds":       "Lossless video trim",
      "apps.app.browser.nm":      "Browser",
      "apps.app.browser.ds":      "Couch-friendly web browser — pad + OSK",
      "apps.app.moonlight.nm":    "Moonlight",
      "apps.app.moonlight.ds":    "Stream games from your PC (Sunshine / GeForce)",
      "apps.app.store.nm":        "Store",
      "apps.app.store.ds":        "Get more apps from Flathub",
      "apps.app.terminal.nm":     "Terminal",
      "apps.app.terminal.ds":     "Command line — the real shell",
      "apps.app.licenses.nm":     "Licenses",
      "apps.app.licenses.ds":     "Open-source credits & licenses",
      "apps.app.ai.nm":           "AI Players",
      "apps.app.ai.ds":           "Pair an AI as a player or co-pilot",
      "apps.app.beam.nm":         "Beam",
      "apps.app.beam.ds":         "AirDrop-style file transfer (Odin 2 era)",
      "apps.app.steam.nm":        "Steam",
      "apps.app.steam.ds":        "Install & launch your Steam library",
      "apps.app.emulators.nm":    "Emulators",
      "apps.app.emulators.ds":    "Cores & systems — license-aware store",

      /* ---------- lock page ---------- */
      "lock.hint_any":            "Press any button to unlock",
      "lock.hint_pin":            "Enter your PIN",
      "lock.hint_password":       "Enter your password",
      "lock.error_wrong":         "Wrong PIN — try again",
      "lock.error_wrong_password": "Wrong password — try again",
      "lock.error_locked":        "Too many attempts — wait 30 s",
      "lock.backspace":           "Back",
      "lock.clear":               "Clear",

      /* ---------- OOBE ---------- */
      "oobe.brand":               "GOSE",
      "oobe.welcome_eyebrow":     "STEP 1",
      "oobe.welcome_title":       "Welcome to GOSE",
      "oobe.welcome_sub":         "Your gaming operating system. Let’s set it up.",
      "oobe.next":                "Next",
      "oobe.skip":                "Skip",
      "oobe.done":                "Done",
      "oobe.continue":            "Continue",
      "oobe.back":                "Back",
      "oobe.req_badge":           "REQUIRED",
      "oobe.opt_badge":           "OPTIONAL",
      "oobe.rec_badge":           "RECOMMENDED",
      "oobe.def_badge":           "DEFAULT",
      "oobe.timezone_step":       "STEP 2",
      "oobe.timezone_title":      "Time zone",
      "oobe.timezone_sub":        "Pick your location so the clock is right.",
      "oobe.controller_step":     "STEP 3",
      "oobe.controller_title":    "Controller",
      "oobe.controller_sub":      "Plug in your controller now. GOSE detects it automatically.",
      "oobe.account_step":        "STEP 4",
      "oobe.account_title":       "Your account",
      "oobe.account_sub":         "Create a profile for this device.",
      "oobe.username_label":      "Username",
      "oobe.display_label":       "Display name",
      "oobe.pin_label":           "PIN (8 digits)",
      "oobe.pin_confirm_label":   "Confirm PIN",
      "oobe.personalize_step":    "STEP 5",
      "oobe.personalize_title":   "Make it yours",
      "oobe.personalize_sub":     "Choose a color theme.",
      "oobe.ai_step":             "STEP 6",
      "oobe.ai_title":            "AI players",
      "oobe.ai_sub":              "Pair an AI to play with you or drive the OS.",
      "oobe.ai_observe":          "Observe",
      "oobe.ai_play":             "Play",
      "oobe.ai_admin":            "Admin",
      "oobe.ai_add_another":      "Add another AI",
      "oobe.done_step":           "ALL SET",
      "oobe.done_title":          "You’re ready.",
      "oobe.done_sub":            "GOSE is set up. Jump in.",

      /* ---------- settings page ---------- */
      "settings.title":           "Settings",
      "settings.tagline":         "GOSE · personalize, sound, network, time, power, privacy, accounts & more",
      "settings.footer_nav":      "↑↓ move · ←→ change value · L1/R1 section · Esc back",

      /* ---------- time & language section ---------- */
      "settings.lang.nm":         "Language",
      "settings.lang.sub":        "On-screen language for the GOSE shell",
      "settings.lang.en":         "English (US)",
      "settings.lang.es":         "Español (Spanish)",
      "settings.lang.toast":      "Language: ",
      "settings.lang.toast_reload": " — reload pages to see it everywhere",
    },

    /* ---------------------------------------------------------------
       SPANISH SCAFFOLD
       Every key is present. null = fall back to English.
       Full translations land as native speakers contribute them.
       --------------------------------------------------------------- */
    es: {
      "app.name":          "GOSE",
      "app.tagline":       "Emulador de Sistema Operativo de Juegos",
      "app.back":          "B / Esc — atrás",
      "app.close":         "Esc — escritorio",
      "app.online":        "Conectado",
      "app.offline":       "Sin internet",
      "app.charging":      "Cargando",
      "app.on_battery":    "En batería",
      "app.no_battery":    "Sin batería",
      "app.optimal":       "Óptimo",
      "app.busy":          "Ocupado",
      "app.offline_badge": "sin conexión",
      "app.live_badge":    "en vivo",
      "app.ac_badge":      "CA",
      "app.low_badge":     "baja",

      "nav.library":       "Biblioteca",
      "nav.files":         "Archivos",
      "nav.terminal":      "Terminal",
      "nav.emulators":     "Emuladores",
      "nav.ai_players":    "Jugadores IA",
      "nav.settings":      "Ajustes",
      "nav.help":          "Ayuda",

      "dock.apps":         "Apps",
      "dock.store":        "Tienda",
      "dock.taskman":      "Administrador de tareas",
      "dock.widgets":      "Widgets",
      "dock.lock":         "Bloquear",

      "home.notifications":       "Notificaciones",
      "home.clear_all":           "Borrar todo",
      "home.no_notifications":    "Sin notificaciones",
      "home.dnd_game":            "No molestar — hay un juego en marcha",
      "home.switch_profile":      "Cambiar perfil",
      "home.recent_games":        "JUEGOS RECIENTES",
      "home.recent_apps":         "APPS RECIENTES",
      "home.all_apps":            "Todas las apps",
      "home.all_games":           "Todos los juegos",
      "home.gallery":             "Galería",
      "home.search_apps":         "Buscar apps y juegos…",
      "home.no_games_yet":        "Sin juegos aún — abre la Biblioteca para agregar ROMs.",
      "home.no_emu_history":      "Sin historial de emuladores — juega algo para rellenarlo.",
      "home.no_games_played":     "Sin juegos jugados aún. Abre la Biblioteca para agregar ROMs.",
      "home.store_unavailable":   "Tienda no disponible.",
      "home.no_controllers":      "Sin mandos conectados.",
      "home.open_taskman":        "A — abrir Administrador de tareas ›",
      "home.open_notifications":  "Abrir notificaciones",
      "home.host_unreachable":    "Telemetría del host no disponible.",
      "home.open_store":          "Abrir Tienda",
      "home.get_steam_store":     "Obtener Steam desde la Tienda",
      "home.open_steam":          "Abrir Steam",
      "home.open_terminal":       "Abrir Terminal",
      "home.suspend":             "Suspender",
      "home.suspend_sub":         "Dormir (reanuda al pulsar)",
      "home.restart":             "Reiniciar",
      "home.restart_sub":         "Reiniciar el sistema",
      "home.shut_down":           "Apagar",
      "home.shut_down_sub":       "Cortar la alimentación",
      "home.no_battery_row":      "Con cable / sobremesa",
      "home.most_played":         "MÁS JUGADO",
      "home.recent":              "RECIENTE",
      "home.apps_section":        "APPS",
      "home.emulators_section":   "EMULADORES",
      "home.games_section":       "JUEGOS",
      "home.signed_in":           "SESIÓN INICIADA",
      "home.steam_running_nosign":"Steam en marcha — sin sesión",
      "home.steam_not_installed": "Steam no está instalado",
      "home.steam_not_running":   "Steam — no en marcha / sin sesión",
      "home.launch_steam":        "Iniciar Steam",
      "home.steam_open_lib":      "Steam en marcha — ábrelo para ver tu biblioteca.",

      "notif.low_battery":        "Batería baja",
      "notif.low_battery_body":   "% restante — conecta el cargador pronto.",
      "notif.critical_suffix":    " Crítico.",
      "notif.ai_access":          "Acceso IA",
      "notif.revoked_suffix":     " revocado → Solo observar",
      "notif.power":              "Energía",

      "library.title":            "Biblioteca",
      "library.search_ph":        "Buscar juegos…",
      "library.all_systems":      "Todos los sistemas",
      "library.collections":      "COLECCIONES",
      "library.favorites":        "Favoritos",
      "library.recent":           "Recientes",
      "library.sort_az":          "A – Z",
      "library.sort_za":          "Z – A",
      "library.sort_recent":      "Jugado recientemente",
      "library.sort_playtime":    "Más jugado",
      "library.no_games":         "No se encontraron juegos.",
      "library.add_roms":         "Agrega ROMs desde la Tienda, recurso compartido SMB o USB.",
      "library.import":           "Importar…",
      "library.launch":           "Iniciar",
      "library.achievements":     "Logros",
      "library.playtime":         "Tiempo jugado",
      "library.game_info":        "Info del juego",
      "library.b_home":           "B — Inicio",
      "library.filter_all":       "Todos",
      "library.cheevos":          "Logros",
      "library.netplay":          "Netplay",
      "library.back":             "Atrás",

      "apps.title":               "Apps",
      "apps.search_ph":           "Buscar apps…",
      "apps.all_apps":            "TODAS LAS APPS",
      "apps.sleep":               "Suspender",
      "apps.restart":             "Reiniciar",
      "apps.shut_down":           "Apagar",
      "apps.footer":              "↑↓←→ mover · Enter abrir · escribe para buscar · Esc escritorio",

      "apps.app.library.nm":      "Biblioteca",
      "apps.app.library.ds":      "Tus juegos, por sistema",
      "apps.app.files.nm":        "Archivos",
      "apps.app.files.ds":        "Explora, abre y edita archivos + mapa de almacenamiento",
      "apps.app.taskman.nm":      "Administrador de tareas",
      "apps.app.taskman.ds":      "Procesos, rendimiento, terminar tareas",
      "apps.app.peripherals.nm":  "Periféricos",
      "apps.app.peripherals.ds":  "Mandos, USB, Wi-Fi y Bluetooth",
      "apps.app.settings.nm":     "Ajustes",
      "apps.app.settings.ds":     "Personaliza todo el sistema",
      "apps.app.gallery.nm":      "Galería",
      "apps.app.gallery.ds":      "Capturas y clips de juegos",
      "apps.app.saves.nm":        "Datos guardados",
      "apps.app.saves.ds":        "Haz copias de seguridad, exporta y sincroniza guardados",
      "apps.app.splice.nm":       "Splice",
      "apps.app.splice.ds":       "Recorte de vídeo sin pérdida",
      "apps.app.browser.nm":      "Navegador",
      "apps.app.browser.ds":      "Navegador web para sofá — mando + teclado",
      "apps.app.moonlight.nm":    "Moonlight",
      "apps.app.moonlight.ds":    "Transmite juegos desde tu PC (Sunshine / GeForce)",
      "apps.app.store.nm":        "Tienda",
      "apps.app.store.ds":        "Obtén más apps de Flathub",
      "apps.app.terminal.nm":     "Terminal",
      "apps.app.terminal.ds":     "Línea de comandos — el shell real",
      "apps.app.licenses.nm":     "Licencias",
      "apps.app.licenses.ds":     "Créditos y licencias de código abierto",
      "apps.app.ai.nm":           "Jugadores IA",
      "apps.app.ai.ds":           "Empareja una IA como jugador o copiloto",
      "apps.app.beam.nm":         "Beam",
      "apps.app.beam.ds":         "Transferencia de archivos estilo AirDrop (era Odin 2)",
      "apps.app.steam.nm":        "Steam",
      "apps.app.steam.ds":        "Instala y lanza tu biblioteca de Steam",
      "apps.app.emulators.nm":    "Emuladores",
      "apps.app.emulators.ds":    "Núcleos y sistemas — tienda con licencias",

      "lock.hint_any":            "Pulsa cualquier botón para desbloquear",
      "lock.hint_pin":            "Introduce tu PIN",
      "lock.hint_password":       "Introduce tu contraseña",
      "lock.error_wrong":         "PIN incorrecto — inténtalo de nuevo",
      "lock.error_wrong_password": "Contraseña incorrecta — inténtalo de nuevo",
      "lock.error_locked":        "Demasiados intentos — espera 30 s",
      "lock.backspace":           "Borrar",
      "lock.clear":               "Limpiar",

      "oobe.brand":               "GOSE",
      "oobe.welcome_eyebrow":     "PASO 1",
      "oobe.welcome_title":       "Bienvenido a GOSE",
      "oobe.welcome_sub":         "Tu sistema operativo de juegos. Vamos a configurarlo.",
      "oobe.next":                "Siguiente",
      "oobe.skip":                "Omitir",
      "oobe.done":                "Listo",
      "oobe.continue":            "Continuar",
      "oobe.back":                "Atrás",
      "oobe.req_badge":           "REQUERIDO",
      "oobe.opt_badge":           "OPCIONAL",
      "oobe.rec_badge":           "RECOMENDADO",
      "oobe.def_badge":           "PREDETERMINADO",
      "oobe.timezone_step":       "PASO 2",
      "oobe.timezone_title":      "Zona horaria",
      "oobe.timezone_sub":        "Elige tu ubicación para que el reloj sea correcto.",
      "oobe.controller_step":     "PASO 3",
      "oobe.controller_title":    "Mando",
      "oobe.controller_sub":      "Conecta tu mando ahora. GOSE lo detecta automáticamente.",
      "oobe.account_step":        "PASO 4",
      "oobe.account_title":       "Tu cuenta",
      "oobe.account_sub":         "Crea un perfil para este dispositivo.",
      "oobe.username_label":      "Nombre de usuario",
      "oobe.display_label":       "Nombre visible",
      "oobe.pin_label":           "PIN (8 dígitos)",
      "oobe.pin_confirm_label":   "Confirmar PIN",
      "oobe.personalize_step":    "PASO 5",
      "oobe.personalize_title":   "Personalízalo",
      "oobe.personalize_sub":     "Elige un tema de color.",
      "oobe.ai_step":             "PASO 6",
      "oobe.ai_title":            "Jugadores IA",
      "oobe.ai_sub":              "Empareja una IA para jugar contigo o manejar el sistema.",
      "oobe.ai_observe":          "Observar",
      "oobe.ai_play":             "Jugar",
      "oobe.ai_admin":            "Admin",
      "oobe.ai_add_another":      "Añadir otra IA",
      "oobe.done_step":           "TODO LISTO",
      "oobe.done_title":          "Estás listo.",
      "oobe.done_sub":            "GOSE está configurado. ¡A jugar!",

      "settings.title":           "Ajustes",
      "settings.tagline":         "GOSE · personalizar, sonido, red, hora, energía, privacidad, cuentas y más",
      "settings.footer_nav":      "↑↓ mover · ←→ cambiar valor · L1/R1 sección · Esc atrás",

      "settings.lang.nm":         "Idioma",
      "settings.lang.sub":        "Idioma en pantalla para el shell de GOSE",
      "settings.lang.en":         "English (US)",
      "settings.lang.es":         "Español (Spanish)",
      "settings.lang.toast":      "Idioma: ",
      "settings.lang.toast_reload": " — recarga las páginas para verlo en todas partes",
    }
  };

  /* ===================================================================
     RUNTIME
     =================================================================== */
  var _lc = (function () {
    var stored = null;
    try { stored = localStorage.getItem('gose-lang'); } catch (e) {}
    return (stored && LOCALES[stored]) ? stored : 'en';
  }());

  function t(key) {
    var tbl = LOCALES[_lc], val;
    if (tbl) val = tbl[key];
    if (val == null && _lc !== 'en') val = (LOCALES.en || {})[key];
    return (val != null) ? val : key;
  }

  function applyPage() {
    /* textContent replacements ([data-i18n]) */
    var els = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], key = el.getAttribute('data-i18n');
      if (key) el.textContent = t(key);
    }
    /* placeholder replacements ([data-i18n-placeholder]) */
    var phels = document.querySelectorAll('[data-i18n-placeholder]');
    for (var j = 0; j < phels.length; j++) {
      var ph = phels[j], pk = ph.getAttribute('data-i18n-placeholder');
      if (pk) ph.placeholder = t(pk);
    }
    /* also update html[lang] */
    try { document.documentElement.lang = _lc; } catch (e) {}
  }

  function locale() { return _lc; }

  function setLocale(lc) {
    if (!LOCALES[lc]) return;
    _lc = lc;
    try { localStorage.setItem('gose-lang', lc); } catch (e) {}
    try { document.documentElement.lang = lc; } catch (e) {}
    applyPage();
    /* fire a custom event so widgets that render dynamic strings can re-render */
    try { window.dispatchEvent(new Event('gose-locale-change')); } catch (e) {}
  }

  /* ---- expose ---- */
  window.GOSE = window.GOSE || {};
  window.GOSE.i18n = { t: t, locale: locale, setLocale: setLocale, applyPage: applyPage, LOCALES: LOCALES };
  window.GOSE.t = t;   /* shorthand used in JS: GOSE.t("key") */

  /* apply on DOM ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyPage);
  } else {
    applyPage();
  }
}());
