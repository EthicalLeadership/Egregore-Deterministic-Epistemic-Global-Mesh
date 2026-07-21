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

app.use(helmet());
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

// Serve static dashboard (production)
app.use(express.static(join(__dirname, '../dist')));

// Fallback to index.html for SPA routes
app.get('*', (req, res) => {
  res.sendFile(join(__dirname, '../dist/index.html'));
});

server.listen(PORT, () => {
  console.log('Gateway + Dashboard: http://localhost:' + PORT);
  console.log('Control Center proxy: ' + CONTROL_URL);
  console.log('Core proxy: ' + CORE_URL);
});
