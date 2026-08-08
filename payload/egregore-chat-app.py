#!/usr/bin/env python3
"""
Egregore Chat — Native Ubuntu Desktop App

A dedicated GTK3 + WebKit2GTK window for the Egregore Chat interface.
The launcher also ensures the local server is running before showing the window.
"""
import os
import sys
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

# Load .env before any imports that read environment variables.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# This app needs the system GTK/WebKit bindings. The project venv does not
# include PyGObject, so running under the venv fails with "No module named 'gi'".
if sys.prefix != sys.base_prefix or getattr(sys, "real_prefix", None):
    print(
        "ERROR: egregore-chat-app.py must be run with system Python, not the project venv.",
        file=sys.stderr,
    )
    print("Run:  deactivate && python3 egregore-chat-app.py", file=sys.stderr)
    sys.exit(1)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, Gio, GLib


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3.12"
if not VENV_PYTHON.exists():
    VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
UVICORN = PROJECT_ROOT / ".venv" / "bin" / "uvicorn"

CERT_FILE = PROJECT_ROOT / "certs" / "dashboard.crt"
KEY_FILE = PROJECT_ROOT / "certs" / "dashboard.key"
ICON_FILE = PROJECT_ROOT / "egregore-icon.svg"
PID_FILE = PROJECT_ROOT / ".pids" / "app.pid"
LOG_FILE = Path("/tmp/egregore-chat.log")

APP_TITLE = "Egregore Chat"
APP_WIDTH = 1280
APP_HEIGHT = 900
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8443
CHAT_URL = f"https://{SERVER_HOST}:{SERVER_PORT}/static/chat/"

SERVER_STARTUP_TIMEOUT = 60  # seconds
SERVER_STARTUP_POLL_INTERVAL = 0.5  # seconds


def log(message: str) -> None:
    line = f"[egregore-chat] {message}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _extract_first_api_key() -> str | None:
    """Return the first configured API key (admin/owner key) from the env."""
    raw = os.environ.get("EGREGORE_API_KEYS", "")
    if not raw:
        return None
    first_entry = raw.split(",")[0].strip()
    return first_entry.split(":")[0].strip() or None


def is_server_running() -> bool:
    """Probe the local HTTPS endpoint to see if the server is up."""
    import ssl as _ssl

    try:
        ctx = ssl_context_with_system_certs()
        req = urllib.request.Request(CHAT_URL, method="GET")
        with urllib.request.urlopen(req, context=ctx, timeout=2) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # Any HTTP response means the server accepted the TCP/TLS connection.
        log(f"Server probe returned HTTP {exc.code}; treating as reachable.")
        return True
    except _ssl.SSLError as exc:
        # TLS handshake succeeded far enough to validate/inspect the cert,
        # which means the server is listening. Trust issues are handled below.
        log(f"Server probe hit SSL issue; treating as reachable: {exc}")
        return True
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # SSL errors are wrapped in URLError by urllib — treat as reachable.
        if isinstance(reason, _ssl.SSLError) or "SSL" in str(reason):
            log(f"Server probe hit SSL issue (wrapped); treating as reachable: {reason}")
            return True
        # Connection refused / no listener means the server is down.
        log(f"Server not reachable: {reason}")
        return False
    except Exception as exc:
        # Defensive fallback: assume down on unexpected errors.
        log(f"Server probe failed unexpectedly: {exc}")
        return False


def ssl_context_with_system_certs():
    """Build an SSL context that trusts the system CA store (includes mkcert root)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.load_default_certs()
    return ctx


def write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip().splitlines()[0])
    except Exception:
        return None


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_stale_servers() -> None:
    """Terminate any stale uvicorn processes bound to our server port."""
    import socket

    try:
        result = subprocess.run(
            ["pgrep", "-f", "egregore.interface.bootstrap:create_app"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        pids = []
        for line in result.stdout.strip().splitlines():
            pid_str = line.strip()
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            if pid == os.getpid():
                continue
            pids.append(pid)
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        if pids:
            log(f"Terminated stale server process(es) {pids}")
            # Wait until the port is actually free before returning.
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    s = socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=0.5)
                    s.close()
                except OSError:
                    return
                time.sleep(0.2)
    except Exception as exc:
        log(f"Could not clean stale servers: {exc}")


def find_running_server() -> int | None:
    """Look for an existing uvicorn process serving egregore.interface.bootstrap."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "egregore.interface.bootstrap:create_app"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def start_server() -> int:
    """Start the Egregore uvicorn server in a background subprocess."""
    log("Starting Egregore server...")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    cmd = [
        str(VENV_PYTHON),
        str(UVICORN),
        "egregore.interface.bootstrap:create_app",
        "--factory",
        "--host", SERVER_HOST,
        "--port", str(SERVER_PORT),
        "--ssl-keyfile", str(KEY_FILE),
        "--ssl-certfile", str(CERT_FILE),
        "--log-level", "info",
    ]

    log(f"Command: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_pid(proc.pid)
    log(f"Server started with PID {proc.pid}")
    return proc.pid


def ensure_server() -> None:
    """Make sure the server is running; start it if necessary."""
    # 1. Fast path: probe the network endpoint.
    if is_server_running():
        log("Server is already reachable.")
        return

    # 2. There is a process but it is not reachable (wrong protocol/stuck/dying).
    # Kill stale processes and wait for the port to be free.
    _kill_stale_servers()

    # 3. Start a fresh server.
    start_server()

    # 4. Wait for the server to become reachable.
    log("Waiting for server to be ready...")
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_server_running():
            log("Server is ready.")
            return
        time.sleep(SERVER_STARTUP_POLL_INTERVAL)

    raise RuntimeError(
        f"Server did not become reachable within {SERVER_STARTUP_TIMEOUT} seconds."
    )


class EgregoreChatApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="digital.egregore.chat",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None
        self.webview = None

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return

        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title(APP_TITLE)
        self.window.set_default_size(APP_WIDTH, APP_HEIGHT)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_icon_from_file(str(ICON_FILE))
        self.window.set_wmclass("egregore-chat", APP_TITLE)

        # Header bar with reload/quit actions.
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_TITLE)
        header.set_subtitle(CHAT_URL)
        self.window.set_titlebar(header)

        reload_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        reload_btn.set_tooltip_text("Reload chat")
        reload_btn.connect("clicked", self.on_reload)
        header.pack_start(reload_btn)

        # WebKit2 web view. Use a fresh context and trust the local mkcert cert.
        context = WebKit2.WebContext.new()
        context.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)
        try:
            cert = Gio.TlsCertificate.new_from_file(str(CERT_FILE))
            for host in ("localhost", "127.0.0.1"):
                context.allow_tls_certificate_for_host(cert, host)
            log("Allowed local dashboard certificate for localhost/127.0.0.1")
        except Exception as exc:
            log(f"Could not allow local certificate: {exc}")
        self.webview = WebKit2.WebView.new_with_context(context)
        self.webview.connect("load-failed", self.on_load_failed)
        self.webview.connect("load-failed-with-tls-errors", self.on_tls_failed)
        self.webview.connect("web-process-terminated", self.on_web_process_terminated)

        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(True)
        self.webview.set_settings(settings)

        # Inject the admin API key as a cookie so the WebSocket can authenticate.
        api_key = _extract_first_api_key()
        if api_key:
            content_manager = self.webview.get_user_content_manager()
            script = WebKit2.UserScript.new(
                f'document.cookie = "api_key={api_key}; path=/; Secure";',
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
            content_manager.add_script(script)
            log("Injected admin API key cookie for WebSocket authentication")

        self.webview.load_uri(CHAT_URL)

        # Capture console messages and load failures for debugging.
        self.webview.connect("load-changed", self.on_load_changed)
        self.webview.connect("notify::title", self.on_title_changed)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        self.window.add(scrolled)

        self.window.connect("destroy", self.on_window_destroy)
        self.window.show_all()
        self.window.present()

    def on_reload(self, _button):
        if self.webview:
            self.webview.reload()

    def on_load_changed(self, webview, event):
        event_name = event.value_nick if hasattr(event, "value_nick") else str(event)
        log(f"Load event: {event_name}")
        if event == WebKit2.LoadEvent.FINISHED:
            log("Chat page finished loading.")
            webview.run_javascript(
                "JSON.stringify({title: document.title, body_len: document.body.innerHTML.length, url: location.href})",
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

    def _show_error(self, title: str, text: str) -> None:
        log(f"ERROR DIALOG: {title} - {text}")
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
        text = f"Failed to load {failing_uri}\n{error}"
        self._show_error("Chat page failed to load", text)
        return False

    def on_tls_failed(self, webview, failing_uri, certificate, errors, user_data):
        text = f"TLS error loading {failing_uri}\n{errors}"
        self._show_error("TLS certificate error", text)
        return False  # Let WebKit continue loading.

    def on_web_process_terminated(self, webview, reason):
        log(f"Web process terminated: {reason.value_nick if hasattr(reason, 'value_nick') else reason}")
        self._show_error("Web process crashed", "The web rendering process terminated unexpectedly.")


    def on_window_destroy(self, _window):
        log("Window closed; app will quit but server keeps running.")
        self.quit()


def main():
    try:
        ensure_server()
    except Exception as exc:
        log(f"Could not ensure server is running: {exc}")
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Could not start Egregore server",
        )
        dialog.format_secondary_text(str(exc))
        dialog.run()
        dialog.destroy()
        sys.exit(1)

    app = EgregoreChatApp()
    exit_status = app.run(sys.argv)
    sys.exit(exit_status)


if __name__ == "__main__":
    main()
