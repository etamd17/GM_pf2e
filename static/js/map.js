(function () {
    'use strict';

    const cfg = window.SCENE_MAP_CONFIG || {};
    const candidates = window.SCENE_TOKEN_CANDIDATES || [];
    const sceneId = cfg.sceneId;
    const canvas = document.getElementById('map-canvas');
    const viewport = document.getElementById('map-viewport');
    if (!sceneId || !canvas || !viewport) {
        wireCreateForm();
        return;
    }

    const ctx = canvas.getContext('2d');
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
    const movementHistory = [];

    function toast(message, error) {
        const el = document.getElementById('map-toast');
        if (!el) return;
        el.textContent = message;
        el.classList.toggle('is-error', !!error);
        el.classList.add('is-visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('is-visible'), 2600);
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
        scene = normalizeClientScene(next);
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
    window.__mapRenderNow = function () { frameQueued = false; renderScene(); };

    function renderScene() {
        if (!scene) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#181611';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        if (background) ctx.drawImage(background, 0, 0, canvas.width, canvas.height);
        drawGrid();
        drawAmbientLights();
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

    function drawAmbientLights() {
        for (const light of scene.lights || []) {
            const radius = Math.max(1, Number(light.radius) || 1);
            const gradient = ctx.createRadialGradient(light.x, light.y, 0, light.x, light.y, radius);
            // A stop is built by concatenating an alpha byte, so the colour has
            // to be exactly #rrggbb or the stop is invalid and throws inside the
            // render loop -- taking the whole canvas down, not just one light.
            const color = /^#[0-9a-f]{6}$/i.test(light.color || '') ? light.color : '#ffd98a';
            const alpha = Math.max(.05, Math.min(1, Number(light.intensity) || .75));
            // Was alpha * 90, i.e. capped at 0x5A -- about 35% -- so the
            // difference between a candle and a bonfire was nearly invisible.
            gradient.addColorStop(0, color + Math.round(alpha * 200).toString(16).padStart(2, '0'));
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
            carveVisibility(mctx, {x: Number(light.x), y: Number(light.y)}, Math.max(0, Number(light.radius) || 0));
        }
        mctx.globalCompositeOperation = 'source-over';
        ctx.drawImage(mask, 0, 0);
    }

    function drawFogOverlay() {
        if (!(scene.fog || {}).enabled) return;
        const fogScratch = scratchCanvas('fog');
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
        for (const key of scene.fog.revealed_cells || []) {
            const parts = String(key).split(',');
            const col = Number(parts[0]), row = Number(parts[1]);
            if (!Number.isFinite(col) || !Number.isFinite(row)) continue;
            mctx.fillRect(g.ox + col * g.size, g.oy + row * g.size, g.size, g.size);
        }
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
        ctx.font = '700 16px system-ui, sans-serif';
        const width = ctx.measureText(text).width + 16;
        const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2 - 18;
        ctx.fillStyle = 'rgba(10,10,12,.82)';
        ctx.fillRect(mx - width / 2, my - 15, width, 24);
        ctx.fillStyle = over ? '#e9a13b' : '#f4e7c0';
        ctx.textAlign = 'center';
        ctx.fillText(text, mx, my + 2);
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
            const mx = (ruler.start.x + ruler.end.x) / 2, my = (ruler.start.y + ruler.end.y) / 2;
            ctx.setLineDash([]); ctx.font = '700 14px system-ui'; ctx.textAlign = 'center';
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
        const x = Number(token.x) || 0;
        const y = Number(token.y) || 0;
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
            ctx.font = '700 ' + Math.max(12, radius * .48) + 'px system-ui';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(initials, x, y);
        }

        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.strokeStyle = token.id === selectedId ? '#ffffff' : (token.color || 'rgba(246,236,204,.86)');
        ctx.lineWidth = token.id === selectedId ? 4 : 3;
        ctx.stroke();

        ctx.textAlign = 'center';
        if (token.show_nameplate !== false) {
            ctx.font = '700 13px system-ui';
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
        const barWidth = radius * 1.7;
        const barLeft = x - barWidth / 2;
        const barTop = y + radius + 6;
        if (Number(live.max_hp) > 0) {
            const pct = Math.max(0, Math.min(1, Number(live.current_hp) / Number(live.max_hp)));
            ctx.fillStyle = 'rgba(0,0,0,.75)';
            ctx.fillRect(barLeft - 1, barTop - 1, barWidth + 2, 7);
            ctx.fillStyle = pct > .5 ? '#58b37a' : pct > .25 ? '#d6a24d' : '#cf554d';
            ctx.fillRect(barLeft, barTop, barWidth * pct, 5);
        } else if (live.hp_status) {
            const dead = live.hp_status === 'Dead';
            ctx.fillStyle = 'rgba(0,0,0,.75)';
            ctx.fillRect(barLeft - 1, barTop - 1, barWidth + 2, 7);
            ctx.fillStyle = dead ? '#cf554d' : '#d6a24d';
            ctx.fillRect(barLeft, barTop, dead ? barWidth : barWidth * .5, 5);
        }
        const conditions = Object.keys(live.conditions || {});
        if (conditions.length) {
            const text = conditions.slice(0, 3).map(k => k.replace(/_/g, ' ')).join(' / ');
            ctx.font = '600 10px system-ui';
            ctx.textBaseline = 'top';
            ctx.strokeStyle = 'rgba(0,0,0,.85)';
            ctx.lineWidth = 3;
            ctx.strokeText(text, x, y + radius + 15);
            ctx.fillStyle = '#f3c6a7';
            ctx.fillText(text, x, y + radius + 15);
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
            ctx.font = '700 7px system-ui';
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
    let viewMode = cfg.isGm ? 'gm' : 'table';
    function isTableView() { return viewMode === 'table'; }

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

    function mapElementAction(body) {
        return request('/api/scenes/' + encodeURIComponent(sceneId) + '/elements', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(body)
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
            movementHistory.push({tokenId: token.id, x: finished.fromX, y: finished.fromY});
            if (movementHistory.length > 30) movementHistory.shift();
            updateUndoButton();
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
        if (!cfg.isGm) return;
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
        if (!cfg.isGm) return;
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
        if (!cfg.isGm) return;
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
        light: 'Click to place a light source', erase: 'Click a wall, door, light, or template to remove it'
    };

    function setActiveTool(tool) {
        // Leaving the wall tool with a run in progress saves it. Discarding a
        // half-drawn room because the GM reached for another tool would be the
        // worst possible answer.
        if (wallChain.length && tool !== activeTool) commitWallChain(activeTool);
        activeTool = tool || 'select';
        measurement = activeTool === 'measure' ? measurement : null;
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
        if (!cfg.isGm) return;
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
        if (button) button.disabled = movementHistory.length === 0;
    }

    async function undoMovement() {
        const move = movementHistory.pop();
        updateUndoButton();
        if (!move) return;
        try {
            const data = await patchToken(move.tokenId, {x: move.x, y: move.y});
            selectedId = move.tokenId;
            applyScene(data.scene);
            toast('Movement undone.');
        } catch (error) {
            movementHistory.push(move);
            updateUndoButton();
            toast(error.message, true);
        }
    }

    document.querySelectorAll('[data-map-tool]').forEach(button => {
        button.addEventListener('click', () => setActiveTool(button.dataset.mapTool));
    });
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
    document.getElementById('map-undo').addEventListener('click', undoMovement);
    viewport.addEventListener('wheel', function (event) {
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const anchor = {x: event.clientX - rect.left, y: event.clientY - rect.top};
        setZoom(zoom * Math.exp(-event.deltaY * .0012), anchor);
    }, {passive: false});
    window.addEventListener('keydown', function (event) {
        const tag = String((event.target || {}).tagName || '').toLowerCase();
        if ((tag === 'input' || tag === 'select' || tag === 'textarea') || !scene) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
            event.preventDefault();
            undoMovement();
        }
        if (event.key === 'Escape') {
            if (wallChain.length) commitWallChain(activeTool);
            else if (calibrationMode) setCalibrationMode(false);
            else setActiveTool('select');
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
    if (window.appSSE) {
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
        window.appSSE('encounter_update', function () {
            fetchScene();
            refreshPickers();   // a combatant may have been added or renamed
        });
        window.appSSE('pc_update', fetchScene);
        window.appSSE('connected', fetchScene);
    }
    fetchScene().then(function () {
        // Restore where this scene was left; failing that, fit it. The GM used
        // to get neither and always landed at 100% in the top-left corner.
        if (!restoreView()) fitMap();
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
