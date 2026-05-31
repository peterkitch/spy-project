import { useMemo } from "react";
import type { CccPoint } from "../types";
import { CCC_EMPTY_MESSAGE } from "../constants";

interface CccStepChartProps {
  series: CccPoint[];
  secondary: string;
}

// Hand-rolled inline SVG step plot. No charting library. Mirrors
// the Dash side's "line": {"shape": "hv"} step semantics: the
// line stays flat between matching bars and steps vertically at
// each new point. No interpolation; no smoothing; no synthesized
// data; flat (zero-delta) segments are preserved verbatim.
//
// The x axis is index-based (one tick per ccc_series point);
// dates are surfaced as part of the dataset for the visible
// labels at the chart endpoints. The y axis is the engine-emitted
// cumulative_capture_pct.
export function CccStepChart({ series, secondary }: CccStepChartProps) {
  const dims = useMemo(() => computeDims(series), [series]);
  if (series.length === 0) {
    return (
      <div id="k6mtf-modal-ccc-empty" className="ccc-empty">
        {CCC_EMPTY_MESSAGE}
      </div>
    );
  }
  if (dims === null) {
    return (
      <div id="k6mtf-modal-ccc-empty" className="ccc-empty">
        {CCC_EMPTY_MESSAGE}
      </div>
    );
  }
  const { points, minY, maxY, minDate, maxDate } = dims;
  return (
    <div id="k6mtf-modal-ccc-chart" className="ccc-chart">
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${secondary} K=6 MTF CCC step plot`}
      >
        <rect
          x={PAD_L}
          y={PAD_T}
          width={VIEW_W - PAD_L - PAD_R}
          height={VIEW_H - PAD_T - PAD_B}
          className="ccc-plot-bg"
        />
        <path d={pathDataFromPoints(points)} className="ccc-step-line" />
        <line
          x1={PAD_L}
          y1={VIEW_H - PAD_B}
          x2={VIEW_W - PAD_R}
          y2={VIEW_H - PAD_B}
          className="ccc-axis"
        />
        <line
          x1={PAD_L}
          y1={PAD_T}
          x2={PAD_L}
          y2={VIEW_H - PAD_B}
          className="ccc-axis"
        />
        <text x={PAD_L} y={PAD_T - 6} className="ccc-axis-label">
          {`${maxY.toFixed(2)}%`}
        </text>
        <text x={PAD_L} y={VIEW_H - PAD_B + 14} className="ccc-axis-label">
          {`${minY.toFixed(2)}%`}
        </text>
        <text
          x={PAD_L}
          y={VIEW_H - 4}
          className="ccc-axis-label"
        >
          {minDate}
        </text>
        <text
          x={VIEW_W - PAD_R}
          y={VIEW_H - 4}
          className="ccc-axis-label ccc-axis-label-right"
          textAnchor="end"
        >
          {maxDate}
        </text>
      </svg>
    </div>
  );
}

interface PlotPoint {
  x: number;
  y: number;
}

interface ChartDims {
  points: PlotPoint[];
  minY: number;
  maxY: number;
  minDate: string;
  maxDate: string;
}

const VIEW_W = 720;
const VIEW_H = 280;
const PAD_T = 16;
const PAD_R = 16;
const PAD_B = 28;
const PAD_L = 64;

function computeDims(series: CccPoint[]): ChartDims | null {
  if (series.length === 0) {
    return null;
  }
  const ys = series
    .map((p) => p.cumulative_capture_pct)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (ys.length === 0) {
    return null;
  }
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (minY === maxY) {
    // Degenerate flat series: expand by 1% so the line is still
    // visible. No data is synthesized; only the y-range is widened.
    minY -= 0.5;
    maxY += 0.5;
  }
  const plotW = VIEW_W - PAD_L - PAD_R;
  const plotH = VIEW_H - PAD_T - PAD_B;
  const n = series.length;
  const points: PlotPoint[] = series.map((p, i) => {
    const x = PAD_L + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2);
    const yVal = typeof p.cumulative_capture_pct === "number"
      && Number.isFinite(p.cumulative_capture_pct)
      ? p.cumulative_capture_pct
      : minY;
    const y = PAD_T + plotH - ((yVal - minY) / (maxY - minY)) * plotH;
    return { x, y };
  });
  const minDate = series[0]?.date_utc ?? "";
  const maxDate = series[series.length - 1]?.date_utc ?? "";
  return { points, minY, maxY, minDate, maxDate };
}

// Step-after path: M x0 y0 then for each subsequent point H x then V y.
// This produces the "hv" shape: flat segment from the previous point
// to the new x at the previous y, then a vertical jump to the new y.
function pathDataFromPoints(points: PlotPoint[]): string {
  if (points.length === 0) {
    return "";
  }
  const first = points[0]!;
  const parts: string[] = [`M ${first.x.toFixed(2)} ${first.y.toFixed(2)}`];
  for (let i = 1; i < points.length; i += 1) {
    const p = points[i]!;
    parts.push(`H ${p.x.toFixed(2)}`);
    parts.push(`V ${p.y.toFixed(2)}`);
  }
  return parts.join(" ");
}
