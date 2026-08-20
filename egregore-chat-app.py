#!/usr/bin/env python3
"""
Egregore Chat — Native Ubuntu Desktop App

A dedicated GTK3 + WebKit2GTK window for the Egregore Chat interface.
The launcher also ensures the local server is running before showing the window.

Commercial-grade improvements:
  * Safe .env loading even if python-dotenv is missing on system Python
  * Robust detection/termination of stale uvicorn processes
  * Better server startup diagnostics (logs are captured, not thrown away)
  * Graceful GTK/WebKit error handling with automatic crash recovery
  * Single-instance Gtk.Application
  * Keyboard shortcuts and optional --debug flag
"""
import logging
import logging.handlers
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------------------
# System Python guard
# ---------------------------------------------------------------------------
if sys.prefix != sys.base_prefix or getattr(sys, "real_prefix", None):
    print(
        "ERROR: egregore-chat-app.py must be run with system Python, not the project venv.",
        file=sys.stderr,
    )
    print("Run:  deactivate && python3 egregore-chat-app.py", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Environment loading (with fallback if dotenv is not installed system-wide)
# ---------------------------------------------------------------------------
def _parse_dotenv_manually(env_file: Path) -> None:
    """Minimal .env parser that handles simple KEY=VALUE lines."""
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except OSError as exc:
        print(f"WARNING: could not read .env file: {exc}", file=sys.stderr)


def load_environment() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)
    except ImportError:
        _parse_dotenv_manually(env_file)
    except Exception as exc:
        print(f"WARNING: dotenv loading failed ({exc}); using manual fallback.", file=sys.stderr)
        _parse_dotenv_manually(env_file)


load_environment()

# ---------------------------------------------------------------------------
# GTK / WebKit imports (after env and system-python guard)
# ---------------------------------------------------------------------------
try:
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2  # type: ignore
except ImportError as exc:
    print(
        "ERROR: PyGObject / WebKit2GTK is not available on system Python.",
        file=sys.stderr,
    )
    print("Install it with: sudo apt install python3-gi gir1.2-webkit2-4.1", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------
VENV_PYTHON = None
for candidate in (
    PROJECT_ROOT / ".venv" / "bin" / "python3.12",
    PROJECT_ROOT / ".venv" / "bin" / "python3",
    PROJECT_ROOT / ".venv" / "bin" / "python",
):
    if candidate.exists() and os.access(candidate, os.X_OK):
        VENV_PYTHON = candidate
        break

CERT_FILE = PROJECT_ROOT / "certs" / "dashboard.crt"
KEY_FILE = PROJECT_ROOT / "certs" / "dashboard.key"
ICON_FILE = PROJECT_ROOT / "egregore-icon.svg"

LOG_DIR = PROJECT_ROOT / ".logs"
SERVER_LOG_FILE = LOG_DIR / "server.log"
APP_LOG_FILE = Path("/tmp/egregore-chat.log")
SERVER_PID_FILE = PROJECT_ROOT / ".pids" / "server.pid"

APP_TITLE = "Egregore Chat"
APP_WIDTH = 1280
APP_HEIGHT = 900
SERVER_HOST = os.environ.get("EGREGORE_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("EGREGORE_PORT", "8443"))
CHAT_URL = f"https://{SERVER_HOST}:{SERVER_PORT}/static/fpi/"

SERVER_STARTUP_TIMEOUT = 60
SERVER_STARTUP_POLL_INTERVAL = 0.5
SERVER_STOP_TIMEOUT = 10
SERVER_PROCESS_PATTERN = "egregore.interface.bootstrap:create_app"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        APP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            APP_LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        logging.getLogger("egregore-chat").warning("Could not create log file: %s", exc)


log = logging.getLogger("egregore-chat")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_first_api_key() -> str | None:
    """Return the first configured API key (admin/owner key) from the env."""
    single = os.environ.get("EGREGORE_API_KEY")
    if single:
        return single.strip()
    raw = os.environ.get("EGREGORE_API_KEYS", "")
    if not raw:
        return None
    first_entry = raw.split(",")[0].strip()
    return first_entry.split(":")[0].strip() or None


def ssl_context_with_system_certs() -> ssl.SSLContext:
    """Build an SSL context that trusts the system CA store."""
    ctx = ssl.create_default_context()
    ctx.load_default_certs()
    return ctx


def is_server_running() -> bool:
    """Probe the local HTTPS endpoint to see if the server is reachable."""
    ctx = ssl_context_with_system_certs()
    req = urllib.request.Request(CHAT_URL, method="GET")

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=2) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        log.info("Server probe returned HTTP %d; treating as reachable.", exc.code)
        return True
    except ssl.SSLError as exc:
        log.info("Server probe hit SSL issue; treating as reachable: %s", exc)
        return True
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError) or "SSL" in str(reason):
            log.info("Server probe hit SSL issue (wrapped); treating as reachable: %s", reason)
            return True
        log.info("Server not reachable: %s", reason)
        return False
    except Exception as exc:
        log.warning("Server probe failed unexpectedly: %s", exc)
        return False


def _get_process_cmdline(pid: int) -> str:
    """Best-effort read of process command line."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read().decode("utf-8", errors="replace")
        return raw.replace("\x00", " ")
    except FileNotFoundError:
        pass
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return ""


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_egregore_process(pid: int) -> bool:
    """Check whether a PID corresponds to our uvicorn server."""
    cmdline = _get_process_cmdline(pid)
    return SERVER_PROCESS_PATTERN in cmdline


def find_running_servers() -> list[int]:
    """Find all PIDs of running Egregore uvicorn processes."""
    pids: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", SERVER_PROCESS_PATTERN],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return pids

        for line in result.stdout.strip().splitlines():
            pid_str = line.strip()
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            if pid == os.getpid():
                continue
            if is_egregore_process(pid):
                pids.append(pid)
    except FileNotFoundError:
        log.warning("pgrep not found; cannot scan for stale servers.")
    except Exception as exc:
        log.warning("Could not scan for stale servers: %s", exc)
    return pids


def wait_for_port_free(host: str, port: int, timeout: int = 5) -> bool:
    """Wait until nothing is listening on host:port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _terminate_stale_servers() -> None:
    """Terminate any stale uvicorn processes bound to our server port."""
    pids = find_running_servers()
    if not pids:
        return

    log.info("Terminating stale server process(es): %s", pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.time() + SERVER_STOP_TIMEOUT
    while time.time() < deadline:
        if not any(is_process_alive(p) for p in pids):
            break
        time.sleep(0.2)

    for pid in pids:
        if is_process_alive(pid):
            log.warning("Server PID %d did not terminate; sending SIGKILL.", pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    wait_for_port_free(SERVER_HOST, SERVER_PORT, timeout=5)


def tail_file(path: Path, lines: int = 20) -> str:
    """Return the last `lines` lines of a file as a string."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except OSError:
        return ""


def start_server() -> int:
    """Start the Egregore uvicorn server in a background subprocess."""
    if VENV_PYTHON is None:
        raise RuntimeError("Virtual environment Python not found. Create .venv first.")
    if not CERT_FILE.exists():
        raise RuntimeError(f"Missing TLS certificate: {CERT_FILE}")
    if not KEY_FILE.exists():
        raise RuntimeError(f"Missing TLS private key: {KEY_FILE}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    src_path = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        str(VENV_PYTHON),
        "-m",
        "uvicorn",
        "egregore.interface.bootstrap:create_app",
        "--factory",
        "--host", SERVER_HOST,
        "--port", str(SERVER_PORT),
        "--ssl-keyfile", str(KEY_FILE),
        "--ssl-certfile", str(CERT_FILE),
        "--log-level", "info",
    ]

    log.info("Starting Egregore server: %s", " ".join(cmd))

    try:
        server_log = open(SERVER_LOG_FILE, "ab", buffering=0)
        server_log.write(f"\n--- Starting server at {time.ctime()} ---\n".encode())
        server_log.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to start server process: {exc}") from exc
    finally:
        try:
            server_log.close()
        except Exception:
            pass

    time.sleep(1.0)
    if proc.poll() is not None:
        tail = tail_file(SERVER_LOG_FILE, 20)
        raise RuntimeError(
            f"Server exited immediately with code {proc.returncode}.\n"
            f"Last log lines:\n{tail}"
        )

    SERVER_PID_FILE.write_text(str(proc.pid))
    log.info("Server started with PID %d", proc.pid)
    return proc.pid


def ensure_server() -> None:
    """Make sure the server is running; start it if necessary."""
    if is_server_running():
        log.info("Server is already reachable at %s", CHAT_URL)
        return

    _terminate_stale_servers()
    start_server()

    log.info("Waiting for server to be ready...")
    deadline = time.time() + SERVER_STARTUP_TIMEOUT
    while time.time() < deadline:
        if is_server_running():
            log.info("Server is ready.")
            return
        time.sleep(SERVER_STARTUP_POLL_INTERVAL)

    raise RuntimeError(
        f"Server did not become reachable within {SERVER_STARTUP_TIMEOUT} seconds."
    )

# ---------------------------------------------------------------------------
# GTK Application
# ---------------------------------------------------------------------------
class EgregoreChatApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="digital.egregore.chat",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window: Gtk.ApplicationWindow | None = None
        self.webview: WebKit2.WebView | None = None
        self.accel_group: Gtk.AccelGroup | None = None
        self._crash_retries = 0
        self._max_crash_retries = 3

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self._setup_actions()

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return
        self._build_ui()

    def _setup_actions(self):
        """Register Gio actions for menu/keyboard access."""
        reload_action = Gio.SimpleAction.new("reload", None)
        reload_action.connect("activate", lambda a, p: self.on_reload())
        self.add_action(reload_action)

        browser_action = Gio.SimpleAction.new("open-browser", None)
        browser_action.connect("activate", lambda a, p: self.on_open_browser())
        self.add_action(browser_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda a, p: self.quit())
        self.add_action(quit_action)

        self.accel_group = Gtk.AccelGroup()
        self.accel_group.connect(
            Gdk.KEY_r, Gdk.ModifierType.CONTROL_MASK, Gtk.AccelFlags.VISIBLE,
            lambda *_: self.on_reload(),
        )
        self.accel_group.connect(
            Gdk.KEY_q, Gdk.ModifierType.CONTROL_MASK, Gtk.AccelFlags.VISIBLE,
            lambda *_: self.quit(),
        )

    def _build_ui(self):
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title(APP_TITLE)
        self.window.set_default_size(APP_WIDTH, APP_HEIGHT)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        if ICON_FILE.exists():
            self.window.set_icon_from_file(str(ICON_FILE))
        else:
            self.window.set_icon_name("web-browser")
        self.window.set_wmclass("egregore-chat", APP_TITLE)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_TITLE)
        header.set_subtitle(CHAT_URL)
        self.window.set_titlebar(header)

        reload_btn = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        reload_btn.set_tooltip_text("Reload chat")
        reload_btn.connect("clicked", self.on_reload)
        header.pack_start(reload_btn)

        browser_btn = Gtk.Button.new_from_icon_name(
            "web-browser-symbolic", Gtk.IconSize.BUTTON
        )
        browser_btn.set_tooltip_text("Open in browser")
        browser_btn.connect("clicked", self.on_open_browser)
        header.pack_end(browser_btn)

        context = WebKit2.WebContext.new()
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
        context.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)

        try:
            cert = Gio.TlsCertificate.new_from_file(str(CERT_FILE))
            for host in ("localhost", "127.0.0.1"):
                context.allow_tls_certificate_for_host(cert, host)
            log.info("Allowed local dashboard certificate for localhost/127.0.0.1")
        except Exception as exc:
            log.warning("Could not allow local certificate: %s", exc)

        self.webview = WebKit2.WebView.new_with_context(context)
        self.webview.connect("load-failed", self.on_load_failed)
        self.webview.connect("load-failed-with-tls-errors", self.on_tls_failed)
        self.webview.connect("web-process-terminated", self.on_web_process_terminated)
        self.webview.connect("load-changed", self.on_load_changed)
        self.webview.connect("notify::title", self.on_title_changed)

        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(os.environ.get("EGREGORE_DEBUG") == "1")
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_webgl(True)
        settings.set_enable_media_stream(False)
        self.webview.set_settings(settings)

        self._inject_api_key_cookie()
        self.webview.load_uri(CHAT_URL)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        self.window.add(scrolled)

        if self.accel_group is not None:
            self.window.add_accel_group(self.accel_group)

        self.window.connect("destroy", self.on_window_destroy)
        self.window.show_all()
        self.window.present()

    def _inject_api_key_cookie(self):
        api_key = _extract_first_api_key()
        if not api_key:
            log.warning("No EGREGORE_API_KEYS found; WebSocket authentication may fail.")
            return

        safe_key = quote(api_key, safe="")
        script_code = (
            f"document.cookie = 'api_key={safe_key}; path=/; Secure';"
        )

        content_manager = self.webview.get_user_content_manager()
        for injection_time in (
            WebKit2.UserScriptInjectionTime.START,
            WebKit2.UserScriptInjectionTime.END,
        ):
            script = WebKit2.UserScript.new(
                script_code,
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                injection_time,
                None,
                None,
            )
            content_manager.add_script(script)
        log.info("Injected admin API key cookie for WebSocket authentication")

    def on_reload(self, *args):
        if self.webview:
            self.webview.reload()

    def on_open_browser(self, *args):
        try:
            Gio.AppInfo.launch_default_for_uri(CHAT_URL, None)
        except Exception as exc:
            log.error("Could not open browser: %s", exc)
            self._show_error("Could not open browser", str(exc))

    def on_load_changed(self, webview, event):
        event_name = event.value_nick if hasattr(event, "value_nick") else str(event)
        log.debug("Load event: %s", event_name)
        if event == WebKit2.LoadEvent.FINISHED:
            log.info("Chat page finished loading.")
            webview.run_javascript(
                "JSON.stringify({title: document.title, body_len: document.body.innerHTML.length, url: location.href})",
                None,
                self._on_load_stats,
            )

    def _on_load_stats(self, webview, result):
        try:
            js_result = webview.run_javascript_finish(result)
            value = js_result.get_js_value()
            log.debug("Page stats: %s", value.to_string())
        except Exception as exc:
            log.debug("Could not read page stats: %s", exc)

    def on_title_changed(self, webview, _param):
        title = webview.get_title()
        if title and self.window:
            self.window.set_title(f"{title} — {APP_TITLE}")

    def _show_error(self, title: str, text: str) -> None:
        log.error("ERROR DIALOG: %s - %s", title, text)
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
        log.error("TLS error: %s", text)
        return False

    def on_web_process_terminated(self, webview, reason):
        reason_text = reason.value_nick if hasattr(reason, "value_nick") else str(reason)
        log.error("Web process terminated: %s", reason_text)

        if self._crash_retries >= self._max_crash_retries:
            self._show_error(
                "Web process crashed repeatedly",
                "The web rendering process has terminated too many times.\n"
                "Please restart the application.",
            )
            self._crash_retries = 0
            return

        self._crash_retries += 1
        GLib.timeout_add_seconds(2, self._reload_after_crash)

    def _reload_after_crash(self):
        if self.webview:
            log.info(
                "Attempting to reload after web process crash (try %d/%d)",
                self._crash_retries,
                self._max_crash_retries,
            )
            self.webview.reload()
        return GLib.SOURCE_REMOVE

    def on_window_destroy(self, _window):
        log.info("Window closed; application will quit but server keeps running.")
        self.quit()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    debug = "--debug" in sys.argv
    if debug:
        sys.argv.remove("--debug")

    configure_logging(debug)
    log.info("Starting Egregore Chat desktop app")

    if not VENV_PYTHON:
        log.error("Virtual environment Python not found. Create .venv first.")
        print("ERROR: no .venv Python found. Please create the virtual environment.",
              file=sys.stderr)
        sys.exit(1)

    try:
        ensure_server()
    except Exception as exc:
        log.critical("Could not ensure server is running: %s", exc)
        print(f"ERROR: Could not start Egregore server: {exc}", file=sys.stderr)
        if os.environ.get("DISPLAY"):
            try:
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
            except Exception:
                pass
        sys.exit(1)

    app = EgregoreChatApp()
    exit_status = app.run(sys.argv)
    sys.exit(exit_status)


if __name__ == "__main__":
    main()
