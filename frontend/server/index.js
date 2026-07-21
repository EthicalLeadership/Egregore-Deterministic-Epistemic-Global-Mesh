// Egregore Control Center — Backend API Server
// Pure Node.js HTTP server with zero dependencies

const http = require("http");
const url = require("url");
const store = require("./store");

const PORT = process.env.PORT || 3001;

// CORS headers
function setCORSHeaders(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  res.setHeader("Content-Type", "application/json");
}

// Parse request body
function parseBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk.toString();
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch {
        resolve({});
      }
    });
  });
}

// Send JSON response
function sendJSON(res, statusCode, data) {
  res.statusCode = statusCode;
  res.end(JSON.stringify(data));
}

// Route: GET /api/metrics
function handleMetrics(req, res) {
  store.updateMetrics();
  sendJSON(res, 200, store.metricsStore.current);
}

// Route: GET /api/health
function handleHealth(req, res) {
  sendJSON(res, 200, store.healthStore.current);
}

// Route: GET /api/services/:name/status
function handleServiceStatus(req, res, name) {
  const status = store.getServiceStatus(name);
  if (!status) {
    return sendJSON(res, 404, {
      statusCode: 404,
      error: "Not Found",
      message: `Service '${name}' not found`,
    });
  }
  sendJSON(res, 200, status);
}

// Route: POST /api/services/:name/:action
async function handleServiceAction(req, res, name, action) {
  if (!["start", "stop", "restart"].includes(action)) {
    return sendJSON(res, 400, {
      statusCode: 400,
      error: "Bad Request",
      message: `Invalid action '${action}'. Use: start, stop, restart`,
    });
  }

  try {
    const result = await store.performServiceAction(name, action);
    sendJSON(res, 200, result);
  } catch (err) {
    if (err.message.includes("already")) {
      return sendJSON(res, 409, {
        statusCode: 409,
        error: "Conflict",
        message: err.message,
      });
    }
    if (err.message.includes("not found")) {
      return sendJSON(res, 404, {
        statusCode: 404,
        error: "Not Found",
        message: err.message,
      });
    }
    sendJSON(res, 500, {
      statusCode: 500,
      error: "Internal Server Error",
      message: err.message,
    });
  }
}

// Route: GET /api/logs
function handleLogs(req, res, query) {
  const source = query.source || null;
  const level = query.level || null;
  const tail = query.tail ? parseInt(query.tail, 10) : 500;

  sendJSON(res, 200, store.getLogs({ source, level, tail }));
}

// Route: POST /api/logs/clear
function handleClearLogs(req, res) {
  sendJSON(res, 200, store.clearLogs());
}

// Route: GET /api/dashboard
function handleDashboard(req, res) {
  store.updateMetrics();
  sendJSON(res, 200, store.getDashboard());
}

// Main server
const server = http.createServer(async (req, res) => {
  setCORSHeaders(res);

  // Handle preflight
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }

  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;
  const method = req.method;

  console.log(`${method} ${pathname}`);

  try {
    // GET /api/metrics
    if (pathname === "/api/metrics" && method === "GET") {
      return handleMetrics(req, res);
    }

    // GET /api/health
    if (pathname === "/api/health" && method === "GET") {
      return handleHealth(req, res);
    }

    // GET /api/services/:name/status
    const statusMatch = pathname.match(/^\/api\/services\/([^/]+)\/status$/);
    if (statusMatch && method === "GET") {
      return handleServiceStatus(req, res, statusMatch[1]);
    }

    // POST /api/services/:name/:action
    const actionMatch = pathname.match(/^\/api\/services\/([^/]+)\/([^/]+)$/);
    if (actionMatch && method === "POST") {
      return await handleServiceAction(req, res, actionMatch[1], actionMatch[2]);
    }

    // GET /api/logs
    if (pathname === "/api/logs" && method === "GET") {
      return handleLogs(req, res, parsedUrl.query);
    }

    // POST /api/logs/clear
    if (pathname === "/api/logs/clear" && method === "POST") {
      return handleClearLogs(req, res);
    }

    // GET /api/dashboard
    if (pathname === "/api/dashboard" && method === "GET") {
      return handleDashboard(req, res);
    }

    // 404
    sendJSON(res, 404, {
      statusCode: 404,
      error: "Not Found",
      message: `Route ${method} ${pathname} not found`,
    });
  } catch (err) {
    console.error("Server error:", err);
    sendJSON(res, 500, {
      statusCode: 500,
      error: "Internal Server Error",
      message: err.message,
    });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`🚀 Egregore Control Center API running on port ${PORT}`);
  console.log(`📡 Endpoints:`);
  console.log(`   GET  /api/metrics       — Dashboard metrics`);
  console.log(`   GET  /api/health        — Health status`);
  console.log(`   GET  /api/services/:name/status`);
  console.log(`   POST /api/services/:name/:action  (start/stop/restart)`);
  console.log(`   GET  /api/logs          — System logs`);
  console.log(`   POST /api/logs/clear    — Clear logs`);
  console.log(`   GET  /api/dashboard     — Combined dashboard`);
});
