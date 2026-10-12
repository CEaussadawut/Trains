export type Node = [string, string]; // [station_id, line_id]

export type Scenario = "operating" | "under_construction" | "planned";
export type Algorithm =
  | "bfs" | "dfs" | "ucs" | "dls" | "ids" | "bidirectional"
  | "greedy" | "hill_climbing" | "beam"
  | "astar" | "astar_landmark" | "ida_star";

export interface Station {
  id: string;
  name_en: string;
  name_th: string;
  lat: number;
  lon: number;
  map_x: number;
  map_y: number;
  label_side: "left" | "right";
  lines: string[];
  active_lines: string[];
  active: boolean;
  /** Serves two lines, or is joined to another station by a transfer walk. */
  interchange: boolean;
  /** First or last station on some line. */
  terminal: boolean;
}

export interface Line {
  id: string;
  name_en: string;
  name_th: string;
  color: string;
  color_hex: string;
  dash: string;
  operator: string;
  status: Scenario;
  mode: string;
  active: boolean;
  /** Station ids in running order along the line, not alphabetical. */
  stations: string[];
}

export interface Edge {
  from: string;
  to: string;
  from_line: string;
  to_line: string;
  line?: string;
  seconds: number;
  active: boolean;
}

export interface Network {
  scenario: Scenario;
  stations: Station[];
  lines: Line[];
  edges: Edge[];
  transfers: Edge[];
  stats: {
    stations: number;
    active_stations: number;
    nodes: number;
    rail_edges: number;
    transfer_edges: number;
  };
  /** How much of the active scenario is estimated rather than observed. */
  quality: {
    edges: { total: number; estimated: number; unverified: number };
    stations: { total: number; interpolated: number };
    transfers: { total: number; estimated: number; unverified: number };
  };
}

export interface Weights {
  time: number;
  cost: number;
  transit: number;
}

export type Leg =
  | {
      kind: "ride";
      line: string;
      line_name: string;
      color_hex: string;
      operator: string;
      from: string;
      to: string;
      from_name: string;
      to_name: string;
      stations: string[];
      stops: number;
      seconds: number;
    }
  | {
      kind: "transfer";
      from: string;
      to: string;
      from_name: string;
      to_name: string;
      from_line: string;
      to_line: string;
      same_station: boolean;
      seconds: number;
    };

export interface RoutePayload {
  path: Node[];
  stations: { id: string; line: string; name_en: string; name_th: string }[];
  duration_sec: number;
  fare_thb: number;
  hops: number;
  transfers: number;
  nodes_expanded: number;
  nodes_generated: number;
  runtime_sec: number;
  peak_memory_bytes: number;
  itinerary: Leg[];
}

export interface FrontierEntry {
  node: Node;
  priority: number;
  stale: boolean;
}

export interface Scored {
  node: Node;
  f: number;
}

export type TraceEvent =
  | { kind: "seed"; nodes: { node: Node; h: number }[] }
  | {
      kind: "expand";
      node: Node;
      g: number;
      h: number;
      f: number;
      depth?: number;
      frontier?: FrontierEntry[];
      frontier_size?: number;
    }
  | {
      kind: "generate";
      parent: Node;
      node: Node;
      cost: number;
      edge: "rail" | "transfer";
      action: "improved" | "candidate" | "skip_visited" | "skip_worse" | "skip_seen" | "skip_on_path";
      g?: number;
      h?: number;
      f?: number;
    }
  | { kind: "phase"; label: string; index: number; round?: number; threshold?: number; beam_size?: number }
  | { kind: "prune"; kept: Scored[]; dropped: Scored[]; dropped_total: number }
  | { kind: "backtrack"; node: Node; reason: string; f: number; limit: number }
  | { kind: "stuck"; node: Node; reason: string; h: number; h_best?: number }
  | { kind: "solution"; path: Node[]; duration_sec: number };

export interface Phase {
  label: string;
  index: number;
  expanded: number;
  generated: number;
  round?: number;
  threshold?: number;
  next_threshold?: number;
  solved?: boolean;
  exhausted?: boolean;
  events_dropped?: number;
}

export interface TracePayload {
  events: TraceEvent[];
  phases: Phase[];
  truncated: boolean;
  dropped: number;
  detail: string;
  keep_phases: string;
  max_events: number;
  counts: { expanded: number; generated: number; seeded: number };
  scenario: Scenario;
  algorithm: Algorithm;
  start: string;
  goal: string;
  solved: boolean;
  error: string;
  route: RoutePayload | null;
  optimal_sec: number | null;
  /** The weights g/h/f were computed under. Absent from servers older than ALT. */
  weights?: Weights;
}

export interface CompareRow {
  algorithm: Algorithm;
  label: string;
  family: string;
  solved: boolean;
  error: string;
  median_ms: number;
  optimal?: boolean;
  excess_pct?: number;
  route?: RoutePayload;
}

export interface ComparePayload {
  scenario: Scenario;
  start: string;
  goal: string;
  repeats: number;
  optimal_sec: number | null;
  results: CompareRow[];
}

export interface Meta {
  scenarios: Scenario[];
  algorithms: { id: Algorithm; label: string; family: string; params: string[] }[];
  defaults: {
    scenario: Scenario;
    algorithm: Algorithm;
    weights: Weights;
    beam_width: number;
    max_events: number;
  };
}

/** How one node's h was obtained under A* + ALT. With `source: "landmark"`,
 *  h = first − second:
 *    beyond_goal — the landmark lies past the goal: d(n→L) − d(goal→L)
 *    behind_you  — the landmark lies behind n:      d(L→goal) − d(L→n) */
export interface HeuristicExplain {
  h: number;
  straight_line: number;
  source: "landmark" | "straight_line" | "goal";
  landmark?: Node;
  via?: "beyond_goal" | "behind_you";
  first?: number;
  second?: number;
}

export interface HeuristicPayload {
  goal: string;
  scenario: Scenario;
  max_h: number;
  /** Per station; for ALT the smallest h over the station's platforms. */
  h: Record<string, number>;
  /** Optional so an older server (straight-line only) still type-checks. */
  kind?: "straight_line" | "landmark";
  algorithm?: Algorithm;
  landmarks?: Node[];
  /** Keyed "STATION|LINE". */
  explain?: Record<string, HeuristicExplain>;
}
