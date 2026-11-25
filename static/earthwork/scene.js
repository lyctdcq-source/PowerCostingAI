// 土石方数智化算量 · 三维场景：现状地表、插值地层、钻孔、设计场平面与边坡、挡墙、剖切与漫游。
// three.js 随平台本地发布（static/lib/three），不访问外网。
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
import { UNITS } from './engine.js';

const UC = Object.fromEntries(UNITS.map(u => [u.key, u.color]));

export function createScene(engine, canvas, { onPickHole, onMove, background = '#eef1f5' } = {}) {
  const { D, nx, ny, dx, NC, ox, oy, dem, inside, soilB, rock, l3t, l3b, w2t, BASE, Z0, demMin, demMax, designZ } = engine;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.localClippingEnabled = true;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(background);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.5, 6000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x665544, 1.4));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(-200, 300, 250); scene.add(sun);
  const world = new THREE.Group(); scene.add(world);
  let VEX = 1.5; world.scale.set(1, VEX, 1);
  const X = i => D.x0 + i * dx - ox, Zc = j => -(D.y0 + j * dx - oy), Y = z => z - Z0;
  const toLocal = (E, N) => [E - ox, -(N - oy)];

  const clipPlane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);
  const mats = [];
  function mat(color, opts = {}) { const m = new THREE.MeshLambertMaterial(Object.assign({ color, side: THREE.DoubleSide }, opts)); mats.push(m); return m; }
  const state = { excav: false, secDir: 'none', secPos: 0.5, secFlip: false };
  const vis = { ground: true, design: true, walls: true, contours: true, land: true, holes: true, labels: true, soil: true, clay: true, w4: true, w3: true, w2: true, fillU: true };

  // 格网面 → 封闭板体（顶、底、四周侧壁），厚度不足处不建面
  function slabGeometry(topZ, botZ, minTh = 0.02) {
    const pos = new Float32Array(NC * 2 * 3);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      pos.set([X(i), Y(topZ[k]), Zc(j)], k * 3); pos.set([X(i), Y(botZ[k]), Zc(j)], (NC + k) * 3);
    }
    const idx = []; const th = k => topZ[k] - botZ[k];
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      if (th(a) > minTh || th(b) > minTh || th(c) > minTh) { idx.push(a, c, b, NC + a, NC + b, NC + c); }
      if (th(b) > minTh || th(d) > minTh || th(c) > minTh) { idx.push(b, c, d, NC + b, NC + d, NC + c); }
    }
    const wall = (ks) => { for (let n = 0; n < ks.length - 1; n++) { const a = ks[n], b = ks[n + 1]; if (th(a) > minTh || th(b) > minTh) idx.push(a, b, NC + a, b, NC + b, NC + a); } };
    wall([...Array(nx).keys()]); wall([...Array(nx).keys()].map(i => (ny - 1) * nx + i));
    wall([...Array(ny).keys()].map(j => j * nx)); wall([...Array(ny).keys()].map(j => j * nx + nx - 1));
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals(); return g;
  }

  // ---------- 地层
  const geo = {};
  function unitArrays(excav) {
    const cap = (z, k) => excav ? Math.min(z, designZ[k]) : z;
    const T = {}; for (const u of UNITS) T[u.key] = [new Float32Array(NC), new Float32Array(NC)];
    for (let k = 0; k < NC; k++) {
      const g = dem[k];
      T.soil[0][k] = cap(g, k); T.soil[1][k] = cap(soilB[k], k);
      T.clay[0][k] = cap(soilB[k], k); T.clay[1][k] = cap(rock[k], k);
      T.w4[0][k] = cap(rock[k], k); T.w4[1][k] = BASE;
      T.w3[0][k] = cap(l3t[k], k); T.w3[1][k] = cap(l3b[k], k);
      if (w2t[k] > -1e8) { T.w2[0][k] = cap(w2t[k], k); T.w2[1][k] = BASE; } else { T.w2[0][k] = BASE; T.w2[1][k] = BASE; }
      if (excav && designZ[k] > g) { T.fillU[0][k] = designZ[k]; T.fillU[1][k] = g; } else { T.fillU[0][k] = g; T.fillU[1][k] = g; }
      if (excav && designZ[k] > g + 0.01) T.soil[0][k] = T.soil[1][k];   // 填方范围内的耕植土已清除
    }
    return T;
  }
  function buildGeology() {
    const T = unitArrays(state.excav);
    for (const u of UNITS) {
      if (!geo[u.key]) { geo[u.key] = new THREE.Mesh(new THREE.BufferGeometry(), mat(u.color, { transparent: true, opacity: 1 })); geo[u.key].userData.unit = u.key; world.add(geo[u.key]); }
      geo[u.key].geometry.dispose(); geo[u.key].geometry = slabGeometry(T[u.key][0], T[u.key][1]);
    }
    applyVis(); buildSection();
  }

  // ---------- 现状地表
  const gpos = new Float32Array(NC * 3), gcol = new Float32Array(NC * 3);
  const cA = new THREE.Color('#2f6b3a'), cB = new THREE.Color('#c9b98a'), tmp = new THREE.Color();
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const k = j * nx + i; gpos.set([X(i), Y(dem[k]) + 0.03, Zc(j)], k * 3);
    tmp.copy(cA).lerp(cB, (dem[k] - demMin) / (demMax - demMin)); gcol.set([tmp.r, tmp.g, tmp.b], k * 3);
  }
  const gidx = []; for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) { const a = j * nx + i; gidx.push(a, a + nx, a + 1, a + 1, a + nx, a + nx + 1); }
  const gGeo = new THREE.BufferGeometry(); gGeo.setAttribute('position', new THREE.BufferAttribute(gpos, 3)); gGeo.setAttribute('color', new THREE.BufferAttribute(gcol, 3)); gGeo.setIndex(gidx); gGeo.computeVertexNormals();
  const ground = new THREE.Mesh(gGeo, mat(0xffffff, { vertexColors: true, transparent: true, opacity: 0.55, depthWrite: false })); world.add(ground);

  // 等高线
  const contourGroup = new THREE.Group(); world.add(contourGroup);
  {
    const lm = new THREE.LineBasicMaterial({ color: 0x3a3a3a, transparent: true, opacity: 0.55 }), lm2 = new THREE.LineBasicMaterial({ color: 0x111111 });
    for (const c of D.contours) {
      const p = []; for (const [E, N] of c.p) { const [x, z] = toLocal(E, N); p.push(x, Y(c.z) + 0.06, z); }
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
      contourGroup.add(new THREE.Line(g, Math.abs(c.z / 2.5 - Math.round(c.z / 2.5)) < 1e-6 ? lm2 : lm));
    }
  }
  // 用地界线
  const landGroup = new THREE.Group(); world.add(landGroup);
  {
    const lm = new THREE.LineBasicMaterial({ color: 0xd0202a });
    for (const seg of D.land) {
      const p = []; for (const [E, N] of seg) { const [x, z] = toLocal(E, N); p.push(x, Y(engine.demAt(E, N)) + 0.15, z); }
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3)); landGroup.add(new THREE.Line(g, lm));
    }
  }
  // 场平范围线（随设计面升降）
  const platLineGeo = new THREE.BufferGeometry(); const platLine = new THREE.Line(platLineGeo, new THREE.LineBasicMaterial({ color: 0x10a050 })); world.add(platLine);

  // 钻孔柱与编号
  const holeGroup = new THREE.Group(); world.add(holeGroup);
  const labelGroup = new THREE.Group(); world.add(labelGroup);
  const holeMeshes = [];
  function textSprite(t) {
    const cv = document.createElement('canvas'); cv.width = 256; cv.height = 64; const x = cv.getContext('2d');
    x.font = 'bold 34px Microsoft YaHei, sans-serif'; x.fillStyle = 'rgba(255,255,255,0.85)'; const w = x.measureText(t).width + 16; x.fillRect(0, 8, w, 48);
    x.fillStyle = '#14212f'; x.fillText(t, 8, 44); const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false })); s.scale.set(12, 3, 1); s.center.set(0, 0); return s;
  }
  for (const h of D.holes) {
    const [x, z] = toLocal(h.E, h.N);
    for (const [u, d0, d1] of h.segs) {
      const len = d1 - d0; if (len <= 0) continue;
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.9, len, 12), mat(UC[u] || '#888'));
      m.position.set(x, Y(h.z - (d0 + d1) / 2), z); m.userData.hole = h; holeGroup.add(m); holeMeshes.push(m);
    }
    const s = textSprite(h.id); s.position.set(x, Y(h.z) + 2, z); labelGroup.add(s);
  }

  // 设计面（挖填着色）
  const dsGeo = new THREE.BufferGeometry(); const dsPos = new Float32Array(NC * 3), dsCol = new Float32Array(NC * 3);
  dsGeo.setAttribute('position', new THREE.BufferAttribute(dsPos, 3)); dsGeo.setAttribute('color', new THREE.BufferAttribute(dsCol, 3));
  const design = new THREE.Mesh(dsGeo, mat(0xffffff, { vertexColors: true, transparent: true, opacity: 0.85, polygonOffset: true, polygonOffsetFactor: -2 })); world.add(design);
  const cEven = new THREE.Color('#9fd39b'), cCut = new THREE.Color('#c8452f'), cCutL = new THREE.Color('#f2c1b6'), cFill = new THREE.Color('#2f73c8'), cFillL = new THREE.Color('#bcd5f2'), col = new THREE.Color();
  // 挡墙
  const wallGeo = new THREE.BufferGeometry(); const walls = new THREE.Mesh(wallGeo, mat(0xffffff, { vertexColors: true })); world.add(walls);
  function buildWalls() {
    const { NS, sE, sN, sTx, sTy, sDs, wTyp, wTop, wBot } = engine;
    const pos = [], colr = [], cF = new THREE.Color('#a3a8b0'), cY = new THREE.Color('#5d6470');
    for (let s = 0; s < NS; s++) {
      if (!wTyp[s]) continue; const h = sDs[s] / 2;
      const [ax, az] = toLocal(sE[s] - sTx[s] * h, sN[s] - sTy[s] * h), [bx, bz] = toLocal(sE[s] + sTx[s] * h, sN[s] + sTy[s] * h), t = Y(wTop[s]), b = Y(wBot[s]);
      pos.push(ax, t, az, bx, t, bz, bx, b, bz, ax, t, az, bx, b, bz, ax, b, az);
      const c = wTyp[s] === 1 ? cF : cY; for (let i = 0; i < 6; i++) colr.push(c.r, c.g, c.b);
    }
    wallGeo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); wallGeo.setAttribute('color', new THREE.Float32BufferAttribute(colr, 3)); wallGeo.computeVertexNormals();
  }
  // compute(...,fillDesign=true) 之后调用：按 engine.designZ 与挡墙数组重建设计面
  function updateDesign(H, iL, iT) {
    const idx = [];
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = j * nx + i; dsPos.set([X(i), Y(designZ[k]) + 0.08, Zc(j)], k * 3);
      const d = designZ[k] - dem[k];
      if (Math.abs(d) < 0.01) col.copy(cEven); else if (d < 0) col.copy(cCut).lerp(cCutL, Math.max(0, 1 + d / 12)); else col.copy(cFill).lerp(cFillL, Math.max(0, 1 - d / 12));
      dsCol.set([col.r, col.g, col.b], k * 3);
    }
    const dis = k => inside[k] || Math.abs(designZ[k] - dem[k]) > 0.01;
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) { const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1; if (dis(a) && dis(b) && dis(c)) idx.push(a, c, b); if (dis(b) && dis(d) && dis(c)) idx.push(b, c, d); }
    dsGeo.setIndex(idx); dsGeo.attributes.position.needsUpdate = true; dsGeo.attributes.color.needsUpdate = true; dsGeo.computeVertexNormals();
    const p = []; for (const [E, N] of D.platform) { const [x, z] = toLocal(E, N); p.push(x, Y(engine.planeZ(H, iL, iT, E, N)) + 0.2, z); }
    platLineGeo.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
    buildWalls();
    if (state.excav) buildGeology();
  }

  // ---------- 显示控制
  const secGroup = new THREE.Group(); world.add(secGroup);
  const objOf = k => ({ ground, design, walls, contours: contourGroup, land: landGroup, holes: holeGroup, labels: labelGroup })[k] || geo[k];
  function applyVis() {
    for (const k in vis) { const o = objOf(k); if (o) o.visible = vis[k] && (k !== 'fillU' || state.excav); }
    secGroup.children.forEach(m => m.visible = vis[m.userData.unit] !== false && (m.userData.unit !== 'fillU' || state.excav));
  }
  function setVisible(key, on) { vis[key] = on; applyVis(); }
  function setGroundOpacity(v) { ground.material.opacity = v; ground.material.depthWrite = v > 0.95; }
  function setGeologyOpacity(v) { for (const u of UNITS) if (geo[u.key]) { geo[u.key].material.opacity = v; geo[u.key].material.depthWrite = v > 0.95; } }
  function setExaggeration(v) { VEX = v; world.scale.y = VEX; updateClip(); }
  function setExcavated(on) { state.excav = on; buildGeology(); }

  // ---------- 剖切
  const extX = [X(0), X(nx - 1)], extZ = [Zc(ny - 1), Zc(0)];
  function updateClip() {
    const { secDir: dir, secPos: t, secFlip: flip } = state, on = dir !== 'none';
    if (dir === 'x') { const x = extX[0] + t * (extX[1] - extX[0]); clipPlane.normal.set(flip ? 1 : -1, 0, 0); clipPlane.constant = flip ? -x : x; }
    if (dir === 'y') { const z = extZ[0] + t * (extZ[1] - extZ[0]); clipPlane.normal.set(0, 0, flip ? 1 : -1); clipPlane.constant = flip ? -z : z; }
    for (const m of mats) m.clippingPlanes = on ? [clipPlane] : [];
    buildSection();
  }
  function buildSection() {
    for (const m of secGroup.children) { m.geometry.dispose(); m.material.dispose(); }
    secGroup.clear(); const dir = state.secDir; if (dir === 'none') return;
    const t = state.secPos, T = unitArrays(state.excav), line = [];
    if (dir === 'x') { const i = Math.round(t * (nx - 1)); for (let j = 0; j < ny; j++) line.push(j * nx + i); }
    else { const j = Math.round((1 - t) * (ny - 1)); for (let i = 0; i < nx; i++) line.push(j * nx + i); }
    const pos = k => { const i = k % nx, j = Math.floor(k / nx); return [X(i), Zc(j)]; };
    for (const u of UNITS) {
      const [top, bot] = T[u.key], p = [];
      for (let n = 0; n < line.length - 1; n++) {
        const a = line[n], b = line[n + 1]; if (top[a] - bot[a] < 0.02 && top[b] - bot[b] < 0.02) continue;
        const [xa, za] = pos(a), [xb, zb] = pos(b);
        p.push(xa, Y(top[a]), za, xb, Y(top[b]), zb, xb, Y(bot[b]), zb, xa, Y(top[a]), za, xb, Y(bot[b]), zb, xa, Y(bot[a]), za);
      }
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: u.color, side: THREE.DoubleSide })); m.userData.unit = u.key; secGroup.add(m);
    }
    applyVis();
  }
  function setSection(dir, pos, flip) { state.secDir = dir; if (pos != null) state.secPos = pos; if (flip != null) state.secFlip = flip; updateClip(); }

  // ---------- 相机、轨道控制、漫游
  const orbit = new OrbitControls(camera, canvas); orbit.enableDamping = true;
  const views = { iso: () => [[260, 230, 260], [0, 40, 0]], top: () => [[0, 520, 1], [0, 0, 0]], under: () => [[-170, (Y(BASE) - 45) * VEX, 170], [0, (Y(BASE) + 10) * VEX, 0]] };
  // 二三维联动：视点移动时报告注视点和相机的平面位置；focus 把注视点移到指定平面位置（落在地面上）
  orbit.addEventListener('change', () => { if (onMove) onMove({ E: orbit.target.x + ox, N: oy - orbit.target.z, camE: camera.position.x + ox, camN: oy - camera.position.z }); });
  function focus(E, N, range) {
    const [x, z] = toLocal(E, N), offset = camera.position.clone().sub(orbit.target);
    if (range) offset.setLength(Math.max(30, Math.min(range, 1500)));
    orbit.target.set(x, Y(engine.demAt(E, N)) * VEX, z); camera.position.copy(orbit.target).add(offset); orbit.update();
  }
  // 场平范围线贴在现状地面上（不依赖设计标高）
  function platformOnGround() {
    const p = []; for (const [E, N] of D.platform) { const [x, z] = toLocal(E, N); p.push(x, Y(engine.demAt(E, N)) + 0.3, z); }
    platLineGeo.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
  }
  const walker = new PointerLockControls(camera, document.body); const keys = {};
  function setView(v) { if (!views[v]) return; if (walker.isLocked) walker.unlock(); const [p, t] = views[v](); camera.position.set(...p); orbit.target.set(...t); orbit.update(); }
  walker.addEventListener('lock', () => { orbit.enabled = false; });
  walker.addEventListener('unlock', () => { orbit.enabled = true; const d = new THREE.Vector3(); camera.getWorldDirection(d); orbit.target.copy(camera.position).addScaledVector(d, 40); orbit.update(); for (const k in keys) keys[k] = false; });
  // 漫游按键只在指针锁定时生效，不影响平台其他输入框
  addEventListener('keydown', e => { if (walker.isLocked) { keys[e.code] = true; e.preventDefault(); } });
  addEventListener('keyup', e => { keys[e.code] = false; });

  // ---------- 点选钻孔
  const ray = new THREE.Raycaster();
  canvas.addEventListener('click', e => {
    if (walker.isLocked) return; const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), camera);
    const hit = ray.intersectObjects(holeMeshes.filter(m => vis.holes))[0];
    if (onPickHole) onPickHole(hit ? hit.object.userData.hole : null);
  });

  // ---------- 渲染循环：视图隐藏时暂停
  function resize() { const box = canvas.parentElement, w = box.clientWidth, h = box.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
  const clock = new THREE.Clock(); let active = false, frame = 0;
  function loop() {
    if (!active) return;
    const dt = Math.min(clock.getDelta(), 0.1);
    if (walker.isLocked) {
      const sp = 40 * dt;
      if (keys.KeyW) walker.moveForward(sp); if (keys.KeyS) walker.moveForward(-sp); if (keys.KeyA) walker.moveRight(-sp); if (keys.KeyD) walker.moveRight(sp);
      if (keys.Space) camera.position.y += sp; if (keys.ShiftLeft || keys.ShiftRight) camera.position.y -= sp;
    } else orbit.update();
    renderer.render(scene, camera); frame = requestAnimationFrame(loop);
  }
  function setActive(on) { if (on === active) return; active = on; cancelAnimationFrame(frame); if (on) { clock.getDelta(); resize(); loop(); } else if (walker.isLocked) walker.unlock(); }
  new ResizeObserver(resize).observe(canvas.parentElement);

  buildGeology(); setView('iso'); updateClip();
  return {
    vis, updateDesign, focus, platformOnGround, setPlatformVisible: on => { platLine.visible = on; }, setVisible, setGroundOpacity, setGeologyOpacity, setExaggeration, setExcavated, setSection, setView,
    walk: () => walker.lock(), setActive, resize, renderOnce: () => renderer.render(scene, camera),
    dispose: () => { setActive(false); orbit.dispose(); renderer.dispose(); },
  };
}
