// Hierarchical scale-tier statechart with hysteresis-banded transitions.
// The band (default 2.5x) is a tunable heuristic, not an empirical constant —
// it prevents flicker when the camera hovers near a tier boundary:
// zooming IN across edge*sqrt(B) promotes a tier; zooming OUT below
// edge/sqrt(B) demotes it.

export interface Tier {
  id: number;
  key: string;
  label: string;
  /** zoom-transform scale at which this tier becomes dominant */
  edge: number;
  description: string;
}

export const TIERS: Tier[] = [
  { id: 0, key: 'PLANET', label: 'Planet', edge: 1, description: 'Global context · land masses · graticule' },
  { id: 1, key: 'REGION', label: 'Region', edge: 6, description: 'Continental context · metro markers' },
  { id: 2, key: 'CITY', label: 'City', edge: 300, description: 'Urban fabric · road network' },
  { id: 3, key: 'DISTRICT', label: 'District', edge: 2500, description: 'Blocks · building footprints' },
  { id: 4, key: 'BUILDING', label: 'Building', edge: 12000, description: 'Footprint detail · sensor lattice' },
  { id: 5, key: 'ROOM', label: 'Room', edge: 60000, description: 'Interior schematic · live sensors' },
];

export interface Transition {
  from: number;
  to: number;
  k: number;
  direction: 'IN' | 'OUT';
  band: number;
}

export class TierMachine {
  tier = 0;
  private listeners = new Set<(t: Transition) => void>();

  onTransition(l: (t: Transition) => void) {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  /** Feed the current zoom-transform scale k. Returns true if tier changed. */
  update(k: number, band: number): boolean {
    const s = Math.sqrt(Math.max(1.0001, band));
    let t = this.tier;
    let changed = false;
    // promote while possible
    while (t < TIERS.length - 1 && k > TIERS[t + 1].edge * s) {
      t++;
      changed = true;
    }
    // demote while possible
    while (t > 0 && k < TIERS[t].edge / s) {
      t--;
      changed = true;
    }
    if (changed && t !== this.tier) {
      const tr: Transition = {
        from: this.tier,
        to: t,
        k,
        direction: t > this.tier ? 'IN' : 'OUT',
        band,
      };
      this.tier = t;
      this.listeners.forEach((l) => l(tr));
      return true;
    }
    return false;
  }

  reset() {
    this.tier = 0;
  }
}

export const tierMachine = new TierMachine();
