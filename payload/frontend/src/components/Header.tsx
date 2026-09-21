import { useState, useEffect } from 'react';
import { FileText, Settings } from 'lucide-react';

interface HeaderProps {
  onOpenLogs: () => void;
  onOpenSettings: () => void;
  isConnected: boolean;
}

export default function Header({ onOpenLogs, onOpenSettings, isConnected }: HeaderProps) {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-[#1e2129] bg-[#0f1117]/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1440px] items-center justify-between px-6">
        {/* Logo */}
        <div className="flex items-center gap-2.5">
          <img src="/images/egregore-logo.png" alt="" className="h-6 w-6" />
          <span className="text-sm font-semibold text-white">Egregore</span>
          <span className="text-sm text-slate-500">Control Center</span>
        </div>

        {/* Right */}
        <div className="flex items-center gap-3">
          {/* Connection */}
          <div className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border ${isConnected ? 'bg-[#161922] border-[#1e2129]' : 'bg-amber-500/10 border-amber-500/20'}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-amber-400'}`} />
            <span className={`text-[11px] font-medium ${isConnected ? 'text-slate-400' : 'text-amber-400'}`}>
              {isConnected ? 'Connected' : 'Disconnected'}
            </span>
          </div>

          {/* Clock */}
          <span className="font-mono text-xs text-slate-500 tabular-nums">
            {time.toLocaleTimeString('en-US', { hour12: false })}
          </span>

          {/* Buttons */}
          <button onClick={onOpenLogs} className="btn-secondary !py-1.5 !px-2.5">
            <FileText className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Logs</span>
          </button>
          <button onClick={onOpenSettings} className="btn-secondary !py-1.5 !px-2.5">
            <Settings className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Settings</span>
          </button>
        </div>
      </div>
    </header>
  );
}
