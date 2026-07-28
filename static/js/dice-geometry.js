import * as THREE from 'three';

/* ------------------------------------------------------------------ *
 * Numeral glyphs -> real extruded geometry.
 * The browser renders the digits, we trace the bitmap into contours
 * (marching squares + simplify + smooth) and extrude the resulting
 * THREE.Shape. Real geometry => survives OBJ/GLB export.
 * ------------------------------------------------------------------ */

const GLYPH_PX = 180;
const glyphCache = new Map();

function rasterize(text) {
  const pad = 24;
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d', { willReadFrequently: true });
  const font = `700 ${GLYPH_PX}px "Helvetica Neue", Helvetica, Arial, sans-serif`;
  ctx.font = font;
  const m = ctx.measureText(text);
  const w = Math.ceil(m.width) + pad * 2;
  const asc = m.actualBoundingBoxAscent || GLYPH_PX * 0.72;
  const desc = m.actualBoundingBoxDescent || 0;
  const h = Math.ceil(asc + desc) + pad * 2;
  c.width = w; c.height = h;
  const ctx2 = c.getContext('2d', { willReadFrequently: true });
  ctx2.font = font;
  ctx2.fillStyle = '#fff';
  ctx2.textBaseline = 'alphabetic';
  ctx2.fillText(text, pad, pad + asc);
  const data = ctx2.getImageData(0, 0, w, h).data;
  const grid = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) grid[i] = data[i * 4 + 3] > 128 ? 1 : 0;
  return { grid, w, h, capHeight: asc };
}

function marchingSquares(grid, W, H) {
  const at = (x, y) => (x < 0 || y < 0 || x >= W || y >= H ? 0 : grid[y * W + x]);
  const segs = [];
  for (let y = -1; y < H; y++) {
    for (let x = -1; x < W; x++) {
      const tl = at(x, y), tr = at(x + 1, y), br = at(x + 1, y + 1), bl = at(x, y + 1);
      const idx = (tl ? 8 : 0) | (tr ? 4 : 0) | (br ? 2 : 0) | (bl ? 1 : 0);
      if (idx === 0 || idx === 15) continue;
      const T = [x + 0.5, y], R = [x + 1, y + 0.5], B = [x + 0.5, y + 1], L = [x, y + 0.5];
      const p = (a, b) => segs.push([a, b]);
      switch (idx) {
        case 1: p(L, B); break;
        case 2: p(B, R); break;
        case 3: p(L, R); break;
        case 4: p(R, T); break;
        case 5: p(L, T); p(B, R); break;
        case 6: p(B, T); break;
        case 7: p(L, T); break;
        case 8: p(T, L); break;
        case 9: p(T, B); break;
        case 10: p(T, R); p(B, L); break;
        case 11: p(T, R); break;
        case 12: p(R, L); break;
        case 13: p(R, B); break;
        case 14: p(B, L); break;
      }
    }
  }
  const key = (pt) => `${pt[0].toFixed(1)},${pt[1].toFixed(1)}`;
  const byStart = new Map();
  for (const s of segs) {
    const k = key(s[0]);
    if (!byStart.has(k)) byStart.set(k, []);
    byStart.get(k).push(s);
  }
  const used = new Set();
  const loops = [];
  for (const s of segs) {
    if (used.has(s)) continue;
    const loop = [s[0]];
    let cur = s;
    while (cur && !used.has(cur)) {
      used.add(cur);
      loop.push(cur[1]);
      const nexts = byStart.get(key(cur[1])) || [];
      cur = nexts.find((n) => !used.has(n));
    }
    if (loop.length > 6) loops.push(loop);
  }
  return loops;
}

/** Douglas-Peucker on a CLOSED loop: split at the antipode first, or the
 *  degenerate start==end chord collapses the whole contour to two points. */
function simplify(pts, eps) {
  let p = pts;
  const n0 = p.length;
  if (n0 > 2 && p[0][0] === p[n0 - 1][0] && p[0][1] === p[n0 - 1][1]) p = p.slice(0, -1);
  if (p.length < 8) return p;
  let far = 0, fd = -1;
  for (let i = 1; i < p.length; i++) {
    const d = (p[i][0] - p[0][0]) ** 2 + (p[i][1] - p[0][1]) ** 2;
    if (d > fd) { fd = d; far = i; }
  }
  const a = dp(p.slice(0, far + 1), eps);
  const b = dp(p.slice(far).concat([p[0]]), eps);
  return a.slice(0, -1).concat(b.slice(0, -1));
}

function dp(pts, eps) {
  if (pts.length < 3) return pts;
  const keep = new Uint8Array(pts.length);
  keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    let far = -1, fd = 0;
    const ax = pts[a][0], ay = pts[a][1], bx = pts[b][0], by = pts[b][1];
    const dx = bx - ax, dy = by - ay;
    const len = Math.hypot(dx, dy) || 1;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs((pts[i][0] - ax) * dy - (pts[i][1] - ay) * dx) / len;
      if (d > fd) { fd = d; far = i; }
    }
    if (fd > eps && far > 0) { keep[far] = 1; stack.push([a, far], [far, b]); }
  }
  return pts.filter((_, i) => keep[i]);
}

function chaikin(pts, iters) {
  let out = pts;
  for (let k = 0; k < iters; k++) {
    const next = [];
    const n = out.length;
    for (let i = 0; i < n; i++) {
      const a = out[i], b = out[(i + 1) % n];
      next.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25]);
      next.push([a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
    }
    out = next;
  }
  return out;
}

const signedArea = (p) => {
  let a = 0;
  for (let i = 0, n = p.length; i < n; i++) {
    const q = p[(i + 1) % n];
    a += p[i][0] * q[1] - q[0] * p[i][1];
  }
  return a / 2;
};

function contains(poly, pt) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** THREE.Shape[] for a label, normalised: cap height = 1, centred on origin. */
export function labelShapes(text) {
  if (glyphCache.has(text)) return glyphCache.get(text);
  const { grid, w, h, capHeight } = rasterize(text);
  let loops = marchingSquares(grid, w, h).map((l) => chaikin(simplify(l, 1.1), 2));
  loops = loops.filter((l) => Math.abs(signedArea(l)) > 12);

  // bounds in pixel space
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const l of loops) for (const p of l) {
    if (p[0] < minX) minX = p[0]; if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1]; if (p[1] > maxY) maxY = p[1];
  }
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, s = 1 / capHeight;
  const toLocal = (l) => l.map((p) => [(p[0] - cx) * s, -(p[1] - cy) * s]);
  const polys = loops.map(toLocal);

  const outers = [], holes = [];
  polys.forEach((p) => {
    const depth = polys.filter((q) => q !== p && contains(q, p[0])).length;
    (depth % 2 === 0 ? outers : holes).push(p);
  });

  const shapes = outers.map((o) => {
    const pts = (signedArea(o) < 0 ? [...o].reverse() : o).map((p) => new THREE.Vector2(p[0], p[1]));
    const shape = new THREE.Shape(pts);
    holes.filter((hl) => contains(o, hl[0])).forEach((hl) => {
      const hp = (signedArea(hl) > 0 ? [...hl].reverse() : hl).map((p) => new THREE.Vector2(p[0], p[1]));
      shape.holes.push(new THREE.Path(hp));
    });
    return shape;
  });

  const result = { shapes, width: (maxX - minX) * s, height: (maxY - minY) * s };
  glyphCache.set(text, result);
  return result;
}

const numeralGeoCache = new Map();
function numeralGeometry(text) {
  if (numeralGeoCache.has(text)) return numeralGeoCache.get(text);
  const { shapes, width, height } = labelShapes(text);
  const geo = new THREE.ExtrudeGeometry(shapes, {
    depth: 0.16, bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.022, bevelSegments: 2, curveSegments: 4,
  });
  geo.computeBoundingBox();
  const bb = geo.boundingBox;
  geo.translate(-(bb.min.x + bb.max.x) / 2, -(bb.min.y + bb.max.y) / 2, -bb.max.z);
  geo.computeVertexNormals();
  const out = { geo, width, height };
  numeralGeoCache.set(text, out);
  return out;
}

/* ------------------------------------------------------------------ *
 * Solids
 * ------------------------------------------------------------------ */

function roundedBox(size, radius, seg = 5) {
  const geo = new THREE.BoxGeometry(size, size, size, seg, seg, seg);
  const pos = geo.attributes.position;
  const half = size / 2, inner = half - radius;
  const v = new THREE.Vector3(), core = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    core.set(
      THREE.MathUtils.clamp(v.x, -inner, inner),
      THREE.MathUtils.clamp(v.y, -inner, inner),
      THREE.MathUtils.clamp(v.z, -inner, inner),
    );
    v.sub(core).normalize().multiplyScalar(radius).add(core);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  geo.computeVertexNormals();
  return geo.toNonIndexed();
}

/** Pentagonal trapezohedron (d10). The apex height is solved exactly from the
 *  planarity condition for a kite face — rounded constants leave the quads
 *  very slightly bent, which splits each kite into two differently-normalled
 *  triangles and scatters the numerals onto half-faces. */
function trapezohedron() {
  const s1 = Math.sin(Math.PI * 2 / 5), c1 = Math.cos(Math.PI * 2 / 5);
  const c36 = Math.cos(Math.PI / 5), s36 = Math.sin(Math.PI / 5);
  const K = s1 * c36 + (1 - c1) * s36;
  const A = 0.115, H = A * (K + s1) / (K - s1), R = 1;
  const ang = (i) => (i * Math.PI * 2) / 5;
  const U = [], L = [];
  for (let i = 0; i < 5; i++) {
    U.push(new THREE.Vector3(R * Math.cos(ang(i)), A, R * Math.sin(ang(i))));
    L.push(new THREE.Vector3(R * Math.cos(ang(i) + Math.PI / 5), -A, R * Math.sin(ang(i) + Math.PI / 5)));
  }
  const top = new THREE.Vector3(0, H, 0), bot = new THREE.Vector3(0, -H, 0);
  const quads = [];
  for (let i = 0; i < 5; i++) {
    quads.push([top, U[i], L[i], U[(i + 1) % 5]]);
    quads.push([bot, L[i], U[(i + 1) % 5], L[(i + 1) % 5]]);
  }
  const verts = [];
  const n = new THREE.Vector3(), e1 = new THREE.Vector3(), e2 = new THREE.Vector3(), c = new THREE.Vector3();
  for (const q of quads) {
    c.set(0, 0, 0); q.forEach((p) => c.add(p)); c.multiplyScalar(0.25);
    e1.subVectors(q[1], q[0]); e2.subVectors(q[2], q[0]);
    n.crossVectors(e1, e2);
    const o = n.dot(c) < 0 ? [q[0], q[2], q[1], q[0], q[3], q[2]] : [q[0], q[1], q[2], q[0], q[2], q[3]];
    o.forEach((p) => verts.push(p.x, p.y, p.z));
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geo.computeVertexNormals();
  return geo;
}

/** Group coplanar triangles into logical faces. */
function extractFaces(geo, expected) {
  const pos = geo.attributes.position;
  const groups = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), cv = new THREE.Vector3();
  const ab = new THREE.Vector3(), ac = new THREE.Vector3(), nrm = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 3) {
    a.fromBufferAttribute(pos, i); b.fromBufferAttribute(pos, i + 1); cv.fromBufferAttribute(pos, i + 2);
    ab.subVectors(b, a); ac.subVectors(cv, a);
    nrm.crossVectors(ab, ac);
    const area = nrm.length() / 2;
    if (area < 1e-8) continue;
    nrm.normalize();
    let g = null;
    for (const cand of groups) {
      if (cand.normal.dot(nrm) > 0.9995) { g = cand; break; }
    }
    if (!g) { g = { normal: nrm.clone(), area: 0, centroid: new THREE.Vector3(), pts: [] }; groups.push(g); }
    g.area += area;
    g.centroid.add(a.clone().add(b).add(cv).multiplyScalar(area / 3));
    g.pts.push(a.clone(), b.clone(), cv.clone());
  }
  const faces = groups.sort((x, y) => y.area - x.area).slice(0, expected);
  for (const f of faces) {
    f.centroid.multiplyScalar(1 / f.area);
    f.distance = f.centroid.dot(f.normal);
    f.radius = Math.max(...f.pts.map((p) => p.distanceTo(f.centroid)));
  }
  return faces;
}

/** Chebyshev centre of a convex polygon — the deepest inscribed point.
 *  Numerals sit here, not at the centroid: on kite and pentagon faces the two
 *  differ enough that centroid-placed digits read visibly off-centre. */
function faceMetrics(hull) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of hull) {
    if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
  }
  const edgeDist = (x, y) => {
    let m = Infinity;
    for (let i = 0; i < hull.length; i++) {
      const a = hull[i], b = hull[(i + 1) % hull.length];
      const ex = b.x - a.x, ey = b.y - a.y;
      const L = Math.hypot(ex, ey) || 1;
      m = Math.min(m, Math.abs(ex * (y - a.y) - ey * (x - a.x)) / L);
    }
    return m;
  };
  const inside = (x, y) => {
    let c = false;
    for (let i = 0, j = hull.length - 1; i < hull.length; j = i++) {
      const xi = hull[i].x, yi = hull[i].y, xj = hull[j].x, yj = hull[j].y;
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  };
  let bx = 0, by = 0, bd = edgeDist(0, 0);
  let sx = (maxX - minX) / 12, sy = (maxY - minY) / 12;
  for (let i = 0; i <= 12; i++) for (let j = 0; j <= 12; j++) {
    const x = minX + i * sx, y = minY + j * sy;
    if (!inside(x, y)) continue;
    const d = edgeDist(x, y);
    if (d > bd) { bd = d; bx = x; by = y; }
  }
  for (let k = 0; k < 4; k++) {
    sx *= 0.45; sy *= 0.45;
    for (let i = -2; i <= 2; i++) for (let j = -2; j <= 2; j++) {
      const x = bx + i * sx, y = by + j * sy;
      if (!inside(x, y)) continue;
      const d = edgeDist(x, y);
      if (d > bd) { bd = d; bx = x; by = y; }
    }
  }
  return { x: bx, y: by, r: bd };
}

function hull2d(pts) {
  const p = [...pts].sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const build = (src) => {
    const st = [];
    for (const q of src) {
      while (st.length >= 2 && cross(st[st.length - 2], st[st.length - 1], q) <= 0) st.pop();
      st.push(q);
    }
    return st;
  };
  const lower = build(p), upper = build([...p].reverse());
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function assignValues(faces) {
  const n = faces.length;
  const values = new Array(n).fill(0);
  let next = 1;
  for (let i = 0; i < n; i++) {
    if (values[i]) continue;
    let best = -1, bd = -2;
    for (let j = 0; j < n; j++) {
      if (j === i || values[j]) continue;
      const d = -faces[i].normal.dot(faces[j].normal);
      if (d > bd) { bd = d; best = j; }
    }
    values[i] = next;
    if (best >= 0) values[best] = n + 1 - next;
    next++;
  }
  return values;
}

export const DIE_SPECS = {
  d4: { sides: 4, inradius: 0.25, glyph: 0.7, apexRead: true },
  d6: { sides: 6, inradius: 0.36, glyph: 1.4 },
  d8: { sides: 8, inradius: 0.34, glyph: 1.25 },
  d10: { sides: 10, inradius: 0.35, glyph: 1.0 },
  d12: { sides: 12, inradius: 0.40, glyph: 1.1 },
  d20: { sides: 20, inradius: 0.42, glyph: 1.25 },
  d100: { sides: 10, inradius: 0.35, glyph: 0.82, tens: true },
};
export const DIE_TYPES = ['d4', 'd6', 'd8', 'd10', 'd12', 'd20'];
export const ALL_TYPES = [...DIE_TYPES, 'd100'];

function baseGeometry(type) {
  switch (type) {
    case 'd4': return new THREE.TetrahedronGeometry(1).toNonIndexed();
    case 'd6': return roundedBox(1.5, 0.26, 5);
    case 'd8': return new THREE.OctahedronGeometry(1).toNonIndexed();
    case 'd10': case 'd100': return trapezohedron();
    case 'd12': return new THREE.DodecahedronGeometry(1).toNonIndexed();
    default: return new THREE.IcosahedronGeometry(1).toNonIndexed();
  }
}

const shapeCache = new Map();

/** Geometry + face table for a die type, cached and shared across instances. */
export function dieShape(type) {
  if (shapeCache.has(type)) return shapeCache.get(type);
  const spec = DIE_SPECS[type];
  let geo = baseGeometry(type);
  let faces = extractFaces(geo, spec.sides);
  const minDist = Math.min(...faces.map((f) => f.distance));
  const k = spec.inradius / minDist;
  geo.scale(k, k, k);
  geo.computeVertexNormals();
  faces = extractFaces(geo, spec.sides);
  const values = assignValues(faces);

  // unique hull vertices for contact queries
  const pos = geo.attributes.position;
  const seen = new Set();
  const vertices = [];
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const key = `${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    vertices.push(new THREE.Vector3(x, y, z));
  }

  const numerals = [];
  let apex = null;
  const isD10ish = type === 'd10' || type === 'd100';

  const basisFor = (f) => {
    const up = new THREE.Vector3(0, isD10ish && f.centroid.y < 0 ? -1 : 1, 0);
    if (Math.abs(up.dot(f.normal)) > 0.985) up.set(0, 0, 1);
    up.projectOnPlane(f.normal).normalize();
    const right = new THREE.Vector3().crossVectors(up, f.normal).normalize();
    return { up, right };
  };
  const metricsFor = (f, up, right) => {
    const d = new THREE.Vector3();
    const flat = f.pts.map((p) => {
      d.subVectors(p, f.centroid);
      return new THREE.Vector2(d.dot(right), d.dot(up));
    });
    const m = faceMetrics(hull2d(flat));
    if (!(m.r > 1e-6)) { m.r = f.radius * 0.5; m.x = 0; m.y = 0; }
    // stay near the centroid: the deepest inscribed point can sit far out on a
    // kite face, which pushed d10 numerals across the edge onto their neighbour
    if (isD10ish) {
      // Kite faces run apex->equator. Their centroid sits near the wide
      // equator end, which parks three neighbouring digits in the same visual
      // band; push each one back up its long axis toward its own apex.
      m.x = 0; m.y = 0;
      return m;
    }
    const cap = m.r * 0.5;
    const off = Math.hypot(m.x, m.y);
    if (off > cap) { m.x *= cap / off; m.y *= cap / off; }
    return m;
  };

  if (spec.apexRead) {
    // Real d4 layout: every face carries the number of each corner it touches,
    // reading upright toward that corner — you read the apex that points up.
    apex = vertices.map((v, i) => ({ vertex: v.clone(), value: i + 1 }));
    const valueAt = (p) => (apex.find((a) => a.vertex.distanceTo(p) < 1e-4) || apex[0]).value;
    for (const f of faces) {
      const b0 = basisFor(f);
      const inr = metricsFor(f, b0.up, b0.right).r;
      const corners = [];
      for (const p of f.pts) if (!corners.some((c) => c.distanceTo(p) < 1e-4)) corners.push(p.clone());
      for (const c of corners) {
        const value = valueAt(c);
        const text = String(value);
        const { geo: ng, width, height } = numeralGeometry(text);
        const up = c.clone().sub(f.centroid).projectOnPlane(f.normal).normalize();
        const right = new THREE.Vector3().crossVectors(up, f.normal).normalize();
        let scale = (inr * spec.glyph) / height;
        if (width * scale > inr * 1.4) scale = (inr * 1.4) / width;
        const m = new THREE.Matrix4().makeBasis(right, up, f.normal);
        numerals.push({
          text, value, geo: ng, scale,
          quaternion: new THREE.Quaternion().setFromRotationMatrix(m),
          position: f.centroid.clone().lerp(c, 0.56).addScaledVector(f.normal, 0.0018),
          underline: false, ulOffset: 0,
        });
      }
    }
  } else faces.forEach((f, i) => {
    const raw = values[i];
    const shown = spec.tens ? (raw % 10) * 10 : raw;
    const text = spec.tens ? String(shown).padStart(2, '0') : String(raw);
    const { geo: ng, width, height } = numeralGeometry(text);

    const { up, right } = basisFor(f);
    const cc = metricsFor(f, up, right);
    const inr = cc.r;

    const hasUL = text === '6' || text === '9' || text === '60' || text === '90';
    const vExtent = hasUL ? height + 0.25 : height;
    const widthCap = isD10ish ? 1.2 : 1.7;
    let scale = (inr * spec.glyph) / vExtent;
    if (width * scale > inr * widthCap) scale = (inr * widthCap) / width;

    const m = new THREE.Matrix4().makeBasis(right, up, f.normal);
    const q = new THREE.Quaternion().setFromRotationMatrix(m);
    const posV = f.centroid.clone()
      .addScaledVector(right, cc.x)
      .addScaledVector(up, cc.y + (hasUL ? 0.125 * scale : 0))
      .addScaledVector(f.normal, 0.0018);
    numerals.push({ text, value: shown, geo: ng, scale, quaternion: q, position: posV, underline: hasUL, ulOffset: -0.7 * scale, up: up.clone() });
  });

  const faceTable = faces.map((f, i) => {
    const raw = values[i];
    const n = numerals.find((x) => !spec.apexRead && x.value === (spec.tens ? (raw % 10) * 10 : raw));
    return {
      normal: f.normal.clone(),
      value: spec.tens ? (raw % 10) * 10 : raw,
      distance: f.distance,
      up: n && n.up ? n.up.clone() : basisFor(f).up,
    };
  });
  const circumradius = Math.max(...vertices.map((v) => v.length()));
  const out = { geo, faces: faceTable, numerals, vertices, circumradius, apex, spec };
  shapeCache.set(type, out);
  return out;
}

/* ------------------------------------------------------------------ *
 * Materials
 * ------------------------------------------------------------------ */

export const MATERIALS = {
  steel: {
    label: 'Forged steel', swatch: '#9aa1aa', sound: 'metal',
    body: { color: 0x9aa1aa, metalness: 0.95, roughness: 0.26 },
    inlay: { color: 0x14161a, metalness: 0.6, roughness: 0.45 },
  },
  copper: {
    label: 'Aged copper', swatch: '#b3714a', sound: 'metal',
    body: { color: 0xb3714a, metalness: 0.92, roughness: 0.38 },
    inlay: { color: 0x22303a, metalness: 0.4, roughness: 0.5 },
  },
  obsidian: {
    label: 'Obsidian & gold', swatch: '#d6ab5e', sound: 'stone',
    body: { color: 0x15171c, metalness: 0.25, roughness: 0.14 },
    inlay: { color: 0xd6ab5e, metalness: 0.9, roughness: 0.24 },
  },
  bone: {
    label: 'Carved bone', swatch: '#ded3bb', sound: 'stone',
    body: { color: 0xded3bb, metalness: 0.0, roughness: 0.66, sheen: 0.6, sheenRoughness: 0.7, sheenColor: 0xfff3dc },
    inlay: { color: 0x39322a, metalness: 0.0, roughness: 0.6 },
  },
  ivory: {
    label: 'Ivory plastic', swatch: '#eee7d9', sound: 'plastic',
    body: { color: 0xeee7d9, metalness: 0.0, roughness: 0.4 },
    inlay: { color: 0x2b2c30, metalness: 0.0, roughness: 0.5 },
  },
  ruby: {
    label: 'Ruby glass', swatch: '#d21f45', sound: 'glass',
    body: { color: 0xd21f45, metalness: 0.0, roughness: 0.06, transmission: 0.92, thickness: 0.55, ior: 1.62, transparent: true },
    inlay: { color: 0xfff3f5, metalness: 0.1, roughness: 0.3 },
  },
  frost: {
    label: 'Frosted quartz', swatch: '#cfe2ec', sound: 'glass',
    body: { color: 0xdfeef5, metalness: 0.0, roughness: 0.34, transmission: 0.88, thickness: 0.7, ior: 1.48, transparent: true },
    inlay: { color: 0x1d3038, metalness: 0.2, roughness: 0.4 },
  },
  jade: {
    label: 'Jade', swatch: '#3f8f6f', sound: 'stone',
    body: { color: 0x2f7f60, metalness: 0.0, roughness: 0.24, transmission: 0.55, thickness: 0.8, ior: 1.55, clearcoat: 1, clearcoatRoughness: 0.2, transparent: true },
    inlay: { color: 0xf3ead2, metalness: 0.0, roughness: 0.45 },
  },
  amber: {
    label: 'Amber resin', swatch: '#c9761c', sound: 'resin',
    body: { color: 0xc9761c, metalness: 0.0, roughness: 0.22, transmission: 0.62, thickness: 0.4, ior: 1.5, clearcoat: 1, clearcoatRoughness: 0.12, transparent: true },
    inlay: { color: 0x2a1508, metalness: 0.0, roughness: 0.4 },
  },
  nebula: {
    label: 'Nebula', swatch: '#6d5ac6', sound: 'resin',
    body: { color: 0x3b2f6e, metalness: 0.3, roughness: 0.18, iridescence: 1, iridescenceIOR: 1.9, iridescenceThicknessRange: [180, 640], clearcoat: 1, clearcoatRoughness: 0.1 },
    inlay: { color: 0xe8e2ff, metalness: 0.2, roughness: 0.3 },
  },
  liquid: {
    label: 'Liquid core · aqua', swatch: '#3fd2c2', sound: 'glass',
    body: { color: 0xe4f7f5, metalness: 0.0, roughness: 0.05, transmission: 0.97, thickness: 0.18, ior: 1.5, transparent: true },
    inlay: { color: 0x0c2f33, metalness: 0.35, roughness: 0.28 },
    core: { liquid: 0x14b7a6, fleck: 0xffe0a3, emissive: 0x0a5f57 },
  },
  liquidRose: {
    label: 'Liquid core · rose', swatch: '#e0577f', sound: 'glass',
    body: { color: 0xfdeef2, metalness: 0.0, roughness: 0.05, transmission: 0.97, thickness: 0.18, ior: 1.5, transparent: true },
    inlay: { color: 0x3a0f1e, metalness: 0.35, roughness: 0.28 },
    core: { liquid: 0xd6386a, fleck: 0xffd7a0, emissive: 0x6b1230 },
  },
};

/* Liquid-core innards: a tinted blob with metallic flecks suspended in it.
   Deterministic layout so the cached geometry is stable across rebuilds. */
const lcg = (seed) => { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; };
let _blobGeo = null, _fleckGeo = null;

export function buildCore(cfg, inradius, env) {
  const R = inradius * 0.6;
  if (!_blobGeo) _blobGeo = new THREE.SphereGeometry(1, 28, 18);
  if (!_fleckGeo) _fleckGeo = new THREE.OctahedronGeometry(1, 0);
  const g = new THREE.Group();
  g.name = 'liquid_core';

  const liquidMat = new THREE.MeshPhysicalMaterial({
    color: cfg.liquid, roughness: 0.08, metalness: 0,
    transmission: 0.78, thickness: R * 1.6, ior: 1.34,
    emissive: cfg.emissive, emissiveIntensity: 0.35,
    transparent: true, envMapIntensity: 1.1,
  });
  liquidMat.name = 'liquid';
  if (env) liquidMat.envMap = env;
  const blob = new THREE.Mesh(_blobGeo, liquidMat);
  blob.name = 'liquid';
  blob.scale.set(R, R * 0.94, R);
  g.add(blob);

  const fleckMat = new THREE.MeshStandardMaterial({ color: cfg.fleck, metalness: 0.95, roughness: 0.16 });
  fleckMat.name = 'fleck';
  if (env) fleckMat.envMap = env;
  const rnd = lcg(0x5eed);
  const flecks = [];
  for (let i = 0; i < 22; i++) {
    const m = new THREE.Mesh(_fleckGeo, fleckMat);
    m.name = `fleck_${i}`;
    const u = rnd() * Math.PI * 2, v = Math.acos(2 * rnd() - 1), rad = R * 0.86 * Math.cbrt(rnd());
    m.position.set(rad * Math.sin(v) * Math.cos(u), rad * Math.cos(v) * 0.9, rad * Math.sin(v) * Math.sin(u));
    m.rotation.set(rnd() * 6.28, rnd() * 6.28, rnd() * 6.28);
    m.scale.setScalar(R * (0.055 + rnd() * 0.055));
    // per-fleck inertia so the suspension drifts apart instead of moving as one rigid clump
    m.userData.home = m.position.clone();
    m.userData.lag = 0.35 + rnd() * 0.75;
    m.userData.sink = 0.25 + rnd() * 0.6;
    m.userData.spin = new THREE.Vector3(rnd() - 0.5, rnd() - 0.5, rnd() - 0.5).multiplyScalar(3.4);
    m.userData.off = new THREE.Vector3();
    flecks.push(m);
    g.add(m);
  }
  return { group: g, travel: inradius * 0.2, blob, flecks, radius: R };
}

export function makeMaterials(key, env) {
  const preset = MATERIALS[key] || MATERIALS.steel;
  const body = new THREE.MeshPhysicalMaterial({ ...preset.body, envMapIntensity: 1.15 });
  body.name = `${key}_body`;
  const inlay = new THREE.MeshPhysicalMaterial({ ...preset.inlay, envMapIntensity: 1.0 });
  inlay.name = `${key}_inlay`;
  if (env) { body.envMap = env; inlay.envMap = env; }
  return { body, inlay, preset };
}

/**
 * A die as a THREE.Group of named meshes.
 * Returns { group, faces, vertices, circumradius, apex }.
 */
export function createDie(type, materialKey = 'steel', env = null) {
  const shape = dieShape(type);
  const { body, inlay, preset } = makeMaterials(materialKey, env);
  const group = new THREE.Group();
  group.name = `${type}_${materialKey}`;

  const bodyMesh = new THREE.Mesh(shape.geo, body);
  bodyMesh.name = `${type}_body`;
  bodyMesh.castShadow = true;
  group.add(bodyMesh);

  for (const n of shape.numerals) {
    const mesh = new THREE.Mesh(n.geo, inlay);
    mesh.name = `${type}_numeral_${n.text}`;
    mesh.position.copy(n.position);
    mesh.quaternion.copy(n.quaternion);
    mesh.scale.setScalar(n.scale);
    group.add(mesh);
    if (n.underline) {
      const ul = new THREE.Mesh(underlineGeo(), inlay);
      ul.name = `${type}_underline_${n.text}`;
      ul.position.copy(n.position);
      ul.quaternion.copy(n.quaternion);
      ul.scale.setScalar(n.scale);
      ul.translateY(n.ulOffset);
      group.add(ul);
    }
  }
  let core = null, coreTravel = 0, coreBlob = null, coreFlecks = null, coreR = 0;
  if (preset.core) {
    const c = buildCore(preset.core, shape.spec.inradius, env);
    core = c.group;
    coreTravel = c.travel;
    coreBlob = c.blob;
    coreFlecks = c.flecks;
    coreR = c.radius;
    group.add(core);
  }
  return {
    group, type,
    faces: shape.faces,
    vertices: shape.vertices,
    circumradius: shape.circumradius,
    apex: shape.apex,
    core, coreTravel, coreBlob, coreFlecks, coreR,
    sides: shape.spec.sides,
  };
}

let _ul = null;
function underlineGeo() {
  if (!_ul) {
    _ul = new THREE.BoxGeometry(0.62, 0.1, 0.14);
    _ul.translate(0, 0, -0.07);
  }
  return _ul;
}
