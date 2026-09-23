/** Chart tokens.
 *
 * These are the reference data-viz palette's DARK steps, used unmodified:
 * categorical slots 1-2 for the two-series charts, status `critical` for the
 * genuine failure state (an algorithm that returns no route). Line colours from
 * the metro map are never reused here -- on this page hue means series, and a
 * status colour always ships with a label, never alone.
 */
export const VIZ = {
  surface: "#1a1a19",
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#898781",
  ink: "#ffffff",
  inkSecondary: "#c3c2b7",
  series1: "#3987e5",
  series2: "#d95926",
  series3: "#199e70",
  critical: "#d03b3b",
  good: "#0ca30c",
} as const;

export const axisProps = {
  stroke: VIZ.axis,
  tick: { fill: VIZ.muted, fontSize: 11 },
  tickLine: false,
} as const;
