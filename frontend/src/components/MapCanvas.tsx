// Spatial core: continuous planet -> city -> building -> room zoom on a single
// canvas. Rendering reacts in-frame (0ms); coordination events are debounced
// upstream in the store (200ms). LOD visibility follows the tier statechart
// with smooth alpha ramps around each hysteresis-banded edge.

import { useEffect, useRef, useState } from 'react';
import { geoEquirectangular, geoGraticule10, geoPath } from 'd3-geo';
import { zoom as d3zoom, zoomIdentity, type D3ZoomEvent } from 'd3-zoom';
import { select, type Selection } from 'd3-selection';
import 'd3-transition';
import {
  LAND, CITY, CITY_CENTER, CITIES, SENSORS, BUILDING_BY_ID,
  sensorActiveInRange, SYSTEM_COLOR, type Building, type Sensor,
} from '../lib/data';
import { store } from '../lib/store';
import { tierMachine } from '../lib/statechart';

const ACCENT = '#22d3ee';

function clamp01(v: number) { return v < 0 ? 0 : v > 1 ? 1 : v; }
function ramp(k: number, a: number, b: number) { return clamp01((k - a) / (b - a)); }

function pip(pt: [number, number], poly: [number, number][]) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}

export interface CameraInfo {
  lon: number;
  lat: number;
  k: number;
  fps: number;
}

export function MapCanvas({
  onCamera,
  reducedMotion,
}: {
  onCamera: (c: CameraInfo) => void;
  reducedMotion: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const miniRef = useRef<HTMLCanvasElement>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);

  useEffect(() => {
    const wrap = wrapRef.current!;
    const canvas = canvasRef.current!;
    const mini = miniRef.current!;
    const ctx = canvas.getContext('2d')!;
    const mctx = mini.getContext('2d')!;
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    let W = 10, H = 10;
    const projection = geoEquirectangular();
    const graticule = geoGraticule10();
    const path = geoPath(projection, ctx);
    const transform = { current: zoomIdentity };
    let raf = 0;
    let lastFrame = performance.now();
    let fps = 60;
    let cameraThrottle = 0;
    let disposed = false;

    const sel: Selection<HTMLCanvasElement, unknown, null, undefined> = select(canvas);

    const zoomBehavior = d3zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.85, 220000])
      .on('zoom', (ev: D3ZoomEvent<HTMLCanvasElement, unknown>) => {
        transform.current = ev.transform;
        const changed = tierMachine.update(ev.transform.k, store.state.hysteresisBand);
        if (changed) {
          // coordination discipline: tier changes are debounced 200ms in the store
          store.dispatch('TIER_CHANGE', { tier: tierMachine.tier }, { debounce: true });
        }
      });

    sel.call(zoomBehavior);

    function resize() {
      const r = wrap.getBoundingClientRect();
      W = Math.max(10, r.width);
      H = Math.max(10, r.height);
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      projection.fitExtent([[0, 0], [W, H]], LAND as GeoJSON.FeatureCollection);
      // minimap
      const mw = 176, mh = 96;
      mini.width = mw * dpr;
      mini.height = mh * dpr;
      mini.style.width = `${mw}px`;
      mini.style.height = `${mh}px`;
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // --- camera flights triggered by store events --------------------------
    let lastTarget: { lon: number; lat: number; k: number } | null = null;
    let lastResetSeq = 0;

    function flyTo(lon: number, lat: number, k: number) {
      const p = projection([lon, lat])!;
      const t = zoomIdentity
        .translate(W / 2, H / 2)
        .scale(k)
        .translate(-p[0], -p[1]);
      if (reducedMotion) {
        sel.call(zoomBehavior.transform, t);
      } else {
        sel.transition().duration(1400).ease((x: number) => 1 - Math.pow(1 - x, 3))
          .call(zoomBehavior.transform, t as never);
      }
    }

    function resetCamera() {
      if (reducedMotion) sel.call(zoomBehavior.transform, zoomIdentity);
      else sel.transition().duration(1200).call(zoomBehavior.transform, zoomIdentity as never);
    }

    const unsubStore = store.subscribe(() => {
      const s = store.state;
      if (s.cameraTarget && s.cameraTarget !== lastTarget) {
        lastTarget = s.cameraTarget;
        flyTo(s.cameraTarget.lon, s.cameraTarget.lat, s.cameraTarget.k);
      }
      if (s.cameraResetSeq !== lastResetSeq) {
        lastResetSeq = s.cameraResetSeq;
        resetCamera();
      }
    });

    // --- pointer interaction ----------------------------------------------
    let pointer: [number, number] | null = null;
    let hover: Building | null = null;

    function toLonLat(x: number, y: number): [number, number] {
      const b = transform.current.invert([x, y]);
      return projection.invert!(b) as [number, number];
    }

    function buildingAt(ll: [number, number]): Building | null {
      const [w0, s0, e0, n0] = CITY.bbox;
      if (ll[0] < w0 || ll[0] > e0 || ll[1] < s0 || ll[1] > n0) return null;
      if (transform.current.k < 1400) return null;
      // deeper footprint first (taller buildings on top)
      const sorted = [...CITY.buildings].sort((a, b) => b.lv - a.lv);
      for (const b of sorted) if (pip(ll, b.p)) return b;
      return null;
    }

    canvas.addEventListener('pointermove', (ev) => {
      const r = canvas.getBoundingClientRect();
      pointer = [ev.clientX - r.left, ev.clientY - r.top];
      const h = buildingAt(toLonLat(pointer[0], pointer[1]));
      const id = h?.id ?? null;
      hover = h;
      setHoverId(id);
      canvas.style.cursor = id || cityNear(pointer) ? 'pointer' : 'grab';
    });

    function cityNear(pt: [number, number]): boolean {
      if (transform.current.k > 4200) return false;
      for (const c of CITIES) {
        const p = projection([c.lon, c.lat]);
        if (!p) continue;
        const sx = transform.current.applyX(p[0]);
        const sy = transform.current.applyY(p[1]);
        if (Math.hypot(sx - pt[0], sy - pt[1]) < 10) return true;
      }
      return false;
    }

    canvas.addEventListener('click', (ev) => {
      const r = canvas.getBoundingClientRect();
      const pt: [number, number] = [ev.clientX - r.left, ev.clientY - r.top];
      const ll = toLonLat(pt[0], pt[1]);
      const b = buildingAt(ll);
      if (b) {
        store.dispatch('SELECT_BUILDING', { id: store.state.selectedBuildingId === b.id ? null : b.id });
        return;
      }
      if (transform.current.k <= 4200) {
        for (const c of CITIES) {
          const p = projection([c.lon, c.lat]);
          if (!p) continue;
          const sx = transform.current.applyX(p[0]);
          const sy = transform.current.applyY(p[1]);
          if (Math.hypot(sx - pt[0], sy - pt[1]) < 10) {
            store.dispatch('FLY_TO', { lon: c.lon, lat: c.lat, k: c.hq ? 4200 : 110 });
            return;
          }
        }
      }
      if (store.state.selectedBuildingId || store.state.systemFilter) {
        store.dispatch('CLEAR_SELECTION', {});
      }
    });

    canvas.addEventListener('pointerleave', () => { pointer = null; });

    // keyboard navigation (accessibility layer)
    canvas.tabIndex = 0;
    canvas.addEventListener('keydown', (ev) => {
      const z = zoomBehavior;
      if (ev.key === '+' || ev.key === '=') sel.call(z.scaleBy, 1.5);
      else if (ev.key === '-') sel.call(z.scaleBy, 1 / 1.5);
      else if (ev.key === 'ArrowLeft') sel.call(z.translateBy, 80, 0);
      else if (ev.key === 'ArrowRight') sel.call(z.translateBy, -80, 0);
      else if (ev.key === 'ArrowUp') sel.call(z.translateBy, 0, 80);
      else if (ev.key === 'ArrowDown') sel.call(z.translateBy, 0, -80);
      else if (ev.key === 'Escape') store.dispatch('CLEAR_SELECTION', {});
      else return;
      ev.preventDefault();
    });

    // --- cached filter sets ------------------------------------------------
    let activeSensors: Set<string> = new Set(SENSORS.map((s) => s.id));
    let lastRangeKey = '';
    function refreshSensorActivity() {
      const r = store.state.timeRange;
      const key = `${r[0].toFixed(2)}|${r[1].toFixed(2)}`;
      if (key === lastRangeKey) return;
      lastRangeKey = key;
      activeSensors = new Set(
        SENSORS.filter((s) => sensorActiveInRange(s, r)).map((s) => s.id)
      );
    }

    // --- flow lines (planet tier) ------------------------------------------
    // filter(Boolean): resilient if CITIES ever shrinks below these indices.
    const FLOWS = [1, 3, 8, 9, 17].map((i) => CITIES[i]).filter(Boolean);
    function drawFlows(k: number, t: number) {
      const a = 1 - ramp(k, 8, 40);
      if (a <= 0) return;
      const hq = projection([CITY_CENTER[0], CITY_CENTER[1]])!;
      ctx.save();
      ctx.lineWidth = 1 / k;
      ctx.setLineDash([6 / k, 8 / k]);
      ctx.lineDashOffset = reducedMotion ? 0 : -((t / 40) % (14 / k));
      for (const c of FLOWS) {
        const dst = projection([c.lon, c.lat]);
        if (!dst) continue;
        const mx = (hq[0] + dst[0]) / 2;
        const my = Math.min(hq[1], dst[1]) - Math.hypot(dst[0] - hq[0], dst[1] - hq[1]) * 0.18;
        ctx.strokeStyle = `rgba(34,211,238,${0.28 * a})`;
        ctx.beginPath();
        ctx.moveTo(hq[0], hq[1]);
        ctx.quadraticCurveTo(mx, my, dst[0], dst[1]);
        ctx.stroke();
      }
      ctx.restore();
    }

    // --- render --------------------------------------------------------------
    function projectPoly(poly: [number, number][]) {
      ctx.beginPath();
      poly.forEach((pt, i) => {
        const p = projection(pt)!;
        if (i === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      });
      ctx.closePath();
    }

    function draw(now: number) {
      const t = transform.current;
      const k = t.k;
      const s = store.state;
      refreshSensorActivity();

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#050810';
      ctx.fillRect(0, 0, W, H);

      // geographic space
      ctx.save();
      ctx.translate(t.x, t.y);
      ctx.scale(t.k, t.k);

      const gratA = 1 - ramp(k, 20, 60);
      if (s.layers.graticule && gratA > 0) {
        ctx.strokeStyle = `rgba(34,211,238,${0.07 * gratA})`;
        ctx.lineWidth = 1 / k;
        path(graticule as never);
        ctx.stroke();
      }
      if (s.layers.land) {
        const landA = 1 - ramp(k, 1200, 5000);
        ctx.fillStyle = '#0d1626';
        ctx.strokeStyle = `rgba(148,197,255,${0.22 * landA})`;
        ctx.lineWidth = 1 / k;
        ctx.beginPath();
        path(LAND as never);
        ctx.fill();
        if (landA > 0) ctx.stroke();
      }
      if (s.layers.flowlines) drawFlows(k, now);

      // roads
      const roadA = ramp(k, 150, 320);
      if (s.layers.roads && roadA > 0) {
        for (const r of CITY.roads) {
          const major = r.t === 'primary' || r.t === 'secondary' || r.t === 'trunk';
          ctx.strokeStyle = major
            ? `rgba(148,197,255,${0.5 * roadA})`
            : `rgba(148,197,255,${0.22 * roadA})`;
          ctx.lineWidth = (major ? 2.2 : 1.1) / k;
          ctx.beginPath();
          r.p.forEach((pt, i) => {
            const p = projection(pt)!;
            if (i === 0) ctx.moveTo(p[0], p[1]);
            else ctx.lineTo(p[0], p[1]);
          });
          ctx.stroke();
        }
      }

      // buildings
      const bldA = ramp(k, 1300, 2600);
      if (s.layers.buildings && bldA > 0) {
        for (const b of CITY.buildings) {
          const isSel = b.id === s.selectedBuildingId;
          const sysHit = !s.systemFilter || b.sys === s.systemFilter;
          const dim = s.systemFilter && !sysHit;
          projectPoly(b.p);
          const col = SYSTEM_COLOR.get(b.sys) ?? ACCENT;
          ctx.fillStyle = isSel
            ? 'rgba(34,211,238,0.30)'
            : dim
              ? `rgba(148,163,184,${0.05 * bldA})`
              : s.systemFilter && sysHit
                ? hexA(col, 0.30 * bldA)
                : `rgba(20,32,54,${0.92 * bldA})`;
          ctx.fill();
          ctx.strokeStyle = isSel
            ? ACCENT
            : dim
              ? `rgba(148,163,184,${0.10 * bldA})`
              : `rgba(125,165,220,${0.35 * bldA})`;
          ctx.lineWidth = (isSel ? 2.2 : 0.9) / k;
          ctx.stroke();
        }
      }

      // sensor lattice
      const senA = ramp(k, 6500, 12500);
      if (s.layers.sensors && senA > 0) {
        const pulse = reducedMotion ? 0.5 : (Math.sin(now / 300) + 1) / 2;
        for (const sn of SENSORS) {
          const active = activeSensors.has(sn.id);
          const inSel = !s.selectedBuildingId || sn.buildingId === s.selectedBuildingId;
          const sysOk = !s.systemFilter || BUILDING_BY_ID.get(sn.buildingId)?.sys === s.systemFilter;
          const alpha = senA * (active && inSel && sysOk ? 0.9 : 0.07);
          if (alpha <= 0.01) continue;
          const p = projection([sn.lon, sn.lat])!;
          const rad = (active ? 2.4 + pulse * 1.6 : 1.8) / k;
          ctx.fillStyle = active ? hexA(sn.col, alpha) : `rgba(148,163,184,${alpha})`;
          ctx.beginPath();
          ctx.arc(p[0], p[1], rad, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // room tier: interior schematic inside selected footprint
      const roomA = ramp(k, 36000, 62000);
      if (roomA > 0 && s.selectedBuildingId) {
        drawInterior(BUILDING_BY_ID.get(s.selectedBuildingId)!, roomA, k, now);
      }

      ctx.restore();

      // screen-space overlays -------------------------------------------------
      drawCityMarkers(t, s.layers.cities, s.layers.flowlines);
      drawHoverLabel(t);
      drawMini(t);
    }

    function drawInterior(b: Building, alpha: number, k: number, now: number) {
      // partition footprint bbox into rooms, clipped to the polygon
      const lons = b.p.map((p) => p[0]);
      const lats = b.p.map((p) => p[1]);
      const minX = Math.min(...lons), maxX = Math.max(...lons);
      const minY = Math.min(...lats), maxY = Math.max(...lats);
      const cols = b.lv > 10 ? 4 : 3;
      const rows = 2 + (b.seed % 2);
      ctx.save();
      projectPoly(b.p);
      ctx.clip();
      ctx.fillStyle = `rgba(6,12,24,${0.75 * alpha})`;
      ctx.fillRect(minX, minY, maxX - minX, maxY - minY);
      ctx.strokeStyle = `rgba(34,211,238,${0.45 * alpha})`;
      ctx.lineWidth = 1 / k;
      for (let c = 1; c < cols; c++) {
        const x = minX + ((maxX - minX) * c) / cols;
        ctx.beginPath(); ctx.moveTo(x, minY); ctx.lineTo(x, maxY); ctx.stroke();
      }
      for (let r = 1; r < rows; r++) {
        const y = minY + ((maxY - minY) * r) / rows;
        ctx.beginPath(); ctx.moveTo(minX, y); ctx.lineTo(maxX, y); ctx.stroke();
      }
      // live sensor blips
      const pulse = reducedMotion ? 0.5 : (Math.sin(now / 240) + 1) / 2;
      const sensors: Sensor[] = SENSORS.filter((sn) => sn.buildingId === b.id);
      for (const sn of sensors) {
        const p = projection([sn.lon, sn.lat])!;
        const active = activeSensors.has(sn.id);
        ctx.fillStyle = active ? `rgba(52,211,153,${alpha})` : `rgba(148,163,184,${0.35 * alpha})`;
        ctx.beginPath();
        ctx.arc(p[0], p[1], (2 + pulse * 2.4) / k, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    /** Log-scale marker radius — behaves across a wide headcount range without
     *  baking unit conventions into the data. ~7px HQ dot down to ~3px colony. */
    function cityDotRadius(pop: number) {
      return Math.max(2, Math.log10(Math.max(1, pop)) * 1.4 - 2);
    }

    function drawCityMarkers(t: typeof zoomIdentity, show: boolean, flows: boolean) {
      if (!show && !flows) return;
      const k = t.k;
      const dotA = 1 - ramp(k, 1400, 4200);
      const labelA = ramp(k, 3, 7) * (1 - ramp(k, 900, 2400));
      if (dotA <= 0 && labelA <= 0) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const pulse = reducedMotion ? 0.5 : (Math.sin(performance.now() / 500) + 1) / 2;
      for (const c of CITIES) {
        const p = projection([c.lon, c.lat]);
        if (!p) continue;
        const x = t.applyX(p[0]);
        const y = t.applyY(p[1]);
        if (x < -20 || x > W + 20 || y < -20 || y > H + 20) continue;
        if (show && dotA > 0) {
          const r = cityDotRadius(c.pop);
          ctx.fillStyle = c.hq ? `rgba(34,211,238,${dotA})` : `rgba(148,197,255,${0.75 * dotA})`;
          ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
          if (c.hq) {
            ctx.strokeStyle = `rgba(34,211,238,${(0.6 - pulse * 0.5) * dotA})`;
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.arc(x, y, r + 3 + pulse * 5, 0, Math.PI * 2); ctx.stroke();
          }
        }
        if (show && labelA > 0) {
          ctx.font = `${c.hq ? 600 : 400} 10px "JetBrains Mono", monospace`;
          ctx.fillStyle = c.hq ? `rgba(34,211,238,${labelA})` : `rgba(203,213,225,${0.8 * labelA})`;
          ctx.fillText(c.name.toUpperCase(), x + 7, y - 6);
        }
      }
    }

    function drawHoverLabel(t: typeof zoomIdentity) {
      const selId = store.state.selectedBuildingId;
      const b = hover ?? (selId ? BUILDING_BY_ID.get(selId) ?? null : null);
      if (!b || t.k < 1400) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const p = projection(b.c)!;
      const x = t.applyX(p[0]);
      const y = t.applyY(p[1]);
      const title = b.n ?? b.id;
      const sub = `${b.sys} · ${b.lv} LVL · ${SENSORS.filter((sn) => sn.buildingId === b.id).length} SENSORS`;
      ctx.font = '600 11px "JetBrains Mono", monospace';
      const w1 = ctx.measureText(title).width;
      ctx.font = '400 9px "JetBrains Mono", monospace';
      const w2 = ctx.measureText(sub).width;
      const bw = Math.max(w1, w2) + 16;
      const bx = Math.min(x + 12, W - bw - 8);
      const by = Math.max(y - 44, 8);
      ctx.fillStyle = 'rgba(12,18,32,0.92)';
      ctx.strokeStyle = 'rgba(34,211,238,0.5)';
      ctx.lineWidth = 1;
      ctx.fillRect(bx, by, bw, 34);
      ctx.strokeRect(bx, by, bw, 34);
      ctx.font = '600 11px "JetBrains Mono", monospace';
      ctx.fillStyle = hover ? '#e2e8f0' : ACCENT;
      ctx.fillText(title, bx + 8, by + 14);
      ctx.font = '400 9px "JetBrains Mono", monospace';
      ctx.fillStyle = '#7d8ba3';
      ctx.fillText(sub, bx + 8, by + 26);
    }

    function drawMini(t: typeof zoomIdentity) {
      const mw = 176, mh = 96;
      mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      mctx.fillStyle = '#0a0f1c';
      mctx.fillRect(0, 0, mw, mh);
      const mp = geoEquirectangular().fitExtent([[2, 2], [mw - 2, mh - 2]], LAND as GeoJSON.FeatureCollection);
      const mpath = geoPath(mp, mctx);
      mctx.fillStyle = '#14203a';
      mctx.beginPath();
      mpath(LAND as never);
      mctx.fill();
      // viewport footprint: project the 4 screen corners back to lon/lat
      const corners: [number, number][] = [[0, 0], [W, 0], [W, H], [0, H], [0, 0]];
      mctx.strokeStyle = ACCENT;
      mctx.lineWidth = 1;
      mctx.beginPath();
      corners.forEach((c, i) => {
        const ll = projection.invert!(t.invert(c)) as [number, number];
        const p = mp(ll)!;
        if (i === 0) mctx.moveTo(p[0], p[1]);
        else mctx.lineTo(p[0], p[1]);
      });
      mctx.stroke();
      const hq = mp(CITY_CENTER)!;
      mctx.fillStyle = ACCENT;
      mctx.beginPath(); mctx.arc(hq[0], hq[1], 2, 0, Math.PI * 2); mctx.fill();
    }

    // --- frame loop -----------------------------------------------------------
    function frame(now: number) {
      if (disposed) return;
      const dt = now - lastFrame;
      lastFrame = now;
      fps = fps * 0.92 + (1000 / Math.max(1, dt)) * 0.08;
      draw(now);
      if (now - cameraThrottle > 250 && pointer) {
        cameraThrottle = now;
        const ll = toLonLat(pointer[0], pointer[1]);
        onCamera({ lon: ll[0], lat: ll[1], k: transform.current.k, fps: Math.round(fps) });
      } else if (now - cameraThrottle > 500) {
        cameraThrottle = now;
        onCamera({ lon: NaN, lat: NaN, k: transform.current.k, fps: Math.round(fps) });
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      unsubStore();
      sel.on('.zoom', null);
    };
  }, [onCamera, reducedMotion]);

  return (
    <div ref={wrapRef} className="absolute inset-0">
      <canvas ref={canvasRef} className="block outline-none" aria-label="Spatial canvas: continuous zoom from planet to building interior. Use plus and minus keys to zoom, arrow keys to pan, Escape to clear selection." />
      <canvas ref={miniRef} className="absolute bottom-3 right-3 border border-[#1a2540] bg-[#0a0f1c]" aria-hidden />
      {hoverId && (
        <div className="sr-only" aria-live="polite">Building {hoverId} under cursor</div>
      )}
    </div>
  );
}

function hexA(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
