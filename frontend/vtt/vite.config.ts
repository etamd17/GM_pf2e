import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { resolveAssetBase } from './src/build/assetOrigin.ts';

// Production build shape (VTT-20261012-010, integration blockers):
// - an absolute, app-origin asset base so /vtt, /vtt/table and
//   /vtt/scene/<id> all resolve the same URLs. The default '/vtt/assets/' is
//   a proposal; Flask owns the route, so it can be overridden with
//   VTT_ASSET_BASE until the integration lead fixes it. The override must be a
//   same-origin root-relative path, and anything else fails the build;
// - a Vite manifest (.vite/manifest.json) for the server-side manifest helper;
// - no source maps and no bench entry unless explicitly requested
//   (VTT_SOURCEMAP=1, VTT_BENCH=1) for a protected diagnostics build.
//
// Output stays in dist/ (gitignored). VTT-ADR-0002 section 11: generated
// bundles are built by Docker/CI, never committed.
export default defineConfig(({ command }) => {
  const env = process.env;
  const withBench = env.VTT_BENCH === '1';
  const input: Record<string, string> = {
    gm: resolve(import.meta.dirname, 'index.html'),
    table: resolve(import.meta.dirname, 'table.html'),
  };
  if (withBench) input.bench = resolve(import.meta.dirname, 'bench.html');
  return {
    plugins: [react()],
    base: command === 'build' ? resolveAssetBase(env.VTT_ASSET_BASE) : '/',
    build: {
      outDir: env.VTT_OUT_DIR ?? 'dist',
      // Assets sit at the root of dist/, beside the two HTML entries, so the
      // base is literally where Flask serves them: /vtt/assets/<file> is
      // dist/<file> (vtt/transport/pages.py). With Vite's default 'assets'
      // directory every URL came out as /vtt/assets/assets/<file>.
      assetsDir: '',
      emptyOutDir: true,
      manifest: true,
      sourcemap: env.VTT_SOURCEMAP === '1',
      rollupOptions: { input, output: {
        // Keep shared React separate from the rendering chunk. Match only
        // vendor modules: never pull GM authoring dependencies into the TV.
        codeSplitting: {groups:[{
          name:'react-vendor',
          test:/[\\/]node_modules[\\/](?:react|react-dom|scheduler)[\\/]/,
          includeDependenciesRecursively:false,
        }]},
      } },
    },
    server: {
      port: 5173,
      strictPort: true,
    },
    test: {
      environment: 'node',
      include: ['src/**/*.test.{ts,tsx}'],
    },
  };
});
