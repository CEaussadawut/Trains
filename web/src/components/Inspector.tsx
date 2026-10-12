import { useEffect, useMemo } from "react";
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

// How long each landmark stays on screen while the calculation plays.
const CALC_STEP_MS = 1800;

/** A* + ALT: computes h(node) one landmark at a time. Each step draws two real
 *  routes on the map (orange A, blue B) and subtracts their lengths; the
 *  running maximum is h. The two colours match the map. */
function LandmarkCalc({ node }: { node: [string, string] }) {
  const stations = useStore((s) => s.stationsById);
  const calc = useStore((s) => s.calc);
  const step = useStore((s) => s.calcStep);
  const playing = useStore((s) => s.calcPlaying);
  const loading = useStore((s) => s.calcLoading);
  const startCalc = useStore((s) => s.startCalc);
  const setCalcStep = useStore((s) => s.setCalcStep);
  const setCalcPlaying = useStore((s) => s.setCalcPlaying);
  const stopCalc = useStore((s) => s.stopCalc);
  const nameOf = (id: string) => stations.get(id)?.name_en ?? id;

  const active = !!calc && calc.node[0] === node[0] && calc.node[1] === node[1];

  useEffect(() => {
    if (!active || !playing || !calc) return;
    if (step >= calc.steps.length) {
      setCalcPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setCalcStep(step + 1), CALC_STEP_MS);
    return () => window.clearTimeout(timer);
  }, [active, playing, step, calc, setCalcStep, setCalcPlaying]);

  if (!active || !calc) {
    return (
      <button className="calc-start" onClick={() => startCalc(node)} disabled={loading}>
        {loading ? "Loading…" : "▶ Animate how h is calculated"}
      </button>
    );
  }

  const done = step >= calc.steps.length;
  const shown = calc.steps.slice(0, done ? calc.steps.length : step + 1);
  let bestSoFar = -1;
  shown.forEach((entry, index) => {
    if (bestSoFar < 0 || entry.bound > shown[bestSoFar].bound) bestSoFar = index;
  });
  const current = done ? null : calc.steps[step];
  const L = current ? nameOf(current.landmark[0]) : "";

  return (
    <div className="calc">
      <div className="calc-head">
        <b>{done ? "Result" : `Landmark ${step + 1} of ${calc.steps.length}`}</b>
        <span className="calc-controls">
          <button title="Previous landmark" disabled={step === 0}
            onClick={() => { setCalcPlaying(false); setCalcStep(step - 1); }}>‹</button>
          <button title={playing ? "Pause" : "Play"} onClick={() => setCalcPlaying(!playing)}>
            {playing ? "❚❚" : "▶"}</button>
          <button title="Next landmark" disabled={done}
            onClick={() => { setCalcPlaying(false); setCalcStep(step + 1); }}>›</button>
          <button title="Close" onClick={stopCalc}>✕</button>
        </span>
      </div>

      {current && (
        <p className="panel-note calc-why">
          {current.via === "beyond_goal" ? (
            <><b>{L}</b> lies beyond the goal. The route <span className="calc-a">here → {L}</span>{" "}
              (A) is no longer than the rest of the trip plus{" "}
              <span className="calc-b">goal → {L}</span> (B), so the rest of the trip is at
              least <b>A − B</b>.</>
          ) : (
            <><b>{L}</b> lies behind you. The route <span className="calc-a">{L} → goal</span>{" "}
              (A) is no longer than <span className="calc-b">{L} → here</span> (B) plus the
              rest of the trip, so the rest of the trip is at least <b>A − B</b>.</>
          )}
        </p>
      )}

      <table className="grid calc-table">
        <thead>
          <tr><th>Landmark</th><th className="num">A</th><th className="num">B</th>
            <th className="num">A − B</th></tr>
        </thead>
        <tbody>
          {shown.map((entry, index) => (
            <tr key={index} className={[
              !done && index === step ? "calc-current" : "",
              index === bestSoFar && entry.bound > 0 ? "calc-best" : "",
              entry.bound <= 0 ? "stale" : "",
            ].join(" ")}>
              <td>{nameOf(entry.landmark[0])}
                <small className="dim"> · {entry.via === "beyond_goal" ? "beyond goal" : "behind you"}</small></td>
              <td className="num calc-a">{mins(entry.first)}</td>
              <td className="num calc-b">{mins(entry.second)}</td>
              <td className="num">{mins(entry.bound)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {done ? (
        <p className="panel-note calc-result">
          h = max(best landmark {calc.best !== null ? mins(calc.steps[calc.best].bound) : "—"},
          straight line {mins(calc.straight_line)}) = <b>{mins(calc.h)} min</b>
          {calc.source === "landmark" && calc.best !== null
            ? <> — from <b>{nameOf(calc.steps[calc.best].landmark[0])}</b>.</>
            : calc.source === "goal" ? <> — this is the goal.</> : <> — the straight line wins here.</>}
        </p>
      ) : (
        <p className="panel-note">
          Largest so far: <b>{mins(shown[bestSoFar].bound)} min</b>
          {shown[bestSoFar].bound > 0 && <> ({nameOf(shown[bestSoFar].landmark[0])})</>}
          {" "}· a negative A − B means that landmark tells us nothing here.
        </p>
      )}
    </div>
  );
}

export function NodeInspector({ visual }: { visual: VisualState | null }) {
  const stations = useStore((s) => s.stationsById);
  const trace = useStore((s) => s.trace);
  const heuristic = useStore((s) => s.heuristic);
  const meta = useStore((s) => s.meta);
  const storeWeights = useStore((s) => s.weights);

  const family = meta?.algorithms.find((entry) => entry.id === trace?.algorithm)?.family;
  const usesLandmarks = trace?.algorithm === "astar_landmark";

  // h/h* at the start, from the run itself: the h the algorithm really used,
  // not a separately fetched straight-line value. Blind searches use no h, and
  // once fares or transfers are weighted h is no longer in seconds, so the
  // ratio against optimal_sec (plain seconds) would be meaningless -- hidden.
  const informedness = useMemo(() => {
    if (!trace?.optimal_sec || family === "blind search") return null;
    const weights = trace.weights ?? storeWeights;
    if (weights.cost !== 0 || weights.transit !== 0 || weights.time <= 0) return null;
    const first = trace.events.find((event) => event.kind === "expand");
    if (!first || first.kind !== "expand" || !(first.h > 0)) return null;
    return first.h / (weights.time * trace.optimal_sec);
  }, [trace, family, storeWeights]);

  if (!visual || !visual.current) return <p className="empty">Run a search to inspect a node.</p>;

  const [stationId, lineId] = visual.current;
  const nameOf = (id: string) => stations.get(id)?.name_en ?? id;
  // only trust the breakdown when it was fetched for this very run
  const explain = usesLandmarks && heuristic?.kind === "landmark" && heuristic.algorithm === trace?.algorithm
    ? heuristic.explain?.[`${stationId}|${lineId}`]
    : undefined;
  return (
    <div className="panel-body">
      <h4 className="node-name">{stations.get(stationId)?.name_en ?? stationId}</h4>
      <p className="panel-note">on {lineId}{visual.depth !== null && <> · depth {visual.depth}</>}</p>

      <dl className="ghf">
        <div><dt>g</dt><dd>{mins(visual.currentG)}<small>min so far</small></dd></div>
        <div><dt>h</dt><dd>{mins(visual.currentH)}<small>min estimated</small></dd></div>
        <div><dt>f</dt><dd>{mins(visual.currentF)}<small>min total</small></dd></div>
      </dl>

      {explain && (
        <p className="panel-note alt-note">
          {explain.source === "landmark" && explain.landmark ? (
            <>
              h from landmark <b>{nameOf(explain.landmark[0])}</b>
              {explain.via === "beyond_goal" ? " — it lies beyond the goal:" : " — it lies behind you:"}
              <br />
              {explain.via === "beyond_goal" ? (
                <>here → {nameOf(explain.landmark[0])} {mins(explain.first)}
                  {" "}− goal → {nameOf(explain.landmark[0])} {mins(explain.second)}</>
              ) : (
                <>{nameOf(explain.landmark[0])} → goal {mins(explain.first)}
                  {" "}− {nameOf(explain.landmark[0])} → here {mins(explain.second)}</>
              )}
              {" "}= <b>{mins(explain.h)} min</b>
              <span className="dim"> · straight line would say {mins(explain.straight_line)}</span>
            </>
          ) : explain.source === "goal" ? (
            <>This is the goal, so h = 0.</>
          ) : (
            <>h comes from the straight-line bound here ({mins(explain.h)} min): this close
              to the goal no landmark gives a larger one.</>
          )}
        </p>
      )}

      {usesLandmarks && heuristic?.kind === "landmark" && (
        <LandmarkCalc node={visual.current} />
      )}

      {visual.threshold !== null && (
        <p className="panel-note">
          IDA* threshold: expand only while f ≤ <b>{mins(visual.threshold)} min</b>
          {visual.round !== null && <> · pass {visual.round + 1}</>}
        </p>
      )}
      {visual.backtrackFrom && (
        <p className="panel-note warn-text">
          Backtracked from {stations.get(visual.backtrackFrom[0])?.name_en} —{" "}
          {visual.backtrackReason === "depth_limit"
            ? "depth limit reached."
            : visual.backtrackReason === "exhausted"
              ? "every branch below it is done."
              : "f exceeded the threshold."}
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
          Heuristic informedness h/h* = <b>{informedness.toFixed(2)}</b>
          {usesLandmarks ? (
            informedness >= 0.995 ? (
              <> — the landmark bound is exact on this trip, so A* expands only
                stations on the route.</>
            ) : (
              <> — exact distances from the landmarks bound the real journey from
                below, never less than the straight-line bound.</>
            )
          ) : (
            <> — straight-line distance at 45 km/h underestimates the real journey
              {trace?.algorithm === "astar" || trace?.algorithm === "ida_star"
                ? ", which is why the search still has to explore widely." : "."}</>
          )}
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
