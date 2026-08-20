// Fixed header: branding, live status LEDs, node count, vertical switcher,
// tier badge, UTC clock.

import { useEffect, useState } from 'react';
import { TIERS } from '../lib/statechart';
import { useStore } from '../hooks/useStore';

function Led({ label, ok, warn }: { label: string; ok?: boolean; warn?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          warn ? 'bg-amber-400' : ok ? 'bg-emerald-400' : 'bg-slate-600'
        } animate-pulse`}
        style={{ animationDuration: '1.6s' }}
      />
      <span className="text-[9px] tracking-[0.2em] text-slate-500 uppercase">{label}</span>
    </span>
  );
}

export function Header({ fps, reducedMotion, onToggleMotion, connected, onlineNodes, totalNodes, onOpenAnchorum, showServices, onToggleServices }: {
  fps: number;
  reducedMotion: boolean;
  onToggleMotion: () => void;
  connected: boolean;
  onlineNodes: number;
  totalNodes: number;
  onOpenAnchorum: () => void;
  showServices?: boolean;
  onToggleServices?: () => void;
}) {
  const state = useStore();
  const [clock, setClock] = useState('');

  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19) + 'Z');
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="flex h-11 shrink-0 items-center gap-4 border-b border-[#1a2540] bg-[#0c1220] px-4">
      <div className="flex items-center gap-2.5">
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
          <circle cx="9" cy="9" r="7.5" fill="none" stroke="#22d3ee" strokeWidth="1" />
          <circle cx="9" cy="9" r="4" fill="none" stroke="#22d3ee" strokeWidth="0.7" opacity="0.5" />
          <circle cx="9" cy="9" r="1.4" fill="#22d3ee" />
          <line x1="9" y1="9" x2="15" y2="4" stroke="#22d3ee" strokeWidth="0.8" opacity="0.8" />
        </svg>
        <span className="font-mono text-[12px] font-semibold tracking-[0.22em] text-slate-100">
          FULL-PERSPECTIVE INTERFACE
        </span>
        <span className="border border-[#1a2540] px-1.5 py-0.5 font-mono text-[8px] tracking-[0.18em] text-slate-500">
          EGREGORE v0.6
        </span>
      </div>

      <div className="hidden items-center gap-4 md:flex">
        <Led label="Gateway" ok={connected} />
        <Led label="Nodes" ok={onlineNodes > 0} warn={onlineNodes === 0 && totalNodes > 0} />
        <Led label="Store" ok />
      </div>

      <div className="ml-auto flex items-center gap-4">
        <button
          onClick={onOpenAnchorum}
          className="border border-[#2563eb]/50 bg-[#2563eb]/10 px-2 py-0.5 font-mono text-[9px] tracking-[0.14em] text-blue-300 hover:border-blue-400 hover:text-blue-200"
        >
          ANCHORUM
        </button>
        {onToggleServices && (
          <button
            onClick={onToggleServices}
            className={`border px-2 py-0.5 font-mono text-[9px] tracking-[0.14em] transition ${
              showServices
                ? 'border-emerald-500 bg-emerald-500/10 text-emerald-300 hover:border-emerald-400 hover:text-emerald-200'
                : 'border-[#1a2540] text-slate-400 hover:border-emerald-400/60 hover:text-emerald-300'
            }`}
            aria-pressed={showServices}
          >
            {showServices ? '🔧 SERVICES' : 'MAP'}
          </button>
        )}
        <span className="hidden font-mono text-[9px] text-slate-500 sm:block">
          {fps} FPS · {onlineNodes}/{totalNodes} NODES
        </span>
        <button
          onClick={onToggleMotion}
          className="border border-[#1a2540] px-2 py-0.5 font-mono text-[9px] text-slate-400 hover:border-cyan-400/60 hover:text-cyan-300"
          aria-pressed={reducedMotion}
        >
          MOTION {reducedMotion ? 'REDUCED' : 'FULL'}
        </button>
        <span className="border border-cyan-400/40 bg-cyan-400/10 px-2 py-0.5 font-mono text-[10px] tracking-[0.16em] text-cyan-300">
          {TIERS[state.tier].key}
        </span>
        <span className="font-mono text-[11px] text-slate-400 tabular-nums">{clock}</span>
      </div>
    </header>
  );
}
