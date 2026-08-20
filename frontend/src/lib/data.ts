// Data layer: bundled vector data + deterministic procedural sensor lattice.
// Land: Natural Earth 1:110m (public domain). City: OpenStreetMap (ODbL),
// pre-processed into compact geometry — no runtime Overpass calls.

import { feature } from 'topojson-client';
import type { GeometryObject, Topology } from 'topojson-specification';
import landTopo from '../assets/land-110m.json';
import mtl from '../assets/montreal.json';

// ---------- World land ----------
const topo = landTopo as unknown as Topology;
export const LAND = feature(
  topo,
  topo.objects.land as GeometryObject
) as GeoJSON.FeatureCollection | GeoJSON.Feature;

// ---------- Montréal dataset ----------
export interface Road {
  t: string;
  p: [number, number][];
}
export interface Building {
  id: string;
  p: [number, number][];
  c: [number, number];
  lv: number;
  n: string | null;
  sys: string;
  seed: number;
}
interface MtlData {
  roads: Road[];
  buildings: Building[];
  bbox: [number, number, number, number];
}
export const MTL = mtl as unknown as MtlData;
export const MTL_CENTER: [number, number] = [-73.5673, 45.5035];

// ---------- Seeded RNG (deterministic sensor data) ----------
export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- Systems (structural lens) ----------
export interface SystemDef {
  key: string;
  label: string;
  color: string;
  hubs: string[];
}
export const SYSTEMS: SystemDef[] = [
  { key: 'POWER', label: 'Power Grid', color: '#fbbf24', hubs: ['HV-SUBSTATION', 'FEEDER-A', 'FEEDER-B'] },
  { key: 'TRANSIT', label: 'Transit Net', color: '#22d3ee', hubs: ['METRO-LINK', 'BUS-RELAY', 'SIGNAL-CTL'] },
  { key: 'WATER', label: 'Water Loop', color: '#34d399', hubs: ['PUMP-NORTH', 'PUMP-SOUTH', 'RESERVOIR'] },
  { key: 'COMMS', label: 'Comms Mesh', color: '#f472b6', hubs: ['CORE-ROUTER', 'EDGE-NODE-1', 'EDGE-NODE-2'] },
];
export const SYSTEM_COLOR = new Map(SYSTEMS.map((s) => [s.key, s.color]));

// ---------- Sensors ----------
export interface Sensor {
  id: string;
  buildingId: string;
  lon: number;
  lat: number;
  kind: 'HVAC' | 'POWER' | 'ACCESS' | 'ENV';
  /** building system color, precomputed for the render loop */
  col: string;
  /** 96 bins of 15 min across a synthetic 24h cycle */
  series: number[];
}

const KINDS: Sensor['kind'][] = ['HVAC', 'POWER', 'ACCESS', 'ENV'];

function pointInPolygon(pt: [number, number], poly: [number, number][]) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (
      yi > pt[1] !== yj > pt[1] &&
      pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi
    ) {
      inside = !inside;
    }
  }
  return inside;
}

function buildSensors(b: Building): Sensor[] {
  const rnd = mulberry32(b.seed);
  const lons = b.p.map((p) => p[0]);
  const lats = b.p.map((p) => p[1]);
  const minX = Math.min(...lons), maxX = Math.max(...lons);
  const minY = Math.min(...lats), maxY = Math.max(...lats);
  const count = 3 + Math.floor(rnd() * 5);
  const sensors: Sensor[] = [];
  let guard = 0;
  while (sensors.length < count && guard++ < 60) {
    const lon = minX + rnd() * (maxX - minX);
    const lat = minY + rnd() * (maxY - minY);
    if (!pointInPolygon([lon, lat], b.p)) continue;
    const kind = KINDS[Math.floor(rnd() * KINDS.length)];
    // synthetic daily profile: base load + business-hours bump + noise
    const series: number[] = [];
    const phase = rnd() * Math.PI * 2;
    const amp = 0.3 + rnd() * 0.5;
    for (let h = 0; h < 96; h++) {
      const t = h / 4; // hours
      const business = Math.exp(-Math.pow(t - 13, 2) / 18);
      const v =
        0.25 + amp * business + 0.12 * Math.sin(t / 3 + phase) + rnd() * 0.12;
      series.push(Math.max(0.02, Math.min(1, v)));
    }
    sensors.push({ id: `${b.id}/S${sensors.length}`, buildingId: b.id, lon, lat, kind, col: SYSTEM_COLOR.get(b.sys) ?? '#22d3ee', series });
  }
  return sensors;
}

export const SENSORS: Sensor[] = MTL.buildings.flatMap(buildSensors);
export const SENSORS_BY_BUILDING = new Map<string, Sensor[]>();
for (const s of SENSORS) {
  const arr = SENSORS_BY_BUILDING.get(s.buildingId) ?? [];
  arr.push(s);
  SENSORS_BY_BUILDING.set(s.buildingId, arr);
}
export const BUILDING_BY_ID = new Map(MTL.buildings.map((b) => [b.id, b]));

/** Aggregate activity per 15-min bin (0..95) across a sensor subset. */
export function aggregateSeries(sensors: Sensor[]): number[] {
  const out = new Array(96).fill(0);
  for (const s of sensors) for (let i = 0; i < 96; i++) out[i] += s.series[i];
  const max = Math.max(...out, 1e-6);
  return out.map((v) => v / max);
}

/** Is a sensor "active" inside the brushed time window (hours 0..24)? */
export function sensorActiveInRange(s: Sensor, range: [number, number]): boolean {
  const i0 = Math.max(0, Math.floor(range[0] * 4));
  const i1 = Math.min(95, Math.ceil(range[1] * 4));
  let sum = 0;
  for (let i = i0; i <= i1; i++) sum += s.series[i];
  return sum / Math.max(1, i1 - i0 + 1) > 0.42;
}

// ---------- World cities (watchlist / region tier) ----------
export interface City {
  name: string;
  country: string;
  lon: number;
  lat: number;
  pop: number; // millions
  hq?: boolean;
}
export const CITIES: City[] = [
  { name: 'Montréal', country: 'CA', lon: -73.5673, lat: 45.5035, pop: 4.3, hq: true },
  { name: 'Toronto', country: 'CA', lon: -79.3832, lat: 43.6532, pop: 6.4 },
  { name: 'Vancouver', country: 'CA', lon: -123.1207, lat: 49.2827, pop: 2.6 },
  { name: 'New York', country: 'US', lon: -74.006, lat: 40.7128, pop: 19.5 },
  { name: 'San Francisco', country: 'US', lon: -122.4194, lat: 37.7749, pop: 4.7 },
  { name: 'Mexico City', country: 'MX', lon: -99.1332, lat: 19.4326, pop: 22.0 },
  { name: 'São Paulo', country: 'BR', lon: -46.6333, lat: -23.5505, pop: 22.4 },
  { name: 'Buenos Aires', country: 'AR', lon: -58.3816, lat: -34.6037, pop: 15.2 },
  { name: 'London', country: 'GB', lon: -0.1276, lat: 51.5074, pop: 14.8 },
  { name: 'Paris', country: 'FR', lon: 2.3522, lat: 48.8566, pop: 11.2 },
  { name: 'Berlin', country: 'DE', lon: 13.405, lat: 52.52, pop: 6.0 },
  { name: 'Madrid', country: 'ES', lon: -3.7038, lat: 40.4168, pop: 6.7 },
  { name: 'Lagos', country: 'NG', lon: 3.3792, lat: 6.5244, pop: 15.4 },
  { name: 'Cairo', country: 'EG', lon: 31.2357, lat: 30.0444, pop: 21.3 },
  { name: 'Nairobi', country: 'KE', lon: 36.8219, lat: -1.2921, pop: 4.9 },
  { name: 'Mumbai', country: 'IN', lon: 72.8777, lat: 19.076, pop: 21.0 },
  { name: 'Singapore', country: 'SG', lon: 103.8198, lat: 1.3521, pop: 6.0 },
  { name: 'Tokyo', country: 'JP', lon: 139.6917, lat: 35.6895, pop: 37.4 },
  { name: 'Seoul', country: 'KR', lon: 126.978, lat: 37.5665, pop: 25.5 },
  { name: 'Sydney', country: 'AU', lon: 151.2093, lat: -33.8688, pop: 5.3 },
];

export const LAYER_DEFS = [
  { key: 'land', label: 'Land Masses' },
  { key: 'graticule', label: 'Graticule' },
  { key: 'cities', label: 'Metro Markers' },
  { key: 'flowlines', label: 'Flow Lines' },
  { key: 'roads', label: 'Road Network' },
  { key: 'buildings', label: 'Footprints' },
  { key: 'sensors', label: 'Sensor Lattice' },
];
