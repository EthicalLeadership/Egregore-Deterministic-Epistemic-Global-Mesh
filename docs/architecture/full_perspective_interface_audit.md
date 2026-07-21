# Full-Perspective Interface Architecture — Feasibility Audit of the Egregore Codebase

**Date:** 2026-07-19  
**Scope:** Egregore repository (`/opt/egregore`) — Python/FastAPI backend, React/TypeScript/Vite frontend, supporting prototypes and data assets.  
**Deliverable type:** Written audit only; no code was modified.  

---

## Executive Verdict

The Full-Perspective Interface Architecture is **theoretically possible** in Egregore, but it is **not currently realizable as a production feature without substantial new work**. The codebase contains strong foundations for Layers 2 and 4 (analytical data, event causality, UI primitives) and the right frontend libraries for Layer 3 (Three.js/React Three Fiber are already installed), but the **core assumption of Layer 1 — a geospatial/physical hierarchy from planet down to room — has no native data model**. Every spatial transition described in the research document would have to be synthetic or metaphorical, and the existing prototypes are visual sketches rather than implementations of the proposed D3/TopoJSON/Three.js stack.

A second, equally important dimension surfaced during this audit: **Egregore is not only an ANCHORUM/legal system**. It hosts multiple verticals (`legal`, `operations`, `dt1`, `investigation`, `university`, `guild`, etc.), a catalog of local GGUF and hosted models, a small but growing agent registry, and an agency taxonomy (species/biome/lobe) that is the closest existing match to the user's "race" metaphor. The user wants to click on a **model** or **agent** and inspect its real skill levels, advantages, and weaknesses. That metadata **does not yet exist**; only raw capabilities, verification rules, and runtime load are currently captured.

**Bottom line:** This is a UI *aspiration* layered on top of a system whose data are organizational, legal, and computational. The most pragmatic realization is a **metaphorical spatial map** (federation → vertical → cell → model/agent → work-unit) rather than a true planet-to-room geographic zoom. Even that path requires a new shared state layer, real-time streaming, a model/agent capability benchmark layer, and a 3D scene built from scratch.

| Layer | Feasibility | Confidence | Primary blocker |
|-------|-------------|------------|-----------------|
| 1. Spatial Zoom | 🟡 Yellow / 🔴 Red | Medium | No geospatial/physical data model; no D3/TopoJSON integration; prototypes are DOM/CSS, not production globe code. |
| 2. Analytical Lenses | 🟢 Green | High | Data exists (RFE, ANCHORUM, ombudsman graph, telemetry); needs shared reactive store and lens components. |
| 3. Immersive Navigation | 🟡 Yellow | Medium | Libraries installed but unused; no WebXR; cross-section is a DOM prototype. |
| 4. Context Preservation | 🟡 Yellow | Medium | Only generic breadcrumb exists; mini-map, sticky objects, fractal grid, and URL state are absent. |
| 5. Model/Agent Race Graph | 🟡 Yellow | Medium | Catalogs and registries exist, but skill levels, advantages, weaknesses, and race-to-agent mappings are missing. |
| Cross-layer integration | 🟡 Yellow | Low-Medium | Dashboard uses local `useState` and 3 s polling; no shared selection/coordination bus. |

---

## 1. What the codebase actually is

Egregore is a **sovereign, deterministic execution runtime** with a strong governance and evidence-fusion story:

- **Backend:** Python 3.11+ FastAPI app (`src/egregore/http_api/http/app.py`, `src/egregore/interface/bootstrap.py`) organized into kernel → domain → application → interface → infrastructure layers (`docs/architecture/01_geological_model.md`).
- **Frontend:** React 19 + Vite + TypeScript + Tailwind + Radix UI (`frontend/src/App.tsx`, `frontend/package.json`).
- **Data:** Immutable `.zarc` provenance logs, RFE (Reproducible Fusion Engine) reports, ANCHORUM forensic reports, cell taxonomy registry, telemetry pulses, local model catalogs, and an agent registry.
- **Real-time:** NATS/JetStream, WebSocket chat, telemetry gates — but the dashboard **polls** `/api/dashboard` every 3 seconds (`frontend/src/hooks/useDashboard.ts`).

The system already thinks in **hierarchies and graphs** — cell taxonomies, layer architecture, causal chains, cluster nodes, model verticals, and agency species — but it does **not** think in latitude, longitude, buildings, floors, or rooms. That mismatch is the central finding of this audit.

---

## 2. Layer 1: Spatial Zoom (Russian-Doll Navigation)

### 2.1 Research requirements

The architecture demands five continuous scales:

| Scale | Visible objects | Representation |
|-------|-----------------|----------------|
| Planet (1:100M) | Continents, oceans, major cities | Simplified polygons, glow markers |
| Region (1:10M) | Countries, provinces, terrain | Admin-1 boundaries, elevation shading |
| City (1:100K) | Districts, major roads, landmarks | Vector tiles, building footprints |
| Building (1:1K) | Rooms, corridors, equipment | Floor plans, furniture, sensor data |
| Room (1:100) | Individual devices, people | Real-time telemetry, video feeds |

It also requires:
- Furnas-style semantic zoom thresholds with hysteresis bands (2.5× entry/exit).
- McGuffin-style expanding targets for city/building acquisition.
- D3 + TopoJSON world-atlas + Natural Earth admin-1 + Overpass Turbo.
- Orthographic → transverse Mercator cross-fades, GSAP animation, <5,000 SVG vertices.

### 2.2 What exists in Egregore

**Frontend stack:** `three@0.185.1`, `@react-three/fiber@9.6.1`, `@react-three/drei@10.7.7`, and `gsap@3.15.0` are already in `frontend/package.json`. `d3` is **not** a dependency.

**Prototypes:** Two plain HTML/CSS/JS prototypes explore the *visual metaphor* but not the proposed implementation stack:
- `static/egregore_command_surface.html` — implements Cross-section, City Grid, Globe, Constellation, Tunnel, and Modules views. The globe is a CSS/DOM construct (concentric rings and satellites), not a D3 orthographic projection or Three.js sphere.
- `egregore_constellation.html` — a sun/planet orbit diagram with particle stars; again DOM + 2D Canvas, not a zoomable geospatial scene.

**Backend data:** No `latitude`, `longitude`, `building`, `floor`, `room`, or geospatial geometry exists in the domain. The only location-like fields are:
- `site: str` in DT1 scheduling models (`src/egregore/dt1/models.py`), e.g. `mtl01`, `yyz01`.
- `cluster_id`, `node_id` in the global event schema (`docs/architecture/13_global_event_schema.md`).
- Cell taxonomy paths (`root/branch/leaf/specialty`) in `src/egregore/cells/models.py`.

### 2.3 Critical gap: the spatial model is missing

The research document assumes the data *are* geographic. Egregore's data are **computational/legal**. There is no planet, continent, city, building, or room to zoom to. Any implementation must either:

1. **Invent a synthetic Location model** and ingest real-world coordinates for nodes, sites, and devices (high cost, high fidelity, questionable utility for this domain).
2. **Treat the spatial layers as a computational/race metaphor** (recommended): planet = entire Egregore federation; continent = vertical family (`legal`, `operations`, `dt1`, `investigation`, `university`, `guild`); city = cell or taxonomy branch; building = model/race; room = agent or work-unit. This is intellectually coherent, aligns with the user's model-as-race concept, and lets users click from federation → vertical → cell → model → agent.

Either way, the **semantic zoom thresholds** described by Furnas cannot be tuned against existing data because the thresholds have no empirical anchor. The 2.5× hysteresis band is a tuning parameter, not a derivable constant.

### 2.4 Feasibility assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| D3 + TopoJSON stack | 🔴 Missing | `d3` not in `frontend/package.json`; no TopoJSON assets. |
| Orthographic globe | 🟡 Possible | Three.js is installed; could build a sphere. D3 orthographic path would need new dependency. |
| Admin-1/city vector data | 🔴 Missing | No Natural Earth, admin-1, or OSM data in repo. |
| Zoom state machine | 🟡 Possible | React state can model it; current dashboard uses only local `useState`. |
| Semantic thresholds + hysteresis | 🟡 Possible | Algorithm is simple; tuning requires data and user testing. |
| Expanding targets (McGuffin) | 🟢 Possible | Can be done in SVG/Canvas/R3F raycaster; no blockers. |
| 60 fps budget | 🟡 Uncertain | No performance baseline exists for the current dashboard. |

**Verdict:** Layer 1 is the highest-risk layer. The visuals can be built, but they will be **a metaphor, not a map**, and the proposed geographic data pipeline does not exist.

---

## 3. Layer 2: Analytical Lenses (Multi-View Coordination)

### 3.1 Research requirements

Four lenses with brushing/linking coordination (Baldonado et al.; North & Shneiderman):

| Lens | Question | Encoding | Coordination |
|------|----------|----------|--------------|
| Temporal | When? | Timeline, Gantt, spiral chart | Brush selection propagates |
| Causal | Why? | DAG, influence diagram, fault tree | Node selection highlights upstream/downstream |
| Structural | What is connected? | Network graph, adjacency matrix | Edge selection shows path through spatial view |
| Operational | What now? | Dashboard, heatmap, alert stream | Live updates, 1 s debounce |

### 3.2 What exists in Egregore

This layer has the **richest existing substrate**:

**Temporal data:**
- Every provenance event carries `timestamp_ns` (`src/egregore/domain/models/event.py`).
- RFE reports contain ISO timestamps, decay half-lives, and sensitivity windows (`src/egregore/rfe/models.py`).
- ANCHORUM reports include a `master_timeline` with 1,044 entries and `timestamp_detected` fields (`ANCHORUM_reports/CASE-00000-00_report.json`).

**Causal data:**
- `CausalityContext`, `VectorClock`, and `CausalityReconstructor` in `src/egregore/application/consistency_and_causality.py` provide deterministic causality ordering.
- RFE decision logs record `conflicts`, `winning_stream_id`, `loser_stream_ids`, and `resolution_rule`.
- ANCHORUM reports contain `supporting_evidence` and entity relationships.

**Structural data:**
- `/api/v1/ombudsman/university/graph` already returns nodes and edges (`src/egregore/interface/ombudsman_router.py`):
  - Nodes: cell_id, taxonomy, type, status, stage, progress, tier.
  - Edges: `advisory` and `dependency` relationships.
- Cell taxonomy (`root/branch/leaf/specialty`) is a natural hierarchical graph.

**Operational data:**
- `Phase0TelemetryPulseAgent` emits gate envelopes (`cpu`, `memory`, `storage`, `network`, `gpu`, `interconnect`) every second to `obs.pulse.<node_id>` (`src/egregore/infrastructure/telemetry/phase0_pulse_agent.py`).
- The dashboard already visualizes compute, inference, power, and network metrics (`frontend/src/components/MetricsGrid.tsx`).

### 3.3 Critical gap: no shared selection/coordination store

The dashboard today is a set of local React states:
- `App.tsx` holds `logsOpen` and `settingsOpen`.
- `useDashboard.ts` holds services, metrics, health, toasts — all isolated to the hook.
- There is no global reactive store, no URL-backed view state, and no brushing/linking bus.

Implementing Baldonado's parsimony and North & Shneiderman's snap-together coordination requires:
1. A shared store (e.g., Zustand, Jotai, or React Context + reducer) for `Selection`, `ActiveLenses`, `TimeRange`, `CrossSection`.
2. Subscriptions from every lens and the spatial view to that store.
3. Animated transitions (200–400 ms) as demanded by Elmqvist et al.'s fluidity criterion.

These are standard frontend engineering tasks, not research problems.

### 3.4 Feasibility assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Temporal data | 🟢 Strong | `timestamp_ns` everywhere; RFE + ANCHORUM timelines. |
| Causal data | 🟢 Strong | Vector clocks, RFE conflicts, decision logs. |
| Structural graph data | 🟢 Strong | `ombudsman/university/graph` endpoint exists. |
| Operational telemetry | 🟢 Strong | `Phase0TelemetryPulseAgent`, dashboard metrics. |
| Timeline/Gantt UI | 🟡 Possible | `recharts` installed but unused; no dedicated timeline component. |
| DAG/fault-tree UI | 🟡 Possible | No graph viz library in deps; R3F or D3 could render it. |
| Brushing/linking store | 🔴 Missing | Local `useState` only; no shared reactive selection. |
| 1 s operational debounce | 🟡 Possible | Requires moving from 3 s polling to WebSocket/SSE. |

**Verdict:** Layer 2 is the **most feasible** layer. The data are already present; the work is UI architecture and coordination plumbing.

---

## 4. Layer 3: Immersive Navigation (3D Spatial Movement)

### 4.1 Research requirements

- Desktop-first, VR-secondary (following CosmoScout VR).
- 3D fly-through with mouse/keyboard.
- Cross-section clipping plane (Space-Time Hypercube model).
- WebXR secondary mode.

### 4.2 What exists in Egregore

**Libraries:** `three`, `@react-three/fiber`, `@react-three/drei`, and an `OrbitControls` type declaration (`frontend/src/types/three-extras.d.ts`) are present. However, **no R3F component is imported anywhere in `frontend/src/` production code.**

**Prototypes:**
- `static/egregore_command_surface.html` has a "Tunnel to Core" view and a "Cross-section" view, but both are DOM/CSS representations, not 3D clipping planes.
- `egregore_constellation.html` uses a 2D canvas particle field.

**Backend:** No 3D geometry, no scene graph API, no WebXR session endpoints.

### 4.3 Critical gap: scene graph must be built from scratch

The cross-section mechanic is described in the research as:
> "Buildings intersecting the plane are shown in full detail; buildings behind the plane are ghosted (20% opacity); buildings in front are hidden."

This requires:
1. A 3D scene with building objects.
2. A user-controllable clipping plane.
3. Shader or material logic for inside/outside classification.

None of this exists. The DOM "cross-section" prototype simply stacks horizontal bands labeled "Kernel", "Governance", "Application", etc. It is a **layer cake**, not a clipping plane through a 3D volume.

### 4.4 Feasibility assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Three.js/R3F installed | 🟢 Present | `frontend/package.json`. |
| R3F components in use | 🔴 None | Only a type declaration file references Three.js. |
| Orbit/fly controls | 🟡 Possible | `OrbitControls` type declared; `@react-three/drei` provides FlyControls/MapControls. |
| 3D scene graph | 🔴 Missing | No meshes, materials, or scene objects. |
| Clipping plane cross-section | 🔴 Missing | Prototype is DOM, not 3D. |
| WebXR | 🔴 Missing | No WebXR dependencies or session code. |
| Desktop-first | 🟢 Aligned | Existing dashboard is desktop web. |

**Verdict:** Layer 3 is **technically straightforward** with the installed libraries but requires building an entire 3D scene, camera controller, and clipping system. VR should be treated as a far-future stretch goal.

---

## 5. Layer 4: Context Preservation (Anti-Disorientation)

### 5.1 Research requirements

- Persistent breadcrumb, mini-map, home button (against "lost in space").
- Procedural detail / grid lines (against "desert fog").
- Animated transitions (1.2–1.5 s) and cross-fades (against reorientation cost).
- Limit simultaneous targets to 4±1 (Miller's Law).
- Fractal grid with threshold transitions.
- Sticky objects with object constancy across scales.

### 5.2 What exists in Egregore

- A generic shadcn-style `ui/breadcrumb.tsx` component exists (`frontend/src/components/ui/breadcrumb.tsx`).
- The prototypes have navigation toolbars and detail panels.
- The dashboard has no mini-map, no fractal grid, no sticky object layer, and no URL-backed view state.

### 5.3 Critical gaps

The most important anti-disorientation mechanisms are absent:
- **Mini-map / overview:** No secondary overview view exists.
- **Fractal grid:** No grid system adapts to zoom level.
- **Sticky objects:** No object persists across scales with changing detail.
- **Animated transitions:** The dashboard uses 500 ms CSS transitions on bars (`MiniBar`) but no cross-scale animation.
- **URL state:** The current app has no router params; a refresh loses view state.

### 5.4 Feasibility assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Breadcrumb | 🟡 Partial | Generic component exists; not wired to spatial path. |
| Mini-map | 🔴 Missing | No overview view. |
| Home button | 🟡 Trivial | Easy to add. |
| Fractal grid | 🔴 Missing | No grid implementation. |
| Sticky objects | 🔴 Missing | No cross-scale object constancy. |
| Animated transitions | 🟡 Partial | 500 ms CSS transitions; no cross-scale animation. |
| 4±1 target limit | 🟡 Possible | Requires design discipline, not code. |

**Verdict:** Layer 4 is **straightforward engineering** but must be designed in from the start. It cannot be bolted onto a completed spatial zoom later without disorientation.

---

## 6. Models, Agents, and Verticals: The Race Metaphor

ANCHORUM is **one vertical among many**. The codebase supports at least six vertical families and twelve registered cells. The user wants to visualize this as a **race/model/agent hierarchy**: models are races, agents are instances drawn from those races, and every entity has advantages, weaknesses, and real skill levels.

### 6.1 What exists

**Verticals and cells.** Cells are defined in `cells/*/spec.yaml` and exposed through the Ombudsman Router (`src/egregore/interface/ombudsman_router.py`):

| Cell ID | Type | Taxonomy | Tier | Max load |
|---|---|---|---|---|
| `anchorum_forensic` | investigation | investigation/forensic/document_analysis | 1 | 0.90 |
| `aegis_hive_actor` | investigation | investigation/defense/response | 3 | 0.70 |
| `aegis_hive_intel` | investigation | investigation/defense/intelligence | 2 | 0.80 |
| `aegis_hive_reasoner` | investigation | investigation/defense/reasoning | 3 | 0.75 |
| `aegis_hive_sensor` | investigation | investigation/defense/telemetry | 2 | 0.85 |
| `law_contract_review` | university | university/social_sciences/law/contract_review | 4 | 0.70 |
| `math_calculus` | university | university/science/mathematics/calculus | 4 | 0.80 |
| `medicine_diagnosis` | university | university/health_sciences/medicine/differential_diagnosis | 5 | 0.60 |
| `sweng_python` | university | university/engineering/software/python | 3 | 0.85 |
| `carpentry_joinery` | guild | guildhall/building/carpentry/joinery | 3 | 0.75 |
| `electrical_wiring` | guild | guildhall/building/electrical/wiring | 4 | 0.65 |
| `self_rep_self_rep` | legal | legal/self_representation/dossier_generation | 3 | 0.60 |

**Model catalog.** The native GGUF catalog lives at `${MODELS_DIR}/gguf/.catalog.json` and is wrapped by `src/egregore/infrastructure/gguf_catalog.py`:

| model_id | tier | params | quantization | capabilities |
|---|---|---|---|---|
| `qwen2.5-1.5b-instruct` | general | 1.5B | Q4_K_M | `chat`, `instruct`, `general` |
| `qwen2.5-7b-instruct` | expert | 7B | Q4_K_M | `chat`, `instruct` |
| `deepseek-coder-6.7b-instruct` | specialized | 6.7B | Q4_K_M | `chat`, `instruct`, `code` |

Vertical-policy bindings are handled by `LocalModelCatalog` (`src/egregore/infrastructure/local_model_catalog.py`), which selects by `vertical → policy_version → speed_tier` and supports SHA-256 pinning. Factory profile aliases map these to cell execution (`config/factory_profiles_v2.yaml`).

**Agents.** `src/egregore/application/agent_registry.py` scans the `agents/` directory. Currently two agents exist:

| Agent | Description | Default model/backend |
|---|---|---|
| `claude-agent` | Calls Anthropic Claude for analysis tasks | `claude-3-5-sonnet-20241022` |
| `example-agent` | Scaffold echo agent | none (echoes context) |

Agents are invoked from chat via `/agents` and `/agent <name> <instruction>` (`src/egregore/application/chat_interpreter.py`).

**Agency species taxonomy.** The closest existing "race" concept is in `src/egregore/domain/agency_taxonomy.py`:

| Species | Biome | Function |
|---|---|---|
| `ACADEMIC` | `RESEARCH` | Theory, models, formal proofs |
| `DEFENSIVE` | `FORTRESS` | Boundary maintenance, threat response |
| `INTELLIGENCE` | `WILDERNESS` | Reconnaissance, surveillance, tracking |
| `PRODUCTIVE` | `FACTORY` | Value generation, work execution |
| `USELESS` | `GARDEN` | Aesthetic, philosophical, experimental |

This taxonomy is **not wired** to the model catalog or agent registry today.

### 6.2 Critical gap: skill levels, advantages, and weaknesses do not exist

The user wants to click on a model or agent and see its **real skill levels, advantages, and weaknesses**. The codebase currently has:

- **Capabilities** as flat strings (`chat`, `instruct`, `code`, `general`) in the GGUF catalog.
- **Verification rules** in cell specs (`json_schema`, `math_verify`, `safety_warnings_nonempty`, etc.) — these are pass/fail quality gates, not numeric skill ratings.
- **Runtime metrics** (load index, latency, token throughput, governance M1–M4 pass/fail) — useful for operational health, not for inherent capability.

There is **no capability matrix**, **no benchmark score**, **no advantage/weakness metadata**, and **no canonical mapping** from species/race → model → agent. The `claude-agent` hard-codes a model; the `example-agent` has none. The agency taxonomy is orphaned from the runtime catalogs.

### 6.3 Pro graph design requirements

To build the race/model/agent graph the user describes, the interface needs:

1. **A unified entity graph** with typed nodes:
   - `Vertical` (e.g., `legal`, `operations`, `investigation`)
   - `Cell` (e.g., `anchorum_forensic`)
   - `Race/Species` (from `agency_taxonomy.py`)
   - `Model` (from GGUF catalog + hosted backends)
   - `Agent` (from `agents/` registry)
   - `WorkUnit` / `Job` / `Service`
2. **Typed edges:**
   - `Cell --uses--> Model`
   - `Cell --advises--> Cell`
   - `Cell --depends_on--> Cell`
   - `Agent --belongs_to--> Race`
   - `Agent --runs_on--> Model`
   - `Vertical --contains--> Cell`
3. **Click-through detail panels** showing:
   - Static metadata (ID, taxonomy, tier, load, status)
   - Capability/skill ratings (to be benchmarked)
   - Advantages and weaknesses (to be authored or derived)
   - Runtime telemetry (load, latency, throughput)
   - Recent jobs/events
4. **A graph layout engine.** The Ombudsman graph endpoint returns simple nodes/edges; a force-directed or hierarchical layout (D3, Cytoscape.js, or R3F) is required. `d3` is not currently a dependency; Cytoscape or R3F force graph would need to be added.

### 6.4 Feasibility assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Cell/vertical taxonomy | 🟢 Strong | `cells/*/spec.yaml`, `src/egregore/cells/models.py`, Ombudsman endpoints. |
| Model catalog | 🟢 Strong | `gguf_catalog.py`, `factory_profiles_v2.yaml`, `local_model_catalog.py`. |
| Agent registry | 🟡 Partial | `agent_registry.py` exists; only two agents, no model link. |
| Race/species taxonomy | 🟡 Partial | `agency_taxonomy.py` exists; not linked to models/agents. |
| Skill levels / advantages / weaknesses | 🔴 Missing | No benchmark or capability matrix exists. |
| Unified entity graph API | 🟡 Partial | Ombudsman `/university/graph` gives cells/edges; must extend to models/agents. |
| Graph layout / click-through UI | 🔴 Missing | No graph viz library; command surface uses mock data. |

**Verdict:** The *data plumbing* for a race/model/agent graph is largely present, but the **semantic content** (skills, advantages, weaknesses) and the **visual graph layer** must be built. This should be treated as a first-class layer of the FPIA, not an afterthought.

---

## 7. Integration mechanics: the proposed state machine vs. reality

The research proposes a hierarchical `ViewportState`:

```
ViewportState
├── ScaleLevel: WORLD | REGION | CITY | BUILDING | ROOM
├── Projection: ORTHOGRAPHIC | MERCATOR | TRANSVERSE_MERCATOR | FLOOR_PLAN
├── ActiveLenses: Set<TEMPORAL | CAUSAL | STRUCTURAL | OPERATIONAL>
├── Selection: { entityId, entityType, coordinates }
├── CrossSection: { enabled, planeNormal, distance }
└── Animation: { phase, progress }
```

### 7.1 What exists

- The dashboard has no global state machine.
- Selection is local to components (e.g., `selectedBlock` in the HTML prototype).
- The only "projection" concept is the layered architecture in `docs/architecture/01_geological_model.md`, which is a code organization principle, not a viewport projection.

### 7.2 What must be added

1. **A single source of truth** for viewport + selection + lenses. Given the React stack, Zustand or Jotai is the natural choice; React Context would work but scales poorly with rapid updates.
2. **A streaming data bus.** The current 3 s poll is incompatible with the operational lens's 1 s debounce and with real-time cross-section updates. NATS/JetStream and WebSocket already exist in the backend; the dashboard needs to consume them.
3. **Entity resolution.** Every selectable object must have a stable ID and a type that maps to backend APIs (cell, node, service, dossier, RFE stream, ANCHORUM artifact).
4. **Animation orchestration.** GSAP is installed but unused. Transitions between scales and lens updates need to be coordinated to avoid the "abrupt update" failure mode Elmqvist warns against.

### 7.3 Integration verdict

The state machine design is sound, but it is **currently vaporware** in this repo. The gap is architectural plumbing, not algorithmic research.

---

## 8. Performance budget reality check

The research proposes:

| Budget Item | Limit | Egregore reality |
|-------------|-------|-------------------|
| Globe path vertices | < 5,000 | No globe exists; no vertex budget baseline. |
| City path vertices | < 10,000 | No city vector data exists. |
| GPU layers | 2–3 | Three.js is installed; layer promotion is possible. |
| Static filters only | Required | Current dashboard uses CSS shadows/blurs; no SVG filter pipeline. |
| 60 fps animation | Required | No R3F/Canvas animation loop exists to measure. |
| Coordination latency < 200 ms | Required | Dashboard polls at 3 s; real-time path must be built. |
| Cross-section 30 fps | Required | No 3D clipping exists. |

**Critical finding:** The performance budget is plausible on paper, but there is **no evidence** the current runtime can meet it. The dashboard runs on mock/fallback data when the backend is unreachable (`useDashboard.ts`), which means any performance testing today would measure CSS transitions on fabricated numbers, not the real system.

---

## 9. Data-source audit

| Source | Size/Shape | Spatial? | Temporal? | Causal/Network? | Usability for FPIA |
|--------|-----------|----------|-----------|-----------------|-------------------|
| `data/rfe.zarc` | 24 JSONL `report_generated` events | No | Yes (`timestamp_ns`, stream timestamps) | Yes (conflicts, decision logs) | Strong for temporal/causal lenses; no spatial mapping. |
| `ANCHORUM_reports/CASE-00000-00_report.json` | 5,283 artifacts, 2,636 entities, 1,044 timeline entries | No | Yes (`master_timeline`) | Partial (entity directory, evidence; correlation graph empty in this report) | Strong for temporal lens; could drive structural lens if correlation graph is populated. |
| `src/egregore/interface/ombudsman_router.py` `/university/graph` | Live cell nodes + advisory/dependency edges | No | No | Yes | Ideal structural lens backend. |
| `src/egregore/infrastructure/telemetry/phase0_pulse_agent.py` | Live gate envelopes | No | Yes (1 s cadence) | No | Ideal operational lens backend. |
| `src/egregore/dt1/models.py` `site` field | String tags like `mtl01`, `yyz01` | No (text only) | No | No | Only spatial *hint* in backend; could anchor a synthetic city layer. |
| `${MODELS_DIR}/gguf/.catalog.json` | 3 GGUF models with capabilities | No | No | No | Strong for model/race nodes; needs skill scores added. |
| `src/egregore/infrastructure/local_model_catalog.py` | Vertical-policy model bindings | No | No | No | Drives model selection by vertical/policy/speed tier. |
| `src/egregore/application/agent_registry.py` | 2 executable agents + manifests | No | No | No | Agent nodes; needs model link and capability metadata. |
| `src/egregore/domain/agency_taxonomy.py` | Species/biome/lobe taxonomy | No | No | No | Closest existing "race" concept; not linked to models/agents. |
| `src/egregore/interface/ombudsman_router.py` `/university/graph` | Cell nodes + advisory/dependency edges | No | No | Yes | Ideal backbone for race graph; must extend to models/agents. |

**Conclusion:** Temporal, causal, structural, and operational data are abundant. Geospatial data are absent. Model, agent, and vertical metadata exist but are fragmented. The FPIA must therefore be a **spatial metaphor** and requires a new **unified entity graph** that joins cells, models, agents, and species/race.

---

## 10. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **No geospatial data** makes planet→room navigation a fiction. | High | High | Adopt metaphorical mapping; explicitly redefine scale layers. |
| **3 s polling** cannot support operational lens fluidity. | High | High | Replace with WebSocket/SSE consuming NATS/telemetry. |
| **Local `useState`** prevents cross-view coordination. | High | High | Introduce shared reactive store early. |
| **Unused Three.js dependency** may be outdated or misconfigured by the time work starts. | Medium | Medium | Pin versions; add integration tests; validate build. |
| **Architecture-policy tests** (`tests/test_arch_enforcement.py`) may restrict where new UI/backend code can live. | Medium | Medium | Place new domain models in `domain/`, adapters in `infrastructure/`, UI in `frontend/src/`. |
| **VR/WebXR** is a research-grade add-on with no user demand. | Medium | Low | Defer VR; build desktop-first as research recommends. |
| **5+ scale nested zoom** has no empirical HCI validation. | Medium | Medium | Run usability tests; consider collapsing to 3 scales initially. |
| **Real-time data mid-animation** (open research gap #4) could corrupt transitions. | Medium | High | Snapshot selection state during animation; queue updates. |
| **No skill/advantage/weakness metadata** for models/agents. | High | High | Define `ModelProfile`/`AgentProfile`; run cell-specific benchmarks. |
| **Agency taxonomy is orphaned** from model/agent catalogs. | Medium | Medium | Explicitly map species → model families and agent instances. |
| **Graph visualization library not chosen.** | Medium | Medium | Evaluate Cytoscape.js vs. D3 vs. R3F force graph against performance budget. |

---

## 11. Recommendations

### 11.1 Do not build a geographic globe

The proposed TopoJSON/Natural Earth pipeline is overkill for Egregore. The system has no continents to show. Instead, redefine the spatial layers as a **computational topology**:

| FPIA layer | Egregore mapping |
|------------|-------------------|
| Planet | Entire Egregore federation / all clusters |
| Continent | Vertical family (`legal`, `operations`, `dt1`, `investigation`, `university`, `guild`) |
| City | Cell / taxonomy branch (e.g., `anchorum_forensic`, `law_contract_review`) |
| Building | Model / race (e.g., `qwen2.5-7b-instruct`, `claude-3-5-sonnet`) |
| Room | Agent / work-unit / service (e.g., `claude-agent`, `aegis_hive_sensor`) |

This preserves the Russian-doll interaction without inventing fake geography.

### 11.2 Start with Layer 2 (Analytical Lenses)

It has the highest data readiness and the lowest risk. A minimal viable product:
1. Add a shared state store.
2. Build four lens panels: Timeline, Causal DAG, Network Graph, Operational Dashboard.
3. Wire them to existing endpoints: ANCHORUM timeline, RFE decision logs, ombudsman graph, telemetry pulses.
4. Implement brushing/linking on `entityId` and `timeRange`.

### 11.3 Defer Layer 3 and VR

Build the spatial zoom in 2D/SVG first (using the metaphorical map), then promote to Three.js only if performance demands it. WebXR should be a future experiment, not a Phase 1 deliverable.

### 11.4 Replace polling with streaming

The operational lens and cross-section updates require sub-second data. Use the existing WebSocket/NATS infrastructure to push gate envelopes and selection events to the frontend.

### 11.5 Add context preservation from day one

Do not build the spatial view and then add breadcrumbs. The state machine must carry `ScaleLevel`, `Selection`, and `ActiveLenses`, and the URL should serialize them so refresh and deep-linking work.

### 11.6 Build the race/model/agent graph as a first-class layer

This is the user's central ask and should be built in parallel with the analytical lenses. Concrete steps:
1. Extend the Ombudsman graph endpoint (or create `/api/v1/universe/graph`) to emit nodes for verticals, cells, races/species, models, and agents, plus typed edges (`uses`, `belongs_to`, `advises`, `depends_on`).
2. Add a `ModelProfile` / `AgentProfile` domain model with fields for `skills`, `advantages`, `weaknesses`, `capabilities`, `benchmark_scores`, and `race_id`.
3. Populate initial profiles by benchmarking existing models against cell verification rules (e.g., code tasks for `sweng_python`, medical reasoning for `medicine_diagnosis`, legal reasoning for `law_contract_review`).
4. Render the graph with a professional graph library (Cytoscape.js for 2D or `@react-three/drei` force graph for 3D). Clicking a node opens a detail panel with the profile and real-time telemetry.
5. Link the race graph to the spatial zoom: continent = vertical, city = cell, building = model/race, room = agent.

### 11.7 Suggested phased roadmap (realistic)

| Phase | Weeks | Deliverable | Key work |
|-------|-------|-------------|----------|
| 0 | 1–2 | Architecture decision | Choose metaphorical mapping; define entity IDs; choose state library; design ModelProfile/AgentProfile schema. |
| 1 | 3–5 | Shared state + analytical lenses | Zustand/Jotai store; Timeline, Network, Causal, Operational lenses; brushing/linking. |
| 2 | 4–6 | Race/model/agent graph | Extend Ombudsman graph; add ModelProfile/AgentProfile; benchmark models; build Cytoscape/R3F graph with click-through panels. |
| 3 | 3–4 | 2D spatial metaphor | Zoomable topology map (federation → vertical → cell → model → agent); no 3D yet. |
| 4 | 2–3 | Context preservation | Breadcrumb, mini-map, sticky objects, URL state, animated transitions. |
| 5 | 4–6 | 3D promotion | Port 2D map to R3F; add cross-section clipping plane; keep desktop-only. |
| 6 | Future | VR evaluation | WebXR prototype only if desktop mode proves valuable. |

---

## 12. Final conclusion

The Full-Perspective Interface Architecture is **possible in Egregore, but only as a spatial metaphor**, not as the geographic planet-to-room system described in the research document. The backend lacks geospatial data, the frontend has the right libraries but no production 3D code, and the dashboard's local-state/polling architecture is incompatible with the real-time coordination the design demands.

ANCHORUM is only one of many verticals. The user's **race/model/agent graph** is the most distinctive and achievable part of the vision: the data plumbing (cells, models, agents, species taxonomy) already exists, but the **skill ratings, advantages, weaknesses, and unified graph API must be created from scratch**. That work should be treated as a first-class layer of the FPIA, not a visualization afterthought.

The strongest path forward is:
1. **Layer 2 first** — analytical lenses are data-ready and high-value.
2. **Race/model/agent graph in parallel** — extend the Ombudsman graph, benchmark models, and build click-through profile panels.
3. **Metaphorical spatial zoom** — map federation → vertical → cell → model/race → agent.
4. **Shared reactive state + streaming** — replace polling before attempting 3D.
5. **3D and VR last** — only after the 2D coordinated system is proven.

The cited HCI research (Furnas, Baldonado, North & Shneiderman, McGuffin, Elmqvist, CosmoScout, Space-Time Hypercube) provides valid design principles, but **none of it removes the need for a data model that matches the spatial metaphor**. Egregore now has a plausible answer to what its "planet" and "room" are: federation and agent. The remaining question is whether the project will invest in the benchmark and graph layers required to make the race metaphor meaningful.
