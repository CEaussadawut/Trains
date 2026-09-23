import { create } from "zustand";
import { api, ApiError } from "../api/client";
import type {
  Algorithm, ComparePayload, HeuristicPayload, Line, Meta, Network, Scenario, Station,
  TracePayload, Weights,
} from "../api/types";
import { buildLayouts, type Layouts } from "../map/layouts";
import { Replay, type VisualState } from "../map/replay";

export type Mode = "animate" | "compare";

interface State {
  meta: Meta | null;
  network: Network | null;
  layouts: Layouts | null;
  stationsById: Map<string, Station>;
  linesById: Map<string, Line>;

  scenario: Scenario;
  algorithm: Algorithm;
  start: string | null;
  goal: string | null;
  weights: Weights;
  beamWidth: number;
  maxEvents: number;

  mode: Mode;
  loading: boolean;
  running: boolean;
  error: string | null;

  trace: TracePayload | null;
  replay: Replay | null;
  step: number;
  playing: boolean;
  speed: number;

  compare: ComparePayload | null;
  heuristic: HeuristicPayload | null;
  showHeuristic: boolean;

  layoutT: number;
  hovered: string | null;

  bootstrap: () => Promise<void>;
  setScenario: (scenario: Scenario) => Promise<void>;
  setAlgorithm: (algorithm: Algorithm) => void;
  setWeights: (weights: Partial<Weights>) => void;
  setBeamWidth: (width: number) => void;
  pickStation: (id: string) => void;
  setEndpoints: (start: string | null, goal: string | null) => void;
  setMode: (mode: Mode) => void;
  run: () => Promise<void>;
  runCompare: () => Promise<void>;
  setStep: (step: number) => void;
  stepBy: (delta: number) => void;
  setPlaying: (playing: boolean) => void;
  setSpeed: (speed: number) => void;
  setLayoutT: (t: number) => void;
  toggleHeuristic: () => void;
  setHovered: (id: string | null) => void;
  visual: () => VisualState | null;
}

const describe = (error: unknown): string =>
  error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);

export const useStore = create<State>((set, get) => ({
  meta: null,
  network: null,
  layouts: null,
  stationsById: new Map(),
  linesById: new Map(),

  scenario: "operating",
  algorithm: "astar",
  start: "N24",
  goal: "BL01",
  weights: { time: 1, cost: 0, transit: 0 },
  beamWidth: 5,
  maxEvents: 20000,

  mode: "animate",
  loading: true,
  running: false,
  error: null,

  trace: null,
  replay: null,
  step: -1,
  playing: false,
  speed: 1,

  compare: null,
  heuristic: null,
  showHeuristic: false,

  layoutT: 0,
  hovered: null,

  async bootstrap() {
    set({ loading: true, error: null });
    try {
      const [meta, network] = await Promise.all([api.meta(), api.network(get().scenario)]);
      set({
        meta,
        network,
        layouts: buildLayouts(network.stations),
        stationsById: new Map(network.stations.map((s) => [s.id, s])),
        linesById: new Map(network.lines.map((l) => [l.id, l])),
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: describe(error) });
    }
  },

  async setScenario(scenario) {
    set({ scenario, loading: true, error: null, trace: null, replay: null, step: -1, compare: null });
    try {
      const network = await api.network(scenario);
      const stationsById = new Map(network.stations.map((s) => [s.id, s]));
      // An endpoint may not exist in a smaller scenario; drop it rather than
      // sending the server a request it must reject.
      const stillActive = (id: string | null) =>
        id && stationsById.get(id)?.active ? id : null;
      set({
        network,
        stationsById,
        linesById: new Map(network.lines.map((l) => [l.id, l])),
        layouts: buildLayouts(network.stations),
        start: stillActive(get().start),
        goal: stillActive(get().goal),
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: describe(error) });
    }
  },

  setAlgorithm: (algorithm) => set({ algorithm }),
  setWeights: (weights) => set({ weights: { ...get().weights, ...weights } }),
  setBeamWidth: (beamWidth) => set({ beamWidth: Math.max(1, Math.round(beamWidth)) }),

  pickStation(id) {
    const { start, goal, stationsById } = get();
    if (!stationsById.get(id)?.active) return;
    if (!start || (start && goal)) set({ start: id, goal: null, trace: null, replay: null, step: -1 });
    else if (id !== start) set({ goal: id });
  },

  setEndpoints: (start, goal) => set({ start, goal }),
  setMode: (mode) => set({ mode }),

  async run() {
    const { scenario, algorithm, start, goal, weights, beamWidth, maxEvents } = get();
    if (!start || !goal) return;
    set({ running: true, error: null, playing: false });
    try {
      const [trace, heuristic] = await Promise.all([
        api.trace({
          scenario, algorithm, start, goal, weights,
          beam_width: beamWidth, trace: { max_events: maxEvents },
        }),
        api.heuristic(scenario, goal),
      ]);
      set({
        trace,
        heuristic,
        replay: new Replay(trace.events, trace.phases),
        step: -1,
        running: false,
        playing: true,
      });
    } catch (error) {
      set({ running: false, error: describe(error), trace: null, replay: null });
    }
  },

  async runCompare() {
    const { scenario, start, goal, weights, beamWidth } = get();
    if (!start || !goal) return;
    set({ running: true, error: null });
    try {
      const compare = await api.compare({
        scenario, start, goal, weights, beam_width: beamWidth, repeats: 5,
      });
      set({ compare, running: false });
    } catch (error) {
      set({ running: false, error: describe(error) });
    }
  },

  setStep(step) {
    const replay = get().replay;
    if (!replay) return;
    set({ step: Math.max(-1, Math.min(step, replay.length - 1)) });
  },

  stepBy(delta) {
    get().setStep(get().step + delta);
    set({ playing: false });
  },

  setPlaying(playing) {
    const { replay, step } = get();
    if (playing && replay && step >= replay.length - 1) set({ step: -1 });
    set({ playing });
  },

  setSpeed: (speed) => set({ speed }),
  setLayoutT: (layoutT) => set({ layoutT }),
  toggleHeuristic: () => set({ showHeuristic: !get().showHeuristic }),
  setHovered: (hovered) => set({ hovered }),

  visual() {
    const { replay, step } = get();
    return replay ? replay.at(step) : null;
  },
}));
