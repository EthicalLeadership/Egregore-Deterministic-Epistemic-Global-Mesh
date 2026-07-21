import { useState, useEffect } from 'react';
import { X, Save, RotateCcw } from 'lucide-react';

interface Settings {
  dashboardUrl: string;
  autoStart: boolean;
  minimizeToTray: boolean;
  updateInterval: number;
  logLevel: string;
}

const defaults: Settings = {
  dashboardUrl: 'http://localhost:5000',
  autoStart: false,
  minimizeToTray: true,
  updateInterval: 3,
  logLevel: 'info',
};

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsPanel({ isOpen, onClose }: Props) {
  const [settings, setSettings] = useState<Settings>(defaults);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (isOpen) {
      const stored = localStorage.getItem('egregore-settings');
      if (stored) {
        try { setSettings(JSON.parse(stored)); } catch { setSettings(defaults); }
      }
    }
  }, [isOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    if (isOpen) document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  const save = () => {
    localStorage.setItem('egregore-settings', JSON.stringify(settings));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const reset = () => { setSettings(defaults); localStorage.removeItem('egregore-settings'); };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-lg border border-[#1e2129] bg-[#161922] shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#1e2129]">
          <h2 className="text-sm font-semibold text-white">Settings</h2>
          <button onClick={onClose} className="btn-secondary !p-1.5">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-[11px] font-medium text-slate-500 mb-1.5">Dashboard URL</label>
            <input
              type="text"
              value={settings.dashboardUrl}
              onChange={e => setSettings({ ...settings, dashboardUrl: e.target.value })}
              className="w-full rounded-md bg-[#0f1117] border border-[#1e2129] px-3 py-2 text-sm text-white outline-none focus:border-blue-500 font-mono"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-[11px] font-medium text-slate-500">Update Interval</label>
              <span className="text-[11px] text-blue-400 font-mono">{settings.updateInterval}s</span>
            </div>
            <input
              type="range" min={1} max={10} step={1}
              value={settings.updateInterval}
              onChange={e => setSettings({ ...settings, updateInterval: parseInt(e.target.value) })}
              className="w-full accent-blue-500"
            />
            <div className="flex justify-between text-[10px] text-slate-600 font-mono mt-0.5">
              <span>1s</span><span>5s</span><span>10s</span>
            </div>
          </div>

          <div>
            <label className="block text-[11px] font-medium text-slate-500 mb-1.5">Log Level</label>
            <select
              value={settings.logLevel}
              onChange={e => setSettings({ ...settings, logLevel: e.target.value })}
              className="w-full rounded-md bg-[#0f1117] border border-[#1e2129] px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            >
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
          </div>

          <div className="space-y-2 pt-1">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-slate-400">Launch with Windows</span>
              <button
                onClick={() => setSettings({ ...settings, autoStart: !settings.autoStart })}
                className={`relative h-5 w-9 rounded-full transition-colors ${settings.autoStart ? 'bg-blue-500' : 'bg-[#2a2e3a]'}`}
              >
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${settings.autoStart ? 'left-4.5 translate-x-0' : 'left-0.5'}`} />
              </button>
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-sm text-slate-400">Minimize to tray on close</span>
              <button
                onClick={() => setSettings({ ...settings, minimizeToTray: !settings.minimizeToTray })}
                className={`relative h-5 w-9 rounded-full transition-colors ${settings.minimizeToTray ? 'bg-blue-500' : 'bg-[#2a2e3a]'}`}
              >
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${settings.minimizeToTray ? 'left-4.5 translate-x-0' : 'left-0.5'}`} />
              </button>
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-[#1e2129]">
          <button onClick={reset} className="btn-secondary !text-[11px]">
            <RotateCcw className="h-3 w-3" />
            Reset
          </button>
          <div className="flex items-center gap-2">
            {saved && <span className="text-[11px] text-emerald-400">Saved</span>}
            <button onClick={save} className="btn-primary !text-[11px]">
              <Save className="h-3 w-3" />
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
