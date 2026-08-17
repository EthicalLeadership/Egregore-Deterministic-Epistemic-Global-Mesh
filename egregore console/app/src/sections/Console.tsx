// Console layout: three-column mission-control arrangement.
// Left 264px: statechart + layers + watchlist. Center: spatial canvas over a
// temporal-lens dock. Right 360px: structural lens over the event store.

import { useCallback, useState } from 'react';
import { MapCanvas, type CameraInfo } from '../components/MapCanvas';
import { TimelineLens } from '../components/TimelineLens';
import { StructuralLens } from '../components/StructuralLens';
import { EventLog } from '../components/EventLog';
import { LeftPanel } from '../components/LeftPanel';
import { Header } from '../components/Header';
import { store } from '../lib/store';
import { useStore } from '../hooks/useStore';
import { TIERS } from '../lib/statechart';
import { MTL_CENTER } from '../lib/data';

function Breadcrumb() {
  const state = useStore();
  const crumbs: { label: string; onClick?: () => void }[] = [
    { label: 'PLANET', onClick: () => store.dispatch('RESET_CAMERA', {}) },
  ];
  if (state.tier >= 2) {
    crumbs.push({
      label: 'MONTRÉAL',
      onClick: () => store.dispatch('FLY_TO', { lon: MTL_CENTER[0], lat: MTL_CENTER[1], k: 4200 }),
    });
  }
  if (state.tier >= 3) {
    crumbs.push({
      label: 'DOWNTOWN',
      onClick: () => store.dispatch('FLY_TO', { lon: MTL_CENTER[0], lat: MTL_CENTER[1], k: 18000 }),
    });
  }
  if (state.selectedBuildingId) {
    crumbs.push({ label: state.selectedBuildingId });
  }
  return (
    <nav className="pointer-events-auto absolute left-3 top-3 flex items-center gap-1 border border-[#1a2540] bg-[#0c1220]/90 px-2.5 py-1.5 backdrop-blur-sm" aria-label="Breadcrumb">
      {crumbs.map((c, i) => (
        <span key={c.label} className="flex items-center gap-1">
          {i > 0 && <span className="text-[10px] text-slate-600">/</span>}
          {c.onClick ? (
            <button
              onClick={c.onClick}
              className="font-mono text-[10px] tracking-[0.14em] text-slate-400 hover:text-cyan-300"
            >
              {c.label}
            </button>
          ) : (
            <span className="font-mono text-[10px] tracking-[0.14em] text-cyan-300">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

function CoordReadout({ cam }: { cam: CameraInfo }) {
  const state = useStore();
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 border border-[#1a2540] bg-[#0c1220]/90 px-2.5 py-1.5 font-mono text-[9px] leading-[1.6] text-slate-500 backdrop-blur-sm">
      <div>
        LAT <span className={cam.lat >= 0 ? 'text-slate-300' : 'text-emerald-400'}>{Number.isNaN(cam.lat) ? '——.—' : Math.abs(cam.lat).toFixed(4)}{Number.isNaN(cam.lat) ? '' : cam.lat >= 0 ? '°N' : '°S'}</span>
        {'  '}
        LON <span className="text-slate-300">{Number.isNaN(cam.lon) ? '———.—' : Math.abs(cam.lon).toFixed(4)}{Number.isNaN(cam.lon) ? '' : cam.lon >= 0 ? '°E' : '°W'}</span>
      </div>
      <div>
        SCALE <span className="text-cyan-400">×{cam.k.toFixed(cam.k < 10 ? 2 : 0)}</span>
        {'  '}TIER <span className="text-cyan-400">{TIERS[state.tier].key}</span>
      </div>
    </div>
  );
}

export function Console({
  reducedMotion,
  onToggleMotion,
}: {
  reducedMotion: boolean;
  onToggleMotion: () => void;
}) {
  const [cam, setCam] = useState<CameraInfo>({ lon: NaN, lat: NaN, k: 1, fps: 60 });
  const onCamera = useCallback((c: CameraInfo) => setCam(c), []);

  return (
    <div className="relative flex h-screen w-screen flex-col overflow-hidden bg-[#050810] text-slate-200">
      <Header fps={cam.fps} reducedMotion={reducedMotion} onToggleMotion={onToggleMotion} />

      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <aside className="w-[264px] shrink-0 border-r border-[#1a2540] bg-[#0c1220]">
          <LeftPanel cameraK={cam.k} />
        </aside>

        {/* center: spatial canvas + temporal dock */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <MapCanvas onCamera={onCamera} reducedMotion={reducedMotion} />
            <Breadcrumb />
            <CoordReadout cam={cam} />
          </div>
          <div className="h-[168px] shrink-0 border-t border-[#1a2540] bg-[#0c1220]">
            <TimelineLens reducedMotion={reducedMotion} />
          </div>
        </main>

        {/* right rail */}
        <aside className="flex w-[360px] shrink-0 flex-col border-l border-[#1a2540] bg-[#0c1220]">
          <div className="min-h-0 flex-[1.15]">
            <StructuralLens reducedMotion={reducedMotion} />
          </div>
          <div className="min-h-0 flex-1 border-t border-[#1a2540]">
            <EventLog />
          </div>
        </aside>
      </div>

      {/* status bar */}
      <footer className="flex h-6 shrink-0 items-center gap-5 border-t border-[#1a2540] bg-[#0c1220] px-4 font-mono text-[8.5px] tracking-[0.14em] text-slate-600 uppercase">
        <span>Scroll · zoom</span>
        <span>Drag · pan</span>
        <span>Click metro · fly</span>
        <span>Click footprint · select</span>
        <span>Brush timeline · global time filter</span>
        <span className="ml-auto">OSM © contributors (ODbL) · Natural Earth (PD) · demo sensors synthetic</span>
      </footer>

      {/* scanline texture */}
      <div
        className="pointer-events-none absolute inset-0 z-50 opacity-[0.05]"
        style={{ backgroundImage: 'repeating-linear-gradient(0deg, transparent 0 2px, #94c5ff 2px 3px)' }}
        aria-hidden
      />
    </div>
  );
}
