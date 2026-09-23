import type { Station } from "../api/types";

/** A single shared canvas for both layouts.
 *
 * Fitting the schematic and the geographic layouts into the *same* box is the
 * trick that makes everything else simple: the morph becomes pure per-station
 * interpolation, and the pan/zoom transform never has to change, so the viewer
 * keeps their place across the toggle.
 */
export const VIEW = { width: 1000, height: 1375, pad: 48 };

export interface Point {
  x: number;
  y: number;
}

export interface Layouts {
  schematic: Map<string, Point>;
  geographic: Map<string, Point>;
}

function fit(raw: Map<string, Point>): Map<string, Point> {
  const points = [...raw.values()];
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const usableW = VIEW.width - VIEW.pad * 2;
  const usableH = VIEW.height - VIEW.pad * 2;
  // One scale for both axes, so neither layout is stretched; the one with the
  // narrower aspect is letterboxed rather than distorted.
  const scale = Math.min(usableW / (maxX - minX || 1), usableH / (maxY - minY || 1));
  const offsetX = VIEW.pad + (usableW - (maxX - minX) * scale) / 2;
  const offsetY = VIEW.pad + (usableH - (maxY - minY) * scale) / 2;

  const fitted = new Map<string, Point>();
  raw.forEach((point, id) => {
    fitted.set(id, {
      x: offsetX + (point.x - minX) * scale,
      y: offsetY + (point.y - minY) * scale,
    });
  });
  return fitted;
}

export function buildLayouts(stations: Station[]): Layouts {
  const schematicRaw = new Map<string, Point>();
  const geographicRaw = new Map<string, Point>();

  const meanLat =
    stations.reduce((total, station) => total + station.lat, 0) / (stations.length || 1);
  // Equirectangular with a cos(lat) correction. Across Bangkok's ~0.5 degrees
  // this is visually indistinguishable from Web Mercator and far simpler.
  const k = Math.cos((meanLat * Math.PI) / 180);

  for (const station of stations) {
    schematicRaw.set(station.id, { x: station.map_x, y: station.map_y });
    geographicRaw.set(station.id, { x: station.lon * k, y: -station.lat });
  }

  return { schematic: fit(schematicRaw), geographic: fit(geographicRaw) };
}

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/** Interpolate a station between the two layouts. t=0 schematic, t=1 geographic. */
export function positionAt(layouts: Layouts, id: string, t: number): Point {
  const a = layouts.schematic.get(id);
  const b = layouts.geographic.get(id);
  if (!a || !b) return a ?? b ?? { x: 0, y: 0 };
  if (t <= 0) return a;
  if (t >= 1) return b;
  const e = easeInOutCubic(t);
  return { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e };
}

/** How much the schematic map distorts real geography, as a readable percentage. */
export function distortion(layouts: Layouts, edges: { from: string; to: string }[]): number {
  let schematicTotal = 0;
  let geographicTotal = 0;
  for (const edge of edges) {
    const s1 = layouts.schematic.get(edge.from);
    const s2 = layouts.schematic.get(edge.to);
    const g1 = layouts.geographic.get(edge.from);
    const g2 = layouts.geographic.get(edge.to);
    if (!s1 || !s2 || !g1 || !g2) continue;
    schematicTotal += Math.hypot(s2.x - s1.x, s2.y - s1.y);
    geographicTotal += Math.hypot(g2.x - g1.x, g2.y - g1.y);
  }
  if (!geographicTotal) return 0;
  return (schematicTotal / geographicTotal - 1) * 100;
}
