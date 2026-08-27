// Left rail: live services (with start/stop/restart), scale-tier statechart,
// layer controls, node watchlist.

import { CITIES, CITY_CENTER, LAYER_DEFS } from '../lib/data';
import { store } from '../lib/store';
import { TIERS } from '../lib/statechart';
import { useStore } from '../hooks/useStore';
import type { LiveNode, LiveService } from '../hooks/useLiveEgregore';

function RadarCheck({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className="group flex w-full items-center gap-2.5 py-[5px] text-left"
      role="checkbox"
      aria-checked={on}
    >
      <span
        className={`flex h-3.5 w-3.5 items-center justify-center rounded-full border transition-all ${
          on ? 'border-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.5)]' : 'border-slate-600 group-hover:border-slate-400'
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full transition-all ${on ? 'bg-cyan-400' : 'bg-transparent'}`} />
      </span>
      <span className={`text-[10px] tracking-[0.14em] uppercase ${on ? 'text-slate-200' : 'text-slate-500'}`}>
        {label}
      </span>
    </button>
  );
}

function ServiceRow({
  service,
  onAction,
  busy,
}: {
  service: LiveService;
  onAction: (name: string, action: 'start' | 'stop' | 'restart') => void;
  busy: boolean;
}) {
  const running = service.status === 'Running';
  return (
    <div className="flex items-center gap-2 py-[4px]">
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          running ? 'bg-emerald-400' : service.status === 'Error' ? 'bg-red-500' : 'bg-slate-600'
        }`}
      />
      <button
        onClick={() => onAction(service.name, running ? 'stop' : 'start')}
        disabled={busy}
        className="min-w-0 flex-1 truncate text-left text-[10.5px] text-slate-300 hover:text-white disabled:opacity-50"
        title={service.pid ? `PID ${service.pid}` : undefined}
      >
        {service.name.replace('Egregore', '')}
      </button>
      <button
        onClick={() => onAction(service.name, 'restart')}
        disabled={busy}
        className="shrink-0 border border-[#1a2540] px-1.5 py-0.5 font-mono text-[8px] text-slate-500 hover:text-cyan-300 disabled:opacity-40"
      >
        ↻
      </button>
    </div>
  );
}

function NodeRow({ node }: { node: LiveNode }) {
  const online = node.status === 'online';
  return (
    <button
      onClick={() => store.dispatch('FLY_TO', { lon: CITY_CENTER[0], lat: CITY_CENTER[1], k: 2 })}
      className="group flex w-full items-center justify-between py-[5px] text-left"
    >
      <span className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${online ? 'bg-emerald-400' : 'bg-red-500'}`} />
        <span className={`text-[10.5px] ${online ? 'text-slate-200' : 'text-slate-500'}`}>{node.node_id}</span>
      </span>
      <span className="font-mono text-[9px] text-slate-600 group-hover:text-cyan-400">
        {online ? (node.latency_ms != null ? `${node.latency_ms.toFixed(0)}ms` : 'ONLINE') : 'OFFLINE'}
      </span>
    </button>
  );
}

export function LeftPanel({
  cameraK,
  services,
  onServiceAction,
  loading,
  nodes,
}: {
  cameraK: number;
  services: LiveService[];
  onServiceAction?: (name: string, action: 'start' | 'stop' | 'restart') => void;
  loading?: Record<string, boolean>;
  nodes: LiveNode[];
}) {
  const state = useStore();

  const band = state.hysteresisBand;
  const sqrtB = Math.sqrt(band);
  const cur = TIERS[state.tier];
  const next = TIERS[state.tier + 1];
  const loK = state.tier > 0 ? cur.edge / sqrtB : 0.5;
  const hiK = next ? next.edge * sqrtB : 220000;
  const pos = Math.max(0, Math.min(1,
    (Math.log(Math.max(0.5, cameraK)) - Math.log(loK)) / (Math.log(hiK) - Math.log(loK))
  ));

  const handleServiceAction = (name: string, action: 'start' | 'stop' | 'restart') => {
    store.dispatch('SERVICE_ACTION', { service: name, action });
    if (onServiceAction) onServiceAction(name, action);
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* statechart */}
      <div className="border-b border-[#1a2540] px-3 py-2.5">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
            Scale Statechart
          </span>
          <span className="font-mono text-[9px] text-cyan-400">×{cameraK.toFixed(0)}</span>
        </div>
        <div className="space-y-0">
          {TIERS.map((t, i) => {
            const active = i === state.tier;
            const passed = i < state.tier;
            return (
              <div key={t.key} className="relative flex items-start gap-2.5 pb-[9px]">
                {i < TIERS.length - 1 && (
                  <span className={`absolute left-[5px] top-3 h-full w-px ${passed || active ? 'bg-cyan-400/50' : 'bg-[#1a2540]'}`} />
                )}
                <span
                  className={`z-10 mt-[3px] h-[11px] w-[11px] shrink-0 rounded-full border ${
                    active
                      ? 'border-cyan-400 bg-cyan-400/30 shadow-[0_0_8px_rgba(34,211,238,0.7)]'
                      : passed
                        ? 'border-cyan-400/60 bg-transparent'
                        : 'border-slate-600 bg-transparent'
                  }`}
                />
                <div className="min-w-0">
                  <div className={`font-mono text-[10px] tracking-[0.12em] ${active ? 'text-cyan-300' : passed ? 'text-slate-300' : 'text-slate-600'}`}>
                    {t.key}
                  </div>
                  {active && (
                    <div className="mt-0.5 text-[9px] leading-tight text-slate-500">{t.description}</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* hysteresis meter */}
        <div className="mt-1 border border-[#1a2540] p-2">
          <div className="mb-1.5 flex justify-between font-mono text-[8.5px] text-slate-500">
            <span>OUT ×{(cur.edge / sqrtB).toFixed(0)}</span>
            <span className="text-slate-400">HYSTERESIS WINDOW</span>
            <span>IN ×{next ? (next.edge * sqrtB).toFixed(0) : '∞'}</span>
          </div>
          <div className="relative h-1.5 bg-[#111a2b]">
            <div
              className="absolute top-0 h-full bg-cyan-400/70 transition-[left] duration-150"
              style={{ left: `${pos * 100}%`, width: 3, marginLeft: -1.5 }}
            />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className="font-mono text-[8.5px] text-slate-500">BAND</span>
            <input
              type="range"
              min={1.1}
              max={6}
              step={0.1}
              value={band}
              onChange={(e) =>
                store.dispatch('SET_HYSTERESIS', { band: +(+e.target.value).toFixed(1) })
              }
              className="h-1 flex-1 cursor-pointer appearance-none bg-[#111a2b] accent-cyan-400"
              aria-label="Hysteresis band factor"
            />
            <span className="font-mono text-[9px] text-cyan-400">{band.toFixed(1)}×</span>
          </div>
          <div className="mt-1 text-[8px] leading-tight text-slate-600">
            Tunable heuristic — not an empirical constant. Prevents tier flicker near boundaries.
          </div>
        </div>
      </div>

      {/* live services */}
      <div className="border-b border-[#1a2540] px-3 py-2.5">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
            Services
          </span>
          <span className="font-mono text-[9px] text-slate-600">{services.length}</span>
        </div>
        {services.length === 0 ? (
          <div className="text-[9.5px] text-slate-600">No services reported.</div>
        ) : (
          services.map((s) => (
            <ServiceRow
              key={s.name}
              service={s}
              onAction={handleServiceAction}
              busy={Boolean(loading?.[s.name])}
            />
          ))
        )}
      </div>

      {/* layers */}
      <div className="border-b border-[#1a2540] px-3 py-2.5">
        <div className="mb-1.5 text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
          Layers
        </div>
        {LAYER_DEFS.map((l) => (
          <RadarCheck
            key={l.key}
            on={state.layers[l.key]}
            onChange={(v) => store.dispatch('TOGGLE_LAYER', { layer: l.key, on: v })}
            label={l.label}
          />
        ))}
      </div>

      {/* node watchlist */}
      <div className="px-3 py-2.5">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
            Nodes
          </span>
          <span className="font-mono text-[9px] text-slate-600">
            {nodes.filter((n) => n.status === 'online').length}/{nodes.length}
          </span>
        </div>
        {nodes.length === 0 ? (
          <div className="text-[9.5px] text-slate-600">No live nodes.</div>
        ) : (
          nodes.map((n) => <NodeRow key={n.node_id} node={n} />)
        )}
        {CITIES.slice(0, 3).map((c) => (
          <button
            key={c.name}
            onClick={() => store.dispatch('FLY_TO', { lon: c.lon, lat: c.lat, k: c.hq ? 4200 : 110 })}
            className="flex w-full items-center justify-between py-[5px] text-left"
          >
            <span className="text-[10.5px] text-slate-400">{c.name}</span>
            <span className="font-mono text-[9px] text-slate-600">geo →</span>
          </button>
        ))}
      </div>
    </div>
  );
}
