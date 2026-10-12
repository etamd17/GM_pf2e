// The renderer's own input model. This is NOT the wire contract: the shared
// v1 schemas are still being agreed (VTT-ADR-0002). Once they are accepted, an
// adapter maps a sanitized projection into these shapes, and the renderer
// never sees protocol fields.
//
// Objects are treated as immutable. SceneRuntime skips any token whose object
// identity has not changed, so producers must replace, never mutate.

export interface RenderToken {
  readonly id: string;
  readonly x: number; // centre, scene px
  readonly y: number;
  readonly size: number; // in grid squares (1 = medium)
  readonly label: string;
  readonly color: number; // 0xRRGGBB
}

export interface RenderWall {
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  readonly kind: 'wall' | 'door';
}

export interface RenderLight {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly radius: number; // scene px
  readonly color: number;
}

export interface RenderScene {
  readonly width: number;
  readonly height: number;
  readonly gridSize: number;
  readonly tokens: readonly RenderToken[];
  readonly walls: readonly RenderWall[];
  readonly lights: readonly RenderLight[];
}

export const EMPTY_SCENE: RenderScene = {
  width: 2048,
  height: 2048,
  gridSize: 64,
  tokens: [],
  walls: [],
  lights: [],
};
