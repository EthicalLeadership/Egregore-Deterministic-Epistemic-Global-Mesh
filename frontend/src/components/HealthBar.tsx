import { Database, Wifi, Brain, CheckCircle, AlertCircle } from 'lucide-react';

interface HealthCheck {
  name: string;
  status: 'Healthy' | 'Degraded' | 'Unhealthy';
  description: string;
}

interface Props {
  health: {
    status: string;
    checks: HealthCheck[];
  };
}

const icons: Record<string, React.ReactNode> = {
  database: <Database className="h-3 w-3" />,
  rabbitmq: <Wifi className="h-3 w-3" />,
  ollama: <Brain className="h-3 w-3" />,
};

const statusColors = {
  Healthy: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  Degraded: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  Unhealthy: 'text-red-400 bg-red-500/10 border-red-500/20',
};

export default function HealthBar({ health }: Props) {
  const degraded = health.checks.filter(c => c.status !== 'Healthy').length;

  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">Health Checks</h2>
        {degraded === 0 ? (
          <div className="flex items-center gap-1 text-emerald-400">
            <CheckCircle className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">All Healthy</span>
          </div>
        ) : (
          <div className="flex items-center gap-1 text-amber-400">
            <AlertCircle className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">{degraded} degraded</span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {health.checks.map((check) => (
          <div
            key={check.name}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 border ${statusColors[check.status]}`}
            title={check.description}
          >
            <span className="opacity-70">{icons[check.name]}</span>
            <span className="text-[11px] font-medium capitalize">{check.name}</span>
            <span className="text-[10px] opacity-60">{check.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
