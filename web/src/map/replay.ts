import type { FrontierEntry, Node, Phase, Scored, TraceEvent } from "../api/types";

export const keyOf = (node: Node): string => `${node[0]}|${node[1]}`;
export const stationOf = (key: string): string => key.split("|")[0];

export interface GeneratedEdge {
  parent: Node;
  node: Node;
  cost: number;
  edge: "rail" | "transfer";
  action: string;
  g?: number;
  h?: number;
  f?: number;
}

/** Everything the UI needs to draw one moment of a search. */
export interface VisualState {
  step: number;
  expandedOrder: string[];
  expanded: Set<string>;
  frontier: FrontierEntry[];
  frontierKeys: Set<string>;
  frontierSize: number;
  current: Node | null;
  currentG: number | null;
  currentH: number | null;
  currentF: number | null;
  depth: number | null;
  lastGenerated: GeneratedEdge[];
  tree: Map<string, string>;
  beamKept: Scored[];
  beamDropped: Scored[];
  prunedKeys: Set<string>;
  phaseLabel: string | null;
  phaseIndex: number | null;
  threshold: number | null;
  round: number | null;
  backtrackFrom: Node | null;
  stuckAt: Node | null;
  stuckReason: string | null;
  finalPath: Node[] | null;
}

function emptyState(): VisualState {
  return {
    step: -1,
    expandedOrder: [],
    expanded: new Set(),
    frontier: [],
    frontierKeys: new Set(),
    frontierSize: 0,
    current: null,
    currentG: null,
    currentH: null,
    currentF: null,
    depth: null,
    lastGenerated: [],
    tree: new Map(),
    beamKept: [],
    beamDropped: [],
    prunedKeys: new Set(),
    phaseLabel: null,
    phaseIndex: null,
    threshold: null,
    round: null,
    backtrackFrom: null,
    stuckAt: null,
    stuckReason: null,
    finalPath: null,
  };
}

function clone(state: VisualState): VisualState {
  return {
    ...state,
    expandedOrder: [...state.expandedOrder],
    expanded: new Set(state.expanded),
    frontier: [...state.frontier],
    frontierKeys: new Set(state.frontierKeys),
    lastGenerated: [...state.lastGenerated],
    tree: new Map(state.tree),
    beamKept: [...state.beamKept],
    beamDropped: [...state.beamDropped],
    prunedKeys: new Set(state.prunedKeys),
  };
}

function apply(state: VisualState, event: TraceEvent): void {
  switch (event.kind) {
    case "seed": {
      state.frontier = event.nodes.map((entry) => ({
        node: entry.node,
        priority: entry.h,
        stale: false,
      }));
      state.frontierKeys = new Set(state.frontier.map((entry) => keyOf(entry.node)));
      state.frontierSize = state.frontier.length;
      break;
    }
    case "expand": {
      const key = keyOf(event.node);
      if (!state.expanded.has(key)) {
        state.expanded.add(key);
        state.expandedOrder.push(key);
      }
      state.current = event.node;
      state.currentG = event.g;
      state.currentH = event.h;
      state.currentF = event.f;
      state.depth = event.depth ?? null;
      state.backtrackFrom = null;
      // A fresh expansion starts a fresh neighbour table.
      state.lastGenerated = [];
      if (event.frontier) {
        state.frontier = event.frontier;
        state.frontierKeys = new Set(event.frontier.map((entry) => keyOf(entry.node)));
        state.frontierSize = event.frontier_size ?? event.frontier.length;
      }
      break;
    }
    case "generate": {
      state.lastGenerated.push(event);
      if (event.action === "improved") {
        state.tree.set(keyOf(event.node), keyOf(event.parent));
      }
      break;
    }
    case "phase": {
      state.phaseLabel = event.label;
      state.phaseIndex = event.index;
      if (event.threshold !== undefined) state.threshold = event.threshold;
      if (event.round !== undefined) state.round = event.round;
      break;
    }
    case "prune": {
      state.beamKept = event.kept;
      state.beamDropped = event.dropped;
      state.prunedKeys = new Set(event.dropped.map((entry) => keyOf(entry.node)));
      break;
    }
    case "backtrack": {
      state.backtrackFrom = event.node;
      break;
    }
    case "stuck": {
      state.stuckAt = event.node;
      state.stuckReason = event.reason;
      break;
    }
    case "solution": {
      state.finalPath = event.path;
      break;
    }
  }
}

const KEYFRAME_EVERY = 250;

/**
 * Random access into a trace.
 *
 * Stepping forward is incremental, but scrubbing backwards would otherwise
 * mean replaying from event zero every time. Snapshots every few hundred
 * events keep any seek bounded to that many applications.
 */
export class Replay {
  private keyframes: VisualState[] = [];

  constructor(readonly events: TraceEvent[], readonly phases: Phase[]) {
    let state = emptyState();
    this.keyframes.push(clone(state));
    events.forEach((event, index) => {
      apply(state, event);
      state.step = index;
      if ((index + 1) % KEYFRAME_EVERY === 0) this.keyframes.push(clone(state));
    });
  }

  get length(): number {
    return this.events.length;
  }

  /** Visual state after applying events[0..step] inclusive. step = -1 is the start. */
  at(step: number): VisualState {
    const clamped = Math.max(-1, Math.min(step, this.events.length - 1));
    const keyframeIndex = Math.max(0, Math.floor((clamped + 1) / KEYFRAME_EVERY));
    const state = clone(this.keyframes[Math.min(keyframeIndex, this.keyframes.length - 1)]);
    const from = state.step + 1;
    for (let index = from; index <= clamped; index += 1) apply(state, this.events[index]);
    state.step = clamped;
    return state;
  }

  /** Indices worth marking on the timeline: phase changes and terminal events. */
  markers(): { index: number; kind: string; label: string }[] {
    const marks: { index: number; kind: string; label: string }[] = [];
    this.events.forEach((event, index) => {
      if (event.kind === "phase") {
        const label =
          event.threshold !== undefined
            ? `f ≤ ${(event.threshold / 60).toFixed(1)} min`
            : `round ${event.round ?? event.index}`;
        marks.push({ index, kind: "phase", label });
      } else if (event.kind === "stuck") {
        marks.push({ index, kind: "stuck", label: event.reason });
      } else if (event.kind === "solution") {
        marks.push({ index, kind: "solution", label: "goal reached" });
      }
    });
    return marks;
  }
}
