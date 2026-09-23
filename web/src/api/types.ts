export type Node = [string, string]; // [station_id, line_id]

export type Scenario = "operating" | "under_construction" | "planned";
export type Algorithm = "greedy" | "hill_climbing" | "beam" | "astar" | "ida_star";

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
  interchange: boolean;
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

export interface HeuristicPayload {
  goal: string;
  scenario: Scenario;
  max_h: number;
  h: Record<string, number>;
}
