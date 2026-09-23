import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import type { CompareRow } from "../api/types";
import { useStore } from "../state/store";
import { VIZ, axisProps } from "./viz";

const mins = (seconds: number): string => (seconds / 60).toFixed(1);

/**
 * A log axis left on "auto" picks round decade bounds and silently clips any
 * bar below them -- greedy expands 24 nodes against a floor of 50, so its bar
 * vanished entirely. Derive the bounds from the data instead.
 */
const logDomain = (floor: number) =>
  [
    (dataMin: number) => Math.max(floor, dataMin * 0.6),
    (dataMax: number) => dataMax * 1.25,
  ] as const;

function Card({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <h3>{title}</h3>
      <p className="card-note">{note}</p>
      <div className="card-plot">{children}</div>
    </section>
  );
}

function ChartTooltip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <b>{payload[0]?.payload?.label ?? label}</b>
      {payload.map((entry: any) => (
        <p key={entry.dataKey}>
          <span className="swatch" style={{ background: entry.color }} />
          {entry.name}: <b>{Number(entry.value).toLocaleString()}</b> {unit ?? ""}
        </p>
      ))}
    </div>
  );
}

export function CompareView() {
  const compare = useStore((s) => s.compare);
  const running = useStore((s) => s.running);

  if (running) return <div className="empty">Benchmarking all five algorithms…</div>;
  if (!compare) {
    return (
      <div className="empty">
        Pick an origin and destination, then run the comparison to score every
        algorithm against Dijkstra's optimum on the same pair.
      </div>
    );
  }

  const solved = compare.results.filter((row) => row.solved && row.route);
  const failed = compare.results.filter((row) => !row.solved);

  const expansion = solved.map((row) => ({
    label: row.label,
    expanded: row.route!.nodes_expanded,
    generated: row.route!.nodes_generated,
  }));

  const runtime = solved.map((row) => ({ label: row.label, ms: Math.max(row.median_ms, 0.001) }));

  const tradeoff = solved.map((row) => ({
    label: row.label,
    expanded: row.route!.nodes_expanded,
    excess: row.excess_pct ?? 0,
    optimal: row.optimal ?? false,
  }));

  return (
    <div className="compare">
      <header className="compare-head">
        <h2>{compare.start} → {compare.goal}</h2>
        <p>
          Dijkstra's optimum is <b>{compare.optimal_sec ? mins(compare.optimal_sec) : "—"} minutes</b>.
          Every algorithm below solved the same problem on the same graph; median of{" "}
          {compare.repeats} runs.
        </p>
      </header>

      {failed.length > 0 && (
        <div className="banner banner-critical">
          <b>⚠ {failed.map((row) => row.label).join(", ")} returned no route.</b>{" "}
          A route exists — these algorithms are incomplete, so they can fail even when
          one does. They are excluded from the charts below and shown in the table.
        </div>
      )}

      <div className="cards">
        <Card
          title="Search effort"
          note="Nodes taken off the queue versus nodes ever created. Log scale — the spread is two orders of magnitude."
        >
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={expansion} margin={{ top: 8, right: 12, bottom: 4, left: 4 }} barGap={2}>
              <CartesianGrid stroke={VIZ.grid} vertical={false} />
              <XAxis dataKey="label" {...axisProps} interval={0} angle={-18} textAnchor="end" height={54} />
              <YAxis scale="log" domain={logDomain(1)} allowDataOverflow={false} {...axisProps} />
              <Tooltip content={<ChartTooltip unit="nodes" />} cursor={{ fill: "rgba(11,11,11,0.05)" }} />
              <Legend wrapperStyle={{ fontSize: 12, color: VIZ.inkSecondary }} />
              <Bar dataKey="expanded" name="expanded" fill={VIZ.series1} radius={[4, 4, 0, 0]} />
              <Bar dataKey="generated" name="generated" fill={VIZ.series2} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card
          title="Runtime"
          note="Median wall-clock time per search, log scale. IDA* trades memory for time and pays for it here."
        >
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={runtime} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={VIZ.grid} vertical={false} />
              <XAxis dataKey="label" {...axisProps} interval={0} angle={-18} textAnchor="end" height={54} />
              <YAxis scale="log" domain={logDomain(0.01)} {...axisProps} unit="ms" />
              <Tooltip content={<ChartTooltip unit="ms" />} cursor={{ fill: "rgba(11,11,11,0.05)" }} />
              <Bar dataKey="ms" name="median runtime" fill={VIZ.series1} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card
          title="Effort against quality"
          note="Bottom-left is the goal: few expansions, no detour. Anything above the zero line took a slower route than Dijkstra proves is possible."
        >
          <ResponsiveContainer width="100%" height={260}>
            <ScatterChart margin={{ top: 28, right: 40, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={VIZ.grid} />
              <XAxis type="number" dataKey="expanded" name="nodes expanded" scale="log"
                domain={logDomain(1)} {...axisProps} />
              {/* Round the upper bound: a linear axis renders its domain endpoint
                  as a tick, so a raw 18.5 * 1.25 would print 23.06601004500683%. */}
              <YAxis type="number" dataKey="excess" name="% above optimal" unit="%"
                domain={[0, (dataMax: number) => Math.max(5, Math.ceil((dataMax * 1.2) / 5) * 5)]}
                tickFormatter={(value: number) => String(Math.round(value))}
                {...axisProps} />
              <ZAxis range={[120, 120]} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: VIZ.axis }} />
              <Scatter data={tradeoff} fill={VIZ.series1}>
                {tradeoff.map((entry) => (
                  <Cell key={entry.label} fill={entry.optimal ? VIZ.series1 : VIZ.series2} />
                ))}
                <LabelList dataKey="label" position="top"
                  style={{ fill: VIZ.inkSecondary, fontSize: 11 }} />
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          <p className="card-note">
            <span className="key" style={{ background: VIZ.series1 }} /> optimal route
            <span className="key" style={{ background: VIZ.series2 }} /> sub-optimal route
          </p>
        </Card>
      </div>

      <section className="card">
        <h3>All results</h3>
        <table className="grid compare-table">
          <thead>
            <tr>
              <th>Algorithm</th><th>Family</th><th>Result</th>
              <th className="num">Minutes</th><th className="num">vs optimal</th>
              <th className="num">THB</th><th className="num">Transfers</th>
              <th className="num">Expanded</th><th className="num">Generated</th>
              <th className="num">Median ms</th><th className="num">Peak KiB</th>
            </tr>
          </thead>
          <tbody>
            {compare.results.map((row: CompareRow) => (
              <tr key={row.algorithm} className={row.solved ? "" : "row-fail"}>
                <td><b>{row.label}</b></td>
                <td className="dim">{row.family}</td>
                <td>
                  {row.solved
                    ? row.optimal
                      ? <span className="pill pill-good">optimal</span>
                      : <span className="pill pill-warn">sub-optimal</span>
                    : <span className="pill pill-fail">no route</span>}
                </td>
                <td className="num">{row.route ? mins(row.route.duration_sec) : "—"}</td>
                <td className="num">
                  {row.solved ? (row.optimal ? "—" : `+${(row.excess_pct ?? 0).toFixed(1)}%`) : "—"}
                </td>
                <td className="num">{row.route ? row.route.fare_thb.toFixed(0) : "—"}</td>
                <td className="num">{row.route?.transfers ?? "—"}</td>
                <td className="num">{row.route?.nodes_expanded.toLocaleString() ?? "—"}</td>
                <td className="num">{row.route?.nodes_generated.toLocaleString() ?? "—"}</td>
                <td className="num">{row.solved ? row.median_ms.toFixed(3) : "—"}</td>
                <td className="num">
                  {row.route ? (row.route.peak_memory_bytes / 1024).toFixed(1) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
