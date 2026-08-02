// Egregore Control Center — Real backend data store
//
// Replaces the previous mock/demo implementation with live data:
//   - Service status/actions via systemd --user
//   - CPU / memory / GPU metrics from /proc and nvidia-smi
//   - Logs from the project's log files
//
const { execSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const LOG_DIR = process.env.EGREGORE_LOG_DIR || path.join(__dirname, "../../logs");

const CORE_API_HOST = process.env.EGREGORE_CORE_API_HOST || "127.0.0.1";
const CORE_API_PORT = parseInt(process.env.EGREGORE_CORE_API_PORT || "8002", 10);
const CORE_API_KEY = (() => {
  try {
    return fs.readFileSync(path.join(__dirname, "../../secrets/api_key.hex"), "utf8").trim();
  } catch {
    return "";
  }
})();

// Map dashboard-facing service names to systemd user units.
const SERVICE_MAP = {
  EgregoreOrchestrator: "egregore-core-api.service",
  EgregoreBroker: "egregore-gateway.service",
  EgregoreAgent: "egregore-control-center.service",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function run(cmd, timeout = 5000) {
  try {
    return execSync(cmd, { encoding: "utf8", timeout });
  } catch (err) {
    return err.stdout || "";
  }
}

function parseProcStat() {
  try {
    const data = fs.readFileSync("/proc/stat", "utf8");
    const line = data.split("\n").find((l) => l.startsWith("cpu "));
    if (!line) return null;
    const parts = line.trim().split(/\s+/).map(Number);
    const user = parts[1] || 0;
    const nice = parts[2] || 0;
    const sys = parts[3] || 0;
    const idle = parts[4] || 0;
    const iowait = parts[5] || 0;
    const irq = parts[6] || 0;
    const softirq = parts[7] || 0;
    const steal = parts[8] || 0;
    const total = user + nice + sys + idle + iowait + irq + softirq + steal;
    const active = total - idle - iowait;
    return { total, active };
  } catch {
    return null;
  }
}

let lastCpu = null;

function getCpuPercent() {
  const cur = parseProcStat();
  if (!cur) return 0;
  if (!lastCpu) {
    lastCpu = cur;
    return 0;
  }
  const totalDiff = cur.total - lastCpu.total;
  const activeDiff = cur.active - lastCpu.active;
  lastCpu = cur;
  if (totalDiff <= 0) return 0;
  return Math.round((activeDiff / totalDiff) * 100);
}

function getMemoryInfo() {
  try {
    const data = fs.readFileSync("/proc/meminfo", "utf8");
    const lines = data.split("\n");
    const get = (key) => {
      const line = lines.find((l) => l.startsWith(key));
      return line ? parseInt(line.split(/\s+/)[1], 10) * 1024 : 0; // kB → bytes
    };
    const total = get("MemTotal");
    const available = get("MemAvailable") || get("MemFree");
    const used = total - available;
    return {
      total,
      used,
      percent: total ? Math.round((used / total) * 100) : 0,
    };
  } catch {
    return { total: 0, used: 0, percent: 0 };
  }
}

function getGpuInfo() {
  try {
    const out = run(
      "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits",
      3000
    );
    const [util, memUsed, memTotal] = out.trim().split(",").map((s) => parseFloat(s.trim()));
    if (isNaN(util) || isNaN(memUsed) || isNaN(memTotal)) return null;
    return {
      util: Math.round(util),
      memoryUsed: memUsed,
      memoryTotal: memTotal,
      memoryPercent: memTotal ? Math.round((memUsed / memTotal) * 100) : 0,
    };
  } catch {
    return null;
  }
}

function fetchInferenceMetrics() {
  return new Promise((resolve) => {
    const options = {
      hostname: CORE_API_HOST,
      port: CORE_API_PORT,
      path: "/v1/metrics",
      method: "GET",
      headers: {
        "X-API-Key": CORE_API_KEY,
        Accept: "application/json",
      },
      timeout: 2000,
    };

    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode !== 200) {
          return resolve(null);
        }
        try {
          resolve(JSON.parse(data));
        } catch {
          resolve(null);
        }
      });
    });

    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.end();
  });
}

function getServiceStatus(name) {
  const unit = SERVICE_MAP[name];
  if (!unit) return null;
  try {
    const active = execSync(`systemctl --user is-active ${unit}`, {
      encoding: "utf8",
      timeout: 3000,
    }).trim();
    const status = active === "active" ? "Running" : "Stopped";

    let pid = null;
    let uptimeSeconds = 0;
    try {
      const statusOut = execSync(`systemctl --user show ${unit} --property=MainPID`, {
        encoding: "utf8",
        timeout: 3000,
      });
      pid = parseInt(statusOut.trim().split("=")[1], 10) || null;
      if (pid && fs.existsSync(`/proc/${pid}/stat`)) {
        const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
        // Field 22 is starttime in clock ticks since boot.
        const parts = stat.split(" ");
        // Account for command name containing spaces/parens by finding the closing parenthesis.
        const closeIdx = stat.lastIndexOf(")");
        const after = stat.slice(closeIdx + 2).split(" ");
        const starttime = parseInt(after[19], 10); // field 22 = index 19 after ')' and space
        const clkTck = parseInt(run("getconf CLK_TCK").trim(), 10) || 100;
        const btime = parseInt(
          fs.readFileSync("/proc/stat", "utf8").split("\n").find((l) => l.startsWith("btime")).split(" ")[1],
          10
        );
        const startEpoch = btime + starttime / clkTck;
        uptimeSeconds = Math.round(Date.now() / 1000 - startEpoch);
      }
    } catch {
      // ignore
    }

    return { name, status, pid, uptime_seconds: uptimeSeconds };
  } catch {
    return { name, status: "Stopped", pid: null, uptime_seconds: 0 };
  }
}

async function performServiceAction(name, action) {
  const unit = SERVICE_MAP[name];
  if (!unit) {
    throw new Error(`Service '${name}' not found`);
  }
  if (!["start", "stop", "restart"].includes(action)) {
    throw new Error(`Invalid action '${action}'. Use: start, stop, restart`);
  }

  const status = getServiceStatus(name);
  if (action === "start" && status.status === "Running") {
    throw new Error(`Service '${name}' is already running`);
  }
  if (action === "stop" && status.status === "Stopped") {
    throw new Error(`Service '${name}' is already stopped`);
  }

  execSync(`systemctl --user ${action} ${unit}`, { timeout: 30000 });
  return { success: true, message: `Service ${name} ${action}ed successfully` };
}

function getLogs({ source, level, tail } = {}) {
  const logFiles = {
    EgregoreOrchestrator: "core-api.log",
    EgregoreBroker: "gateway.log",
    EgregoreAgent: "control-center.log",
  };

  const entries = [];
  const maxTail = tail && tail > 0 ? tail : 500;

  Object.entries(logFiles).forEach(([src, file]) => {
    if (source && source !== src) return;
    const filePath = path.join(LOG_DIR, file);
    if (!fs.existsSync(filePath)) return;

    const lines = fs.readFileSync(filePath, "utf8").split("\n").filter(Boolean);
    const tailLines = lines.slice(-maxTail);
    tailLines.forEach((line) => {
      const lvl =
        /error|fail|fatal/i.test(line) ? "error" :
        /warn/i.test(line) ? "warning" :
        /debug/i.test(line) ? "debug" : "info";
      if (level && level !== lvl) return;
      entries.push({
        timestamp: new Date().toISOString(),
        source: src,
        level: lvl,
        message: line.trim(),
      });
    });
  });

  entries.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  return { entries, total: entries.length };
}

function clearLogs() {
  const files = ["core-api.log", "gateway.log", "control-center.log"].map((f) =>
    path.join(LOG_DIR, f)
  );
  files.forEach((f) => {
    if (fs.existsSync(f)) {
      try {
        fs.writeFileSync(f, "");
      } catch {
        // ignore permission issues
      }
    }
  });
  return { success: true, message: "Logs cleared successfully" };
}

// ---------------------------------------------------------------------------
// Metrics / health stores
// ---------------------------------------------------------------------------

let metricsStore = {
  nodes: { total: 1, active: 1, offline: 0 },
  jobs: { queued: 0, assigned: 0, running: 0, completed: 0, failed: 0, total: 0 },
  queue_depth: { work: 0, retry: 0, dlq: 0 },
  compute: {
    cpu_percent: 0,
    memory_percent: 0,
    memory_used_mb: 0,
    memory_total_mb: 0,
    gpu_percent: 0,
    gpu_memory_percent: 0,
    gpu_memory_used_mb: 0,
    gpu_memory_total_mb: 0,
  },
  inference: {
    active_models: 0,
    tokens_per_sec: 0,
    requests_per_min: 0,
    avg_latency_ms: 0,
  },
  power: {
    gpu_watts: 0,
    system_watts: 0,
    tdp_percent: 0,
  },
  network: {
    inter_node_rx_mbps: 0,
    inter_node_tx_mbps: 0,
    internet_rx_mbps: 0,
    internet_tx_mbps: 0,
  },
  uptime_seconds: 0,
};

let healthStore = {
  status: "Healthy",
  checks: [
    { name: "database", status: "Healthy", description: "Database operational" },
    { name: "rabbitmq", status: "Healthy", description: "RabbitMQ operational" },
    { name: "ollama", status: "Healthy", description: "Ollama operational" },
  ],
  degradedTimer: 0,
};

async function updateMetrics() {
  const mem = getMemoryInfo();
  const gpu = getGpuInfo();
  const cpu = getCpuPercent();

  const services = Object.keys(SERVICE_MAP).map(getServiceStatus);
  const runningServices = services.filter((s) => s.status === "Running").length;

  const inference = await fetchInferenceMetrics();

  metricsStore = {
    nodes: {
      total: Object.keys(SERVICE_MAP).length,
      active: runningServices,
      offline: Object.keys(SERVICE_MAP).length - runningServices,
    },
    jobs: {
      queued: 0,
      assigned: 0,
      running: runningServices,
      completed: inference ? inference.requests_total : 0,
      failed: inference ? inference.errors_total : 0,
      total: runningServices + (inference ? inference.requests_total : 0),
    },
    queue_depth: { work: 0, retry: 0, dlq: 0 },
    compute: {
      cpu_percent: cpu,
      memory_percent: mem.percent,
      memory_used_mb: Math.round(mem.used / 1024 / 1024),
      memory_total_mb: Math.round(mem.total / 1024 / 1024),
      gpu_percent: gpu ? gpu.util : 0,
      gpu_memory_percent: gpu ? gpu.memoryPercent : 0,
      gpu_memory_used_mb: gpu ? Math.round(gpu.memoryUsed) : 0,
      gpu_memory_total_mb: gpu ? Math.round(gpu.memoryTotal) : 0,
    },
    inference: {
      active_models: runningServices > 0 ? 1 : 0,
      tokens_per_sec: inference ? inference.tokens_per_sec : 0,
      requests_per_min: inference ? inference.requests_per_min : 0,
      avg_latency_ms: inference ? inference.avg_latency_ms : 0,
      p50_latency_ms: inference ? inference.p50_latency_ms : 0,
      p95_latency_ms: inference ? inference.p95_latency_ms : 0,
      error_rate: inference ? inference.error_rate : 0,
      requests_total: inference ? inference.requests_total : 0,
      errors_total: inference ? inference.errors_total : 0,
    },
    power: {
      gpu_watts: gpu ? Math.round(gpu.util * 2.5) : 0,
      system_watts: 0,
      tdp_percent: gpu ? gpu.util : 0,
    },
    network: {
      inter_node_rx_mbps: 0,
      inter_node_tx_mbps: 0,
      internet_rx_mbps: 0,
      internet_tx_mbps: 0,
    },
    uptime_seconds: Math.floor(process.uptime()),
  };
}

function updateHealth() {
  const services = Object.keys(SERVICE_MAP).map(getServiceStatus);
  const allRunning = services.every((s) => s.status === "Running");

  healthStore = {
    status: allRunning ? "Healthy" : "Degraded",
    checks: services.map((s) => ({
      name: s.name,
      status: s.status === "Running" ? "Healthy" : "Degraded",
      description:
        s.status === "Running"
          ? `${s.name} is running`
          : `${s.name} is not running`,
    })),
  };
}

async function getDashboard() {
  await updateMetrics();
  updateHealth();
  return {
    metrics: metricsStore,
    health: healthStore,
    services: Object.keys(SERVICE_MAP).map(getServiceStatus),
    logs_count: getLogs({ tail: 1 }).total,
  };
}

module.exports = {
  metricsStore: { get current() { return metricsStore; } },
  healthStore: { get current() { updateHealth(); return healthStore; } },
  getServicesList: () => Object.keys(SERVICE_MAP).map(getServiceStatus),
  getServiceStatus,
  performServiceAction,
  getLogs,
  clearLogs,
  getDashboard,
  updateMetrics,
  updateHealth,
};
