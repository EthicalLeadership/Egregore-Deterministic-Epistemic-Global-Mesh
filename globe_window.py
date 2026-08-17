#!/usr/bin/env python3
"""
Egregore Globe — Native Ubuntu Desktop Window

A dedicated GTK3 + WebKit2GTK window for the Full-Perspective globe
(served by run.py). Starts the globe server on 127.0.0.1:8090 if it is
not already running, then opens it inside a WebKit window.

Run with SYSTEM Python (GTK/WebKit bindings are not in the project venv):
    deactivate && python3 globe_window.py

Optional: force a different globe port via EGREGORE_PORT.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# This app needs the system GTK/WebKit bindings. The project venv does not
# include PyGObject, so running under the venv fails with "No module named 'gi'".
if sys.prefix != sys.base_prefix or getattr(sys, "real_prefix", None):
    print(
        "ERROR: globe_window.py must be run with system Python, not the project venv.",
        file=sys.stderr,
    )
    print("Run:  deactivate && python3 globe_window.py", file=sys.stderr)
    sys.exit(1)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, Gio, GLib  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
RUN_SCRIPT = PROJECT_ROOT / "run.py"
ICON_FILE = PROJECT_ROOT / "egregore-icon.svg"
LOG_FILE = Path("/tmp/egregore-globe.log")

APP_TITLE = "Egregore Globe"
APP_WIDTH = 1280
APP_HEIGHT = 900
GLOBE_HOST = "127.0.0.1"
GLOBE_PORT = int(os.environ.get("EGREGORE_PORT", "8090"))
GLOBE_URL = f"http://{GLOBE_HOST}:{GLOBE_PORT}/"

SERVER_STARTUP_TIMEOUT = 30  # seconds
SERVER_STARTUP_POLL_INTERVAL = 0.5  # seconds


def log(message: str) -> None:
    line = f"[egregore-globe] {message}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_globe_server_running() -> bool:
    """Probe the globe HTTP endpoint to see if the server is up."""
    try:
        req = urllib.request.Request(GLOBE_URL, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # Any HTTP response means a server is listening on the port.
        log(f"Globe server probe returned HTTP {exc.code}; treating as reachable.")
        return True
    except Exception as exc:
        log(f"Globe server not reachable: {exc}")
        return False


class GlobeWindowApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="digital.egregore.globe",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None
        self.webview = None
        self.server_proc = None

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return

        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title(APP_TITLE)
        self.window.set_default_size(APP_WIDTH, APP_HEIGHT)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        if ICON_FILE.exists():
            try:
                self.window.set_icon_from_file(str(ICON_FILE))
            except Exception as exc:
                log(f"Could not set window icon: {exc}")
        self.window.set_wmclass("egregore-globe", APP_TITLE)

        # Header bar with reload/quit actions.
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_TITLE)
        header.set_subtitle(GLOBE_URL)
        self.window.set_titlebar(header)

        reload_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reload_btn.set_tooltip_text("Reload globe")
        reload_btn.connect("clicked", self.on_reload)
        header.pack_start(reload_btn)

        # WebKit2 web view. Software-rendering fallback helps WebKitGTK on
        # systems where the GPU compositor misbehaves and blanks the canvas.
        os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

        self.webview = WebKit2.WebView.new()
        self.webview.connect("load-failed", self.on_load_failed)
        self.webview.connect("load-changed", self.on_load_changed)
        self.webview.connect("notify::title", self.on_title_changed)
        try:
            self.webview.connect("web-process-terminated", self.on_web_process_terminated)
        except Exception:
            pass

        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(True)
        self.webview.set_settings(settings)

        # Capture console messages (WebKit2 4.1 exposes a ConsoleMessage object).
        try:
            self.webview.connect("console-message", self.on_console_message)
        except Exception:
            log("console-message signal unavailable in this WebKit2 version")

        self.webview.load_uri(GLOBE_URL)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        self.window.add(scrolled)

        self.window.connect("destroy", self.on_window_destroy)
        self.window.show_all()
        self.window.present()

    def on_reload(self, _button):
        if self.webview:
            self.webview.reload()

    def on_console_message(self, webview, message):
        try:
            text = message.get_text()
        except Exception:
            text = str(message)
        log(f"CONSOLE: {text}")

    def on_load_changed(self, webview, event):
        nick = getattr(event, "value_nick", None) or str(event)
        log(f"Load event: {nick}")
        if event == WebKit2.LoadEvent.FINISHED:
            log("Globe page finished loading.")
            webview.run_javascript(
                "JSON.stringify({title: document.title, url: location.href})",
                None,
                self._on_load_stats,
            )

    def _on_load_stats(self, webview, result):
        try:
            js_result = webview.run_javascript_finish(result)
            value = js_result.get_js_value()
            log(f"Page stats: {value.to_string()}")
        except Exception as exc:
            log(f"Could not read page stats: {exc}")

    def on_title_changed(self, webview, _param):
        title = webview.get_title()
        if title and self.window:
            self.window.set_title(f"{title} — {APP_TITLE}")

    def _show_error(self, title, text):
        log(f"ERROR DIALOG: {title} — {text}")
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def on_load_failed(self, webview, event, failing_uri, error):
        self._show_error("Globe page failed to load", f"{failing_uri}\n{error}")
        return False

    def on_web_process_terminated(self, webview, reason):
        nick = getattr(reason, "value_nick", None) or reason
        log(f"Web process terminated: {nick}")
        self._show_error("Web process crashed", "The web rendering process terminated unexpectedly.")

    def on_window_destroy(self, _window):
        log("Window closed; stopping globe server if we started it.")
        if self.server_proc is not None and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except Exception:
                self.server_proc.kill()
            log("Globe server stopped.")
        self.quit()


def ensure_globe_server():
    """Make sure the globe server is running; start it if necessary."""
    if is_globe_server_running():
        log("Globe server is already reachable at " + GLOBE_URL)
        return None

    log(f"Starting globe server ({RUN_SCRIPT.name}) on port {GLOBE_PORT}…")
    env = os.environ.copy()
    env["EGREGORE_PORT"] = str(GLOBE_PORT)
    env["EGREGORE_NO_BROWSER"] = "1"  # the window IS the browser

    # run.py is pure stdlib, so the current (system) interpreter can run it.
    proc = subprocess.Popen(
        [sys.executable, str(RUN_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=open(LOG_FILE, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    log("Waiting for globe server to be ready…")
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_globe_server_running():
            log("Globe server is ready.")
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                f"Globe server exited early (code {proc.returncode}). "
                f"Check {LOG_FILE}."
            )
        time.sleep(SERVER_STARTUP_POLL_INTERVAL)

    proc.terminate()
    raise RuntimeError(
        f"Globe server did not become reachable within {SERVER_STARTUP_TIMEOUT}s. "
        f"Check {LOG_FILE}."
    )


def main():
    try:
        server_proc = ensure_globe_server()
    except Exception as exc:
        log(f"Could not ensure globe server is running: {exc}")
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Could not start globe server",
        )
        dialog.format_secondary_text(str(exc))
        dialog.run()
        dialog.destroy()
        sys.exit(1)

    app = GlobeWindowApp()
    app.server_proc = server_proc
    exit_status = app.run(sys.argv)
    sys.exit(exit_status)


if __name__ == "__main__":
    main()
