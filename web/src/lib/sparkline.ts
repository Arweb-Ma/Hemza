export interface SparklineGeometry {
  /** SVG path data for the price line. */
  path: string;
  /** Position of the last point (the current price). */
  end: { x: number; y: number };
}

/** Maps a price series onto a width x height box, leaving `pad` px above and below. */
export function sparkline(
  values: readonly number[],
  width: number,
  height: number,
  pad = 3,
): SparklineGeometry | null {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const y = (value: number) => height - pad - ((value - min) / range) * (height - pad * 2);
  const round = (n: number) => Math.round(n * 10) / 10;

  const points = values.map((value, i) => ({ x: round(i * stepX), y: round(y(value)) }));
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x} ${p.y}`).join('');
  return { path, end: points[points.length - 1]! };
}
