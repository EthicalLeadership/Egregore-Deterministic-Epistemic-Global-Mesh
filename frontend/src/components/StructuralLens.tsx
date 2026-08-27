// Structural lens: force-directed graph of city systems (hub nodes) linked to
// the buildings that depend on them. Clicking a system node dispatches
// SET_SYSTEM_FILTER — a global filter the spatial view consumes.

import { useEffect, useMemo, useRef } from 'react';
import {
  forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide,
  type SimulationNodeDatum, type SimulationLinkDatum,
} from 'd3-force';
import { CITY, SYSTEMS, SENSORS, sensorActiveInRange, BUILDING_BY_ID } from '../lib/data';
import { store } from '../lib/store';
import { useStore } from '../hooks/useStore';

interface Node extends SimulationNodeDatum {
  id: string;
  kind: 'system' | 'hub' | 'building';
  sys: string;
  label: string;
  r: number;
}

interface Link extends SimulationLinkDatum<Node> {
  source: string | Node;
  target: string | Node;
}

export function StructuralLens({ reducedMotion }: { reducedMotion: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useStore();

  const { nodes, links } = useMemo(() => {
    const nodes: Node[] = [];
    const links: Link[] = [];
    for (const sys of SYSTEMS) {
      nodes.push({ id: sys.key, kind: 'system', sys: sys.key, label: sys.label, r: 9 });
      for (const hub of sys.hubs) {
        nodes.push({ id: hub, kind: 'hub', sys: sys.key, label: hub, r: 4 });
        links.push({ source: sys.key, target: hub });
      }
    }
    // attach a deterministic sample of buildings per system
    for (const b of CITY.buildings) {
      if (b.seed % 3 !== 0) continue; // keep the graph legible
      const hubIdx = b.seed % 3;
      const hub = SYSTEMS.find((s) => s.key === b.sys)!.hubs[hubIdx];
      nodes.push({ id: b.id, kind: 'building', sys: b.sys, label: b.id, r: 2.4 });
      links.push({ source: hub, target: b.id });
    }
    return { nodes, links };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext('2d')!;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let W = 10, H = 10;
    let raf = 0;
    let hovered: Node | null = null;

    function resize() {
      const r = canvas.getBoundingClientRect();
      W = Math.max(10, r.width); H = Math.max(10, r.height);
      canvas.width = W * dpr; canvas.height = H * dpr;
      sim.force('center', forceCenter(W / 2, H / 2));
      sim.alpha(0.4).restart();
    }

    const sim = forceSimulation(nodes)
      .force('link', forceLink<Node, Link>(links).id((d) => d.id).distance((l) => {
        const s = l.source as Node;
        return s.kind === 'system' ? 34 : 14;
      }).strength(0.6))
      .force('charge', forceManyBody().strength(-26))
      .force('collide', forceCollide().radius((d) => (d as Node).r + 2))
      .force('center', forceCenter(180, 130));

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function nodeAt(x: number, y: number): Node | null {
      for (const n of nodes) {
        if (Math.hypot((n.x ?? 0) - x, (n.y ?? 0) - y) < Math.max(7, n.r)) return n;
      }
      return null;
    }

    const onMove = (ev: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      hovered = nodeAt(ev.clientX - r.left, ev.clientY - r.top);
      canvas.style.cursor = hovered && hovered.kind !== 'building' ? 'pointer' : 'default';
    };
    const onClick = (ev: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const n = nodeAt(ev.clientX - r.left, ev.clientY - r.top);
      if (!n) return;
      if (n.kind === 'system' || n.kind === 'hub') {
        const next = store.state.systemFilter === n.sys ? null : n.sys;
        store.dispatch('SET_SYSTEM_FILTER', { system: next });
      } else {
        store.dispatch('SELECT_BUILDING', { id: n.id });
      }
    };
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('click', onClick);

    function draw(now: number) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const s = store.state;
      const range = s.timeRange;
      // keep the graph inside the lens
      for (const n of nodes) {
        n.x = Math.max(36, Math.min(W - 36, n.x ?? W / 2));
        n.y = Math.max(30, Math.min(H - 14, n.y ?? H / 2));
      }

      // links
      for (const l of links) {
        const a = l.source as Node;
        const b = l.target as Node;
        const sysOk = !s.systemFilter || a.sys === s.systemFilter;
        ctx.strokeStyle = sysOk ? 'rgba(125,139,163,0.20)' : 'rgba(125,139,163,0.05)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x ?? 0, a.y ?? 0);
        ctx.lineTo(b.x ?? 0, b.y ?? 0);
        ctx.stroke();
      }

      const pulse = reducedMotion ? 0.5 : (Math.sin(now / 400) + 1) / 2;
      for (const n of nodes) {
        const col = SYSTEMS.find((sy) => sy.key === n.sys)?.color ?? '#94a3b8';
        const sysOk = !s.systemFilter || n.sys === s.systemFilter;
        let alpha = sysOk ? 1 : 0.12;
        // temporal coordination: building nodes dim when their sensors are
        // quiet inside the brushed window
        if (n.kind === 'building') {
          const sn = SENSORS.filter((x) => x.buildingId === n.id);
          const anyActive = sn.some((x) => sensorActiveInRange(x, range));
          if (!anyActive) alpha *= 0.25;
        }
        const isSel = n.id === s.selectedBuildingId;
        const isFiltered = s.systemFilter === n.sys && n.kind === 'system';

        ctx.globalAlpha = alpha;
        if (n.kind === 'system') {
          ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, n.r, 0, Math.PI * 2); ctx.fill();
          ctx.strokeStyle = isFiltered ? '#fff' : col;
          ctx.lineWidth = isFiltered ? 2 : 1;
          ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, n.r + 3 + (isFiltered ? pulse * 2 : 0), 0, Math.PI * 2); ctx.stroke();
          ctx.font = '600 8.5px "JetBrains Mono", monospace';
          ctx.fillStyle = sysOk ? '#cbd5e1' : '#475569';
          ctx.textAlign = 'center';
          ctx.fillText(n.sys, n.x ?? 0, (n.y ?? 0) - n.r - 6);
        } else if (n.kind === 'hub') {
          ctx.strokeStyle = col;
          ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, n.r, 0, Math.PI * 2); ctx.stroke();
        } else {
          ctx.fillStyle = isSel ? '#fff' : col;
          ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, isSel ? n.r + 1.6 : n.r, 0, Math.PI * 2); ctx.fill();
          if (isSel) {
            ctx.strokeStyle = '#22d3ee';
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, n.r + 4, 0, Math.PI * 2); ctx.stroke();
          }
        }
        ctx.globalAlpha = 1;
      }
      raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);

    // keep the sim gently alive for organic drift
    const ticker = setInterval(() => sim.alpha(Math.max(sim.alpha(), reducedMotion ? 0 : 0.02)), 1000);

    return () => {
      cancelAnimationFrame(raf);
      clearInterval(ticker);
      ro.disconnect();
      sim.stop();
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('click', onClick);
    };
  }, [nodes, links, reducedMotion]);

  const buildingCount = state.systemFilter
    ? CITY.buildings.filter((b) => b.sys === state.systemFilter).length
    : CITY.buildings.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[#1a2540] px-3 py-1.5">
        <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
          Structural Lens · Systems
        </span>
        {state.systemFilter && (
          <button
            onClick={() => store.dispatch('SET_SYSTEM_FILTER', { system: null })}
            className="font-mono text-[9px] text-cyan-400 hover:text-white"
          >
            CLEAR ×
          </button>
        )}
      </div>
      <div className="relative min-h-0 flex-1">
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-label="Structural lens: click a system node to filter all views" />
      </div>
      <div className="border-t border-[#1a2540] px-3 py-1 text-[9px] tracking-wider text-slate-500 uppercase">
        {state.systemFilter
          ? `${state.systemFilter} · ${buildingCount} buildings in view`
          : `4 systems · ${buildingCount} buildings`}{' '}
        · node size reflects role
      </div>
      {state.selectedBuildingId && (
        <div className="border-t border-[#1a2540] px-3 py-1.5 font-mono text-[9px] text-slate-400">
          SELECTED → <span className="text-cyan-400">{state.selectedBuildingId}</span>
          {' · '}{BUILDING_BY_ID.get(state.selectedBuildingId)?.sys}
        </div>
      )}
    </div>
  );
}
