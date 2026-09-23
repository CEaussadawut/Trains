import { useEffect, useMemo, useState } from "react";
import { CompareView } from "./components/CompareView";
import { Controls } from "./components/Controls";
import { FrontierPanel, NodeInspector, RouteSummary } from "./components/Inspector";
import { MetroMap } from "./components/MetroMap";
import { Playback } from "./components/Playback";
import { useStore } from "./state/store";

type Tab = "route" | "queue" | "node";

const TAB_LABEL: Record<Tab, string> = {
  route: "Journey",
  queue: "Open list",
  node: "Current node",
};

function Legend() {
  const network = useStore((s) => s.network);
  const active = useMemo(
    () => (network?.lines ?? []).filter((line) => line.active),
    [network],
  );
  return (
    <details className="legend">
      <summary>Legend · {active.length} lines</summary>
      <div className="legend-body">
        <div className="legend-group">
          <h4>Search state</h4>
          <span><i className="key-ring ring-current" /> expanding now</span>
          <span><i className="key-ring ring-frontier" /> on the open list</span>
          <span><i className="key-ring ring-expanded" /> already expanded</span>
          <span><i className="key-ring ring-pruned" /> pruned</span>
          <span><i className="key-ring ring-stuck" /> stuck</span>
        </div>
        <div className="legend-group">
          <h4>Lines</h4>
          {active.map((line) => (
            <span key={line.id}>
              <i className="key" style={{ background: line.color_hex }} /> {line.name_en}
            </span>
          ))}
        </div>
        <p className="panel-note">
          Dashed lines are not yet open. Station colour always means which line it is
          on — search state is drawn as a ring around it.
        </p>
      </div>
    </details>
  );
}

export function App() {
  const bootstrap = useStore((s) => s.bootstrap);
  const loading = useStore((s) => s.loading);
  const error = useStore((s) => s.error);
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const trace = useStore((s) => s.trace);
  const replay = useStore((s) => s.replay);
  const step = useStore((s) => s.step);
  const network = useStore((s) => s.network);

  const [tab, setTab] = useState<Tab>("route");

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  const visual = useMemo(() => (replay ? replay.at(step) : null), [replay, step]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Bangkok Metro Search</h1>
          <p>Heuristic and informed search, step by step</p>
        </div>
        <nav className="tabs">
          <button className={mode === "animate" ? "on" : ""} onClick={() => setMode("animate")}>
            Animate one search
          </button>
          <button className={mode === "compare" ? "on" : ""} onClick={() => setMode("compare")}>
            Compare all five
          </button>
        </nav>
        {network && (
          <p className="stats">
            {network.stats.active_stations} stations · {network.stats.nodes} nodes ·{" "}
            {network.stats.rail_edges} edges
          </p>
        )}
      </header>

      {error && <div className="banner banner-critical">⚠ {error}</div>}
      {loading && <div className="banner">Loading the network…</div>}

      <div className={`layout${mode === "compare" ? " layout-wide" : ""}`}>
        <Controls />

        {mode === "animate" ? (
          <main className="stage">
            <div className="stage-map">
              <MetroMap />
              <Legend />
            </div>
            <Playback />
            {trace && (
              <div className="readout">
                <p>
                  <b>{trace.algorithm}</b> expanded{" "}
                  <b>{trace.counts.expanded.toLocaleString()}</b> nodes and generated{" "}
                  <b>{trace.counts.generated.toLocaleString()}</b>.
                  {trace.phases.length > 1 && <> {trace.phases.length} passes.</>}
                  {trace.truncated && (
                    <> Only the last pass is replayable in detail — earlier passes are
                    summarised to keep the trace small.</>
                  )}
                </p>
              </div>
            )}
          </main>
        ) : (
          <main className="stage">
            <CompareView />
          </main>
        )}

        {mode === "animate" && (
          <aside className="panels">
            <nav className="panel-tabs">
              {(Object.keys(TAB_LABEL) as Tab[]).map((value) => (
                <button key={value} className={tab === value ? "on" : ""} onClick={() => setTab(value)}>
                  {TAB_LABEL[value]}
                </button>
              ))}
            </nav>
            {tab === "route" && <RouteSummary />}
            {tab === "queue" && <FrontierPanel visual={visual} />}
            {tab === "node" && <NodeInspector visual={visual} />}
          </aside>
        )}
      </div>
    </div>
  );
}
