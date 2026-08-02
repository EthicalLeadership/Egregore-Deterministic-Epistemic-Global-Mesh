import { useState, useEffect, useRef } from 'react';
import { X, RefreshCw, Trash2, ScrollText } from 'lucide-react';

interface LogEntry {
  timestamp: string;
  source: string;
  level: 'info' | 'debug' | 'warning' | 'error';
  message: string;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const API_BASE = ''; // relative paths proxied through the gateway

const levelColors: Record<string, string> = {
  error: 'text-red-400',
  warning: 'text-amber-400',
  debug: 'text-slate-500',
  info: 'text-slate-300',
};

const levelBg: Record<string, string> = {
  error: 'bg-red-500/10',
  warning: 'bg-amber-500/10',
  debug: 'bg-slate-500/10',
  info: 'bg-slate-500/5',
};

export default function LogViewer({ isOpen, onClose }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [source, setSource] = useState('');
  const [level, setLevel] = useState('');
  const [tailMode, setTailMode] = useState(true);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (source) params.append('source', source);
      if (level) params.append('level', level);
      params.append('tail', '500');
      const res = await fetch(`${API_BASE}/api/logs?${params}`);
      const data = await res.json();
      setLogs(data.entries || []);
    } catch (err) {
      // Backend unreachable — show empty with a console warning
      console.error('Failed to fetch logs:', err);
      setLogs([]);
    } finally {
      setLoading(false);
    }
  };

  const clearLogs = async () => {
    try {
      await fetch(`${API_BASE}/api/logs/clear`, { method: 'POST' });
      setLogs([]);
    } catch {
      // silent
    }
  };

  useEffect(() => {
    if (tailMode && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, tailMode]);

  useEffect(() => {
    if (!isOpen) return;
    fetchLogs();
    const iv = setInterval(fetchLogs, 3000);
    return () => clearInterval(iv);
  }, [isOpen, source, level]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    if (isOpen) document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100]">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="absolute right-0 top-0 bottom-0 flex w-[600px] max-w-full flex-col bg-[#0f1117] border-l border-[#1e2129]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2129]">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-blue-400" />
            <span className="text-sm font-medium text-white">System Logs</span>
            <span className="text-[10px] text-slate-500 font-mono">{logs.length} entries</span>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={source}
              onChange={e => setSource(e.target.value)}
              className="rounded bg-[#161922] border border-[#1e2129] px-2 py-1 text-[11px] text-slate-300 outline-none focus:border-blue-500"
            >
              <option value="">All Sources</option>
              <option value="EgregoreOrchestrator">Orchestrator</option>
              <option value="EgregoreBroker">Broker</option>
              <option value="EgregoreAgent">Agent</option>
            </select>
            <select
              value={level}
              onChange={e => setLevel(e.target.value)}
              className="rounded bg-[#161922] border border-[#1e2129] px-2 py-1 text-[11px] text-slate-300 outline-none focus:border-blue-500"
            >
              <option value="">All Levels</option>
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
            <button
              onClick={() => setTailMode(!tailMode)}
              className={`rounded px-2 py-1 text-[11px] font-medium border transition-colors ${
                tailMode
                  ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                  : 'bg-[#161922] border-[#1e2129] text-slate-500'
              }`}
            >
              Tail
            </button>
            <button onClick={fetchLogs} className="btn-secondary !p-1.5">
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={onClose} className="btn-secondary !p-1.5">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Logs */}
        <div className="flex-1 overflow-y-auto p-2 font-mono text-[12px] leading-5">
          {logs.length === 0 ? (
            <div className="flex h-full items-center justify-center text-slate-600 text-xs">No logs</div>
          ) : (
            logs.map((entry, i) => (
              <div
                key={i}
                className={`flex gap-2 px-2 py-0.5 rounded hover:bg-[#1a1d26] ${levelColors[entry.level]}`}
              >
                <span className="shrink-0 text-slate-600 tabular-nums">
                  {new Date(entry.timestamp).toLocaleTimeString('en-US', { hour12: false })}
                </span>
                <span className={`shrink-0 rounded px-1 text-[10px] font-semibold uppercase ${levelBg[entry.level]} ${levelColors[entry.level]}`}>
                  {entry.level}
                </span>
                <span className="shrink-0 text-slate-600">[{entry.source.replace('Egregore', '')}]</span>
                <span className="break-all">{entry.message}</span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-[#1e2129]">
          <span className="text-[10px] text-slate-600 font-mono">/var/log/egregore/*.log</span>
          <button onClick={clearLogs} className="btn-danger !text-[11px]">
            <Trash2 className="h-3 w-3" />
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}
