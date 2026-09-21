import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import { WebSocketServer } from 'ws';
import axios from 'axios';
import { createServer } from 'http';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

const PORT = process.env.GATEWAY_PORT || 3000;
const CORE_URL = process.env.CORE_URL || 'http://localhost:8002';
const CONTROL_URL = process.env.CONTROL_URL || 'http://localhost:3001';

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

// WebSocket
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
