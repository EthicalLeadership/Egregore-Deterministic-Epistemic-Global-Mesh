/**
 * Live Egregore data hook — replaces the FPI synthetic data with real
 * dashboard/control-center/ombudsman endpoints proxied through the gateway.
 * Polls on a fixed cadence; exposes connection state and last error.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { z } from 'zod';

export interface LiveNode {
  node_id: string;
  status: string;
  host: string;
  port: number;
  healthy: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface LiveHealth {
  status: string;
  plane: string;
  timestamp: number;
  checks: Record<string, unknown>;
}

export interface LiveCell {
  cell_id: string;
  status: string;
  load_index?: number;
  [key: string]: unknown;
}

export interface LiveService {
  name: string;
  status: 'Running' | 'Stopped' | 'Error';
  pid: number | null;
  uptime_seconds: number;
}

export interface LiveMetrics {
  nodes: { total: number; active: number; offline: number };
  jobs: {
    queued: number; assigned: number; running: number;
    completed: number; failed: number; total: number;
  };
  compute: {
    cpu_percent: number; memory_percent: number;
    memory_used_mb: number; memory_total_mb: number;
    gpu_percent: number; gpu_memory_percent: number;
    gpu_memory_used_mb: number; gpu_memory_total_mb: number;
  };
  inference: {
    active_models: number; tokens_per_sec: number; requests_per_min: number;
    avg_latency_ms: number; p50_latency_ms?: number; p95_latency_ms?: number;
    error_rate?: number; requests_total?: number; errors_total?: number;
  };
  power: { gpu_watts: number; system_watts: number; tdp_percent: number };
  network: {
    inter_node_rx_mbps: number; inter_node_tx_mbps: number;
    internet_rx_mbps: number; internet_tx_mbps: number;
  };
  uptime_seconds: number;
}

export interface LiveState {
  nodes: LiveNode[];
  health: LiveHealth | null;
  services: LiveService[];
  metrics: LiveMetrics | null;
  cells: LiveCell[];
  connected: boolean;
  lastError: string | null;
  refresh: () => void;
}

const liveNodeSchema = z.object({
  node_id: z.string(),
  status: z.string(),
  host: z.string(),
  port: z.number(),
  healthy: z.boolean(),
  latency_ms: z.number().nullable().default(null),
  error: z.string().nullable().default(null),
}).passthrough();

const liveHealthSchema = z.object({
  status: z.string(),
  plane: z.string(),
  timestamp: z.number(),
  checks: z.record(z.string(), z.unknown()).default({}),
}).passthrough();

const liveServiceSchema = z.object({
  name: z.string(),
  status: z.enum(['Running', 'Stopped', 'Error']),
  pid: z.number().nullable().default(null),
  uptime_seconds: z.number(),
}).passthrough();

const liveMetricsSchema = z.object({
  nodes: z.object({
    total: z.number(),
    active: z.number(),
    offline: z.number(),
  }),
  jobs: z.object({
    queued: z.number(),
    assigned: z.number(),
    running: z.number(),
    completed: z.number(),
    failed: z.number(),
    total: z.number(),
  }),
  compute: z.object({
    cpu_percent: z.number(),
    memory_percent: z.number(),
    memory_used_mb: z.number(),
    memory_total_mb: z.number(),
    gpu_percent: z.number(),
    gpu_memory_percent: z.number(),
    gpu_memory_used_mb: z.number(),
    gpu_memory_total_mb: z.number(),
  }),
  inference: z.object({
    active_models: z.number(),
    tokens_per_sec: z.number(),
    requests_per_min: z.number(),
    avg_latency_ms: z.number(),
    p50_latency_ms: z.number().optional(),
    p95_latency_ms: z.number().optional(),
    error_rate: z.number().optional(),
    requests_total: z.number().optional(),
    errors_total: z.number().optional(),
  }),
  power: z.object({
    gpu_watts: z.number(),
    system_watts: z.number(),
    tdp_percent: z.number(),
  }),
  network: z.object({
    inter_node_rx_mbps: z.number(),
    inter_node_tx_mbps: z.number(),
    internet_rx_mbps: z.number(),
    internet_tx_mbps: z.number(),
  }),
  uptime_seconds: z.number(),
}).passthrough();

const liveCellSchema = z.object({
  cell_id: z.string(),
  status: z.string(),
  load_index: z.number().optional(),
}).passthrough();

const nodesResponseSchema = z.object({
  nodes: z.array(liveNodeSchema).default([]),
});

const cellsResponseSchema = z.object({
  cells: z.array(liveCellSchema).default([]),
});

const dashboardResponseSchema = z.object({
  services: z.array(liveServiceSchema).default([]),
  metrics: liveMetricsSchema.nullable().optional(),
  health: z.unknown().optional(),
});

type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };

async function getJson<T>(
  path: string,
  signal: AbortSignal,
  schema?: z.ZodType<T>,
): Promise<ApiResult<T>> {
  try {
    const res = await fetch(path, {
      headers: { Accept: 'application/json' },
      signal,
      cache: 'no-store',
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      return {
        ok: false,
        error: `${path}: HTTP ${res.status}${detail ? ` ${detail}` : ''}`,
      };
    }

    const text = await res.text();
    if (!text) {
      return { ok: false, error: `${path}: empty response` };
    }

    const raw = JSON.parse(text) as unknown;
    if (schema) {
      const parsed = schema.safeParse(raw);
      if (!parsed.success) {
        return {
          ok: false,
          error: `${path}: invalid payload: ${parsed.error.message}`,
        };
      }
      return { ok: true, data: parsed.data };
    }

    return { ok: true, data: raw as T };
  } catch (err) {
    if (signal.aborted) {
      return { ok: false, error: `${path}: aborted` };
    }
    return {
      ok: false,
      error: `${path}: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

export function useLiveEgregore(intervalMs = 3000): LiveState {
  const [nodes, setNodes] = useState<LiveNode[]>([]);
  const [health, setHealth] = useState<LiveHealth | null>(null);
  const [services, setServices] = useState<LiveService[]>([]);
  const [metrics, setMetrics] = useState<LiveMetrics | null>(null);
  const [cells, setCells] = useState<LiveCell[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const generation = ++generationRef.current;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const timeoutId = setTimeout(() => controller.abort(), 5000);

    const [nodesR, healthR, dashR, cellsR] = await Promise.all([
      getJson('/health/nodes', controller.signal, nodesResponseSchema),
      getJson('/health/ready', controller.signal, liveHealthSchema),
      getJson('/api/dashboard', controller.signal, dashboardResponseSchema),
      getJson('/api/v1/ombudsman/cells', controller.signal, cellsResponseSchema),
    ]);

    clearTimeout(timeoutId);

    if (generation !== generationRef.current || !mountedRef.current) return;

    if (nodesR.ok) setNodes(nodesR.data.nodes ?? []);
    if (healthR.ok) setHealth(healthR.data);
    if (dashR.ok) {
      setServices(dashR.data.services ?? []);
      setMetrics(dashR.data.metrics ?? initialMetrics());
    }
    if (cellsR.ok) setCells(cellsR.data.cells ?? []);

    const gatewayOk = nodesR.ok || dashR.ok;
    const errors: string[] = [];
    if (!nodesR.ok) errors.push(nodesR.error);
    if (!healthR.ok) errors.push(healthR.error);
    if (!dashR.ok) errors.push(dashR.error);
    if (!cellsR.ok) errors.push(cellsR.error);

    setConnected(gatewayOk);
    setLastError(
      gatewayOk
        ? errors.length
          ? errors.join('; ')
          : null
        : 'All gateway endpoints failed',
    );
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    generationRef.current++;

    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (!mountedRef.current) return;
      try {
        await refresh();
      } catch (error) {
        console.error('[useLiveEgregore] refresh failed', error);
      } finally {
        if (!mountedRef.current) return;
        timer = setTimeout(tick, intervalMs);
      }
    };

    void tick();

    return () => {
      mountedRef.current = false;
      generationRef.current++;
      abortRef.current?.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  return { nodes, health, services, metrics, cells, connected, lastError, refresh };
}

function initialMetrics(): LiveMetrics {
  return {
    nodes: { total: 0, active: 0, offline: 0 },
    jobs: { queued: 0, assigned: 0, running: 0, completed: 0, failed: 0, total: 0 },
    compute: {
      cpu_percent: 0, memory_percent: 0, memory_used_mb: 0, memory_total_mb: 0,
      gpu_percent: 0, gpu_memory_percent: 0, gpu_memory_used_mb: 0, gpu_memory_total_mb: 0,
    },
    inference: {
      active_models: 0, tokens_per_sec: 0, requests_per_min: 0, avg_latency_ms: 0,
    },
    power: { gpu_watts: 0, system_watts: 0, tdp_percent: 0 },
    network: {
      inter_node_rx_mbps: 0, inter_node_tx_mbps: 0,
      internet_rx_mbps: 0, internet_tx_mbps: 0,
    },
    uptime_seconds: 0,
  };
}
