export interface FrameStats {
  frames: number;
  p50: number;
  p95: number;
  max: number;
  mean: number;
}

// Nearest-rank percentile over a copy; input order is untouched.
export function percentile(samples: readonly number[], p: number): number {
  if (samples.length === 0) return Number.NaN;
  const sorted = [...samples].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.min(sorted.length, Math.max(1, rank)) - 1] ?? Number.NaN;
}

export function summarize(samples: readonly number[]): FrameStats {
  const total = samples.reduce((sum, s) => sum + s, 0);
  return {
    frames: samples.length,
    p50: percentile(samples, 50),
    p95: percentile(samples, 95),
    max: samples.length ? Math.max(...samples) : Number.NaN,
    mean: samples.length ? total / samples.length : Number.NaN,
  };
}

export function formatStats(name: string, s: FrameStats): string {
  const f = (n: number) => n.toFixed(2).padStart(7);
  return `${name.padEnd(34)} frames=${s.frames}  p50=${f(s.p50)}ms  p95=${f(s.p95)}ms  max=${f(s.max)}ms`;
}
