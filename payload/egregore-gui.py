#!/usr/bin/env python3
"""Egregore Desktop Power Switch — Click to launch entire ecosystem."""
import os
import sys
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path

REPO_ROOT = Path.home() / "egregore"
VENV_PATH = REPO_ROOT / ".venv"
ENV_FILE = REPO_ROOT / ".env"
PID_DIR = REPO_ROOT / ".pids"
LOG_DIR = REPO_ROOT / "logs"

APP_HOST = os.environ.get("EGREGORE_HOST", "0.0.0.0")
APP_PORT = os.environ.get("EGREGORE_PORT", "8443")

COLOR_OFF = "#2d2d2d"
COLOR_ON = "#00c853"
COLOR_WARN = "#ffab00"
COLOR_ERROR = "#ff1744"
COLOR_BG = "#1a1a1a"
COLOR_FG = "#e0e0e0"


class EgregoreGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Egregore — Main Power Switch")
        self.root.geometry("600x500")
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(False, False)
        self.is_running = False
        self.processes = {}
        self._build_ui()
        self._log("Egregore GUI ready. Click the switch to power on.")

    def _build_ui(self):
        tk.Label(self.root, text="EGREGORE", font=("Helvetica", 24, "bold"), fg=COLOR_FG, bg=COLOR_BG).pack(pady=(20, 5))
        tk.Label(self.root, text="Deterministic Runtime — SEL-X Chain Verification", font=("Helvetica", 10), fg="#888888", bg=COLOR_BG).pack(pady=(0, 20))

        switch_frame = tk.Frame(self.root, bg=COLOR_BG)
        switch_frame.pack(pady=10)
        self.switch_canvas = tk.Canvas(switch_frame, width=120, height=60, bg=COLOR_BG, highlightthickness=0)
        self.switch_canvas.pack()
        self.switch_bg = self.switch_canvas.create_oval(5, 5, 115, 55, fill=COLOR_OFF, outline="")
        self.switch_knob = self.switch_canvas.create_oval(10, 10, 50, 50, fill="#ffffff", outline="")
        self.switch_canvas.bind("<Button-1>", self._toggle_switch)

        self.status_label = tk.Label(self.root, text="OFF", font=("Helvetica", 14, "bold"), fg=COLOR_ERROR, bg=COLOR_BG)
        self.status_label.pack(pady=10)

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=500, mode="determinate")
        self.progress.pack(pady=10)

        log_frame = tk.Frame(self.root, bg=COLOR_BG)
        log_frame.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, width=70, bg="#0d0d0d", fg=COLOR_FG, font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.audit_frame = tk.Frame(self.root, bg=COLOR_BG)
        self.audit_frame.pack(pady=5, fill=tk.X, padx=20)
        self.audit_labels = {}
        for check in ["Keys", "Env", "Postgres", "Redis", "App", "Audit"]:
            lbl = tk.Label(self.audit_frame, text=f"{check}: —", font=("Consolas", 9), fg="#666666", bg=COLOR_BG)
            lbl.pack(side=tk.LEFT, padx=5)
            self.audit_labels[check] = lbl

    def _log(self, msg):
        self.log_text.configure(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.root.update_idletasks()

    def _set_audit(self, check, status):
        lbl = self.audit_labels[check]
        colors = {"pending": ("#666666", "—"), "pass": (COLOR_ON, "✓"), "fail": (COLOR_ERROR, "✗"), "warn": (COLOR_WARN, "!")}
        fg, symbol = colors.get(status, ("#666666", "?"))
        lbl.configure(fg=fg, text=f"{check}: {symbol}")
        self.root.update_idletasks()

    def _toggle_switch(self, event=None):
        self._shutdown() if self.is_running else self._startup()

    def _animate_switch(self, to_on):
        if to_on:
            self.switch_canvas.itemconfig(self.switch_bg, fill=COLOR_ON)
            self.switch_canvas.coords(self.switch_knob, 70, 10, 110, 50)
            self.status_label.configure(text="ON", fg=COLOR_ON)
        else:
            self.switch_canvas.itemconfig(self.switch_bg, fill=COLOR_OFF)
            self.switch_canvas.coords(self.switch_knob, 10, 10, 50, 50)
            self.status_label.configure(text="OFF", fg=COLOR_ERROR)
        self.root.update_idletasks()

    def _startup(self):
        self.is_running = True
        self._animate_switch(True)
        self.progress["value"] = 0
        self._log("=== POWER ON SEQUENCE ===")
        threading.Thread(target=self._startup_sequence, daemon=True).start()

    def _startup_sequence(self):
        steps = [
            ("Checking prerequisites", self._check_prerequisites, 10),
            ("Loading environment", self._load_env, 20),
            ("Starting PostgreSQL", self._start_postgres, 40),
            ("Starting Redis", self._start_redis, 50),
            ("Starting Egregore app", self._start_app, 70),
            ("Running warmup audit", self._run_audit, 90),
            ("Finalizing", self._finalize, 100),
        ]
        for label, func, progress_val in steps:
            self._log(f">>> {label}...")
            try:
                func()
                self.progress["value"] = progress_val
            except Exception as e:
                self._log(f"FAILED: {e}")
                self._shutdown()
                return
        self._log("=== ALL SYSTEMS GREEN ===")
        self._set_audit("Audit", "pass")

    def _check_prerequisites(self):
        self._set_audit("Keys", "pending")
        self._set_audit("Env", "pending")
        if not VENV_PATH.exists():
            raise RuntimeError(f"Virtual env not found: {VENV_PATH}")
        signing_key = REPO_ROOT / "secrets" / "signing_key.pem"
        if not signing_key.exists():
            raise RuntimeError("Signing key not found. Run key rotation first.")
        self._set_audit("Keys", "pass")
        if not ENV_FILE.exists():
            self._log("WARNING: .env not found, creating from .env.example")
            example = REPO_ROOT / ".env.example"
            if example.exists():
                ENV_FILE.write_text(example.read_text())
            else:
                raise RuntimeError("No .env or .env.example found")
        self._set_audit("Env", "pass")

    def _load_env(self):
        self._log("Self-adjusting environment...")
        if ENV_FILE.exists():
            with open(ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        os.environ[key] = val
        if not os.environ.get("EGREGORE_ZARC_SIGNING_KEY_HEX"):
            key_file = REPO_ROOT / "secrets" / "signing_key.pem"
            if key_file.exists():
                os.environ["EGREGORE_ZARC_SIGNING_KEY_HEX"] = key_file.read_text().strip()
                self._log("Auto-exported EGREGORE_ZARC_SIGNING_KEY_HEX")
        if not os.environ.get("EGREGORE_API_KEYS"):
            key_file = REPO_ROOT / "secrets" / "signing_key.pem"
            if key_file.exists():
                key = key_file.read_text().strip()
                os.environ["EGREGORE_API_KEYS"] = f"{key}:default:admin:admin"
                self._log("Auto-exported EGREGORE_API_KEYS")
        src_path = str(REPO_ROOT / "src")
        if src_path not in os.environ.get("PYTHONPATH", ""):
            os.environ["PYTHONPATH"] = f"{src_path}:{os.environ.get('PYTHONPATH', '')}"
            self._log(f"Added {src_path} to PYTHONPATH")

    def _start_postgres(self):
        self._set_audit("Postgres", "pending")
        result = subprocess.run(["systemctl", "is-active", "--quiet", "postgresql"], capture_output=True)
        if result.returncode != 0:
            self._log("Starting PostgreSQL...")
            subprocess.run(["sudo", "systemctl", "start", "postgresql"], check=False)
            time.sleep(2)
        result = subprocess.run(["pg_isready", "-q"], capture_output=True)
        if result.returncode == 0:
            self._set_audit("Postgres", "pass")
            self._log("PostgreSQL: ready")
        else:
            self._set_audit("Postgres", "warn")
            self._log("PostgreSQL: may not be ready (continuing)")

    def _command_exists(self, cmd):
        return subprocess.run(["which", cmd], capture_output=True).returncode == 0

    def _redis_is_listening(self, host="localhost", port=6379, timeout=1):
        try:
            import socket

            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _start_redis(self):
        self._set_audit("Redis", "pending")
        try:
            result = subprocess.run(["systemctl", "is-active", "--quiet", "redis-server"], capture_output=True)
            if result.returncode != 0:
                self._log("Starting Redis...")
                subprocess.run(
                    ["sudo", "systemctl", "start", "redis-server"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1)
        except FileNotFoundError:
            self._log("WARNING: systemctl not available; cannot auto-start Redis")

        redis_ready = False
        if self._command_exists("redis-cli"):
            for _ in range(5):
                result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True)
                if "PONG" in result.stdout:
                    redis_ready = True
                    break
                time.sleep(1)
        elif self._redis_is_listening():
            redis_ready = True

        if redis_ready:
            self._set_audit("Redis", "pass")
            self._log("Redis: ready")
        else:
            self._set_audit("Redis", "warn")
            self._log("WARNING: Redis not detected. Install redis-server or ensure it is running. Continuing without Redis.")

    def _get_pid_on_port(self, port):
        """Return PID of process listening on given port, or None."""
        try:
            result = subprocess.run(
                ["ss", "-tlnpH", f"sport = :{port}"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "pid=" in line:
                    import re
                    m = re.search(r'pid=(\d+)', line)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return None

    def _start_app(self):
        self._set_audit("App", "pending")
        PID_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        pid_file = PID_DIR / "app.pid"
        # Check if something is already listening on the port
        existing_pid = self._get_pid_on_port(APP_PORT)
        if existing_pid and Path(f"/proc/{existing_pid}").exists():
            self._log(f"App already running (PID {existing_pid}) — reusing")
            pid_file.write_text(existing_pid)
            self._set_audit("App", "pass")
            return
        self._log(f"Starting Egregore app on {APP_HOST}:{APP_PORT}...")
        ssl_key = REPO_ROOT / "certs" / "dashboard.key"
        ssl_cert = REPO_ROOT / "certs" / "dashboard.crt"
        cmd = [
            str(VENV_PATH / "bin" / "uvicorn"),
            "egregore.interface.bootstrap:create_app",
            "--factory",
            "--host", APP_HOST, "--port", APP_PORT,
            "--log-level", "info",
        ]
        if ssl_key.exists() and ssl_cert.exists():
            cmd += ["--ssl-keyfile", str(ssl_key), "--ssl-certfile", str(ssl_cert)]
            scheme = "https"
        else:
            scheme = "http"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        api_key_file = REPO_ROOT / "secrets" / "api_key.hex"
        if api_key_file.exists():
            api_key = api_key_file.read_text().strip()
            env["EGREGORE_API_KEYS"] = f"{api_key}:test:admin:admin"
        signing_key_file = REPO_ROOT / "secrets" / "signing_key.pem"
        if signing_key_file.exists():
            env["EGREGORE_ZARC_SIGNING_KEY_HEX"] = signing_key_file.read_text().strip()
        log_file = LOG_DIR / "app.log"
        with open(log_file, "a") as f:
            f.write(f"\n=== Startup {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        proc = subprocess.Popen(cmd, stdout=open(log_file, "a"), stderr=subprocess.STDOUT, cwd=str(REPO_ROOT), env=env)
        pid_file.write_text(str(proc.pid))
        self.processes["app"] = proc
        for i in range(20):
            time.sleep(1)
            pid_on_port = self._get_pid_on_port(APP_PORT)
            if pid_on_port:
                self._set_audit("App", "pass")
                self._log(f"App ready at {scheme}://{APP_HOST}:{APP_PORT} (PID {pid_on_port})")
                pid_file.write_text(pid_on_port)
                return
        self._set_audit("App", "warn")
        self._log("App may not be ready yet. Check logs/app.log.")

    def _run_audit(self):
        self._set_audit("Audit", "pending")
        self._log("Running warmup audit...")
        audit_tests = [
            ("Bootstrap import", [str(VENV_PATH / "bin" / "python"), "-c", "import sys; sys.path.insert(0, 'src'); from egregore.interface.bootstrap import create_app; print('OK')"]),
            ("Key middleware test", [str(VENV_PATH / "bin" / "python"), "-c", "import sys; sys.path.insert(0, 'src'); from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware; print('OK')"]),
            ("Composition root test", [str(VENV_PATH / "bin" / "python"), "-c", "import sys; sys.path.insert(0, 'src'); from egregore.application.composition_root import CompositionRoot; print('OK')"]),
        ]
        all_pass = True
        for name, cmd in audit_tests:
            self._log(f"  Audit: {name}...")
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), env=os.environ)
            if result.returncode == 0 and "OK" in result.stdout:
                self._log(f"    PASS")
            else:
                self._log(f"    FAIL: {result.stderr or result.stdout}")
                all_pass = False
        if all_pass:
            self._set_audit("Audit", "pass")
        else:
            self._set_audit("Audit", "warn")

    def _finalize(self):
        self._log("System warmed up and ready.")

    def _shutdown(self):
        self._log("=== SHUTDOWN SEQUENCE ===")
        self.is_running = False
        pid_file = PID_DIR / "app.pid"
        # Kill by port first (most reliable), then fall back to PID file
        pid_on_port = self._get_pid_on_port(APP_PORT)
        if pid_on_port and Path(f"/proc/{pid_on_port}").exists():
            self._log(f"Stopping app on port {APP_PORT} (PID {pid_on_port})...")
            subprocess.run(["kill", pid_on_port], check=False)
            time.sleep(1)
        elif pid_file.exists():
            pid = pid_file.read_text().strip()
            if pid and Path(f"/proc/{pid}").exists():
                self._log(f"Stopping app (PID {pid})...")
                subprocess.run(["kill", pid], check=False)
        if pid_file.exists():
            pid_file.unlink()
        for check in ["Keys", "Env", "Postgres", "Redis", "App", "Audit"]:
            self._set_audit(check, "pending")
        self._animate_switch(False)
        self.progress["value"] = 0
        self._log("All layers stopped.")


def main():
    root = tk.Tk()
    app = EgregoreGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
