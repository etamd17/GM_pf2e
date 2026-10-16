import { gridLines, tokenRadius, type RenderScene } from '../scene/renderModel';
import { uiFont } from '../ui/fonts';

// Canvas 2D baseline for the R4 spike: the same primitives SceneRuntime draws,
// drawn the way the legacy map.js does, so the two renderers can be compared
// on the reference scene. Not used by the application.

const hex = (c: number) => `#${c.toString(16).padStart(6, '0')}`;

export function drawCanvas2d(
  ctx: CanvasRenderingContext2D,
  scene: RenderScene,
  scale: number,
): void {
  const { width, height, gridSize } = scene;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = '#0c0a07';
  ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  ctx.fillStyle = '#1d1a14';
  ctx.fillRect(0, 0, width, height);

  ctx.beginPath();
  const xs = scene.showGrid ? gridLines(width, gridSize) : [];
  const ys = scene.showGrid ? gridLines(height, gridSize) : [];
  for (const x of xs) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
  }
  for (const y of ys) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.strokeStyle = 'rgba(201,163,78,0.12)';
  ctx.lineWidth = 1 / scale;
  ctx.stroke();

  ctx.beginPath();
  for (const w of scene.walls) {
    if (w.kind !== 'wall') continue;
    ctx.moveTo(w.x1, w.y1);
    ctx.lineTo(w.x2, w.y2);
  }
  ctx.strokeStyle = 'rgba(237,228,207,0.85)';
  ctx.lineWidth = 4;
  ctx.lineCap = 'round';
  ctx.stroke();
  ctx.beginPath();
  for (const w of scene.walls) {
    if (w.kind !== 'door') continue;
    ctx.moveTo(w.x1, w.y1);
    ctx.lineTo(w.x2, w.y2);
  }
  ctx.strokeStyle = '#c9a34e';
  ctx.lineWidth = 6;
  ctx.lineCap = 'butt';
  ctx.stroke();

  ctx.globalCompositeOperation = 'lighter';
  for (const l of scene.lights) {
    ctx.fillStyle = 'rgba(255,217,160,0.06)';
    ctx.beginPath();
    ctx.arc(l.x, l.y, l.radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = 'rgba(255,217,160,0.08)';
    ctx.beginPath();
    ctx.arc(l.x, l.y, l.radius * 0.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalCompositeOperation = 'source-over';

  ctx.font = `600 13px ${uiFont()}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const t of scene.tokens) {
    const r = tokenRadius(t.size, gridSize);
    ctx.beginPath();
    ctx.arc(t.x, t.y, r, 0, Math.PI * 2);
    ctx.fillStyle = hex(t.color);
    ctx.globalAlpha = 0.9;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#0c0a07';
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#f0ead7';
    ctx.fillText(t.label, t.x, t.y + r + 4);
  }
}
