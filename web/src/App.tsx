import { useEffect, useMemo, useState } from "react";
import { CompareView } from "./components/CompareView";
import { DataNote, REPO_URL } from "./components/DataNote";
import { DesktopOnly } from "./components/DesktopOnly";
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
  const algorithmCount = useStore((s) => s.meta?.algorithms.length ?? 0);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  const visual = useMemo(() => (replay ? replay.at(step) : null), [replay, step]);

  return (
    <div className="app">
      <DesktopOnly />
      <header className="topbar">
        <div className="brand">
          <h1>Bangkok Metro Search</h1>
          <p>Blind, heuristic and informed search, step by step</p>
        </div>
        <nav className="tabs">
          <button className={mode === "animate" ? "on" : ""} onClick={() => setMode("animate")}>
            Animate one search
          </button>
          <button className={mode === "compare" ? "on" : ""} onClick={() => setMode("compare")}>
            Compare {algorithmCount ? `all ${algorithmCount}` : "all"}
          </button>
        </nav>
        <div className="topbar-right">
          {network && (
            <p className="stats">
              {network.stats.active_stations} stations · {network.stats.nodes} nodes ·{" "}
              {network.stats.rail_edges} edges
            </p>
          )}
          <DataNote />
          <a className="source-link" href={REPO_URL} target="_blank" rel="noreferrer"
             title="View the source on GitHub">
            <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" fill="currentColor">
              <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38l-.01-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48l-.01 2.2c0 .21.15.46.55.38A8 8 0 0 0 8 0Z" />
            </svg>
            Source
          </a>
        </div>
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
