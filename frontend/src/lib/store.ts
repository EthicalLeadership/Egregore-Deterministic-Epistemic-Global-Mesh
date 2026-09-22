// Event-sourced coordination store.
// Every cross-view interaction is an appended event; all shared state is derived
// by replaying the log. Undo = move the cursor back; Redo = forward; the event
// log panel is a literal view of this structure.

export type AppEventType =
  | 'INIT'
  | 'FLY_TO' // camera flight request: { lon, lat, k }
  | 'TIER_CHANGE' // semantic zoom tier changed: { tier }
  | 'SELECT_BUILDING' // { id | null }
  | 'SET_SYSTEM_FILTER' // { system | null }
  | 'SET_TIME_RANGE' // { t0, t1 } fractional hours 0..24
  | 'TOGGLE_LAYER' // { layer, on }
  | 'SET_HYSTERESIS' // { band }
  | 'SERVICE_ACTION' // { service, action }
  | 'RESET_CAMERA'
  | 'CLEAR_SELECTION';

export interface AppEvent {
  seq: number;
  at: number; // ms timestamp
  type: AppEventType;
  payload: Record<string, unknown>;
}

export interface DerivedState {
  tier: number;
  selectedBuildingId: string | null;
  systemFilter: string | null;
  timeRange: [number, number];
  layers: Record<string, boolean>;
  hysteresisBand: number;
  cameraTarget: { lon: number; lat: number; k: number } | null;
  cameraResetSeq: number;
}

export const DEFAULT_LAYERS: Record<string, boolean> = {
  land: true,
  graticule: true,
  cities: true,
  roads: true,
  buildings: true,
  sensors: true,
  flowlines: true,
};

const initial: DerivedState = {
  tier: 0,
  selectedBuildingId: null,
  systemFilter: null,
  timeRange: [0, 24],
  layers: { ...DEFAULT_LAYERS },
  hysteresisBand: 2.5,
  cameraTarget: null,
  cameraResetSeq: 0,
};

export function reduce(state: DerivedState, ev: AppEvent): DerivedState {
  switch (ev.type) {
    case 'TIER_CHANGE':
      return { ...state, tier: ev.payload.tier as number };
    case 'SELECT_BUILDING':
      return { ...state, selectedBuildingId: ev.payload.id as string | null };
    case 'CLEAR_SELECTION':
      return { ...state, selectedBuildingId: null, systemFilter: null };
    case 'SET_SYSTEM_FILTER':
      return { ...state, systemFilter: ev.payload.system as string | null };
    case 'SET_TIME_RANGE':
      return {
        ...state,
        timeRange: [ev.payload.t0 as number, ev.payload.t1 as number],
      };
    case 'TOGGLE_LAYER': {
      const layers = { ...state.layers, [ev.payload.layer as string]: ev.payload.on as boolean };
      return { ...state, layers };
    }
    case 'SET_HYSTERESIS':
      return { ...state, hysteresisBand: ev.payload.band as number };
    case 'FLY_TO':
      return {
        ...state,
        cameraTarget: {
          lon: ev.payload.lon as number,
          lat: ev.payload.lat as number,
          k: ev.payload.k as number,
        },
      };
    case 'RESET_CAMERA':
      return {
        ...state,
        cameraTarget: null,
        selectedBuildingId: null,
        systemFilter: null,
        cameraResetSeq: ev.payload.seq as number,
      };
    default:
      return state;
  }
}

type Listener = () => void;

class EventStore {
  events: AppEvent[] = [
    { seq: 0, at: Date.now(), type: 'INIT', payload: {} },
  ];
  cursor = 1; // number of events currently applied
  state: DerivedState = { ...initial };
  private listeners = new Set<Listener>();
  private pending: AppEventType | null = null;
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;

  /** Recompute derived state by replaying events up to cursor. */
  private recompute() {
    let s: DerivedState = { ...initial, layers: { ...DEFAULT_LAYERS } };
    for (let i = 0; i < this.cursor; i++) s = reduce(s, this.events[i]);
    this.state = s;
    this.emit();
  }

  private emit() {
    this.listeners.forEach((l) => l());
  }

  subscribe(l: Listener): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  /**
   * Dispatch an event. Per the blueprint's debounce discipline: rendering
   * reacts in the same frame (camera/zoom internals live outside this store),
   * while cross-view coordination events are debounced at 200ms.
   */
  dispatch(type: AppEventType, payload: Record<string, unknown>, opts?: { debounce?: boolean }) {
    const apply = () => {
      // truncate any "future" events if we had undone
      this.events = this.events.slice(0, this.cursor);
      const ev: AppEvent = { seq: this.cursor, at: Date.now(), type, payload: { ...payload, seq: this.cursor } };
      this.events.push(ev);
      this.cursor++;
      this.recompute();
    };
    if (opts?.debounce) {
      if (this.debounceTimer && this.pending !== type) {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = null;
        // different event family arrived: flush immediately below
      }
      this.pending = type;
      if (this.debounceTimer) clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        this.debounceTimer = null;
        this.pending = null;
        apply();
      }, 200);
    } else {
      apply();
    }
  }

  undo() {
    if (this.cursor > 1) {
      this.cursor--;
      this.recompute();
    }
  }

  redo() {
    if (this.cursor < this.events.length) {
      this.cursor++;
      this.recompute();
    }
  }

  canUndo() { return this.cursor > 1; }
  canRedo() { return this.cursor < this.events.length; }
}

export const store = new EventStore();
