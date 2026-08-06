/**
 * dice-adapter.js — compatibility shim between the retired physics engine's
 * public surface (static/js/dice-engine.js, deleted) and the new dice-overlay.js
 * + dice-geometry.js engine (three.js, vendored at static/js/vendor/).
 *
 * Consumers: templates/player_sheet.html, templates/base.html (GM/table toast
 * for other players' rolls) and templates/cosmere_sheet.html. All of them only
 * ever touch:
 *   - DiceRoller.isSupported()            static WebGL feature check
 *   - window.diceRoller.enabled           readable bool, driven by the sheet's
 *                                          own pf2e_dice_render/pf2e_dice_style
 *                                          settings via setEnabled()
 *   - window.diceRoller.setEnabled(bool)  toggle 3D on/off
 *   - window.diceRoller.roll(config)      config: { dice:[{sides,value}], modifier,
 *                                          total, label, detail, isCrit, isFumble }
 *   - window.diceRoller.markLocalRoll(sig)   / .consumeLocalRoll(sig)
 *                                          self-echo dedup, see below
 *   - DiceRoller.MATERIAL_OPTIONS          static [{key,label,swatch}, ...]
 *                                          for a dice-theme picker UI
 *   - window.diceRoller.materialKey        currently-selected theme key
 *   - window.diceRoller.setMaterial(key)   switch theme, persists + applies
 *                                          live (see "Dice theme" section below)
 *
 * window.diceRoller and window.DiceRoller are created synchronously below so
 * those guards work immediately at page load. dice-overlay.js itself (three.js
 * + geometry, ~400 KB gzipped) is only pulled in via dynamic import() the first
 * time roll() actually runs — most page loads never roll, and none of them
 * should pay for three.js.
 *
 * roll(config) never lets the overlay pick its own numbers: config.dice already
 * carries the exact faces the caller rolled (and already displayed/broadcast to
 * the GM), so they're threaded straight into overlay.rollTo(formula, { rolls }).
 * The 3D dice always land on the total the app already decided.
 */

// Die sizes dice-overlay.js can throw directly, in the fixed order its own
// throwListFor() groups them into (see dice-overlay.js's parseSpec/throwListFor).
// d100 is deliberately excluded: the overlay models it as a tens-die + ones-die
// pair, but config.dice carries one {sides:100, value} entry for the whole
// 1-100 result — there's no clean way to split that back into two physical
// dice, so a d100 (or any other non-standard side count) falls back to the
// existing 2D toast instead of guessing.
const OVERLAY_DIE_ORDER = [4, 6, 8, 10, 12, 20];

const CONTAINER_ID = 'dice-adapter-overlay';
// templates/_pc_sheet/_modals.html's #roll-toast-container is z-[200]; the
// sheet's own modals (equip, spell card, daily prep, etc.) run 500-10000+.
// Sit under all of them so an open modal or an incoming toast is never
// covered by the dice overlay.
const Z_INDEX = 150;
// How long a settled roll stays on screen before it's cleared automatically —
// mirrors the old engine's 3s auto-dismiss.
const DISMISS_MS = 2600;
// Hard cap on how long roll() will wait for the dice to settle before
// resolving anyway. rAF (and so the overlay's own settle detection) is
// paused whenever the tab/pane is hidden, so this is not just a nicety —
// it's the only thing that keeps a hidden/backgrounded roll from hanging
// forever. Tuned to comfortably exceed a visible 'tumble'-style throw.
const SETTLE_TIMEOUT_MS = 2500;

// ─── Dice theme (material) ───────────────────────────────────────────────
//
// dice-overlay.js's MATERIALS map (12 entries, from dice-geometry.js) is
// only reachable through a dynamic import() of the three.js-backed engine —
// this file deliberately never imports it eagerly (see the module docstring:
// three.js is ~400KB gz and must stay lazy, pulled in only on first roll).
// A theme *picker* still needs the label + swatch colour for every material
// before any roll has happened, so that subset is duplicated here as plain
// data. Keep in sync with the `label`/`swatch` fields in dice-geometry.js's
// MATERIALS if a material is ever added, renamed, or recoloured there.
const MATERIAL_OPTIONS = [
    { key: 'steel', label: 'Forged steel', swatch: '#9aa1aa' },
    { key: 'copper', label: 'Aged copper', swatch: '#b3714a' },
    { key: 'obsidian', label: 'Obsidian & gold', swatch: '#d6ab5e' },
    { key: 'bone', label: 'Carved bone', swatch: '#ded3bb' },
    { key: 'ivory', label: 'Ivory plastic', swatch: '#eee7d9' },
    { key: 'ruby', label: 'Ruby glass', swatch: '#d21f45' },
    { key: 'frost', label: 'Frosted quartz', swatch: '#cfe2ec' },
    { key: 'jade', label: 'Jade', swatch: '#3f8f6f' },
    { key: 'amber', label: 'Amber resin', swatch: '#c9761c' },
    { key: 'nebula', label: 'Nebula', swatch: '#6d5ac6' },
    { key: 'liquid', label: 'Liquid core · aqua', swatch: '#3fd2c2' },
    { key: 'liquidRose', label: 'Liquid core · rose', swatch: '#e0577f' },
];
const DEFAULT_MATERIAL = 'obsidian';
const MATERIAL_STORAGE_KEY = 'pf2e_dice_material';

function isValidMaterial(key) {
    return MATERIAL_OPTIONS.some((m) => m.key === key);
}

function getStoredMaterial() {
    try {
        const v = localStorage.getItem(MATERIAL_STORAGE_KEY);
        if (v && isValidMaterial(v)) return v;
    } catch (e) { /* localStorage unavailable */ }
    return DEFAULT_MATERIAL;
}

// Current material key. Module-scoped (not tied to the DiceRollerAdapter
// instance) so getOverlay() below can read it without a `this` reference —
// it's set once at load from localStorage and updated by setMaterial().
let currentMaterialKey = getStoredMaterial();

function isSupported() {
    try {
        const c = document.createElement('canvas');
        return !!(c.getContext('webgl2') || c.getContext('webgl'));
    } catch (e) {
        return false;
    }
}

class DiceRoller {
    static isSupported() {
        return isSupported();
    }

    // {key, label, swatch} for every dice theme, safe to render into a
    // picker at page load — no three.js import required.
    static get MATERIAL_OPTIONS() {
        return MATERIAL_OPTIONS;
    }
}

// ─── Full-viewport overlay container (created lazily, on first roll) ───────

let container = null;
let canvasEl = null;

function ensureContainer() {
    if (container) return container;

    container = document.createElement('div');
    container.id = CONTAINER_ID;
    container.style.cssText = `position:fixed; inset:0; z-index:${Z_INDEX}; pointer-events:none;`;

    if (!document.getElementById('dice-adapter-css')) {
        const style = document.createElement('style');
        style.id = 'dice-adapter-css';
        // The canvas starts non-interactive. Unlike the old engine (which
        // created/destroyed its overlay per roll), this engine's overlay is a
        // long-lived singleton, so "pointer-events: auto" can't be a static
        // rule — it's flipped on only while dice are actively thrown or
        // settled-and-visible (see setInteractive below), so a finished roll
        // never leaves an invisible full-viewport click-blocker over the sheet.
        style.textContent = `#${CONTAINER_ID} canvas { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; display:block; }`;
        document.head.appendChild(style);
    }

    document.body.appendChild(container);
    return container;
}

function setInteractive(on) {
    if (!canvasEl) canvasEl = container ? container.querySelector('canvas') : null;
    if (canvasEl) canvasEl.style.pointerEvents = on ? 'auto' : 'none';
}

let dismissTimer = null;

function clearDismissTimer() {
    if (dismissTimer) {
        clearTimeout(dismissTimer);
        dismissTimer = null;
    }
}

function dismiss(overlay) {
    clearDismissTimer();
    setInteractive(false);
    try { overlay.clear(); } catch (e) { /* never break the caller */ }
}

// ─── Lazy overlay singleton ──────────────────────────────────────────────

let overlayPromise = null;

// The overlay's `onResult` is a single-slot setter (see dice-overlay.js's
// `set onResult(fn)`) — assigning it again would replace, not add to, the
// dismiss-scheduling hook wired up below. So that hook is set exactly once,
// here, and roll() below hooks into settle notifications by queueing onto
// this array instead of touching overlay.onResult itself.
let settleListeners = [];

function notifySettleListeners(result) {
    const listeners = settleListeners;
    settleListeners = [];
    listeners.forEach((fn) => {
        try { fn(result); } catch (e) { /* never break other listeners */ }
    });
}

function getOverlay() {
    if (!overlayPromise) {
        overlayPromise = import('./dice-overlay.js').then((mod) => {
            const el = ensureContainer();
            const overlay = mod.createDiceOverlay({
                container: el,
                material: currentMaterialKey,
                onRollStart() {
                    clearDismissTimer();
                    setInteractive(true);
                },
                onResult(result) {
                    clearDismissTimer();
                    dismissTimer = setTimeout(() => dismiss(overlay), DISMISS_MS);
                    notifySettleListeners(result);
                },
            });
            canvasEl = el.querySelector('canvas');
            el.addEventListener('click', () => dismiss(overlay));
            return overlay;
        }).catch((e) => {
            // Don't cache a failed import/init — a transient failure (e.g. a
            // WebGL context creation error) shouldn't permanently disable 3D
            // dice for the rest of the session.
            overlayPromise = null;
            throw e;
        });
    }
    return overlayPromise;
}

// ─── config.dice -> overlay formula + forced face values ───────────────────

/**
 * Build a dice-overlay formula string plus a matching "rolls" array (one raw
 * face value per physical die, in the exact order dice-overlay.js's
 * throwListFor() throws them: grouped by side count, d4 through d20).
 * Returns null if any die can't be represented (see OVERLAY_DIE_ORDER above) —
 * callers should fall back to the existing 2D toast rather than show a 3D
 * roll that doesn't match every die.
 */
function buildRollPlan(diceList, modifier) {
    const bySides = new Map();
    for (const d of diceList) {
        if (!d || !OVERLAY_DIE_ORDER.includes(d.sides)) return null;
        if (!bySides.has(d.sides)) bySides.set(d.sides, []);
        bySides.get(d.sides).push(d.value);
    }

    const parts = [];
    const rolls = [];
    for (const sides of OVERLAY_DIE_ORDER) {
        const values = bySides.get(sides);
        if (!values || !values.length) continue;
        parts.push(`${values.length}d${sides}`);
        rolls.push(...values);
    }
    if (!parts.length) return null;

    let formula = parts.join(' + ');
    const mod = Number(modifier) || 0;
    if (mod) formula += ` ${mod > 0 ? '+' : '-'} ${Math.abs(mod)}`;

    return { formula, rolls };
}

// ─── Self-echo dedup registry ───────────────────────────────────────────────
//
// Some pages both animate a roll locally AND broadcast it to the server for
// re-display elsewhere (e.g. cosmere_sheet.html calls roll() directly, then
// posts to /api/cosmere/roll, which re-broadcasts the same roll as a
// 'player_roll' SSE event). Because cosmere_sheet.html extends base.html, its
// own tab also runs base.html's GM/table toast listener — without a dedup
// hook that SSE echo would trigger a second, redundant 3D animation of a roll
// this exact tab just played. A caller that already animated a roll locally
// registers its fingerprint here (markLocalRoll); the SSE-side listener
// consumes it (consumeLocalRoll) to skip re-animating that one echo, while
// still animating rolls that came from someone else. This is a fixed-size,
// time-boxed ring buffer, not a correctness-critical structure — a missed
// match just means one extra (harmless) animation.
const LOCAL_ROLL_SIG_TTL_MS = 5000;
const _recentLocalRollSigs = [];

function markLocalRoll(sig) {
    if (!sig) return;
    _recentLocalRollSigs.push({ sig, ts: Date.now() });
    if (_recentLocalRollSigs.length > 12) _recentLocalRollSigs.shift();
}

function consumeLocalRoll(sig) {
    if (!sig) return false;
    const now = Date.now();
    const idx = _recentLocalRollSigs.findIndex((r) => r.sig === sig && (now - r.ts) < LOCAL_ROLL_SIG_TTL_MS);
    if (idx === -1) return false;
    _recentLocalRollSigs.splice(idx, 1);
    return true;
}

// ─── Public window.diceRoller ───────────────────────────────────────────────

class DiceRollerAdapter {
    constructor() {
        this.enabled = true;
        try {
            this.enabled = localStorage.getItem('dice3d_enabled') !== 'false';
        } catch (e) { /* localStorage unavailable */ }
    }

    setEnabled(val) {
        this.enabled = !!val;
        try { localStorage.setItem('dice3d_enabled', this.enabled ? 'true' : 'false'); } catch (e) {}
    }

    // Registers/consumes a fingerprint for the self-echo dedup described
    // above. Both are no-ops (false/undefined) if `sig` is falsy.
    markLocalRoll(sig) { markLocalRoll(sig); }
    consumeLocalRoll(sig) { return consumeLocalRoll(sig); }

    // Current dice theme (material) key — read by the picker UI to show
    // which option is selected.
    get materialKey() { return currentMaterialKey; }

    // Switch the dice theme. Persists to localStorage so it survives a
    // reload, and — if the overlay already exists (a roll has happened this
    // session) — applies immediately via its own setMaterial(), which only
    // changes what NEW dice are made from on the next roll(); it never
    // touches bodies already in flight, so this is always safe to call
    // mid-roll. If the overlay hasn't been created yet, getOverlay() reads
    // currentMaterialKey when it does, so the very first roll already uses
    // the picked theme.
    setMaterial(key) {
        if (!isValidMaterial(key)) return;
        currentMaterialKey = key;
        try { localStorage.setItem(MATERIAL_STORAGE_KEY, key); } catch (e) {}
        if (overlayPromise) {
            overlayPromise.then((overlay) => { try { overlay.setMaterial(key); } catch (e) {} }).catch(() => {});
        }
    }

    // config: { dice: [{sides, value}], modifier, total, label, detail, isCrit, isFumble }
    // Async: when 3D actually runs, resolves once the dice come to rest (so a
    // caller's `.finally(() => showRollToast(...))` lands the toast WITH the
    // dice, not before them) or after SETTLE_TIMEOUT_MS, whichever is first —
    // never both, never neither. Returns false immediately (and does nothing)
    // when disabled/unsupported/unrepresentable, so the caller's existing 2D
    // toast fallback still runs with no pointless delay.
    // Total: this must NEVER reject and NEVER hang, no matter what throws -
    // callers chain a result toast off this promise, so a rejection (or a
    // promise that never settles) would silently swallow the roll result.
    async roll(config) {
        try {
            if (!this.enabled || !DiceRoller.isSupported()) return false;
            if (!config || !Array.isArray(config.dice) || config.dice.length === 0) return false;

            const plan = buildRollPlan(config.dice, config.modifier);
            if (!plan) return false;

            const overlay = await getOverlay();
            overlay.rollTo(plan.formula, { rolls: plan.rolls });

            // Wait for the dice to settle (via notifySettleListeners, fed by
            // the overlay's single onResult hook above) or for the hard
            // timeout, whichever comes first. `done` guards against firing
            // twice — e.g. a late settle notification arriving just after
            // the timeout already resolved this same roll.
            await new Promise((resolve) => {
                let done = false;
                const finish = () => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    resolve();
                };
                const timer = setTimeout(finish, SETTLE_TIMEOUT_MS);
                settleListeners.push(finish);
            });
            return true;
        } catch (e) {
            console.warn('dice-adapter: 3D roll unavailable, falling back to 2D toast.', e);
            return false;
        }
    }
}

window.DiceRoller = DiceRoller;
window.diceRoller = new DiceRollerAdapter();
