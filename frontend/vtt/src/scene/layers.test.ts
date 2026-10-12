import { describe, expect, it } from 'vitest';
import { LAYER_ORDER, layersFor } from './layers';

describe('layers', () => {
  it('keeps the agreed bottom-to-top order', () => {
    expect(LAYER_ORDER).toEqual([
      'background',
      'grid',
      'tiles',
      'templates',
      'walls',
      'tokens',
      'lighting',
      'fog',
      'measure',
      'effects',
      'overlay',
    ]);
  });

  it('gives the GM every layer', () => {
    expect(layersFor('gm')).toEqual([...LAYER_ORDER]);
  });

  it('never builds walls or the authoring overlay on the table', () => {
    const table = layersFor('table');
    expect(table).not.toContain('walls');
    expect(table).not.toContain('overlay');
    // Remaining order is preserved.
    expect(table).toEqual(LAYER_ORDER.filter((l) => l !== 'walls' && l !== 'overlay'));
  });
});
