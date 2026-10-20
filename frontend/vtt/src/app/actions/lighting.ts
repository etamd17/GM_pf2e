// Dynamic lighting and vision (E-6). Published by Claude for Codex's Scene tab
// and token inspector. The wire is in scene pixels (like a light's radius);
// a GM thinks in feet, so these convert by the scene's grid (5 ft a square).
//
// - Lighting on: the table draws darkness except what party tokens (linked to
//   a player character) and the scene's lights can see; walls and doors that
//   are not open block sight. A light reveals out to twice its radius (dim
//   light), as on /map.
// - A party token sees out to its own vision radius, or the scene's default.
//   0 means it sees only by light (no darkvision).
// - The GM page shows no darkness unless the preview is on
//   (MapHost/SceneRuntime setVisionPreview), which draws it half as dark.

import type { GmEntity } from '../../protocol/frames';
import { updateScene, updateToken, type Draft } from '../gmCommands';
import { FEET_PER_SQUARE } from '../../scene/measure';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;

const MAX_PX = 16384;

export function feetToPx(feet: number, gridSize: number): number {
  return Math.max(0, Math.min(MAX_PX, Math.round((feet / FEET_PER_SQUARE) * gridSize)));
}

export function pxToFeet(px: number, gridSize: number): number {
  return gridSize > 0 ? Math.round((px / gridSize) * FEET_PER_SQUARE) : 0;
}

export interface LightingState {
  lighting: boolean;
  defaultVisionFeet: number;
}

export function lightingState(scene: Of<'scene'>): LightingState {
  return {
    lighting: scene.lighting === true,
    defaultVisionFeet: pxToFeet(typeof scene.defaultVision === 'number' ? scene.defaultVision : 0, scene.gridSize),
  };
}

// Turn lighting on or off, optionally with the party's default vision in feet.
export function setLighting(scene: Of<'scene'>, on: boolean, defaultVisionFeet?: number): Draft {
  return updateScene(scene, {
    lighting: on,
    ...(typeof defaultVisionFeet === 'number' ? { defaultVision: feetToPx(defaultVisionFeet, scene.gridSize) } : {}),
  });
}

// A token's own vision in feet, or null for the scene's default.
export function setTokenVision(token: Of<'token'>, feet: number | null, gridSize: number): Draft {
  return updateToken(token, { vision: feet === null ? null : feetToPx(feet, gridSize) });
}

export function tokenVisionFeet(token: Of<'token'>, gridSize: number): number | null {
  return typeof token.vision === 'number' ? pxToFeet(token.vision, gridSize) : null;
}
