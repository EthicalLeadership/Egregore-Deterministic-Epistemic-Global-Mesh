import { Cpu, HardDrive, Zap, Box, Gauge, Clock, Battery, Activity, ArrowDown, ArrowUp } from 'lucide-react';

interface Metrics {
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
  jobs: { running: number };
}

interface Props {
  metrics: Metrics;
}

function formatMem(mb: number) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

function formatUptime(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

const colorForPercent = (pct: number) => {
  if (pct < 50) return 'text-emerald-400';
  if (pct < 80) return 'text-amber-400';
  return 'text-red-400';
};

const barColor = (pct: number) => {
  if (pct < 50) return '#10b981';
  if (pct < 80) return '#f59e0b';
  return '#ef4444';
};

function MiniBar({ pct }: { pct: number }) {
  return (
    <div className="mt-3 h-1.5 w-full rounded-full bg-[#1e2129] overflow-hidden">
      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: barColor(pct) }} />
    </div>
  );
}

export default function MetricsGrid({ metrics }: Props) {
  const c = metrics.compute;
  const inf = metrics.inference;
  const p = metrics.power;
  const n = metrics.network;

  const computeCards = [
    {
      icon: <Cpu className="h-4 w-4 text-slate-400" />, label: 'CPU Usage',
      value: `${c.cpu_percent}%`, sub: `${metrics.jobs.running} jobs active`,
      color: colorForPercent(c.cpu_percent), bar: c.cpu_percent,
    },
    {
      icon: <HardDrive className="h-4 w-4 text-slate-400" />, label: 'Memory',
      value: `${c.memory_percent}%`, sub: `${formatMem(c.memory_used_mb)} / ${formatMem(c.memory_total_mb)}`,
      color: colorForPercent(c.memory_percent), bar: c.memory_percent,
    },
    {
      icon: <Zap className="h-4 w-4 text-violet-400" />, label: 'GPU Compute',
      value: `${c.gpu_percent}%`, sub: `${formatMem(c.gpu_memory_used_mb)} VRAM used`,
      color: colorForPercent(c.gpu_percent), bar: c.gpu_percent,
    },
    {
      icon: <Box className="h-4 w-4 text-blue-400" />, label: 'Models Loaded',
      value: inf.active_models,
      sub: `${inf.tokens_per_sec.toFixed(1)} tok/s • ${inf.requests_total ?? 0} reqs`,
      color: 'text-white',
    },
    {
      icon: <Gauge className="h-4 w-4 text-amber-400" />, label: 'Inference',
      value: `${inf.requests_per_min.toFixed(1)}/min`,
      sub: `${inf.avg_latency_ms.toFixed(0)}ms avg • p95 ${(inf.p95_latency_ms ?? 0).toFixed(0)}ms`,
      color: 'text-white',
    },
    {
      icon: <Clock className="h-4 w-4 text-slate-400" />, label: 'Uptime',
      value: formatUptime(metrics.uptime_seconds), sub: 'Since startup', color: 'text-white',
    },
  ];

  const powerCards = [
    {
      icon: <Zap className="h-4 w-4 text-yellow-400" />, label: 'GPU Power',
      value: `${p.gpu_watts}W`, sub: `${p.tdp_percent}% TDP`,
      color: colorForPercent(p.tdp_percent), bar: p.tdp_percent,
    },
    {
      icon: <Battery className="h-4 w-4 text-emerald-400" />, label: 'System Power',
      value: `${p.system_watts}W`, sub: 'Total draw',
      color: 'text-white',
    },
  ];

  const networkCards = [
    {
      icon: <Activity className="h-4 w-4 text-blue-400" />, label: 'Node Traffic RX',
      value: `${n.inter_node_rx_mbps.toFixed(1)} Mbps`, sub: 'Inter-node inbound',
      color: 'text-blue-400',
    },
    {
      icon: <Activity className="h-4 w-4 text-violet-400" />, label: 'Node Traffic TX',
      value: `${n.inter_node_tx_mbps.toFixed(1)} Mbps`, sub: 'Inter-node outbound',
      color: 'text-violet-400',
    },
    {
      icon: <ArrowDown className="h-4 w-4 text-slate-400" />, label: 'Internet RX',
      value: `${n.internet_rx_mbps.toFixed(1)} Mbps`, sub: 'External download',
      color: 'text-slate-400',
    },
    {
      icon: <ArrowUp className="h-4 w-4 text-slate-400" />, label: 'Internet TX',
      value: `${n.internet_tx_mbps.toFixed(1)} Mbps`, sub: 'External upload',
      color: 'text-slate-400',
    },
  ];

  return (
    <div className="mb-6 space-y-5">
      {/* Compute */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">Compute</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {computeCards.map((card) => (
            <div key={card.label} className="panel p-4 panel-hover transition-colors">
              <div className="flex items-center gap-1.5 mb-3">
                {card.icon}
                <span className="metric-label">{card.label}</span>
              </div>
              <div className={`metric-value ${card.color}`}>{card.value}</div>
              <div className="mt-1 text-[11px] text-slate-500">{card.sub}</div>
              {card.bar !== undefined && <MiniBar pct={card.bar} />}
            </div>
          ))}
        </div>
      </div>

      {/* Power */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">Power</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          {powerCards.map((card) => (
            <div key={card.label} className="panel p-4 panel-hover transition-colors">
              <div className="flex items-center gap-1.5 mb-3">
                {card.icon}
                <span className="metric-label">{card.label}</span>
              </div>
              <div className={`metric-value ${card.color}`}>{card.value}</div>
              <div className="mt-1 text-[11px] text-slate-500">{card.sub}</div>
              {card.bar !== undefined && <MiniBar pct={card.bar} />}
            </div>
          ))}
        </div>
      </div>

      {/* Network */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">Network</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          {networkCards.map((card) => (
            <div key={card.label} className="panel p-4 panel-hover transition-colors">
              <div className="flex items-center gap-1.5 mb-3">
                {card.icon}
                <span className="metric-label">{card.label}</span>
              </div>
              <div className={`metric-value ${card.color}`}>{card.value}</div>
              <div className="mt-1 text-[11px] text-slate-500">{card.sub}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
