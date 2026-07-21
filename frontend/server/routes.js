// Fastify route handlers for Egregore Control Center API

const store = require("./store");

async function routes(fastify, options) {
  // GET /api/metrics
  fastify.get("/api/metrics", async (request, reply) => {
    store.updateMetrics();
    return store.metricsStore.current;
  });

  // GET /api/health
  fastify.get("/api/health", async (request, reply) => {
    return store.healthStore.current;
  });

  // GET /api/services/:name/status
  fastify.get("/api/services/:name/status", async (request, reply) => {
    const { name } = request.params;
    const status = store.getServiceStatus(name);

    if (!status) {
      reply.code(404);
      return { statusCode: 404, error: "Not Found", message: `Service '${name}' not found` };
    }

    return status;
  });

  // POST /api/services/:name/:action
  fastify.post("/api/services/:name/:action", async (request, reply) => {
    const { name, action } = request.params;

    if (!["start", "stop", "restart"].includes(action)) {
      reply.code(400);
      return {
        statusCode: 400,
        error: "Bad Request",
        message: `Invalid action '${action}'. Use: start, stop, restart`,
      };
    }

    try {
      const result = await store.performServiceAction(name, action);
      return result;
    } catch (err) {
      if (err.message.includes("already")) {
        reply.code(409);
        return { statusCode: 409, error: "Conflict", message: err.message };
      }
      if (err.message.includes("not found")) {
        reply.code(404);
        return { statusCode: 404, error: "Not Found", message: err.message };
      }
      reply.code(500);
      return { statusCode: 500, error: "Internal Server Error", message: err.message };
    }
  });

  // GET /api/logs
  fastify.get("/api/logs", async (request, reply) => {
    const { source, level, tail } = request.query;
    const tailNum = tail ? parseInt(tail, 10) : 500;

    return store.getLogs({
      source,
      level,
      tail: tailNum,
    });
  });

  // POST /api/logs/clear
  fastify.post("/api/logs/clear", async (request, reply) => {
    return store.clearLogs();
  });

  // GET /api/dashboard
  fastify.get("/api/dashboard", async (request, reply) => {
    store.updateMetrics();
    return store.getDashboard();
  });
}

module.exports = routes;
