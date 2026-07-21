// In-memory data stores and generators for Egregore Control Center

let uptimeCounter = 9234;
let ledgerId = 127;
let requestCount = 0;

// Default service states
const servicesStore = {
  EgregoreOrchestrator: {
    name: "EgregoreOrchestrator",
    status: "Running",
    pid: 4520,
    uptime_seconds: 9234,
  },
  EgregoreBroker: {
    name: "EgregoreBroker",
    status: "Running",
    pid: 4521,
    uptime_seconds: 9234,
  },
  EgregoreAgent: {
    name: "EgregoreAgent",
    status: "Running",
    pid: 4522,
    uptime_seconds: 9234,
  },
};

// Metrics store — compute resources for local AI orchestration
let metricsStore = {
  nodes: { total: 2, active: 2, offline: 0 },
  jobs: {
    queued: 3,
    assigned: 2,
    running: 5,
    completed: 40,
    failed: 5,
    total: 55,
  },
  queue_depth: { work: 3, retry: 0, dlq: 5 },
  compute: {
    cpu_percent: 34,
    memory_percent: 62,
    memory_used_mb: 4892,
    memory_total_mb: 8192,
    gpu_percent: 78,
    gpu_memory_percent: 61,
    gpu_memory_used_mb: 4896,
    gpu_memory_total_mb: 8192,
  },
  inference: {
    active_models: 2,
    tokens_per_sec: 42,
    requests_per_min: 18,
    avg_latency_ms: 245,
  },
  power: {
    gpu_watts: 195,
    system_watts: 340,
    tdp_percent: 78,
  },
  network: {
    inter_node_rx_mbps: 45.2,
    inter_node_tx_mbps: 38.7,
    internet_rx_mbps: 0.4,
    internet_tx_mbps: 0.1,
  },
  uptime_seconds: 9234,
};

// Health store
let healthStore = {
  status: "Healthy",
  checks: [
    { name: "database", status: "Healthy", description: "Database operational" },
    { name: "rabbitmq", status: "Healthy", description: "RabbitMQ operational" },
    { name: "ollama", status: "Healthy", description: "Ollama operational" },
  ],
  degradedTimer: 0,
};

// Log store
const logsStore = [];

const LOG_MESSAGES = {
  EgregoreOrchestrator: [
    "Orchestrator initialized, ready to dispatch jobs",
    "Workflow orchestration started for job {jobId}",
    "Dependency graph built for pipeline {jobId}, {depth} stages",
    "Orchestrator heartbeat, managing {activeNodes} active pipelines",
    "Resource allocation updated, cluster utilization: {gpu}%",
    "Pipeline {jobId} completed orchestration, latency {latency}ms",
    "Scaling decision: adjusting worker pool to {batchSize} instances",
    "Orchestrator rebalancing load across nodes",
    "Checkpoint saved for pipeline {jobId} at stage {retryCount}",
    "Failover triggered for node agent-{nodeId}, rerouting jobs",
  ],
  EgregoreBroker: [
    "Node {nodeId} heartbeat received, latency {latency}ms",
    "Job {jobId} assigned to agent-{nodeId}",
    "Queue depth: {depth} work items, {retry} retry, {dlq} DLQ",
    "Ledger commit #{ledgerId} confirmed, wallet balance: ${balance}",
    "Agent agent-{nodeId} registered with broker",
    "Knowledge token #{tokenId} minted for job {jobId}",
    "Heartbeat timeout for node agent-{nodeId}, marking degraded",
    "Job {jobId} completed successfully, result committed to ledger",
    "Rebalancing queue across {activeNodes} active nodes",
    "Model ollama:{model} loaded on agent-{nodeId}, ready for inference",
  ],
  EgregoreAgent: [
    "Received job {jobId} from broker, starting execution",
    "Inference complete on job {jobId}, confidence: {confidence}%",
    "Memory usage: {memory}MB / {maxMemory}MB",
    "Heartbeat sent to broker, status: active",
    "GPU utilization: {gpu}% on device {deviceId}",
    "Job {jobId} failed, retry attempt {retryCount}/{maxRetries}",
    "Model {model} loaded successfully, warm start",
    "Processing batch of {batchSize} inference requests",
    "Network latency to broker: {latency}ms",
    "Garbage collection triggered, freed {freed}MB",
  ],
};

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomFloat(min, max) {
  return Math.random() * (max - min) + min;
}

function formatMessage(template, source) {
  let msg = template;
  msg = msg.replace(/{nodeId}/g, randomInt(1, 3));
  msg = msg.replace(/{jobId}/g, `job-${randomInt(100, 999)}`);
  msg = msg.replace(/{latency}/g, randomInt(5, 150));
  msg = msg.replace(/{depth}/g, randomInt(1, 10));
  msg = msg.replace(/{retry}/g, randomInt(0, 2));
  msg = msg.replace(/{dlq}/g, randomInt(0, 8));
  msg = msg.replace(/{ledgerId}/g, ledgerId);
  msg = msg.replace(/{balance}/g, (metricsStore.wallet_balance || 0).toFixed(2));
  msg = msg.replace(/{tokenId}/g, randomInt(1000, 9999));
  msg = msg.replace(/{activeNodes}/g, metricsStore.nodes.active);
  msg = msg.replace(/{model}/g, ["llama3.2", "qwen2.5", "mistral", "codellama"][randomInt(0, 3)]);
  msg = msg.replace(/{confidence}/g, randomInt(85, 99));
  msg = msg.replace(/{memory}/g, randomInt(200, 800));
  msg = msg.replace(/{maxMemory}/g, 2048);
  msg = msg.replace(/{gpu}/g, randomInt(30, 95));
  msg = msg.replace(/{deviceId}/g, randomInt(0, 1));
  msg = msg.replace(/{retryCount}/g, randomInt(1, 3));
  msg = msg.replace(/{maxRetries}/g, 3);
  msg = msg.replace(/{batchSize}/g, randomInt(1, 8));
  msg = msg.replace(/{freed}/g, randomInt(50, 200));
  return msg;
}

function getLogLevel() {
  const r = Math.random();
  if (r < 0.05) return "error";
  if (r < 0.2) return "warning";
  if (r < 0.6) return "debug";
  return "info";
}

function generateLogEntry(source) {
  const templates = LOG_MESSAGES[source];
  const template = templates[randomInt(0, templates.length - 1)];
  const level = getLogLevel();
  const now = new Date();
  // Offset by uptimeCounter seconds to simulate running system
  const timestamp = new Date(now.getTime() - randomInt(0, 5000));

  return {
    timestamp: timestamp.toISOString(),
    source,
    level,
    message: formatMessage(template, source),
  };
}

function seedLogs(count = 200) {
  logsStore.length = 0;
  const sources = ["EgregoreOrchestrator", "EgregoreBroker", "EgregoreAgent"];
  for (let i = 0; i < count; i++) {
    const source = sources[i % 3];
    const entry = generateLogEntry(source);
    // Backdate entries
    entry.timestamp = new Date(
      Date.now() - (count - i) * 3000
    ).toISOString();
    logsStore.push(entry);
  }
}

function appendLogEntry() {
  const r = Math.random();
  let source;
  if (r < 0.33) {
    source = "EgregoreOrchestrator";
  } else if (r < 0.66) {
    source = "EgregoreBroker";
  } else {
    source = "EgregoreAgent";
  }
  const entry = generateLogEntry(source);
  logsStore.push(entry);
  // Cap at 2000 entries
  if (logsStore.length > 2000) {
    logsStore.shift();
  }
}

function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

function updateMetrics() {
  requestCount++;
  uptimeCounter += 3;

  // Fluctuate jobs
  const running = randomInt(3, 8);
  const completed = metricsStore.jobs.completed + randomInt(0, 2);
  const failed = metricsStore.jobs.failed + (Math.random() < 0.1 ? 1 : 0);
  const queued = randomInt(1, 8);
  const assigned = randomInt(1, 4);
  const total = queued + assigned + running + completed + failed;

  // Fluctuate compute metrics
  const prevCompute = metricsStore.compute;
  const cpu = clamp(prevCompute.cpu_percent + randomInt(-5, 5), 10, 95);
  const mem = clamp(prevCompute.memory_percent + randomInt(-3, 3), 30, 90);
  const memUsed = Math.round((mem / 100) * prevCompute.memory_total_mb);
  const gpu = clamp(prevCompute.gpu_percent + randomInt(-8, 8), 5, 98);
  const gpuMem = clamp(prevCompute.gpu_memory_percent + randomInt(-5, 5), 20, 85);
  const gpuMemUsed = Math.round((gpuMem / 100) * prevCompute.gpu_memory_total_mb);

  // Fluctuate inference metrics
  const prevInf = metricsStore.inference;
  const tps = clamp(prevInf.tokens_per_sec + randomInt(-5, 5), 15, 85);
  const rpm = clamp(prevInf.requests_per_min + randomInt(-2, 3), 5, 35);
  const lat = clamp(prevInf.avg_latency_ms + randomInt(-20, 20), 120, 500);
  // Check if orchestrator and broker are running
  const orchestratorRunning = servicesStore.EgregoreOrchestrator.status === "Running";
  const brokerRunning = servicesStore.EgregoreBroker.status === "Running";
  const systemActive = orchestratorRunning && brokerRunning;

  const models = brokerRunning ? randomInt(1, 4) : 0;

  metricsStore = {
    nodes: {
      total: systemActive ? 2 : 0,
      active: systemActive ? 2 : 0,
      offline: systemActive ? 0 : 2,
    },
    jobs: {
      queued,
      assigned,
      running: brokerRunning ? running : 0,
      completed: brokerRunning ? completed : metricsStore.jobs.completed,
      failed: brokerRunning ? failed : metricsStore.jobs.failed,
      total: brokerRunning ? total : 0,
    },
    queue_depth: {
      work: brokerRunning ? queued : 0,
      retry: brokerRunning ? randomInt(0, 2) : 0,
      dlq: brokerRunning ? randomInt(3, 8) : 0,
    },
    power: {
      gpu_watts: clamp(gpu * 2.2 + randomInt(-10, 10), 60, 300),
      system_watts: clamp(340 + (cpu / 100) * 100 + randomInt(-15, 15), 120, 450),
      tdp_percent: clamp(Math.round(gpu * 0.85), 15, 95),
    },
    network: {
      inter_node_rx_mbps: parseFloat((brokerRunning ? 30 + Math.random() * 40 : 0).toFixed(1)),
      inter_node_tx_mbps: parseFloat((brokerRunning ? 25 + Math.random() * 35 : 0).toFixed(1)),
      internet_rx_mbps: parseFloat((brokerRunning ? 0.2 + Math.random() * 0.6 : 0).toFixed(1)),
      internet_tx_mbps: parseFloat((brokerRunning ? 0.05 + Math.random() * 0.15 : 0).toFixed(1)),
    },
    compute: {
      cpu_percent: cpu,
      memory_percent: mem,
      memory_used_mb: memUsed,
      memory_total_mb: prevCompute.memory_total_mb,
      gpu_percent: gpu,
      gpu_memory_percent: gpuMem,
      gpu_memory_used_mb: gpuMemUsed,
      gpu_memory_total_mb: prevCompute.gpu_memory_total_mb,
    },
    inference: {
      active_models: models,
      tokens_per_sec: tps,
      requests_per_min: rpm,
      avg_latency_ms: lat,
    },
    uptime_seconds: uptimeCounter,
  };
}

function updateHealth() {
  // 5% chance to degrade
  if (Math.random() < 0.05 && healthStore.degradedTimer === 0) {
    const checkIdx = randomInt(0, healthStore.checks.length - 1);
    healthStore.checks[checkIdx].status = "Degraded";
    healthStore.checks[checkIdx].description =
      "Service experiencing elevated latency";
    healthStore.degradedTimer = randomInt(2, 4);
  }

  // Recover from degradation
  if (healthStore.degradedTimer > 0) {
    healthStore.degradedTimer--;
    if (healthStore.degradedTimer === 0) {
      healthStore.checks.forEach((check) => {
        check.status = "Healthy";
        check.description = `${
          check.name.charAt(0).toUpperCase() + check.name.slice(1)
        } operational`;
      });
    }
  }

  // Determine overall status
  const allHealthy = healthStore.checks.every((c) => c.status === "Healthy");
  healthStore.status = allHealthy ? "Healthy" : "Degraded";
}

function getServicesList() {
  return Object.values(servicesStore);
}

function getServiceStatus(name) {
  return servicesStore[name] || null;
}

async function performServiceAction(name, action) {
  const service = servicesStore[name];
  if (!service) {
    throw new Error(`Service '${name}' not found`);
  }

  // Simulate NSSM operation delay
  await new Promise((resolve) => setTimeout(resolve, 1500));

  if (action === "start") {
    if (service.status === "Running") {
      throw new Error(`Service '${name}' is already running`);
    }
    service.status = "Running";
    service.pid = randomInt(4000, 9999);
    service.uptime_seconds = 0;
  } else if (action === "stop") {
    if (service.status === "Stopped") {
      throw new Error(`Service '${name}' is already stopped`);
    }
    service.status = "Stopped";
    service.pid = null;
  } else if (action === "restart") {
    service.status = "Stopped";
    service.pid = null;
    await new Promise((resolve) => setTimeout(resolve, 500));
    service.status = "Running";
    service.pid = randomInt(4000, 9999);
    service.uptime_seconds = 0;
  } else {
    throw new Error(`Invalid action '${action}'. Use: start, stop, restart`);
  }

  return { success: true, message: `Service ${name} ${action}ed successfully` };
}

function getLogs({ source, level, tail } = {}) {
  let entries = [...logsStore];

  if (source) {
    entries = entries.filter((e) => e.source === source);
  }

  if (level) {
    entries = entries.filter((e) => e.level === level);
  }

  if (tail && tail > 0) {
    entries = entries.slice(-tail);
  }

  return { entries, total: entries.length };
}

function clearLogs() {
  logsStore.length = 0;
  seedLogs(50); // Re-seed with minimal entries
  return { success: true, message: "Logs cleared successfully" };
}

function getDashboard() {
  return {
    metrics: metricsStore,
    health: { ...healthStore, degradedTimer: undefined },
    services: getServicesList(),
    logs_count: logsStore.length,
  };
}

// Initialize
seedLogs(200);

// Start background intervals
setInterval(() => {
  appendLogEntry();
}, 3000);

setInterval(() => {
  updateHealth();
}, 3000);

module.exports = {
  metricsStore: {
    get current() {
      return metricsStore;
    },
  },
  servicesStore: {
    get current() {
      return servicesStore;
    },
  },
  healthStore: {
    get current() {
      return { ...healthStore, degradedTimer: undefined };
    },
  },
  logsStore: {
    get current() {
      return logsStore;
    },
  },
  updateMetrics,
  updateHealth,
  getServicesList,
  getServiceStatus,
  performServiceAction,
  getLogs,
  clearLogs,
  getDashboard,
};
