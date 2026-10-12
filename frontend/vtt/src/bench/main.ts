import { SceneRuntime } from '../scene/SceneRuntime';
import type { RenderScene } from '../scene/renderModel';
import { buildReferenceScene, moveToken } from '../spike/referenceScene';
import { drawCanvas2d } from './canvas2d';
import { formatStats, summarize } from './stats';
import '../styles/app.css';

// R4 spike: is Pixi worth it over Canvas 2D for the charter's reference scene
// (4096 x 4096, 100 tokens, 500 walls, 20 lights)? Fog and vision are not yet
// drawn by either renderer; they wait for D6.
//
// Each sample is CPU submit time plus a forced GPU sync (gl.finish for WebGL,
// a 1-pixel readback for 2D), so deferred GPU work is counted. Frames are
// forced, not rAF-paced, so this runs in a hidden tab too.

const FRAMES = 300;
const out = document.getElementById('out') as HTMLPreElement;
const pixiHost = document.getElementById('pixi-host') as HTMLDivElement;
const canvas2d = document.getElementById('canvas2d') as HTMLCanvasElement;

function log(line: string): void {
  out.textContent = out.textContent === 'Not run.' ? line : `${out.textContent}\n${line}`;
}

// Drag simulation: one token moves every frame, like a live drag preview.
function dragFrames(base: RenderScene): RenderScene[] {
  const frames: RenderScene[] = [];
  let scene = base;
  for (let i = 0; i < FRAMES; i += 1) {
    scene = moveToken(scene, 't0', i % 2 === 0 ? 8 : -8, 4);
    frames.push(scene);
  }
  return frames;
}

// Worst case: every token moves every frame (a full resync or mass update).
function massFrames(base: RenderScene): RenderScene[] {
  const frames: RenderScene[] = [];
  for (let i = 0; i < FRAMES; i += 1) {
    const d = i % 2 === 0 ? 8 : -8;
    frames.push({ ...base, tokens: base.tokens.map((t) => ({ ...t, x: t.x + d })) });
  }
  return frames;
}

async function benchPixi(frames: RenderScene[], label: string): Promise<string> {
  const runtime = new SceneRuntime(pixiHost, { surface: 'gm' });
  await runtime.init();
  runtime.setScene(frames[0] ?? buildReferenceScene());
  runtime.fit();
  runtime.renderNow();
  runtime.finishGpuForBench();
  const samples: number[] = [];
  for (const scene of frames) {
    const start = performance.now();
    runtime.setScene(scene);
    runtime.renderNow();
    runtime.finishGpuForBench();
    samples.push(performance.now() - start);
  }
  runtime.destroy();
  return formatStats(`pixi ${label}`, summarize(samples));
}

function bench2d(frames: RenderScene[], label: string): string {
  const width = pixiHost.clientWidth;
  const height = pixiHost.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  canvas2d.width = Math.round(width * dpr);
  canvas2d.height = Math.round(height * dpr);
  const ctx = canvas2d.getContext('2d');
  if (!ctx) return `canvas2d ${label}: no 2D context`;
  const first = frames[0];
  if (!first) return `canvas2d ${label}: no frames`;
  const scale = Math.min(canvas2d.width / first.width, canvas2d.height / first.height);
  const samples: number[] = [];
  for (const scene of frames) {
    const start = performance.now();
    drawCanvas2d(ctx, scene, scale);
    ctx.getImageData(0, 0, 1, 1);
    samples.push(performance.now() - start);
  }
  return formatStats(`canvas2d ${label}`, summarize(samples));
}

async function run(): Promise<void> {
  out.textContent = 'Not run.';
  const base = buildReferenceScene();
  log(
    `scene ${base.width}x${base.height} tokens=${base.tokens.length} walls=${base.walls.length} ` +
      `lights=${base.lights.length}  viewport=${pixiHost.clientWidth}x${pixiHost.clientHeight} ` +
      `dpr=${window.devicePixelRatio}`,
  );
  const drag = dragFrames(base);
  const mass = massFrames(base);
  log(await benchPixi(drag, 'drag (1 token/frame)'));
  log(await benchPixi(mass, 'mass (100 tokens/frame)'));
  log(bench2d(drag, 'drag (1 token/frame)'));
  log(bench2d(mass, 'mass (100 tokens/frame)'));
  log('done');
}

document.getElementById('run')?.addEventListener('click', () => void run());
(window as unknown as { __vttBench: () => Promise<void> }).__vttBench = run;
