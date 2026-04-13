/**
 * PF2E 3D Physics Dice Roller Engine
 * Uses Three.js + Cannon-es for realistic dice rolling with physics
 * Dice land on pre-determined values (RNG is done before animation)
 * Non-blocking: users can interact with the page while dice roll
 */

// ─── CDN Imports ─────────────────────────────────────────────────────────────
let THREE, CANNON;

const CDN = {
    three: 'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.min.js',
    cannon: 'https://cdn.jsdelivr.net/npm/cannon-es@0.20.0/dist/cannon-es.js'
};

let _libsLoaded = false;
async function loadLibs() {
    if (_libsLoaded) return;
    [THREE, CANNON] = await Promise.all([
        import(CDN.three),
        import(CDN.cannon)
    ]);
    _libsLoaded = true;
}

// ─── Die Color Palette ───────────────────────────────────────────────────────
const DIE_COLORS = {
    4:   { base: '#7c3aed', accent: '#5b21b6', text: '#e9d5ff', name: 'purple' },
    6:   { base: '#2563eb', accent: '#1d4ed8', text: '#bfdbfe', name: 'blue' },
    8:   { base: '#059669', accent: '#047857', text: '#a7f3d0', name: 'green' },
    10:  { base: '#d97706', accent: '#b45309', text: '#fde68a', name: 'orange' },
    12:  { base: '#dc2626', accent: '#b91c1c', text: '#fecaca', name: 'red' },
    20:  { base: '#f59e0b', accent: '#d97706', text: '#1c1917', name: 'gold' },
    100: { base: '#6366f1', accent: '#4f46e5', text: '#c7d2fe', name: 'indigo' },
};

// ─── Physics Config ──────────────────────────────────────────────────────────
const PHYSICS = {
    gravity: -70,
    restitution: 0.12,
    friction: 0.6,
    linearDamping: 0.45,
    angularDamping: 0.4,
    settleSpeed: 0.12,
    maxTime: 2000,
    correctionTime: 180,
};

// ─── Numbered Texture Generation ─────────────────────────────────────────────

/**
 * Create a numbered texture for a die face.
 * For d6 (square faces): number fills the face generously.
 * For polyhedra (triangular/pentagonal faces): number is drawn inside a compact
 * circular badge that fits within the inscribed circle of any face shape.
 */
function createNumberedTexture(sides, faceValue, isSquareFace) {
    const color = DIE_COLORS[sides] || DIE_COLORS[20];
    const size = 256;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');

    const cx = size / 2, cy = size / 2;

    // Fill with die color gradient
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, size / 2);
    grad.addColorStop(0, color.base);
    grad.addColorStop(1, color.accent);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);

    if (isSquareFace) {
        // D6: square face — number fills generously
        ctx.strokeStyle = 'rgba(255,255,255,0.15)';
        ctx.lineWidth = 3;
        ctx.strokeRect(4, 4, size - 8, size - 8);

        const fontSize = Math.floor(size * 0.48);
        ctx.fillStyle = color.text;
        ctx.font = `bold ${fontSize}px "Cinzel", "Georgia", serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.shadowColor = 'rgba(0,0,0,0.5)';
        ctx.shadowBlur = 6;
        ctx.shadowOffsetY = 2;
        ctx.fillText(String(faceValue), cx, cy);
    } else {
        // Polyhedra: draw a circular badge that fits inside the inscribed circle
        // of a triangle (smallest face type). Badge radius ~28% of texture.
        const badgeR = size * 0.28;

        // Circular badge background (slightly lighter than base)
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, badgeR, 0, Math.PI * 2);
        const badgeGrad = ctx.createRadialGradient(cx, cy - badgeR * 0.2, 0, cx, cy, badgeR);
        badgeGrad.addColorStop(0, lightenColor(color.base, 0.15));
        badgeGrad.addColorStop(1, color.accent);
        ctx.fillStyle = badgeGrad;
        ctx.fill();

        // Badge border
        ctx.strokeStyle = 'rgba(255,255,255,0.2)';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.restore();

        // Number sized to fit inside the badge
        const fontSize = faceValue >= 10 ? Math.floor(badgeR * 1.0) : Math.floor(badgeR * 1.3);
        ctx.fillStyle = color.text;
        ctx.font = `bold ${fontSize}px "Cinzel", "Georgia", serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.shadowColor = 'rgba(0,0,0,0.6)';
        ctx.shadowBlur = 4;
        ctx.shadowOffsetY = 1;
        ctx.fillText(String(faceValue), cx, cy);
    }

    // Underline 6 and 9 to distinguish them
    if (faceValue === 6 || faceValue === 9) {
        const textMetrics = ctx.measureText(String(faceValue));
        const lineWidth = textMetrics.width * 0.6;
        const underY = isSquareFace ? cy + size * 0.2 : cy + size * 0.12;
        ctx.shadowBlur = 0;
        ctx.fillStyle = color.text;
        ctx.fillRect(cx - lineWidth / 2, underY, lineWidth, 2.5);
    }

    return canvas;
}

/** Lighten a hex color by a factor (0-1) */
function lightenColor(hex, factor) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    const lr = Math.min(255, Math.floor(r + (255 - r) * factor));
    const lg = Math.min(255, Math.floor(g + (255 - g) * factor));
    const lb = Math.min(255, Math.floor(b + (255 - b) * factor));
    return `rgb(${lr},${lg},${lb})`;
}

// ─── Per-Face Geometry + Materials ───────────────────────────────────────────
// Every die type gets per-face material groups so each face shows its own number.

/**
 * Compute per-face UVs by projecting each face's 3D vertices onto a 2D plane
 * centered in the [0,1] texture space, so the number at the center is visible.
 */
function computePerFaceUVs(geometry, faceCount, vertsPerFace) {
    const pos = geometry.getAttribute('position').array;
    const totalVerts = faceCount * vertsPerFace;
    const uvs = new Float32Array(totalVerts * 2);

    for (let f = 0; f < faceCount; f++) {
        const base = f * vertsPerFace;

        // Collect 3D positions
        const pts = [];
        for (let v = 0; v < vertsPerFace; v++) {
            const i = (base + v) * 3;
            pts.push({ x: pos[i], y: pos[i + 1], z: pos[i + 2] });
        }

        // Centroid
        let cx = 0, cy = 0, cz = 0;
        for (const p of pts) { cx += p.x; cy += p.y; cz += p.z; }
        cx /= pts.length; cy /= pts.length; cz /= pts.length;

        // Face normal from first triangle
        const e1x = pts[1].x - pts[0].x, e1y = pts[1].y - pts[0].y, e1z = pts[1].z - pts[0].z;
        const e2x = pts[2].x - pts[0].x, e2y = pts[2].y - pts[0].y, e2z = pts[2].z - pts[0].z;
        // cross product
        let nx = e1y * e2z - e1z * e2y;
        let ny = e1z * e2x - e1x * e2z;
        let nz = e1x * e2y - e1y * e2x;
        const nLen = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
        nx /= nLen; ny /= nLen; nz /= nLen;

        // Tangent = normalized e1
        const e1Len = Math.sqrt(e1x * e1x + e1y * e1y + e1z * e1z) || 1;
        const tx = e1x / e1Len, ty = e1y / e1Len, tz = e1z / e1Len;

        // Bitangent = normal x tangent
        const bx = ny * tz - nz * ty;
        const by = nz * tx - nx * tz;
        const bz = nx * ty - ny * tx;

        // Project to 2D
        const pts2D = pts.map(p => {
            const dx = p.x - cx, dy = p.y - cy, dz = p.z - cz;
            return [dx * tx + dy * ty + dz * tz, dx * bx + dy * by + dz * bz];
        });

        // Normalize: centroid maps to texture center (0.5, 0.5).
        // pts2D is already centroid-centered (3D centroid subtracted before projection),
        // so the centroid in 2D is at (0,0). Scale so all vertices fit in [0.06, 0.94].
        let maxExtent = 0;
        for (const [u, v] of pts2D) {
            maxExtent = Math.max(maxExtent, Math.abs(u), Math.abs(v));
        }
        const scale = maxExtent > 0 ? 0.44 / maxExtent : 1;

        for (let v = 0; v < vertsPerFace; v++) {
            const idx = (base + v) * 2;
            uvs[idx]     = 0.5 + pts2D[v][0] * scale;
            uvs[idx + 1] = 0.5 + pts2D[v][1] * scale;
        }
    }

    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
}

/**
 * Compute the outward face normal (in local/model space) for each face group.
 * Returns an array of {x,y,z} unit normals, one per face.
 */
function computeFaceNormals(geometry, faceCount, vertsPerFace) {
    const pos = geometry.getAttribute('position').array;
    const normals = [];

    for (let f = 0; f < faceCount; f++) {
        const base = f * vertsPerFace * 3;
        const ax = pos[base], ay = pos[base + 1], az = pos[base + 2];
        const bx = pos[base + 3], by = pos[base + 4], bz = pos[base + 5];
        const cx2 = pos[base + 6], cy2 = pos[base + 7], cz2 = pos[base + 8];

        const e1x = bx - ax, e1y = by - ay, e1z = bz - az;
        const e2x = cx2 - ax, e2y = cy2 - ay, e2z = cz2 - az;
        let nx = e1y * e2z - e1z * e2y;
        let ny = e1z * e2x - e1x * e2z;
        let nz = e1x * e2y - e1y * e2x;
        const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
        normals.push({ x: nx / len, y: ny / len, z: nz / len });
    }

    return normals;
}

/**
 * Build die geometry with per-face material groups, proper UVs, and face normals.
 * Returns { geometry, faceCount, vertsPerFace, faceNormals }.
 */
function buildDieGeometry(sides, radius) {
    // D6: BoxGeometry natively has 6 material groups
    if (sides === 6) {
        const geo = new THREE.BoxGeometry(radius * 1.4, radius * 1.4, radius * 1.4);
        return { geometry: geo, faceCount: 6, isBox: true, faceNormals: null };
    }

    let baseGeo, faceCount, trisPerFace;

    switch (sides) {
        case 4:
            baseGeo = new THREE.TetrahedronGeometry(radius, 0);
            faceCount = 4; trisPerFace = 1;
            break;
        case 8:
            baseGeo = new THREE.OctahedronGeometry(radius, 0);
            faceCount = 8; trisPerFace = 1;
            break;
        case 12:
            baseGeo = new THREE.DodecahedronGeometry(radius, 0);
            faceCount = 12; trisPerFace = 3;
            break;
        case 10:
            return buildD10Geometry(radius);
        case 20:
        default:
            baseGeo = new THREE.IcosahedronGeometry(radius, 0);
            faceCount = 20; trisPerFace = 1;
            break;
    }

    // Convert to non-indexed for independent per-face vertices
    const geo = baseGeo.toNonIndexed();
    geo.clearGroups();

    const vertsPerFace = trisPerFace * 3;
    for (let f = 0; f < faceCount; f++) {
        geo.addGroup(f * vertsPerFace, vertsPerFace, f);
    }

    computePerFaceUVs(geo, faceCount, vertsPerFace);
    const faceNormals = computeFaceNormals(geo, faceCount, vertsPerFace);

    return { geometry: geo, faceCount, vertsPerFace, faceNormals };
}

/**
 * D10 (pentagonal trapezohedron) geometry.
 * Constructed as the dual of a pentagonal antiprism:
 *   - 2 pole vertices (top/bottom)
 *   - 2 rings of 5 equatorial vertices, offset 36° from each other
 *   - 10 kite faces: 5 connect to the top pole, 5 connect to the bottom pole
 *   - The two sets of kites interlock at the equator (the signature trapezohedron look)
 */
function buildD10Geometry(radius) {
    // --- Step 1: define a pentagonal antiprism ---
    const h_a = radius * 0.55;   // antiprism half-height (for equatorial ring placement)
    const r_a = radius * 0.90;   // antiprism pentagon circumradius

    const topRing = [], botRing = [];
    for (let k = 0; k < 5; k++) {
        const aT = (2 * Math.PI * k) / 5;
        topRing.push([r_a * Math.cos(aT), h_a, r_a * Math.sin(aT)]);
        const aB = aT + Math.PI / 5;   // rotated 36°
        botRing.push([r_a * Math.cos(aB), -h_a, r_a * Math.sin(aB)]);
    }

    // --- Step 2: dual vertices = face centroids of the antiprism ---
    // Poles extended well beyond equator for the tall diamond shape of a real d10
    const pole_h = radius * 1.30;  // tall pointed poles (>> h_a)
    const topPole = [0, pole_h, 0];    // index 0
    const botPole = [0, -pole_h, 0];   // index 1

    // "Down" triangle centroids: (topRing[k] + botRing[k] + topRing[k+1]) / 3
    const D = [];   // indices 2-6
    for (let k = 0; k < 5; k++) {
        const kn = (k + 1) % 5;
        D.push([
            (topRing[k][0] + botRing[k][0] + topRing[kn][0]) / 3,
            (topRing[k][1] + botRing[k][1] + topRing[kn][1]) / 3,
            (topRing[k][2] + botRing[k][2] + topRing[kn][2]) / 3,
        ]);
    }

    // "Up" triangle centroids: (botRing[k] + topRing[k+1] + botRing[k+1]) / 3
    const U = [];   // indices 7-11
    for (let k = 0; k < 5; k++) {
        const kn = (k + 1) % 5;
        U.push([
            (botRing[k][0] + topRing[kn][0] + botRing[kn][0]) / 3,
            (botRing[k][1] + topRing[kn][1] + botRing[kn][1]) / 3,
            (botRing[k][2] + topRing[kn][2] + botRing[kn][2]) / 3,
        ]);
    }

    // --- Step 3: scale so the shape fits within `radius` ---
    const allPts = [topPole, botPole, ...D, ...U];
    let maxDist = 0;
    for (const p of allPts) {
        const d = Math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2]);
        if (d > maxDist) maxDist = d;
    }
    const scale = radius / maxDist;

    const vertices = [];
    for (const p of allPts) {
        vertices.push(p[0] * scale, p[1] * scale, p[2] * scale);
    }

    // --- Step 4: build indexed faces (10 kites, 2 tris each) ---
    const indices = [];

    // 5 "top" kites — each connects topPole to three equatorial verts
    // Kite for antiprism top vertex k: topPole, D[(k-1)%5], U[(k-1)%5], D[k]
    // Split along diagonal topPole ↔ U[(k-1)%5]
    for (let k = 0; k < 5; k++) {
        const km = (k + 4) % 5;
        const iDkm = 2 + km;   // D[(k-1)%5]
        const iUkm = 7 + km;   // U[(k-1)%5]
        const iDk  = 2 + k;    // D[k]
        indices.push(0, iDkm, iUkm);   // tri 1
        indices.push(0, iUkm, iDk);    // tri 2
    }

    // 5 "bottom" kites — each connects botPole to three equatorial verts
    // Kite for antiprism bottom vertex k: botPole, U[(k-1)%5], D[k], U[k]
    // Split along diagonal botPole ↔ D[k]
    for (let k = 0; k < 5; k++) {
        const km = (k + 4) % 5;
        const iUkm = 7 + km;   // U[(k-1)%5]
        const iDk  = 2 + k;    // D[k]
        const iUk  = 7 + k;    // U[k]
        indices.push(1, iUkm, iDk);    // tri 1
        indices.push(1, iDk, iUk);     // tri 2
    }

    // --- Step 5: create geometry, fix winding, convert to non-indexed ---
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    indexed.setIndex(indices);

    fixWinding(indexed);
    indexed.computeVertexNormals();

    const geo = indexed.toNonIndexed();
    geo.clearGroups();

    const vertsPerFace = 6;
    for (let f = 0; f < 10; f++) {
        geo.addGroup(f * vertsPerFace, vertsPerFace, f);
    }

    computePerFaceUVs(geo, 10, vertsPerFace);
    const faceNormals = computeFaceNormals(geo, 10, vertsPerFace);

    return { geometry: geo, faceCount: 10, vertsPerFace, faceNormals };
}

/**
 * Fix triangle winding so all face normals point outward.
 * For each triangle, check if the computed normal points away from the mesh centroid.
 * If not, swap two vertices to flip the normal.
 */
function fixWinding(geo) {
    const pos = geo.getAttribute('position');
    const idx = geo.getIndex();
    if (!idx) return;

    const arr = idx.array;
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();

    // Mesh centroid
    const centroid = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) {
        a.fromBufferAttribute(pos, i);
        centroid.add(a);
    }
    centroid.divideScalar(pos.count);

    for (let i = 0; i < arr.length; i += 3) {
        a.fromBufferAttribute(pos, arr[i]);
        b.fromBufferAttribute(pos, arr[i + 1]);
        c.fromBufferAttribute(pos, arr[i + 2]);

        const e1 = new THREE.Vector3().subVectors(b, a);
        const e2 = new THREE.Vector3().subVectors(c, a);
        const normal = new THREE.Vector3().crossVectors(e1, e2);

        // Face center
        const center = new THREE.Vector3().addVectors(a, b).add(c).divideScalar(3);
        const outward = new THREE.Vector3().subVectors(center, centroid);

        if (normal.dot(outward) < 0) {
            // Flip: swap indices 1 and 2
            const tmp = arr[i + 1];
            arr[i + 1] = arr[i + 2];
            arr[i + 2] = tmp;
        }
    }
    idx.needsUpdate = true;
}

/**
 * Create per-face materials array. Each face gets its own numbered texture.
 */
function createDieMaterials(sides, faceCount) {
    const materials = [];
    for (let f = 0; f < faceCount; f++) {
        const faceValue = f + 1;
        const canvas = createNumberedTexture(sides, faceValue, false);
        const texture = new THREE.CanvasTexture(canvas);
        texture.needsUpdate = true;
        materials.push(new THREE.MeshStandardMaterial({
            map: texture,
            roughness: 0.35,
            metalness: 0.1,
        }));
    }
    return materials;
}

/**
 * Create D6 per-face materials (box faces: +X, -X, +Y, -Y, +Z, -Z).
 * Standard d6 opposite faces sum to 7.
 */
function createD6Materials() {
    // Three.js BoxGeometry face order: +X, -X, +Y, -Y, +Z, -Z
    const faceValues = [1, 6, 2, 5, 3, 4];
    const materials = [];
    for (const val of faceValues) {
        const canvas = createNumberedTexture(6, val, true);
        const texture = new THREE.CanvasTexture(canvas);
        texture.needsUpdate = true;
        materials.push(new THREE.MeshStandardMaterial({ map: texture, roughness: 0.35, metalness: 0.1 }));
    }
    return materials;
}

// ─── Physics Shapes ──────────────────────────────────────────────────────────

function getPhysicsShape(sides, radius) {
    switch (sides) {
        case 6:
            const half = radius * 0.7;
            return new CANNON.Box(new CANNON.Vec3(half, half, half));
        default:
            return new CANNON.Sphere(radius * 0.85);
    }
}

// ─── D6 Face Detection & Correction ─────────────────────────────────────────

const D6_NORMALS = [
    { x: 1, y: 0, z: 0 },   // +X → value 1
    { x: -1, y: 0, z: 0 },  // -X → value 6
    { x: 0, y: 1, z: 0 },   // +Y → value 2
    { x: 0, y: -1, z: 0 },  // -Y → value 5
    { x: 0, y: 0, z: 1 },   // +Z → value 3
    { x: 0, y: 0, z: -1 },  // -Z → value 4
];
const D6_VALUES = [1, 6, 2, 5, 3, 4];

function getD6QuaternionForValue(value) {
    const q = new THREE.Quaternion();
    switch (value) {
        case 1: q.setFromAxisAngle(new THREE.Vector3(0, 0, 1), -Math.PI / 2); break;
        case 2: /* identity — +Y is already up */ break;
        case 3: q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2); break;
        case 4: q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2); break;
        case 5: q.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI); break;
        case 6: q.setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2); break;
    }
    return q;
}

// ─── Generic Face Correction ─────────────────────────────────────────────────
// For non-d6 dice: compute quaternion that rotates the target face's normal to point up.

function computeCorrectionQuaternion(mesh, targetFaceIndex, faceNormals) {
    // Get the local-space normal for the target face
    const ln = faceNormals[targetFaceIndex];
    const localNormal = new THREE.Vector3(ln.x, ln.y, ln.z).normalize();

    // Compute quaternion that rotates localNormal to point straight up.
    // When set as the mesh quaternion, this makes the target face point up.
    const worldUp = new THREE.Vector3(0, 1, 0);
    return new THREE.Quaternion().setFromUnitVectors(localNormal, worldUp);
}

// ─── Main DiceRoller Class ───────────────────────────────────────────────────

class DiceRoller {
    constructor() {
        this.overlay = null;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.world = null;
        this.diceObjects = [];
        this.isAnimating = false;
        this.enabled = true;
        this._animId = null;
        this._startTime = 0;

        try {
            this.enabled = localStorage.getItem('dice3d_enabled') !== 'false';
        } catch (e) {}
    }

    setEnabled(val) {
        this.enabled = val;
        try { localStorage.setItem('dice3d_enabled', val ? 'true' : 'false'); } catch (e) {}
    }

    static isSupported() {
        try {
            const c = document.createElement('canvas');
            return !!(c.getContext('webgl2') || c.getContext('webgl'));
        } catch (e) { return false; }
    }

    async roll(config) {
        // config: { dice: [{sides, value}], modifier, total, label, detail, isCrit, isFumble }
        if (!this.enabled || !DiceRoller.isSupported()) return false;
        if (this.isAnimating) this.cleanup();

        try {
            await loadLibs();
        } catch (e) {
            console.warn('Dice3D: Failed to load libraries', e);
            return false;
        }

        this.isAnimating = true;
        this._settled = false;
        this._createOverlay();
        this._initScene();
        this._initPhysics();
        this._createDice(config.dice);
        this._throwDice();
        this._startTime = performance.now();

        // Fire-and-forget: animation continues in background
        this._animate(config);
        return true;
    }

    _createOverlay() {
        this.overlay = document.createElement('div');
        this.overlay.id = 'dice-roller-overlay';
        this.overlay.innerHTML = `
            <canvas id="dice-3d-canvas"></canvas>
            <div id="dice-result-display" class="dice-result-hidden"></div>
            <div id="dice-dismiss-hint">tap to dismiss</div>
        `;
        document.body.appendChild(this.overlay);

        if (!document.getElementById('dice-roller-css')) {
            const style = document.createElement('style');
            style.id = 'dice-roller-css';
            style.textContent = DICE_CSS;
            document.head.appendChild(style);
        }

        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay || e.target.id === 'dice-3d-canvas' || e.target.id === 'dice-dismiss-hint') {
                this.cleanup();
            }
        });
    }

    _initScene() {
        const canvas = document.getElementById('dice-3d-canvas');
        const w = window.innerWidth;
        const h = window.innerHeight;

        this.scene = new THREE.Scene();

        this.camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 100);
        this.camera.position.set(0, 18, 12);
        this.camera.lookAt(0, 0, 0);

        this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
        this.renderer.setSize(w, h);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

        const ambient = new THREE.AmbientLight(0xffffff, 0.65);
        this.scene.add(ambient);

        const dirLight = new THREE.DirectionalLight(0xffffff, 1.1);
        dirLight.position.set(5, 15, 8);
        dirLight.castShadow = true;
        dirLight.shadow.mapSize.set(1024, 1024);
        dirLight.shadow.camera.near = 0.5;
        dirLight.shadow.camera.far = 50;
        dirLight.shadow.camera.left = -10;
        dirLight.shadow.camera.right = 10;
        dirLight.shadow.camera.top = 10;
        dirLight.shadow.camera.bottom = -10;
        this.scene.add(dirLight);

        const rimLight = new THREE.DirectionalLight(0x8ED4D4, 0.3);
        rimLight.position.set(-3, 10, -5);
        this.scene.add(rimLight);

        const floorGeo = new THREE.PlaneGeometry(30, 30);
        const floorMat = new THREE.ShadowMaterial({ opacity: 0.15 });
        const floor = new THREE.Mesh(floorGeo, floorMat);
        floor.rotation.x = -Math.PI / 2;
        floor.position.y = -0.01;
        floor.receiveShadow = true;
        this.scene.add(floor);
    }

    _initPhysics() {
        this.world = new CANNON.World({
            gravity: new CANNON.Vec3(0, PHYSICS.gravity, 0)
        });
        this.world.broadphase = new CANNON.NaiveBroadphase();
        this.world.solver.iterations = 16;

        const floorBody = new CANNON.Body({
            mass: 0,
            shape: new CANNON.Plane(),
            material: new CANNON.Material({ friction: PHYSICS.friction, restitution: PHYSICS.restitution })
        });
        floorBody.quaternion.setFromEuler(-Math.PI / 2, 0, 0);
        this.world.addBody(floorBody);

        const wallPositions = [
            { pos: [8, 4, 0], rot: [0, -Math.PI / 2, 0] },
            { pos: [-8, 4, 0], rot: [0, Math.PI / 2, 0] },
            { pos: [0, 4, 6], rot: [0, Math.PI, 0] },
            { pos: [0, 4, -6], rot: [0, 0, 0] },
        ];
        for (const w of wallPositions) {
            const wall = new CANNON.Body({ mass: 0, shape: new CANNON.Plane() });
            wall.position.set(...w.pos);
            wall.quaternion.setFromEuler(...w.rot);
            this.world.addBody(wall);
        }
    }

    _createDice(diceConfig) {
        const count = diceConfig.length;
        const spread = Math.min(count * 1.8, 8);

        for (let i = 0; i < count; i++) {
            const { sides, value } = diceConfig[i];
            const dieRadius = sides === 6 ? 0.7 : (sides <= 8 ? 0.8 : (sides <= 12 ? 0.9 : 1.0));

            // Build geometry with per-face groups
            const dieInfo = buildDieGeometry(sides, dieRadius);

            // Create per-face materials
            let materials;
            if (dieInfo.isBox) {
                materials = createD6Materials();
            } else {
                materials = createDieMaterials(sides, dieInfo.faceCount);
            }

            const mesh = new THREE.Mesh(dieInfo.geometry, materials);
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            mesh.userData = { dieType: sides, targetValue: value };
            this.scene.add(mesh);

            // Physics body
            const shape = getPhysicsShape(sides, dieRadius);
            const body = new CANNON.Body({
                mass: 1.2,
                shape: shape,
                linearDamping: PHYSICS.linearDamping,
                angularDamping: PHYSICS.angularDamping,
                material: new CANNON.Material({
                    friction: PHYSICS.friction,
                    restitution: PHYSICS.restitution,
                }),
            });

            const xOff = (i - (count - 1) / 2) * (spread / Math.max(count, 1));
            body.position.set(
                xOff + (Math.random() - 0.5) * 1.5,
                5 + Math.random() * 2,
                -2 + Math.random() * 1.5
            );
            this.world.addBody(body);

            this.diceObjects.push({
                mesh, body, sides, targetValue: value, settled: false,
                faceNormals: dieInfo.faceNormals,
                faceCount: dieInfo.faceCount,
                isBox: !!dieInfo.isBox,
            });
        }
    }

    _throwDice() {
        for (const die of this.diceObjects) {
            const fx = (Math.random() - 0.5) * 8;
            const fy = -(8 + Math.random() * 5);
            const fz = (Math.random() - 0.5) * 5;
            die.body.velocity.set(fx, fy, fz);

            die.body.angularVelocity.set(
                (Math.random() - 0.5) * 18,
                (Math.random() - 0.5) * 18,
                (Math.random() - 0.5) * 18
            );

            die.body.quaternion.setFromEuler(
                Math.random() * Math.PI * 2,
                Math.random() * Math.PI * 2,
                Math.random() * Math.PI * 2
            );
        }
    }

    _animate(config) {
        const step = () => {
            if (!this.isAnimating) return;

            const elapsed = performance.now() - this._startTime;
            const dt = 1 / 60;
            this.world.step(dt, dt, 3);

            let allSettled = true;
            for (const die of this.diceObjects) {
                die.mesh.position.copy(die.body.position);
                die.mesh.quaternion.copy(die.body.quaternion);

                const speed = die.body.velocity.length() + die.body.angularVelocity.length();
                if (speed < PHYSICS.settleSpeed && die.body.position.y < 2) {
                    die.settled = true;
                } else {
                    allSettled = false;
                }
            }

            if (elapsed > PHYSICS.maxTime) {
                allSettled = true;
                for (const die of this.diceObjects) {
                    die.body.velocity.set(0, 0, 0);
                    die.body.angularVelocity.set(0, 0, 0);
                    die.settled = true;
                }
            }

            this.renderer.render(this.scene, this.camera);

            if (allSettled && !this._settled) {
                this._settled = true;
                this._correctAndShow(config);
            } else {
                this._animId = requestAnimationFrame(step);
            }
        };

        this._animId = requestAnimationFrame(step);
    }

    _correctAndShow(config) {
        const corrections = [];

        for (const die of this.diceObjects) {
            die.body.velocity.set(0, 0, 0);
            die.body.angularVelocity.set(0, 0, 0);
            die.body.type = CANNON.Body.STATIC;

            if (die.isBox) {
                // D6: use hardcoded quaternion for target value
                const targetQuat = getD6QuaternionForValue(die.targetValue);
                corrections.push({ mesh: die.mesh, body: die.body, targetQuat });
            } else {
                // All other dice: rotate so the face with targetValue is on top
                // Face index = targetValue - 1 (face 0 = value 1, face 1 = value 2, ...)
                const targetFaceIdx = die.targetValue - 1;
                if (targetFaceIdx >= 0 && targetFaceIdx < die.faceNormals.length) {
                    const targetQuat = computeCorrectionQuaternion(die.mesh, targetFaceIdx, die.faceNormals);
                    corrections.push({ mesh: die.mesh, body: die.body, targetQuat });
                }
            }
        }

        if (corrections.length > 0) {
            const startTime = performance.now();
            const startQuats = corrections.map(c => c.mesh.quaternion.clone());

            const correctStep = () => {
                const t = Math.min(1, (performance.now() - startTime) / PHYSICS.correctionTime);
                const eased = 1 - Math.pow(1 - t, 3);

                for (let i = 0; i < corrections.length; i++) {
                    corrections[i].mesh.quaternion.slerpQuaternions(startQuats[i], corrections[i].targetQuat, eased);
                    corrections[i].body.quaternion.set(
                        corrections[i].mesh.quaternion.x,
                        corrections[i].mesh.quaternion.y,
                        corrections[i].mesh.quaternion.z,
                        corrections[i].mesh.quaternion.w
                    );
                }
                this.renderer.render(this.scene, this.camera);

                if (t < 1) {
                    requestAnimationFrame(correctStep);
                } else {
                    this._showResult(config);
                }
            };
            requestAnimationFrame(correctStep);
        } else {
            this._showResult(config);
        }
    }

    _showResult(config) {
        const display = document.getElementById('dice-result-display');
        if (!display) return;

        const isCrit = config.isCrit;
        const isFumble = config.isFumble;
        const critClass = isCrit ? 'crit' : (isFumble ? 'fumble' : '');

        const diceFaces = config.dice.map((d, i) => {
            const colors = DIE_COLORS[d.sides] || DIE_COLORS[20];
            const isNat20 = d.sides === 20 && d.value === 20;
            const isNat1 = d.sides === 20 && d.value === 1;
            const faceClass = isNat20 ? 'dice-face-nat20' : (isNat1 ? 'dice-face-nat1' : '');
            return `<span class="dice-result-face ${faceClass}" style="background: linear-gradient(135deg, ${colors.base}, ${colors.accent}); color: ${colors.text}; animation-delay: ${i * 0.06}s">${d.value}</span>`;
        }).join('');

        let badge = '';
        if (isCrit) badge = '<span class="dice-badge-crit">NAT 20!</span>';
        else if (isFumble) badge = '<span class="dice-badge-fumble">NAT 1</span>';

        display.innerHTML = `
            <div class="dice-result-faces">${diceFaces}</div>
            <div class="dice-result-total ${critClass}">${config.total}${badge}</div>
            <div class="dice-result-label">${config.label || ''}</div>
            <div class="dice-result-detail">${config.detail || ''}</div>
        `;
        display.className = 'dice-result-show';

        // Continue rendering for crit glow
        const glowStart = performance.now();
        const glowLoop = () => {
            if (!this.isAnimating) return;
            const t = (performance.now() - glowStart) / 1000;

            if (isCrit) {
                for (const die of this.diceObjects) {
                    if (die.sides === 20) {
                        const mats = Array.isArray(die.mesh.material) ? die.mesh.material : [die.mesh.material];
                        for (const m of mats) {
                            if (m.emissiveIntensity !== undefined) {
                                m.emissiveIntensity = 0.2 + Math.sin(t * 4) * 0.15;
                                m.emissive = new THREE.Color('#f59e0b');
                            }
                        }
                    }
                }
            }
            this.renderer.render(this.scene, this.camera);
            this._animId = requestAnimationFrame(glowLoop);
        };
        requestAnimationFrame(glowLoop);

        this._dismissTimer = setTimeout(() => this.cleanup(), 3000);
    }

    cleanup() {
        this.isAnimating = false;
        this._settled = false;
        if (this._animId) cancelAnimationFrame(this._animId);
        if (this._dismissTimer) clearTimeout(this._dismissTimer);

        if (this.overlay && this.overlay.parentNode) {
            this.overlay.parentNode.removeChild(this.overlay);
        }
        this.overlay = null;

        if (this.renderer) {
            this.renderer.dispose();
            this.renderer = null;
        }
        if (this.scene) {
            this.scene.traverse(obj => {
                if (obj.geometry) obj.geometry.dispose();
                if (obj.material) {
                    if (Array.isArray(obj.material)) {
                        obj.material.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
                    } else {
                        if (obj.material.map) obj.material.map.dispose();
                        obj.material.dispose();
                    }
                }
            });
            this.scene = null;
        }
        this.camera = null;
        this.world = null;
        this.diceObjects = [];
    }
}

// ─── CSS ─────────────────────────────────────────────────────────────────────

const DICE_CSS = `
#dice-roller-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    pointer-events: none;
    animation: diceOverlayIn 0.15s ease-out;
}
@keyframes diceOverlayIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

#dice-3d-canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: auto;
}

#dice-result-display {
    position: absolute;
    bottom: 15%;
    left: 50%;
    transform: translateX(-50%);
    text-align: center;
    pointer-events: none;
    transition: opacity 0.3s, transform 0.3s;
    font-family: 'Cinzel', serif;
}
.dice-result-hidden {
    opacity: 0;
    transform: translateX(-50%) translateY(20px);
}
.dice-result-show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
}

.dice-result-faces {
    display: flex;
    justify-content: center;
    gap: 8px;
    margin-bottom: 12px;
}
.dice-result-face {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 8px;
    font-weight: 800;
    font-size: 18px;
    font-family: 'Cinzel', serif;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    animation: diceFacePop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both;
}
@keyframes diceFacePop {
    0% { transform: scale(0) rotate(-20deg); opacity: 0; }
    100% { transform: scale(1) rotate(0deg); opacity: 1; }
}

.dice-face-nat20 {
    box-shadow: 0 0 20px 6px rgba(245, 158, 11, 0.5);
    animation: diceFacePop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both,
               diceNat20Glow 1.5s ease-in-out infinite 0.4s;
}
@keyframes diceNat20Glow {
    0%, 100% { box-shadow: 0 0 12px 4px rgba(245, 158, 11, 0.4); }
    50% { box-shadow: 0 0 24px 8px rgba(245, 158, 11, 0.7); }
}

.dice-face-nat1 {
    border: 2px solid #ef4444;
    box-shadow: 0 0 12px 4px rgba(239, 68, 68, 0.4);
}

.dice-result-total {
    font-size: 56px;
    font-weight: 900;
    color: #fff;
    text-shadow: 0 2px 20px rgba(0,0,0,0.5);
    line-height: 1;
    margin-bottom: 4px;
    animation: diceTotalIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) 0.2s both;
}
@keyframes diceTotalIn {
    0% { transform: scale(0.3); opacity: 0; }
    80% { transform: scale(1.08); }
    100% { transform: scale(1); opacity: 1; }
}

.dice-result-total.crit {
    color: #fbbf24;
    text-shadow: 0 0 30px rgba(251, 191, 36, 0.6);
}
.dice-result-total.fumble {
    color: #ef4444;
    text-shadow: 0 0 20px rgba(239, 68, 68, 0.5);
}

.dice-badge-crit {
    display: inline-block;
    background: #f59e0b;
    color: #1c1917;
    font-size: 12px;
    font-weight: 800;
    padding: 2px 10px;
    border-radius: 4px;
    margin-left: 8px;
    vertical-align: super;
    animation: diceBadgeIn 0.3s ease-out 0.5s both;
}
.dice-badge-fumble {
    display: inline-block;
    background: #dc2626;
    color: #fff;
    font-size: 12px;
    font-weight: 800;
    padding: 2px 10px;
    border-radius: 4px;
    margin-left: 8px;
    vertical-align: super;
    animation: diceBadgeIn 0.3s ease-out 0.5s both;
}
@keyframes diceBadgeIn {
    from { transform: scale(0) rotate(-10deg); opacity: 0; }
    to { transform: scale(1) rotate(0deg); opacity: 1; }
}

.dice-result-label {
    font-size: 16px;
    color: #8ED4D4;
    font-weight: 700;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
    animation: diceDetailIn 0.3s ease-out 0.4s both;
}
.dice-result-detail {
    font-size: 13px;
    color: rgba(255,255,255,0.6);
    font-family: 'Courier New', monospace;
    animation: diceDetailIn 0.3s ease-out 0.5s both;
}
@keyframes diceDetailIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}

#dice-dismiss-hint {
    position: absolute;
    bottom: 5%;
    left: 50%;
    transform: translateX(-50%);
    color: rgba(255,255,255,0.3);
    font-size: 11px;
    font-family: 'Cinzel', serif;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    pointer-events: auto;
    cursor: pointer;
    padding: 8px 16px;
    animation: diceDetailIn 0.3s ease-out 1s both;
}
`;

// ─── Global Instance ─────────────────────────────────────────────────────────
window.DiceRoller = DiceRoller;
window.diceRoller = new DiceRoller();
