import { Application, Container, Graphics, Sprite, Text, Texture, type WebGLRenderer } from 'pixi.js';
import { layersFor, type LayerName, type Surface } from './layers';
import { RenderScheduler } from './renderScheduler';
import {
  gridLines,
  normalizeScene,
  sameLive,
  tokenRadius,
  type RenderLive,
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
import { templateOutline } from './templates';
import { TextureLoader } from './textures';
import { WALL_STYLES, wallPlan } from './walls';
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
  // `alt`: the Alt key was held (terrain paints one square instead of a room).
  onClick?(point: Point, hit: Hit | null, modifiers?: { alt: boolean }): void;
  // A token dragged and released: its snapped centre. The token stays where it
  // was dropped until the projection (or an overlay) says otherwise.
  onTokenDrop?(id: string, x: number, y: number): void;
  canDragToken?(id: string): boolean;
  // A template dragged and released (parity P2): how far it moved, in whole
  // grid squares expressed in pixels, so its anchor keeps its alignment.
  onTemplateDrop?(id: string, dx: number, dy: number): void;
  canDragTemplate?(id: string): boolean;
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
// Terrain kinds 1-4 (lava, water, poison, blood) as RGBA, about half opaque.
const TERRAIN_COLORS: Record<number, [number, number, number, number]> = {
  1: [214, 78, 26, 150],
  2: [46, 120, 196, 140],
  3: [104, 168, 44, 140],
  4: [138, 18, 28, 150],
};
const SCENE_COLOR = 0x1d1a14;
const GRID_COLOR = 0xc9a34e;
const WALL_COLOR = 0xede4cf;
const KEY_PAN_PX = 64;
const DRAG_THRESHOLD_PX = 4;
const TEMPLATE_SHIFT_HOLD_MS = 2000;
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
    template: { id: string; start: Point } | null;
    hit: Hit | null;
  } | null = null;
  // The template being dragged and its snapped offset, drawn shifted until the
  // projection (or an overlay) catches up.
  private templateShift: { id: string; dx: number; dy: number } | null = null;
  // Templates have no optimistic overlay, so after a drop the shift is held
  // until the projection changes the templates, or this fallback (a refused
  // or lost command changes nothing) puts it back.
  private templateShiftTimer: ReturnType<typeof setTimeout> | null = null;
  private selection: Hit | null = null;
  private wallDraft: { a: Point; b: Point } | null = null;
  private lastFrameMs = 0;
  // Backgrounds and token art (M6). Owned by the runtime, not the Pixi app, so
  // a renderer rebuild after a lost context keeps what is already loaded.
  // The fog mask (parity P1): a cols x rows alpha texture stretched over the
  // scene, rebuilt only when the fog itself changes (its key is the projected
  // version, which a token move does not touch: /map's revision trap avoided).
  private fogMask: { key: string; texture: Texture } | null = null;
  // Painted terrain (parity P2), the same one-pixel-per-cell approach.
  private terrainMask: { key: string; texture: Texture } | null = null;
  private readonly textures = new TextureLoader<Texture>(
    { toTexture: (bitmap) => Texture.from(bitmap), destroy: (texture) => texture.destroy(true) },
    (url) => this.textureLoaded(url),
  );

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
    else if (prev.backgroundUrl !== next.backgroundUrl || prev.terrain !== next.terrain) this.drawBackground(next);
    if (prev?.templates !== next.templates && !this.drag?.template) this.dropTemplateShift();
    if (geometryChanged || prev?.templates !== next.templates) this.drawTemplates(next);
    if (geometryChanged || prev?.fog !== next.fog) this.drawFog(next);
    if (!prev || prev.walls !== next.walls) this.drawWalls(next);
    if (!prev || prev.lights !== next.lights) this.drawLights(next);
    this.syncTokens(next);
    // Images the scene still uses keep retrying after a failure; others stop.
    this.textures.keep(sceneImageUrls(next));
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
    this.dropTemplateShift();
    this.scheduler.dispose();
    this.resizeObserver?.disconnect();
    if (this.restoreTimer) clearTimeout(this.restoreTimer);
    this.teardownApplication();
    this.textures.dispose();
    this.fogMask?.texture.destroy(true);
    this.fogMask = null;
    this.terrainMask?.texture.destroy(true);
    this.terrainMask = null;
  }

  // Test probe (M6 harness): what this runtime is drawing. Only data the page
  // already holds; MapHost exposes it when the probe flag is set.
  probe(): {
    surface: Surface;
    viewport: Viewport;
    scene: {
      width: number;
      height: number;
      gridSize: number;
      backgroundUrl: string | null;
      background: string;
      fog: { enabled: boolean; cols: number; rows: number; revealed: number } | null;
      terrainPainted: number;
    } | null;
    templates: Array<{ id: string; shape: string; x: number; y: number; size: number; hidden: boolean }>;
    tokens: Array<{
      id: string; x: number; y: number; label: string; hidden: boolean; artUrl: string | null; art: string;
      live: { hp: number | null; maxHp: number | null; status: string | null; conditions: readonly string[]; active: boolean } | null;
    }>;
  } {
    const scene = this.scene;
    return {
      surface: this.options.surface,
      viewport: { ...this.viewport },
      scene: scene
        ? {
            width: scene.width,
            height: scene.height,
            gridSize: scene.gridSize,
            backgroundUrl: scene.backgroundUrl ?? null,
            background: scene.backgroundUrl ? this.textures.state(scene.backgroundUrl) : 'none',
            fog: scene.fog
              ? {
                  enabled: scene.fog.enabled,
                  cols: scene.fog.cols,
                  rows: scene.fog.rows,
                  revealed: scene.fog.bits.reduce((n, byte) => {
                    let count = n;
                    for (let b = byte; b; b &= b - 1) count += 1;
                    return count;
                  }, 0),
                }
              : null,
            terrainPainted: scene.terrain
              ? Array.from({ length: scene.terrain.cols * scene.terrain.rows }).reduce<number>((n, _v, index) => {
                  const byte = scene.terrain?.cells[index >> 1] ?? 0;
                  return n + ((index & 1 ? byte >> 4 : byte & 0x0f) ? 1 : 0);
                }, 0)
              : 0,
          }
        : null,
      templates: (scene?.templates ?? []).map((t) => ({
        id: t.id, shape: t.shape, x: t.x, y: t.y, size: t.size, hidden: t.hidden === true,
      })),
      tokens: (scene?.tokens ?? []).map((t) => ({
        id: t.id,
        x: t.x,
        y: t.y,
        label: t.label,
        hidden: t.hidden === true,
        artUrl: t.artUrl ?? null,
        art: t.artUrl ? this.textures.state(t.artUrl) : 'none',
        live: t.live ? { ...t.live } : null,
      })),
    };
  }

  // An image arrived: redraw whatever shows it.
  private textureLoaded(url: string): void {
    const scene = this.scene;
    if (!this.app || !scene) return;
    if (scene.backgroundUrl === url) this.drawBackground(scene);
    const layer = this.layer('tokens');
    if (layer) {
      for (const [id, view] of this.tokens) {
        if (view.model.artUrl !== url) continue;
        const root = buildToken(view.model, scene.gridSize, this.textures.get(url));
        // A token mid-drag keeps where the pointer has it.
        if (this.drag?.token?.id === id) root.position.copyFrom(view.root.position);
        layer.addChildAt(root, layer.getChildIndex(view.root));
        layer.removeChild(view.root);
        view.root.destroy({ children: true });
        view.root = root;
      }
    }
    this.drawOverlay();
    this.scheduler.requestRender();
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
    let template: { id: string; start: Point } | null = null;
    if (interaction && e.button === 0 && this.scene) {
      const p = this.worldPoint(e);
      hit = hitTest(this.scene, p, this.viewport.scale);
      if (hit?.kind === 'token' && interaction.canDragToken?.(hit.id) !== false) {
        const view = this.tokens.get(hit.id);
        if (view) token = { id: hit.id, dx: p.x - view.root.x, dy: p.y - view.root.y };
      } else if (hit?.kind === 'template' && interaction.canDragTemplate?.(hit.id) === true) {
        template = { id: hit.id, start: p };
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
      template,
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
    if (drag.template) {
      if (!drag.moved || !this.scene) return;
      const p = this.worldPoint(e);
      const g = this.scene.gridSize;
      const dx = Math.round((p.x - drag.template.start.x) / g) * g;
      const dy = Math.round((p.y - drag.template.start.y) / g) * g;
      const prev = this.templateShift;
      if (!prev || prev.dx !== dx || prev.dy !== dy) {
        this.templateShift = { id: drag.template.id, dx, dy };
        this.drawTemplates(this.scene);
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
      // Rendering is event-driven, so the restored position (and the selection
      // ring that follows it) must be redrawn and a frame requested, or the
      // token stays painted where the drag left it (handoff 043).
      if (drag.token && this.scene) {
        this.syncTokens(this.scene, true);
        this.drawOverlay();
        this.scheduler.requestRender();
      }
      this.clearTemplateShift();
      return;
    }
    const interaction = this.options.interaction;
    if (!interaction) return;
    if (drag.template && drag.moved) {
      const shift = this.templateShift;
      // The shift stays drawn until the next projection replaces the scene.
      if (shift && (shift.dx !== 0 || shift.dy !== 0)) {
        if (this.templateShiftTimer) clearTimeout(this.templateShiftTimer);
        this.templateShiftTimer = setTimeout(() => this.clearTemplateShift(), TEMPLATE_SHIFT_HOLD_MS);
        interaction.onTemplateDrop?.(shift.id, shift.dx, shift.dy);
      } else this.clearTemplateShift();
      return;
    }
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
    if (!drag.moved) interaction.onClick?.(this.worldPoint(e), drag.hit, { alt: e.altKey });
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
    const root = new Container({ label: 'scene-background' });
    const g = new Graphics();
    g.rect(0, 0, scene.width, scene.height).fill(SCENE_COLOR);
    root.addChild(g);
    // The image is stretched to the scene, as /map does; until it loads (or if
    // it never does) the plain scene colour shows.
    const texture = this.textures.get(scene.backgroundUrl);
    if (texture) {
      const image = new Sprite(texture);
      image.width = scene.width;
      image.height = scene.height;
      root.addChild(image);
    }
    // Terrain over the image and under the grid, as /map draws it.
    const terrain = this.terrainSprite(scene);
    if (terrain) root.addChild(terrain);
    const layer = this.layer('background');
    if (!layer) return;
    // The shared terrain texture belongs to this runtime: keep it on replace.
    for (const old of layer.removeChildren()) old.destroy({ children: true, texture: false });
    layer.addChild(root);
  }

  private terrainSprite(scene: RenderScene): Sprite | null {
    const terrain = scene.terrain;
    if (!terrain || !terrain.cells.some((b) => b !== 0)) return null;
    if (this.terrainMask?.key !== terrain.key) {
      const canvas = document.createElement('canvas');
      canvas.width = terrain.cols;
      canvas.height = terrain.rows;
      const context = canvas.getContext('2d');
      if (!context) return null;
      const image = context.createImageData(terrain.cols, terrain.rows);
      for (let index = 0; index < terrain.cols * terrain.rows; index += 1) {
        const byte = terrain.cells[index >> 1] ?? 0;
        const kind = index & 1 ? byte >> 4 : byte & 0x0f;
        const colour = TERRAIN_COLORS[kind];
        if (!colour) continue;
        const at = index * 4;
        image.data[at] = colour[0];
        image.data[at + 1] = colour[1];
        image.data[at + 2] = colour[2];
        image.data[at + 3] = colour[3];
      }
      context.putImageData(image, 0, 0);
      this.terrainMask?.texture.destroy(true);
      const texture = Texture.from(canvas);
      texture.source.scaleMode = 'linear';
      this.terrainMask = { key: terrain.key, texture };
    }
    const sprite = new Sprite(this.terrainMask.texture);
    sprite.width = terrain.cols * scene.gridSize;
    sprite.height = terrain.rows * scene.gridSize;
    return sprite;
  }

  // Area templates (parity P2): a translucent fill and a stroke in their
  // colour; hidden ones dimmed for the GM, never drawn on the table.
  // Forget the shift without drawing (the caller redraws).
  private dropTemplateShift(): void {
    if (this.templateShiftTimer) clearTimeout(this.templateShiftTimer);
    this.templateShiftTimer = null;
    this.templateShift = null;
  }

  private clearTemplateShift(): void {
    const had = this.templateShift !== null;
    this.dropTemplateShift();
    if (!had || this.destroyed || !this.app) return;
    if (this.scene) this.drawTemplates(this.scene);
    this.scheduler.requestRender();
  }

  private drawTemplates(scene: RenderScene): void {
    const shift = this.templateShift;
    const templates = (scene.templates ?? []).map((t) =>
      shift && t.id === shift.id ? { ...t, x: t.x + shift.dx, y: t.y + shift.dy, aimX: t.aimX + shift.dx, aimY: t.aimY + shift.dy } : t,
    );
    if (templates.length === 0) {
      this.replaceLayerContent('templates', null);
      return;
    }
    const g = new Graphics();
    for (const t of templates) {
      const outline = templateOutline(t, scene.gridSize);
      const alpha = t.hidden ? 0.45 : 1;
      if (outline.kind === 'circle') g.circle(outline.x, outline.y, outline.r);
      else g.poly(outline.points.flatMap((p) => [p.x, p.y]));
      g.fill({ color: t.color, alpha: 0.2 * alpha }).stroke({ width: 3, color: t.color, alpha: 0.9 * alpha });
    }
    this.replaceLayerContent('templates', g);
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
    // One stroke per door state (walls.ts): open, closed, locked and secret
    // doors each look different, so a change in the inspector shows on the map.
    for (const { state, segments } of wallPlan(scene.walls)) {
      for (const [x1, y1, x2, y2] of segments) g.moveTo(x1, y1).lineTo(x2, y2);
      g.stroke(WALL_STYLES[state]);
    }
    this.replaceLayerContent('walls', g);
  }

  // Unrevealed cells dark: near-opaque on the table, a haze on the GM page so
  // the GM still sees what is under it. One pixel per cell, upscaled with
  // linear filtering, which softens the stepped edge as /map's blur did.
  private drawFog(scene: RenderScene): void {
    const fog = scene.fog;
    if (!fog || !fog.enabled) {
      this.replaceLayerContent('fog', null);
      return;
    }
    const key = `${fog.key}:${this.options.surface}`;
    if (this.fogMask?.key !== key) {
      const canvas = document.createElement('canvas');
      canvas.width = fog.cols;
      canvas.height = fog.rows;
      const context = canvas.getContext('2d');
      if (!context) return;
      const image = context.createImageData(fog.cols, fog.rows);
      const alpha = this.options.surface === 'table' ? 247 : 77;
      for (let index = 0; index < fog.cols * fog.rows; index += 1) {
        const lit = ((fog.bits[index >> 3] ?? 0) >> (index & 7)) & 1;
        const at = index * 4;
        image.data[at] = 12;
        image.data[at + 1] = 10;
        image.data[at + 2] = 7;
        image.data[at + 3] = lit ? 0 : alpha;
      }
      context.putImageData(image, 0, 0);
      this.fogMask?.texture.destroy(true);
      const texture = Texture.from(canvas);
      texture.source.scaleMode = 'linear';
      this.fogMask = { key, texture };
    }
    const sprite = new Sprite(this.fogMask.texture);
    sprite.width = fog.cols * scene.gridSize;
    sprite.height = fog.rows * scene.gridSize;
    const root = new Container({ label: 'fog' });
    root.addChild(sprite);
    // replaceLayerContent destroys the previous sprite but not the shared
    // texture, which this runtime owns.
    const layer = this.layer('fog');
    if (!layer) return;
    for (const old of layer.removeChildren()) old.destroy({ children: true, texture: false });
    layer.addChild(root);
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
      const root = buildToken(model, scene.gridSize, this.textures.get(model.artUrl));
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
    a.artUrl === b.artUrl &&
    a.hidden === b.hidden &&
    a.pending === b.pending &&
    sameLive(a.live, b.live)
  );
}

const HP_BAR_HEIGHT = 5;
const ACTIVE_RING = 0xc9a34e;
const STATUS_COLORS = { Wounded: 0xe0913a, Dead: 0xd2564a } as const;
const MAX_CONDITION_LABELS = 4;

function hpColor(ratio: number): number {
  if (ratio > 0.5) return 0x5c9a3a;
  if (ratio > 0.25) return 0xc9a34e;
  return 0xb8433a;
}

function smallText(text: string, fill: number): Text {
  return new Text({
    text,
    style: { fontFamily: uiFont(), fontSize: CANVAS_LABEL_FONT.sizePx - 2, fontWeight: CANVAS_LABEL_FONT.weight, fill },
  });
}

// Tracker state around a token (parity P3): an outline on whose turn it is, an
// HP bar under it when the projection carries numbers, the status the table
// may see instead, and the conditions above it. Returns where the label goes.
function drawLive(root: Container, live: RenderLive, radius: number): { labelY: number; below: Text[] } {
  if (live.active) {
    root.addChild(new Graphics().circle(0, 0, radius + 5).stroke({ width: 3, color: ACTIVE_RING, alpha: 0.95 }));
  }
  let labelY = radius + 4;
  const below: Text[] = [];
  if (live.hp !== null && live.maxHp !== null && live.maxHp > 0) {
    const width = Math.max(24, radius * 1.8);
    const ratio = Math.max(0, Math.min(1, live.hp / live.maxHp));
    const bar = new Graphics();
    bar.rect(-width / 2, radius + 3, width, HP_BAR_HEIGHT).fill({ color: 0x0c0a07, alpha: 0.85 });
    if (ratio > 0) bar.rect(-width / 2, radius + 3, width * ratio, HP_BAR_HEIGHT).fill({ color: hpColor(ratio) });
    root.addChild(bar);
    labelY = radius + 3 + HP_BAR_HEIGHT + 3;
    below.push(smallText(`${live.hp} / ${live.maxHp}`, 0xd8d0bb));
  } else if (live.status) {
    below.push(smallText(live.status, STATUS_COLORS[live.status]));
  }
  if (live.conditions.length) {
    const shown = live.conditions.slice(0, MAX_CONDITION_LABELS);
    const more = live.conditions.length - shown.length;
    const text = smallText(shown.join(', ') + (more > 0 ? ` +${more}` : ''), 0xe8c27a);
    text.anchor.set(0.5, 1);
    text.position.set(0, -radius - (live.active ? 9 : 4));
    root.addChild(text);
  }
  return { labelY, below };
}

function buildToken(model: RenderToken, gridSize: number, art: Texture | null = null): Container {
  const root = new Container({ label: `token:${model.id}` });
  const radius = tokenRadius(model.size, gridSize);
  const body = new Graphics();
  body.circle(0, 0, radius).fill({ color: model.color, alpha: 0.9 });
  root.addChild(body);
  // Token art (M6) fills the disc, cropped to a circle: scaled to cover, so a
  // portrait or a landscape image both fill it without stretching.
  if (art && art.width > 0 && art.height > 0) {
    const image = new Sprite(art);
    image.anchor.set(0.5);
    const scale = (2 * radius) / Math.min(art.width, art.height);
    image.scale.set(scale);
    const mask = new Graphics().circle(0, 0, radius).fill(0xffffff);
    image.mask = mask;
    root.addChild(mask, image);
  }
  const ring = new Graphics();
  ring.circle(0, 0, radius).stroke({ width: 3, color: 0x0c0a07, alpha: 0.9 });
  // A pending command: a dashed-looking outer ring until its frame arrives.
  if (model.pending) ring.circle(0, 0, radius + 5).stroke({ width: 2, color: 0xede4cf, alpha: 0.7 });
  root.addChild(ring);
  // GM only: what the table cannot see is drawn dimmed, never hidden.
  if (model.hidden) root.alpha = 0.45;
  const { labelY, below } = model.live ? drawLive(root, model.live, radius) : { labelY: radius + 4, below: [] };
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
  label.position.set(0, labelY);
  root.addChild(label);
  let y = labelY + CANVAS_LABEL_FONT.sizePx + 3;
  for (const line of below) {
    line.anchor.set(0.5, 0);
    line.position.set(0, y);
    root.addChild(line);
    y += CANVAS_LABEL_FONT.sizePx + 1;
  }
  root.position.set(model.x, model.y);
  return root;
}

// Every image URL a scene draws: its background and each token's art.
export function sceneImageUrls(scene: RenderScene): string[] {
  const urls: string[] = [];
  if (scene.backgroundUrl) urls.push(scene.backgroundUrl);
  for (const token of scene.tokens) if (token.artUrl) urls.push(token.artUrl);
  return urls;
}
