/** Chart tokens.
 *
 * These are the reference data-viz palette's LIGHT steps, used unmodified:
 * categorical slots 1-2 for the two-series charts, status `critical` for the
 * genuine failure state (an algorithm that returns no route). Metro line
 * colours are never reused here -- on this page hue means series, and a status
 * colour always ships with a label, never alone. A table view of every chart
 * is always on screen, which satisfies the palette's relief rule.
 */
export const VIZ = {
  surface: "#fcfcfb",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  muted: "#898781",
  ink: "#0b0b0b",
  inkSecondary: "#52514e",
  series1: "#2a78d6",
  series2: "#eb6834",
  series3: "#1baf7a",
  critical: "#d03b3b",
  good: "#0ca30c",
} as const;

export const axisProps = {
  stroke: VIZ.axis,
  tick: { fill: VIZ.muted, fontSize: 11 },
  tickLine: false,
} as const;
