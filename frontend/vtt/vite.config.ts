import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Three entry pages. The table entry must never import authoring code: it is a
// separate bundle so a TV that is only ever a viewer does not ship GM tools.
//
// Output stays in dist/ (gitignored). Where built assets are published
// (committed static/dist/vtt/ per ADR-0004, or built on release) is an open
// decision in VTT-20261012-007, so nothing here assumes a deploy path yet.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        gm: resolve(import.meta.dirname, 'index.html'),
        table: resolve(import.meta.dirname, 'table.html'),
        bench: resolve(import.meta.dirname, 'bench.html'),
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
