import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";

export const REPO_URL = "https://github.com/CEaussadawut/Trains";

const share = (part: number, whole: number): string =>
  whole ? `${part}/${whole} (${Math.round((part / whole) * 100)}%)` : "—";

/**
 * The numbers this app prints are only as good as the dataset behind them, and
 * most of that dataset is modelled rather than measured. Saying so in the UI
 * keeps a confident-looking "76.7 minutes" from being read as a real timetable.
 *
 * The figures are read from the API for the scenario currently on screen, not
 * hardcoded, so they cannot drift away from the data they describe.
 */
export function DataNote() {
  const network = useStore((s) => s.network);
  const scenario = useStore((s) => s.scenario);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const esc = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  if (!network) return null;
  const { edges, stations, transfers } = network.quality;

  return (
    <div className="datanote" ref={box}>
      <button
        type="button"
        className={`datanote-trigger${open ? " on" : ""}`}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden="true">⚠</span> Estimated data
      </button>

      {open && (
        <div className="datanote-pop" role="dialog" aria-label="About this dataset">
          <h3>Travel times and fares are modelled, not measured</h3>
          <p>
            This is a course project. The search algorithms are exact on the graph
            as modelled, but the graph itself is largely estimated — treat the
            minutes and baht as plausible, not authoritative.
          </p>

          <table className="grid">
            <tbody>
              <tr>
                <td>Track segments estimated</td>
                <td className="num">{share(edges.estimated, edges.total)}</td>
              </tr>
              <tr>
                <td>Track segments unverified</td>
                <td className="num">{share(edges.unverified, edges.total)}</td>
              </tr>
              <tr>
                <td>Station coordinates interpolated</td>
                <td className="num">{share(stations.interpolated, stations.total)}</td>
              </tr>
              <tr>
                <td>Interchange walks estimated</td>
                <td className="num">{share(transfers.estimated, transfers.total)}</td>
              </tr>
            </tbody>
          </table>
          <p className="datanote-scope">Figures are for the “{scenario}” network currently shown.</p>

          <ul>
            <li>
              Journey time is derived as <code>distance ÷ average line speed + dwell</code>,
              using one average speed per line. The distance is straight-line, so it
              understates track length on curves.
            </li>
            <li>
              Fares ignore the per-operator entry fee, so trips crossing operators
              are charged less than they would be in reality.
            </li>
            <li>
              Unbuilt lines carry invented opening years, speeds and station
              positions — the “under construction” and “planned” networks are
              scenarios, not forecasts.
            </li>
            <li>
              Several stations share an English name (Phaya Thai, Samrong, Din
              Daeng…). Those are genuinely separate stations joined by a walk, not
              duplicate rows.
            </li>
          </ul>

          <p className="datanote-foot">
            Comparisons between algorithms — nodes expanded, optimality, runtime —
            are unaffected by any of this, because every algorithm runs on the same
            graph.{" "}
            <a href={`${REPO_URL}/blob/main/datasets/README.md`} target="_blank" rel="noreferrer">
              Dataset schema ↗
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
