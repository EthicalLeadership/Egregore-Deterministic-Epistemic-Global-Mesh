import { Play, Square, RotateCcw, Loader2 } from 'lucide-react';

interface Service {
  name: string;
  status: 'Running' | 'Stopped' | 'Error';
  pid: number | null;
}

interface Props {
  service: Service;
  loading: boolean;
  onAction: (name: string, action: 'start' | 'stop' | 'restart') => void;
}

const statusConfig = {
  Running: { dot: 'status-running', text: 'text-emerald-400', label: 'Running' },
  Stopped: { dot: 'status-stopped', text: 'text-slate-400', label: 'Stopped' },
  Error:   { dot: 'status-error',   text: 'text-red-400',    label: 'Error' },
};

export default function ServiceCard({ service, loading, onAction }: Props) {
  const cfg = statusConfig[service.status];
  const isRunning = service.status === 'Running';
  const displayName = service.name.replace('Egregore', '');

  return (
    <div className="panel p-4 panel-hover transition-colors">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`status-dot ${cfg.dot}`} />
          <span className="text-sm font-medium text-white">{displayName}</span>
        </div>
        {service.pid && (
          <span className="font-mono text-[10px] text-slate-600">PID {service.pid}</span>
        )}
      </div>

      {/* Status */}
      <div className={`text-xs font-medium mb-4 ${cfg.text}`}>
        {cfg.label}
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={() => onAction(service.name, 'start')}
          disabled={isRunning || loading}
          className="btn-primary flex-1 !text-[11px]"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
          Start
        </button>
        <button
          onClick={() => onAction(service.name, 'stop')}
          disabled={!isRunning || loading}
          className="btn-danger flex-1 !text-[11px]"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
          Stop
        </button>
        <button
          onClick={() => onAction(service.name, 'restart')}
          disabled={loading}
          className="btn-secondary flex-1 !text-[11px]"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
          Restart
        </button>
      </div>
    </div>
  );
}
