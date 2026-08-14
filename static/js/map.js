(function () {
    'use strict';

    const cfg = window.SCENE_MAP_CONFIG || {};
    const candidates = window.SCENE_TOKEN_CANDIDATES || [];
    const sceneId = cfg.sceneId;
    const canvas = document.getElementById('map-canvas');
    const viewport = document.getElementById('map-viewport');
    if (!sceneId || !canvas || !viewport) {
        wireCreateForm();
        // A table screen opened BEFORE a scene is pushed used to stop here, and
        // every appSSE subscription lives at the bottom of this file -- so that
        // window subscribed to nothing and could never recover. Switching the
        // TV on and then setting up is the natural order of operations at a
        // table, and it left the screen dead on "No active scene" until someone
        // walked over and reloaded it.
        //
        // Reloading is the honest fix rather than building the whole surface
        // lazily: the page is server-rendered around a scene id, so there is no
        // canvas, no sidebar and no controls to hand a scene to.
        if (cfg.tableView && window.appSSE) {
            window.appSSE('scene_activated', function () { window.location.reload(); });
        }
        return;
    }

    const ctx = canvas.getContext('2d');

    // Canvas text has to be told the family; it inherits nothing from CSS. Every
    // ctx.font here used to hardcode 'system-ui' -- which is the FALLBACK inside
    // --font-ui, the thing Inter exists to avoid. The canvas is the entire table
    // screen, so that made the one typeface the players read all session the one
    // the project's two-face rule forbids, and it differed between the GM's
    // laptop and the TV. It hid in JS rather than CSS, which is why every
    // previous font sweep missed it.
    // Canvas colours have the same problem canvas fonts had: nothing inherits.
    // Only the HP bar reads from here, because system.css documents --success
    // and --warn as "used SPARINGLY -- only HP-full / HP-low state", and the
    // bar is exactly that. The door markers and draft strokes further down stay
    // literal: they are GM-only authoring affordances with no token counterpart,
    // and painting a door with --success would break the token's stated scope.
    const brandCache = new Map();
    function brand(name, fallback) {
        if (!brandCache.has(name)) {
            const value = getComputedStyle(document.documentElement)
                .getPropertyValue(name).trim();
            brandCache.set(name, value || fallback);
        }
        return brandCache.get(name);
    }

    let cachedUiFont = '';
    function uiFont() {
        if (!cachedUiFont) {
            cachedUiFont = getComputedStyle(document.documentElement)
                .getPropertyValue('--font-ui').trim() || 'system-ui, sans-serif';
        }
        return cachedUiFont;
    }
    const tokenImages = new Map();
    let scene = null;
    let zoom = 1;
    let selectedId = null;
    let interaction = null;
    let calibrationMode = false;
    let followActiveTurn = true;
    let focusedTurnCombatantId = null;
    let activeTool = 'select';
    const targetIds = new Set();
    const localTemplates = [];
    let measurement = null;
    let background = null;
    let backgroundKey = '';
    let toastTimer = null;

    function toast(message, error) {
        const el = document.getElementById('map-toast');
        if (!el) return;
        el.textContent = message;
        el.classList.toggle('is-error', !!error);
        el.classList.add('is-visible');
        clearTimeout(toastTimer);
        // A failure gets longer than a success. Both used to vanish on the same
        // 2.6 seconds, so an error that landed while the GM was looking at the
        // map was simply gone -- and the only difference between "saved" and
        // "that did not work" was a border colour.
        toastTimer = setTimeout(() => el.classList.remove('is-visible'), error ? 7000 : 2600);
    }

    async function request(url, options) {
        const response = await fetch(url, Object.assign({
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        }, options || {}));
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) throw new Error(data.error || 'Request failed');
        return data;
    }

    function normalizeClientScene(next) {
        next.grid = next.grid || {};
        next.grid.size = Number(next.grid.size) || 70;
        next.grid.offset_x = Number(next.grid.offset_x) || 0;
        next.grid.offset_y = Number(next.grid.offset_y) || 0;
        next.settings = next.settings || {};
        if (next.settings.dynamic_lighting === undefined) next.settings.dynamic_lighting = false;
        if (next.settings.default_vision === undefined) next.settings.default_vision = 700;
        next.fog = next.fog || {enabled: false, operations: []};
        next.fog.operations = next.fog.operations || [];
        next.walls = next.walls || [];
        next.lights = next.lights || [];
        next.templates = next.templates || [];
        for (const token of next.tokens || []) {
            if (token.locked === undefined) token.locked = false;
            if (token.show_nameplate === undefined) token.show_nameplate = true;
            if (!token.image_focus) token.image_focus = {x: 50, y: 50};
            if (token.vision_radius === undefined) token.vision_radius = next.settings.default_vision;
        }
        return next;
    }

    function applyScene(next) {
        if (!next || next.id !== sceneId) return;
        // Compare against the OUTGOING scene: once it is replaced there is
        // nothing left to diff against.
        noteMotionAndHealth(scene && scene.tokens, (next || {}).tokens);
        scene = normalizeClientScene(next);
        // Keep a token the GM is mid-nudge on. Every token move broadcasts a
        // scene_update, so the frame answering keypress one arrives AFTER
        // keypress two has already moved the token locally -- and without this
        // it lands on top and that keypress is silently lost. Same rule as the
        // one that stops an SSE frame overwriting a field being typed into;
        // this is the position equivalent.
        if (nudge) {
            const held = (scene.tokens || []).find(token => token.id === nudge.tokenId);
            if (held) { held.x = nudge.x; held.y = nudge.y; }
        }
        canvas.width = Math.max(1, Number(scene.width) || 1400);
        canvas.height = Math.max(1, Number(scene.height) || 900);
        if (selectedId && !(scene.tokens || []).some(token => token.id === selectedId)) selectedId = null;
        for (const id of Array.from(targetIds)) {
            if (!(scene.tokens || []).some(token => token.id === id)) targetIds.delete(id);
        }
        applyZoom();
        document.getElementById('map-title').textContent = scene.name || 'Tactical Map';
        document.getElementById('map-status').textContent = cfg.isGm
            ? (calibrationMode ? 'Grid alignment mode: drag anywhere on the map.' : 'GM view - changes are shared live')
            : ((scene.settings || {}).player_movement ? 'Your controlled token can be moved.' : 'Movement is controlled by the GM.');
        document.getElementById('map-revision').textContent = 'Revision ' + (scene.revision || 1);
        document.getElementById('map-empty').hidden = !!scene.background;
        if (cfg.isGm) fillControls();
        loadBackground();
        updateSelectionPanel();
        if (cfg.isGm) paintLightPanel();
        updateTargetPanel();
        draw();
        focusActiveTurn();
    }

    async function fetchScene() {
        try {
            const data = await request('/api/scenes/' + encodeURIComponent(sceneId));
            applyScene(data.scene);
        } catch (error) {
            toast(error.message, true);
        }
    }

    function loadBackground() {
        if (!scene || !scene.background) {
            background = null;
            backgroundKey = '';
            draw();
            return;
        }
        const meta = scene.background || {};
        const key = scene.id + ':' + (meta.filename || '') + ':' + (meta.version || 'legacy');
        if (backgroundKey === key) return;
        backgroundKey = key;
        const image = new Image();
        image.onload = function () { background = image; draw(); };
        image.onerror = function () { background = null; draw(); };
        image.src = '/api/scenes/' + encodeURIComponent(scene.id) + '/background?v=' + encodeURIComponent(meta.version || scene.revision);
    }

    function applyZoom() {
        if (!scene) return;
        canvas.style.width = Math.round(scene.width * zoom) + 'px';
        canvas.style.height = Math.round(scene.height * zoom) + 'px';
        const label = document.getElementById('map-zoom-label');
        if (label) label.textContent = Math.round(zoom * 100) + '%';
    }

    function viewportCenter() {
        return {x: viewport.clientWidth / 2, y: viewport.clientHeight / 2};
    }

    function setZoom(next, anchor) {
        if (!scene) return;
        const bounded = Math.max(.15, Math.min(4, next));
        const focus = anchor || viewportCenter();
        const worldX = (viewport.scrollLeft + focus.x) / zoom;
        const worldY = (viewport.scrollTop + focus.y) / zoom;
        zoom = bounded;
        applyZoom();
        viewport.scrollLeft = worldX * zoom - focus.x;
        viewport.scrollTop = worldY * zoom - focus.y;
        saveView();
    }

    // Per-scene zoom/pan, so reopening prep lands where you left it instead of
    // at 100% in the top-left corner. Scenes are now sized to their background
    // image, so "top-left" can be a long way from anything.
    //
    // The role is part of the key on purpose: the table screen is a different
    // view of the same scene and must never inherit the GM's viewport.
    const VIEW_KEY = 'pf2e_map_view:' + (cfg.isGm ? 'gm' : 'table') + ':';

    function saveView() {
        if (!scene) return;
        try {
            localStorage.setItem(VIEW_KEY + sceneId, JSON.stringify({
                zoom: zoom, x: viewport.scrollLeft, y: viewport.scrollTop
            }));
        } catch (_) { /* private mode / quota - not worth surfacing */ }
    }

    function restoreView() {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(VIEW_KEY + sceneId) || 'null'); } catch (_) {}
        if (!saved || !Number(saved.zoom)) return false;
        zoom = Math.max(.15, Math.min(4, Number(saved.zoom)));
        applyZoom();
        viewport.scrollLeft = Math.max(0, Number(saved.x) || 0);
        viewport.scrollTop = Math.max(0, Number(saved.y) || 0);
        return true;
    }

    function fitMap() {
        if (!scene) return;
        const widthZoom = (viewport.clientWidth - 20) / scene.width;
        const heightZoom = (viewport.clientHeight - 20) / scene.height;
        zoom = Math.max(.15, Math.min(1, widthZoom, heightZoom));
        applyZoom();
        viewport.scrollLeft = Math.max(0, (scene.width * zoom - viewport.clientWidth) / 2);
        viewport.scrollTop = Math.max(0, (scene.height * zoom - viewport.clientHeight) / 2);
    }

    function focusActiveTurn(force) {
        if (!scene || !followActiveTurn) return;
        const active = (scene.tokens || []).find(token => token.live && token.live.is_active);
        if (!active) return;
        const combatantId = active.combatant_id || active.id;
        if (!force && focusedTurnCombatantId === combatantId) return;
        focusedTurnCombatantId = combatantId;
        viewport.scrollLeft = Math.max(0, Number(active.x) * zoom - viewport.clientWidth / 2);
        viewport.scrollTop = Math.max(0, Number(active.y) * zoom - viewport.clientHeight / 2);
        // Deliberately does NOT touch selectedId. Scrolling to the active turn is
        // useful; silently repointing the inspector at a different token while
        // the GM is editing one is not -- it moved the target out from under
        // Save/Remove mid-edit. Follow the turn with the viewport only.
        draw();
    }

    // --- Frame scheduling ------------------------------------------------
    //
    // draw() is called from 18 places, several of them inside pointermove --
    // so dragging a token re-rendered the entire scene once per mouse event,
    // often several times per frame. Nothing was coalesced and there was no
    // requestAnimationFrame anywhere.
    //
    // draw() now only REQUESTS a frame. renderScene() is the real work and runs
    // at most once per animation frame, which is the most the display can show
    // anyway. Call sites are unchanged.
    let frameQueued = false;

    function draw() {
        if (frameQueued) return;
        frameQueued = true;
        window.requestAnimationFrame(function () {
            frameQueued = false;
            renderScene();
        });
    }

    // Diagnostic seam. requestAnimationFrame does not fire in a hidden
    // document -- correct behaviour, and what keeps a backgrounded tab from
    // burning CPU -- but it also means this canvas never renders in a headless
    // preview pane, where document.hidden is permanently true. Without a way to
    // force one frame, the map cannot be visually verified there at all.
    //
    // Deliberately not a fallback inside draw(): rendering a hidden document on
    // a timer is exactly the waste rAF exists to avoid.
    // An optional clock renders one frame AT a chosen moment. The animation
    // clock is only ever advanced by the rAF loop, so without this every forced
    // frame in a headless pane is frame zero, and nothing animated -- torch
    // flicker, a token's glide, terrain -- can be verified as MOVING rather
    // than merely as present. Setting it here cannot disturb a live loop: in a
    // pane where this is needed, no loop is running.
    window.__mapRenderNow = function (clock) {
        if (typeof clock === 'number') animationClock = clock;
        frameQueued = false;
        renderScene();
    };

    function renderScene() {
        if (!scene) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#181611';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        if (background) ctx.drawImage(background, 0, 0, canvas.width, canvas.height);
        // Terrain is ON THE FLOOR, so it goes under the grid: the GM still has
        // to count squares across a lake, and a grid drawn under a translucent
        // pool comes out tinted and muddy.
        drawTerrain();
        drawGrid();
        drawAmbientLights();
        // ...but the glow is LIGHT, so it belongs with the lights, above the
        // grid rather than beneath it.
        drawTerrainGlow();
        for (const template of scene.templates || []) drawTemplate(template);
        for (const template of localTemplates) drawTemplate(template);
        for (const token of scene.tokens || []) {
            // A truthful preview has to DROP hidden tokens, not dim them. The
            // GM's payload still contains them (the server only strips them for
            // player-facing payloads), so a preview that merely faded them
            // would quietly lie about what the table can see.
            if (isTableView() && token.visible_to_players === false) continue;
            drawToken(token);
        }
        if (isTableView()) drawVisionOverlay();
        drawFogOverlay();
        if (!isTableView()) {
            drawWallsAndDoors();
            drawLightControls();
        }
        drawTargetOverlays();
        if (!isTableView()) drawToolOverlay();
        drawFloaters();
        // Above the fog, like the floaters and for the same reason: a ruler the
        // GM is holding up for the room, and a ring they are pointing with, are
        // the two things that must not be dimmed by it.
        drawSharedMeasure();
        drawPings();
        drawTurnBanner();
        pruneGlides();
        syncAnimation();
    }

    function drawGrid() {
        const grid = scene.grid || {};
        if (!grid.visible) return;
        const size = Math.max(20, Number(grid.size) || 70);
        const ox = Number(grid.offset_x) || 0;
        const oy = Number(grid.offset_y) || 0;
        ctx.save();
        ctx.strokeStyle = calibrationMode ? 'rgba(240,216,138,.78)' : 'rgba(238,225,188,.24)';
        ctx.lineWidth = calibrationMode ? 2 : 1;
        ctx.beginPath();
        for (let x = ox; x <= canvas.width; x += size) { ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); }
        for (let y = oy; y <= canvas.height; y += size) { ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); }
        ctx.stroke();
        if (calibrationMode) {
            ctx.fillStyle = '#f0d88a';
            ctx.beginPath();
            ctx.arc(ox, oy, 6, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.restore();
    }

    // PF2e light has two zones: BRIGHT out to the stated radius, DIM to twice
    // it. The map drew one soft circle, so the edge of a lit area meant nothing
    // -- you could not tell where a torch stopped being useful. The stop at
    // 0.5 below is the bright/dim boundary, which is why it is a hard-ish step
    // rather than a smooth ramp.
    // Steady radii. Flicker is applied ONLY to the glow, never here.
    //
    // Two reasons, and the second is the load-bearing one. Visually, a revealed
    // area that strobes with the flame is unreadable and slightly sickening.
    // Mechanically, the vision mask is cached on a signature containing
    // light.radius (stage 6a); if flicker moved the carve radius, the cache
    // would either serve a stale mask or miss every single frame and drag the
    // whole raycast back into the frame budget -- undoing 6a to make a torch
    // wobble.
    function lightRadii(light) {
        const bright = Math.max(1, Number(light.radius) || 1);
        return {bright: bright, dim: bright * 2};
    }

    function drawAmbientLights() {
        for (const light of scene.lights || []) {
            // The GLOW flickers; the carved vision above does not.
            const radius = lightRadii(light).dim * flickerFactor(light);
            const gradient = ctx.createRadialGradient(light.x, light.y, 0, light.x, light.y, radius);
            // A stop is built by concatenating an alpha byte, so the colour has
            // to be exactly #rrggbb or the stop is invalid and throws inside the
            // render loop -- taking the whole canvas down, not just one light.
            const color = /^#[0-9a-f]{6}$/i.test(light.color || '') ? light.color : '#ffd98a';
            const alpha = Math.max(.05, Math.min(1, Number(light.intensity) || .75));
            // Was alpha * 90, i.e. capped at 0x5A -- about 35% -- so the
            // difference between a candle and a bonfire was nearly invisible.
            const byte = (a) => Math.round(Math.max(0, Math.min(255, a))).toString(16).padStart(2, '0');
            // Bright zone holds its value, then drops at the boundary and fades
            // out through the dim zone.
            gradient.addColorStop(0, color + byte(alpha * 200));
            gradient.addColorStop(0.48, color + byte(alpha * 170));
            gradient.addColorStop(0.52, color + byte(alpha * 80));
            gradient.addColorStop(1, color + '00');
            ctx.save();
            ctx.globalCompositeOperation = 'screen';
            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.arc(Number(light.x), Number(light.y), radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }
    }

    // --- Environmental terrain ----------------------------------------------
    //
    // Lava, water, poison and blood, painted a room at a time. COSMETIC BY
    // DECISION: none of it touches damage, conditions or movement cost, and
    // nothing downstream reads it for rules. It exists so the shared table
    // screen reads as a place rather than as a diagram.
    //
    // Two costs are deliberately kept out of the frame:
    //
    //   * the feathered outline is built ONCE per change, not per frame. A
    //     blur() over the painted area every frame would spend most of what
    //     stage 6a bought.
    //   * everything is drawn inside the layer's BOUNDING BOX. A pool is
    //     normally one room, and compositing four full-canvas layers to paint
    //     four rooms is most of a frame for nothing visible.
    //
    // Known limit of that second one, stated rather than hidden: a layer is one
    // per KIND, not one per pool, so two blood spills at opposite corners share
    // a bounding box that covers the whole map and the saving disappears. It
    // degrades rather than breaks -- the buffers are half resolution, so even
    // the worst case is a quarter of the naive cost -- and splitting layers
    // into connected components would buy the rest. Not worth it until a real
    // scene is slow.
    const TERRAIN_KINDS = ['lava', 'water', 'poison', 'blood'];
    const terrainCache = new Map();
    // Masks and buffers are built at half resolution. Everything here is a
    // blurred blob or a soft gradient, so there is no detail at full res to
    // lose -- and it quarters both the fill cost and the backing store, which
    // is what keeps a GM who floods an entire 60x40 map from allocating tens of
    // megabytes per kind.
    const TERRAIN_SCALE = 0.5;

    function terrainLayers() {
        const layers = [];
        for (const layer of scene.terrain || []) {
            if (!layer || !Array.isArray(layer.cells) || !layer.cells.length) continue;
            if (TERRAIN_KINDS.indexOf(layer.kind) === -1) continue;
            layers.push(layer);
        }
        return layers;
    }

    // Blood does not move, deliberately -- so a scene whose only terrain is a
    // blood pool never starts the animation loop at all, and the table screen
    // stays at stage 6a's idle cost.
    function terrainAnimates() {
        return terrainLayers().some(layer => layer.kind !== 'blood');
    }

    // Deterministic per-kind pseudo-randomness. Bubbles and magma cells have to
    // sit still between frames -- Math.random() here would make the whole pool
    // boil with static.
    function terrainRandom(seed) {
        const value = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
        return value - Math.floor(value);
    }

    function terrainEntry(layer) {
        const g = gridGeometry();
        // The grid has to be in the FAST key, not only in the slow one. The
        // calibration drag mutates scene.grid.offset_* locally with no save and
        // therefore no revision bump, so keying on the revision alone would
        // leave the pool sitting where the squares used to be while the GM
        // drags. Beyond that, scene.revision bumps on any save -- a token move
        // included -- so it is checked first and the cell signature is only
        // built when the revision actually moved. Otherwise every step a
        // creature takes would rebuild four blurred masks.
        const fast = [scene.revision, g.size, g.ox, g.oy].join(':');
        const cached = terrainCache.get(layer.kind);
        if (cached && cached.fast === fast) return cached;
        const sig = [g.size, g.ox, g.oy, layer.cells.length, layer.cells.join(',')].join('|');
        if (cached && cached.sig === sig) { cached.fast = fast; return cached; }

        // Enough to lose the square edges, not enough to lose the SQUARES. At
        // 0.42 a 42px blur spread about 120px, so two pools with a full empty
        // row between them merged into one puddle and the gap the GM left
        // simply vanished.
        const feather = Math.max(5, g.size * 0.26);
        const pad = Math.ceil(feather * 2);
        let minC = Infinity, maxC = -Infinity, minR = Infinity, maxR = -Infinity;
        const cells = [];
        for (const key of layer.cells) {
            const parts = String(key).split(',');
            const col = Number(parts[0]), row = Number(parts[1]);
            if (!Number.isFinite(col) || !Number.isFinite(row)) continue;
            cells.push([col, row]);
            if (col < minC) minC = col;
            if (col > maxC) maxC = col;
            if (row < minR) minR = row;
            if (row > maxR) maxR = row;
        }
        if (!cells.length) return null;
        const x = Math.floor(g.ox + minC * g.size) - pad;
        const y = Math.floor(g.oy + minR * g.size) - pad;
        const width = Math.max(1, Math.ceil((maxC - minC + 1) * g.size) + pad * 2);
        const height = Math.max(1, Math.ceil((maxR - minR + 1) * g.size) + pad * 2);
        const bw = Math.max(1, Math.round(width * TERRAIN_SCALE));
        const bh = Math.max(1, Math.round(height * TERRAIN_SCALE));

        const mask = document.createElement('canvas');
        mask.width = bw;
        mask.height = bh;
        const mctx = mask.getContext('2d');
        mctx.scale(TERRAIN_SCALE, TERRAIN_SCALE);
        // The same trick drawFogOverlay uses, for the same reason: painting
        // works in whole squares, so a hard edge announces the grid. Blurring
        // the MASK gives a pool an organic shoreline without softening the
        // battlemap underneath it.
        mctx.filter = 'blur(' + Math.round(feather * TERRAIN_SCALE) + 'px)';
        mctx.fillStyle = '#000';
        for (const cell of cells) {
            mctx.fillRect(g.ox + cell[0] * g.size - x, g.oy + cell[1] * g.size - y, g.size, g.size);
        }
        mctx.filter = 'none';
        mctx.setTransform(1, 0, 0, 1, 0, 0);

        const scratch = document.createElement('canvas');
        scratch.width = bw;
        scratch.height = bh;
        const entry = {
            fast: fast, sig: sig, mask: mask, scratch: scratch,
            sctx: scratch.getContext('2d'), x: x, y: y, w: width, h: height,
            bw: bw, bh: bh,
            // One square, in buffer pixels. Every size below is a fraction of
            // it rather than an absolute pixel count: a scene calibrated at 50
            // and one at 140 have to look like the same substance, and bubbles
            // measured in raw pixels are half a square on one map and invisible
            // on another.
            unit: Math.max(4, g.size * TERRAIN_SCALE),
            glow: layer.kind === 'lava' ? terrainGlow(mask, feather) : null
        };
        terrainCache.set(layer.kind, entry);
        return entry;
    }

    // Built once alongside the mask rather than blurred per frame. The pulse
    // comes from globalAlpha at draw time, which costs nothing.
    function terrainGlow(mask, feather) {
        const glow = document.createElement('canvas');
        glow.width = mask.width;
        glow.height = mask.height;
        const gctx = glow.getContext('2d');
        gctx.filter = 'blur(' + Math.round(feather * 2.6 * TERRAIN_SCALE) + 'px)';
        gctx.drawImage(mask, 0, 0);
        gctx.filter = 'none';
        gctx.globalCompositeOperation = 'source-in';
        gctx.fillStyle = '#ff7a2a';
        gctx.fillRect(0, 0, glow.width, glow.height);
        return glow;
    }

    // --- what each substance actually looks like -----------------------------
    //
    // Each kind is one or two passes, and the PASS COMPOSITE carries most of the
    // identity. Hue is the first thing a television across a room destroys --
    // at low alpha over an arbitrary battlemap, lava and blood are both "a red
    // patch". Luminance DIRECTION survives: lava is the only substance on the
    // map that makes it brighter, water and blood darken it, and poison lays a
    // haze over it. So any two of them differ by more than their colour even on
    // a bad screen at ten feet.
    //
    // The map is seen from ABOVE, which decides most of the motion: a bubble
    // does not rise up the screen, it swells and bursts in place, and anything
    // that travels in one direction reads as a side-on view fighting the map.
    //
    // One dominant behavioural cue each, because that is what carries across a
    // room when the colour does not:
    //   lava   - dark still crust with heat breathing underneath
    //   water  - two sets of highlights crossing and travelling
    //   poison - discrete things: bubbles swelling and bursting
    //   blood  - stillness, with a wet gloss

    function paintLavaCrust(c, w, h) {
        // Cooled basalt. Without it an additive orange layer is just an amber
        // filter over the floor; the dark crust is what makes the bright parts
        // read as incandescent rock rather than as a lighting bug.
        c.fillStyle = '#3a2419';
        c.fillRect(0, 0, w, h);
    }

    function paintLavaHeat(c, w, h, t, unit) {
        // Magma breathing out of phase under a crust that does not move. The
        // stillness is half the effect: crust that slid about would read as
        // fog, not as stone.
        // Dense enough that the pool comes out BRIGHTER than the floor around
        // it. Measured, not guessed: at nine sparse spots the crust won and
        // lava read darker than bare stone, which inverts the one thing that
        // makes it unmistakable across a room.
        const spots = Math.max(9, Math.min(40, Math.round((w * h) / (unit * unit * 1.4))));
        for (let i = 0; i < spots; i++) {
            const cx = terrainRandom(i * 3 + 1) * w;
            const cy = terrainRandom(i * 3 + 2) * h;
            const phase = terrainRandom(i * 7 + 5) * Math.PI * 2;
            const beat = Math.max(0.12, 0.62 + 0.3 * Math.sin(t * 0.00105 + phase)
                                             + 0.13 * Math.sin(t * 0.0026 + phase * 1.7));
            const radius = unit * (0.34 + terrainRandom(i * 3 + 3) * 0.72) * (0.72 + beat * 0.42);
            const hot = c.createRadialGradient(cx, cy, 0, cx, cy, radius);
            hot.addColorStop(0, 'rgba(255,222,158,' + (0.82 * beat).toFixed(3) + ')');
            hot.addColorStop(0.34, 'rgba(242,128,40,' + (0.58 * beat).toFixed(3) + ')');
            hot.addColorStop(1, 'rgba(150,32,10,0)');
            c.fillStyle = hot;
            c.beginPath();
            c.arc(cx, cy, radius, 0, Math.PI * 2);
            c.fill();
        }
    }

    function paintWaterBody(c, w, h) {
        // Deliberately greener and darker than drawTemplate's blue, or a burst
        // dropped on a lake is unreadable against it.
        c.fillStyle = '#2f6270';
        c.fillRect(0, 0, w, h);
        const depth = c.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
        depth.addColorStop(0, 'rgba(10,34,44,.5)');
        depth.addColorStop(1, 'rgba(10,34,44,0)');
        c.fillStyle = depth;
        c.fillRect(0, 0, w, h);
    }

    function paintWaterSurface(c, w, h, t, unit) {
        // One set of drifting bands is a gradient sliding about. Two, at
        // different angles and speeds, interfere -- and the bright knots where
        // they cross TRAVEL across the pool, which is what the eye reads as a
        // water surface. A standing wobble at the same frequency reads as heat
        // haze instead.
        const span = Math.hypot(w, h);
        for (let pass = 0; pass < 2; pass++) {
            const spacing = unit * (pass ? 0.44 : 0.62);
            const speed = pass ? 0.0092 : 0.0051;
            const peak = pass ? 0.3 : 0.42;
            c.save();
            c.translate(w / 2, h / 2);
            c.rotate(pass ? -0.58 : 0.4);
            const drift = (t * speed) % spacing;
            // Broken into segments along the band rather than drawn as one
            // continuous stripe. Full-width stripes came out as hard parallel
            // diagonals -- hatching, not water. Modulating ALONG the band as
            // well as across it leaves short bright knots that drift, which is
            // what a lit water surface actually looks like from above.
            const step = spacing * 1.6;
            for (let offset = -span / 2; offset < span / 2; offset += spacing) {
                const across = Math.max(0, Math.sin(offset / spacing * 0.9 + t * 0.0013));
                if (across <= 0.02) continue;
                for (let along = -span / 2; along < span / 2; along += step) {
                    const knot = Math.max(0, Math.sin(along / step * 1.7 + offset * 0.11 + t * 0.0021));
                    const alpha = across * knot * peak;
                    if (alpha <= 0.015) continue;
                    c.fillStyle = 'rgba(150,214,236,' + alpha.toFixed(3) + ')';
                    c.fillRect(along, offset + drift, step * 0.72, spacing * 0.4);
                }
            }
            c.restore();
        }
    }

    function paintPoison(c, w, h, t, unit) {
        c.fillStyle = 'rgba(66,102,36,.6)';
        c.fillRect(0, 0, w, h);
        // A slow roil so the surface between bubbles is not flat.
        const haze = c.createRadialGradient(
            w * (0.45 + 0.1 * Math.sin(t * 0.00035)), h * (0.5 + 0.1 * Math.cos(t * 0.00028)), 0,
            w * 0.5, h * 0.5, Math.max(w, h) * 0.7);
        haze.addColorStop(0, 'rgba(150,196,78,.2)');
        haze.addColorStop(1, 'rgba(150,196,78,0)');
        c.fillStyle = haze;
        c.fillRect(0, 0, w, h);
        // The strongest cue in the whole stage, because it is DISCRETE. From
        // across a room nobody resolves yellow-green from teal, but everybody
        // can see that small things are swelling and bursting in that patch --
        // and nothing else on the map does that.
        // Sized off the SQUARE, not off raw pixels. Measured at absolute sizes
        // the bubbles changed the image barely more than canvas noise did --
        // present in the code, invisible in the room, which is the exact
        // failure this cue exists to avoid.
        const count = Math.max(8, Math.min(38, Math.round((w * h) / (unit * unit * 0.9))));
        for (let i = 0; i < count; i++) {
            const cx = terrainRandom(i * 5 + 1) * w;
            const cy = terrainRandom(i * 5 + 2) * h;
            const period = 3200 + terrainRandom(i * 5 + 3) * 4400;
            const life = ((t + terrainRandom(i * 5 + 4) * period) % period) / period;
            const full = unit * (0.16 + terrainRandom(i * 5 + 6) * 0.2);
            c.lineWidth = Math.max(1.2, unit * 0.07);
            if (life < 0.82) {
                // A RING, not a disc: a disc is a dot, a ring is a bubble.
                const grow = life / 0.82;
                c.strokeStyle = 'rgba(206,240,146,' + (Math.sin(Math.PI * grow) * 0.6).toFixed(3) + ')';
                c.beginPath();
                c.arc(cx, cy, full * (0.25 + grow * 0.85), 0, Math.PI * 2);
                c.stroke();
            } else {
                const burst = (life - 0.82) / 0.18;
                c.strokeStyle = 'rgba(214,244,164,' + ((1 - burst) * 0.45).toFixed(3) + ')';
                c.beginPath();
                c.arc(cx, cy, full * (1 + burst * 1.6), 0, Math.PI * 2);
                c.stroke();
            }
        }
    }

    function paintBlood(c, w, h) {
        // Blood is the still one, and that is the point rather than a saving.
        // It must not ripple (that is water) and must not bubble (that is
        // poison); what makes a viewer read "blood" is a dark, glossy,
        // motionless patch. Stillness against three moving neighbours is itself
        // the signal -- and it means a scene whose only terrain is a blood pool
        // never starts the animation loop.
        c.fillStyle = '#4a0a0e';
        c.fillRect(0, 0, w, h);
        const depth = c.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.62);
        depth.addColorStop(0, 'rgba(22,2,5,.55)');
        depth.addColorStop(1, 'rgba(22,2,5,0)');
        c.fillStyle = depth;
        c.fillRect(0, 0, w, h);
        // One fixed gloss, so the pool looks wet rather than painted. It does
        // not travel: from above, a still surface's highlight only moves if the
        // light does.
        const gloss = c.createLinearGradient(w * 0.2, 0, w * 0.62, h * 0.5);
        gloss.addColorStop(0, 'rgba(196,74,64,0)');
        gloss.addColorStop(0.5, 'rgba(226,120,102,.3)');
        gloss.addColorStop(1, 'rgba(196,74,64,0)');
        c.fillStyle = gloss;
        c.fillRect(0, 0, w, h);
    }

    const TERRAIN_PASSES = {
        lava: [{op: 'multiply', alpha: 0.45, paint: paintLavaCrust},
               {op: 'screen', alpha: 1, paint: paintLavaHeat}],
        water: [{op: 'multiply', alpha: 0.6, paint: paintWaterBody},
                {op: 'screen', alpha: 0.5, paint: paintWaterSurface}],
        poison: [{op: 'source-over', alpha: 0.62, paint: paintPoison}],
        blood: [{op: 'multiply', alpha: 0.7, paint: paintBlood}]
    };

    function drawTerrain() {
        const layers = terrainLayers();
        if (!layers.length) return;
        // Static on the GM's view. Stage 6a made rendering event-driven and 6c
        // confined animation to the table screen; terrain still has to be
        // VISIBLE while prepping, it just does not move. One code path with a
        // frozen clock, the same shape as flickerFactor's early return, so the
        // two screens can never drift apart on how terrain looks.
        const t = isTableView() ? animationClock : 0;
        for (const layer of layers) {
            const entry = terrainEntry(layer);
            if (!entry) continue;
            const c = entry.sctx;
            for (const pass of TERRAIN_PASSES[layer.kind] || []) {
                c.setTransform(1, 0, 0, 1, 0, 0);
                c.globalCompositeOperation = 'source-over';
                c.globalAlpha = 1;
                c.clearRect(0, 0, entry.bw, entry.bh);
                pass.paint(c, entry.bw, entry.bh, t, entry.unit);
                // Clip the painted substance to the feathered pool outline.
                c.globalCompositeOperation = 'destination-in';
                c.globalAlpha = 1;
                c.drawImage(entry.mask, 0, 0);
                ctx.save();
                ctx.globalCompositeOperation = pass.op;
                ctx.globalAlpha = pass.alpha;
                ctx.drawImage(entry.scratch, 0, 0, entry.bw, entry.bh,
                              entry.x, entry.y, entry.w, entry.h);
                ctx.restore();
            }
        }
    }

    // Lava lights the room. It does NOT carve vision, for exactly the reason
    // stage 6c kept flicker out of lightRadii(): the vision mask is cached on a
    // signature, and feeding it something that moves either serves a stale mask
    // or misses the cache every frame and drags the whole raycast back into the
    // frame budget. A GM who wants lava to REVEAL an area places a light on it;
    // this only makes it glow.
    function drawTerrainGlow() {
        for (const layer of terrainLayers()) {
            if (layer.kind !== 'lava') continue;
            const entry = terrainEntry(layer);
            if (!entry || !entry.glow) continue;
            const t = isTableView() ? animationClock : 0;
            ctx.save();
            ctx.globalCompositeOperation = 'screen';
            ctx.globalAlpha = 0.3 * (0.74 + 0.26 * Math.sin(t * 0.0009));
            ctx.drawImage(entry.glow, 0, 0, entry.bw, entry.bh, entry.x, entry.y, entry.w, entry.h);
            ctx.restore();
        }
    }

    function blockingWalls() {
        return (scene.walls || []).filter(wall => wall.kind !== 'door' || !wall.open);
    }

    function raySegmentDistance(origin, angle, wall, maxDistance) {
        const dx = Math.cos(angle);
        const dy = Math.sin(angle);
        const sx = Number(wall.x2) - Number(wall.x1);
        const sy = Number(wall.y2) - Number(wall.y1);
        const denominator = dx * sy - dy * sx;
        if (Math.abs(denominator) < 1e-8) return null;
        const qx = Number(wall.x1) - origin.x;
        const qy = Number(wall.y1) - origin.y;
        const t = (qx * sy - qy * sx) / denominator;
        const u = (qx * dy - qy * dx) / denominator;
        if (t >= 0 && t <= maxDistance && u >= 0 && u <= 1) return t;
        return null;
    }

    function visibilityPolygon(source, radius) {
        const walls = blockingWalls();
        const angles = [];
        const rayCount = 160;
        for (let i = 0; i < rayCount; i++) angles.push((Math.PI * 2 * i) / rayCount);
        for (const wall of walls) {
            for (const endpoint of [{x: wall.x1, y: wall.y1}, {x: wall.x2, y: wall.y2}]) {
                const angle = Math.atan2(Number(endpoint.y) - source.y, Number(endpoint.x) - source.x);
                angles.push(angle - .0002, angle, angle + .0002);
            }
        }
        angles.sort((a, b) => a - b);
        return angles.map(angle => {
            let distance = radius;
            for (const wall of walls) {
                const hit = raySegmentDistance(source, angle, wall, radius);
                if (hit !== null && hit < distance) distance = hit;
            }
            return {x: source.x + Math.cos(angle) * distance, y: source.y + Math.sin(angle) * distance};
        });
    }

    function ownsTokenForVision(token) {
        // The table shows the UNION of the party's vision: one screen, one
        // answer, and no per-player state to maintain. Anything any party
        // member can see is revealed.
        if (isTableView()) return !!token.is_pc;
        if (cfg.characterId && (token.controller_character_id === cfg.characterId || token.character_id === cfg.characterId)) return true;
        return !!cfg.playerName && (token.controller_name === cfg.playerName || token.name === cfg.playerName);
    }

    function carveVisibility(maskContext, source, radius) {
        const polygon = visibilityPolygon(source, radius);
        if (!polygon.length) return;
        maskContext.beginPath();
        maskContext.moveTo(polygon[0].x, polygon[0].y);
        for (let i = 1; i < polygon.length; i++) maskContext.lineTo(polygon[i].x, polygon[i].y);
        maskContext.closePath();
        maskContext.fill();
    }

    // Two full-scene offscreen canvases were allocated EVERY frame -- about
    // 5 MB each at 1400x900, and scenes are now sized to their background
    // image, so a 2560x1440 map made that ~15 MB of garbage per frame while
    // dragging. They are reused instead, resized only when the scene is.
    const scratch = {};

    function scratchCanvas(key) {
        let entry = scratch[key];
        if (!entry) {
            entry = scratch[key] = {canvas: document.createElement('canvas')};
            entry.ctx = entry.canvas.getContext('2d');
        }
        if (entry.canvas.width !== canvas.width || entry.canvas.height !== canvas.height) {
            entry.canvas.width = canvas.width;
            entry.canvas.height = canvas.height;
        } else {
            entry.ctx.setTransform(1, 0, 0, 1, 0, 0);
            entry.ctx.globalCompositeOperation = 'source-over';
            entry.ctx.clearRect(0, 0, entry.canvas.width, entry.canvas.height);
        }
        return entry;
    }

    // The vision mask is expensive -- raycasting every source against every
    // wall -- and changes only when something that casts or blocks light moves.
    // Panning and zooming do not change it at all, and those are exactly the
    // gestures that used to recompute it dozens of times a second.
    let visionKey = null;

    function visionSignature() {
        const settings = scene.settings || {};
        const parts = [isTableView() ? 't' : 'g', settings.dynamic_lighting ? 1 : 0,
                       settings.default_vision, canvas.width, canvas.height];
        for (const w of scene.walls || []) {
            parts.push(w.x1, w.y1, w.x2, w.y2, w.kind, w.open ? 1 : 0);
        }
        for (const l of scene.lights || []) parts.push(l.x, l.y, l.radius);
        for (const t of scene.tokens || []) {
            if (ownsTokenForVision(t)) parts.push(t.id, t.x, t.y, t.vision_radius);
        }
        return parts.join('|');
    }

    function drawVisionOverlay() {
        if (!(scene.settings || {}).dynamic_lighting) return;
        const signature = visionSignature();
        const entry = scratch.vision;
        if (entry && visionKey === signature
            && entry.canvas.width === canvas.width && entry.canvas.height === canvas.height) {
            ctx.drawImage(entry.canvas, 0, 0);
            return;
        }
        const fresh = scratchCanvas('vision');
        visionKey = signature;
        const mask = fresh.canvas;
        const mctx = fresh.ctx;
        mctx.fillStyle = 'rgba(0,0,0,.96)';
        mctx.fillRect(0, 0, mask.width, mask.height);
        mctx.globalCompositeOperation = 'destination-out';
        mctx.fillStyle = '#000';
        for (const token of scene.tokens || []) {
            if (!ownsTokenForVision(token)) continue;
            carveVisibility(mctx, {x: Number(token.x), y: Number(token.y)},
                Math.max(0, Number(token.vision_radius) || Number(scene.settings.default_vision) || 700));
        }
        for (const light of scene.lights || []) {
            // Carve to the DIM radius: a creature can see by dim light, it just
            // sees worse. Carving only the bright zone made a torch reveal half
            // the area it should.
            carveVisibility(mctx, {x: Number(light.x), y: Number(light.y)},
                Math.max(0, lightRadii(light).dim));
        }
        mctx.globalCompositeOperation = 'source-over';
        ctx.drawImage(mask, 0, 0);
    }

    let fogMaskKey = null;
    let fogMaskFast = null;

    function drawFogOverlay() {
        if (!(scene.fog || {}).enabled) return;
        const geometry = gridGeometry();
        // The mask is rebuilt only when the fog, the view or the grid actually
        // changes, and blitted otherwise.
        //
        // Rebuilding it every frame was free while rendering was event-driven
        // (stage 6a): one draw, one blur, only on a change. Stage 6e breaks
        // that assumption -- terrain keeps the table screen's animation loop
        // running on scenes that never animated before, and a full-canvas
        // Gaussian at 60fps would be the most expensive thing on the map by a
        // wide margin. Same shape as the vision cache above: compare a
        // signature, blit on a hit, rebuild on a miss.
        //
        // The grid belongs in the key even though fog changes bump the
        // revision, because the calibration drag moves grid.offset_* locally
        // with no save at all.
        // Two levels, for the reason terrainEntry already documents and this
        // did not honour: scene.revision bumps on ANY save, a token move
        // included, so keying on it alone re-blurred the whole canvas every
        // time a creature took a step. Measured at 2560x1440 with 540 revealed
        // cells that was 2.7 ms on the first frame after every move -- never a
        // dropped frame on its own, but paid on the most frequent action in a
        // fight, and avoidable. The cheap key is checked first so the cell list
        // is only joined when the revision actually moved.
        const fast = [isTableView() ? 't' : 'g', scene.revision, geometry.size,
                      geometry.ox, geometry.oy, canvas.width, canvas.height].join('|');
        if (scratch.fog && fogMaskFast === fast) {
            ctx.drawImage(scratch.fog.canvas, 0, 0);
            return;
        }
        const fogState = scene.fog || {};
        const key = [fast.split('|')[0], geometry.size, geometry.ox, geometry.oy,
                     canvas.width, canvas.height,
                     (fogState.revealed_cells || []).length,
                     (fogState.revealed_cells || []).join(','),
                     (fogState.operations || []).length].join('|');
        if (scratch.fog && fogMaskKey === key) {
            fogMaskFast = fast;
            ctx.drawImage(scratch.fog.canvas, 0, 0);
            return;
        }
        const fogScratch = scratchCanvas('fog');
        fogMaskKey = key;
        fogMaskFast = fast;
        const mask = fogScratch.canvas;
        const mctx = fogScratch.ctx;
        const darkness = isTableView() ? .97 : .30;
        mctx.fillStyle = 'rgba(4,5,6,' + darkness + ')';
        mctx.fillRect(0, 0, mask.width, mask.height);
        // Revealed squares are punched out of the darkness. A cell set is
        // bounded by the grid, so this costs the same at the end of a session
        // as at the start -- unlike the arc log below, which only ever grew.
        const g = gridGeometry();
        mctx.globalCompositeOperation = 'destination-out';
        mctx.fillStyle = '#000';
        // Region reveal works in whole squares, so an explored area ends on a
        // hard stair-stepped edge that announces the grid. A blur on the
        // punch-out feathers the boundary into the dark instead. Applied to the
        // MASK, not the map, so nothing else on the canvas is softened.
        const feather = Math.max(6, g.size * 0.35);
        mctx.filter = 'blur(' + Math.round(feather) + 'px)';
        for (const key of scene.fog.revealed_cells || []) {
            const parts = String(key).split(',');
            const col = Number(parts[0]), row = Number(parts[1]);
            if (!Number.isFinite(col) || !Number.isFinite(row)) continue;
            mctx.fillRect(g.ox + col * g.size, g.oy + row * g.size, g.size, g.size);
        }
        mctx.filter = 'none';
        // Legacy brush strokes. Nothing writes these any more; they are replayed
        // so scenes fogged before region reveal do not suddenly go dark.
        for (const operation of scene.fog.operations || []) {
            mctx.globalCompositeOperation = operation.mode === 'hide' ? 'source-over' : 'destination-out';
            mctx.fillStyle = operation.mode === 'hide' ? 'rgba(4,5,6,' + darkness + ')' : '#000';
            mctx.beginPath();
            mctx.arc(Number(operation.x), Number(operation.y), Math.max(1, Number(operation.radius)), 0, Math.PI * 2);
            mctx.fill();
        }
        mctx.globalCompositeOperation = 'source-over';
        ctx.drawImage(mask, 0, 0);
    }

    function drawWallsAndDoors() {
        ctx.save();
        ctx.lineCap = 'round';
        for (const wall of scene.walls || []) {
            ctx.beginPath();
            ctx.moveTo(Number(wall.x1), Number(wall.y1));
            ctx.lineTo(Number(wall.x2), Number(wall.y2));
            if (wall.kind === 'door') {
                ctx.strokeStyle = wall.open ? '#58b37a' : (wall.secret ? '#bb87df' : '#d6a24d');
                ctx.lineWidth = 7;
                ctx.setLineDash(wall.open ? [12, 8] : []);
            } else {
                ctx.strokeStyle = '#7ea3bd';
                ctx.lineWidth = 5;
                ctx.setLineDash([]);
            }
            ctx.stroke();
            if (wall.kind === 'door') {
                const mx = (Number(wall.x1) + Number(wall.x2)) / 2;
                const my = (Number(wall.y1) + Number(wall.y2)) / 2;
                ctx.fillStyle = wall.open ? '#58b37a' : '#d6a24d';
                ctx.beginPath();
                ctx.arc(mx, my, 6, 0, Math.PI * 2);
                ctx.fill();
            }
        }
        ctx.restore();
    }

    function drawLightControls() {
        ctx.save();
        for (const light of scene.lights || []) {
            ctx.strokeStyle = light.color || '#ffd98a';
            ctx.lineWidth = 2;
            ctx.setLineDash([8, 6]);
            ctx.beginPath();
            ctx.arc(Number(light.x), Number(light.y), Number(light.radius) || 1, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = light.color || '#ffd98a';
            ctx.beginPath();
            ctx.arc(Number(light.x), Number(light.y), 7, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.restore();
    }

    function templateGeometry(template) {
        const x1 = Number(template.x1), y1 = Number(template.y1);
        const x2 = Number(template.x2), y2 = Number(template.y2);
        const radius = Number(template.radius) || 1;
        return {x1, y1, x2, y2, radius, angle: Math.atan2(y2 - y1, x2 - x1),
            length: Math.max(1, Math.hypot(x2 - x1, y2 - y1)), width: Number(template.width) || 35};
    }

    function drawTemplate(template) {
        const g = templateGeometry(template);
        ctx.save();
        ctx.fillStyle = 'rgba(94,164,201,.20)';
        ctx.strokeStyle = '#75c5eb';
        ctx.lineWidth = 3;
        if (template.kind === 'burst' || template.kind === 'emanation') {
            ctx.beginPath();
            ctx.arc(g.x1, g.y1, g.radius, 0, Math.PI * 2);
            ctx.fill(); ctx.stroke();
        } else if (template.kind === 'cone') {
            // A PF2e cone is a quarter circle: 45 degrees either side of the
            // aim vector. Keep in sync with the hit test in templateContainsToken.
            const spread = Math.PI / 4;
            ctx.beginPath();
            ctx.moveTo(g.x1, g.y1);
            ctx.arc(g.x1, g.y1, g.length, g.angle - spread, g.angle + spread);
            ctx.closePath();
            ctx.fill(); ctx.stroke();
        } else if (template.kind === 'line') {
            const nx = -Math.sin(g.angle) * g.width / 2;
            const ny = Math.cos(g.angle) * g.width / 2;
            ctx.beginPath();
            ctx.moveTo(g.x1 + nx, g.y1 + ny);
            ctx.lineTo(g.x2 + nx, g.y2 + ny);
            ctx.lineTo(g.x2 - nx, g.y2 - ny);
            ctx.lineTo(g.x1 - nx, g.y1 - ny);
            ctx.closePath();
            ctx.fill(); ctx.stroke();
        }
        ctx.restore();
    }

    function distanceToSegment(point, segment) {
        const dx = Number(segment.x2) - Number(segment.x1);
        const dy = Number(segment.y2) - Number(segment.y1);
        const lengthSquared = dx * dx + dy * dy;
        if (!lengthSquared) return Math.hypot(point.x - segment.x1, point.y - segment.y1);
        const t = Math.max(0, Math.min(1, ((point.x - segment.x1) * dx + (point.y - segment.y1) * dy) / lengthSquared));
        return Math.hypot(point.x - (Number(segment.x1) + t * dx), point.y - (Number(segment.y1) + t * dy));
    }

    // The squares a creature's space occupies: its centre plus the corners of
    // its footprint. PF2e affects a creature when the area overlaps ANY square
    // of its space, so a Large creature clipped at one corner is caught.
    //
    // This replaces a circular padding of tokenRadius(). A circle of radius
    // size*grid*0.42 is smaller than the square's half-diagonal (~0.707), so a
    // template grazing a corner missed -- exactly the case the GM has to
    // adjudicate out loud, and exactly the case footprints were made real for.
    function tokenSpacePoints(token) {
        const gridSize = Number((scene.grid || {}).size) || 70;
        const half = tokenFootprint(token) * gridSize / 2;
        const x = Number(token.x) || 0, y = Number(token.y) || 0;
        return [
            {x: x, y: y},
            {x: x - half, y: y - half}, {x: x + half, y: y - half},
            {x: x - half, y: y + half}, {x: x + half, y: y + half}
        ];
    }

    function templatePointInside(template, point) {
        const g = templateGeometry(template);
        if (template.kind === 'burst' || template.kind === 'emanation') {
            return Math.hypot(point.x - g.x1, point.y - g.y1) <= g.radius;
        }
        if (template.kind === 'line') {
            return distanceToSegment(point, template) <= g.width / 2;
        }
        if (template.kind === 'cone') {
            const dx = point.x - g.x1, dy = point.y - g.y1;
            const distance = Math.hypot(dx, dy);
            if (distance > g.length) return false;
            if (distance < 1e-6) return true;      // the origin square itself
            let difference = Math.atan2(dy, dx) - g.angle;
            difference = Math.atan2(Math.sin(difference), Math.cos(difference));
            return Math.abs(difference) <= Math.PI / 4;
        }
        return false;
    }

    function templateContainsToken(template, token) {
        // An emanation never catches the creature it radiates from.
        if (template.kind === 'emanation' && template.source_token_id === token.id) return false;
        return tokenSpacePoints(token).some(point => templatePointInside(template, point));
    }

    function drawTargetOverlays() {
        ctx.save();
        ctx.strokeStyle = '#ef5a52';
        ctx.lineWidth = 4;
        ctx.setLineDash([7, 5]);
        for (const token of scene.tokens || []) {
            if (!targetIds.has(token.id)) continue;
            ctx.beginPath();
            ctx.arc(Number(token.x), Number(token.y), tokenRadius(token) + 9, 0, Math.PI * 2);
            ctx.stroke();
        }
        ctx.restore();
    }

    function pf2eDistanceLabel(start, end) {
        const size = Number(scene.grid.size) || 70;
        const sx = Math.round(Math.abs(end.x - start.x) / size);
        const sy = Math.round(Math.abs(end.y - start.y) / size);
        const diagonal = Math.min(sx, sy);
        const straight = Math.max(sx, sy) - diagonal;
        const feet = Math.floor(diagonal / 2) * 15 + (diagonal % 2) * 5 + straight * 5;
        return feet + ' ft (' + Math.max(sx, sy) + ' squares)';
    }

    // Feet travelled while dragging, against the creature's Speed.
    //
    // The ruler existed, but measuring a move meant putting the token down,
    // switching tools, measuring, switching back. This shows it as you drag --
    // the actual question being asked ("can she reach him?") rather than a
    // generic distance.
    function drawMoveMeasure() {
        if (!interaction || interaction.type !== 'token') return;
        const token = interaction.token;
        if (!token) return;
        const from = {x: Number(interaction.fromX), y: Number(interaction.fromY)};
        const to = {x: Number(token.x), y: Number(token.y)};
        if (Math.hypot(to.x - from.x, to.y - from.y) < 2) return;
        const speed = Number((token.live || {}).speed) || 0;
        const label = pf2eDistanceLabel(from, to);
        const feet = parseInt(label, 10) || 0;
        // Over Speed is not illegal -- it is a second action, or a Stride and a
        // Step. Flag it rather than forbid it.
        const over = speed > 0 && feet > speed;
        // Mirror it to the table. This is the one the players asked for by
        // name: how far they could still travel, shown on the shared screen
        // rather than read out.
        shareMeasure(from, to, speed > 0 ? label + ' / Speed ' + speed : label, over);
        ctx.save();
        ctx.setLineDash([8, 6]);
        ctx.strokeStyle = over ? '#e9a13b' : '#f0d88a';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(from.x, from.y);
        ctx.lineTo(to.x, to.y);
        ctx.stroke();
        ctx.setLineDash([]);
        const text = speed > 0 ? label + '  /  Speed ' + speed + ' ft' : label;
        ctx.font = '700 16px ' + uiFont();
        const width = ctx.measureText(text).width + 16;
        const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2 - 18;
        ctx.fillStyle = 'rgba(10,10,12,.82)';
        ctx.fillRect(mx - width / 2, my - 15, width, 24);
        ctx.fillStyle = over ? '#e9a13b' : '#f4e7c0';
        ctx.textAlign = 'center';
        ctx.fillText(text, mx, my + 2);
        ctx.restore();
    }

    // Whose turn it is, stated plainly on the shared screen so nobody has to
    // ask. Drawn in scene coordinates and pinned to the visible viewport, so it
    // stays put while the map pans underneath it.
    // --- Animation -------------------------------------------------------
    //
    // Stage 6a made rendering event-driven: one frame, only when something
    // changes. Animation is the opposite, so it is confined to the TABLE
    // screen. The GM's working view stays static and cheap -- which is what was
    // asked for, and what keeps 6a's win intact on the screen being worked on.
    //
    // The loop also stops dead when nothing is animating, so a table showing a
    // lightless scene costs nothing either.
    let animationHandle = null;
    let animationClock = 0;

    // Tokens in flight: id -> {fromX, fromY, toX, toY, started}. The table
    // shows a creature SLIDE to its new square so the room can see who moved
    // and from where, instead of noticing it is suddenly somewhere else.
    const glides = new Map();
    const GLIDE_MS = 260;

    // Rising damage and healing numbers.
    //
    // Derived from a token's HP CHANGING rather than from the map's own combat
    // buttons, which is the more useful rule: damage rolled on the tracker, or
    // a player healing on their own sheet, floats on the table too. The map is
    // not the only thing that changes HP.
    const floaters = [];
    const FLOAT_MS = 1400;
    let lastHp = new Map();

    // --- pointing at things, across two screens ---------------------------
    //
    // The table screen is a SEPARATE BROWSER. A ruler dragged on the GM's
    // laptop is invisible to a window that has never heard of their pointer, so
    // "let the players see me measure that" is a broadcast problem, not a
    // rendering one. Both of these arrive over SSE and neither is ever stored:
    // a ping that survived a reload would be a mystery ring nobody remembers
    // drawing, and a ruler is only true while it is being held.
    const pings = [];
    const PING_MS = 1900;
    let sharedMeasure = null;
    const MEASURE_LINGER_MS = 4000;
    // Throttled because this rides the single gevent worker that also serves
    // every player's SSE. A drag fires at frame rate; the table does not need
    // 60 updates a second to follow a line.
    const MEASURE_MIN_GAP_MS = 90;
    let lastMeasureSentAt = 0;
    let lastMeasureKey = '';

    function sendBeacon(payload) {
        if (!cfg.isGm) return;
        payload.kind = payload.kind || 'ping';
        fetch('/api/scenes/' + encodeURIComponent(sceneId) + '/beacon', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(payload)
        }).catch(function () { /* a lost ping is not worth a toast */ });
    }

    function shareMeasure(start, end, label, over) {
        // Only the GM's own view broadcasts, and only when the line actually
        // changed -- otherwise a held-still pointer would stream duplicates.
        if (!cfg.isGm || isTableView()) return;
        const key = [Math.round(start.x), Math.round(start.y),
                     Math.round(end.x), Math.round(end.y), label].join(',');
        const now = Date.now();
        if (key === lastMeasureKey) return;
        if (now - lastMeasureSentAt < MEASURE_MIN_GAP_MS) return;
        lastMeasureKey = key;
        lastMeasureSentAt = now;
        sendBeacon({kind: 'measure', x1: start.x, y1: start.y, x2: end.x, y2: end.y,
                    label: label, over: !!over});
    }

    function clearSharedMeasure() {
        if (!cfg.isGm || isTableView()) return;
        if (!lastMeasureKey) return;
        lastMeasureKey = '';
        sendBeacon({kind: 'measure', clear: true});
    }

    function receiveBeacon(data) {
        if (!data || (data.scene_id && data.scene_id !== sceneId)) return;
        if (data.kind === 'ping') {
            pings.push({x: Number(data.x), y: Number(data.y), born: animationClock});
            // The GM's own view is event-driven, so a ping there needs the loop
            // started explicitly rather than waiting for a scene change.
            syncAnimation();
        } else if (data.kind === 'measure') {
            sharedMeasure = data.clear ? null : {
                start: {x: Number(data.x1), y: Number(data.y1)},
                end: {x: Number(data.x2), y: Number(data.y2)},
                label: String(data.label || ''), over: !!data.over,
                seen: Date.now()
            };
        }
        draw();
    }

    function drawPings() {
        if (!pings.length) return;
        const now = animationClock;
        for (let i = pings.length - 1; i >= 0; i--) {
            const ping = pings[i];
            const t = (now - ping.born) / PING_MS;
            if (t >= 1 || t < 0) { pings.splice(i, 1); continue; }
            // Three rings leaving at staggered times. One ring reads as a
            // circle drawn on the map; several leaving outward read as
            // somebody pointing.
            ctx.save();
            for (let ring = 0; ring < 3; ring++) {
                const phase = t * 1.5 - ring * 0.22;
                if (phase <= 0 || phase >= 1) continue;
                ctx.globalAlpha = (1 - phase) * 0.85;
                ctx.strokeStyle = '#ffd98a';
                ctx.lineWidth = 4 - phase * 2;
                ctx.beginPath();
                ctx.arc(ping.x, ping.y, 12 + phase * 70, 0, Math.PI * 2);
                ctx.stroke();
            }
            ctx.globalAlpha = Math.max(0, 1 - t * 1.6);
            ctx.fillStyle = '#ffd98a';
            ctx.beginPath();
            ctx.arc(ping.x, ping.y, 6, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }
    }

    // The GM's ruler, mirrored onto the TV. Drawn for the table only -- the GM
    // already sees their own line locally and at full fidelity, and echoing the
    // broadcast back over it would double-draw.
    function drawSharedMeasure() {
        if (!sharedMeasure || !isTableView()) return;
        if (Date.now() - sharedMeasure.seen > MEASURE_LINGER_MS) { sharedMeasure = null; return; }
        const {start, end, label, over} = sharedMeasure;
        ctx.save();
        ctx.setLineDash([10, 7]);
        ctx.strokeStyle = over ? '#e9a13b' : '#f0d88a';
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
        ctx.setLineDash([]);
        if (label) {
            // Sized for the room, like the nameplates, not for the GM's laptop.
            ctx.font = '700 26px ' + uiFont();
            const width = ctx.measureText(label).width + 20;
            const mx = (start.x + end.x) / 2, my = (start.y + end.y) / 2;
            ctx.fillStyle = 'rgba(12,11,9,.86)';
            ctx.fillRect(mx - width / 2, my - 20, width, 34);
            ctx.fillStyle = over ? '#e9a13b' : '#f4e6bb';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, mx, my - 3);
        }
        ctx.restore();
    }

    function noteMotionAndHealth(previous, next) {
        if (!isTableView()) { lastHp = new Map(); return; }
        const before = new Map((previous || []).map(t => [t.id, t]));
        for (const token of next || []) {
            const was = before.get(token.id);
            if (was && (Number(was.x) !== Number(token.x) || Number(was.y) !== Number(token.y))) {
                const inFlight = glides.get(token.id);
                glides.set(token.id, {
                    // Continue from where it is mid-glide, not from where it
                    // started, or a second move snaps backwards first.
                    fromX: inFlight ? inFlight.currentX : Number(was.x),
                    fromY: inFlight ? inFlight.currentY : Number(was.y),
                    toX: Number(token.x), toY: Number(token.y),
                    started: animationClock, currentX: Number(was.x), currentY: Number(was.y)
                });
            }
            const hp = token.live && token.live.current_hp;
            if (hp !== undefined && hp !== null) {
                const had = lastHp.get(token.id);
                if (had !== undefined && had !== hp) {
                    floaters.push({tokenId: token.id, delta: hp - had, started: animationClock});
                }
                lastHp.set(token.id, hp);
            }
        }
    }

    // Where to draw a token this frame: its real position, or somewhere along
    // its glide. Everything that positions a token goes through this.
    function tokenRenderPos(token) {
        const glide = glides.get(token.id);
        if (!glide) return {x: Number(token.x) || 0, y: Number(token.y) || 0};
        const t = Math.min(1, (animationClock - glide.started) / GLIDE_MS);
        // Ease-out: fast off the mark, settling into the square.
        const e = 1 - Math.pow(1 - t, 3);
        glide.currentX = glide.fromX + (glide.toX - glide.fromX) * e;
        glide.currentY = glide.fromY + (glide.toY - glide.fromY) * e;
        return {x: glide.currentX, y: glide.currentY};
    }

    // Finished glides are dropped once per frame rather than inside
    // tokenRenderPos, which is called more than once per token per frame (the
    // token itself, then any floater above it). Deleting from a read would make
    // the second call return a different answer than the first.
    function pruneGlides() {
        for (const [id, glide] of glides) {
            if (animationClock - glide.started >= GLIDE_MS) glides.delete(id);
        }
    }

    function drawFloaters() {
        if (!floaters.length) return;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.font = '700 26px ' + uiFont();
        for (let i = floaters.length - 1; i >= 0; i--) {
            const f = floaters[i];
            const t = (animationClock - f.started) / FLOAT_MS;
            if (t >= 1) { floaters.splice(i, 1); continue; }
            const token = (scene.tokens || []).find(x => x.id === f.tokenId);
            if (!token) { floaters.splice(i, 1); continue; }
            const at = tokenRenderPos(token);
            const healing = f.delta > 0;
            ctx.globalAlpha = 1 - t * t;
            ctx.fillStyle = healing ? '#7fd08a' : '#f08a7a';
            ctx.strokeStyle = 'rgba(0,0,0,.85)';
            ctx.lineWidth = 5;
            const text = (healing ? '+' : '') + f.delta;
            const y = at.y - tokenRadius(token) - 14 - t * 46;
            ctx.strokeText(text, at.x, y);
            ctx.fillText(text, at.x, y);
        }
        ctx.restore();
    }

    function animationsWanted() {
        if (!scene) return false;
        // A ping is the one animation the GM's own view runs too. It is the
        // confirmation that the thing they pointed at actually went out, and it
        // stops the moment the rings finish -- so 6a's event-driven idle holds
        // everywhere except for those two seconds.
        if (pings.length > 0) return true;
        if (!isTableView()) return false;
        return (scene.lights || []).length > 0 || glides.size > 0 || floaters.length > 0
               || terrainAnimates();
    }

    function stepAnimation(timestamp) {
        animationClock = timestamp;
        if (!animationsWanted()) { animationHandle = null; return; }
        renderScene();
        animationHandle = window.requestAnimationFrame(stepAnimation);
    }

    function syncAnimation() {
        if (animationsWanted()) {
            if (animationHandle === null) animationHandle = window.requestAnimationFrame(stepAnimation);
        } else if (animationHandle !== null) {
            window.cancelAnimationFrame(animationHandle);
            animationHandle = null;
        }
    }

    // A stable per-light phase so two torches in the same room do not flicker
    // in lockstep, which reads as a strobe rather than as fire.
    function lightPhase(light) {
        const id = String(light.id || '');
        let hash = 0;
        for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) % 100000;
        return hash / 100000 * Math.PI * 2;
    }

    // Flame wanders; it does not pulse on a sine. Two detuned waves keep it
    // from reading as a heartbeat.
    function flickerFactor(light) {
        if (!isTableView()) return 1;
        const t = animationClock / 1000;
        const phase = lightPhase(light);
        const wander = Math.sin(t * 5.3 + phase) * 0.5 + Math.sin(t * 8.9 + phase * 1.7) * 0.5;
        return 1 + wander * 0.06;
    }

    function drawTurnBanner() {
        if (!isTableView()) return;
        const active = (scene.tokens || []).find(t => t.live && t.live.is_active
                                                  && t.visible_to_players !== false);
        if (!active) return;
        const label = (active.name || 'Unknown') + "— it's their turn";
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        const left = viewport.scrollLeft / zoom;
        const top = viewport.scrollTop / zoom;
        const wide = viewport.clientWidth / zoom;
        ctx.font = '700 30px ' + uiFont();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const width = ctx.measureText(label).width + 56;
        const cx = left + wide / 2;
        const cy = top + 46;
        ctx.fillStyle = 'rgba(10,10,12,.82)';
        ctx.fillRect(cx - width / 2, cy - 26, width, 52);
        ctx.fillStyle = '#f0d88a';
        ctx.fillText(label, cx, cy);
        ctx.restore();
    }

    function drawToolOverlay() {
        drawMoveMeasure();
        // The wall run in progress, so the GM can see the room taking shape
        // before any of it is saved.
        if (wallChain.length) {
            ctx.save();
            ctx.strokeStyle = activeTool === 'door' ? 'rgba(224,182,90,.95)' : 'rgba(238,225,188,.95)';
            ctx.lineWidth = 4;
            ctx.setLineDash([10, 6]);
            ctx.beginPath();
            ctx.moveTo(wallChain[0].x, wallChain[0].y);
            for (const point of wallChain.slice(1)) ctx.lineTo(point.x, point.y);
            ctx.stroke();
            ctx.setLineDash([]);
            for (const point of wallChain) {
                ctx.beginPath();
                ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
                ctx.fillStyle = '#f0d88a';
                ctx.fill();
            }
            ctx.restore();
        }
        const draft = interaction && (interaction.type === 'tool' || interaction.type === 'fog') ? interaction : null;
        const ruler = draft && draft.tool === 'measure' ? draft : measurement;
        if (ruler) {
            ctx.save();
            ctx.strokeStyle = '#f0d88a';
            ctx.lineWidth = 4;
            ctx.setLineDash([10, 6]);
            ctx.beginPath(); ctx.moveTo(ruler.start.x, ruler.start.y); ctx.lineTo(ruler.end.x, ruler.end.y); ctx.stroke();
            const label = pf2eDistanceLabel(ruler.start, ruler.end);
            // The GM measuring something for the room is the point of the
            // ruler; the table sees the same line and the same number.
            shareMeasure(ruler.start, ruler.end, label, false);
            const mx = (ruler.start.x + ruler.end.x) / 2, my = (ruler.start.y + ruler.end.y) / 2;
            ctx.setLineDash([]); ctx.font = '700 14px ' + uiFont(); ctx.textAlign = 'center';
            ctx.fillStyle = 'rgba(0,0,0,.85)'; ctx.fillRect(mx - 65, my - 24, 130, 22);
            ctx.fillStyle = '#fff2bd'; ctx.fillText(label, mx, my - 8);
            ctx.restore();
        }
        if (draft && ['burst', 'emanation', 'cone', 'line'].includes(draft.tool)) drawTemplate(draftTemplate(draft));
        if (draft && (draft.tool === 'wall' || draft.tool === 'door')) {
            ctx.save(); ctx.strokeStyle = draft.tool === 'door' ? '#d6a24d' : '#7ea3bd'; ctx.lineWidth = 5;
            ctx.beginPath(); ctx.moveTo(draft.start.x, draft.start.y); ctx.lineTo(draft.end.x, draft.end.y); ctx.stroke(); ctx.restore();
        }
        if (draft && draft.type === 'fog') {
            ctx.save(); ctx.strokeStyle = draft.tool === 'fog-hide' ? '#cf554d' : '#75c5eb'; ctx.lineWidth = 2;
            for (const point of draft.points) { ctx.beginPath(); ctx.arc(point.x, point.y, draft.radius, 0, Math.PI * 2); ctx.stroke(); }
            ctx.restore();
        }
    }

    function tokenRadius(token) {
        return Math.max(12, (Number((scene.grid || {}).size) || 70) * (Number(token.size) || 1) * .42);
    }

    function getTokenImage(token) {
        if (!token.image) return null;
        const cached = tokenImages.get(token.image);
        if (cached) return cached.loaded ? cached.image : null;
        const entry = {image: new Image(), loaded: false};
        tokenImages.set(token.image, entry);
        entry.image.onload = function () { entry.loaded = true; draw(); };
        entry.image.onerror = function () { entry.failed = true; };
        entry.image.src = token.image;
        return null;
    }

    function drawCover(image, left, top, size, focus) {
        const scale = Math.max(size / image.naturalWidth, size / image.naturalHeight);
        const sourceW = size / scale;
        const sourceH = size / scale;
        const fx = Math.max(0, Math.min(100, Number((focus || {}).x) || 50)) / 100;
        const fy = Math.max(0, Math.min(100, Number((focus || {}).y) || 50)) / 100;
        const sx = Math.max(0, Math.min(image.naturalWidth - sourceW, image.naturalWidth * fx - sourceW / 2));
        const sy = Math.max(0, Math.min(image.naturalHeight - sourceH, image.naturalHeight * fy - sourceH / 2));
        ctx.drawImage(image, sx, sy, sourceW, sourceH, left, top, size, size);
    }

    function drawToken(token) {
        const at = tokenRenderPos(token);
        const x = at.x;
        const y = at.y;
        const radius = tokenRadius(token);
        const live = token.live || {};
        const portrait = getTokenImage(token);
        ctx.save();
        // A token hidden from players is ghosted rather than drawn normally, so
        // the GM can stage an ambush and still see at a glance which creatures
        // the table cannot. Without this the flag is invisible on the only
        // screen that renders it, and it is easy to narrate a monster the party
        // has not been shown.
        if (token.visible_to_players === false) ctx.globalAlpha = 0.45;
        if (live.is_active) {
            ctx.beginPath();
            ctx.arc(x, y, radius + 8, 0, Math.PI * 2);
            ctx.strokeStyle = '#f0d88a';
            ctx.lineWidth = 5;
            ctx.shadowColor = '#f0d88a';
            ctx.shadowBlur = 15;
            ctx.stroke();
            ctx.shadowBlur = 0;
        }

        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fillStyle = token.color || (token.is_pc ? '#4f8a62' : '#a84b45');
        ctx.fill();
        if (portrait) {
            ctx.save();
            ctx.beginPath();
            ctx.arc(x, y, radius - 3, 0, Math.PI * 2);
            ctx.clip();
            drawCover(portrait, x - radius + 3, y - radius + 3, (radius - 3) * 2, token.image_focus);
            ctx.restore();
        } else {
            const initials = String(token.name || '?').split(/\s+/).slice(0, 2).map(s => s[0] || '').join('').toUpperCase();
            ctx.fillStyle = '#fff8e7';
            ctx.font = '700 ' + Math.max(12, radius * .48) + 'px ' + uiFont();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(initials, x, y);
        }

        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.strokeStyle = token.id === selectedId ? '#ffffff' : (token.color || 'rgba(246,236,204,.86)');
        ctx.lineWidth = token.id === selectedId ? 4 : 3;
        ctx.stroke();

        // Party members get a second, inset ring. Faction was carried by fill
        // colour alone -- #4f8a62 against #a84b45, green against red, which is
        // the one axis a red-green colourblind viewer cannot use at all. It
        // marks the PCs rather than the NPCs because there are four of them and
        // a screen full of monsters, so the map stays quiet.
        if (token.is_pc) {
            ctx.beginPath();
            ctx.arc(x, y, Math.max(2, radius - 6), 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(248,240,214,.9)';
            ctx.lineWidth = 2;
            ctx.stroke();
        }

        ctx.textAlign = 'center';
        if (token.show_nameplate !== false) {
            // The table screen is read from several feet away, so names and the
            // health bar below are scaled up there. On the GM's own screen the
            // smaller type keeps a crowded fight legible.
            ctx.font = '700 ' + tableType(13) + 'px ' + uiFont();
            ctx.textBaseline = 'bottom';
            ctx.lineWidth = 4;
            ctx.strokeStyle = 'rgba(0,0,0,.8)';
            ctx.strokeText(token.name || 'Token', x, y - radius - 7);
            ctx.fillStyle = '#fff8e7';
            ctx.fillText(token.name || 'Token', x, y - radius - 7);
        }

        // Exact bar when we have numbers (the GM always; players for PCs, whose
        // health the party shares). Players get no NPC numbers at all -- the
        // server strips them -- so fall back to the same coarse Wounded/Dead
        // signal the tracker gives them rather than showing nothing.
        // The nameplate above was the ONLY thing the table view scaled, despite
        // its comment claiming the health bar came with it. So the room could
        // read who a token was but not that it was Frightened or Prone -- which
        // is the entire reason conditions are painted under the token instead
        // of left in the sidebar. Everything below the token scales together.
        const barWidth = radius * 1.7;
        const barLeft = x - barWidth / 2;
        const barTop = y + radius + 6;
        const barHeight = tableType(5);
        if (Number(live.max_hp) > 0) {
            const pct = Math.max(0, Math.min(1, Number(live.current_hp) / Number(live.max_hp)));
            ctx.fillStyle = 'rgba(0,0,0,.75)';
            ctx.fillRect(barLeft - 1, barTop - 1, barWidth + 2, barHeight + 2);
            ctx.fillStyle = pct > .5 ? brand('--success', '#4a7c2a')
                : pct > .25 ? brand('--warn', '#8a6a14') : brand('--danger', '#a83a3a');
            ctx.fillRect(barLeft, barTop, barWidth * pct, barHeight);
        } else if (live.hp_status) {
            const dead = live.hp_status === 'Dead';
            ctx.fillStyle = 'rgba(0,0,0,.75)';
            ctx.fillRect(barLeft - 1, barTop - 1, barWidth + 2, barHeight + 2);
            ctx.fillStyle = dead ? brand('--danger', '#a83a3a') : brand('--warn', '#8a6a14');
            ctx.fillRect(barLeft, barTop, dead ? barWidth : barWidth * .5, barHeight);
        }
        const conditions = Object.keys(live.conditions || {});
        if (conditions.length) {
            // Stacked rather than joined by ' / ' on the table: three condition
            // names at 17px on one line runs wider than the token and collides
            // with whatever is beside it.
            const names = conditions.slice(0, 3).map(k => k.replace(/_/g, ' '));
            const size = tableType(10);
            ctx.font = '600 ' + size + 'px ' + uiFont();
            ctx.textBaseline = 'top';
            ctx.lineWidth = 3;
            const lines = isTableView() ? names : [names.join(' / ')];
            let lineTop = y + radius + 9 + barHeight + 1;
            for (const line of lines) {
                ctx.strokeStyle = 'rgba(0,0,0,.85)';
                ctx.strokeText(line, x, lineTop);
                ctx.fillStyle = '#f3c6a7';
                ctx.fillText(line, x, lineTop);
                lineTop += size + 2;
            }
        }
        if (!isTableView() && token.visible_to_players === false) {
            ctx.setLineDash([5, 4]);
            ctx.beginPath();
            ctx.arc(x, y, radius + 4, 0, Math.PI * 2);
            ctx.strokeStyle = '#cf554d';
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.setLineDash([]);
        }
        if (token.locked) {
            ctx.fillStyle = 'rgba(15,13,10,.9)';
            ctx.fillRect(x + radius - 13, y - radius - 2, 22, 13);
            ctx.fillStyle = '#f0d88a';
            ctx.font = '700 7px ' + uiFont();
            ctx.textBaseline = 'middle';
            ctx.fillText('LOCK', x + radius - 2, y - radius + 5);
        }
        ctx.restore();
    }

    function pointFromEvent(event) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: (event.clientX - rect.left) * canvas.width / rect.width,
            y: (event.clientY - rect.top) * canvas.height / rect.height
        };
    }

    function hitToken(point) {
        const tokens = (scene.tokens || []).slice().reverse();
        return tokens.find(token => Math.hypot(point.x - token.x, point.y - token.y) <= tokenRadius(token)) || null;
    }

    function canControl(token) {
        if (token.locked) return false;
        if (cfg.isGm) return true;
        if (!(scene.settings || {}).player_movement) return false;
        if (cfg.characterId && (token.controller_character_id === cfg.characterId || token.character_id === cfg.characterId)) return true;
        return !!cfg.playerName && (token.controller_name === cfg.playerName || token.name === cfg.playerName);
    }

    function normalizedOffset(value, size) {
        return ((value % size) + size) % size;
    }

    // What this canvas is RENDERING, as distinct from who is allowed to touch
    // it. Vision and fog were gated on cfg.isGm, which conflated two different
    // questions: "may this person edit the scene" and "should this canvas show
    // only what the party can see". That is exactly why the GM could never
    // preview the table view -- the check deciding whether to draw vision was
    // the same check deciding whether to show the sidebar.
    //
    //   'gm'    everything visible; fog as a light haze so the boundary reads
    //   'table' what the players see: vision computed, fog opaque, hidden
    //           tokens absent, secret doors indistinguishable from walls
    //
    // cfg.isGm still governs every EDIT. Only rendering moved.
    // cfg.tableView is set by /map/table -- the shared screen. A GM window
    // still starts in 'gm' and only previews on request.
    let viewMode = (cfg.tableView || !cfg.isGm) ? 'table' : 'gm';
    function isTableView() { return viewMode === 'table'; }

    // One place to scale canvas type for the room. 13 -> 22 is the ratio the
    // nameplate already used, so nothing that was tuned by eye moves.
    function tableType(px) { return isTableView() ? Math.round(px * 1.7) : px; }

    // Is the GM sidebar actually in the DOM?
    //
    // Distinct from cfg.isGm, which only says the request was authenticated.
    // The table screen (stage 6b) is GM-authenticated AND has no controls, so
    // anything that reaches for a sidebar element has to ask this instead --
    // otherwise it finds null, throws, and aborts the rest of init.
    function hasGmChrome() { return !!cfg.isGm && !cfg.tableView; }

    // --- Region fog ---------------------------------------------------------
    //
    // Fog was a brush: painted arcs appended to an operation log, replayed in
    // full every frame and capped at 2000 entries. Round 5 chose reveal-by-room
    // instead, which is both fewer actions and a better fit for how dungeon
    // maps are drawn -- and it needs a different store. Revealed CELLS are
    // bounded by the grid, so the cost stops growing with how long the session
    // has run, and a room is one click rather than thirty brush strokes.
    //
    // The old operations are still rendered so existing scenes do not go dark;
    // nothing writes them any more.

    function gridGeometry() {
        const grid = scene.grid || {};
        return {
            size: Math.max(8, Number(grid.size) || 70),
            ox: Number(grid.offset_x) || 0,
            oy: Number(grid.offset_y) || 0
        };
    }

    function cellAt(point) {
        const g = gridGeometry();
        return {
            col: Math.floor((point.x - g.ox) / g.size),
            row: Math.floor((point.y - g.oy) / g.size)
        };
    }

    function segmentsCross(a, b, c, d) {
        const side = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
        const d1 = side(c, d, a), d2 = side(c, d, b);
        const d3 = side(a, b, c), d4 = side(a, b, d);
        return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
    }

    // Walls that stop sight AND movement of the reveal. An OPEN door stops
    // neither -- that is the whole point of opening it.
    function blockingWallList() {
        return (scene.walls || []).filter(w => w.kind !== 'door' || !w.open);
    }

    // Can the reveal step from one cell to its neighbour, or is a wall between?
    //
    // Tested CENTRE TO CENTRE, not along the shared edge, and that detail is
    // load-bearing. segmentsCross uses strict sign changes, so two collinear
    // segments never register as crossing -- and a wall snapped to the grid
    // (which is what 4a's snapping guarantees) lies exactly along the cell edge
    // it is supposed to block. Testing the edge therefore missed every
    // grid-aligned wall and the reveal flooded the whole map. A centre-to-centre
    // segment is perpendicular to such a wall, so the crossing is unambiguous.
    function edgeBlocked(col, row, dcol, drow, walls) {
        const g = gridGeometry();
        const from = {x: g.ox + (col + 0.5) * g.size, y: g.oy + (row + 0.5) * g.size};
        const to = {x: from.x + dcol * g.size, y: from.y + drow * g.size};
        const lo = {x: Math.min(from.x, to.x), y: Math.min(from.y, to.y)};
        const hi = {x: Math.max(from.x, to.x), y: Math.max(from.y, to.y)};
        for (const wall of walls) {
            const a = {x: Number(wall.x1), y: Number(wall.y1)};
            const b = {x: Number(wall.x2), y: Number(wall.y2)};
            // Cheap bounding-box reject before the cross-product test.
            if (Math.min(a.x, b.x) > hi.x || Math.max(a.x, b.x) < lo.x) continue;
            if (Math.min(a.y, b.y) > hi.y || Math.max(a.y, b.y) < lo.y) continue;
            if (segmentsCross(from, to, a, b)) return true;
        }
        return false;
    }

    // Every cell reachable from the clicked one without crossing a wall.
    // Bounded by the scene, so an unwalled map floods to its edges rather than
    // running away.
    function floodRegion(point) {
        const g = gridGeometry();
        const maxCol = Math.ceil((scene.width - g.ox) / g.size);
        const maxRow = Math.ceil((scene.height - g.oy) / g.size);
        const walls = blockingWallList();
        const start = cellAt(point);
        if (start.col < 0 || start.row < 0 || start.col >= maxCol || start.row >= maxRow) return [];
        const seen = new Set([start.col + ',' + start.row]);
        const queue = [start];
        const out = [];
        while (queue.length) {
            const cell = queue.shift();
            out.push(cell.col + ',' + cell.row);
            if (out.length > 20000) break;      // pathological grid; stop rather than hang
            for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
                const nc = cell.col + dc, nr = cell.row + dr;
                if (nc < 0 || nr < 0 || nc >= maxCol || nr >= maxRow) continue;
                const key = nc + ',' + nr;
                if (seen.has(key)) continue;
                if (edgeBlocked(cell.col, cell.row, dc, dr, walls)) continue;
                seen.add(key);
                queue.push({col: nc, row: nr});
            }
        }
        return out;
    }

    // A wall run being chained: the points clicked so far. Empty when idle.
    //
    // Walls used to be one disconnected drag per segment, each its own HTTP
    // round-trip. Closed rooms were tedious enough to skip -- and region fog is
    // only as good as wall completeness, since a one-pixel gap leaks the reveal
    // into the next room. Chaining makes a closed room the easy thing to draw.
    let wallChain = [];

    // Wall ends snap to grid INTERSECTIONS, not cell centres: a wall runs along
    // the edge of a square, not through the middle of one. This is the opposite
    // of tokens, and deliberately so.
    function snapToIntersection(point) {
        const grid = scene.grid || {};
        const size = Number(grid.size) || 70;
        const ox = Number(grid.offset_x) || 0;
        const oy = Number(grid.offset_y) || 0;
        if (!(scene.settings || {}).snap_to_grid) return {x: point.x, y: point.y};
        return {
            x: Math.round((point.x - ox) / size) * size + ox,
            y: Math.round((point.y - oy) / size) * size + oy
        };
    }

    async function commitWallChain(kind) {
        const points = wallChain;
        wallChain = [];
        if (points.length < 2) { draw(); return; }
        const segments = [];
        for (let i = 1; i < points.length; i += 1) {
            segments.push({x1: points[i - 1].x, y1: points[i - 1].y,
                           x2: points[i].x, y2: points[i].y});
        }
        try {
            const data = await mapElementAction({
                action: 'add_walls', kind: kind, segments: segments,
                secret: kind === 'door' && document.getElementById('map-secret-door').checked
            });
            applyScene(data.scene);
            toast(segments.length === 1 ? 'Wall added.' : segments.length + ' wall segments added.');
        } catch (error) { toast(error.message, true); fetchScene(); }
    }

    // How many cells across a creature occupies. PF2e: Medium and smaller take
    // one square, Large two, Huge three, Gargantuan four.
    function tokenFootprint(token) {
        return Math.max(1, Math.round(Number(token && token.size) || 1));
    }

    // Snap a token's CENTRE so its footprint lands on cells.
    //
    // Snapping used to round the centre to the nearest gridline intersection,
    // which put every Medium token on a corner straddling four squares -- the
    // one thing snapping exists to prevent. Where the centre belongs depends on
    // the footprint: an odd number of cells centres in the middle of a cell, an
    // even number centres ON a line, because a 2x2 creature genuinely straddles
    // it. The half-cell shift below is the whole difference.
    function snapPoint(point, token) {
        const grid = scene.grid || {};
        const size = Number(grid.size) || 70;
        const ox = Number(grid.offset_x) || 0;
        const oy = Number(grid.offset_y) || 0;
        const half = (tokenFootprint(token) % 2 === 1) ? size / 2 : 0;
        return {
            x: Math.round((point.x - ox - half) / size) * size + ox + half,
            y: Math.round((point.y - oy - half) / size) * size + oy + half
        };
    }

    // --- undo -------------------------------------------------------------
    //
    // An INVERSE, not a restore: every entry is a list of ordinary map actions
    // sent back through the same endpoints, so undo can never write a shape the
    // normal path would have rejected. That is the rule this project already
    // settled for the Obsidian pane's undo, and it holds for the same reason --
    // a second write path is a second set of bugs.
    //
    // The inverse is computed by DIFFING the scene before and after, rather than
    // from the request that was sent. Painting lava over water changes cells the
    // request never named, and only the diff knows what was underneath.
    const undoStack = [];
    const UNDO_LIMIT = 40;
    let undoing = false;

    function undoSnapshot(source) {
        const fog = source.fog || {};
        return {
            walls: JSON.parse(JSON.stringify(source.walls || [])),
            lights: JSON.parse(JSON.stringify(source.lights || [])),
            templates: JSON.parse(JSON.stringify(source.templates || [])),
            revealed: (fog.revealed_cells || []).slice(),
            terrain: JSON.parse(JSON.stringify(source.terrain || []))
        };
    }

    function terrainOwners(snapshot) {
        const owners = new Map();
        for (const layer of snapshot.terrain) {
            for (const cell of layer.cells || []) owners.set(cell, layer.kind);
        }
        return owners;
    }

    function inverseOps(before, after) {
        const ops = [];
        const byId = list => new Map(list.map(item => [item.id, item]));

        const wallsBefore = byId(before.walls), wallsAfter = byId(after.walls);
        for (const id of wallsAfter.keys()) {
            if (!wallsBefore.has(id)) ops.push({action: 'delete_wall', id: id});
        }
        for (const [id, wall] of wallsBefore) {
            const now = wallsAfter.get(id);
            if (!now) {
                // Re-adding mints a NEW id, so an erased wall comes back in the
                // right place but not with its old identity. Harmless here; it
                // is why a failed op clears the stack rather than trying to
                // carry on with ids that no longer exist.
                ops.push({action: 'add_wall', restore_id: id,
                          x1: wall.x1, y1: wall.y1, x2: wall.x2, y2: wall.y2,
                          kind: wall.kind, secret: !!wall.secret});
            } else if (!!now.open !== !!wall.open) {
                ops.push({action: 'toggle_door', id: id});
            }
        }

        const lightsBefore = byId(before.lights), lightsAfter = byId(after.lights);
        for (const id of lightsAfter.keys()) {
            if (!lightsBefore.has(id)) ops.push({action: 'delete_light', id: id});
        }
        for (const [id, light] of lightsBefore) {
            const now = lightsAfter.get(id);
            if (!now) {
                ops.push({action: 'add_light', restore_id: id,
                          x: light.x, y: light.y, radius: light.radius,
                          color: light.color, intensity: light.intensity,
                          visible_to_players: light.visible_to_players !== false});
            } else if (now.radius !== light.radius || now.color !== light.color
                       || now.intensity !== light.intensity
                       || now.visible_to_players !== light.visible_to_players) {
                ops.push({action: 'update_light', id: id, radius: light.radius, color: light.color,
                          intensity: light.intensity,
                          visible_to_players: light.visible_to_players !== false});
            }
        }

        const templatesBefore = byId(before.templates), templatesAfter = byId(after.templates);
        for (const id of templatesAfter.keys()) {
            if (!templatesBefore.has(id)) ops.push({action: 'delete_template', id: id});
        }
        for (const [id, item] of templatesBefore) {
            if (templatesAfter.has(id)) continue;
            const restore = {action: 'add_template', restore_id: id,
                             kind: item.kind, x1: item.x1, y1: item.y1,
                             x2: item.x2, y2: item.y2, radius: item.radius, width: item.width,
                             visible_to_players: item.visible_to_players !== false};
            if (item.source_token_id) restore.source_token_id = item.source_token_id;
            ops.push(restore);
        }

        const revealedBefore = new Set(before.revealed), revealedAfter = new Set(after.revealed);
        const toHide = [...revealedAfter].filter(cell => !revealedBefore.has(cell));
        const toReveal = [...revealedBefore].filter(cell => !revealedAfter.has(cell));
        if (toHide.length) ops.push({action: 'fog_region', mode: 'hide', cells: toHide});
        if (toReveal.length) ops.push({action: 'fog_region', mode: 'reveal', cells: toReveal});

        // Terrain is per CELL, not per layer: repainting a room can strip cells
        // out of another substance, so the inverse is "put each changed square
        // back to whatever owned it before" -- including nothing.
        const ownersBefore = terrainOwners(before), ownersAfter = terrainOwners(after);
        const touched = new Set([...ownersBefore.keys(), ...ownersAfter.keys()]);
        const restoreByKind = new Map();
        const toDrain = [];
        for (const cell of touched) {
            const was = ownersBefore.get(cell) || null;
            if (was === (ownersAfter.get(cell) || null)) continue;
            if (was === null) { toDrain.push(cell); continue; }
            if (!restoreByKind.has(was)) restoreByKind.set(was, []);
            restoreByKind.get(was).push(cell);
        }
        // Drain first: repainting a cell already claims it away from other
        // kinds, but a cell that should end up EMPTY has to be cleared
        // explicitly or it keeps whatever the forward action gave it.
        if (toDrain.length) ops.push({action: 'paint_terrain', mode: 'clear', cells: toDrain});
        for (const [kind, cells] of restoreByKind) {
            ops.push({action: 'paint_terrain', mode: 'paint', kind: kind, cells: cells});
        }
        return ops;
    }

    const UNDO_LABELS = {
        add_wall: 'wall', add_walls: 'wall run', toggle_door: 'door', delete_wall: 'erased wall',
        add_light: 'light', update_light: 'light change', delete_light: 'erased light',
        fog_reset: 'fog reset', add_template: 'template', delete_template: 'erased template',
        clear_templates: 'cleared templates', clear_terrain: 'drained terrain'
    };

    function describeAction(body) {
        if (body.action === 'fog_region') return body.mode === 'hide' ? 'hidden area' : 'revealed area';
        if (body.action === 'paint_terrain') {
            return body.mode === 'clear' ? 'drained squares' : ('flooded ' + body.kind);
        }
        return UNDO_LABELS[body.action] || body.action;
    }

    function pushUndo(label, ops) {
        if (!ops.length) return;
        undoStack.push({label: label, ops: ops});
        if (undoStack.length > UNDO_LIMIT) undoStack.shift();
        updateUndoButton();
    }

    function mapElementAction(body) {
        const before = scene && !undoing ? undoSnapshot(scene) : null;
        return request('/api/scenes/' + encodeURIComponent(sceneId) + '/elements', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(body)
        }).then(function (data) {
            if (before && data && data.scene) {
                pushUndo(describeAction(body), inverseOps(before, undoSnapshot(data.scene)));
            }
            return data;
        });
    }

    function draftTemplate(draft) {
        const gridSize = Number(scene.grid.size) || 70;
        const sizeInput = document.getElementById('map-template-size');
        const squares = Math.max(1, Number(sizeInput ? sizeInput.value : 3) || 3);
        const radius = squares * gridSize;
        // The number is the size, for every shape. Cone and line took their
        // extent from how far you happened to drag, so "20 feet" meant two
        // different things depending on which tool was selected -- and PF2e
        // states every area in feet. The drag now only AIMS them.
        let end = draft.end || draft.start;
        if (draft.tool === 'burst' || draft.tool === 'emanation') {
            end = draft.start;
        } else {
            const aim = Math.atan2(end.y - draft.start.y, end.x - draft.start.x);
            const aimed = Math.hypot(end.x - draft.start.x, end.y - draft.start.y) >= 5 ? aim : 0;
            end = {x: draft.start.x + Math.cos(aimed) * radius,
                   y: draft.start.y + Math.sin(aimed) * radius};
        }
        const template = {
            kind: draft.tool, x1: draft.start.x, y1: draft.start.y,
            x2: end.x, y2: end.y, radius: radius, width: gridSize,
            visible_to_players: true
        };
        // An emanation radiates from a creature's whole space, a burst from a
        // point. They were rendered and hit-tested identically, so two toolbar
        // buttons did one thing. Linking the source token is what makes them
        // differ: the emanation grows by that creature's footprint.
        if (draft.tool === 'emanation') {
            const source = hitToken(draft.start);
            if (source) {
                template.source_token_id = source.id;
                template.x1 = Number(source.x);
                template.y1 = Number(source.y);
                template.x2 = template.x1;
                template.y2 = template.y1;
                template.radius = radius + tokenFootprint(source) * gridSize / 2;
            }
        }
        return template;
    }

    function selectTemplateTargets(template) {
        targetIds.clear();
        for (const token of scene.tokens || []) {
            if (templateContainsToken(template, token)) targetIds.add(token.id);
        }
        updateTargetPanel();
    }

    function toggleTarget(token) {
        if (!token) return;
        if (targetIds.has(token.id)) targetIds.delete(token.id);
        else targetIds.add(token.id);
        updateTargetPanel();
        draw();
    }

    function updateTargetPanel() {
        const count = document.getElementById('map-target-count');
        if (count) count.textContent = targetIds.size + ' selected';
        const toolClear = document.getElementById('map-clear-targets-tool');
        if (toolClear) toolClear.disabled = targetIds.size === 0;
    }

    function nearestWall(point, onlyDoors) {
        let best = null, bestDistance = 18 / zoom;
        for (const wall of scene.walls || []) {
            if (onlyDoors && wall.kind !== 'door') continue;
            const distance = distanceToSegment(point, wall);
            if (distance < bestDistance) { best = wall; bestDistance = distance; }
        }
        return best;
    }

    function nearestElement(point) {
        const wall = nearestWall(point, false);
        if (wall) return {kind: 'wall', item: wall};
        let best = null, bestDistance = 18 / zoom;
        for (const light of scene.lights || []) {
            const distance = Math.hypot(point.x - light.x, point.y - light.y);
            if (distance < bestDistance) { best = {kind: 'light', item: light}; bestDistance = distance; }
        }
        for (const template of scene.templates || []) {
            const distance = template.kind === 'line'
                ? distanceToSegment(point, template)
                : Math.abs(Math.hypot(point.x - template.x1, point.y - template.y1) - Number(template.radius || 0));
            if (distance < bestDistance) { best = {kind: 'template', item: template}; bestDistance = distance; }
        }
        return best;
    }

    async function eraseElementAt(point) {
        const found = nearestElement(point);
        if (!found) { toast('No wall, door, light, or template at that point.'); return; }
        const action = found.kind === 'wall' ? 'delete_wall' : found.kind === 'light' ? 'delete_light' : 'delete_template';
        try {
            const data = await mapElementAction({action: action, id: found.item.id});
            applyScene(data.scene);
        } catch (error) { toast(error.message, true); }
    }

    canvas.addEventListener('pointerdown', function (event) {
        if (event.button !== 0 && event.button !== 1) return;
        const point = pointFromEvent(event);
        if (event.button === 1) {
            interaction = {type: 'pan', clientX: event.clientX, clientY: event.clientY,
                scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop};
            canvas.classList.add('is-panning');
            canvas.setPointerCapture(event.pointerId);
            return;
        }
        // Point at something for the room. Placed before every other branch so
        // it works whatever tool is armed -- pointing is the thing a GM does
        // mid-sentence, and having to disarm the wall tool first means not
        // doing it.
        // Terrain already claims Alt-click for "paint one square", so the
        // shortcut stands down while a terrain tool is armed rather than
        // silently stealing it.
        if (activeTool === 'ping'
            || (event.altKey && cfg.isGm && !activeTool.startsWith('terrain-'))) {
            sendBeacon({kind: 'ping', x: point.x, y: point.y});
            return;
        }
        if (calibrationMode && cfg.isGm) {
            interaction = {
                type: 'grid', startPoint: point,
                offsetX: Number(scene.grid.offset_x) || 0,
                offsetY: Number(scene.grid.offset_y) || 0
            };
            canvas.setPointerCapture(event.pointerId);
            return;
        }
        // Clicking a light with Select opens it for editing. Lights could only
        // be placed and deleted, so changing a torch's radius meant erasing and
        // re-placing it.
        if (activeTool === 'select' && cfg.isGm) {
            const light = nearestLight(point);
            if (light && !hitToken(point)) {
                selectedLightId = light.id;
                paintLightPanel();
                draw();
                return;
            }
            if (selectedLightId) { selectedLightId = null; paintLightPanel(); }
        }
        const token = hitToken(point);
        if (activeTool === 'target') {
            toggleTarget(token);
            return;
        }
        if (activeTool === 'erase' && cfg.isGm) {
            eraseElementAt(point);
            return;
        }
        if (activeTool === 'door' && cfg.isGm && !wallChain.length) {
            const existingDoor = nearestWall(point, true);
            if (existingDoor) {
                mapElementAction({action: 'toggle_door', id: existingDoor.id})
                    .then(data => applyScene(data.scene)).catch(error => toast(error.message, true));
                return;
            }
        }
        if ((activeTool === 'wall' || activeTool === 'door') && cfg.isGm) {
            // Click to start, click again for each corner, Escape or a click on
            // the last point to finish. No drag: a room is a sequence of
            // corners, and dragging each edge separately is what made closed
            // rooms tedious.
            const snapped = snapToIntersection(point);
            const last = wallChain[wallChain.length - 1];
            const first = wallChain[0];
            // Clicking the last point again finishes an open run.
            if (last && Math.hypot(snapped.x - last.x, snapped.y - last.y) < 4) {
                commitWallChain(activeTool);
                return;
            }
            // Clicking the FIRST point closes the loop and finishes -- the
            // gesture every polygon tool uses, and the one that produces the
            // sealed rooms region fog depends on. Without it the GM has to
            // click the start and then press Escape, and a room that merely
            // looks closed leaks the reveal into the next one.
            if (first && wallChain.length >= 3
                && Math.hypot(snapped.x - first.x, snapped.y - first.y) < 4) {
                wallChain.push({x: first.x, y: first.y});
                commitWallChain(activeTool);
                return;
            }
            wallChain.push(snapped);
            draw();
            return;
        }
        if ((activeTool === 'fog-reveal' || activeTool === 'fog-hide') && cfg.isGm) {
            // One click reveals or hides a whole enclosed area. This is why
            // wall chaining had to land first: the flood stops at walls, so a
            // room with a one-pixel gap leaks into the next one.
            const cells = floodRegion(point);
            if (!cells.length) return;
            mapElementAction({
                action: 'fog_region',
                mode: activeTool === 'fog-hide' ? 'hide' : 'reveal',
                cells: cells
            }).then(data => {
                applyScene(data.scene);
                toast((activeTool === 'fog-hide' ? 'Hid ' : 'Revealed ') + cells.length
                      + (cells.length === 1 ? ' square.' : ' squares.'));
            }).catch(error => toast(error.message, true));
            return;
        }
        if (activeTool.startsWith('terrain-') && cfg.isGm) {
            // Same one-click-floods-a-room interaction as fog, for the same
            // reason: painting a lake square by square is tedious enough that
            // the GM would simply not do it, and an unused feature adds no
            // atmosphere at all.
            //
            // Alt-click paints the single square instead. Flooding a room is
            // right for water, poison and lava and wrong for blood, which is a
            // spill -- and a spill wants one or two squares by the body, not
            // the whole chamber.
            const cell = cellAt(point);
            const cells = event.altKey ? [cell.col + ',' + cell.row] : floodRegion(point);
            if (!cells.length) return;
            const kind = activeTool.slice('terrain-'.length);
            const clearing = kind === 'clear';
            mapElementAction({
                action: 'paint_terrain',
                mode: clearing ? 'clear' : 'paint',
                kind: clearing ? '' : kind,
                cells: cells
            }).then(data => {
                applyScene(data.scene);
                toast((clearing ? 'Drained ' : 'Flooded ') + cells.length
                      + (cells.length === 1 ? ' square.' : ' squares.'));
            }).catch(error => toast(error.message, true));
            return;
        }
        if (['measure', 'burst', 'emanation', 'cone', 'line', 'wall', 'door', 'light'].includes(activeTool)) {
            interaction = {type: 'tool', tool: activeTool, start: point, end: point};
            canvas.setPointerCapture(event.pointerId);
            draw();
            return;
        }
        if (event.shiftKey && token) {
            toggleTarget(token);
            return;
        }
        // Selecting a different token abandons any unsaved edit to the previous
        // one, so its dirty marks must go too -- otherwise they would block the
        // new token's values from ever painting into those fields.
        if (selectedId !== (token ? token.id : null)) clearDirty(document.getElementById('map-token-actions'));
        selectedId = token ? token.id : null;
        updateSelectionPanel();
        draw();
        if (token && canControl(token) && event.button === 0) {
            interaction = {
                type: 'token', token: token, dx: point.x - token.x, dy: point.y - token.y,
                fromX: Number(token.x), fromY: Number(token.y)
            };
            canvas.classList.add('is-dragging');
        } else if (!token || event.button === 1) {
            interaction = {
                type: 'pan', clientX: event.clientX, clientY: event.clientY,
                scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop
            };
            canvas.classList.add('is-panning');
        }
        if (interaction) canvas.setPointerCapture(event.pointerId);
    });

    canvas.addEventListener('pointermove', function (event) {
        if (!interaction) return;
        if (interaction.type === 'pan') {
            viewport.scrollLeft = interaction.scrollLeft - (event.clientX - interaction.clientX);
            viewport.scrollTop = interaction.scrollTop - (event.clientY - interaction.clientY);
            return;
        }
        const point = pointFromEvent(event);
        if (interaction.type === 'grid') {
            const derived = calibrationFromDrag(interaction.startPoint, point);
            scene.grid.size = derived.size;
            scene.grid.offset_x = derived.offsetX;
            scene.grid.offset_y = derived.offsetY;
            fillGridControls();
            const sizeField = document.getElementById('map-grid-size');
            if (sizeField && !isBeingEdited(sizeField)) sizeField.value = derived.size;
            draw();
            return;
        }
        if (interaction.type === 'tool') {
            interaction.end = point;
            draw();
            return;
        }
        if (interaction.type === 'fog') {
            const previous = interaction.points[interaction.points.length - 1];
            if (Math.hypot(point.x - previous.x, point.y - previous.y) >= interaction.radius * .35) {
                interaction.points.push(point);
                draw();
            }
            return;
        }
        if (interaction.type === 'token') {
            interaction.token.x = Math.max(0, Math.min(scene.width, point.x - interaction.dx));
            interaction.token.y = Math.max(0, Math.min(scene.height, point.y - interaction.dy));
            draw();
        }
    });

    canvas.addEventListener('pointerup', finishInteraction);
    canvas.addEventListener('pointercancel', function () {
        interaction = null;
        canvas.classList.remove('is-dragging', 'is-panning');
        fetchScene();
    });

    async function finishInteraction() {
        if (!interaction) return;
        const finished = interaction;
        interaction = null;
        canvas.classList.remove('is-dragging', 'is-panning');
        if (finished.type === 'pan') return;
        if (finished.type === 'fog') {
            try {
                const operations = finished.points.map(point => ({
                    mode: finished.tool === 'fog-hide' ? 'hide' : 'reveal',
                    x: point.x, y: point.y, radius: finished.radius
                }));
                const data = await mapElementAction({action: 'fog_ops', operations: operations});
                applyScene(data.scene);
            } catch (error) { toast(error.message, true); }
            return;
        }
        if (finished.type === 'tool') {
            if (finished.tool === 'measure') {
                measurement = finished;
                draw();
                return;
            }
            if (finished.tool === 'light' && cfg.isGm) {
                try {
                    const data = await mapElementAction({
                        action: 'add_light', x: finished.start.x, y: finished.start.y,
                        radius: document.getElementById('map-light-radius').value,
                        color: document.getElementById('map-light-color').value,
                        intensity: .75, visible_to_players: true
                    });
                    applyScene(data.scene);
                } catch (error) { toast(error.message, true); }
                return;
            }
            if (['burst', 'emanation', 'cone', 'line'].includes(finished.tool)) {
                // draftTemplate already projects cone/line to the stated size
                // and defaults their aim, so no short-drag fallback is needed.
                const template = draftTemplate(finished);
                selectTemplateTargets(template);
                if (cfg.isGm) {
                    try {
                        const data = await mapElementAction(Object.assign({action: 'add_template'}, template));
                        applyScene(data.scene);
                    } catch (error) { toast(error.message, true); }
                } else {
                    localTemplates.push(template);
                    draw();
                }
                return;
            }
            return;
        }
        if (finished.type === 'grid') {
            try {
                const data = await patchScene({grid: {
                    size: scene.grid.size,
                    offset_x: scene.grid.offset_x, offset_y: scene.grid.offset_y
                }});
                applyScene(data.scene);
                toast('Grid aligned: ' + Math.round(Number(data.scene.grid.size)) + 'px squares.');
            } catch (error) { toast(error.message, true); fetchScene(); }
            return;
        }
        const token = finished.token;
        if ((scene.settings || {}).snap_to_grid) {
            const snapped = snapPoint(token, token);   // its own size decides where it lands
            token.x = Math.max(0, Math.min(scene.width, snapped.x));
            token.y = Math.max(0, Math.min(scene.height, snapped.y));
        }
        draw();
        if (token.x === finished.fromX && token.y === finished.fromY) return;
        try {
            const data = await patchToken(token.id, {x: token.x, y: token.y});
            if (!undoing) {
                undoStack.push({label: 'move' + (token.name ? ' ' + token.name : ''),
                                move: {tokenId: token.id, x: finished.fromX, y: finished.fromY}});
                if (undoStack.length > UNDO_LIMIT) undoStack.shift();
                updateUndoButton();
            }
            applyScene(data.scene);
        } catch (error) {
            toast(error.message, true);
            fetchScene();
        }
    }

    function fillGridControls() {
        const x = document.getElementById('map-grid-offset-x');
        const y = document.getElementById('map-grid-offset-y');
        if (x && !isBeingEdited(x)) x.value = Math.round((Number(scene.grid.offset_x) || 0) * 100) / 100;
        if (y && !isBeingEdited(y)) y.value = Math.round((Number(scene.grid.offset_y) || 0) * 100) / 100;
    }

    // An inbound scene_update repaints the whole sidebar. That is right for
    // fields the GM is not touching and destructive for the one they are: type
    // half a scene name, have any other client nudge a token, and the frame
    // overwrites it mid-word. Two things count as "being edited" -- the field
    // that currently has focus, and one that was typed into and left without
    // saving (tab away to set something else, come back to a reverted value).
    function isBeingEdited(el) {
        if (!el) return false;
        if (el === document.activeElement) return true;
        return !!(el.dataset && el.dataset.mapDirty === '1');
    }

    // Mark on input, clear on a successful save. Delegated at the sidebar so it
    // covers controls added later without needing to be re-wired.
    function watchDirtyFields() {
        const aside = document.querySelector('.map-sidebar') || document.querySelector('aside');
        if (!aside) return;
        aside.addEventListener('input', (event) => {
            const el = event.target;
            if (el && el.dataset) el.dataset.mapDirty = '1';
        });
    }

    // The three sidebar pickers were rendered by Jinja at page load, so adding a
    // combatant in the tracker or renaming a PC left them stale until a manual
    // reload. Rebuilt from /api/scenes, which now carries token_candidates
    // alongside the scene list, so one call refreshes all three.
    //
    // Each rebuild preserves the current value where it still exists: this runs
    // on encounter_update, which fires while the GM is mid-action, and silently
    // resetting a picker they had already set would be its own bug.
    function refillSelect(id, options, keyOf, labelOf) {
        const select = document.getElementById(id);
        if (!select || isBeingEdited(select)) return;
        const previous = select.value;
        const keep = select.querySelector('option[value=""]');
        select.textContent = '';
        if (keep) select.appendChild(keep);
        for (const item of options) {
            const option = document.createElement('option');
            option.value = keyOf(item);
            option.textContent = labelOf(item);
            select.appendChild(option);
        }
        if (previous && select.querySelector('option[value="' + CSS.escape(previous) + '"]')) {
            select.value = previous;
        }
    }

    async function refreshPickers() {
        if (!hasGmChrome()) return;
        try {
            const data = await request('/api/scenes');
            tableSceneId = data.active_scene_id || null;
            paintTableState();
            const candidates = data.token_candidates || [];
            refillSelect('map-scene-select', data.scenes || [],
                         s => s.id, s => s.name);
            const sceneSelect = document.getElementById('map-scene-select');
            if (sceneSelect && !isBeingEdited(sceneSelect)) sceneSelect.value = sceneId;
            refillSelect('map-token-source', candidates, c => c.key, c => c.label);
            refillSelect('map-token-owner', candidates.filter(c => c.character_id),
                         c => c.character_id, c => c.name);
            // The owner picker belongs to the selected token, so restore its value.
            const token = selectedToken();
            const owner = document.getElementById('map-token-owner');
            if (token && owner && !isBeingEdited(owner)) {
                owner.value = token.controller_character_id || '';
            }
        } catch (_) { /* a stale picker is not worth a toast */ }
    }

    function clearDirty(root) {
        const scope = root || document.querySelector('.map-sidebar') || document.querySelector('aside');
        if (!scope) return;
        scope.querySelectorAll('[data-map-dirty="1"]').forEach(el => { delete el.dataset.mapDirty; });
    }

    function fillControls() {
        const set = (id, value, prop) => {
            const el = document.getElementById(id);
            if (el && !isBeingEdited(el)) el[prop || 'value'] = value;
        };
        set('map-scene-name', scene.name || '');
        set('map-grid-size', scene.grid.size || 70);
        set('map-grid-visible', !!scene.grid.visible, 'checked');
        set('map-player-movement', !!scene.settings.player_movement, 'checked');
        set('map-snap-grid', !!scene.settings.snap_to_grid, 'checked');
        set('map-dynamic-lighting', !!scene.settings.dynamic_lighting, 'checked');
        set('map-default-vision', Number(scene.settings.default_vision) || 700);
        set('map-fog-enabled', !!scene.fog.enabled, 'checked');
        fillGridControls();
    }

    function selectedToken() {
        return scene && (scene.tokens || []).find(token => token.id === selectedId);
    }

    function updateSelectionPanel() {
        if (!hasGmChrome()) return;
        const box = document.getElementById('map-token-actions');
        const empty = document.getElementById('map-token-empty');
        const token = selectedToken();
        box.hidden = !token;
        if (empty) empty.hidden = !!token;
        if (!token) return;
        // Same rule as fillControls: never overwrite what the GM is editing.
        const setField = (id, value, prop) => {
            const el = document.getElementById(id);
            if (el && !isBeingEdited(el)) el[prop || 'value'] = value;
        };
        setField('map-selected-name', token.name || '');
        setField('map-token-size', String(Number(token.size) || 1));
        setField('map-token-vision', Number(token.vision_radius) || Number(scene.settings.default_vision) || 700);
        setField('map-token-color', /^#[0-9a-f]{6}$/i.test(token.color || '') ? token.color : '#a84b45');
        setField('map-token-owner', token.controller_character_id || '');
        setField('map-token-nameplate', token.show_nameplate !== false, 'checked');
        setField('map-token-locked', !!token.locked, 'checked');
        setField('map-token-visible', token.visible_to_players !== false, 'checked');
        document.getElementById('map-token-visibility').textContent = token.visible_to_players === false
            ? 'Reveal to players' : 'Hide from players';
        const link = document.getElementById('map-token-sheet');
        link.hidden = !token.sheet_url;
        if (token.sheet_url) {
            link.href = token.sheet_url;
            link.textContent = token.is_pc ? 'Open character sheet' : 'Open combat tracker';
        }
        const combat = document.getElementById('map-combat-actions');
        const live = token.live || {};
        // GM-only: every action in this panel posts to a @gm_required route,
        // and players are not sent the numbers it displays.
        combat.hidden = !cfg.isGm || !token.combatant_id || live.current_hp === undefined;
        if (!combat.hidden) {
            document.getElementById('map-combat-hp').textContent =
                'HP ' + Number(live.current_hp || 0) + ' / ' + Number(live.max_hp || 0);
        }
    }

    function patchScene(body) {
        return request('/api/scenes/' + encodeURIComponent(sceneId), {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(body)
        });
    }

    function patchToken(tokenId, body) {
        return request('/api/scenes/' + encodeURIComponent(sceneId) + '/tokens/' + encodeURIComponent(tokenId), {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(body)
        });
    }

    function runCombatAction(tokenId, body) {
        return request('/api/scenes/' + encodeURIComponent(sceneId) + '/combatants/' + encodeURIComponent(tokenId), {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(body)
        });
    }

    function wireCreateForm() {
        const form = document.getElementById('map-create-form');
        if (!form) return;
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            const fields = new FormData(form);
            try {
                const data = await request('/api/scenes', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
                    body: JSON.stringify({name: fields.get('name'), width: fields.get('width'), height: fields.get('height')})
                });
                location.href = '/map/' + encodeURIComponent(data.scene.id);
            } catch (error) { toast(error.message, true); }
        });
    }

    // Derive grid size AND offset from one alignment drag.
    //
    // The drag is made across the printed grid of an uploaded battlemap: it
    // starts on a gridline intersection, which fixes where the grid begins, and
    // spans a stated number of squares, which fixes how big they are. Spanning
    // several squares divides the pointing error across them, so it is far more
    // accurate than trying to drag exactly one square.
    //
    // Only the longer axis is measured. A drag is never perfectly horizontal,
    // and averaging both axes would let a few pixels of vertical wobble shrink
    // the derived size.
    function calibrationFromDrag(start, end) {
        const squaresField = document.getElementById('map-calibrate-squares');
        const squares = Math.max(1, Math.min(40, Math.round(Number(squaresField && squaresField.value) || 1)));
        const dx = Math.abs(end.x - start.x);
        const dy = Math.abs(end.y - start.y);
        const span = Math.max(dx, dy);
        const current = Number(scene.grid.size) || 70;
        // Too short a drag says nothing about size; keep the current size and
        // treat the gesture as an offset nudge, which is what it used to be.
        const size = span < 8 ? current : Math.max(20, Math.min(300, span / squares));
        return {
            size: Math.round(size * 100) / 100,
            offsetX: normalizedOffset(start.x, size),
            offsetY: normalizedOffset(start.y, size)
        };
    }

    // Which scene the TABLE is showing -- not necessarily this one. Kept here
    // so the sidebar can say so plainly; the GM otherwise has no way to tell
    // whether what they are editing is currently in front of the players.
    let tableSceneId = null;

    function paintTableState() {
        const label = document.getElementById('map-table-state');
        const button = document.getElementById('map-show-on-table');
        if (!label) return;
        const live = tableSceneId && tableSceneId === sceneId;
        label.textContent = live
            ? 'On the table now'
            : (tableSceneId ? 'Another scene is on the table' : 'Not on the table');
        label.classList.toggle('is-live', !!live);
        if (button) {
            button.disabled = !!live;
            button.textContent = live ? 'Already on the table' : 'Show on table';
        }
        // Deleting what the players are looking at is refused server-side; say
        // so here rather than letting the GM find out from an error toast.
        const del = document.getElementById('map-delete-scene');
        if (del) {
            del.disabled = !!live;
            del.title = live ? 'Take this scene off the table before deleting it.' : '';
        }
    }

    async function refreshTableState() {
        if (!hasGmChrome()) return;
        try {
            const data = await request('/api/scenes');
            tableSceneId = data.active_scene_id || null;
        } catch (_) { /* leave it unknown rather than lying about it */ }
        paintTableState();
    }

    let selectedLightId = null;

    function nearestLight(point) {
        let best = null, bestDistance = Infinity;
        for (const light of scene.lights || []) {
            const distance = Math.hypot(point.x - Number(light.x), point.y - Number(light.y));
            // Grab by the handle at its centre, not anywhere in its glow --
            // a large light would otherwise swallow every click in the room.
            if (distance < 22 && distance < bestDistance) { best = light; bestDistance = distance; }
        }
        return best;
    }

    function selectedLight() {
        return (scene.lights || []).find(light => light.id === selectedLightId) || null;
    }

    function paintLightPanel() {
        const box = document.getElementById('map-light-actions');
        if (!box) return;
        const light = selectedLight();
        box.hidden = !light;
        if (!light) return;
        const set = (id, value) => {
            const el = document.getElementById(id);
            if (el && !isBeingEdited(el)) el.value = value;
        };
        set('map-light-edit-radius', Number(light.radius) || 0);
        set('map-light-edit-color', /^#[0-9a-f]{6}$/i.test(light.color || '') ? light.color : '#ffd98a');
        set('map-light-edit-intensity', Math.round((Number(light.intensity) || .75) * 100));
    }

    async function saveSelectedLight() {
        const light = selectedLight();
        if (!light) return;
        try {
            const data = await mapElementAction({
                action: 'update_light', id: light.id,
                radius: document.getElementById('map-light-edit-radius').value,
                color: document.getElementById('map-light-edit-color').value,
                intensity: Math.max(5, Math.min(100, Number(document.getElementById('map-light-edit-intensity').value) || 75)) / 100
            });
            clearDirty();
            applyScene(data.scene);
            toast('Light updated.');
        } catch (error) { toast(error.message, true); }
    }

    function setViewMode(mode) {
        viewMode = mode === 'table' ? 'table' : 'gm';
        const button = document.getElementById('map-preview-table');
        if (button) {
            button.classList.toggle('is-active', isTableView());
            button.textContent = isTableView() ? 'Exit table preview' : 'Preview table view';
        }
        document.querySelector('.map-page').classList.toggle('is-previewing', isTableView());
        // Drawing while previewing would edit what you cannot fully see, so the
        // tools step back to Select.
        if (isTableView() && activeTool !== 'select') setActiveTool('select');
        syncAnimation();
        document.getElementById('map-status').textContent = isTableView()
            ? 'Table preview: exactly what the players would see.'
            : 'GM view - changes are shared live';
        draw();
    }

    function setCalibrationMode(enabled) {
        calibrationMode = !!enabled;
        canvas.classList.toggle('is-calibrating', calibrationMode);
        const button = document.getElementById('map-calibrate-grid');
        if (button) {
            button.classList.toggle('is-active', calibrationMode);
            button.textContent = calibrationMode ? 'Finish grid alignment' : 'Align grid by dragging';
        }
        const squaresField = document.getElementById('map-calibrate-squares');
        const squares = Math.max(1, Math.round(Number(squaresField && squaresField.value) || 1));
        document.getElementById('map-status').textContent = calibrationMode
            ? ('Grid alignment: drag from a gridline corner across ' + squares
               + (squares === 1 ? ' square.' : ' squares.'))
            : 'GM view - changes are shared live';
        draw();
    }

    const toolDescriptions = {
        select: 'Select and move tokens', target: 'Click tokens to target; Shift-click also works in Select',
        measure: 'Drag to measure PF2e grid distance', burst: 'Click to place a burst',
        emanation: 'Click to place an emanation', cone: 'Drag to aim a cone', line: 'Drag to aim a line',
        'fog-reveal': 'Click a room to reveal it', 'fog-hide': 'Click a room to hide it again',
        wall: 'Click each corner; Esc or click the last point to finish', door: 'Click each corner to place doors, or click an existing door to open it',
        light: 'Click to place a light source', erase: 'Click a wall, door, light, or template to remove it',
        'terrain-lava': 'Click a room to flood it with lava; Alt-click one square',
        'terrain-water': 'Click a room to flood it with water; Alt-click one square',
        'terrain-poison': 'Click a room to fill it with poison; Alt-click one square',
        'terrain-blood': 'Alt-click a square to spill blood, or click to pool a whole room',
        'terrain-clear': 'Click a flooded area to drain it; Alt-click one square',
        ping: 'Click to point at something on the table screen'
    };

    function setActiveTool(tool) {
        // Leaving the wall tool with a run in progress saves it. Discarding a
        // half-drawn room because the GM reached for another tool would be the
        // worst possible answer.
        if (wallChain.length && tool !== activeTool) commitWallChain(activeTool);
        activeTool = tool || 'select';
        measurement = activeTool === 'measure' ? measurement : null;
        // Take the line off the TV as soon as the GM puts the ruler down,
        // rather than leaving the room staring at a stale number.
        if (!measurement) clearSharedMeasure();
        interaction = null;
        canvas.classList.toggle('is-tool-active', activeTool !== 'select');
        document.querySelectorAll('[data-map-tool]').forEach(button => {
            button.classList.toggle('is-active', button.dataset.mapTool === activeTool);
        });
        const readout = document.getElementById('map-tool-readout');
        if (readout) readout.textContent = toolDescriptions[activeTool] || activeTool;
        draw();
    }

    function wireGmControls() {
        if (!hasGmChrome()) return;
        wireCreateForm();
        const sceneSelect = document.getElementById('map-scene-select');
        // Opening a scene is not the same as showing it to the players. This
        // used to activate first, which meant every scene you glanced at was
        // pushed to the table mid-session.
        sceneSelect.addEventListener('change', function () {
            if (sceneSelect.value) location.href = '/map/' + encodeURIComponent(sceneSelect.value);
        });
        document.getElementById('map-show-on-table').addEventListener('click', async function () {
            try {
                await request('/api/scenes/' + encodeURIComponent(sceneId) + '/activate', {method: 'POST'});
                tableSceneId = sceneId;
                paintTableState();
                toast('This scene is now on the table.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-delete-scene').addEventListener('click', async function () {
            const name = (scene && scene.name) || 'this scene';
            if (!window.confirm('Delete "' + name + '" and its background image? This cannot be undone.')) return;
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId) + '/delete', {method: 'POST'});
                // The scene we were looking at is gone, so there is nowhere to
                // stay -- go wherever the server says is sensible now.
                location.href = data.default_scene_id ? '/map/' + encodeURIComponent(data.default_scene_id) : '/map';
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-light-save').addEventListener('click', saveSelectedLight);
        document.getElementById('map-light-delete').addEventListener('click', async function () {
            const light = selectedLight();
            if (!light) return;
            try {
                const data = await mapElementAction({action: 'delete_light', id: light.id});
                selectedLightId = null;
                applyScene(data.scene);
                paintLightPanel();
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-preview-table').addEventListener('click', function () {
            setViewMode(isTableView() ? 'gm' : 'table');
        });
        document.getElementById('map-calibrate-grid').addEventListener('click', function () {
            setCalibrationMode(!calibrationMode);
        });
        document.getElementById('map-fog-enabled').addEventListener('change', async function (event) {
            try {
                const data = await mapElementAction({action: 'fog_enabled', enabled: event.target.checked});
                applyScene(data.scene);
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-reset-fog').addEventListener('click', async function () {
            try {
                const data = await mapElementAction({action: 'fog_reset'});
                applyScene(data.scene); toast('Fog painting reset.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-clear-templates').addEventListener('click', async function () {
            try {
                const data = await mapElementAction({action: 'clear_templates'});
                applyScene(data.scene); toast('Templates cleared.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-sync-encounter').addEventListener('click', async function () {
            const status = document.getElementById('map-sync-status');
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId) + '/sync-encounter', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
                    body: '{}'
                });
                if (data.added_token_ids && data.added_token_ids.length) selectedId = data.added_token_ids[0];
                applyScene(data.scene);
                status.textContent = data.encounter_count
                    ? (data.added + ' added, ' + data.linked + ' linked, ' + data.encounter_count + ' combatants synchronized.')
                    : 'The live encounter is empty. Add combatants in the tracker first.';
                toast(data.encounter_count ? 'Live encounter synchronized.' : 'No live combatants to add.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-save-settings').addEventListener('click', async function () {
            try {
                const data = await patchScene({
                    name: document.getElementById('map-scene-name').value,
                    grid: {
                        size: document.getElementById('map-grid-size').value,
                        offset_x: document.getElementById('map-grid-offset-x').value,
                        offset_y: document.getElementById('map-grid-offset-y').value,
                        visible: document.getElementById('map-grid-visible').checked
                    },
                    settings: {
                        player_movement: document.getElementById('map-player-movement').checked,
                        snap_to_grid: document.getElementById('map-snap-grid').checked,
                        dynamic_lighting: document.getElementById('map-dynamic-lighting').checked,
                        default_vision: document.getElementById('map-default-vision').value
                    }
                });
                clearDirty();
                applyScene(data.scene);
                toast('Scene settings saved.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-upload-form').addEventListener('submit', async function (event) {
            event.preventDefault();
            const input = document.getElementById('map-upload-input');
            if (!input.files.length) return;
            const form = new FormData();
            form.append('image', input.files[0]);
            try {
                const response = await fetch('/api/scenes/' + encodeURIComponent(sceneId) + '/background', {
                    method: 'POST', body: form, headers: {'X-Requested-With': 'XMLHttpRequest'}
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Upload failed');
                applyScene(data.scene);
                toast('Map image uploaded.');
            } catch (error) { toast(error.message, true); }
        });
        // Where a new token goes.
        //
        // Every token used to land on one hardcoded square -- grid*2 from the
        // origin -- so adding four of anything produced a stack you then had to
        // drag apart one by one. Now you drag it to the square you want.
        function newTokenBody() {
            const key = document.getElementById('map-token-source').value;
            const candidate = candidates.find(item => item.key === key);
            return candidate
                ? {source_kind: candidate.source_kind, source_id: candidate.source_id}
                : {name: document.getElementById('map-token-name').value || 'Token'};
        }

        async function placeToken(at) {
            const body = newTokenBody();
            const spot = (scene.settings || {}).snap_to_grid ? snapPoint(at, null) : at;
            body.x = Math.max(0, Math.min(scene.width, spot.x));
            body.y = Math.max(0, Math.min(scene.height, spot.y));
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId) + '/tokens', {
                    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}, body: JSON.stringify(body)
                });
                selectedId = data.token.id;
                applyScene(data.scene);
            } catch (error) { toast(error.message, true); }
        }

        const addButton = document.getElementById('map-add-token');
        addButton.addEventListener('dragstart', function (event) {
            // Firefox refuses to start a drag without payload, and the payload
            // itself is irrelevant -- the drop handler reads the live sidebar.
            if (event.dataTransfer) {
                event.dataTransfer.setData('text/plain', 'map-token');
                event.dataTransfer.effectAllowed = 'copy';
            }
            addButton.classList.add('is-dragging');
        });
        addButton.addEventListener('dragend', function () { addButton.classList.remove('is-dragging'); });
        canvas.addEventListener('dragover', function (event) {
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        });
        canvas.addEventListener('drop', function (event) {
            event.preventDefault();
            addButton.classList.remove('is-dragging');
            placeToken(pointFromEvent(event));
        });
        // Clicking still works, and still beats the old behaviour: it drops the
        // token in the middle of what you are looking at rather than in a corner
        // you may have scrolled far away from.
        addButton.addEventListener('click', function () {
            const centre = viewportCenter();
            placeToken({
                x: (viewport.scrollLeft + centre.x) / zoom,
                y: (viewport.scrollTop + centre.y) / zoom
            });
        });
        // Token art. The bestiary cannot supply this -- almost every monster
        // entry carries the same generic Foundry default icon -- so uploading
        // is the only route to real art, and the only one built.
        document.getElementById('map-token-art').addEventListener('change', async function (event) {
            const token = selectedToken();
            const file = event.target.files && event.target.files[0];
            if (!token || !file) return;
            const form = new FormData();
            form.append('image', file);
            try {
                const response = await fetch('/api/scenes/' + encodeURIComponent(sceneId)
                    + '/tokens/' + encodeURIComponent(token.id) + '/image',
                    {method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest'}, body: form});
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Upload failed');
                applyScene(data.scene);
                toast('Token art updated.');
            } catch (error) { toast(error.message, true); }
            event.target.value = '';
        });
        document.getElementById('map-token-art-clear').addEventListener('click', async function () {
            const token = selectedToken();
            if (!token) return;
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId)
                    + '/tokens/' + encodeURIComponent(token.id) + '/image', {method: 'DELETE'});
                applyScene(data.scene);
                toast('Token art cleared.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-save-token').addEventListener('click', async function () {
            const token = selectedToken();
            if (!token) return;
            try {
                const data = await patchToken(token.id, {
                    name: document.getElementById('map-selected-name').value,
                    size: document.getElementById('map-token-size').value,
                    vision_radius: document.getElementById('map-token-vision').value,
                    color: document.getElementById('map-token-color').value,
                    controller_character_id: document.getElementById('map-token-owner').value,
                    show_nameplate: document.getElementById('map-token-nameplate').checked,
                    locked: document.getElementById('map-token-locked').checked,
                    visible_to_players: document.getElementById('map-token-visible').checked
                });
                clearDirty();
                applyScene(data.scene);
                toast('Token saved.');
            } catch (error) { toast(error.message, true); }
        });
        async function applyHpAction(action) {
            const token = selectedToken();
            if (!token || !token.combatant_id) return;
            const amount = document.getElementById('map-combat-amount').value;
            try {
                const data = await runCombatAction(token.combatant_id, {
                    action: action,
                    amount: amount,
                    damage_type: document.getElementById('map-damage-type').value
                });
                applyScene(data.scene);
                const result = data.result || {};
                toast((action === 'damage' ? 'Damage applied: ' : 'Healing applied: ') + Number(result.net || 0));
            } catch (error) { toast(error.message, true); }
        }
        async function applyCondition(operation) {
            const token = selectedToken();
            if (!token || !token.combatant_id) return;
            try {
                const condition = document.getElementById('map-condition').value;
                const roundsField = document.getElementById('map-condition-rounds');
                const rounds = Math.max(0, Math.min(100, Number(roundsField && roundsField.value) || 0));
                const data = await runCombatAction(token.combatant_id, {
                    action: 'condition', condition: condition,
                    operation: operation, rounds: rounds
                });
                applyScene(data.scene);
                toast(condition.replace(/_/g, ' ') + (rounds ? ' for ' + rounds + ' rounds.' : ' updated.'));
            } catch (error) { toast(error.message, true); }
        }
        document.getElementById('map-apply-pd').addEventListener('click', async function () {
            const token = selectedToken();
            if (!token || !token.combatant_id) return;
            const damage = document.getElementById('map-pd-damage').value.trim();
            if (!damage) { toast('Enter a damage expression, e.g. 1d6', true); return; }
            try {
                const data = await runCombatAction(token.combatant_id, {
                    action: 'persistent_damage', damage: damage,
                    damage_type: document.getElementById('map-pd-type').value.trim()
                });
                applyScene(data.scene);
                toast('Persistent damage applied.');
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-apply-damage').addEventListener('click', () => applyHpAction('damage'));
        document.getElementById('map-apply-healing').addEventListener('click', () => applyHpAction('heal'));
        document.getElementById('map-add-condition').addEventListener('click', () => applyCondition('add'));
        document.getElementById('map-remove-condition').addEventListener('click', () => applyCondition('decrease'));
        document.getElementById('map-delete-token').addEventListener('click', async function () {
            if (!selectedId) return;
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId) + '/tokens/' + encodeURIComponent(selectedId), {method: 'DELETE'});
                selectedId = null;
                applyScene(data.scene);
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-token-visibility').addEventListener('click', async function () {
            const token = selectedToken();
            if (!token) return;
            try {
                const data = await patchToken(token.id, {visible_to_players: token.visible_to_players === false});
                applyScene(data.scene);
            } catch (error) { toast(error.message, true); }
        });
        document.getElementById('map-clear-targets').addEventListener('click', function () {
            targetIds.clear(); updateTargetPanel(); draw();
        });
        function targetedCombatantIds() {
            return (scene.tokens || []).filter(token => targetIds.has(token.id) && token.combatant_id)
                .map(token => token.combatant_id);
        }
        async function bulkAction(body) {
            body.combatant_ids = targetedCombatantIds();
            try {
                const data = await request('/api/scenes/' + encodeURIComponent(sceneId) + '/bulk-combat', {
                    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
                    body: JSON.stringify(body)
                });
                applyScene(data.scene);
                toast((data.results || []).length + ' target result(s).');
            } catch (error) { toast(error.message, true); }
        }
        document.getElementById('map-bulk-damage').addEventListener('click', () => bulkAction({
            action: 'damage', amount: document.getElementById('map-bulk-amount').value,
            damage_type: document.getElementById('map-bulk-damage-type').value
        }));
        document.getElementById('map-bulk-heal').addEventListener('click', () => bulkAction({
            action: 'heal', amount: document.getElementById('map-bulk-amount').value,
            damage_type: 'untyped'
        }));
        document.getElementById('map-bulk-condition-add').addEventListener('click', () => bulkAction({
            action: 'condition', condition: document.getElementById('map-bulk-condition').value, operation: 'add'
        }));
        document.getElementById('map-bulk-condition-remove').addEventListener('click', () => bulkAction({
            action: 'condition', condition: document.getElementById('map-bulk-condition').value, operation: 'decrease'
        }));
        document.getElementById('map-request-save').addEventListener('click', () => bulkAction({
            action: 'save_request', save: document.getElementById('map-save-kind').value,
            dc: document.getElementById('map-save-dc').value
        }));
    }

    function updateUndoButton() {
        const button = document.getElementById('map-undo');
        if (!button) return;
        const next = undoStack[undoStack.length - 1];
        button.disabled = !next;
        // Say what it will actually undo. The old label read "Undo move" no
        // matter what the GM had just done, and Ctrl+Z fired after any action
        // -- so painting lava on the wrong room and pressing it silently moved
        // a token back instead.
        button.textContent = next ? 'Undo ' + next.label : 'Undo';
    }

    async function undoLast() {
        const entry = undoStack.pop();
        updateUndoButton();
        if (!entry) return;
        undoing = true;
        try {
            if (entry.move) {
                const data = await patchToken(entry.move.tokenId, {x: entry.move.x, y: entry.move.y});
                selectedId = entry.move.tokenId;
                applyScene(data.scene);
            } else {
                let data = null;
                for (const op of entry.ops) data = await mapElementAction(op);
                if (data && data.scene) applyScene(data.scene);
            }
            toast('Undid ' + entry.label + '.');
        } catch (error) {
            // Refuse honestly rather than half-apply. Restoring an erased wall
            // mints a new id, so once one step of an inverse fails the entries
            // beneath it are describing a scene that no longer exists -- and
            // silently applying those would move things the GM never touched.
            undoStack.length = 0;
            updateUndoButton();
            toast('Could not undo ' + entry.label + ': ' + error.message
                  + '. Undo history cleared.', true);
        } finally {
            undoing = false;
            updateUndoButton();
        }
    }

    document.querySelectorAll('[data-map-tool]').forEach(button => {
        button.addEventListener('click', () => setActiveTool(button.dataset.mapTool));
    });
    (function wireBuildTools() {
        const toggle = document.getElementById('map-build-toggle');
        const group = document.getElementById('map-build-group');
        if (!toggle || !group) return;
        toggle.addEventListener('click', function () {
            const open = group.hasAttribute('hidden');
            group.toggleAttribute('hidden', !open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            // Folding the drawer away while one of its tools is armed would
            // leave the GM in a mode with nothing on screen naming it.
            if (!open && activeTool !== 'select'
                && !!group.querySelector('[data-map-tool="' + activeTool + '"]')) {
                setActiveTool('select');
            }
        });
    })();
    document.getElementById('map-clear-targets-tool').addEventListener('click', function () {
        targetIds.clear(); updateTargetPanel(); draw();
    });

    try {
        followActiveTurn = localStorage.getItem('pf2e-map-follow-turn') !== 'false';
    } catch (_) {}

    function updateFollowTurnButton() {
        const button = document.getElementById('map-follow-turn');
        button.classList.toggle('is-active', followActiveTurn);
        button.textContent = followActiveTurn ? 'Following turn' : 'Follow turn';
    }

    document.getElementById('map-follow-turn').addEventListener('click', function () {
        followActiveTurn = !followActiveTurn;
        focusedTurnCombatantId = null;
        try { localStorage.setItem('pf2e-map-follow-turn', String(followActiveTurn)); } catch (_) {}
        updateFollowTurnButton();
        if (followActiveTurn) focusActiveTurn(true);
    });

    document.getElementById('map-zoom-out').addEventListener('click', () => setZoom(zoom / 1.15));
    document.getElementById('map-zoom-in').addEventListener('click', () => setZoom(zoom * 1.15));
    document.getElementById('map-fit').addEventListener('click', fitMap);
    document.getElementById('map-undo').addEventListener('click', undoLast);
    viewport.addEventListener('wheel', function (event) {
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const anchor = {x: event.clientX - rect.left, y: event.clientY - rect.top};
        setZoom(zoom * Math.exp(-event.deltaY * .0012), anchor);
    }, {passive: false});
    // --- reaching a token without a mouse ---------------------------------
    //
    // The canvas is the entire interactive surface and carries no semantics, so
    // until now there was no keyboard path to a token at all -- only Escape and
    // Ctrl+Z existed.
    //
    // Deliberately NOT bound to Tab. Trapping Tab inside a canvas is a keyboard
    // trap: it would leave someone unable to reach the sidebar at all. Brackets
    // cycle instead, and Tab goes on meaning what it means everywhere else.
    function announce(message) {
        const region = document.getElementById('map-announce');
        if (region) region.textContent = message;
    }

    function keyboardTokens() {
        // Reading order, not array order, so "next" means the next one down the
        // map rather than whichever happened to be added first.
        return (scene.tokens || [])
            .filter(token => !isTableView() || token.visible_to_players !== false)
            .slice()
            .sort((a, b) => (a.y - b.y) || (a.x - b.x));
    }

    function cycleSelection(step) {
        const tokens = keyboardTokens();
        if (!tokens.length) return;
        const at = tokens.findIndex(token => token.id === selectedId);
        const next = tokens[(at + step + tokens.length * 2) % tokens.length];
        selectedId = next.id;
        updateSelectionPanel();
        focusToken(next);
        draw();
        announce(describeSelection(next));
    }

    function describeSelection(token) {
        const g = gridGeometry();
        const col = Math.floor((token.x - g.ox) / g.size);
        const row = Math.floor((token.y - g.oy) / g.size);
        const live = token.live || {};
        const hp = (Number(live.max_hp) > 0)
            ? ', ' + live.current_hp + ' of ' + live.max_hp + ' hit points' : '';
        return (token.name || 'Token') + ', column ' + (col + 1) + ' row ' + (row + 1) + hp;
    }

    function focusToken(token) {
        // Bring it into view; cycling to something off-screen is the same as
        // selecting nothing.
        const half = (tokenFootprint(token) * gridGeometry().size) / 2;
        const left = token.x * zoom - viewport.clientWidth / 2;
        const top = token.y * zoom - viewport.clientHeight / 2;
        viewport.scrollLeft = Math.max(0, left + half);
        viewport.scrollTop = Math.max(0, top + half);
    }

    // Nudges are collected and committed once the GM stops pressing, so holding
    // an arrow key is one write and one undo entry rather than thirty of each --
    // this rides the single worker that also serves every player's SSE.
    let nudge = null;
    const NUDGE_COMMIT_MS = 400;

    function nudgeSelected(dx, dy, fine) {
        const token = (scene.tokens || []).find(item => item.id === selectedId);
        if (!token) return false;
        // Locked is checked first only so it can say so; canControl() covers it
        // too, but silently, and a key that does nothing reads as a dead map.
        if (token.locked) { toast('That token is locked.', true); return true; }
        if (!canControl(token)) return false;
        const g = gridGeometry();
        // One square is the unit the game is played in; fine is for lining up
        // art on an unsnapped scene.
        const step = fine ? 1 : ((scene.settings || {}).snap_to_grid ? g.size : 10);
        if (!nudge || nudge.tokenId !== token.id) {
            nudge = {tokenId: token.id, fromX: token.x, fromY: token.y, timer: null};
        }
        token.x = Math.max(0, Math.min(scene.width, token.x + dx * step));
        token.y = Math.max(0, Math.min(scene.height, token.y + dy * step));
        // Where the GM has keyed it to, so an SSE frame answering an earlier
        // keypress can be reconciled against it rather than over it.
        nudge.x = token.x;
        nudge.y = token.y;
        draw();
        announce(describeSelection(token));
        clearTimeout(nudge.timer);
        nudge.timer = setTimeout(commitNudge, NUDGE_COMMIT_MS);
        return true;
    }

    async function commitNudge() {
        const pending = nudge;
        nudge = null;
        if (!pending) return;
        const token = (scene.tokens || []).find(item => item.id === pending.tokenId);
        if (!token) return;
        if (token.x === pending.fromX && token.y === pending.fromY) return;
        try {
            const data = await patchToken(token.id, {x: token.x, y: token.y});
            undoStack.push({label: 'move' + (token.name ? ' ' + token.name : ''),
                            move: {tokenId: token.id, x: pending.fromX, y: pending.fromY}});
            if (undoStack.length > UNDO_LIMIT) undoStack.shift();
            updateUndoButton();
            // Only reconcile if no NEWER nudge started while this write was in
            // flight. Otherwise the response -- which describes where the token
            // was one keypress ago -- lands on top of the position the GM has
            // since keyed, and that nudge is silently lost. Its own commit will
            // carry the scene forward instead.
            if (!nudge) applyScene(data.scene);
        } catch (error) {
            toast(error.message, true);
            fetchScene();
        }
    }

    const ARROWS = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};

    window.addEventListener('keydown', function (event) {
        const tag = String((event.target || {}).tagName || '').toLowerCase();
        if ((tag === 'input' || tag === 'select' || tag === 'textarea') || !scene) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
            event.preventDefault();
            undoLast();
            return;
        }
        if (event.key === 'Escape') {
            if (wallChain.length) commitWallChain(activeTool);
            else if (calibrationMode) setCalibrationMode(false);
            else if (selectedId) { selectedId = null; updateSelectionPanel(); draw(); announce('Selection cleared.'); }
            else setActiveTool('select');
            return;
        }
        if (isTableView()) return;               // the TV has no operator
        if (event.key === '[' || event.key === ']') {
            event.preventDefault();
            cycleSelection(event.key === ']' ? 1 : -1);
            return;
        }
        const arrow = ARROWS[event.key];
        // Only claim the arrows when something is selected -- otherwise they go
        // on scrolling the viewport, which is what they did before.
        if (arrow && selectedId && nudgeSelected(arrow[0], arrow[1], event.shiftKey)) {
            event.preventDefault();
        }
    });
    canvas.addEventListener('dblclick', function (event) {
        const token = hitToken(pointFromEvent(event));
        if (token && token.sheet_url) location.href = token.sheet_url;
    });

    wireGmControls();
    watchDirtyFields();
    refreshTableState();
    updateUndoButton();
    updateFollowTurnButton();
    // Stage 6a made rendering event-driven, so a first paint that happened
    // before Inter finished loading would measure text in the fallback face and
    // never repaint itself -- and measureText sizes the ruler and turn-banner
    // backing boxes, so those would stay wrong for the session.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(function () { cachedUiFont = ''; draw(); });
    }
    // "Live sync" was a green dot with no JS behind it at all -- permanently
    // reassuring, including while the stream was dead, which is worse than
    // having no indicator. There is no 'disconnected' event to listen for (the
    // hub nulls its socket and retries on a backoff), so the drop is noticed by
    // asking. Two seconds is far below the cost of anything else on this page.
    (function watchLiveSync() {
        const wrap = document.getElementById('map-live');
        const label = document.getElementById('map-live-label');
        if (!wrap || !label) return;
        let wasLive = true;
        function paint() {
            const live = !window.__appSSE || window.__appSSE.isConnected();
            if (live === wasLive) return;
            wasLive = live;
            wrap.classList.toggle('is-dropped', !live);
            label.textContent = live ? 'Live sync' : 'Reconnecting...';
        }
        paint();
        setInterval(paint, 2000);
    })();
    if (window.appSSE) {
        window.appSSE('scene_beacon', function (event) {
            try { receiveBeacon(JSON.parse(event.data)); } catch (_) {}
        });
        window.appSSE('scene_update', function (event) {
            try { applyScene(JSON.parse(event.data)); } catch (_) {}
        });
        window.appSSE('scene_activated', function (event) {
            try {
                const data = JSON.parse(event.data);
                tableSceneId = data.scene_id || null;
                // The GM's view deliberately does NOT follow. Only the table
                // screen should jump when a scene is pushed; yanking the GM's
                // window mid-prep is the behaviour this stage removes.
                paintTableState();
            } catch (_) {}
        });
        // These three all mean "live state moved", not "the scene changed" --
        // the scene arrives on its own scene_update. They refetch because the
        // map paints HP and conditions onto tokens from the live projection.
        //
        // Coalesced, because they arrive in bursts and each one costs a full
        // scene fetch on BOTH the GM page and the TV. One area effect on four
        // targets emits four pc_update frames plus an encounter_update; that
        // was six scene fetches for a scene nobody edited, on the single worker
        // that is also serving those same SSE streams.
        let liveRefetch = null;
        function refetchLiveState() {
            if (liveRefetch) return;
            liveRefetch = setTimeout(function () { liveRefetch = null; fetchScene(); }, 250);
        }
        // refreshPickers parses every scene file on disk to rebuild the
        // dropdowns, and exists to notice a combatant being added or renamed --
        // which is rare, and never urgent. It gets its own slow trailing timer
        // rather than riding every turn advance.
        let pickerRefresh = null;
        function refreshPickersSoon() {
            if (!hasGmChrome()) return;
            clearTimeout(pickerRefresh);
            pickerRefresh = setTimeout(refreshPickers, 2000);
        }
        window.appSSE('encounter_update', function () {
            refetchLiveState();
            refreshPickersSoon();
        });
        window.appSSE('pc_update', refetchLiveState);
        window.appSSE('connected', refetchLiveState);
    }
    fetchScene().then(function () {
        // Restore where this scene was left; failing that, fit it. The GM used
        // to get neither and always landed at 100% in the top-left corner.
        //
        // The table screen always fits instead. It has no operator to correct a
        // restored view, and the saved one is whatever the last window left
        // behind -- a TV that comes up scrolled into a corner stays there all
        // session.
        if (isTableView() || !restoreView()) fitMap();
    });
    // Persist the viewport rather than every scroll frame.
    let viewSaveTimer = null;
    function scheduleViewSave() {
        if (viewSaveTimer) clearTimeout(viewSaveTimer);
        viewSaveTimer = setTimeout(saveView, 400);
    }
    viewport.addEventListener('scroll', scheduleViewSave, {passive: true});
    window.addEventListener('pagehide', saveView);
})();
