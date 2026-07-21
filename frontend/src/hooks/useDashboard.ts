import { useState, useEffect, useCallback, useRef } from 'react';

const API_BASE = 'http://localhost:3001';

// Fallback mock data when backend is not running
const fallbackServices: ServiceStatus[] = [
  { name: 'EgregoreOrchestrator', status: 'Running', pid: 4520, uptime_seconds: 9240 },
  { name: 'EgregoreBroker', status: 'Running', pid: 4521, uptime_seconds: 9240 },
  { name: 'EgregoreAgent', status: 'Running', pid: 4522, uptime_seconds: 9240 },
];

let fallbackMetrics: Metrics = {
  nodes: { total: 2, active: 2, offline: 0 },
  jobs: { queued: 3, assigned: 2, running: 5, completed: 42, failed: 5, total: 57 },
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
  uptime_seconds: 9240,
};

const fallbackHealth: HealthData = {
  status: 'Healthy',
  checks: [
    { name: 'database', status: 'Healthy', description: 'Database operational' },
    { name: 'rabbitmq', status: 'Healthy', description: 'RabbitMQ operational' },
    { name: 'ollama', status: 'Healthy', description: 'Ollama operational' },
  ],
};

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
    } catch {
      // Backend unreachable — use fallback mock data with slight fluctuations
      fallbackMetrics.uptime_seconds += 3;
      fallbackMetrics.jobs.running = 3 + Math.floor(Math.random() * 6);
      fallbackMetrics.compute.cpu_percent = Math.max(10, Math.min(95, fallbackMetrics.compute.cpu_percent + Math.floor(Math.random() * 11) - 5));
      fallbackMetrics.compute.memory_percent = Math.max(30, Math.min(90, fallbackMetrics.compute.memory_percent + Math.floor(Math.random() * 7) - 3));
      fallbackMetrics.compute.memory_used_mb = Math.round((fallbackMetrics.compute.memory_percent / 100) * fallbackMetrics.compute.memory_total_mb);
      fallbackMetrics.compute.gpu_percent = Math.max(5, Math.min(98, fallbackMetrics.compute.gpu_percent + Math.floor(Math.random() * 17) - 8));
      fallbackMetrics.compute.gpu_memory_percent = Math.max(20, Math.min(85, fallbackMetrics.compute.gpu_memory_percent + Math.floor(Math.random() * 11) - 5));
      fallbackMetrics.compute.gpu_memory_used_mb = Math.round((fallbackMetrics.compute.gpu_memory_percent / 100) * fallbackMetrics.compute.gpu_memory_total_mb);
      fallbackMetrics.inference.tokens_per_sec = Math.max(15, Math.min(85, fallbackMetrics.inference.tokens_per_sec + Math.floor(Math.random() * 11) - 5));
      fallbackMetrics.inference.requests_per_min = Math.max(5, Math.min(35, fallbackMetrics.inference.requests_per_min + Math.floor(Math.random() * 6) - 2));
      fallbackMetrics.inference.avg_latency_ms = Math.max(120, Math.min(500, fallbackMetrics.inference.avg_latency_ms + Math.floor(Math.random() * 41) - 20));
      fallbackMetrics.inference.active_models = Math.floor(Math.random() * 3) + 1;

      fallbackMetrics.power.gpu_watts = Math.max(60, Math.min(300, fallbackMetrics.power.gpu_watts + Math.floor(Math.random() * 21) - 10));
      fallbackMetrics.power.system_watts = Math.max(120, Math.min(450, fallbackMetrics.power.system_watts + Math.floor(Math.random() * 31) - 15));
      fallbackMetrics.power.tdp_percent = Math.max(15, Math.min(95, fallbackMetrics.power.tdp_percent + Math.floor(Math.random() * 7) - 3));

      fallbackMetrics.network.inter_node_rx_mbps = Math.max(0, parseFloat((fallbackMetrics.network.inter_node_rx_mbps + (Math.random() * 8 - 4)).toFixed(1)));
      fallbackMetrics.network.inter_node_tx_mbps = Math.max(0, parseFloat((fallbackMetrics.network.inter_node_tx_mbps + (Math.random() * 8 - 4)).toFixed(1)));
      fallbackMetrics.network.internet_rx_mbps = Math.max(0, parseFloat((fallbackMetrics.network.internet_rx_mbps + (Math.random() * 0.4 - 0.2)).toFixed(1)));
      fallbackMetrics.network.internet_tx_mbps = Math.max(0, parseFloat((fallbackMetrics.network.internet_tx_mbps + (Math.random() * 0.2 - 0.1)).toFixed(1)));

      setServices(fallbackServices);
      setMetrics({ ...fallbackMetrics });
      setHealth(fallbackHealth);
      setIsConnected(false);
      setLoading(false);
    }
  }, []);

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
