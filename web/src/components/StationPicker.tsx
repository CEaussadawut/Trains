import { useEffect, useMemo, useRef, useState } from "react";
import type { Line, Station } from "../api/types";
import { useStore } from "../state/store";

interface Option {
  station: Station;
  line: Line;
  position: number;
}

interface Group {
  line: Line;
  options: Option[];
}

const normalise = (value: string): string => value.toLowerCase().trim();

function buildGroups(lines: Line[], stations: Map<string, Station>, query: string): Group[] {
  const needle = normalise(query);
  const groups: Group[] = [];

  for (const line of lines) {
    if (!line.active) continue;
    const options: Option[] = [];
    // `line.stations` arrives in running order from the server, so the list
    // reads the way the line does rather than alphabetically.
    line.stations.forEach((id, index) => {
      const station = stations.get(id);
      if (!station?.active) return;
      if (
        needle &&
        !normalise(station.name_en).includes(needle) &&
        !station.name_th.includes(query.trim()) &&
        !normalise(station.id).includes(needle)
      ) {
        return;
      }
      options.push({ station, line, position: index + 1 });
    });
    if (options.length) groups.push({ line, options });
  }
  return groups;
}

export function StationPicker({
  label, value, onChange,
}: { label: string; value: string | null; onChange: (id: string) => void }) {
  // Select the stable slice, never a freshly-built array: zustand compares
  // selector results with Object.is, so `s.network?.lines ?? []` would hand
  // back a new [] on every call and re-render forever.
  const network = useStore((s) => s.network);
  const stations = useStore((s) => s.stationsById);
  const lines = network?.lines;
  const setHovered = useStore((s) => s.setHovered);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(
    () => buildGroups(lines ?? [], stations, query),
    [lines, stations, query],
  );
  const flat = useMemo(() => groups.flatMap((group) => group.options), [groups]);

  useEffect(() => { setCursor(0); }, [query]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
    else { setQuery(""); setHovered(null); }
  }, [open, setHovered]);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(".option.at")?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  const choose = (id: string) => { onChange(id); setOpen(false); };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!flat.length) return;
      const next = event.key === "ArrowDown" ? cursor + 1 : cursor - 1;
      const wrapped = (next + flat.length) % flat.length;
      setCursor(wrapped);
      setHovered(flat[wrapped].station.id);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (flat[cursor]) choose(flat[cursor].station.id);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  const selected = value ? stations.get(value) : undefined;
  const selectedLines =
    selected && lines
      ? lines.filter((line) => line.active && selected.active_lines.includes(line.id))
      : [];

  return (
    <div className="field picker" ref={boxRef}>
      <span>{label}</span>

      <button type="button" className={`picker-value ${open ? "on" : ""}`} onClick={() => setOpen(!open)}>
        {selected ? (
          <>
            <span className="picker-swatches">
              {selectedLines.map((line) => (
                <i key={line.id} className="key" style={{ background: line.color_hex }} />
              ))}
            </span>
            <span className="picker-name">{selected.name_en}</span>
            <span className="picker-id">{selected.id}</span>
          </>
        ) : (
          <span className="picker-empty">choose a station…</span>
        )}
        <span className="picker-caret">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="picker-pop">
          <input
            ref={inputRef}
            className="picker-search"
            placeholder="Search name, Thai name or code…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
          />

          <div className="picker-list" ref={listRef}>
            {flat.length === 0 && <p className="picker-none">No station matches “{query}”.</p>}

            {groups.map((group) => (
              <div key={group.line.id} className="picker-group">
                <div className="picker-group-head">
                  <i className="key" style={{ background: group.line.color_hex }} />
                  <b>{group.line.name_en}</b>
                  <span>{group.options.length}</span>
                </div>
                {group.options.map((option) => {
                  const index = flat.indexOf(option);
                  return (
                    <button
                      key={`${group.line.id}-${option.station.id}`}
                      type="button"
                      className={
                        `option${index === cursor ? " at" : ""}` +
                        (option.station.id === value ? " picked" : "")
                      }
                      onMouseEnter={() => { setCursor(index); setHovered(option.station.id); }}
                      onClick={() => choose(option.station.id)}
                    >
                      <span className="option-pos">{option.position}</span>
                      <span className="option-name">
                        {option.station.name_en}
                        <small>{option.station.name_th}</small>
                      </span>
                      <span className="option-id">{option.station.id}</span>
                      {option.station.interchange && <span className="option-x" title="interchange">⇄</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
