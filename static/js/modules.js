/* ── PF2E_HOOKS — minimal module hook registry ──────────────────────────
 *
 * Scaffolding for a future module system. Today this is the smallest
 * useful surface: a global pub/sub for well-known events the host
 * fires, plus a discovery channel so a campaign module can register
 * handlers in a known way.
 *
 * What you can do with it right now:
 *
 *   1. Drop a JS file into `static/js/modules/<name>.js`
 *   2. Register your name in the campaign config (see
 *      /api/modules — enabled IDs persist per campaign)
 *   3. The host loads enabled modules on each page that includes
 *      modules.js. Your file gets `PF2E_HOOKS` on window and can
 *      register handlers:
 *
 *      PF2E_HOOKS.on('token_moved',       (t) => console.log(t));
 *      PF2E_HOOKS.on('map_state',         (s) => ...);  // any state push
 *      PF2E_HOOKS.on('encounter_update',  (p) => ...);  // turn / HP / cond
 *      PF2E_HOOKS.on('drawings_changed',  (d) => ...);
 *      PF2E_HOOKS.on('audio_play',        (p) => ...);  // {id,url,...}
 *      PF2E_HOOKS.on('audio_stop',        (p) => ...);
 *
 *   4. Optional `module.meta = { id, name, version }` for the
 *      enable/disable UI.
 *
 * What's NOT here yet (deferred — see commit message):
 *   • Permissions / sandboxing — modules run with full page DOM access
 *   • Manifest schema beyond the meta hint above
 *   • Marketplace / install flow — drop files in by hand for now
 *   • Server-side hooks (Python-side plugin loading)
 *
 * The hook taxonomy is intentionally narrow. Adding a new event is a
 * deliberate decision the host owns; we don't want every internal
 * function call leaking to modules.
 */
(function () {
    if (window.PF2E_HOOKS) return;  // idempotent — multiple <script> tags safe

    const handlers = new Map();  // eventName → [{module, fn}]
    const modules = new Map();   // moduleId → {meta, errors[]}

    function on(event, fn, moduleId) {
        if (typeof fn !== 'function') return;
        if (!handlers.has(event)) handlers.set(event, []);
        handlers.get(event).push({ module: moduleId || '_anon', fn });
    }

    function off(event, fn) {
        const list = handlers.get(event);
        if (!list) return;
        const i = list.findIndex(h => h.fn === fn);
        if (i >= 0) list.splice(i, 1);
    }

    function fire(event, data) {
        const list = handlers.get(event);
        if (!list || list.length === 0) return;
        for (const h of list) {
            try { h.fn(data); }
            catch (err) {
                // A bad module shouldn't break the host. Log against
                // the module's record so the GM can see what's flaky.
                const rec = modules.get(h.module);
                if (rec) rec.errors.push({ event, err: String(err) });
                console.error('[PF2E_HOOKS]', h.module, event, err);
            }
        }
    }

    function registerModule(meta) {
        if (!meta || !meta.id) return;
        modules.set(meta.id, { meta, errors: [] });
    }

    function listModules() {
        return Array.from(modules.values());
    }

    window.PF2E_HOOKS = { on, off, fire, registerModule, listModules };
})();
