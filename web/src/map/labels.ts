import type { Station } from "../api/types";
import { VIEW, type Point } from "./layouts";

export interface Placed {
  id: string;
  name: string;
  x: number;
  y: number;
  anchor: "start" | "end";
  important: boolean;
  fontSize: number;
}

interface Box { x0: number; y0: number; x1: number; y1: number }

/** On-screen label size in px, independent of zoom. */
export const LABEL_PX = 13.5;

const overlaps = (a: Box, b: Box): boolean =>
  a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/** Rough text width. Measuring 302 labels per frame is not worth the reflow;
 *  the system sans averages ~0.52em per character at this size. */
const widthOf = (text: string, fontSize: number): number => text.length * fontSize * 0.52 + fontSize * 0.5;

export interface PlacementInput {
  stations: Station[];
  positions: Map<string, Point>;
  /** Current zoom, so label boxes are measured in the same units as the map. */
  k: number;
  /** Lower sorts first and wins ties; Infinity means never draw. */
  rank: (station: Station) => number;
}

/**
 * Greedy label placement.
 *
 * Every station wanting a label is a potential collision, and at city scale the
 * downtown interchanges overlap badly. Place them in priority order, try the
 * side the dataset suggests and then the other one, and drop a label outright
 * rather than let it sit on top of one that matters more.
 */
export function placeLabels({ stations, positions, k, rank }: PlacementInput): Placed[] {
  // Constant on-screen size: the map scales, the type does not. Sized larger
  // than the surrounding UI because the map is the thing being read.
  const fontSize = LABEL_PX / k;
  const halfHeight = fontSize * 0.62;
  const dotRadius = 4.5 / Math.pow(k, 0.75);   // must track MetroMap's dot radius
  // Derive the gap from the dot, never independently: if the dot ever grows
  // wider than the gap, every label collides with its own station and nothing
  // gets placed at all.
  const gap = dotRadius + 3 / k;

  const candidates = stations
    .map((station) => ({ station, score: rank(station), point: positions.get(station.id) }))
    .filter((entry) => entry.point && Number.isFinite(entry.score))
    .sort((a, b) => a.score - b.score);

  // Station dots are obstacles too -- a label lying across a dot is as bad as
  // one lying across another label.
  const obstacles: Box[] = [];
  for (const station of stations) {
    const point = positions.get(station.id);
    if (!point) continue;
    obstacles.push({
      x0: point.x - dotRadius, y0: point.y - dotRadius,
      x1: point.x + dotRadius, y1: point.y + dotRadius,
    });
  }

  const placed: Placed[] = [];
  for (const { station, score, point } of candidates) {
    if (!point) continue;
    const width = widthOf(station.name_en, fontSize);

    // Prefer the side the dataset chose, unless that would run off the canvas.
    const preferLeft =
      point.x + gap + width > VIEW.width - 4
        ? true
        : point.x - gap - width < 4
          ? false
          : station.label_side === "left";

    let chosen: { box: Box; anchor: "start" | "end" } | null = null;
    for (const left of [preferLeft, !preferLeft]) {
      const x0 = left ? point.x - gap - width : point.x + gap;
      const box: Box = { x0, y0: point.y - halfHeight, x1: x0 + width, y1: point.y + halfHeight };
      if (box.x0 < 0 || box.x1 > VIEW.width) continue;
      if (obstacles.some((other) => overlaps(box, other))) continue;
      chosen = { box, anchor: left ? "end" : "start" };
      break;
    }
    if (!chosen) continue;

    obstacles.push(chosen.box);
    placed.push({
      id: station.id,
      name: station.name_en,
      x: chosen.anchor === "end" ? point.x - gap : point.x + gap,
      y: point.y + halfHeight * 0.55,
      anchor: chosen.anchor,
      important: score < 100,
      fontSize,
    });
  }
  return placed;
}
