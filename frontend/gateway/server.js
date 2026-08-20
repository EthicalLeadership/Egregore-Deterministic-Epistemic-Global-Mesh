import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import { WebSocketServer, WebSocket } from 'ws';
import axios from 'axios';
import { createServer } from 'http';
import { readFileSync } from 'fs';
import { join } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import https from 'https';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ noServer: true });

const PORT = process.env.GATEWAY_PORT || 3000;
const CORE_URL = process.env.CORE_URL || 'http://localhost:8002';
const CONTROL_URL = process.env.CONTROL_URL || 'http://localhost:3001';
// ANCHORUM desktop endpoints are served by the ANCHORUM plain-HTTP site
// (anchorum_http) which mounts the /api/v1/anchorum/* router on :8080.
const ANCHORUM_URL = process.env.ANCHORUM_URL || 'http://localhost:8080';
// Projection-plane bootstrap serves live cells / factory / health + chat WS.
const BOOTSTRAP_URL = process.env.BOOTSTRAP_URL || 'https://localhost:8443';
const BOOTSTRAP_SSL = !/^http:\/\//.test(BOOTSTRAP_URL);

function bootstrapApiKey() {
  if (process.env.EGREGORE_API_KEY) return process.env.EGREGORE_API_KEY;
  try {
    return readFileSync(join(__dirname, '../../secrets/api_key.hex'), 'utf8').trim();
  } catch {
    return '';
  }
}

function anchorumApiKey() {
  if (process.env.EGREGORE_API_KEY) return process.env.EGREGORE_API_KEY;
  try {
    return readFileSync(join(__dirname, '../../secrets/api_key.hex'), 'utf8').trim();
  } catch {
    return '';
  }
}

app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      ...helmet.contentSecurityPolicy.getDefaultDirectives(),
      'upgrade-insecure-requests': null,
    },
  },
}));
app.use(cors({ origin: '*' }));
app.use(morgan('combined'));
app.use(express.json());

// ANCHORUM desktop proxy — filesystem, IMAP consent-gated staging, cases,
// RAG, and tools. Must be registered before the generic /api/* Control
// Center catch-all. File staging of large directories can be slow, so the
// timeout is generous.
app.all('/api/v1/anchorum/*', async (req, res) => {
  try {
    // Use req.originalUrl (path + query string) — /fs/list and other browse
    // endpoints take their target as ?path=<mount>, and req.path alone drops
    // the query, causing FastAPI 422 on the backend.
    const response = await axios({
      method: req.method,
      url: ANCHORUM_URL + req.originalUrl,
      data: req.body,
      headers: {
        'Content-Type': 'application/json',
        ...(anchorumApiKey() ? { 'X-API-Key': anchorumApiKey() } : {}),
      },
      timeout: 600000,
    });
    res.status(response.status).json(response.data);
  } catch (err) {
    res.status(err.response?.status || 502).json({
      error: 'ANCHORUM backend unreachable',
      detail: err.message,
    });
  }
});

// Live projection-plane proxy — cells, factory, nodes health come from the
// bootstrap (8443). Registered before the generic /api/* catch-all.
const BOOTSTRAP_AGENT = BOOTSTRAP_SSL ? new https.Agent({ rejectUnauthorized: false }) : undefined;
const BOOTSTRAP_HEADERS = { 'X-API-Key': bootstrapApiKey(), 'Content-Type': 'application/json' };

app.all(['/api/v1/ombudsman/cells', '/api/v1/ombudsman/cells/*', '/api/v1/factory', '/api/v1/factory/*'], async (req, res) => {
  try {
    const response = await axios({
      method: req.method,
      url: BOOTSTRAP_URL + req.originalUrl,
      data: req.body,
      headers: BOOTSTRAP_HEADERS,
      timeout: 30000,
      httpsAgent: BOOTSTRAP_AGENT,
    });
    res.status(response.status).json(response.data);
  } catch (err) {
    res.status(err.response?.status || 502).json({
      error: 'Projection plane unreachable',
      detail: err.message,
    });
  }
});

// Health probes for the FPI console — delegated to the bootstrap.
app.get('/health/ready', async (_req, res) => {
  try {
    const response = await axios({
      method: 'GET',
      url: BOOTSTRAP_URL + '/health/ready',
      headers: BOOTSTRAP_HEADERS,
      timeout: 5000,
      httpsAgent: BOOTSTRAP_AGENT,
    });
    res.status(response.status).json(response.data);
  } catch (err) {
    res.status(502).json({ status: 'unreachable', error: err.message });
  }
});

app.get('/health/nodes', async (_req, res) => {
  try {
    const response = await axios({
      method: 'GET',
      url: BOOTSTRAP_URL + '/health/nodes',
      headers: BOOTSTRAP_HEADERS,
      timeout: 5000,
      httpsAgent: BOOTSTRAP_AGENT,
    });
    res.status(response.status).json(response.data);
  } catch (err) {
    res.status(502).json({ online_count: 0, total: 0, nodes: [], error: err.message });
  }
});

// Control Center API proxy (orchestrator dashboard backend)
app.all('/api/*', async (req, res) => {
  try {
    const url = CONTROL_URL + req.path;
    const response = await axios({
      method: req.method,
      url,
      data: req.body,
      headers: { 'Content-Type': 'application/json' },
      timeout: 30000,
    });
    res.status(response.status).json(response.data);
  } catch (err) {
    // Fallback: try core backend if control center is down
    try {
      const coreUrl = CORE_URL + req.path.replace('/api', '');
      const coreResponse = await axios({
        method: req.method,
        url: coreUrl,
        data: req.body,
        headers: { 'Content-Type': 'application/json' },
        timeout: 30000,
      });
      res.status(coreResponse.status).json(coreResponse.data);
    } catch (coreErr) {
      res.status(err.response?.status || 502).json({
        error: 'Backend unreachable',
        detail: err.message,
      });
    }
  }
});

// Health check: gateway liveness + backend reachability
app.get('/health', async (req, res) => {
  const checks = {};
  try {
    await axios.get(CONTROL_URL + '/api/health', { timeout: 5000 });
    checks.control_center = 'ok';
  } catch (err) {
    checks.control_center = 'unreachable';
  }
  // The Python core requires an API key even for /health.
  let apiKey = process.env.EGREGORE_API_KEY;
  if (!apiKey) {
    try {
      const fs = await import('fs');
      const path = await import('path');
      const keyPath = path.join(__dirname, '../../secrets/api_key.hex');
      apiKey = fs.readFileSync(keyPath, 'utf8').trim();
    } catch {
      apiKey = '';
    }
  }
  try {
    await axios.get(CORE_URL + '/health', {
      timeout: 5000,
      headers: apiKey ? { 'X-API-Key': apiKey } : {},
    });
    checks.core = 'ok';
  } catch (err) {
    checks.core = err.response ? `http_${err.response.status}` : 'unreachable';
  }
  const healthy = checks.control_center === 'ok' && checks.core === 'ok';
  res.status(healthy ? 200 : 503).json({ status: healthy ? 'ok' : 'degraded', checks });
});

// WebSocket: gateway heartbeat (existing dashboard channel)
wss.on('connection', (ws) => {
  ws.send(JSON.stringify({ type: 'connected', ts: Date.now() }));
  const interval = setInterval(() => {
    ws.send(JSON.stringify({
      type: 'heartbeat',
      ts: Date.now(),
      memory: process.memoryUsage(),
    }));
  }, 5000);
  ws.on('close', () => clearInterval(interval));
});

// WebSocket: chat proxy — forwards /ws/chat/* to the projection-plane
// bootstrap (WSS/WS on 8443). Client messages are relayed verbatim; the
// backend authenticates via the api_key cookie the client carries.
// Uses an upgrade hook (noServer) so any /ws/chat/<session> path matches.
const chatProxyWss = new WebSocketServer({ noServer: true });

function connectChatUpstream(clientWs, request) {
  const targetUrl = BOOTSTRAP_SSL
    ? BOOTSTRAP_URL.replace('https://', 'wss://')
    : BOOTSTRAP_URL.replace('http://', 'ws://');
  const pathname = (request.url || '').split('?')[0];
  const match = pathname.match(/^\/ws\/chat\/([^/]+)/);
  const sessionId = match ? match[1] : 'desktop-' + Math.random().toString(36).slice(2, 10);

  const connectError = (msg) => {
    if (clientWs.readyState === clientWs.OPEN) {
      clientWs.send(JSON.stringify({ type: 'error', error: msg }));
      clientWs.close();
    }
  };

  let upstream;
  const upstreamUrl = targetUrl + '/ws/chat/' + sessionId;
  try {
    upstream = new WebSocket(upstreamUrl, BOOTSTRAP_SSL ? { rejectUnauthorized: false } : undefined);
  } catch (err) {
    return connectError('Chat backend unreachable: ' + err.message);
  }

  upstream.on('open', () => {
    clientWs.on('message', (data) => {
      if (upstream.readyState === upstream.OPEN) upstream.send(data);
    });
  });
  upstream.on('message', (data) => {
    if (clientWs.readyState === clientWs.OPEN) clientWs.send(data);
  });
  upstream.on('close', () => {
    if (clientWs.readyState === clientWs.OPEN) clientWs.close();
  });
  upstream.on('error', (err) => {
    connectError('Chat upstream error: ' + err.message);
  });
  clientWs.on('close', () => {
    if (upstream && upstream.readyState === upstream.OPEN) upstream.close();
  });
}

chatProxyWss.on('connection', connectChatUpstream);

server.on('upgrade', (request, socket, head) => {
  const pathname = (request.url || '').split('?')[0];
  if (pathname.startsWith('/ws/chat/')) {
    chatProxyWss.handleUpgrade(request, socket, head, (ws) => {
      chatProxyWss.emit('connection', ws, request);
    });
  } else if (wss.shouldHandle(request)) {
    wss.handleUpgrade(request, socket, head, (ws) => {
      wss.emit('connection', ws, request);
    });
  } else {
    socket.destroy();
  }
});

// Serve static dashboard (production). Never cache index.html; hashed assets are cacheable.
app.use(
  express.static(join(__dirname, '../dist'), {
    setHeaders: (res, filePath) => {
      if (filePath.endsWith('index.html')) {
        res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
        res.setHeader('Pragma', 'no-cache');
        res.setHeader('Expires', '0');
      }
    },
  })
);

// Fallback to index.html for SPA routes
app.get('*', (req, res) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  res.sendFile(join(__dirname, '../dist/index.html'));
});

server.listen(PORT, () => {
  console.log('Gateway + Dashboard: http://localhost:' + PORT);
  console.log('Control Center proxy: ' + CONTROL_URL);
  console.log('Core proxy: ' + CORE_URL);
});
