import { useState, useEffect, useCallback, useRef } from 'react';

const API_BASE = ''; // relative paths proxied through the gateway

function formatError(err: unknown): string {
  return err instanceof Error ? err.message : 'Backend unreachable';
}

export interface ServiceStatus {
  name: string;
  status: 'Running' | 'Stopped' | 'Error';
  pid: number | null;
  uptime_seconds: number;
}

export interface Metrics {
  nodes: { total: number; active: number; offline: number };
  jobs: {
    queued: number;
    assigned: number;
    running: number;
    completed: number;
    failed: number;
    total: number;
  };
  queue_depth: { work: number; retry: number; dlq: number };
  compute: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_mb: number;
    memory_total_mb: number;
    gpu_percent: number;
    gpu_memory_percent: number;
    gpu_memory_used_mb: number;
    gpu_memory_total_mb: number;
  };
  inference: {
    active_models: number;
    tokens_per_sec: number;
    requests_per_min: number;
    avg_latency_ms: number;
    p50_latency_ms?: number;
    p95_latency_ms?: number;
    error_rate?: number;
    requests_total?: number;
    errors_total?: number;
  };
  power: {
    gpu_watts: number;
    system_watts: number;
    tdp_percent: number;
  };
  network: {
    inter_node_rx_mbps: number;
    inter_node_tx_mbps: number;
    internet_rx_mbps: number;
    internet_tx_mbps: number;
  };
  uptime_seconds: number;
}

export interface HealthCheck {
  name: string;
  status: 'Healthy' | 'Degraded' | 'Unhealthy';
  description: string;
}

export interface HealthData {
  status: string;
  checks: HealthCheck[];
}

export interface Toast {
  id: string;
  message: string;
  type: 'success' | 'error' | 'info' | 'warning';
}

export function useDashboard() {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingActions, setLoadingActions] = useState<Record<string, boolean>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [isConnected, setIsConnected] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const addToast = useCallback((message: string, type: Toast['type']) => {
    const id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const fetchDashboard = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/dashboard`);
      if (!response.ok) throw new Error('Failed to fetch dashboard');
      const data = await response.json();

      setServices(data.services || []);
      setMetrics(data.metrics || null);
      setHealth(data.health || null);
      setIsConnected(true);
      setLoading(false);
    } catch (err) {
      // Backend unreachable — surface the error instead of showing demo data
      addToast(`Dashboard disconnected: ${formatError(err)}`, 'error');
      setIsConnected(false);
      setLoading(false);
    }
  }, [addToast]);

  const performServiceAction = useCallback(
    async (name: string, action: 'start' | 'stop' | 'restart') => {
      setLoadingActions((prev) => ({ ...prev, [name]: true }));

      try {
        const response = await fetch(
          `${API_BASE}/api/services/${name}/${action}`,
          { method: 'POST' }
        );
        const data = await response.json();

        if (response.ok) {
          addToast(data.message, 'success');
          // Refresh dashboard after action
          setTimeout(fetchDashboard, 500);
        } else {
          addToast(data.message || `Failed to ${action} ${name}`, 'error');
        }
      } catch (err) {
        console.error(`Service action error:`, err);
        addToast(`Network error: Could not ${action} ${name}`, 'error');
      } finally {
        setLoadingActions((prev) => ({ ...prev, [name]: false }));
      }
    },
    [fetchDashboard, addToast]
  );

  // Start polling
  useEffect(() => {
    fetchDashboard();
    intervalRef.current = setInterval(fetchDashboard, 3000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchDashboard]);

  return {
    services,
    metrics,
    health,
    loading,
    loadingActions,
    toasts,
    isConnected,
    performServiceAction,
    removeToast,
    addToast,
    refresh: fetchDashboard,
  };
}
