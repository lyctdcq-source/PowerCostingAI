// 土石方数智化算量 · 界面：资料清洗、三维建模、标高寻优三个页签；并向平台暴露智能体动作执行接口。
import { createEngine, UNITS, DRAWING } from './engine.js';
import { createScene } from './scene.js';
import { createCleanPane } from './clean.js';
import { createModelPane } from './modelview.js';

const UC = Object.fromEntries(UNITS.map(u => [u.key, u.color]));
const fmt = v => (v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
const wan = v => ((v || 0) / 1e4).toFixed(1);
const pct = v => (v * 100).toFixed(2) + '%';
const LAYER_LABELS = { ground: '现状地表', design: '设计场平/边坡', walls: '挡墙', contours: '等高线', land: '用地界线', holes: '钻孔柱', labels: '钻孔编号', fillU: '填方体' };
const OPT_LABEL = { full: '放坡寻优', height: '当前坡度下最优标高', balance: '挖填平衡标高', drawing: '套用图纸方案' };

const TEMPLATE = `
<div class="tabs ew-tabs"><button data-ew-tab="clean">资料清洗</button><button data-ew-tab="build">三维建模</button><button class="selected" data-ew-tab="model">标高寻优</button></div>
<div class="ew-pane" data-ew-pane="model"><div class="ew-layout">
 <div class="ew-side">
  <div class="ew-sub">地形：等高线与高程点重建；地层：<span data-ew="nh"></span> 个钻孔插值 · <span data-ew="project"></span></div>
  <h4>场平设计面</h4>
  <div class="ew-row"><label>场地中心标高 H (m)</label><input type="number" data-ew="hNum" step="0.05"></div>
  <input type="range" data-ew="hRange" step="0.05" aria-label="场地中心标高">
  <div class="ew-row"><label>纵坡 (%) <span class="ew-note">+ 南高北低</span></label><input type="number" data-ew="iL" step="0.1" value="0"></div>
  <div class="ew-row"><label>横坡 (%) <span class="ew-note">+ 东高西低</span></label><input type="number" data-ew="iT" step="0.1" value="0"></div>
  <div class="ew-row"><label>边坡处理</label><select data-ew="mode"><option value="rule">按设计规则：挡墙 + 护坡</option><option value="free">自由放坡（不设挡墙）</option></select></div>
  <div class="ew-btns"><button class="primary" data-ew="opt3Btn">放坡寻优（标高 + 纵横坡）</button><button class="button" data-ew="optBtn">当前坡度下最优标高</button><button class="button" data-ew="balBtn">挖填平衡标高</button><button class="button" data-ew="drawBtn">套用图纸方案</button></div>
  <div class="ew-note" data-ew="opt3Note"></div>
  <div class="ew-kv"><span>土石方及挡墙护坡造价</span><span class="ew-big" data-ew="costBig">—</span></div>
  <div class="ew-note" data-ew="optNote"></div>
  <table data-ew="qty"></table>
  <h4>单价（示例值，可修改）</h4>
  <div data-ew="suggestions"></div>
  <table data-ew="prices"></table>
  <div class="ew-btns"><button class="button" data-ew="priceBtn">请模型建议单价</button></div>
  <div class="ew-note">单价为示例假设，用于方案间相对比较，请按工程实际定额与信息价修改。挖方单价含开挖及场内倒运；可用挖方（粘土、风化岩）按折算系数回填，耕植土及清表土外弃。</div>
  <h4>挡墙与护坡布设规则</h4>
  <table data-ew="rules"></table>
  <div class="ew-note">默认值取自本工程《边坡剖面示意图》《挡土墙定位示意图》《站外挡土墙详图》。填方挡墙埋深按《护坡挡墙布置图》工程量表（挡墙 6648.3 m³，含扩展基础）反算。合成坡度限值用于寻优，手动输入坡度不受限制。</div>
  <h4>显示</h4>
  <div class="ew-chk" data-ew="layers"></div>
  <div class="ew-row"><label>地表透明度</label><input type="range" data-ew="groundOp" min="0" max="1" step="0.05" value="0.55"></div>
  <div class="ew-row"><label>地层透明度</label><input type="range" data-ew="geoOp" min="0.1" max="1" step="0.05" value="1"></div>
  <div class="ew-row"><label>竖向夸张</label><input type="range" data-ew="vex" min="1" max="4" step="0.25" value="1.5"><span data-ew="vexv">1.5 倍</span></div>
  <div class="ew-row"><label><input type="checkbox" data-ew="excav"> 按设计开挖/回填显示地层</label></div>
  <h4>剖切</h4>
  <div class="ew-row"><label>方向</label><select data-ew="secDir"><option value="none">不剖切</option><option value="x">东西向推进（沿东坐标）</option><option value="y">南北向推进（沿北坐标）</option></select></div>
  <input type="range" data-ew="secPos" min="0" max="1" step="0.002" value="0.5" aria-label="剖切位置">
  <div class="ew-row"><label><input type="checkbox" data-ew="secFlip"> 反向保留</label></div>
  <h4>视角与漫游</h4>
  <div class="ew-btns"><button class="button" data-ew-view="iso">东南鸟瞰</button><button class="button" data-ew-view="top">俯视</button><button class="button" data-ew-view="under">地下仰视</button><button class="button" data-ew="walk">进入漫游</button><button class="button" data-ew="chartTog">造价曲线</button></div>
  <div class="ew-note">漫游：鼠标转向，W/S/A/D 前后左右，空格上升，Shift 下降，Esc 退出，可飞入地下观察地层。轨道模式：左键旋转、右键平移、滚轮缩放；点击钻孔查看分层。</div>
  <h4>说明</h4>
  <div class="ew-note" data-ew="method"></div>
 </div>
 <div class="ew-stage">
  <canvas data-ew="c3d"></canvas>
  <button class="ew-side-toggle button" data-ew="sideTog" title="显示或隐藏参数面板">参数</button>
  <div class="ew-hud" data-ew="hud"></div>
  <div class="ew-info" data-ew="info" hidden></div>
  <div class="ew-chart" data-ew="chartBox"><canvas data-ew="chart"></canvas></div>
 </div>
</div></div>
<div class="ew-pane" data-ew-pane="clean" hidden></div>
<div class="ew-pane" data-ew-pane="build" hidden></div>`;

export async function mount(root, host) {
  const { api, toast, esc } = host;
  root.classList.add('ew');
  root.innerHTML = '<div class="ew-loading">正在加载三维地质模型…</div>';
  const response = await fetch('/api/earthwork/model');
  if (!response.ok) throw Error((await response.json().catch(() => ({}))).error || '模型数据加载失败');
  const D = await response.json();
  // 没有由清洗成果生成的模型（只有随平台发布的示例模型）时，标高寻优没有数据可算
  if (!D.build) return mountWithoutModel(root, host);
  const engine = createEngine(D), { P, RU, hMin, hMax, pMin, pMax } = engine;
  root.innerHTML = TEMPLATE;
  const $ = k => root.querySelector(`[data-ew="${k}"]`);
  $('nh').textContent = D.holes.length;

  const scene = createScene(engine, $('c3d'), { onPickHole: showHole });
  let H = 0, iL = 0, iT = 0, curve = [], best = null, balance = null, best3 = null, stale = false, last = null, visible = false;
  let sources = null, platformState = null, handledPending = '', busy = false, optimized = false;
  const mode = () => $('mode').value;

  // ---------- 参数表
  function paramTable(el, obj) {
    for (const [k, [l, u, v]] of Object.entries(obj)) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${esc(l)}</td><td class="n"><input type="number" step="${v < 5 ? 0.05 : 1}" value="${v}" data-k="${k}"></td><td>${esc(u)}</td>`; el.appendChild(tr);
    }
    el.addEventListener('input', e => { const k = e.target.dataset.k; if (k) { obj[k][2] = parseFloat(e.target.value) || 0; markStale(); rescan(); update(); } });
  }
  paramTable($('prices'), P); paramTable($('rules'), RU);
  function setParam(el, obj, key, value) { obj[key][2] = value; const input = el.querySelector(`input[data-k="${key}"]`); if (input) input.value = value; markStale(); rescan(); update(); }
  function markStale() { if (best3 && !stale) { stale = true; $('opt3Note').insertAdjacentHTML('beforeend', ' <span class="ew-warn">（参数已修改，请重新寻优）</span>'); } }

  // ---------- 计算与刷新
  function rescan() {
    ({ curve, best, balance } = engine.scan(iL, iT, mode()));
    $('optNote').innerHTML = `当前坡度（纵 ${pct(iL)}，横 ${pct(iT)}）下：最优中心标高 <b>${best.H.toFixed(2)} m</b>，造价 ${wan(best.total)} 万元；挖填平衡标高约 ${balance.H.toFixed(1)} m，造价 ${wan(balance.total)} 万元。`;
  }
  function update() {
    const r = last = engine.compute(H, iL, iT, mode(), true), V = r.V, rule = mode() === 'rule';
    $('costBig').textContent = wan(r.total) + ' 万元';
    const sw = k => `<span class="ew-sw" style="background:${UC[k]}"></span>`;
    const wallRows = rule ? `
     <tr><td>场外边坡挖方 / 填方</td><td class="n">${fmt(V.outCut)} / ${fmt(V.outFill)}</td><td>m³</td></tr>
     <tr><td>填方挡墙（俯斜式）长 / 圬工</td><td class="n">${fmt(V.wFL)} m / ${fmt(V.wFV)}</td><td>m³</td></tr>
     <tr><td>挖方挡墙（仰斜式）长 / 圬工</td><td class="n">${fmt(V.wYL)} m / ${fmt(V.wYV)}</td><td>m³</td></tr>
     <tr><td>最大墙高 填方 / 挖方</td><td class="n">${V.wFH.toFixed(1)} / ${V.wYH.toFixed(1)}</td><td>m</td></tr>
     <tr><td>骨架护坡 坡面 / 长度</td><td class="n">${fmt(V.slopeA)} m² / ${fmt(V.slopeL)}</td><td>m</td></tr>
     <tr><td>因征地线加高挡墙段</td><td class="n">${fmt(V.raisedL)}</td><td>m</td></tr>` : `
     <tr><td>场外边坡挖方 / 填方</td><td class="n">${fmt(V.outCut)} / ${fmt(V.outFill)}</td><td>m³</td></tr>
     <tr><td>边坡坡面面积</td><td class="n">${fmt(V.slopeA)}</td><td>m²</td></tr>`;
    $('qty').innerHTML = `
     <tr><td>挖方合计</td><td class="n">${fmt(r.cut)}</td><td>m³</td></tr>
     <tr><td>${sw('soil')}耕植土</td><td class="n">${fmt(V.soil)}</td><td>m³</td></tr>
     <tr><td>${sw('clay')}粘土</td><td class="n">${fmt(V.clay)}</td><td>m³</td></tr>
     <tr><td>${sw('w4')}全风化砂岩</td><td class="n">${fmt(V.w4)}</td><td>m³</td></tr>
     <tr><td>${sw('w3')}强风化砂岩</td><td class="n">${fmt(V.w3)}</td><td>m³</td></tr>
     <tr><td>${sw('w2')}中风化砂岩</td><td class="n">${fmt(V.w2)}</td><td>m³</td></tr>
     <tr><td>填方（压实方）</td><td class="n">${fmt(V.fill)}</td><td>m³</td></tr>
     <tr><td>填方区清表</td><td class="n">${fmt(V.strip)}</td><td>m³</td></tr>
     <tr><td>余方外运 / 借方</td><td class="n">${fmt(r.spoil)} / ${fmt(r.borrow)}</td><td>m³</td></tr>
     ${wallRows}
     ${Object.entries(r.c).map(([k, v]) => `<tr><td>· ${k}</td><td class="n">${wan(v)}</td><td>万元</td></tr>`).join('')}`;
    scene.updateDesign(H, iL, iT);
    const [zLo, zHi] = engine.planeRange(H, iL, iT), gs = Math.hypot(iL, iT);
    const warn = (gs < RU.iMin[2] / 100 - 1e-9 || gs > RU.iMax[2] / 100 + 1e-9) ? `　<span class="ew-warn">合成坡度 ${pct(gs)} 超出限值</span>` : '';
    $('hud').innerHTML = `<b>H = ${H.toFixed(2)} m</b>　纵坡 ${pct(iL)}　横坡 ${pct(iT)}${warn}<br>挖 ${fmt(r.cut)} m³　填 ${fmt(V.fill)} m³　造价 ${wan(r.total)} 万元<br><span class="ew-note">场平标高 ${zLo.toFixed(2)}–${zHi.toFixed(2)} m；场平范围 ${fmt(engine.platformArea)} m²，场内现状地面 ${pMin.toFixed(2)}–${pMax.toFixed(2)} m</span>`;
    renderMethod(); drawChart();
  }
  function renderMethod() {
    const cv = Object.entries(D.cv || {}).map(([name, [method, table]]) => `${name} RMSE ${table[method][0]} m`).join('，');
    $('method').innerHTML = `地形 DEM ${engine.dx} m 格网。地层界面采用反距离加权插值，留一交叉验证：${esc(cv) || '—'}。强风化砂岩按夹层（透镜体）处理；钻孔控制深度以下（底面 ${engine.BASE} m）为推测。<br>
     场平设计面为平面：z = H + 纵坡·s + 横坡·t，H 为场平范围形心处标高，s 沿 ${engine.AZ_V.toFixed(0)}° 方向（场地“南”向，垂直长边），t 沿 ${engine.AZ_U.toFixed(0)}° 方向（长边）。挖方按各地层分别计量，填方前清除耕植土。<br>
     边坡（“按设计规则”）：沿场平边线每 1 m 取一断面。设计面高于边线地面 ${RU.thr[2]} m 以上为填方侧，设俯斜式填方挡墙，墙顶与场地齐平，站外不填；低于 ${RU.thr[2]} m 以上为挖方侧，设仰斜式挖方挡墙（外露 ${RU.hw[2]} m，埋深 ${RU.embC[2]} m）+ 墙顶平台 ${RU.bench[2]} m + 1:${RU.mcut[2]} 方格骨架植物护坡（坡高 ≥ ${RU.hmin[2]} m 计护坡）；其间为挖填平衡区，仅设围墙。边坡外缘不得超出征地线，超出时自动加高挖方挡墙。挡墙圬工量按详图截面尺寸表插值，超过 8 m 按墙高平方外推。“自由放坡”时场外按挖/填坡率放坡至与地面相交，不设挡墙。`;
  }

  // ---------- 造价曲线
  const chart = $('chart');
  function drawChart() {
    const r = chart.getBoundingClientRect(); if (!r.width || !r.height) return;
    const dpr = devicePixelRatio; chart.width = r.width * dpr; chart.height = r.height * dpr;
    const c = chart.getContext('2d'); c.scale(dpr, dpr); const W = r.width, Hh = r.height, L = 58, R = 14, T = 22, B = 26;
    c.fillStyle = '#242424'; c.font = '12px Microsoft YaHei'; c.fillText(`造价随场地中心标高变化（纵坡 ${pct(iL)}，横坡 ${pct(iT)}；万元）`, L, 15);
    if (!curve.length) return;
    const ys = curve.map(d => d.total / 1e4), ymin = Math.min(...ys), ymax = Math.max(...ys);
    const sx = h => L + (h - hMin) / (hMax - hMin) * (W - L - R), sy = v => T + (1 - (v - ymin) / (ymax - ymin || 1)) * (Hh - T - B);
    c.strokeStyle = '#e2e5e2'; c.lineWidth = 1; c.beginPath();
    for (let g = 0; g <= 4; g++) { const v = ymin + (ymax - ymin) * g / 4; c.moveTo(L, sy(v)); c.lineTo(W - R, sy(v)); c.fillStyle = '#777'; c.fillText(v.toFixed(0), 6, sy(v) + 4); }
    c.stroke();
    for (let h = Math.ceil(hMin); h <= hMax; h += 2) { c.fillStyle = '#777'; c.fillText(h, sx(h) - 14, Hh - 8); }
    c.strokeStyle = '#1f5fae'; c.lineWidth = 2; c.beginPath(); curve.forEach((d, i) => { const x = sx(d.H), y = sy(d.total / 1e4); i ? c.lineTo(x, y) : c.moveTo(x, y); }); c.stroke();
    const mark = (h, col, txt, dy) => { c.strokeStyle = col; c.setLineDash([4, 3]); c.beginPath(); c.moveTo(sx(h), T); c.lineTo(sx(h), Hh - B); c.stroke(); c.setLineDash([]); c.fillStyle = col; c.fillText(txt, Math.min(sx(h) + 4, W - 150), T + 12 + dy); };
    mark(balance.H, '#888', '挖填平衡 ' + balance.H.toFixed(1), 14);
    mark(best.H, '#1a8a3a', '本坡度最优 ' + best.H.toFixed(2), 0);
    mark(H, '#c8452f', '当前 ' + H.toFixed(2), 28);
  }
  chart.addEventListener('click', e => { const r = chart.getBoundingClientRect(); const h = hMin + (e.clientX - r.left - 58) / (r.width - 72) * (hMax - hMin); if (h >= hMin && h <= hMax) setH(h); });
  new ResizeObserver(drawChart).observe(chart);

  // ---------- 设计参数
  const hR = $('hRange'), hN = $('hNum');
  hR.min = hMin; hR.max = hMax; hN.min = hMin; hN.max = hMax;
  function setH(v) { H = Math.round(v * 100) / 100; hR.value = H; hN.value = H.toFixed(2); update(); }
  function setSlopes(a, b) { iL = a; iT = b; $('iL').value = (a * 100).toFixed(2); $('iT').value = (b * 100).toFixed(2); rescan(); }
  hR.addEventListener('input', () => setH(+hR.value)); hN.addEventListener('change', () => setH(+hN.value));
  $('iL').addEventListener('change', () => { setSlopes((parseFloat($('iL').value) || 0) / 100, iT); update(); });
  $('iT').addEventListener('change', () => { setSlopes(iL, (parseFloat($('iT').value) || 0) / 100); update(); });
  $('mode').addEventListener('change', () => { markStale(); rescan(); update(); });
  $('optBtn').onclick = () => setH(best.H);
  $('balBtn').onclick = () => setH(balance.H);
  // 套用图纸方案：需要模型里带有图纸的设计标高和坡度；由清洗成果生成的模型没有，按钮隐藏
  const hasDrawing = Boolean(D.drawing);
  $('drawBtn').hidden = !hasDrawing;
  function applyDrawing() { if (!hasDrawing) throw Error('当前模型没有图纸方案的设计标高和坡度'); const d = engine.drawingDesign(); setSlopes(d.iL, d.iT); setH(d.H); }
  $('drawBtn').onclick = applyDrawing;
  async function optimize() {
    const btn = $('opt3Btn'), note = $('opt3Note'); btn.disabled = true;
    try {
      const o = await engine.optimize3(mode(), text => { note.textContent = text; });
      if (!o.best) { note.textContent = '坡度上下限之间没有可行解，请检查“合成坡度”参数。'; return null; }
      best3 = o.best; stale = false; optimized = true; setSlopes(o.best.iL, o.best.iT); setH(o.best.H);
      const save = o.flat.total - last.total;
      note.innerHTML = `放坡寻优：中心标高 <b>${o.best.H.toFixed(2)} m</b>，纵坡 <b>${pct(o.best.iL)}</b>，横坡 <b>${pct(o.best.iT)}</b>（合成 ${pct(Math.hypot(o.best.iL, o.best.iT))}），造价 <b>${wan(last.total)} 万元</b>；同规则下水平场平最优 ${o.flat.H.toFixed(2)} m、${wan(o.flat.total)} 万元，${save >= 0 ? '节省' : '增加'} ${wan(Math.abs(save))} 万元。<br>计算 ${o.evaluations} 次，用时 ${o.seconds.toFixed(1)} s。`;
      return o;
    } finally { btn.disabled = false; }
  }
  $('opt3Btn').onclick = optimize;

  // ---------- 显示、剖切、视角
  const lay = $('layers');
  for (const k of Object.keys(scene.vis)) {
    const u = UNITS.find(u => u.key === k), l = document.createElement('label');
    l.innerHTML = `<input type="checkbox" checked data-k="${k}">${u ? `<span class="ew-sw" style="background:${u.color}"></span>${u.name}` : LAYER_LABELS[k]}`; lay.appendChild(l);
  }
  lay.addEventListener('change', e => scene.setVisible(e.target.dataset.k, e.target.checked));
  $('groundOp').addEventListener('input', e => scene.setGroundOpacity(+e.target.value));
  $('geoOp').addEventListener('input', e => scene.setGeologyOpacity(+e.target.value));
  $('vex').addEventListener('input', e => { scene.setExaggeration(+e.target.value); $('vexv').textContent = e.target.value + ' 倍'; });
  $('excav').addEventListener('change', e => scene.setExcavated(e.target.checked));
  const applySection = () => scene.setSection($('secDir').value, +$('secPos').value, $('secFlip').checked);
  ['secDir', 'secPos', 'secFlip'].forEach(id => $(id).addEventListener('input', applySection));
  function setView(v) { scene.setView(v); if (v === 'under') { $('geoOp').value = 0.45; scene.setGeologyOpacity(0.45); } }
  root.querySelectorAll('[data-ew-view]').forEach(b => b.onclick = () => setView(b.dataset.ewView));
  $('walk').onclick = () => scene.walk();
  $('chartTog').onclick = () => { $('chartBox').hidden = !$('chartBox').hidden; drawChart(); };
  $('sideTog').onclick = () => root.classList.toggle('ew-side-hidden');
  new ResizeObserver(() => root.classList.toggle('ew-narrow', root.clientWidth < 760)).observe(root);

  function showHole(h) {
    const info = $('info'); if (!h) { info.hidden = true; return; }
    const nm = Object.fromEntries(UNITS.map(u => [u.key, u.short]));
    info.innerHTML = `<b>${esc(h.id)}</b><br><span class="ew-note">E ${h.E.toFixed(3)}　N ${h.N.toFixed(3)}<br>孔口 ${h.z.toFixed(2)} m　稳定水位深度 ${esc(h.water || '—')} m</span>
     <table>${h.segs.map(([u, a, b]) => `<tr><td><span class="ew-sw" style="background:${UC[u]}"></span>${nm[u] || esc(u)}</td><td class="n">${a.toFixed(1)}–${b.toFixed(1)} m</td><td class="n">${(h.z - b).toFixed(2)}</td></tr>`).join('')}</table>
     <span class="ew-note">最后一列为层底高程（m）</span>`;
    info.hidden = false;
  }

  // ---------- 方案快照：交给平台保存、比选和发给模型的只有这些汇总数字，不含坐标
  const r2 = v => Math.round(v * 100) / 100;
  function snapshot() {
    const r = last, V = r.V, [zLo, zHi] = engine.planeRange(H, iL, iT);
    const brief = x => x ? { H: r2(x.H), iL: r2(x.iL * 100), iT: r2(x.iT * 100), total: Math.round(x.total) } : null;
    return {
      design: { H, iL: r2(iL * 100), iT: r2(iT * 100), mode: mode(), zLo: r2(zLo), zHi: r2(zHi), synth: r2(Math.hypot(iL, iT) * 100) },
      quantities: {
        cut: Math.round(r.cut), soil: Math.round(V.soil), clay: Math.round(V.clay), w4: Math.round(V.w4), w3: Math.round(V.w3), w2: Math.round(V.w2),
        fill: Math.round(V.fill), strip: Math.round(V.strip), spoil: Math.round(r.spoil), borrow: Math.round(r.borrow),
        inCut: Math.round(V.inCut), inFill: Math.round(V.inFill), outCut: Math.round(V.outCut), outFill: Math.round(V.outFill),
        wall: Math.round(V.wFV + V.wYV), wFL: Math.round(V.wFL), wFV: Math.round(V.wFV), wYL: Math.round(V.wYL), wYV: Math.round(V.wYV), wFH: r2(V.wFH), wYH: r2(V.wYH),
        slopeA: Math.round(V.slopeA), slopeL: Math.round(V.slopeL), raisedL: Math.round(V.raisedL),
      },
      cost: { total: Math.round(r.total), items: Object.fromEntries(Object.entries(r.c).map(([k, v]) => [k, Math.round(v)])) },
      prices: Object.fromEntries(Object.entries(P).map(([k, v]) => [k, v[2]])),
      rules: Object.fromEntries(Object.entries(RU).map(([k, v]) => [k, v[2]])),
      context: {
        hRange: [hMin, hMax], ground: [r2(pMin), r2(pMax)], platformArea: Math.round(engine.platformArea),
        bestAtSlope: brief(best), balanceAtSlope: brief(balance), optimum: stale ? null : brief(best3),
      },
      drawing: hasDrawing ? DRAWING.quantities : null,
    };
  }
  function applyScheme(s) {
    for (const [k, v] of Object.entries(s.prices || {})) if (P[k]) { P[k][2] = v; const i = $('prices').querySelector(`input[data-k="${k}"]`); if (i) i.value = v; }
    for (const [k, v] of Object.entries(s.rules || {})) if (RU[k]) { RU[k][2] = v; const i = $('rules').querySelector(`input[data-k="${k}"]`); if (i) i.value = v; }
    $('mode').value = s.design.mode === 'free' ? 'free' : 'rule'; markStale();
    setSlopes(s.design.iL / 100, s.design.iT / 100); setH(s.design.H);
  }
  async function saveScheme(name = '') { const res = await api('earthwork/scheme', { op: 'save', name, snapshot: snapshot() }); return res.name; }
  $('priceBtn').onclick = async () => { try { await api('earthwork/prices', { snapshot: snapshot() }); toast('已请模型建议单价，结果会出现在单价表上方。'); } catch (e) { toast(e.message); } };

  // ---------- 智能体动作执行：动作已由后端按白名单和取值范围校验
  async function execute(a) {
    if (a.type === 'set_design') {
      if (a.mode) { $('mode').value = a.mode; markStale(); }
      if (a.iL != null || a.iT != null || a.mode) setSlopes(a.iL != null ? a.iL / 100 : iL, a.iT != null ? a.iT / 100 : iT);
      setH(a.H != null ? a.H : a.dH != null ? H + a.dH : H);
      return `中心标高 ${H.toFixed(2)} m，造价 ${wan(last.total)} 万元`;
    }
    if (a.type === 'optimize') {
      if (a.target === 'full') {
        const o = await optimize(); if (!o) throw Error('坡度上下限之间没有可行解');
        return `中心标高 ${H.toFixed(2)} m，纵坡 ${pct(iL)}，横坡 ${pct(iT)}，造价 ${wan(last.total)} 万元；同规则下水平场平最优 ${o.flat.H.toFixed(2)} m、${wan(o.flat.total)} 万元`;
      }
      if (a.target === 'height') setH(best.H); else if (a.target === 'balance') setH(balance.H); else applyDrawing();
      return `中心标高 ${H.toFixed(2)} m，造价 ${wan(last.total)} 万元`;
    }
    if (a.type === 'set_price') { setParam($('prices'), P, a.key, a.value); return `造价 ${wan(last.total)} 万元`; }
    if (a.type === 'set_rule') { setParam($('rules'), RU, a.key, a.value); return `造价 ${wan(last.total)} 万元`; }
    if (a.type === 'save_scheme') return `已存为“${await saveScheme(a.name)}”`;
    if (a.type === 'set_view') { setView(a.view); return ''; }
    if (a.type === 'section') { $('secDir').value = a.dir; if (a.pos != null) $('secPos').value = a.pos; applySection(); return ''; }
    throw Error('未知动作');
  }
  async function runPending(pending) {
    if (busy || handledPending === pending.id) return;
    busy = true; handledPending = pending.id;
    const before = snapshot(), steps = [];
    try {
      for (const a of pending.actions) {
        try { steps.push({ label: a.label, ok: true, note: await execute(a) }); }
        catch (e) { steps.push({ label: a.label, ok: false, note: e.message }); }
      }
      await api('earthwork/result', { id: pending.id, steps, before, after: snapshot() });
    } catch (e) { toast(e.message); } finally { busy = false; }
  }

  function renderSuggestions() {
    const list = platformState?.earthwork?.suggestions || [], box = $('suggestions');
    box.innerHTML = list.length ? `<div class="notice warning ew-suggest"><b>模型建议单价（需确认后采纳）</b>${list.map(s => `<div><span>${esc(s.name)}：${esc(s.current ?? '—')} 改为 <b>${esc(s.value)}</b> ${esc(s.unit)}${s.category ? ' · ' + esc(s.category) : ''}<br><small>${esc(s.basis)}${s.source ? ' · 出处 ' + esc(s.source) : ' · 无知识库出处'}</small></span><span><button class="button" data-adopt="${esc(s.key)}">采纳</button> <button class="button" data-skip="${esc(s.key)}">忽略</button></span></div>`).join('')}</div>` : '';
    box.querySelectorAll('[data-adopt]').forEach(b => b.onclick = () => { const s = list.find(x => x.key === b.dataset.adopt); setParam($('prices'), P, s.key, s.value); api('earthwork/suggestions', { key: s.key }).catch(e => toast(e.message)); });
    box.querySelectorAll('[data-skip]').forEach(b => b.onclick = () => api('earthwork/suggestions', { key: b.dataset.skip }).catch(e => toast(e.message)));
  }

  const cleaner = createCleanPane(root.querySelector('[data-ew-pane="clean"]'), { ...host, openTab: t => showTab(t) });
  const modeller = createModelPane(root.querySelector('[data-ew-pane="build"]'), { ...host, engine, snapshot: () => snapshot() });

  // ---------- 页签
  let tab = 'model';
  function showTab(t) {
    tab = t;
    root.querySelectorAll('[data-ew-tab]').forEach(b => b.classList.toggle('selected', b.dataset.ewTab === t));
    root.querySelectorAll('[data-ew-pane]').forEach(p => p.hidden = p.dataset.ewPane !== t);
    scene.setActive(visible && t === 'model'); modeller.setActive(visible && t === 'build');
    if (t === 'model') drawChart();
  }
  root.querySelectorAll('[data-ew-tab]').forEach(b => b.onclick = () => showTab(b.dataset.ewTab));

  let lastSync = '';
  function sync(state) {
    platformState = state; const ew = state?.earthwork || {};
    cleaner.sync(state); modeller.sync(state);
    const built = ew.model?.folder;
    $('project').textContent = built ? built.split(/[\\/]/).filter(Boolean).pop() + ' · 由清洗成果建模' : '示例工程';
    const signature = JSON.stringify(ew.suggestions);
    if (signature !== lastSync) { lastSync = signature; renderSuggestions(); }
    if (ew.pending && !state.running) runPending(ew.pending);
  }
  function setVisible(on) { visible = on; scene.setActive(on && tab === 'model'); modeller.setActive(on && tab === 'build'); if (on) drawChart(); }

  // 模型一加载就按当前规则和单价寻优（全部计算在本机，约 1 秒），标高寻优页直接显示寻优结果
  rescan(); setH(best.H);
  $('opt3Note').textContent = '正在按新模型自动寻优…';
  optimize().then(o => { if (o) $('opt3Note').insertAdjacentHTML('afterbegin', '<b>已按当前模型自动寻优。</b>'); host.onChange?.(); });
  return { engine, snapshot, sync, setVisible, showTab, runPending, optimized: () => optimized, dispose: () => { scene.dispose(); modeller.dispose(); }, ready: true };
}

// ---------- 还没有三维模型：资料清洗、三维建模照常可用，标高寻优页提示先建模
function mountWithoutModel(root, host) {
  const { esc } = host;
  root.innerHTML = `<div class="tabs ew-tabs"><button class="selected" data-ew-tab="clean">资料清洗</button><button data-ew-tab="build">三维建模</button><button data-ew-tab="model">标高寻优</button></div>
<div class="ew-pane ew-scroll" data-ew-pane="clean"></div>
<div class="ew-pane" data-ew-pane="build" hidden></div>
<div class="ew-pane ew-scroll" data-ew-pane="model" hidden><div class="card empty ew-wait"><h3>还没有三维模型</h3><p>标高寻优要用由你的资料建立的三维地形和地层模型来计算挖填方量和造价。请先在“资料清洗”中处理资料，再到“三维建模”生成模型；模型生成后这里会自动寻优并显示结果。</p><div class="action-bar"><span></span><div><button class="button" data-go="clean">去资料清洗</button> <button class="primary" data-go="build">去三维建模</button></div></div></div></div>`;
  let tab = 'clean', visible = false;
  const cleaner = createCleanPane(root.querySelector('[data-ew-pane="clean"]'), { ...host, openTab: t => showTab(t) });
  const modeller = createModelPane(root.querySelector('[data-ew-pane="build"]'), { ...host, engine: null, snapshot: () => null });
  function showTab(t) {
    tab = t;
    root.querySelectorAll('[data-ew-tab]').forEach(b => b.classList.toggle('selected', b.dataset.ewTab === t));
    root.querySelectorAll('[data-ew-pane]').forEach(p => p.hidden = p.dataset.ewPane !== t);
    modeller.setActive(visible && t === 'build');
  }
  root.querySelectorAll('[data-ew-tab]').forEach(b => b.onclick = () => showTab(b.dataset.ewTab));
  root.querySelectorAll('[data-go]').forEach(b => b.onclick = () => showTab(b.dataset.go));
  return {
    engine: null, ready: true, snapshot: () => null, optimized: () => false, runPending: () => {}, showTab,
    sync(state) { cleaner.sync(state); modeller.sync(state); },
    setVisible(on) { visible = on; modeller.setActive(on && tab === 'build'); },
    dispose() { modeller.dispose(); },
  };
}
