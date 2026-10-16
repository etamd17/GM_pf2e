// Self-hosted typefaces (Inter for UI, Cinzel for display), latin subset only.
// Vite bundles the woff2 files as app-origin assets, so no page contacts a font
// CDN at runtime (VTT-20261012-010 finding 4; app ADR-0004 asset origin).
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-600.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/cinzel/latin-400.css';
import '@fontsource/cinzel/latin-600.css';
