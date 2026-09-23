import { memo, useCallback, useMemo, useRef, useState } from "react";
import type { Station } from "../api/types";
import { VIEW, positionAt, type Point } from "../map/layouts";
import { keyOf, stationOf, type VisualState } from "../map/replay";
import { useStore } from "../state/store";

type StationState =
  | "idle" | "inactive" | "expanded" | "frontier" | "current" | "path" | "pruned"
  | "start" | "goal" | "stuck";

interface Transform { k: number; x: number; y: number }

const LABEL_ZOOM = 1.6;

/** Search state never touches line colour: hue means geography, rings mean algorithm. */
const STATE_RING: Partial<Record<StationState, string>> = {
  expanded: "var(--expanded)",
  frontier: "var(--frontier)",
  current: "var(--current)",
  pruned: "var(--pruned)",
  stuck: "var(--stuck)",
};

function stationStates(
  visual: VisualState | null,
  start: string | null,
  goal: string | null,
  stations: Station[],
): Map<string, StationState> {
  const states = new Map<string, StationState>();
  for (const station of stations) states.set(station.id, station.active ? "idle" : "inactive");
  if (visual) {
    visual.prunedKeys.forEach((key) => states.set(stationOf(key), "pruned"));
    visual.expanded.forEach((key) => states.set(stationOf(key), "expanded"));
    visual.frontierKeys.forEach((key) => {
      const id = stationOf(key);
      if (states.get(id) !== "expanded") states.set(id, "frontier");
    });
    if (visual.finalPath) for (const node of visual.finalPath) states.set(node[0], "path");
    if (visual.current) states.set(visual.current[0], "current");
    if (visual.stuckAt) states.set(visual.stuckAt[0], "stuck");
  }
  if (start) states.set(start, "start");
  if (goal) states.set(goal, "goal");
  return states;
}

const StationDot = memo(function StationDot({
  station, point, state, radius, onPick, onHover,
}: {
  station: Station;
  point: Point;
  state: StationState;
  radius: number;
  onPick: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const ring = STATE_RING[state];
  const terminal = state === "start" || state === "goal";
  return (
    <g
      className={`stn stn-${state}`}
      transform={`translate(${point.x} ${point.y})`}
      onClick={(event) => { event.stopPropagation(); onPick(station.id); }}
      onMouseEnter={() => onHover(station.id)}
      onMouseLeave={() => onHover(null)}
    >
      {ring && <circle className="ring" r={radius * 2.6} fill="none" stroke={ring} strokeWidth={radius * 0.9} />}
      <circle
        r={terminal ? radius * 1.7 : station.interchange ? radius * 1.25 : radius}
        className="dot"
      />
      {/* Generous invisible hit target -- the visible dots are tiny. */}
      <circle r={Math.max(radius * 3, 9)} fill="transparent" />
    </g>
  );
});

export function MetroMap() {
  const network = useStore((s) => s.network);
  const layouts = useStore((s) => s.layouts);
  const layoutT = useStore((s) => s.layoutT);
  const start = useStore((s) => s.start);
  const goal = useStore((s) => s.goal);
  const step = useStore((s) => s.step);
  const replay = useStore((s) => s.replay);
  const showHeuristic = useStore((s) => s.showHeuristic);
  const heuristic = useStore((s) => s.heuristic);
  const hovered = useStore((s) => s.hovered);
  const pickStation = useStore((s) => s.pickStation);
  const setHovered = useStore((s) => s.setHovered);
  const linesById = useStore((s) => s.linesById);

  const [transform, setTransform] = useState<Transform>({ k: 1, x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const visual = useMemo(() => (replay ? replay.at(step) : null), [replay, step]);

  const positions = useMemo(() => {
    const map = new Map<string, Point>();
    if (!layouts || !network) return map;
    for (const station of network.stations) {
      map.set(station.id, positionAt(layouts, station.id, layoutT));
    }
    return map;
  }, [layouts, network, layoutT]);

  const states = useMemo(
    () => stationStates(visual, start, goal, network?.stations ?? []),
    [visual, start, goal, network],
  );

  const pathEdges = useMemo(() => {
    const path = visual?.finalPath;
    if (!path) return [];
    const segments: { from: string; to: string; color: string }[] = [];
    for (let i = 0; i < path.length - 1; i += 1) {
      const line = linesById.get(path[i + 1][1]);
      segments.push({
        from: path[i][0],
        to: path[i + 1][0],
        color: path[i][1] === path[i + 1][1] ? line?.color_hex ?? "#fff" : "var(--transfer)",
      });
    }
    return segments;
  }, [visual, linesById]);

  const onWheel = useCallback((event: React.WheelEvent) => {
    event.preventDefault();
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((event.clientX - rect.left) / rect.width) * VIEW.width;
    const py = ((event.clientY - rect.top) / rect.height) * VIEW.height;
    setTransform((current) => {
      const k = Math.min(14, Math.max(0.5, current.k * Math.exp(-event.deltaY * 0.0015)));
      // Keep the point under the cursor fixed while zooming.
      return { k, x: px - ((px - current.x) / current.k) * k, y: py - ((py - current.y) / current.k) * k };
    });
  }, []);

  const onPointerDown = (event: React.PointerEvent) => {
    dragging.current = { x: event.clientX, y: event.clientY, tx: transform.x, ty: transform.y };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragging.current;
    if (!drag || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scale = VIEW.width / rect.width;
    setTransform((current) => ({
      ...current,
      x: drag.tx + (event.clientX - drag.x) * scale,
      y: drag.ty + (event.clientY - drag.y) * scale,
    }));
  };

  const endDrag = () => { dragging.current = null; };

  if (!network || !layouts) return <div className="map-empty">loading network…</div>;

  const radius = 3.4 / Math.sqrt(transform.k);
  const strokeScale = 1 / Math.sqrt(transform.k);
  const showLabels = transform.k >= LABEL_ZOOM;
  const pathStations = new Set(visual?.finalPath?.map((node) => node[0]) ?? []);

  return (
    <div className="map-wrap">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
        className="map"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
      >
        <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.k})`}>
          {/* h(n) shading sits behind everything as a halo, so it never competes
              with the line colours for the station dot itself. */}
          {showHeuristic && heuristic && network.stations.map((station) => {
            const h = heuristic.h[station.id];
            const point = positions.get(station.id);
            if (h === undefined || !point) return null;
            const t = heuristic.max_h ? 1 - h / heuristic.max_h : 0;
            return (
              <circle key={`h-${station.id}`} cx={point.x} cy={point.y}
                r={5 + t * 12} fill="var(--heuristic)" opacity={0.08 + t * 0.42} />
            );
          })}

          {network.edges.map((edge, index) => {
            const a = positions.get(edge.from);
            const b = positions.get(edge.to);
            if (!a || !b) return null;
            const line = linesById.get(edge.line ?? edge.from_line);
            return (
              <line key={`e${index}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke={line?.color_hex ?? "#666"}
                strokeWidth={(edge.active ? 3.4 : 2.2) * strokeScale}
                strokeDasharray={line?.dash || undefined}
                strokeLinecap="round"
                opacity={edge.active ? 0.95 : 0.22} />
            );
          })}

          {network.transfers.map((edge, index) => {
            const a = positions.get(edge.from);
            const b = positions.get(edge.to);
            if (!a || !b || edge.from === edge.to) return null;
            return (
              <line key={`t${index}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke="var(--transfer)" strokeWidth={1.6 * strokeScale}
                strokeDasharray="3 3" opacity={edge.active ? 0.6 : 0.15} />
            );
          })}

          {/* The final route, drawn over the network with a casing for contrast. */}
          {pathEdges.map((segment, index) => {
            const a = positions.get(segment.from);
            const b = positions.get(segment.to);
            if (!a || !b) return null;
            return (
              <g key={`p${index}`}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke="var(--route-casing)" strokeWidth={9 * strokeScale} strokeLinecap="round" />
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={segment.color} strokeWidth={5 * strokeScale} strokeLinecap="round" />
              </g>
            );
          })}

          {network.stations.map((station) => {
            const point = positions.get(station.id);
            if (!point) return null;
            return (
              <StationDot key={station.id} station={station} point={point}
                state={states.get(station.id) ?? "idle"} radius={radius}
                onPick={pickStation} onHover={setHovered} />
            );
          })}

          {network.stations.map((station) => {
            const point = positions.get(station.id);
            if (!point) return null;
            const important =
              hovered === station.id || station.id === start || station.id === goal ||
              pathStations.has(station.id);
            if (!showLabels && !important) return null;
            const left = station.label_side === "left";
            return (
              <text key={`l-${station.id}`}
                x={point.x + (left ? -7 : 7) * strokeScale}
                y={point.y + 3.5 * strokeScale}
                textAnchor={left ? "end" : "start"}
                className={`label ${important ? "label-strong" : ""}`}
                fontSize={11 * strokeScale}>
                {station.name_en}
              </text>
            );
          })}
        </g>
      </svg>

      <div className="map-tools">
        <button onClick={() => setTransform({ k: 1, x: 0, y: 0 })}>Reset view</button>
        <span className="zoom">{transform.k.toFixed(1)}×</span>
      </div>
    </div>
  );
}
