/**
 * dice-overlay.js — 3D dice overlay, engine only.
 *
 * Extracted from the "Dice Roller.html" design prototype: same renderer,
 * physics, materials and audio, with every reference to the prototype's
 * control panel replaced by a callback. It renders nothing but dice.
 *
 * The host app owns all UI. Wire it up through the hooks:
 *   onRollStart()          a throw began — lock your UI
 *   onResult(payload)      dice have settled — render the result
 *   onCrit({crit,fumble})  a natural 20 / natural 1 landed — fire your own flash
 *   onBusy(bool)           enable/disable your roll button
 *   onHover(index)         pointer is over dice[index], or -1
 *
 * Two ways to roll:
 *   overlay.roll('1d20+5')                    dice decide the number (client-authoritative)
 *   overlay.rollTo('1d20+5', { rolls: [17] }) server decides, dice land on it
 *
 * rollTo() is not a cheat overlay. It runs the simulation headless, sees what
 * the die was going to land on, then rotates the die in its own body space by
 * an exact rotational symmetry of the solid so the required face is the one
 * that comes up. Because the rotation is a symmetry, the collision shape is
 * unchanged and the replayed tumble is bit-identical to the one simulated —
 * the same bounces, the same contacts, just different numbers on the faces.
 * There is no frame in which the die is doing something it could not do.
 *
 * Requires an import map providing 'three' (see example.html).
 */
import * as THREE from 'three';
import { createDie, DIE_TYPES, ALL_TYPES, MATERIALS } from './dice-geometry.js';

export { DIE_TYPES, ALL_TYPES, MATERIALS };

export function createDiceOverlay(options = {}) {
  const opts = Object.assign({ container: document.body, material: 'obsidian', style: 'tumble' }, options);
  const hooks = {
    onResult: options.onResult || null,
    onRollStart: options.onRollStart || null,
    onCrit: options.onCrit || null,
    onBusy: options.onBusy || null,
    onHover: options.onHover || null,
  };
  let pendingWanted = null;
  let lastResult = null;
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------------- *
   * Renderer / scene — transparent, so it floats over the page
   * ---------------------------------------------------------------- */
  const host = opts.container;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearAlpha(0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  // Camera distance from the origin directly sets the dice's on-screen size
  // (fixed FOV below): doubling the distance roughly halves how large a die
  // reads, since the dice themselves sit near the origin. CAM_ZOOM is that
  // single lever - scale it to resize the dice without touching every mesh.
  const CAM_ZOOM = 1.5;
  const CAM_BASE = new THREE.Vector3(0, 9.2, 6.4).multiplyScalar(CAM_ZOOM);
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 200);
  camera.position.copy(CAM_BASE);
  camera.lookAt(0, 0, 0);

  function buildEnv() {
    const c = document.createElement('canvas');
    c.width = 512; c.height = 256;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0, 0, 0, 256);
    grad.addColorStop(0, '#f2f5f8'); grad.addColorStop(0.42, '#b8bec6');
    grad.addColorStop(0.52, '#6d7176'); grad.addColorStop(1, '#2e3033');
    g.fillStyle = grad; g.fillRect(0, 0, 512, 256);
    g.fillStyle = 'rgba(255,255,255,0.95)';
    g.beginPath(); g.ellipse(150, 52, 110, 40, 0, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgba(255,240,220,0.55)';
    g.beginPath(); g.ellipse(390, 78, 70, 26, 0, 0, Math.PI * 2); g.fill();
    const tex = new THREE.CanvasTexture(c);
    tex.mapping = THREE.EquirectangularReflectionMapping;
    tex.colorSpace = THREE.SRGBColorSpace;
    const pmrem = new THREE.PMREMGenerator(renderer);
    const env = pmrem.fromEquirectangular(tex).texture;
    pmrem.dispose(); tex.dispose();
    return env;
  }
  const envMap = buildEnv();
  scene.environment = envMap;

  scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x2a2620, 0.55));
  const key = new THREE.DirectionalLight(0xfff4e2, 2.4);
  key.position.set(-4.5, 9, 4.5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.bias = -0.0012;
  const sc = key.shadow.camera;
  sc.left = -9; sc.right = 9; sc.top = 7; sc.bottom = -7; sc.near = 1; sc.far = 26;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xa8c4ff, 0.7);
  rim.position.set(5, 4, -6);
  scene.add(rim);

  const shadowPlane = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({ opacity: 0.34 }));
  shadowPlane.rotation.x = -Math.PI / 2;
  shadowPlane.receiveShadow = true;
  scene.add(shadowPlane);

  function glowTexture(inner) {
    const c = document.createElement('canvas');
    c.width = c.height = 256;
    const g = c.getContext('2d');
    const rg = g.createRadialGradient(128, 128, 6, 128, 128, 128);
    rg.addColorStop(0, inner); rg.addColorStop(0.35, inner.replace('1)', '0.45)'));
    rg.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = rg; g.fillRect(0, 0, 256, 256);
    return new THREE.CanvasTexture(c);
  }
  const glowMats = {
    crit: new THREE.MeshBasicMaterial({ map: glowTexture('rgba(255,206,120,1)'), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }),
    fumble: new THREE.MeshBasicMaterial({ map: glowTexture('rgba(255,96,72,1)'), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }),
  };
  const glowGeo = new THREE.PlaneGeometry(2.6, 2.6);

  /* ---------------------------------------------------------------- *
   * Play area
   * ---------------------------------------------------------------- */
  const bounds = { x: 4.5, z: 2.0, cz: -0.4 };
  const raycaster = new THREE.Raycaster();
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);

  function resize() {
    const w = innerWidth, h = innerHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    const hit = new THREE.Vector3();
    const corner = (nx, ny) => {
      raycaster.setFromCamera(new THREE.Vector2(nx, ny), camera);
      return raycaster.ray.intersectPlane(groundPlane, hit) ? hit.clone() : new THREE.Vector3(nx * 5, 0, ny * -3);
    };
    const near = corner(0, -0.26), far = corner(0, 0.34);
    bounds.cz = (near.z + far.z) / 2;
    bounds.z = Math.max(0.9, (near.z - far.z) / 2 - 0.3);
    const lp = corner(-1, -0.26), rp = corner(1, -0.26);
    // Dice-polish (2026-07-28): this max is a SEPARATE lever from CAM_ZOOM —
    // it caps how far a settled die can bounce from center in world units, so
    // it controls the play area's on-screen reach independently of how big a
    // die reads. At a wide desktop viewport this clamp is what actually
    // decides the horizontal bounds (the raycasted screen-edge value is
    // usually larger than it), and since the play area is always centered on
    // the viewport's horizontal midpoint, a host page whose content sits
    // right-of-center (e.g. the player sheet's left character rail) needs
    // this kept tight enough that a die can never settle over it. 2.3 keeps
    // the worst-case settle comfortably clear of that rail at CAM_ZOOM 1.5
    // (measured against the die's actual rendered silhouette, not just its
    // center point — a die's vertices reach further sideways than a single
    // sampled surface point would suggest).
    bounds.x = Math.min(2.3, Math.max(2.2, Math.min(Math.abs(lp.x), Math.abs(rp.x)) - 0.5));
  }
  addEventListener('resize', resize);
  resize();

  // rAF pauses while the tab is hidden; restart the clocks so the force-settle
  // timeout measures simulated time rather than wall time
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    last = performance.now();
    acc = 0;
    if (rolling) rollStart = performance.now();
  });

  /* ---------------------------------------------------------------- *
   * Sound — synthesised per material, no assets
   * ---------------------------------------------------------------- */
  const Sound = (() => {
    let ctx = null, master = null, noiseBuf = null, rumble = null, rumbleGain = null;
    let enabled = true;
    const init = () => {
      if (ctx) return;
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain(); master.gain.value = 0.85; master.connect(ctx.destination);
      const len = ctx.sampleRate * 2;
      noiseBuf = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = noiseBuf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    };
    const env = (node, peak, attack, decay, t0) => {
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(Math.max(peak, 0.0002), t0 + attack);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + decay);
      node.connect(g); g.connect(master);
      return g;
    };
    const noise = (t0, dur) => {
      const s = ctx.createBufferSource();
      s.buffer = noiseBuf; s.loop = true;
      s.start(t0); s.stop(t0 + dur);
      return s;
    };
    const tone = (type, f, t0, dur, bend) => {
      const o = ctx.createOscillator();
      o.type = type; o.frequency.setValueAtTime(f, t0);
      if (bend) o.frequency.exponentialRampToValueAtTime(f * bend, t0 + dur);
      o.start(t0); o.stop(t0 + dur + 0.05);
      return o;
    };
    const band = (src, f, q) => {
      const b = ctx.createBiquadFilter();
      b.type = 'bandpass'; b.frequency.value = f; b.Q.value = q;
      src.connect(b); return b;
    };

    const profiles = {
      metal(v, t0) {
        const dur = 0.55 * (0.5 + v * 0.5);
        [1580, 2370, 3520].forEach((f, i) => env(tone('triangle', f * (1 + (Math.random() - 0.5) * 0.03), t0, dur), v * (0.2 / (i + 1)), 0.002, dur, t0));
        env(band(noise(t0, 0.08), 4200, 1.6), v * 0.16, 0.001, 0.07, t0);
        env(tone('sine', 220, t0, 0.1, 0.6), v * 0.18, 0.002, 0.09, t0);
      },
      plastic(v, t0) {
        env(band(noise(t0, 0.07), 2400, 2.4), v * 0.4, 0.001, 0.05, t0);
        env(tone('sine', 320, t0, 0.07, 0.55), v * 0.34, 0.001, 0.055, t0);
      },
      glass(v, t0) {
        const dur = 0.38 * (0.5 + v * 0.5);
        [2680, 4020, 5400].forEach((f, i) => env(tone('sine', f * (1 + (Math.random() - 0.5) * 0.02), t0, dur), v * (0.17 / (i + 1)), 0.001, dur, t0));
        env(band(noise(t0, 0.05), 6800, 2), v * 0.1, 0.001, 0.04, t0);
      },
      resin(v, t0) {
        env(band(noise(t0, 0.12), 950, 5), v * 0.34, 0.001, 0.1, t0);
        env(tone('sine', 205, t0, 0.13, 0.5), v * 0.32, 0.002, 0.11, t0);
      },
      stone(v, t0) {
        env(band(noise(t0, 0.1), 520, 3.2), v * 0.36, 0.001, 0.08, t0);
        env(tone('sine', 148, t0, 0.16, 0.45), v * 0.4, 0.003, 0.14, t0);
      },
    };

    return {
      get enabled() { return enabled; },
      set enabled(v) { enabled = v; if (!v) this.stopRumble(); },
      unlock() { init(); if (ctx.state === 'suspended') ctx.resume(); },
      impact(profile, strength) {
        if (!enabled || !ctx || !isFinite(strength)) return;
        const v = Math.min(1, Math.max(0.06, strength));
        try { (profiles[profile] || profiles.plastic)(v, ctx.currentTime + 0.001); } catch (e) { /* never break the frame */ }
      },
      throwWhoosh() {
        if (!enabled || !ctx) return;
        const t0 = ctx.currentTime;
        const n = noise(t0, 0.34);
        const f = ctx.createBiquadFilter();
        f.type = 'bandpass'; f.Q.value = 0.8;
        f.frequency.setValueAtTime(300, t0);
        f.frequency.exponentialRampToValueAtTime(1700, t0 + 0.22);
        n.connect(f);
        const g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.18, t0 + 0.09);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.33);
        f.connect(g); g.connect(master);
      },
      startRumble(profile) {
        if (!enabled || !ctx) return;
        this.stopRumble();
        rumble = noise(ctx.currentTime, 14);
        const f = ctx.createBiquadFilter();
        f.type = 'bandpass';
        f.frequency.value = profile === 'metal' ? 1500 : profile === 'glass' ? 2100 : 700;
        f.Q.value = 1.1;
        rumbleGain = ctx.createGain();
        rumbleGain.gain.value = 0;
        rumble.connect(f); f.connect(rumbleGain); rumbleGain.connect(master);
      },
      setRumble(level) {
        if (!rumbleGain || !ctx || !isFinite(level)) return;
        // hard gate: below the floor the bed goes fully silent, no residual hum
        const v = level < 0.1 ? 0 : Math.min(0.1, level * 0.1);
        rumbleGain.gain.setTargetAtTime(v, ctx.currentTime, 0.04);
      },
      stopRumble() {
        if (rumbleGain && ctx) { try { rumbleGain.gain.cancelScheduledValues(ctx.currentTime); rumbleGain.gain.setValueAtTime(0, ctx.currentTime); } catch (e) { /* noop */ } }
        if (rumble) { try { rumble.stop(); } catch (e) { /* already stopped */ } }
        rumble = null; rumbleGain = null;
      },
    };
  })();

  /* ---------------------------------------------------------------- *
   * Physics — convex support-point contacts against the table plane
   * ---------------------------------------------------------------- */
  const STYLES = {
    tumble:    { label: 'Tumble',    g: 26, e: 0.40, mu: 0.44, speed: 5.2, spin: 18, rate: 1.0,  lift: 0.55 },
    snap:      { label: 'Snap',      g: 44, e: 0.18, mu: 0.64, speed: 7.6, spin: 26, rate: 1.15, lift: 0.2 },
    cinematic: { label: 'Cinematic', g: 17, e: 0.50, mu: 0.34, speed: 4.3, spin: 11, rate: 0.6,  lift: 1.0 },
  };
  const PRESENT_TILT = 0.19; // lean the settled die toward the camera so the top face reads

  const _v = new THREE.Vector3(), _v2 = new THREE.Vector3(), _v3 = new THREE.Vector3(), _v4 = new THREE.Vector3();
  const _q = new THREE.Quaternion(), _q2 = new THREE.Quaternion();
  const N_UP = new THREE.Vector3(0, 1, 0);
  const AXIS_Y = new THREE.Vector3(0, 1, 0);
  const AXIS_X = new THREE.Vector3(1, 0, 0);

  class Body {
    constructor(die) {
      Object.assign(this, die);
      this.pos = new THREE.Vector3();
      this.vel = new THREE.Vector3();
      this.omega = new THREE.Vector3();
      this.quat = new THREE.Quaternion();
      this.inertia = 0.4 * this.circumradius * this.circumradius;
      this.restFrames = 0;
      this.asleep = false;
      this.settling = null;
      this.result = null;
      this.dropped = false;
      this.hl = 0;
      this.coreVel = new THREE.Vector3();
      this.prevVel = new THREE.Vector3();
      this.bodyMat = this.group.children[0].material;
      this.inlayMat = (this.group.children[1] || this.group.children[0]).material;
      this.baseOpacity = { body: this.bodyMat.opacity, inlay: this.inlayMat.opacity };
    }
    lowest() {
      let minY = Infinity, best = null;
      for (const v of this.vertices) {
        _v.copy(v).applyQuaternion(this.quat);
        if (_v.y < minY) { minY = _v.y; best = _v.clone(); }
      }
      return { y: this.pos.y + minY, local: best };
    }
    applyImpulse(r, imp) {
      this.vel.add(imp);
      _v2.copy(r).cross(imp).multiplyScalar(1 / this.inertia);
      this.omega.add(_v2);
    }
    sync() {
      this.group.position.copy(this.pos);
      this.group.quaternion.copy(this.quat);
    }
  }

  const bodies = [];
  let style = STYLES[opts.style] || STYLES.tumble;
  let rolling = false;
  let rollStart = 0;
  let impactBudget = 0;
  let shake = 0;

  function stepBody(b, dt) {
    if (b.asleep || b.settling) return 0;
    b.vel.y -= style.g * dt;
    b.pos.addScaledVector(b.vel, dt);

    const ang = b.omega.length() * dt;
    if (ang > 1e-6) {
      _q.setFromAxisAngle(_v.copy(b.omega).normalize(), ang);
      b.quat.premultiply(_q).normalize();
    }

    let energy = 0;
    const low = b.lowest();
    if (low.y < 0) {
      b.pos.y -= low.y;
      const r = low.local;
      _v.copy(b.omega).cross(r).add(b.vel);
      const vn = _v.y;
      if (vn < 0) {
        const rn = _v2.copy(r).cross(N_UP);
        const denom = 1 + rn.lengthSq() / b.inertia;
        const speed = -vn;
        const e = speed < 0.9 ? style.e * 0.3 : style.e;
        const j = ((1 + e) * speed) / denom;
        b.applyImpulse(r, _v3.copy(N_UP).multiplyScalar(j));
        _v.y = 0;
        const vt = _v.length();
        if (vt > 1e-4) {
          const t = _v.multiplyScalar(-1 / vt);
          const rt = _v2.copy(r).cross(t);
          const jt = Math.min((vt / (1 + rt.lengthSq() / b.inertia)) * 0.9, style.mu * j);
          b.applyImpulse(r, t.multiplyScalar(jt));
        }
        energy = speed;
      }
      // frame-rate-independent contact damping; a die that has stopped travelling
      // must also stop spinning — that was the "rolling in place" artefact
      b.vel.multiplyScalar(Math.exp(-5.5 * dt));
      b.omega.multiplyScalar(Math.exp(-8 * dt));
      if (b.vel.lengthSq() < 0.6) b.omega.multiplyScalar(Math.exp(-18 * dt));
    }

    for (const [axis, mid, lim] of [['x', 0, bounds.x], ['z', bounds.cz, bounds.z]]) {
      const R = b.circumradius;
      const hi = mid + lim - R, lo = mid - lim + R;
      if (b.pos[axis] > hi) { b.pos[axis] = hi; if (b.vel[axis] > 0) { b.vel[axis] *= -0.45; energy = Math.max(energy, Math.abs(b.vel[axis])); } }
      if (b.pos[axis] < lo) { b.pos[axis] = lo; if (b.vel[axis] < 0) { b.vel[axis] *= -0.45; energy = Math.max(energy, Math.abs(b.vel[axis])); } }
    }

    b.vel.x *= 0.9975; b.vel.z *= 0.9975;
    b.omega.multiplyScalar(0.998);
    if (b.omega.lengthSq() > 2500) b.omega.setLength(50);

    if (low.y < 0.02 && b.vel.lengthSq() < 0.05 && b.omega.lengthSq() < 1.1) {
      if (++b.restFrames > 12) beginSettle(b);
    } else b.restFrames = 0;

    return energy;
  }

  function separate() {
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        const a = bodies[i], b = bodies[j];
        const rr = (a.circumradius + b.circumradius) * 0.88;
        _v.subVectors(b.pos, a.pos);
        const d = _v.length();
        if (d > rr || d < 1e-5) continue;
        _v.multiplyScalar(1 / d);
        const push = (rr - d) * 0.5;
        const aFree = !a.asleep && !a.settling, bFree = !b.asleep && !b.settling;
        if (aFree) a.pos.addScaledVector(_v, -push);
        if (bFree) b.pos.addScaledVector(_v, push);
        const rel = _v2.subVectors(b.vel, a.vel).dot(_v);
        if (rel < 0) {
          const j2 = -rel * 0.6;
          if (aFree) { a.vel.addScaledVector(_v, -j2); a.omega.addScaledVector(_v3.set(_v.z, 0.4, -_v.x), -j2 * 1.8); a.restFrames = 0; }
          if (bFree) { b.vel.addScaledVector(_v, j2); b.omega.addScaledVector(_v3.set(-_v.z, 0.4, _v.x), j2 * 1.8); b.restFrames = 0; }
          impactBudget = Math.max(impactBudget, Math.min(1, -rel * 0.16));
        }
      }
    }
  }

  /** Snap the die flat onto its resting face, read the result, then pose it:
   *  yaw so the winning numeral reads upright, tilt it toward the camera. */
  function beginSettle(b) {
    if (b.settling || b.asleep) return;
    // A roll started just before the tab is backgrounded accumulates no physics,
    // so the wall-clock force-settle can fire while the die is still at its
    // spawn point off-screen. Never let one come to rest outside the tray.
    const R = b.circumradius;
    b.pos.x = THREE.MathUtils.clamp(b.pos.x, -bounds.x + R, bounds.x - R);
    b.pos.z = THREE.MathUtils.clamp(b.pos.z, bounds.cz - bounds.z + R, bounds.cz + bounds.z - R);
    let bottom = null, bd = 2;
    for (const f of b.faces) {
      const d = _v.copy(f.normal).applyQuaternion(b.quat).y;
      if (d < bd) { bd = d; bottom = f; }
    }
    const worldN = _v.copy(bottom.normal).applyQuaternion(b.quat).normalize();
    const flat = new THREE.Quaternion().setFromUnitVectors(worldN, _v2.set(0, -1, 0)).multiply(b.quat).normalize();

    let top = b.faces[0], td = -2;
    for (const f of b.faces) {
      const d = _v.copy(f.normal).applyQuaternion(flat).y;
      if (d > td) { td = d; top = f; }
    }

    if (b.apex) {
      let best = b.apex[0], by = -Infinity;
      for (const a of b.apex) {
        const y = _v.copy(a.vertex).applyQuaternion(flat).y;
        if (y > by) { by = y; best = a; }
      }
      b.result = best.value;
    } else {
      b.result = top.value;
    }

    // yaw: bring the reading direction of the up-face to screen-up
    const target = flat.clone();
    const ref = b.apex ? _v.copy(top.normal).applyQuaternion(flat) : _v.copy(top.up).applyQuaternion(flat);
    ref.y = 0;
    if (ref.lengthSq() > 1e-5) {
      ref.normalize();
      const theta = Math.atan2(ref.x, -ref.z) + (b.apex ? Math.PI : 0);
      target.premultiply(_q.setFromAxisAngle(AXIS_Y, theta));
    }
    target.premultiply(_q.setFromAxisAngle(AXIS_X, PRESENT_TILT)).normalize();

    let minY = Infinity;
    for (const v of b.vertices) {
      const y = _v.copy(v).applyQuaternion(target).y;
      if (y < minY) minY = y;
    }

    b.settling = { from: b.quat.clone(), to: target, y0: b.pos.y, y1: -minY, t: 0 };
    b.vel.set(0, 0, 0);
    b.omega.set(0, 0, 0);
  }

  /* ---------------------------------------------------------------- *
   * Pool, modes, materials
   * ---------------------------------------------------------------- */
  const pool = Object.fromEntries(ALL_TYPES.map((t) => [t, 0]));
  let modifier = 0;
  let mode = 'normal';
  let keepMode = 'all';
  let keepN = 3;
  let materialKey = MATERIALS[opts.material] ? opts.material : 'obsidian';
  let mixed = false;
  let glowMesh = null;
  const MIXED_SET = { d4: 'jade', d6: 'ivory', d8: 'copper', d10: 'ruby', d12: 'amber', d20: 'liquid', d100: 'frost' };
  const materialFor = (type) => (mixed ? MIXED_SET[type] || materialKey : materialKey);
  const soundKey = () => MATERIALS[mixed ? 'obsidian' : materialKey].sound;

  function clearDice() {
    for (const b of bodies) scene.remove(b.group);
    bodies.length = 0;
    hoverIdx = -1;
    if (glowMesh) { scene.remove(glowMesh); glowMesh = null; }
  }

  function throwList() {
    return throwListFor(currentSpec());
  }

  function currentSpec(label) {
    return { label: label || '', pool: { ...pool }, modifier, mode, keepMode, keepN };
  }

  /** Parse a character-sheet style expression: '1d20+5', '2d6+3', '4d6kh3', '1d20+2 adv'. */
  function parseSpec(str, label) {
    const p = Object.fromEntries(ALL_TYPES.map((t) => [t, 0]));
    const s = String(str);
    for (const m of s.matchAll(/(\d*)\s*d\s*(%|\d+)/gi)) {
      const n = Math.max(1, parseInt(m[1] || '1', 10));
      if (m[2] === '%') { p.d100 += n; p.d10 += n; }
      else if (p[`d${m[2]}`] !== undefined) p[`d${m[2]}`] += n;
    }
    const mm = s.replace(/\d*d\s*(%|\d+)/gi, '').replace(/k[hl]\s*\d+/gi, '').match(/[+-]\s*\d+/g);
    const k = s.match(/k([hl])\s*(\d+)/i);
    return {
      label: label || '',
      pool: p,
      modifier: mm ? mm.reduce((a, x) => a + parseInt(x.replace(/\s/g, ''), 10), 0) : 0,
      mode: /\badv/i.test(s) ? 'adv' : /\bdis/i.test(s) ? 'dis' : 'normal',
      keepMode: k ? (k[1].toLowerCase() === 'h' ? 'high' : 'low') : 'all',
      keepN: k ? parseInt(k[2], 10) || 1 : 3,
    };
  }

  function specFormula(s) {
    const pairs = Math.min(s.pool.d100, s.pool.d10);
    const parts = [];
    if (pairs) parts.push(`${pairs}d%`);
    DIE_TYPES.forEach((t) => {
      const n = t === 'd10' ? s.pool.d10 - pairs : s.pool[t];
      if (n > 0) parts.push(`${n}${t}`);
    });
    if (!parts.length) return '';
    let f = parts.join(' + ');
    if (s.modifier) f += ` ${s.modifier > 0 ? '+' : '−'} ${Math.abs(s.modifier)}`;
    if (s.keepMode !== 'all') f += ` ${s.keepMode === 'high' ? 'kh' : 'kl'}${s.keepN}`;
    if (s.mode !== 'normal' && s.pool.d20 === 1 && s.keepMode === 'all') f += s.mode === 'adv' ? ' · adv' : ' · dis';
    return f;
  }

  function throwListFor(s) {
    const list = [];
    const pairs = Math.min(s.pool.d100, s.pool.d10);
    for (let i = 0; i < pairs; i++) { list.push('d100'); list.push('d10'); }
    DIE_TYPES.forEach((t) => {
      const n = t === 'd10' ? s.pool.d10 - pairs : s.pool[t];
      for (let i = 0; i < n; i++) list.push(t);
    });
    if (s.mode !== 'normal' && s.pool.d20 === 1 && s.keepMode === 'all') list.push('d20');
    return list;
  }

  function launch(b, i, count) {
    const spread = Math.min(bounds.z * 1.4, Math.max(0.5, count * 0.5));
    const lane = count === 1 ? 0 : (i / (count - 1) - 0.5) * spread;
    const speed = style.speed * THREE.MathUtils.clamp(bounds.x / 3.6, 0.8, 1.35) * (0.86 + Math.random() * 0.3);
    // vary where each die enters, how steeply, and how high, so repeat rolls
    // never trace the same arc twice
    const entryZ = bounds.cz + lane + (Math.random() - 0.5) * bounds.z * 0.7;
    const backset = 1.0 + Math.random() * 1.6 + Math.floor(i / 3) * 0.7;
    const height = 0.7 + style.lift * (0.6 + Math.random() * 1.1) + Math.random() * 0.9;
    const aim = (bounds.cz - entryZ) * (0.25 + Math.random() * 0.45);
    b.pos.set(-bounds.x - backset, height, entryZ);
    b.vel.set(speed, 0.3 + Math.random() * 1.0, aim + (Math.random() - 0.5) * 1.4);
    b.omega.set(
      (Math.random() - 0.5) * style.spin * 1.2,
      (Math.random() - 0.5) * style.spin * 0.8,
      -style.spin * (0.4 + Math.random() * 1.0),
    );
    b.quat.setFromEuler(new THREE.Euler(Math.random() * 6.28, Math.random() * 6.28, Math.random() * 6.28));
    b.asleep = false; b.settling = null; b.result = null; b.restFrames = 0; b.dropped = false;
    setDropped(b, false);
    b.sync();
  }

  let rollSpecs = [];

  function roll(specs) {
    rollSpecs = (specs && specs.length) ? specs : [currentSpec()];
    const plan = [];
    rollSpecs.forEach((s, gi) => throwListFor(s).forEach((t) => plan.push({ type: t, gi })));
    if (!plan.length) return;
    Sound.unlock();
    clearDice();
    clearGlow();
    if (hooks.onRollStart) hooks.onRollStart();
    rolling = true;
    rollStart = performance.now();
    setBusy(true);

    plan.forEach((item, i) => {
      const b = new Body(createDie(item.type, materialFor(item.type), envMap));
      b.rollGroup = item.gi;
      launch(b, i, plan.length);
      scene.add(b.group);
      bodies.push(b);
    });

    if (pendingWanted) { applyDirected(pendingWanted); pendingWanted = null; }
    if (REDUCED) { settleInstantly(); return; }
    Sound.throwWhoosh();
    Sound.startRumble(soundKey());
  }

  function rerollOne(i) {
    const b = bodies[i];
    if (!b) return;
    Sound.unlock();
    clearGlow();
    if (hooks.onRollStart) hooks.onRollStart();
    launch(b, 0, 1);
    rolling = true;
    rollStart = performance.now();
    setBusy(true);
    if (REDUCED) { settleInstantly(); return; }
    Sound.throwWhoosh();
    Sound.startRumble(soundKey());
  }

  /** prefers-reduced-motion: run the simulation headless, show the outcome. */
  function settleInstantly() {
    for (let n = 0; n < 1400; n++) {
      let live = false;
      for (const b of bodies) { if (!b.asleep && !b.settling) { stepBody(b, 1 / 120); live = true; } }
      separate();
      if (!live) break;
    }
    for (const b of bodies) {
      if (!b.settling && !b.asleep) beginSettle(b);
      if (b.settling) { b.quat.copy(b.settling.to); b.pos.y = b.settling.y1; b.settling = null; }
      b.asleep = true;
      b.sync();
    }
    finishRoll();
  }

  function finishRoll() {
    rolling = false;
    Sound.stopRumble();
    setBusy(false);
    emitResult();
  }

  /* ---------------------------------------------------------------- *
   * Loop
   * ---------------------------------------------------------------- */
  const FIXED = 1 / 120;
  let acc = 0, last = performance.now();
  let hoverIdx = -1;
  const pointerNDC = new THREE.Vector2(-2, -2);
  let pointerInside = false;

  function frame(now) {
    requestAnimationFrame(frame);
    const raw = Math.min((now - last) / 1000, 0.05);
    last = now;
    acc += raw * style.rate;

    let steps = 0;
    while (acc >= FIXED && steps < 8) {
      acc -= FIXED; steps++;
      let peak = impactBudget;
      impactBudget = 0;
      for (const b of bodies) peak = Math.max(peak, stepBody(b, FIXED));
      separate();
      if (peak > 0.35) Sound.impact(soundKey(), Math.min(1, peak / 7));
    }

    let motion = 0, awake = 0, allDone = bodies.length > 0;
    for (const b of bodies) {
      if (b.settling) {
        const s = b.settling;
        s.t = Math.min(1, s.t + raw / 0.18);
        const k = s.t * s.t * (3 - 2 * s.t);
        b.quat.copy(s.from).slerp(s.to, k);
        b.pos.y = s.y0 + (s.y1 - s.y0) * k;
        if (s.t >= 1) { b.settling = null; b.asleep = true; Sound.impact(soundKey(), 0.12); }
      }
      if (!b.asleep) { allDone = false; if (!b.settling) { motion += b.vel.length() + b.omega.length() * 0.08; awake++; } }
      if (b.core) {
        // Pseudo-gravity inside the shell: real gravity minus the die's own
        // acceleration, so a hard throw or a bounce slings the liquid the other way.
        const inv = _q.copy(b.quat).invert();
        _v3.subVectors(b.vel, b.prevVel).multiplyScalar(1 / Math.max(raw, 1e-3));
        _v4.set(0, -22, 0).addScaledVector(_v3, -0.5).normalize().applyQuaternion(inv);
        _v.copy(_v4).multiplyScalar(b.coreTravel);

        // underdamped spring: the mass overshoots and wobbles instead of gliding home
        _v2.subVectors(_v, b.core.position).multiplyScalar(150 * raw);
        b.coreVel.add(_v2).multiplyScalar(Math.exp(-3.4 * raw));
        b.core.position.addScaledVector(b.coreVel, raw);

        // pool: flatten along the pull axis and spread across it, more when sloshing
        const k = 0.06 + Math.min(0.26, b.coreVel.length() * 1.6);
        _q2.setFromUnitVectors(N_UP, _v4);
        b.coreBlob.quaternion.copy(_q2);
        b.coreBlob.scale.set(b.coreR * (1 + k * 0.55), b.coreR * (0.94 - k), b.coreR * (1 + k * 0.55));

        // flecks lag the liquid by different amounts and sink through it at rest
        const stir = Math.min(1.6, b.coreVel.length() * 6 + b.omega.length() * 0.1);
        for (const f of b.coreFlecks) {
          const u = f.userData;
          u.off.addScaledVector(_v2, raw * u.lag * 0.5).multiplyScalar(Math.exp(-4.2 * raw));
          if (u.off.lengthSq() > b.coreR * b.coreR * 0.05) u.off.setLength(b.coreR * 0.22);
          f.position.copy(u.home).addScaledVector(_v4, u.sink * b.coreTravel * 1.3).add(u.off);
          f.rotation.x += u.spin.x * raw * (0.25 + stir);
          f.rotation.y += u.spin.y * raw * (0.25 + stir);
          f.rotation.z += u.spin.z * raw * (0.25 + stir);
        }
      }
      b.prevVel.copy(b.vel);
      b.sync();
    }

    if (rolling) {
      Sound.setRumble(awake ? Math.min(1, motion / (awake * 6)) : 0);
      if (allDone || now - rollStart > 4200 / style.rate) {
        bodies.forEach((b) => { if (!b.asleep && !b.settling) beginSettle(b); });
        if (allDone) finishRoll();
      }
    }

    // hover highlight + cursor
    if (!rolling && bodies.length && pointerInside) {
      raycaster.setFromCamera(pointerNDC, camera);
      const hit = raycaster.intersectObjects(bodies.map((b) => b.group), true)[0];
      hoverIdx = hit ? bodies.findIndex((b) => hit.object.parent === b.group) : -1;
    } else if (rolling) hoverIdx = -1;
    renderer.domElement.style.cursor = hoverIdx >= 0 ? 'pointer' : '';

    for (let i = 0; i < bodies.length; i++) {
      const b = bodies[i];
      const t = i === hoverIdx ? 1 : 0;
      b.hl += (t - b.hl) * Math.min(1, raw * 14);
      b.group.scale.setScalar(1 + 0.07 * b.hl);
      b.bodyMat.emissive.setHex(0xffe4b0);
      b.bodyMat.emissiveIntensity = 0.22 * b.hl;
    }
    if (hooks.onHover) hooks.onHover(hoverIdx);

    if (shake > 0.0005) {
      shake *= Math.pow(0.0025, raw);
      camera.position.set(
        CAM_BASE.x + (Math.random() - 0.5) * shake,
        CAM_BASE.y + (Math.random() - 0.5) * shake,
        CAM_BASE.z + (Math.random() - 0.5) * shake,
      );
      camera.lookAt(0, 0, 0);
    } else if (shake) {
      shake = 0; camera.position.copy(CAM_BASE); camera.lookAt(0, 0, 0);
    }
    if (glowMesh) glowMesh.material.opacity = 0.55 + Math.sin(now / 320) * 0.18;

    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);

  /* ---------------------------------------------------------------- *
   * Scoring
   * ---------------------------------------------------------------- */
  function setDropped(b, on) {
    b.dropped = on;
    [b.bodyMat, b.inlayMat].forEach((m, k) => {
      m.transparent = on ? true : m.transmission > 0;
      m.opacity = on ? 0.28 : (k === 0 ? b.baseOpacity.body : b.baseOpacity.inlay);
    });
    b.group.scale.setScalar(on ? 0.86 : 1);
  }

  function scoreGroup(idxs, spec) {
    const entries = [];
    const used = new Set();
    const tens = [], ones = [];
    idxs.forEach((i) => { const b = bodies[i]; if (b.type === 'd100') tens.push(i); else if (b.type === 'd10') ones.push(i); });
    for (let k = 0; k < Math.min(tens.length, ones.length); k++) {
      const ti = tens[k], oi = ones[k];
      used.add(ti); used.add(oi);
      let v = (bodies[ti].result || 0) + ((bodies[oi].result || 0) % 10);
      if (v === 0) v = 100;
      entries.push({ label: 'd%', value: v, idx: [ti, oi] });
    }
    idxs.forEach((i) => { if (!used.has(i)) entries.push({ label: bodies[i].type, value: bodies[i].result == null ? 0 : bodies[i].result, idx: [i] }); });

    if (spec.keepMode !== 'all' && entries.length > 1) {
      const n = Math.min(spec.keepN, entries.length);
      const order = [...entries].sort((a, b) => (spec.keepMode === 'high' ? b.value - a.value : a.value - b.value));
      order.slice(n).forEach((e) => { e.dropped = true; e.idx.forEach((i) => setDropped(bodies[i], true)); });
    }
    if (spec.mode !== 'normal' && spec.keepMode === 'all') {
      const d20s = entries.filter((e) => e.label === 'd20');
      if (d20s.length > 1) {
        const keep = d20s.reduce((a, e) => (spec.mode === 'adv' ? (e.value > a.value ? e : a) : (e.value < a.value ? e : a)));
        d20s.forEach((e) => { if (e !== keep) { e.dropped = true; e.idx.forEach((i) => setDropped(bodies[i], true)); } });
      }
    }
    const total = entries.reduce((a, e) => a + (e.dropped ? 0 : e.value), 0) + spec.modifier;
    const live = entries.filter((e) => !e.dropped && e.label === 'd20');
    return {
      label: spec.label, formula: specFormula(spec), modifier: spec.modifier, entries, total,
      crit: live.some((e) => e.value === 20),
      fumble: live.length === 1 && live[0].value === 1,
    };
  }

  function computeResult() {
    bodies.forEach((b) => setDropped(b, false));
    return rollSpecs.map((s, gi) => scoreGroup(
      bodies.map((b, i) => (b.rollGroup === gi ? i : -1)).filter((i) => i >= 0), s,
    ));
  }
  /* ---------------------------------------------------------------- *
   * Host hooks — what the prototype's control panel used to do
   * ---------------------------------------------------------------- */
  function setBusy(v) { if (hooks.onBusy) hooks.onBusy(v); }
  function clearGlow() { if (glowMesh) { scene.remove(glowMesh); glowMesh = null; } }

  function emitResult() {
    const groups = computeResult();
    let hero = null, heroCrit = false;
    for (const g of groups) {
      if (hero || !(g.crit || g.fumble)) continue;
      const e = g.entries.find((x) => !x.dropped && x.label === 'd20' && x.value === (g.crit ? 20 : 1));
      if (e) { hero = e; heroCrit = g.crit; }
    }

    clearGlow();
    if (hero) {
      const b = bodies[hero.idx[0]];
      glowMesh = new THREE.Mesh(glowGeo, (heroCrit ? glowMats.crit : glowMats.fumble).clone());
      glowMesh.material.transparent = true;
      glowMesh.rotation.x = -Math.PI / 2;
      glowMesh.position.set(b.pos.x, 0.012, b.pos.z);
      scene.add(glowMesh);
      if (!REDUCED) shake = heroCrit ? 0.42 : 0.26;
      if (hooks.onCrit) hooks.onCrit({ crit: heroCrit, fumble: !heroCrit });
    }

    const grand = groups.reduce((a, g) => a + g.total, 0);
    lastResult = {
      total: groups.length === 1 ? groups[0].total : grand,
      crit: groups.some((g) => g.crit),
      fumble: groups.some((g) => g.fumble),
      groups: groups.map((g) => ({
        label: g.label, formula: g.formula, total: g.total, modifier: g.modifier,
        crit: g.crit, fumble: g.fumble,
        rolls: g.entries.map((e) => ({ type: e.label, value: e.value, dropped: !!e.dropped })),
      })),
    };
    if (hooks.onResult) hooks.onResult(lastResult);
  }

  /* ---------------------------------------------------------------- *
   * Directed results — land on a number the server already decided
   * ---------------------------------------------------------------- */

  /** Which value is face-up for this orientation, without touching the body. */
  function restingValue(b, quat) {
    const t = new THREE.Vector3(), t2 = new THREE.Vector3();
    let bottom = b.faces[0], bd = 2;
    for (const f of b.faces) {
      const d = t.copy(f.normal).applyQuaternion(quat).y;
      if (d < bd) { bd = d; bottom = f; }
    }
    const worldN = t.copy(bottom.normal).applyQuaternion(quat).normalize();
    const flat = new THREE.Quaternion().setFromUnitVectors(worldN, t2.set(0, -1, 0)).multiply(quat).normalize();
    if (b.apex) {
      let best = b.apex[0], by = -Infinity;
      for (const a of b.apex) {
        const y = t.copy(a.vertex).applyQuaternion(flat).y;
        if (y > by) { by = y; best = a; }
      }
      return { value: best.value, dir: best.vertex };
    }
    let top = b.faces[0], td = -2;
    for (const f of b.faces) {
      const d = t.copy(f.normal).applyQuaternion(flat).y;
      if (d > td) { td = d; top = f; }
    }
    return { value: top.value, dir: top.normal };
  }

  const symCache = new Map();

  function frameQuat(n, u) {
    const w = new THREE.Vector3().crossVectors(n, u).normalize();
    return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(u, w, n));
  }

  /** Every rotation that maps the solid onto itself. Finite; computed once per die type. */
  function symmetriesFor(b) {
    if (symCache.has(b.type)) return symCache.get(b.type);
    const V = b.vertices.map((v) => v.clone());
    const onSolid = (p) => V.some((v) => v.distanceToSquared(p) < 1e-6);

    const n0 = b.faces[0].normal.clone().normalize();
    let u0 = null, best = 0;
    for (const v of V) {
      const p = v.clone().projectOnPlane(n0);
      if (p.lengthSq() > best) { best = p.lengthSq(); u0 = p; }
    }
    u0.normalize();
    const inv0 = frameQuat(n0, u0).invert();

    const out = [];
    for (const f of b.faces) {
      const nf = f.normal.clone().normalize();
      for (const v of V) {
        const u = v.clone().projectOnPlane(nf);
        if (u.lengthSq() < 1e-8) continue;
        const R = frameQuat(nf, u.normalize()).multiply(inv0);
        if (!V.every((p) => onSolid(p.clone().applyQuaternion(R)))) continue;
        if (!out.some((q) => q.angleTo(R) < 1e-3)) out.push(R);
      }
    }
    symCache.set(b.type, out);
    return out;
  }

  /** A symmetry that carries `from` onto `to` (both body-space directions). */
  function symmetryCarrying(b, from, to) {
    const a = from.clone().normalize(), z = to.clone().normalize();
    const t = new THREE.Vector3();
    let best = null, bd = -Infinity;
    for (const R of symmetriesFor(b)) {
      const d = t.copy(a).applyQuaternion(R).dot(z);
      if (d > bd) { bd = d; best = R; }
    }
    return bd > 0.999 ? best : null;
  }

  function directionFor(b, value) {
    if (b.apex) {
      const a = b.apex.find((x) => x.value === value);
      if (!a) console.warn(`[dice-overlay] rollTo: ${b.type} has no ${value}; left to the simulation.`);
      return a ? a.vertex : null;
    }
    let f = b.faces.find((x) => x.value === value);
    // a d10 marked "0" is a 10; a d100 is marked 00,10,…,90 but is often logged 0–9
    if (!f && b.type === 'd10' && value === 0) f = b.faces.find((x) => x.value === 10);
    if (!f && b.type === 'd100') f = b.faces.find((x) => x.value === (value % 10) * 10);
    if (!f) console.warn(`[dice-overlay] rollTo: ${b.type} has no face showing ${value}; left to the simulation.`);
    return f ? f.normal : null;
  }

  /**
   * Rotate each die in body space so the required value comes up, then rewind.
   * Called from roll() after the dice are launched but before a frame is drawn.
   */
  function applyDirected(wanted) {
    const snap = bodies.map((b) => ({
      pos: b.pos.clone(), quat: b.quat.clone(), vel: b.vel.clone(), omega: b.omega.clone(),
    }));

    for (let n = 0; n < 1400; n++) {
      let live = false;
      for (const b of bodies) if (!b.asleep && !b.settling) { stepBody(b, 1 / 120); live = true; }
      separate();
      if (!live) break;
    }
    const landed = bodies.map((b) => restingValue(b, b.quat));

    bodies.forEach((b, i) => {
      const s = snap[i];
      b.pos.copy(s.pos); b.quat.copy(s.quat); b.vel.copy(s.vel); b.omega.copy(s.omega);
      b.asleep = false; b.settling = null; b.prevVel.copy(s.vel);

      const want = wanted[i];
      if (want == null || want === landed[i].value) { b.sync(); return; }
      const from = directionFor(b, want);
      if (!from) { b.sync(); return; }
      const R = symmetryCarrying(b, from, landed[i].dir);
      if (R) b.quat.multiply(R).normalize();
      b.sync();
    });
  }

  /* ---------------------------------------------------------------- *
   * Public API
   * ---------------------------------------------------------------- */

  /** Set the standing pool from a formula string or a {d20:1, d6:2} map. */
  function setPool(spec) {
    ALL_TYPES.forEach((t) => (pool[t] = 0));
    modifier = 0;
    if (typeof spec === 'string') {
      for (const m of spec.matchAll(/(\d*)\s*d\s*(%|\d+)/gi)) {
        const n = Math.max(1, parseInt(m[1] || '1', 10));
        if (m[2] === '%') { pool.d100 += n; pool.d10 += n; }
        else if (pool[`d${m[2]}`] !== undefined) pool[`d${m[2]}`] += n;
      }
      const mod = spec.replace(/\d*d\s*(%|\d+)/gi, '').match(/[+-]\s*\d+/g);
      if (mod) modifier = mod.reduce((a, s) => a + parseInt(s.replace(/\s/g, ''), 10), 0);
      if (/\badv/i.test(spec)) mode = 'adv';
      else if (/\bdis/i.test(spec)) mode = 'dis';
      const keep = spec.match(/k([hl])\s*(\d+)/i);
      if (keep) { keepMode = keep[1].toLowerCase() === 'h' ? 'high' : 'low'; keepN = parseInt(keep[2], 10) || 1; }
      else keepMode = 'all';
    } else if (spec) {
      Object.entries(spec).forEach(([k, v]) => { if (pool[k] !== undefined) pool[k] = v; });
    }
  }

  const parse = (s) => (typeof s === 'string' ? parseSpec(s) : parseSpec(s.formula, s.label));
  const toList = (spec) => (Array.isArray(spec) ? spec.map(parse) : [parse(spec)]);

  const api = {
    /** Client-authoritative: throw, and read whatever comes up. */
    roll(spec) { roll(spec === undefined ? undefined : toList(spec)); },

    /**
     * Server-authoritative: throw, and land on numbers already decided.
     *   rollTo('1d20+5', { rolls: [17] })
     *   rollTo([{label:'Attack',formula:'1d20+7'},{label:'Damage',formula:'2d6+4'}],
     *          { rolls: [12, 3, 5] })
     * `rolls` is one raw die value per die, in throw order — d100 tens dice take
     * 0,10,…,90. Values that are null or absent are left to the simulation.
     */
    rollTo(spec, outcome = {}) {
      pendingWanted = Array.isArray(outcome.rolls) ? outcome.rolls.slice() : [];
      roll(toList(spec));
    },

    setPool,
    setMaterial(k) { if (MATERIALS[k]) { materialKey = k; mixed = false; } },
    setMixed(on) { mixed = !!on; },
    setStyle(k) { if (STYLES[k]) style = STYLES[k]; },
    setMode(m) { mode = ['normal', 'adv', 'dis'].includes(m) ? m : 'normal'; },
    setKeep(m, n) { keepMode = ['all', 'high', 'low'].includes(m) ? m : 'all'; if (n) keepN = n; },
    setModifier(n) { modifier = n | 0; },
    clear() { clearDice(); clearGlow(); setBusy(false); },
    rerollOne,

    /** Fired for you, but also readable: the last settled result. */
    get result() { return lastResult; },
    get dice() { return bodies.map((b) => ({ type: b.type, value: b.result, dropped: !!b.dropped })); },
    get rolling() { return rolling; },
    get reducedMotion() { return REDUCED; },

    set onResult(fn) { hooks.onResult = fn; },
    set onRollStart(fn) { hooks.onRollStart = fn; },
    set onCrit(fn) { hooks.onCrit = fn; },
    set onBusy(fn) { hooks.onBusy = fn; },
    set onHover(fn) { hooks.onHover = fn; },

    MATERIALS, DIE_TYPES, ALL_TYPES,
    _internals: { THREE, scene, camera, renderer, bodies, symmetriesFor, restingValue },
  };
  return api;
}
