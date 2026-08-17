#!/usr/bin/env python3
"""
NAIL Egregore Bridge (stdlib-only)
Exposes NAIL data to Egregore via HTTP and pushes heartbeats.
No external dependencies.
"""

import argparse
import json
import logging
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DB_FILENAME = "nail.db"
LOG = logging.getLogger("nail_egregore")


class NailDB:
    """Lightweight read-only wrapper around nail.db"""
    def __init__(self, db_path=DB_FILENAME):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def get_latest(self):
        cur = self.conn.execute("SELECT * FROM telemetry ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else {}

    def get_history(self, limit=100):
        cur = self.conn.execute("SELECT * FROM telemetry ORDER BY ts DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        return rows

    def get_baseline(self, metric, n=100):
        cur = self.conn.execute(
            f"SELECT {metric} FROM telemetry WHERE {metric} IS NOT NULL ORDER BY ts DESC LIMIT ?", (n,)
        )
        vals = [r[0] for r in cur.fetchall()]
        if not vals:
            return {"avg": 0.0, "std": 0.0}
        avg = sum(vals) / len(vals)
        var = sum((v - avg) ** 2 for v in vals) / len(vals)
        return {"avg": avg, "std": var ** 0.5}


def detect_weaknesses(db, current):
    """Simple threshold check (mirror the logic from nail.py)"""
    thresholds = {
        "cpu_temp": 80.0,
        "gpu_temp": 80.0,
        "cpu_percent": 90.0,
        "gpu_util": 90.0,
        "mem_percent": 90.0,
        "swap_percent": 50.0,
    }
    weaknesses = []
    for metric, threshold in thresholds.items():
        val = current.get(metric)
        if val is None:
            continue
        avg_std = db.get_baseline(metric)
        avg = avg_std["avg"]
        std = avg_std["std"]
        if val > threshold:
            severity = "high" if val > threshold * 1.1 else "medium"
            weaknesses.append({
                "metric": metric,
                "value": round(val, 1),
                "threshold": threshold,
                "severity": severity,
                "description": f"{metric} is {val:.1f} (threshold {threshold})"
            })
        elif avg and std and val > avg + 2 * std:
            weaknesses.append({
                "metric": metric,
                "value": round(val, 1),
                "threshold": round(avg + 2 * std, 1),
                "severity": "low",
                "description": f"{metric} is anomalously high vs baseline (avg {avg:.1f}, std {std:.1f})"
            })
    return weaknesses


class RequestHandler(BaseHTTPRequestHandler):
    db = None
    node_id = "nail-01"

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok", "node_id": self.node_id})
        elif self.path == "/telemetry/latest":
            self._send_json(self.db.get_latest())
        elif self.path.startswith("/telemetry/history"):
            try:
                limit = int(self.path.split("limit=")[1]) if "limit=" in self.path else 100
            except Exception:
                limit = 100
            self._send_json(self.db.get_history(limit))
        elif self.path == "/weaknesses":
            current = self.db.get_latest()
            weaknesses = detect_weaknesses(self.db, current)
            self._send_json(weaknesses)
        elif self.path == "/model":
            model = {}
            for metric in ["cpu_percent", "mem_percent", "swap_percent", "cpu_temp", "load1", "gpu_util", "gpu_temp"]:
                model[metric] = self.db.get_baseline(metric)
            self._send_json(model)
        else:
            self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, status=400)
            return

        if self.path == "/actions":
            # This bridge does not execute actions; it's read-only for Egregore.
            # To execute actions, Egregore should talk to the base nail.py daemon.
            self._send_json({"error": "Action execution not supported in bridge; use base daemon"}, status=501)
        elif self.path == "/tasks/execute":
            # Placeholder: return latest telemetry as task result
            result = {
                "job_id": data.get("job_id", "unknown"),
                "status": "completed",
                "result": self.db.get_latest()
            }
            self._send_json(result)
        else:
            self._send_json({"error": "Not found"}, status=404)

    def log_message(self, format, *args):
        LOG.debug(f"HTTP {self.address_string()} - {format % args}")


class EgregorePusher:
    def __init__(self, egregore_url, node_id, interval=30):
        self.egregore_url = egregore_url.rstrip('/')
        self.node_id = node_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _build_heartbeat(self, metrics):
        return {
            "node_id": self.node_id,
            "timestamp_ns": int(time.time() * 1e9),
            "load_metrics": {
                "cpu_percent": metrics.get("cpu_percent", 0.0),
                "memory_mb": metrics.get("mem_percent", 0.0) * 32768 / 100,  # adjust total RAM
                "vram_mb": metrics.get("gpu_mem_used", 0.0),
                "disk_iops": 0,
                "network_mbps": 0,
            },
            "available_capabilities": ["cpu", "gpu", "telemetry"],
            "active_job_count": 0,
            "uptime_ticks": 0,
            "public_key_fingerprint": None,
        }

    def _push(self):
        try:
            import psutil  # optional, for better metrics; fallback to zeros
            total_mem = psutil.virtual_memory().total
            boot_time = psutil.boot_time()
            net = psutil.net_io_counters()
            metrics = self._latest_metrics()
            heartbeat = self._build_heartbeat(metrics)
            heartbeat["load_metrics"]["memory_mb"] = metrics.get("mem_percent", 0.0) * total_mem / (1024 * 1024 * 100)
            heartbeat["uptime_ticks"] = int(time.time() - boot_time)
            heartbeat["load_metrics"]["network_mbps"] = (net.bytes_sent + net.bytes_recv) / 1e6
        except ImportError:
            metrics = self._latest_metrics()
            heartbeat = self._build_heartbeat(metrics)
        event = {
            "event_id": f"hb-{self.node_id}-{int(time.time()*1000)}",
            "source": "nail",
            "purpose": "node_heartbeat",
            "entity_kind": "node",
            "entity_value": self.node_id,
            "payload": heartbeat,
        }
        data = json.dumps(event).encode()
        req = urllib.request.Request(
            f"{self.egregore_url}/ingest",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    LOG.debug("Heartbeat pushed to Egregore")
                else:
                    LOG.warning(f"Egregore push returned {resp.status}")
        except urllib.error.URLError as e:
            LOG.warning(f"Egregore push failed: {e}")

    def _latest_metrics(self):
        try:
            db = NailDB(DB_FILENAME)
            return db.get_latest()
        except Exception:
            return {}

    def _run(self):
        while not self._stop.is_set():
            self._push()
            self._stop.wait(self.interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", default="nail-01")
    parser.add_argument("--egregore-url", default=None, help="Egregore base URL for heartbeat push")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--interval", type=int, default=30, help="Heartbeat interval seconds")
    parser.add_argument("--db", default=DB_FILENAME)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # Initialize shared DB for handler
    db = NailDB(args.db)
    RequestHandler.db = db
    RequestHandler.node_id = args.node_id

    # Start pusher if egregore URL provided
    pusher = None
    if args.egregore_url:
        pusher = EgregorePusher(args.egregore_url, args.node_id, args.interval)
        pusher.start()
        LOG.info(f"Heartbeat pusher started -> {args.egregore_url}/ingest")

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", args.port), RequestHandler)
    LOG.info(f"Egregore bridge API listening on port {args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if pusher:
            pusher.stop()
        server.server_close()


if __name__ == "__main__":
    main()
