import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The table bundle must not contain GM workspace or authoring code: a TV that is
// only ever a viewer should not ship GM tools. This walks the static relative
// import graph from each entry, so a new import that crosses the boundary fails
// here rather than in review.

const SRC = resolve(import.meta.dirname);
const IMPORT_RE = /(?:import|export)\s[^'"]*?from\s*['"](\.[^'"]+)['"]|import\s*['"](\.[^'"]+)['"]/g;
const EXTENSIONS = ['', '.ts', '.tsx', '/index.ts', '/index.tsx'];

function resolveImport(fromFile: string, spec: string): string | null {
  const base = join(dirname(fromFile), spec);
  for (const ext of EXTENSIONS) {
    const candidate = base + ext;
    if (existsSync(candidate) && /\.(ts|tsx)$/.test(candidate)) return candidate;
  }
  return null; // CSS and other assets are not code
}

function importGraph(entry: string): Set<string> {
  const seen = new Set<string>();
  const stack = [entry];
  while (stack.length) {
    const file = stack.pop() as string;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = readFileSync(file, 'utf-8');
    for (const m of text.matchAll(IMPORT_RE)) {
      const spec = m[1] ?? m[2];
      if (!spec) continue;
      const target = resolveImport(file, spec);
      if (target) stack.push(target);
    }
  }
  return seen;
}

const rel = (files: Set<string>) =>
  [...files].map((f) => f.slice(SRC.length + 1).replaceAll('\\', '/')).sort();

describe('bundle boundary', () => {
  it('the table entry does not reach GM workspace, bench or authoring code', () => {
    const files = rel(importGraph(join(SRC, 'main-table.tsx')));
    const forbidden = files.filter(
      (f) => f.startsWith('app/GmWorkspace') || f.startsWith('bench/') || f.startsWith('authoring/'),
    );
    expect(forbidden).toEqual([]);
    // Sanity: the walker actually followed imports.
    expect(files).toContain('app/TableScreen.tsx');
    expect(files).toContain('scene/SceneRuntime.ts');
  });

  it('the GM entry reaches the GM workspace (walker sanity check)', () => {
    expect(rel(importGraph(join(SRC, 'main-gm.tsx')))).toContain('app/GmWorkspace.tsx');
  });
});
