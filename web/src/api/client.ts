import type {
  ComparePayload, HeuristicPayload, Meta, Network, Scenario, TracePayload, Weights,
} from "./types";

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new ApiError(response.status, "bad_response", text.slice(0, 200));
  }
  if (!response.ok) {
    const failure = body as { code?: string; message?: string };
    throw new ApiError(response.status, failure.code ?? "error", failure.message ?? response.statusText);
  }
  return body as T;
}

export interface SearchRequest {
  scenario: Scenario;
  algorithm: string;
  start: string;
  goal: string;
  weights: Weights;
  beam_width: number;
  depth_limit: number;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),

  network: (scenario: Scenario) => request<Network>(`/api/network?scenario=${scenario}`),

  /** h(n) as `algorithm` computes it under `weights` -- the landmark bound for
   *  A* + ALT, the straight-line bound otherwise. */
  heuristic: (scenario: Scenario, goal: string, algorithm: string, weights: Weights) =>
    request<HeuristicPayload>(
      `/api/heuristic?scenario=${scenario}&goal=${encodeURIComponent(goal)}` +
      `&algorithm=${encodeURIComponent(algorithm)}` +
      `&w_time=${weights.time}&w_cost=${weights.cost}&w_transit=${weights.transit}`,
    ),

  trace: (body: SearchRequest & { trace?: { max_events?: number; detail?: string } }) =>
    request<TracePayload>("/api/trace", { method: "POST", body: JSON.stringify(body) }),

  compare: (body: Omit<SearchRequest, "algorithm"> & { repeats?: number }) =>
    request<ComparePayload>("/api/compare", { method: "POST", body: JSON.stringify(body) }),
};
