// Strict decoding for raw text from the VTT server (schema amendment S2).
// JSON.parse alone silently keeps the last of two duplicate keys and turns an
// overflowing number into Infinity, so a frame can mean something different to
// the client than to the server's validator. Every inbound message goes
// through decodeStrict before schema validation.

export const MAX_MESSAGE_BYTES = 2_097_152;
export const MAX_DEPTH = 64;

export class StrictJsonError extends Error {
  constructor(readonly reason: 'size' | 'syntax' | 'duplicate-key' | 'number' | 'depth') {
    super(`strict JSON rejected: ${reason}`);
  }
}

const TOKEN = /"(?:\\.|[^"\\])*"|[{}[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g;

export function decodeStrict(raw: string): unknown {
  if (new TextEncoder().encode(raw).length > MAX_MESSAGE_BYTES) throw new StrictJsonError('size');
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new StrictJsonError('syntax');
  }
  // Syntax is already valid, so a token scan sees every object key in order.
  // Keys are compared after decoding, so "seq" and "\u0073eq" collide.
  const scopes: Array<Set<string> | null> = [];
  const tokens = raw.match(TOKEN) ?? [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i] as string;
    if (token === '{') scopes.push(new Set());
    else if (token === '[') scopes.push(null);
    else if (token === '}' || token === ']') scopes.pop();
    else if (token.startsWith('"') && tokens[i + 1] === ':') {
      const key = JSON.parse(token) as string;
      const scope = scopes.at(-1);
      if (scope) {
        if (scope.has(key)) throw new StrictJsonError('duplicate-key');
        scope.add(key);
      }
    }
  }
  checkValue(value, 0);
  return value;
}

function checkValue(value: unknown, depth: number): void {
  if (depth > MAX_DEPTH) throw new StrictJsonError('depth');
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new StrictJsonError('number');
    }
    return;
  }
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) checkValue(child, depth + 1);
  }
}
