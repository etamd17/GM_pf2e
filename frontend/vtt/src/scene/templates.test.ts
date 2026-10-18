import { describe, expect, it } from 'vitest';
import { aimAngle, templateContains, templateOutline, type TemplateGeometry } from './templates';

const at = (shape: TemplateGeometry['shape'], extra: Partial<TemplateGeometry> = {}): TemplateGeometry => ({
  shape, x: 0, y: 0, aimX: 100, aimY: 0, size: 2, ...extra,
});
const G = 50; // px per square: size 2 reaches 100 px

describe('template geometry', () => {
  it('a burst and an emanation are circles of size squares', () => {
    for (const shape of ['burst', 'emanation'] as const) {
      expect(templateContains(at(shape), G, { x: 70, y: 70 })).toBe(true); // 99 px
      expect(templateContains(at(shape), G, { x: 75, y: 75 })).toBe(false); // 106 px
    }
  });

  it('a cone is 90 degrees wide around its aim', () => {
    const cone = at('cone');
    expect(templateContains(cone, G, { x: 90, y: 0 })).toBe(true);
    expect(templateContains(cone, G, { x: 60, y: 59 })).toBe(true); // just inside 45 degrees
    expect(templateContains(cone, G, { x: 50, y: 60 })).toBe(false); // outside 45 degrees
    expect(templateContains(cone, G, { x: -10, y: 0 })).toBe(false); // behind
    expect(templateContains(cone, G, { x: 101, y: 0 })).toBe(false); // too far
    expect(templateContains(at('cone', { aimX: 0, aimY: 100 }), G, { x: 0, y: 90 })).toBe(true); // aimed down
  });

  it('a line is one square wide along its aim, starting at the anchor', () => {
    const line = at('line');
    expect(templateContains(line, G, { x: 99, y: 24 })).toBe(true);
    expect(templateContains(line, G, { x: 50, y: 26 })).toBe(false);
    expect(templateContains(line, G, { x: -1, y: 0 })).toBe(false);
  });

  it('an aim on the anchor points along +x', () => {
    expect(aimAngle(at('cone', { aimX: 0, aimY: 0 }))).toBe(0);
  });

  it('outlines: a circle, a cone fan from the anchor, a four-corner line', () => {
    expect(templateOutline(at('burst'), G)).toEqual({ kind: 'circle', x: 0, y: 0, r: 100 });
    const cone = templateOutline(at('cone'), G);
    expect(cone.kind === 'polygon' && cone.points[0]).toEqual({ x: 0, y: 0 });
    const line = templateOutline(at('line'), G);
    expect(line.kind === 'polygon' && line.points.length).toBe(4);
  });
});
