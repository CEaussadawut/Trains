import { useMemo } from "react";
import type { Algorithm, Scenario } from "../api/types";
import { useStore } from "../state/store";

const SCENARIO_LABEL: Record<Scenario, string> = {
  operating: "Operating today",
  under_construction: "+ Under construction",
  planned: "+ Planned",
};

function StationSelect({
  label, value, onChange,
}: { label: string; value: string | null; onChange: (id: string) => void }) {
  const network = useStore((s) => s.network);
  const options = useMemo(
    () =>
      (network?.stations ?? [])
        .filter((station) => station.active)
        .sort((a, b) => a.name_en.localeCompare(b.name_en)),
    [network],
  );
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
        <option value="" disabled>choose a station…</option>
        {options.map((station) => (
          <option key={station.id} value={station.id}>
            {station.name_en} ({station.id})
          </option>
        ))}
      </select>
    </label>
  );
}

function WeightSlider({
  name, value, max, step, hint, onChange,
}: {
  name: string; value: number; max: number; step: number; hint: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="slider">
      <span className="slider-head">
        <span>{name}</span>
        <b>{value}</b>
      </span>
      <input type="range" min={0} max={max} step={step} value={value}
        onChange={(event) => onChange(Number(event.target.value))} />
      <small>{hint}</small>
    </label>
  );
}

export function Controls() {
  const meta = useStore((s) => s.meta);
  const scenario = useStore((s) => s.scenario);
  const algorithm = useStore((s) => s.algorithm);
  const start = useStore((s) => s.start);
  const goal = useStore((s) => s.goal);
  const weights = useStore((s) => s.weights);
  const beamWidth = useStore((s) => s.beamWidth);
  const running = useStore((s) => s.running);
  const mode = useStore((s) => s.mode);
  const showHeuristic = useStore((s) => s.showHeuristic);
  const layoutT = useStore((s) => s.layoutT);

  const setScenario = useStore((s) => s.setScenario);
  const setAlgorithm = useStore((s) => s.setAlgorithm);
  const setWeights = useStore((s) => s.setWeights);
  const setBeamWidth = useStore((s) => s.setBeamWidth);
  const setEndpoints = useStore((s) => s.setEndpoints);
  const run = useStore((s) => s.run);
  const runCompare = useStore((s) => s.runCompare);
  const toggleHeuristic = useStore((s) => s.toggleHeuristic);
  const setLayoutT = useStore((s) => s.setLayoutT);

  const families = useMemo(() => {
    const grouped = new Map<string, { id: Algorithm; label: string }[]>();
    for (const entry of meta?.algorithms ?? []) {
      const list = grouped.get(entry.family) ?? [];
      list.push({ id: entry.id, label: entry.label });
      grouped.set(entry.family, list);
    }
    return [...grouped.entries()];
  }, [meta]);

  const ready = Boolean(start && goal);

  return (
    <aside className="controls">
      <label className="field">
        <span>Network</span>
        <select value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}>
          {(meta?.scenarios ?? []).map((value) => (
            <option key={value} value={value}>{SCENARIO_LABEL[value] ?? value}</option>
          ))}
        </select>
      </label>

      <StationSelect label="From" value={start} onChange={(id) => setEndpoints(id, goal)} />
      <StationSelect label="To" value={goal} onChange={(id) => setEndpoints(start, id)} />
      <p className="hint">Or click two stations on the map.</p>

      {mode === "animate" && (
        <label className="field">
          <span>Algorithm</span>
          <select value={algorithm} onChange={(event) => setAlgorithm(event.target.value as Algorithm)}>
            {families.map(([family, entries]) => (
              <optgroup key={family} label={family}>
                {entries.map((entry) => (
                  <option key={entry.id} value={entry.id}>{entry.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      )}

      {algorithm === "beam" && mode === "animate" && (
        <WeightSlider name="Beam width" value={beamWidth} max={20} step={1}
          hint="How many partial paths survive each round." onChange={setBeamWidth} />
      )}

      <div className="group">
        <h3>Cost weights</h3>
        <WeightSlider name="Time" value={weights.time} max={5} step={0.5}
          hint="Seconds per second of travel." onChange={(time) => setWeights({ time })} />
        <WeightSlider name="Fare" value={weights.cost} max={60} step={5}
          hint="Seconds per baht — raise to prefer cheaper routes." onChange={(cost) => setWeights({ cost })} />
        <WeightSlider name="Transfers" value={weights.transit} max={1800} step={100}
          hint="Seconds per interchange — raise to avoid changing line." onChange={(transit) => setWeights({ transit })} />
      </div>

      <div className="group">
        <h3>Map</h3>
        <label className="toggle">
          <input type="checkbox" checked={layoutT > 0.5}
            onChange={(event) => setLayoutT(event.target.checked ? 1 : 0)} />
          <span>Geographic layout</span>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showHeuristic} onChange={toggleHeuristic} />
          <span>Shade by h(n) to goal</span>
        </label>
      </div>

      <button className="primary" disabled={!ready || running}
        onClick={() => (mode === "animate" ? run() : runCompare())}>
        {running ? "Running…" : mode === "animate" ? "Run search" : "Compare all five"}
      </button>
    </aside>
  );
}
