#!/usr/bin/env python3
# GOSE shell: render the HTML GOSE UI fullscreen via WebKit2GTK (in the VM, GPU-accel).
import gi, sys
gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, Gdk
url = sys.argv[1] if len(sys.argv) > 1 else "file:///userdata/gose-ui/gose-home.html"
# GOSE base dark — used for the window AND the WebView so the frame between page
# loads is dark, never white. (WebKit paints its base color before a page's first
# paint; default is white = the flash seen on every navigation.)
GOSE_DARK = Gdk.RGBA(); GOSE_DARK.parse('#07070f')
win = Gtk.Window(); win.set_title("GOSE")
win.set_decorated(False)
win.override_background_color(Gtk.StateFlags.NORMAL, GOSE_DARK)
win.fullscreen()
# NOTE: no keep_above — GOSE is the only shell now, and keep_above would hide
# launched native apps (Steam, emulators) behind the kiosk. When an app exits,
# this fullscreen window is revealed again = back to GOSE.
win.connect('destroy', Gtk.main_quit)
# Autoplay: boot/login/system sounds must fire on page load WITHOUT a user gesture (the
# shell is controller/key driven). The lever is the per-view autoplay POLICY, NOT the
# WebKitSettings 'media-playback-requires-user-gesture' flag — verified in-guest that the
# flag (either value) does NOT govern autoplay in this WebKit2GTK build (a page-load
# <audio>.play() still rejects NotAllowedError), whereas WebsitePolicies(autoplay=ALLOW)
# lets it play. Build the WebView with that policy; fall back if the API is unavailable.
try:
    _pol = WebKit2.WebsitePolicies(autoplay=WebKit2.AutoplayPolicy.ALLOW)
    wv = WebKit2.WebView(website_policies=_pol)
except Exception:
    wv = WebKit2.WebView()
try: wv.set_background_color(GOSE_DARK)   # the seamless bit: no white frame between pages
except Exception: pass
st = wv.get_settings()
for prop, val in [('enable-webgl', True), ('enable-developer-extras', True),
                  ('enable-write-console-messages-to-stdout', True)]:
    try: st.set_property(prop, val)
    except Exception: pass
try: st.set_property('hardware-acceleration-policy', WebKit2.HardwareAccelerationPolicy.ALWAYS)
except Exception: pass
wv.load_uri(url)
win.add(wv); win.show_all()
win.present()
wv.grab_focus()   # so keyboard input reaches the page
Gtk.main()
