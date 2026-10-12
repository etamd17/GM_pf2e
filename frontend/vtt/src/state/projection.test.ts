import { beforeEach, describe, expect, it } from 'vitest';
import { EMPTY_SCENE } from '../scene/renderModel';
import { resetProjectionForTest, useProjection } from './projection';

const store = () => useProjection.getState();

beforeEach(resetProjectionForTest);

describe('projection epochs', () => {
  it('accepts a write carrying the current epoch', () => {
    const epoch = store().open();
    expect(epoch).not.toBeNull();
    expect(store().setScene(epoch!, EMPTY_SCENE)).toBe(true);
    expect(store().scene).toBe(EMPTY_SCENE);
  });

  it('rejects a write from an older subscription after a new one opens', () => {
    const old = store().open()!;
    const current = store().open()!;
    expect(store().setScene(old, EMPTY_SCENE)).toBe(false);
    expect(store().scene).toBeNull();
    expect(store().setScene(current, EMPTY_SCENE)).toBe(true);
  });

  it('clear() drops the scene and invalidates outstanding writers', () => {
    const epoch = store().open()!;
    store().setScene(epoch, EMPTY_SCENE);
    store().clear();
    expect(store().scene).toBeNull();
    expect(store().setScene(epoch, EMPTY_SCENE)).toBe(false);
    // Recoverable: a fresh subscription may open after clear().
    expect(store().open()).not.toBeNull();
  });

  it('seal() is terminal: no open, no write, scene stays null', () => {
    const epoch = store().open()!;
    store().setScene(epoch, EMPTY_SCENE);
    store().seal();
    expect(store().scene).toBeNull();
    expect(store().setScene(epoch, EMPTY_SCENE)).toBe(false);
    expect(store().open()).toBeNull();
    expect(store().scene).toBeNull();
  });
});
