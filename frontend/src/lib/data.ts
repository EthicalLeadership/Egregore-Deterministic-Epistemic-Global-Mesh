// Data layer: bundled vector data + deterministic procedural sensor lattice.
// Land/city: fictional "Blackstar" world (de-identified) — plain GeoJSON land
// and pre-processed city geometry. No runtime network calls, no topojson decode
// needed (LAND is already a FeatureCollection).

import blackstarCity from '../assets/blackstar-city.json';
import blackstarLand from '../assets/blackstar-land.json';

// ---------- World land (already plain GeoJSON) ----------
export const LAND = blackstarLand as unknown as GeoJSON.FeatureCollection | GeoJSON.Feature;

// ---------- Blackstar city dataset (formerly Montréal) ----------
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
interface CityData {
  roads: Road[];
  buildings: Building[];
  bbox: [number, number, number, number];
}
export const CITY = blackstarCity as unknown as CityData;
export const CITY_CENTER: [number, number] = [
  (CITY.bbox[0] + CITY.bbox[2]) / 2,
  (CITY.bbox[1] + CITY.bbox[3]) / 2,
];

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

export const SENSORS: Sensor[] = CITY.buildings.flatMap(buildSensors);
export const SENSORS_BY_BUILDING = new Map<string, Sensor[]>();
for (const s of SENSORS) {
  const arr = SENSORS_BY_BUILDING.get(s.buildingId) ?? [];
  arr.push(s);
  SENSORS_BY_BUILDING.set(s.buildingId, arr);
}
export const BUILDING_BY_ID = new Map(CITY.buildings.map((b) => [b.id, b]));

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
// Fictional Blackstar node-cities. `hq` marks the fully-built capital.
// `pop` is a raw headcount — resolution for rendering happens in the canvas
// (log-scale marker radius), not baked into the data.
export interface CityMarker {
  name: string;
  lon: number;
  lat: number;
  hq: boolean;
  pop: number;
}
export const CITIES: CityMarker[] = [
  // The detailed city — formerly Montréal, now fully built out with
  // CITY.buildings/roads. Capital of the Information Arm.
  { name: 'Lumen Prime', lon: CITY_CENTER[0], lat: CITY_CENTER[1], hq: true, pop: 3_000_000 },

  // Remote node-cities — markers only, no building-level detail.
  { name: 'The Mint',       lon: -23.5, lat: 6,   hq: false, pop: 900_000 },  // Aurum Reach (Economic Arm)
  { name: 'Forge Prime',    lon: 22,    lat: 6,   hq: false, pop: 750_000 },  // Ferrum Expanse (Builder)
  { name: 'Redoubt',        lon: 53,    lat: -24, hq: false, pop: 600_000 },  // Bastion Rim (Defense)
  { name: 'Sentinel Cairn', lon: 0,     lat: -22, hq: false, pop: 40_000 },   // Auditor's Isle
  { name: 'Proxima Vale',   lon: -144,  lat: -55, hq: false, pop: 5_000 },    // Fractal Archipelago (colony)
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
