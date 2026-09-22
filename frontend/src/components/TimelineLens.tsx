// Temporal lens: aggregate 24h activity profile with a brush window.
// Brushing dispatches SET_TIME_RANGE (debounced 200ms) which the spatial view
// and structural lens consume as a global filter — the coordination pattern.

import { useEffect, useMemo, useRef } from 'react';
import { aggregateSeries, SENSORS, SENSORS_BY_BUILDING } from '../lib/data';
import { store } from '../lib/store';
import { useStore } from '../hooks/useStore';
import type { LiveMetrics } from '../hooks/useLiveEgregore';

const PAD_L = 8, PAD_R = 8, PAD_T = 10, PAD_B = 16;

export function TimelineLens({ reducedMotion, metrics }: { reducedMotion: boolean; metrics?: LiveMetrics | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useStore();
  const dragRef = useRef<{ mode: 'move' | 'new'; start: number; orig: [number, number] } | null>(null);

  const sensors = useMemo(
    () =>
      state.selectedBuildingId
        ? SENSORS_BY_BUILDING.get(state.selectedBuildingId) ?? []
        : SENSORS,
    [state.selectedBuildingId]
  );
  const series = useMemo(() => aggregateSeries(sensors), [sensors]);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext('2d')!;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let raf = 0;
    let W = 10, H = 10;

    function resize() {
      const r = canvas.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const hourX = (h: number) => PAD_L + ((W - PAD_L - PAD_R) * h) / 24;

    function draw(now: number) {
      const [t0, t1] = store.state.timeRange;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // gridlines
      ctx.strokeStyle = 'rgba(148,197,255,0.08)';
      ctx.fillStyle = 'rgba(125,139,163,0.9)';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.lineWidth = 1;
      for (let h = 0; h <= 24; h += 4) {
        const x = hourX(h);
        ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, H - PAD_B); ctx.stroke();
        const lx = Math.max(0, Math.min(W - 26, x - 11));
        ctx.fillText(`${String(h).padStart(2, '0')}:00`, lx, H - 4);
      }

      // series area
      const base = H - PAD_B;
      const hh = base - PAD_T;
      ctx.beginPath();
      ctx.moveTo(hourX(0), base - series[0] * hh);
      for (let i = 1; i < 96; i++) ctx.lineTo(hourX(i / 4), base - series[i] * hh);
      ctx.strokeStyle = 'rgba(34,211,238,0.9)';
      ctx.lineWidth = 1.4;
      ctx.stroke();
      ctx.lineTo(hourX(24), base);
      ctx.lineTo(hourX(0), base);
      ctx.closePath();
      ctx.fillStyle = 'rgba(34,211,238,0.10)';
      ctx.fill();

      // outside-brush dimming
      ctx.fillStyle = 'rgba(5,8,16,0.72)';
      ctx.fillRect(0, 0, hourX(t0), H);
      ctx.fillRect(hourX(t1), 0, W - hourX(t1), H);

      // brush handles
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = 1.5;
      for (const t of [t0, t1]) {
        const x = hourX(t);
        ctx.beginPath(); ctx.moveTo(x, 2); ctx.lineTo(x, H - PAD_B + 4); ctx.stroke();
        ctx.fillStyle = '#22d3ee';
        ctx.fillRect(x - 3, 2, 6, 5);
      }

      // moving "now" marker
      if (!reducedMotion) {
        const nowH = ((now / 1000) % 60) / 60 * 24;
        ctx.strokeStyle = 'rgba(248,113,113,0.55)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(hourX(nowH), PAD_T); ctx.lineTo(hourX(nowH), base); ctx.stroke();
      }
      raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [series, reducedMotion]);

  // brush interaction
  useEffect(() => {
    const canvas = canvasRef.current!;
    const toHour = (clientX: number) => {
      const r = canvas.getBoundingClientRect();
      const x = clientX - r.left;
      return Math.max(0, Math.min(24, ((x - PAD_L) / (r.width - PAD_L - PAD_R)) * 24));
    };
    const down = (ev: PointerEvent) => {
      const h = toHour(ev.clientX);
      const [t0, t1] = store.state.timeRange;
      if (h >= t0 - 0.5 && h <= t1 + 0.5) {
        dragRef.current = { mode: 'move', start: h, orig: [t0, t1] };
      } else {
        dragRef.current = { mode: 'new', start: h, orig: [h, h] };
      }
      canvas.setPointerCapture(ev.pointerId);
    };
    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const h = toHour(ev.clientX);
      let range: [number, number];
      if (d.mode === 'move') {
        const dt = h - d.start;
        const span = d.orig[1] - d.orig[0];
        let a = Math.max(0, Math.min(24 - span, d.orig[0] + dt));
        range = [a, a + span];
      } else {
        range = [Math.min(d.start, h), Math.max(d.start, h)];
        if (range[1] - range[0] < 0.5) range[1] = Math.min(24, range[0] + 0.5);
      }
      // rendering reacts instantly: the store debounces coordination at 200ms
      store.dispatch('SET_TIME_RANGE', { t0: +range[0].toFixed(2), t1: +range[1].toFixed(2) }, { debounce: true });
    };
    const up = () => { dragRef.current = null; };
    canvas.addEventListener('pointerdown', down);
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', up);
    return () => {
      canvas.removeEventListener('pointerdown', down);
      canvas.removeEventListener('pointermove', move);
      canvas.removeEventListener('pointerup', up);
    };
  }, []);

  const [t0, t1] = state.timeRange;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[#1a2540] px-3 py-1.5">
        <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
          Temporal Lens · 24h Activity
        </span>
        <span className="font-mono text-[10px] text-cyan-400">
          {String(Math.floor(t0)).padStart(2, '0')}:{String(Math.round((t0 % 1) * 60)).padStart(2, '0')}
          {' – '}
          {String(Math.floor(t1)).padStart(2, '0')}:{String(Math.round((t1 % 1) * 60)).padStart(2, '0')}
        </span>
      </div>
      <div className="relative min-h-0 flex-1">
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full cursor-crosshair" aria-label="Temporal lens: drag to brush a time window that filters all views" />
      </div>
      <div className="border-t border-[#1a2540] px-3 py-1 text-[9px] tracking-wider text-slate-500 uppercase">
        {metrics
          ? `cpu ${metrics.compute.cpu_percent.toFixed(0)}% · ${metrics.jobs.running} jobs · ${metrics.inference.tokens_per_sec.toFixed(1)} tok/s`
          : state.selectedBuildingId
            ? `Scoped to ${state.selectedBuildingId} · ${sensors.length} sensors`
            : `All systems · ${sensors.length} sensors`}{' '}
        · brush to filter spatial + structural views
      </div>
    </div>
  );
}
