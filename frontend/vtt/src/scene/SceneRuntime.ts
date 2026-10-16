import { Application, Container, Graphics, Text, type WebGLRenderer } from 'pixi.js';
import { layersFor, type LayerName, type Surface } from './layers';
import { RenderScheduler } from './renderScheduler';
import {
  gridLines,
  normalizeScene,
  tokenRadius,
  type RenderScene,
  type RenderToken,
} from './renderModel';
import {
  DEFAULT_ZOOM_LIMITS,
  FIT_ZOOM_LIMITS,
  fitToScreen,
  interactiveZoomLimits,
  panBy,
  screenToWorld,
  zoomAt,
  type Viewport,
  type ZoomLimits,
} from './viewport';
import { hitTest, snapTokenCentre, type Hit, type Point } from './interaction';
import { CANVAS_LABEL_FONT, ensureCanvasFont, uiFont } from '../ui/fonts';
import type { RendererStatus } from '../state/connection';

// Owns the Pixi application for one map host. React mounts a <div>, hands it
// here, and never touches the display objects; this class never touches React.
// Input arrives from pointer/keyboard handlers attached here; durable intents
// will leave through a callback once the transport exists (VTT-ADR-0002).

// GM map input (M4b). The runtime decides what was hit and whether a gesture
// was a click or a drag; the workspace decides what that means and issues the
// command. Nothing here talks to the transport.
export interface RuntimeInteraction {
  onClick?(point: Point, hit: Hit | null): void;
  // A token dragged and released: its snapped centre. The token stays where it
  // was dropped until the projection (or an overlay) says otherwise.
  onTokenDrop?(id: string, x: number, y: number): void;
  canDragToken?(id: string): boolean;
  onPointerMove?(point: Point): void;
  // Return true when the key was handled (the runtime then does not pan).
  onKey?(event: KeyboardEvent): boolean;
}

export interface SceneRuntimeOptions {
  surface: Surface;
  interaction?: RuntimeInteraction;
  onRendererStatus?: (status: RendererStatus) => void;
  onViewportChange?: (viewport: Viewport) => void;
  // How long to wait for the browser to restore a lost WebGL context before
  // rebuilding the renderer on a fresh canvas.
  contextRestoreTimeoutMs?: number;
}

interface TokenView {
  model: RenderToken;
  root: Container;
}

const BG_COLOR = 0x0c0a07;
const SCENE_COLOR = 0x1d1a14;
const GRID_COLOR = 0xc9a34e;
const WALL_COLOR = 0xede4cf;
const DOOR_COLOR = 0xc9a34e;
const KEY_PAN_PX = 64;
const DRAG_THRESHOLD_PX = 4;
const SELECT_COLOR = 0xede4cf;
const KEY_ZOOM = 1.2;

export class SceneRuntime {
  private app: Application | null = null;
  private layers = new Map<LayerName, Container>();
  private world = new Container({ label: 'world' });
  private scene: RenderScene | null = null;
  private tokens = new Map<string, TokenView>();
  private viewport: Viewport = { x: 0, y: 0, scale: 1 };
  private userMovedViewport = false;
  private readonly scheduler: RenderScheduler;
  private resizeObserver: ResizeObserver | null = null;
  private restoreTimer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;
  private rendererStatus: RendererStatus = 'ok';
  private drag: {
    pointerId: number;
    lastX: number;
    lastY: number;
    startX: number;
    startY: number;
    moved: boolean;
    token: { id: string; dx: number; dy: number } | null;
    hit: Hit | null;
  } | null = null;
  private selection: Hit | null = null;
  private wallDraft: { a: Point; b: Point } | null = null;
  private lastFrameMs = 0;

  constructor(
    private readonly host: HTMLElement,
    private readonly options: SceneRuntimeOptions,
  ) {
    this.scheduler = new RenderScheduler(this.renderFrame);
  }

  get surface(): Surface {
    return this.options.surface;
  }

  get lastFrameDurationMs(): number {
    return this.lastFrameMs;
  }

  async init(): Promise<void> {
    await ensureCanvasFont();
    if (this.destroyed) return;
    await this.createApplication();
    if (this.destroyed) return;
    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.host);
    if (this.options.surface === 'gm') this.attachInput();
  }

  setScene(input: RenderScene): void {
    const next = normalizeScene(input);
    const prev = this.scene;
    this.scene = next;
    if (!this.app) return; // applied in full once init() completes
    const geometryChanged =
      !prev ||
      prev.width !== next.width ||
      prev.height !== next.height ||
      prev.gridSize !== next.gridSize ||
      prev.showGrid !== next.showGrid;
    if (geometryChanged) {
      // Token radius is baked from the grid size, so new geometry rebuilds them.
      if (prev && prev.gridSize !== next.gridSize) this.clearTokens();
      this.drawBackground(next);
      this.drawGrid(next);
      if (!this.userMovedViewport) this.fit();
    }
    if (!prev || prev.walls !== next.walls) this.drawWalls(next);
    if (!prev || prev.lights !== next.lights) this.drawLights(next);
    this.syncTokens(next);
    this.drawOverlay();
    this.scheduler.requestRender();
  }

  // GM only: what is selected, drawn on the authoring overlay.
  setSelection(hit: Hit | null): void {
    this.selection = hit;
    this.drawOverlay();
    this.scheduler.requestRender();
  }

  // GM only: the wall being drawn (from its first point to the pointer).
  setWallDraft(draft: { a: Point; b: Point } | null): void {
    this.wallDraft = draft;
    this.drawOverlay();
    this.scheduler.requestRender();
  }

  get currentViewport(): Viewport {
    return this.viewport;
  }

  fit(): void {
    if (!this.scene) return;
    // A hidden or collapsed host has no size; fitting into 0x0 would set the
    // minimum zoom. The ResizeObserver fits again once the host has a size.
    if (this.host.clientWidth === 0 || this.host.clientHeight === 0) return;
    this.applyViewport(
      fitToScreen(this.sceneSize(), this.screenSize(), this.fitPadding(), FIT_ZOOM_LIMITS),
    );
  }

  // One padding value for both fitting and the zoom-out floor.
  private fitPadding(): number {
    return this.options.surface === 'table' ? 0 : 24;
  }

  private sceneSize() {
    return { width: this.scene?.width ?? 1, height: this.scene?.height ?? 1 };
  }

  private screenSize() {
    return { width: this.host.clientWidth, height: this.host.clientHeight };
  }

  private zoomLimits(): ZoomLimits {
    if (!this.scene) return DEFAULT_ZOOM_LIMITS;
    return interactiveZoomLimits(this.sceneSize(), this.screenSize(), this.fitPadding());
  }

  // Forces one frame synchronously. requestAnimationFrame does not fire in a
  // hidden tab or a headless preview pane, so tests and the benchmark call this.
  renderNow(): number {
    this.renderFrame(performance.now());
    return this.lastFrameMs;
  }

  // Benchmark helper: block until the GPU has finished queued work, so a
  // timing includes it. Never call this on a live frame path.
  finishGpuForBench(): void {
    (this.app?.renderer as WebGLRenderer | undefined)?.gl?.finish();
  }

  // Development hook: simulate a GPU reset to exercise recovery.
  loseContextForTest(restoreAfterMs: number | null): boolean {
    const renderer = this.app?.renderer as WebGLRenderer | undefined;
    const gl = renderer?.gl;
    const ext = gl?.getExtension('WEBGL_lose_context');
    if (!ext) return false;
    ext.loseContext();
    if (restoreAfterMs !== null) setTimeout(() => ext.restoreContext(), restoreAfterMs);
    return true;
  }

  destroy(): void {
    this.destroyed = true;
    this.scheduler.dispose();
    this.resizeObserver?.disconnect();
    if (this.restoreTimer) clearTimeout(this.restoreTimer);
    this.teardownApplication();
  }

  // -- application lifecycle ------------------------------------------------

  private async createApplication(): Promise<void> {
    const app = new Application();
    await app.init({
      width: Math.max(1, this.host.clientWidth),
      height: Math.max(1, this.host.clientHeight),
      background: BG_COLOR,
      antialias: true,
      autoStart: false,
      sharedTicker: false,
      preference: 'webgl',
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    });
    if (this.destroyed) {
      app.destroy(true, { children: true });
      return;
    }
    this.app = app;
    app.ticker.stop();
    const canvas = app.canvas;
    canvas.classList.add('vtt-canvas');
    canvas.addEventListener('webglcontextlost', this.handleContextLost);
    canvas.addEventListener('webglcontextrestored', this.handleContextRestored);
    this.host.appendChild(canvas);

    this.world = new Container({ label: 'world' });
    this.layers.clear();
    for (const name of layersFor(this.options.surface)) {
      const layer = new Container({ label: name });
      this.layers.set(name, layer);
      this.world.addChild(layer);
    }
    app.stage.addChild(this.world);
    this.tokens.clear();
    const scene = this.scene;
    this.scene = null;
    if (scene) this.setScene(scene);
    this.applyViewport(this.viewport);
  }

  private teardownApplication(): void {
    const app = this.app;
    this.app = null;
    if (!app) return;
    const canvas = app.canvas;
    canvas.removeEventListener('webglcontextlost', this.handleContextLost);
    canvas.removeEventListener('webglcontextrestored', this.handleContextRestored);
    this.detachInput(canvas);
    app.destroy(true, { children: true });
    this.tokens.clear();
    this.layers.clear();
  }

  private setRendererStatus(status: RendererStatus): void {
    if (this.rendererStatus === status) return;
    this.rendererStatus = status;
    this.options.onRendererStatus?.(status);
  }

  private readonly handleContextLost = (event: Event): void => {
    // preventDefault tells the browser we intend to restore; Pixi does the same.
    event.preventDefault();
    this.setRendererStatus('lost');
    if (this.restoreTimer) clearTimeout(this.restoreTimer);
    const timeout = this.options.contextRestoreTimeoutMs ?? 3000;
    this.restoreTimer = setTimeout(() => void this.rebuild(), timeout);
  };

  private readonly handleContextRestored = (): void => {
    if (this.restoreTimer) clearTimeout(this.restoreTimer);
    this.restoreTimer = null;
    // Pixi re-initialises its GL state on restore; the scene graph is intact,
    // so one frame is enough.
    this.setRendererStatus('ok');
    this.scheduler.requestRender();
  };

  // Fallback when the browser never restores the context: throw the canvas
  // away and rebuild everything from the last scene. Store state is untouched,
  // so this is a pure re-render.
  private async rebuild(): Promise<void> {
    this.restoreTimer = null;
    if (this.destroyed) return;
    this.setRendererStatus('restoring');
    this.teardownApplication();
    try {
      await this.createApplication();
      if (this.destroyed) return;
      if (this.options.surface === 'gm') this.attachInput();
      this.setRendererStatus('ok');
      this.scheduler.requestRender();
    } catch {
      this.setRendererStatus('failed');
    }
  }

  private handleResize(): void {
    const app = this.app;
    if (!app) return;
    app.renderer.resize(Math.max(1, this.host.clientWidth), Math.max(1, this.host.clientHeight));
    // The table always shows the whole scene; the GM keeps their framing once
    // they have panned or zoomed.
    if (this.options.surface === 'table' || !this.userMovedViewport) this.fit();
    this.scheduler.requestRender();
  }

  private readonly renderFrame = (_timeMs: number): void => {
    const app = this.app;
    if (!app || this.rendererStatus !== 'ok') return;
    const start = performance.now();
    app.render();
    this.lastFrameMs = performance.now() - start;
  };

  // -- viewport and input ---------------------------------------------------

  private applyViewport(v: Viewport): void {
    const rescaled = v.scale !== this.viewport.scale;
    this.viewport = v;
    this.world.position.set(v.x, v.y);
    this.world.scale.set(v.scale);
    // Overlay strokes are sized in screen pixels, so a zoom redraws them.
    if (rescaled) this.drawOverlay();
    this.options.onViewportChange?.(v);
    this.scheduler.requestRender();
  }

  private attachInput(): void {
    const canvas = this.app?.canvas;
    if (!canvas) return;
    canvas.addEventListener('pointerdown', this.onPointerDown);
    canvas.addEventListener('pointermove', this.onPointerMove);
    canvas.addEventListener('pointerup', this.onPointerUp);
    canvas.addEventListener('pointercancel', this.onPointerUp);
    canvas.addEventListener('lostpointercapture', this.onPointerUp);
    canvas.addEventListener('wheel', this.onWheel, { passive: false });
    this.host.addEventListener('keydown', this.onKeyDown);
  }

  private detachInput(canvas: HTMLCanvasElement): void {
    canvas.removeEventListener('pointerdown', this.onPointerDown);
    canvas.removeEventListener('pointermove', this.onPointerMove);
    canvas.removeEventListener('pointerup', this.onPointerUp);
    canvas.removeEventListener('pointercancel', this.onPointerUp);
    canvas.removeEventListener('lostpointercapture', this.onPointerUp);
    canvas.removeEventListener('wheel', this.onWheel);
    this.host.removeEventListener('keydown', this.onKeyDown);
  }

  private worldPoint(e: { clientX: number; clientY: number }): Point {
    const rect = this.host.getBoundingClientRect();
    return screenToWorld(this.viewport, { x: e.clientX - rect.left, y: e.clientY - rect.top });
  }

  private readonly onPointerDown = (e: PointerEvent): void => {
    if (e.button !== 0 && e.button !== 1) return;
    // Middle-button pan must not also start the browser's autoscroll.
    if (e.button === 1) e.preventDefault();
    const interaction = this.options.interaction;
    let hit: Hit | null = null;
    let token: { id: string; dx: number; dy: number } | null = null;
    if (interaction && e.button === 0 && this.scene) {
      const p = this.worldPoint(e);
      hit = hitTest(this.scene, p, this.viewport.scale);
      if (hit?.kind === 'token' && interaction.canDragToken?.(hit.id) !== false) {
        const view = this.tokens.get(hit.id);
        if (view) token = { id: hit.id, dx: p.x - view.root.x, dy: p.y - view.root.y };
      }
    }
    this.drag = {
      pointerId: e.pointerId,
      lastX: e.clientX,
      lastY: e.clientY,
      startX: e.clientX,
      startY: e.clientY,
      moved: false,
      token,
      hit,
    };
    (e.target as Element).setPointerCapture?.(e.pointerId);
    this.host.focus({ preventScroll: true });
  };

  private readonly onPointerMove = (e: PointerEvent): void => {
    const drag = this.drag;
    if (!drag || drag.pointerId !== e.pointerId) {
      if (this.options.interaction?.onPointerMove) this.options.interaction.onPointerMove(this.worldPoint(e));
      return;
    }
    if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > DRAG_THRESHOLD_PX) drag.moved = true;
    const dx = e.clientX - drag.lastX;
    const dy = e.clientY - drag.lastY;
    drag.lastX = e.clientX;
    drag.lastY = e.clientY;
    if (drag.token) {
      if (!drag.moved) return;
      const view = this.tokens.get(drag.token.id);
      const p = this.worldPoint(e);
      if (view) {
        view.root.position.set(p.x - drag.token.dx, p.y - drag.token.dy);
        this.drawOverlay();
        this.scheduler.requestRender();
      }
      return;
    }
    if (!drag.moved) return;
    this.userMovedViewport = true;
    this.applyViewport(panBy(this.viewport, dx, dy));
  };

  private readonly onPointerUp = (e: PointerEvent): void => {
    const drag = this.drag;
    if (drag?.pointerId !== e.pointerId) return;
    this.drag = null;
    if (e.type !== 'pointerup') {
      // Cancelled: a dragged token goes back to where the projection has it.
      if (drag.token && this.scene) this.syncTokens(this.scene, true);
      return;
    }
    const interaction = this.options.interaction;
    if (!interaction) return;
    if (drag.token && drag.moved && this.scene) {
      const view = this.tokens.get(drag.token.id);
      if (!view) return;
      const snapped = snapTokenCentre({ x: view.root.x, y: view.root.y }, view.model.size, this.scene);
      view.root.position.set(snapped.x, snapped.y);
      this.drawOverlay();
      this.scheduler.requestRender();
      interaction.onTokenDrop?.(drag.token.id, snapped.x, snapped.y);
      return;
    }
    if (!drag.moved) interaction.onClick?.(this.worldPoint(e), drag.hit);
  };

  private readonly onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    const rect = this.host.getBoundingClientRect();
    const factor = Math.exp(-e.deltaY * 0.0015);
    this.userMovedViewport = true;
    this.applyViewport(
      zoomAt(
        this.viewport,
        { x: e.clientX - rect.left, y: e.clientY - rect.top },
        factor,
        this.zoomLimits(),
      ),
    );
  };

  private readonly onKeyDown = (e: KeyboardEvent): void => {
    if (this.options.interaction?.onKey?.(e)) {
      e.preventDefault();
      return;
    }
    const centre = { x: this.host.clientWidth / 2, y: this.host.clientHeight / 2 };
    let next: Viewport | null = null;
    switch (e.key) {
      case 'ArrowLeft':
        next = panBy(this.viewport, KEY_PAN_PX, 0);
        break;
      case 'ArrowRight':
        next = panBy(this.viewport, -KEY_PAN_PX, 0);
        break;
      case 'ArrowUp':
        next = panBy(this.viewport, 0, KEY_PAN_PX);
        break;
      case 'ArrowDown':
        next = panBy(this.viewport, 0, -KEY_PAN_PX);
        break;
      case '+':
      case '=':
        next = zoomAt(this.viewport, centre, KEY_ZOOM, this.zoomLimits());
        break;
      case '-':
        next = zoomAt(this.viewport, centre, 1 / KEY_ZOOM, this.zoomLimits());
        break;
      case '0':
        this.userMovedViewport = false;
        this.fit();
        e.preventDefault();
        return;
      default:
        return;
    }
    e.preventDefault();
    this.userMovedViewport = true;
    this.applyViewport(next);
  };

  // -- layer drawing --------------------------------------------------------

  private layer(name: LayerName): Container | null {
    return this.layers.get(name) ?? null;
  }

  private replaceLayerContent(name: LayerName, child: Container | null): void {
    const layer = this.layer(name);
    if (!layer) return;
    for (const old of layer.removeChildren()) old.destroy({ children: true });
    if (child) layer.addChild(child);
  }

  private drawBackground(scene: RenderScene): void {
    const g = new Graphics();
    g.rect(0, 0, scene.width, scene.height).fill(SCENE_COLOR);
    this.replaceLayerContent('background', g);
  }

  private drawGrid(scene: RenderScene): void {
    if (!scene.showGrid) {
      this.replaceLayerContent('grid', null);
      return;
    }
    // gridLines is bounded and returns nothing for an invalid step, so bad
    // persisted or imported geometry cannot spin this loop.
    const g = new Graphics();
    for (const x of gridLines(scene.width, scene.gridSize)) g.moveTo(x, 0).lineTo(x, scene.height);
    for (const y of gridLines(scene.height, scene.gridSize)) g.moveTo(0, y).lineTo(scene.width, y);
    g.stroke({ width: 1, color: GRID_COLOR, alpha: 0.12 });
    this.replaceLayerContent('grid', g);
  }

  private drawWalls(scene: RenderScene): void {
    if (!this.layer('walls')) return;
    const g = new Graphics();
    for (const w of scene.walls) {
      if (w.kind === 'wall') g.moveTo(w.x1, w.y1).lineTo(w.x2, w.y2);
    }
    g.stroke({ width: 4, color: WALL_COLOR, alpha: 0.85, cap: 'round' });
    for (const w of scene.walls) {
      if (w.kind === 'door') g.moveTo(w.x1, w.y1).lineTo(w.x2, w.y2);
    }
    g.stroke({ width: 6, color: DOOR_COLOR, alpha: 0.95, cap: 'butt' });
    this.replaceLayerContent('walls', g);
  }

  private drawLights(scene: RenderScene): void {
    const g = new Graphics();
    for (const l of scene.lights) {
      g.circle(l.x, l.y, l.radius).fill({ color: l.color, alpha: 0.06 });
      g.circle(l.x, l.y, l.radius * 0.5).fill({ color: l.color, alpha: 0.08 });
    }
    g.blendMode = 'add';
    this.replaceLayerContent('lighting', g);
  }

  // The authoring overlay (GM only; the table never builds this layer): light
  // handles so lights can be picked, the selection, and a wall being drawn.
  private drawOverlay(): void {
    if (!this.layer('overlay') || !this.scene) return;
    const g = new Graphics();
    const s = this.viewport.scale > 0 ? this.viewport.scale : 1;
    for (const l of this.scene.lights) {
      g.circle(l.x, l.y, 6 / s).fill({ color: l.color, alpha: l.hidden ? 0.4 : 0.9 });
      g.circle(l.x, l.y, 6 / s).stroke({ width: 1.5 / s, color: 0x0c0a07, alpha: 0.9 });
    }
    const sel = this.selection;
    if (sel?.kind === 'token') {
      const view = this.tokens.get(sel.id);
      if (view) {
        const r = tokenRadius(view.model.size, this.scene.gridSize) + 6;
        g.circle(view.root.x, view.root.y, r).stroke({ width: 3 / s, color: SELECT_COLOR, alpha: 0.95 });
      }
    } else if (sel?.kind === 'light') {
      const l = this.scene.lights.find((x) => x.id === sel.id);
      if (l) {
        g.circle(l.x, l.y, 11 / s).stroke({ width: 2.5 / s, color: SELECT_COLOR, alpha: 0.95 });
        g.circle(l.x, l.y, l.radius).stroke({ width: 1.5 / s, color: SELECT_COLOR, alpha: 0.5 });
      }
    } else if (sel?.kind === 'wall') {
      const w = this.scene.walls.find((x) => x.id === sel.id);
      if (w) g.moveTo(w.x1, w.y1).lineTo(w.x2, w.y2).stroke({ width: 10 / s, color: SELECT_COLOR, alpha: 0.35 });
    }
    if (this.wallDraft) {
      const { a, b } = this.wallDraft;
      g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ width: 3 / s, color: WALL_COLOR, alpha: 0.6 });
      g.circle(a.x, a.y, 4 / s).fill({ color: WALL_COLOR, alpha: 0.9 });
    }
    this.replaceLayerContent('overlay', g);
  }

  private clearTokens(): void {
    for (const view of this.tokens.values()) {
      view.root.parent?.removeChild(view.root);
      view.root.destroy({ children: true });
    }
    this.tokens.clear();
  }

  private syncTokens(scene: RenderScene, resetPositions = false): void {
    const layer = this.layer('tokens');
    if (!layer) return;
    const seen = new Set<string>();
    for (const model of scene.tokens) {
      seen.add(model.id);
      const view = this.tokens.get(model.id);
      // A token mid-drag keeps following the pointer whatever arrives.
      if (view && this.drag?.token?.id === model.id && !resetPositions) {
        view.model = model;
        continue;
      }
      if (view && view.model === model) {
        if (resetPositions) view.root.position.set(model.x, model.y);
        continue; // unchanged by identity
      }
      if (view && sameAppearance(view.model, model)) {
        view.root.position.set(model.x, model.y);
        view.model = model;
        continue;
      }
      if (view) {
        layer.removeChild(view.root);
        view.root.destroy({ children: true });
      }
      const root = buildToken(model, scene.gridSize);
      layer.addChild(root);
      this.tokens.set(model.id, { model, root });
    }
    for (const [id, view] of this.tokens) {
      if (seen.has(id)) continue;
      layer.removeChild(view.root);
      view.root.destroy({ children: true });
      this.tokens.delete(id);
    }
  }
}

// Only fields baked into the display objects. Grid size is handled by
// setScene, which clears every token when it changes.
function sameAppearance(a: RenderToken, b: RenderToken): boolean {
  return (
    a.size === b.size &&
    a.label === b.label &&
    a.color === b.color &&
    a.hidden === b.hidden &&
    a.pending === b.pending
  );
}

function buildToken(model: RenderToken, gridSize: number): Container {
  const root = new Container({ label: `token:${model.id}` });
  const radius = tokenRadius(model.size, gridSize);
  const body = new Graphics();
  body.circle(0, 0, radius).fill({ color: model.color, alpha: 0.9 });
  body.circle(0, 0, radius).stroke({ width: 3, color: 0x0c0a07, alpha: 0.9 });
  // A pending command: a dashed-looking outer ring until its frame arrives.
  if (model.pending) body.circle(0, 0, radius + 5).stroke({ width: 2, color: 0xede4cf, alpha: 0.7 });
  root.addChild(body);
  // GM only: what the table cannot see is drawn dimmed, never hidden.
  if (model.hidden) root.alpha = 0.45;
  const label = new Text({
    text: model.label,
    style: {
      fontFamily: uiFont(),
      fontSize: CANVAS_LABEL_FONT.sizePx,
      fontWeight: CANVAS_LABEL_FONT.weight,
      fill: 0xf0ead7,
    },
  });
  label.anchor.set(0.5, 0);
  label.position.set(0, radius + 4);
  root.addChild(label);
  root.position.set(model.x, model.y);
  return root;
}
