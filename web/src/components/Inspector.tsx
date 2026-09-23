import { useMemo } from "react";
import { useStore } from "../state/store";
import type { VisualState } from "../map/replay";

const mins = (seconds: number | null | undefined): string =>
  seconds === null || seconds === undefined ? "—" : `${(seconds / 60).toFixed(1)}`;

const ACTION_LABEL: Record<string, string> = {
  improved: "queued",
  candidate: "candidate",
  skip_visited: "already expanded",
  skip_worse: "worse than known",
  skip_seen: "already seen",
  skip_on_path: "already on path",
};

export function FrontierPanel({ visual }: { visual: VisualState | null }) {
  const stations = useStore((s) => s.stationsById);
  if (!visual) return <p className="empty">Run a search to see the queue.</p>;

  if (visual.frontier.length === 0 && visual.beamKept.length === 0) {
    return <p className="empty">This algorithm keeps no open list — it commits as it goes.</p>;
  }

  const rows = visual.beamKept.length
    ? visual.beamKept.map((entry) => ({ node: entry.node, priority: entry.f, stale: false }))
    : visual.frontier;

  return (
    <div className="panel-body">
      <p className="panel-note">
        {visual.beamKept.length ? "Beam (kept this round)" : "Open list, best first"}
        {visual.frontierSize > rows.length && <> · showing {rows.length} of {visual.frontierSize}</>}
      </p>
      <table className="grid">
        <thead>
          <tr><th>#</th><th>Station</th><th>Line</th><th className="num">priority</th></tr>
        </thead>
        <tbody>
          {rows.map((entry, index) => (
            <tr key={`${entry.node[0]}-${entry.node[1]}-${index}`} className={entry.stale ? "stale" : ""}>
              <td className="num dim">{index + 1}</td>
              <td>{stations.get(entry.node[0])?.name_en ?? entry.node[0]}</td>
              <td className="dim">{entry.node[1]}</td>
              <td className="num">{mins(entry.priority)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.some((entry) => entry.stale) && (
        <p className="panel-note">
          Greyed rows are superseded entries still sitting in the heap — they get
          discarded when popped. That is lazy deletion.
        </p>
      )}
      {visual.beamDropped.length > 0 && (
        <p className="panel-note warn-text">
          Pruned this round: {visual.beamDropped.length} of {visual.beamDropped.length + rows.length}
          {" "}candidates discarded and never revisited.
        </p>
      )}
    </div>
  );
}

export function NodeInspector({ visual }: { visual: VisualState | null }) {
  const stations = useStore((s) => s.stationsById);
  const trace = useStore((s) => s.trace);
  const heuristic = useStore((s) => s.heuristic);

  const informedness = useMemo(() => {
    if (!trace?.optimal_sec || !heuristic) return null;
    const h = heuristic.h[trace.start];
    return h === undefined ? null : h / trace.optimal_sec;
  }, [trace, heuristic]);

  if (!visual || !visual.current) return <p className="empty">Run a search to inspect a node.</p>;

  const [stationId, lineId] = visual.current;
  return (
    <div className="panel-body">
      <h4 className="node-name">{stations.get(stationId)?.name_en ?? stationId}</h4>
      <p className="panel-note">on {lineId}{visual.depth !== null && <> · depth {visual.depth}</>}</p>

      <dl className="ghf">
        <div><dt>g</dt><dd>{mins(visual.currentG)}<small>min so far</small></dd></div>
        <div><dt>h</dt><dd>{mins(visual.currentH)}<small>min estimated</small></dd></div>
        <div><dt>f</dt><dd>{mins(visual.currentF)}<small>min total</small></dd></div>
      </dl>

      {visual.threshold !== null && (
        <p className="panel-note">
          IDA* threshold: expand only while f ≤ <b>{mins(visual.threshold)} min</b>
          {visual.round !== null && <> · pass {visual.round + 1}</>}
        </p>
      )}
      {visual.backtrackFrom && (
        <p className="panel-note warn-text">
          Backtracked from {stations.get(visual.backtrackFrom[0])?.name_en} — f exceeded the threshold.
        </p>
      )}
      {visual.stuckAt && (
        <p className="panel-note warn-text">
          Stuck: {visual.stuckReason}. Every unvisited neighbour looks further from the
          goal than here, and this algorithm cannot go downhill to escape.
        </p>
      )}

      {informedness !== null && (
        <p className="panel-note">
          Heuristic informedness h/h* = <b>{informedness.toFixed(2)}</b> — straight-line
          distance at 45 km/h underestimates the real journey, which is why A* still
          has to explore widely.
        </p>
      )}

      {visual.lastGenerated.length > 0 && (
        <>
          <h5 className="panel-sub">Neighbours considered</h5>
          <table className="grid">
            <thead><tr><th>Station</th><th>Edge</th><th className="num">h</th><th>Outcome</th></tr></thead>
            <tbody>
              {visual.lastGenerated.map((entry, index) => (
                <tr key={index} className={entry.action.startsWith("skip") ? "stale" : ""}>
                  <td>{stations.get(entry.node[0])?.name_en ?? entry.node[0]}</td>
                  <td className="dim">{entry.edge}</td>
                  <td className="num">{entry.h !== undefined ? mins(entry.h) : "—"}</td>
                  <td className="dim">{ACTION_LABEL[entry.action] ?? entry.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export function RouteSummary() {
  const trace = useStore((s) => s.trace);
  const stations = useStore((s) => s.stationsById);
  if (!trace) return <p className="empty">Run a search to see the journey.</p>;

  if (!trace.solved || !trace.route) {
    return (
      <div className="panel-body">
        <div className="verdict verdict-fail">No route returned</div>
        <p className="panel-note">{trace.error}</p>
        {trace.optimal_sec !== null && (
          <p className="panel-note">
            A route does exist — Dijkstra finds one in {mins(trace.optimal_sec)} min. This
            algorithm is incomplete: it cannot guarantee finding a path even when one exists.
          </p>
        )}
      </div>
    );
  }

  const route = trace.route;
  const optimal = trace.optimal_sec;
  const excess = optimal ? (route.duration_sec / optimal - 1) * 100 : 0;

  return (
    <div className="panel-body">
      <div className={`verdict ${excess < 0.001 ? "verdict-ok" : "verdict-warn"}`}>
        {excess < 0.001 ? "Optimal" : `${excess.toFixed(1)}% slower than optimal`}
      </div>

      <div className="totals">
        <div><b>{mins(route.duration_sec)}</b><span>minutes</span></div>
        <div><b>{route.fare_thb.toFixed(0)}</b><span>baht</span></div>
        <div><b>{route.transfers}</b><span>transfers</span></div>
        <div><b>{route.hops}</b><span>hops</span></div>
      </div>

      <ol className="legs">
        {route.itinerary.map((leg, index) =>
          leg.kind === "ride" ? (
            <li key={index} className="leg leg-ride">
              <span className="swatch" style={{ background: leg.color_hex }} />
              <div>
                <b>{leg.line_name}</b>
                <p>{leg.from_name} → {leg.to_name}</p>
                <small>{leg.stops} stops · {mins(leg.seconds)} min</small>
              </div>
            </li>
          ) : (
            <li key={index} className="leg leg-transfer">
              <span className="swatch swatch-walk" />
              <div>
                <b>{leg.same_station ? "Change line" : "Walk to connect"}</b>
                <p>
                  {leg.from_name} [{leg.from_line}] → {stations.get(leg.to)?.name_en ?? leg.to} [{leg.to_line}]
                </p>
                <small>{mins(leg.seconds)} min</small>
              </div>
            </li>
          ),
        )}
      </ol>

      <div className="cost-row">
        <span>{route.nodes_expanded} expanded</span>
        <span>{route.nodes_generated} generated</span>
        <span>{(route.runtime_sec * 1000).toFixed(2)} ms</span>
      </div>
    </div>
  );
}
