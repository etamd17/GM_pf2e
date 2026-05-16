/* Example module — disabled by default (file name starts with `_` so
 * it's hidden from /api/modules). Copy + rename to a non-underscore
 * filename if you want to try it. Demonstrates the four things a
 * module typically does:
 *
 *   1. registerModule({id, name, version}) — surfaces in the modules
 *      panel and serves as the source of error attribution
 *   2. on(event, fn) — listen to host events
 *   3. read mapState / DOM — modules run in page scope
 *   4. POST to /api/... — modules can drive the same endpoints the
 *      host JS uses (subject to GM auth)
 *
 * Available events (current scaffold):
 *   token_moved  — { id, from:[x,y], to:[x,y], name, is_pc }
 *   map_state    — full mapState payload on SSE map_state
 *   (extend the host's PF2E_HOOKS.fire calls to add more)
 */
(function () {
    if (!window.PF2E_HOOKS) return;

    PF2E_HOOKS.registerModule({
        id: '_example',
        name: 'Example module',
        version: '0.1',
    });

    PF2E_HOOKS.on('token_moved', (t) => {
        // Log every PC movement to the console — replace with anything.
        if (t.is_pc) {
            console.log(`[example] ${t.name} moved ${t.from.join(',')} → ${t.to.join(',')}`);
        }
    });
})();
