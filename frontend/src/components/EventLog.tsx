// Event-sourced store inspector: the literal event log, with undo / redo
// (cursor moves) demonstrating selection as derived state.

import { useEffect, useRef, useState } from 'react';
import { store, type AppEvent } from '../lib/store';

const TYPE_COLOR: Record<string, string> = {
  INIT: 'text-slate-500',
  FLY_TO: 'text-cyan-400',
  TIER_CHANGE: 'text-violet-400',
  SELECT_BUILDING: 'text-amber-400',
  SET_SYSTEM_FILTER: 'text-pink-400',
  SET_TIME_RANGE: 'text-emerald-400',
  TOGGLE_LAYER: 'text-slate-400',
  SET_HYSTERESIS: 'text-slate-400',
  RESET_CAMERA: 'text-red-400',
  CLEAR_SELECTION: 'text-slate-400',
};

function fmtPayload(ev: AppEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case 'TIER_CHANGE': return `tier=${p.tier}`;
    case 'SELECT_BUILDING': return `id=${p.id ?? '∅'}`;
    case 'SET_SYSTEM_FILTER': return `sys=${p.system ?? '∅'}`;
    case 'SET_TIME_RANGE': return `${p.t0}–${p.t1}h`;
    case 'FLY_TO': return `${(p.lon as number).toFixed(2)},${(p.lat as number).toFixed(2)} @${p.k}`;
    case 'TOGGLE_LAYER': return `${p.layer}=${p.on}`;
    case 'SET_HYSTERESIS': return `band=${p.band}`;
    default: return '';
  }
}

export function EventLog() {
  const [, force] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => store.subscribe(() => force((x) => x + 1)), []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [store.events.length]);

  const applied = store.events.slice(0, store.cursor);
  const future = store.events.slice(store.cursor);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[#1a2540] px-3 py-1.5">
        <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
          Event Store · Derived State
        </span>
        <div className="flex gap-1.5">
          <button
            onClick={() => store.undo()}
            disabled={!store.canUndo()}
            className="border border-[#1a2540] px-2 py-0.5 font-mono text-[9px] text-slate-300 hover:border-cyan-400/60 hover:text-cyan-300 disabled:opacity-30"
            aria-label="Undo last event"
          >
            ⟲ UNDO
          </button>
          <button
            onClick={() => store.redo()}
            disabled={!store.canRedo()}
            className="border border-[#1a2540] px-2 py-0.5 font-mono text-[9px] text-slate-300 hover:border-cyan-400/60 hover:text-cyan-300 disabled:opacity-30"
            aria-label="Redo event"
          >
            ⟳ REDO
          </button>
        </div>
      </div>
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-1.5 font-mono text-[9.5px] leading-[1.7]">
        {applied.map((ev) => (
          <div key={ev.seq} className="flex gap-2">
            <span className="w-8 shrink-0 text-right text-slate-600">#{ev.seq}</span>
            <span className={`w-28 shrink-0 ${TYPE_COLOR[ev.type] ?? 'text-slate-400'}`}>{ev.type}</span>
            <span className="truncate text-slate-500">{fmtPayload(ev)}</span>
          </div>
        ))}
        {future.map((ev) => (
          <div key={ev.seq} className="flex gap-2 opacity-30 line-through">
            <span className="w-8 shrink-0 text-right text-slate-600">#{ev.seq}</span>
            <span className={`w-28 shrink-0 ${TYPE_COLOR[ev.type] ?? 'text-slate-400'}`}>{ev.type}</span>
            <span className="truncate text-slate-500">{fmtPayload(ev)}</span>
          </div>
        ))}
        {applied.length <= 1 && (
          <div className="mt-2 border border-dashed border-[#1a2540] p-2 text-slate-500">
            Interact with any view — every coordination event is appended here
            and all shared state is derived by replaying this log.
          </div>
        )}
      </div>
      <div className="border-t border-[#1a2540] px-3 py-1 text-[9px] tracking-wider text-slate-500 uppercase">
        cursor {store.cursor}/{store.events.length} · undo replays the log — state is never mutated directly
      </div>
    </div>
  );
}
