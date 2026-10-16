import { describe, expect, it } from 'vitest';
import { MAX_DEPTH, MAX_MESSAGE_BYTES, StrictJsonError, decodeStrict } from './strictJson';

// Limits the shared corpus pins only on the Python side (S2); the client
// decoder must agree.

const reason = (raw: string): string | null => {
  try {
    decodeStrict(raw);
    return null;
  } catch (e) {
    return e instanceof StrictJsonError ? e.reason : 'other';
  }
};

describe('decodeStrict limits', () => {
  it('accepts nesting at the depth limit and refuses one deeper', () => {
    const nest = (n: number) => '['.repeat(n) + ']'.repeat(n);
    expect(reason(nest(MAX_DEPTH))).toBeNull();
    expect(reason(nest(MAX_DEPTH + 2))).toBe('depth');
  });

  it('refuses text over the byte cap, counting UTF-8 bytes rather than UTF-16 units', () => {
    const astral = '\u{1F409}'; // 4 UTF-8 bytes, 2 UTF-16 units
    const count = Math.floor(MAX_MESSAGE_BYTES / 4) - 1;
    expect(reason(JSON.stringify(astral.repeat(count)))).toBeNull();
    expect(reason(JSON.stringify(astral.repeat(count + 2)))).toBe('size');
  });

  it('refuses duplicate keys only within the same object', () => {
    expect(reason('{"a":{"k":1},"b":{"k":2}}')).toBeNull();
    expect(reason('{"a":1,"\\u0061":2}')).toBe('duplicate-key');
    expect(reason('[{"k":1,"k":1}]')).toBe('duplicate-key');
  });

  it('refuses unsafe integers and overflow, keeps safe ones', () => {
    expect(reason(String(Number.MAX_SAFE_INTEGER))).toBeNull();
    expect(reason('9007199254740993')).toBe('number');
    expect(reason('1e400')).toBe('number');
  });

  it('a key-like string value is not mistaken for a key', () => {
    expect(reason('{"a":"b","c":"b"}')).toBeNull();
    expect(reason('{"a":[":","a"]}')).toBeNull();
  });
});
