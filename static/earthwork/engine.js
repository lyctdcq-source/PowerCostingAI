// 土石方数智化算量 · 计算引擎：分层挖填、挡墙护坡、造价、标高与纵横坡寻优。
// 纯计算，不接触 DOM 和 three.js；浏览器与 node 均可运行（engine.test.mjs 用图纸方案回归校核）。
// 场平设计面为平面 z = H + iL·s + iT·t：H 为场平范围形心处标高，s 垂直场地长边（+ 南高北低），t 沿长边（+ 东高西低）。

export const UNITS = [
  { key: 'soil', name: '① 耕植土', short: '耕植土', color: '#7b5a39' },
  { key: 'clay', name: '②③ 粘土', short: '粘土', color: '#b5563a' },
  { key: 'w4', name: '④ 全风化砂岩', short: '全风化砂岩', color: '#d4a463' },
  { key: 'w3', name: '强风化砂岩（夹层）', short: '强风化砂岩', color: '#8c5f8f' },
  { key: 'w2', name: '中风化砂岩', short: '中风化砂岩', color: '#4f647f' },
  { key: 'fillU', name: '填方体', short: '填方体', color: '#3d86d6' },
];

// 挡墙截面圬工量（《站外挡土墙详图》，m³/m，墙高 2～8 m、步长 0.5 m）
const VY = [0.99, 1.49, 2.06, 2.69, 3.40, 4.20, 5.53, 6.55, 7.64, 8.82, 10.00, 11.35, 12.77];   // 仰斜式挖方区挡土墙 YJDA
const VF = [1.59, 2.31, 3.15, 4.06, 5.35, 6.84, 8.27, 10.06, 11.94, 14.02, 16.22, 18.63, 21.16]; // 俯斜式填方挡土墙 FJDB
export function wallVolume(h, table) {
  if (h <= 0) return 0;
  if (h < 2) return table[0] * h / 2;
  if (h >= 8) return table[12] * (h / 8) ** 2;
  const f = (h - 2) / 0.5, i = Math.floor(f);
  return table[i] + (table[i + 1] - table[i]) * (f - i);
}

// 单价与规则参数：[名称, 单位, 默认值]
export const defaultPrices = () => ({
  soil: ['耕植土挖除/清表', '元/m³', 18], clay: ['粘土挖方', '元/m³', 16], w4: ['全风化砂岩挖方', '元/m³', 24],
  w3: ['强风化砂岩挖方', '元/m³', 42], w2: ['中风化砂岩挖方', '元/m³', 75],
  fill: ['填方分层碾压', '元/m³', 15], spoil: ['余方外运弃置', '元/m³', 28], borrow: ['借方外购运入', '元/m³', 58],
  wall: ['C25毛石混凝土挡墙（含基础）', '元/m³', 620], slope: ['方格骨架植物护坡', '元/m²', 95],
  kc: ['可用挖方压实折算系数', '', 0.87],
});
export const defaultRules = () => ({
  iMin: ['合成坡度下限（寻优）', '%', 0.5], iMax: ['合成坡度上限（寻优）', '%', 2.0],
  thr: ['挖填平衡区阈值（仅设围墙）', 'm', 0.5],
  hw: ['挖方挡墙外露高度', 'm', 1.8], embC: ['挖方挡墙埋深', 'm', 1.2],
  bench: ['墙顶平台宽（含护脚排水沟）', 'm', 3.5], mcut: ['挖方边坡坡率 1:', '', 1.25], hmin: ['骨架护坡起设坡高', 'm', 2.0],
  embF: ['填方挡墙埋深（含扩展基础折算）', 'm', 2.75],
  mfill: ['填方边坡坡率 1:（仅自由放坡）', '', 1.5],
});
// 图纸方案（《土方平衡图》：北侧边线场平标高 1841.64，由南向北整体放坡 2.0%）及图纸工程量，用于校核
export const DRAWING = {
  zNorth: 1841.64, iL: 0.02, iT: 0,
  quantities: { cutInside: 63627, fillInside: 34568, cutOutside: 6110, fillOutside: 0, wall: 6648.3, slopeArea: 919.8, slopeFoot: 174.1 },
};

export function createEngine(D) {
  const nx = D.nx, ny = D.ny, dx = D.dx, NC = nx * ny, A = dx * dx;
  const [ox, oy] = D.origin;
  const dem = Float32Array.from(D.dem), ts = Float32Array.from(D.tsoil), tc = Float32Array.from(D.tclay),
    t3t = Float32Array.from(D.t3top), t3h = Float32Array.from(D.t3th), i2 = Float32Array.from(D.i2),
    inside = Uint8Array.from(D.inside), dist = Float32Array.from(D.dist);

  // ---------- 地层界面格网
  const soilB = new Float32Array(NC), rock = new Float32Array(NC), l3t = new Float32Array(NC), l3b = new Float32Array(NC), w2t = new Float32Array(NC);
  let zmin = 1e9;
  for (let k = 0; k < NC; k++) {
    soilB[k] = dem[k] - ts[k]; rock[k] = soilB[k] - tc[k]; l3t[k] = rock[k] - t3t[k]; l3b[k] = l3t[k] - t3h[k];
    w2t[k] = i2[k] > 0.5 ? rock[k] - D.w2top : -1e9; zmin = Math.min(zmin, rock[k]);
  }
  let demMin = 1e9, demMax = -1e9, pMin = 1e9, pMax = -1e9, w2Min = 1e9;
  for (let k = 0; k < NC; k++) {
    demMin = Math.min(demMin, dem[k]); demMax = Math.max(demMax, dem[k]);
    if (inside[k]) { pMin = Math.min(pMin, dem[k]); pMax = Math.max(pMax, dem[k]); }
    if (w2t[k] > -1e8) w2Min = Math.min(w2Min, w2t[k] - 6);
  }
  const BASE = Math.floor(Math.min(zmin - 12, w2Min));
  for (let k = 0; k < NC; k++) if (w2t[k] > -1e8) w2t[k] = Math.max(w2t[k], BASE + 0.5);
  const Z0 = Math.floor(demMin) - 2;   // 场景竖向基准

  function demAt(E, N) {
    const fi = (E - D.x0) / dx, fj = (N - D.y0) / dx;
    const i = Math.max(0, Math.min(nx - 2, Math.floor(fi))), j = Math.max(0, Math.min(ny - 2, Math.floor(fj)));
    const u = Math.min(1, Math.max(0, fi - i)), v = Math.min(1, Math.max(0, fj - j)), k = j * nx + i;
    return dem[k] * (1 - u) * (1 - v) + dem[k + 1] * u * (1 - v) + dem[k + nx] * (1 - u) * v + dem[k + nx + 1] * u * v;
  }

  // ---------- 场地坐标系：场平范围多边形、坡度轴、形心
  const PL = D.platform.map(q => [q[0], q[1]]);
  if (PL.length > 1 && Math.hypot(PL[0][0] - PL[PL.length - 1][0], PL[0][1] - PL[PL.length - 1][1]) < 1e-6) PL.pop();
  let A2 = 0, cxS = 0, cyS = 0, LE = 0, ux = 1, uy = 0;
  for (let i = 0; i < PL.length; i++) { // 以 PL[0] 为原点计算，保持精度
    const x1 = PL[i][0] - PL[0][0], y1 = PL[i][1] - PL[0][1], x2 = PL[(i + 1) % PL.length][0] - PL[0][0], y2 = PL[(i + 1) % PL.length][1] - PL[0][1];
    const c = x1 * y2 - x2 * y1; A2 += c; cxS += (x1 + x2) * c; cyS += (y1 + y2) * c;
    const l = Math.hypot(x2 - x1, y2 - y1); if (l > LE) { LE = l; ux = (x2 - x1) / l; uy = (y2 - y1) / l; }
  }
  if (ux < 0) { ux = -ux; uy = -uy; }
  const PCX = PL[0][0] + cxS / (3 * A2), PCY = PL[0][1] + cyS / (3 * A2);   // 场平范围形心
  const vx = uy, vy = -ux;   // 纵坡轴：垂直场地长边，指向场地“南”侧；横坡轴 (ux,uy) 沿长边指向“东”侧
  const azOf = (a, b) => ((Math.atan2(a, b) * 180 / Math.PI) + 360) % 360;
  const AZ_U = azOf(ux, uy), AZ_V = azOf(vx, vy);
  const sOf = (E, N) => (E - PCX) * vx + (N - PCY) * vy, tOf = (E, N) => (E - PCX) * ux + (N - PCY) * uy;
  const cS = new Float32Array(NC), cT = new Float32Array(NC);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) { const k = j * nx + i, E = D.x0 + i * dx, N = D.y0 + j * dx; cS[k] = sOf(E, N); cT[k] = tOf(E, N); }
  const INS = []; for (let k = 0; k < NC; k++) if (inside[k]) INS.push(k);
  const platformArea = Math.abs(A2 / 2);

  // ---------- 场平边线断面（每 1 m）：到征地线的净距、沿外法线的地面线
  const DSTEP = 0.5, WMAX = 30, NP = Math.round(WMAX / DSTEP) + 1;
  const LR = D.land.flat();   // 征地线为 CAD 虚线短段，按存储顺序首尾相接即成闭合环
  function rayLand(E, N, a, b) {
    let best = WMAX;
    for (let i = 0; i < LR.length; i++) {
      const p = LR[i], q = LR[(i + 1) % LR.length], ex = q[0] - p[0], ey = q[1] - p[1], den = a * ey - b * ex; if (Math.abs(den) < 1e-12) continue;
      const t = ((p[0] - E) * ey - (p[1] - N) * ex) / den, s = ((p[0] - E) * b - (p[1] - N) * a) / den; if (t > 0.05 && t < best && s >= 0 && s <= 1) best = t;
    }
    return best;
  }
  const STa = [];
  for (let e = 0; e < PL.length; e++) {
    const [x1, y1] = PL[e], [x2, y2] = PL[(e + 1) % PL.length], L = Math.hypot(x2 - x1, y2 - y1), tx = (x2 - x1) / L, ty = (y2 - y1) / L;
    const onx = A2 > 0 ? ty : -ty, ony = A2 > 0 ? -tx : tx, n = Math.max(1, Math.round(L));
    for (let q = 0; q < n; q++) { const f = (q + 0.5) / n; STa.push([x1 + (x2 - x1) * f, y1 + (y2 - y1) * f, onx, ony, L / n, tx, ty]); }
  }
  const NS = STa.length;
  const sE = new Float64Array(NS), sN = new Float64Array(NS), sNx = new Float32Array(NS), sNy = new Float32Array(NS), sDs = new Float32Array(NS), sTx = new Float32Array(NS), sTy = new Float32Array(NS),
    sS = new Float32Array(NS), sT = new Float32Array(NS), Wa = new Float32Array(NS), g0 = new Float32Array(NS), gW = new Float32Array(NS), GP = new Float32Array(NS * NP);
  for (let s = 0; s < NS; s++) {
    const [E, N, a, b, ds, tx, ty] = STa[s]; sE[s] = E; sN[s] = N; sNx[s] = a; sNy[s] = b; sDs[s] = ds; sTx[s] = tx; sTy[s] = ty; sS[s] = sOf(E, N); sT[s] = tOf(E, N);
    Wa[s] = rayLand(E, N, a, b); g0[s] = demAt(E, N);
    for (let q = 0; q < NP; q++) GP[s * NP + q] = demAt(E + a * q * DSTEP, N + b * q * DSTEP);
    const f = Wa[s] / DSTEP, i = Math.min(NP - 2, Math.floor(f)); gW[s] = GP[s * NP + i] + (GP[s * NP + i + 1] - GP[s * NP + i]) * (f - i);
  }
  // 场外可及格网，各自归属最近的边线断面
  const ACTl = [], NEARl = [];
  for (let k = 0; k < NC; k++) {
    if (inside[k] || dist[k] > 32) continue; const E = D.x0 + (k % nx) * dx, N = D.y0 + Math.floor(k / nx) * dx; let bd = 1e18, bs = 0;
    for (let s = 0; s < NS; s++) { const a = sE[s] - E, b = sN[s] - N, d = a * a + b * b; if (d < bd) { bd = d; bs = s; } }
    ACTl.push(k); NEARl.push(bs);
  }
  const ACT = Int32Array.from(ACTl), NEAR = Int32Array.from(NEARl), NA = ACT.length;

  const P = defaultPrices(), RU = defaultRules();
  const designZ = new Float32Array(NC);
  const zEs = new Float32Array(NS), heS = new Float32Array(NS), wTyp = new Uint8Array(NS), wTop = new Float32Array(NS), wBot = new Float32Array(NS);
  const ov = (a, b, c, d) => Math.max(0, Math.min(b, d) - Math.max(a, c)); // 区间 [a,b] 与 [c,d] 的重叠长度

  // ---------- 设计面 z = H + iL·s + iT·t 下的土石方、挡墙、护坡与造价；mode: 'rule' 按设计规则 | 'free' 自由放坡
  function compute(H, iL, iT, mode = 'rule', fillDesign = false) {
    const rule = mode === 'rule', kc = P.kc[2] || 1;
    const mc = RU.mcut[2] || 1, mf = RU.mfill[2] || 1.5, hw = RU.hw[2], Bw = RU.bench[2], embC = RU.embC[2], embF = RU.embF[2], thr = RU.thr[2], hmin = RU.hmin[2];
    const V = { soil: 0, clay: 0, w4: 0, w3: 0, w2: 0, fill: 0, strip: 0, slopeA: 0, cutA: 0, fillA: 0, inCut: 0, inFill: 0, outCut: 0, outFill: 0, wFL: 0, wFV: 0, wYL: 0, wYV: 0, wFH: 0, wYH: 0, slopeL: 0, raisedL: 0 };
    if (fillDesign) designZ.set(dem);
    const cut = (k, z, g) => {
      const lens = t3h[k] > 0.02 ? ov(z, g, l3b[k], l3t[k]) : 0, w2 = w2t[k] > -1e8 ? ov(z, g, BASE, w2t[k]) : 0;
      V.soil += ov(z, g, soilB[k], g) * A; V.clay += ov(z, g, rock[k], soilB[k]) * A; V.w4 += (ov(z, g, BASE, rock[k]) - lens - w2) * A; V.w3 += lens * A; V.w2 += w2 * A; V.cutA += A;
    };
    const fil = (k, z, g) => { V.fill += (z - g) * A; V.strip += Math.min(ts[k], 0.6) * A; V.fillA += A; };
    for (let n = 0; n < INS.length; n++) {
      const k = INS[n], g = dem[k], z = H + iL * cS[k] + iT * cT[k];
      if (fillDesign) designZ[k] = z;
      if (z < g - 1e-4) { cut(k, z, g); V.inCut += (g - z) * A; } else if (z > g + 1e-4) { fil(k, z, g); V.inFill += (z - g) * A; }
    }
    // 逐断面：挡墙类型与墙高、平台、骨架护坡
    const sq = Math.sqrt(1 + mc * mc), q0 = Math.ceil(Bw / DSTEP);
    for (let s = 0; s < NS; s++) {
      const zE = H + iL * sS[s] + iT * sT[s]; zEs[s] = zE; wTyp[s] = 0; if (!rule) continue;
      const W = Wa[s], he = Math.max(hw, gW[s] - zE - Math.max(0, W - Bw) / mc); heS[s] = he;   // 坡顶超出征地线时加高挡墙
      const dl = zE - g0[s], ds = sDs[s];
      if (dl > thr) { const h = dl + embF; V.wFL += ds; V.wFV += wallVolume(h, VF) * ds; if (h > V.wFH) V.wFH = h; wTyp[s] = 1; wTop[s] = zE; wBot[s] = g0[s] - embF; }
      else if (-dl > thr) {
        const ex = Math.min(-dl, he);
        if (ex > thr) { const h = ex + embC; V.wYL += ds; V.wYV += wallVolume(h, VY) * ds; if (h > V.wYH) V.wYH = h; if (he > hw + 0.05) V.raisedL += ds; wTyp[s] = 2; wTop[s] = zE + ex; wBot[s] = zE - embC; }
      }
      const bench = zE + he; let hs = 0;
      for (let q = q0; q < NP; q++) { const d = q * DSTEP; if (d > W) break; const v = Math.min(GP[s * NP + q], bench + (d - Bw) / mc) - bench; if (v > hs) hs = v; }
      if (hs >= hmin) { V.slopeA += hs * sq * ds; V.slopeL += ds; }
    }
    // 场外格网
    const fc = Math.sqrt(1 + 1 / (mc * mc)), ff = Math.sqrt(1 + 1 / (mf * mf));
    for (let n = 0; n < NA; n++) {
      const k = ACT[n], s = NEAR[n], g = dem[k], d = dist[k], zE = zEs[s]; let z = g;
      if (rule) { if (d <= Wa[s]) { const zs = zE + heS[s] + Math.max(0, d - Bw) / mc; if (zs < g) z = zs; } }   // 填方侧由挡墙收边，站外不填
      else z = g > zE ? Math.min(g, zE + d / mc) : Math.max(g, zE - d / mf);
      if (fillDesign) designZ[k] = z;
      if (z < g - 1e-4) { cut(k, z, g); V.outCut += (g - z) * A; if (!rule) V.slopeA += A * fc; }
      else if (z > g + 1e-4) { fil(k, z, g); V.outFill += (z - g) * A; V.slopeA += A * ff; }
    }
    const usable = V.clay + V.w4 + V.w3 + V.w2, need = V.fill / kc;
    const spoil = Math.max(0, usable - need) + V.soil + V.strip, borrow = Math.max(0, need - usable);
    const c = {
      '挖方（按地层）': V.soil * P.soil[2] + V.clay * P.clay[2] + V.w4 * P.w4[2] + V.w3 * P.w3[2] + V.w2 * P.w2[2],
      '填方碾压': V.fill * P.fill[2], '填方区清表': V.strip * P.soil[2],
      '余方外运': spoil * P.spoil[2], '借方': borrow * P.borrow[2],
      '挡墙': (V.wFV + V.wYV) * P.wall[2], '护坡': V.slopeA * P.slope[2],
    };
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    return { H, iL, iT, mode, V, usable, need, spoil, borrow, c, total, cut: V.soil + usable };
  }

  // ---------- 当前坡度下沿标高的一维扫描：造价曲线、最优标高、挖填平衡标高
  const hMin = Math.floor(pMin), hMax = Math.ceil(pMax);
  function scan(iL, iT, mode = 'rule') {
    const curve = [];
    for (let h = hMin; h <= hMax + 1e-6; h += 0.1) curve.push(compute(+h.toFixed(2), iL, iT, mode));
    let best = curve.reduce((a, b) => b.total < a.total ? b : a);
    // 在 ±0.3 m 内细化：造价曲线在底部受挡墙阈值影响略有锯齿
    let b2 = best; for (let h = best.H - 0.3; h <= best.H + 0.3 + 1e-9; h += 0.01) { const r = compute(+h.toFixed(2), iL, iT, mode); if (r.total < b2.total) b2 = r; } best = b2;
    const balance = curve.reduce((a, b) => Math.abs(b.usable - b.need) < Math.abs(a.usable - a.need) ? b : a);
    return { curve, best, balance };
  }

  // ---------- 三维寻优：标高 + 纵坡 + 横坡（坡度 0.5% 粗搜 → 0.1% 细搜，标高用黄金分割）
  async function optimize3(mode = 'rule', onProgress = null) {
    const iMax = RU.iMax[2] / 100, iMin = RU.iMin[2] / 100;
    const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const t0 = now(); let nEval = 0, tY = now();
    const ev = (h, a, b) => { nEval++; return compute(h, a, b, mode); };
    const bestH = (a, b) => {
      let bb = null; for (let h = hMin; h <= hMax + 1e-6; h += 1) { const r = ev(h, a, b); if (!bb || r.total < bb.total) bb = r; }
      let lo = bb.H - 1, hi = bb.H + 1; const g = (Math.sqrt(5) - 1) / 2; let x1 = hi - g * (hi - lo), x2 = lo + g * (hi - lo), f1 = ev(x1, a, b), f2 = ev(x2, a, b);
      for (let it = 0; it < 16; it++) { if (f1.total < f2.total) { hi = x2; x2 = x1; f2 = f1; x1 = hi - g * (hi - lo); f1 = ev(x1, a, b); } else { lo = x1; x1 = x2; f1 = f2; x2 = lo + g * (hi - lo); f2 = ev(x2, a, b); } }
      const r = f1.total < f2.total ? f1 : f2; return bb.total < r.total ? bb : r;
    };
    const ok = (a, b) => { const g = Math.hypot(a, b); return g <= iMax + 1e-9 && g >= iMin - 1e-9; };
    const grid = (ca, cb, half, step) => { const o = [], m = Math.round(half / step); for (let i = -m; i <= m; i++) for (let j = -m; j <= m; j++) o.push([+(ca + i * step).toFixed(5), +(cb + j * step).toFixed(5)]); return o; };
    let b3 = null;
    const run = async (cands, label) => {
      for (let n = 0; n < cands.length; n++) {
        const [a, b] = cands[n]; if (ok(a, b)) { const r = bestH(a, b); if (!b3 || r.total < b3.total) b3 = r; }
        if (onProgress && now() - tY > 60) { onProgress(`${label}：${n + 1}/${cands.length} …`); await new Promise(r => setTimeout(r, 0)); tY = now(); }
      }
    };
    await run(grid(0, 0, iMax, 0.005), '粗搜（坡度步长 0.5%）');
    if (b3) await run(grid(b3.iL, b3.iT, 0.005, 0.001), '细搜（坡度步长 0.1%）');
    const flat = bestH(0, 0);
    return { best: b3, flat, evaluations: nEval, seconds: (now() - t0) / 1000 };
  }

  // 图纸方案对应的中心标高与坡度
  function drawingDesign() {
    let sMin = 1e9; for (const [E, N] of PL) sMin = Math.min(sMin, sOf(E, N));
    return { H: Math.round((DRAWING.zNorth - DRAWING.iL * sMin) * 100) / 100, iL: DRAWING.iL, iT: DRAWING.iT };
  }
  const planeZ = (H, iL, iT, E, N) => H + iL * sOf(E, N) + iT * tOf(E, N);
  function planeRange(H, iL, iT) { let lo = 1e9, hi = -1e9; for (const [E, N] of PL) { const z = planeZ(H, iL, iT, E, N); lo = Math.min(lo, z); hi = Math.max(hi, z); } return [lo, hi]; }

  return {
    D, nx, ny, dx, NC, ox, oy, dem, ts, inside, soilB, rock, l3t, l3b, w2t, BASE, Z0, demMin, demMax, pMin, pMax, hMin, hMax,
    PL, platformArea, AZ_U, AZ_V, sOf, tOf, demAt, planeZ, planeRange,
    NS, sE, sN, sTx, sTy, sDs, wTyp, wTop, wBot, designZ,
    P, RU, compute, scan, optimize3, drawingDesign,
  };
}
