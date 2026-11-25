// 土石方数智化算量 · 三维建模页：左侧是清洗后的 DXF 文件和图层，右侧是二维图形窗口和三维模型窗口，两者联动；
// 顶部是建模按钮和实际建模步骤的进度。二维、三维都用本地 three.js 绘制。
import * as THREE from 'three';
import { createScene } from './scene.js';
import { UNITS } from './engine.js';

const LAYER_COLOR = { '等高线-计曲线': '#e0a060', '等高线-首曲线': '#8a7358', '等高线-高程待定': '#ff5fb0', '高程点': '#5fd08a', '高程注记': '#5fd08a',
  '高程点-高程待定': '#ff5fb0', '征地线': '#ff5a5a', '场平范围': '#5aa8ff', '钻孔': '#39c5d8', '钻孔编号': '#39c5d8' };
const PALETTE = ['#8b96a8', '#a3adbd', '#7f8c8d', '#9aa7b5', '#84a59d', '#b08968', '#9d8189', '#6d9dc5', '#a3b18a', '#b5838d'];
const fmt = v => (v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
// 二维图层与三维图层的对应：左侧开关同时作用于两个窗口
const LINK_3D = [[/^等高线/, 'contours'], [/^征地线$/, 'land'], [/^场平范围$/, 'platform'], [/^钻孔$/, 'holes'], [/^钻孔编号$/, 'labels']];

// ---------- 二维图形窗口：正交相机俯视，线和点交给显卡绘制
function createMap(canvas, { onHover, onClick, onView }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.setClearColor(0x1c2026);
  const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -10, 10);
  let cx = 0, cy = 0, span = 100, w = 1, h = 1, origin = null, queued = false;
  const layers = new Map();                  // "文件|图层" → {object, data, dx, dy, on}
  // 三维注视点标记：十字和相机方向线
  const marker = new THREE.Group(); marker.visible = false; scene.add(marker);
  const markMat = new THREE.LineBasicMaterial({ color: 0xffe14d });
  const cross = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([-1, 0, 1, 1, 0, 1, 0, -1, 1, 0, 1, 1], 3)), markMat);
  const sight = new THREE.Line(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 3)), new THREE.LineBasicMaterial({ color: 0xffe14d, transparent: true, opacity: 0.55 }));
  marker.add(cross); scene.add(sight); sight.visible = false;

  function paint() { if (!queued) return; queued = false; cross.scale.setScalar(span / h * 10); renderer.render(scene, camera); }
  // 下一帧绘制；窗口在后台时动画帧不触发，用定时器兜底
  function request() { if (queued) return; queued = true; requestAnimationFrame(paint); setTimeout(paint, 80); }
  function apply() {
    const a = w / h; camera.left = cx - span * a / 2; camera.right = cx + span * a / 2; camera.top = cy + span / 2; camera.bottom = cy - span / 2;
    camera.updateProjectionMatrix(); request();
  }
  function resize() { const box = canvas.parentElement; if (!box.clientWidth || !box.clientHeight) return; w = box.clientWidth; h = box.clientHeight; renderer.setSize(w, h, false); apply(); }
  new ResizeObserver(resize).observe(canvas.parentElement);
  const toWorld = (px, py) => [cx + (px / w - 0.5) * span * w / h, cy + (0.5 - py / h) * span];

  function addDrawing(file, data) {
    if (!origin) origin = data.origin.slice();
    const dx = data.origin[0] - origin[0], dy = data.origin[1] - origin[1];
    data.layers.forEach((layer, index) => {
      const color = new THREE.Color(LAYER_COLOR[layer.name] || PALETTE[index % PALETTE.length]), group = new THREE.Group();
      let count = 0; for (const line of layer.lines) count += Math.max(0, line.p.length / 2 - 1 + (line.c ? 1 : 0));
      if (count) {
        const pos = new Float32Array(count * 6); let k = 0;
        for (const line of layer.lines) {
          const q = line.p, n = q.length;
          for (let i = 0; i + 3 < n; i += 2) { pos[k++] = q[i] + dx; pos[k++] = q[i + 1] + dy; pos[k++] = 0; pos[k++] = q[i + 2] + dx; pos[k++] = q[i + 3] + dy; pos[k++] = 0; }
          if (line.c && n >= 4) { pos[k++] = q[n - 2] + dx; pos[k++] = q[n - 1] + dy; pos[k++] = 0; pos[k++] = q[0] + dx; pos[k++] = q[1] + dy; pos[k++] = 0; }
        }
        group.add(new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(pos, 3)), new THREE.LineBasicMaterial({ color })));
      }
      if (layer.points.length) {
        const pos = new Float32Array(layer.points.length * 3);
        layer.points.forEach((p, i) => { pos[i * 3] = p[0] + dx; pos[i * 3 + 1] = p[1] + dy; pos[i * 3 + 2] = 0.5; });
        group.add(new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(pos, 3)), new THREE.PointsMaterial({ color, size: 4, sizeAttenuation: false })));
      }
      scene.add(group);
      layers.set(file + '|' + layer.name, { object: group, data: layer, dx, dy, on: true, file });
    });
    request();
  }
  function setLayer(key, on) { const l = layers.get(key); if (l) { l.on = on; l.object.visible = on; request(); } }
  function removeFile(file) { for (const [key, l] of layers) if (l.file === file) { scene.remove(l.object); l.object.traverse(o => o.geometry?.dispose()); layers.delete(key); } request(); }
  function fit() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const l of layers.values()) {
      if (!l.on) continue;
      l.object.traverse(o => { if (!o.geometry) return; o.geometry.computeBoundingBox(); const b = o.geometry.boundingBox; if (b.isEmpty()) return; x0 = Math.min(x0, b.min.x); y0 = Math.min(y0, b.min.y); x1 = Math.max(x1, b.max.x); y1 = Math.max(y1, b.max.y); });
    }
    if (x0 === Infinity) return;
    cx = (x0 + x1) / 2; cy = (y0 + y1) / 2; span = Math.max((y1 - y0), (x1 - x0) * h / w, 1) * 1.08; apply();
  }
  // 点击查询：离点击处最近的线或点（6 像素内）
  function pick(wx, wy) {
    const reach = span / h * 6; let best = null;
    for (const [key, l] of layers) {
      if (!l.on) continue;
      const x = wx - l.dx, y = wy - l.dy, name = key.split('|')[1];
      for (const p of l.data.points) { const d = Math.hypot(p[0] - x, p[1] - y); if (d < reach && (!best || d < best.d)) best = { d, text: `${name}${p[3] ? ' ' + p[3] : ''}${p[2] != null ? '　高程 ' + p[2] + ' m' : ''}` }; }
      for (const line of l.data.lines) {
        const q = line.p;
        for (let i = 0; i + 3 < q.length; i += 2) {
          const ax = q[i], ay = q[i + 1], vx = q[i + 2] - ax, vy = q[i + 3] - ay, len = vx * vx + vy * vy;
          const u = len ? Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / len)) : 0, d = Math.hypot(ax + u * vx - x, ay + u * vy - y);
          if (d < reach && (!best || d < best.d)) best = { d, text: `${name}${line.z != null ? '　高程 ' + line.z + ' m' : ''}` };
        }
      }
    }
    return best?.text || '';
  }
  canvas.addEventListener('wheel', e => {
    e.preventDefault(); const r = canvas.getBoundingClientRect(), [wx, wy] = toWorld(e.clientX - r.left, e.clientY - r.top), f = Math.exp(e.deltaY * 0.0015);
    const next = Math.min(Math.max(span * f, 0.5), 1e5); cx = wx + (cx - wx) * next / span; cy = wy + (cy - wy) * next / span; span = next; apply(); viewChanged();
  }, { passive: false });
  let drag = null, viewTimer = 0;
  function viewChanged() { clearTimeout(viewTimer); viewTimer = setTimeout(() => origin && onView?.(cx + origin[0], cy + origin[1], span), 120); }
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, cx, cy, moved: false }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => {
    const r = canvas.getBoundingClientRect();
    if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; cx = drag.cx - dx * span / h; cy = drag.cy + dy * span / h; apply(); return; }
    if (origin) { const [wx, wy] = toWorld(e.clientX - r.left, e.clientY - r.top); onHover?.(wx + origin[0], wy + origin[1]); }
  });
  canvas.addEventListener('pointerup', e => {
    const was = drag; drag = null; if (!was || !origin) return;
    if (was.moved) return viewChanged();
    const r = canvas.getBoundingClientRect(), [wx, wy] = toWorld(e.clientX - r.left, e.clientY - r.top);
    onClick?.(wx + origin[0], wy + origin[1], pick(wx, wy));
  });
  return {
    addDrawing, removeFile, setLayer, fit, resize,
    hasData: () => layers.size > 0,
    // 三维窗口的注视点和相机位置（工程坐标）
    setMarker(E, N, camE, camN) {
      if (!origin) return; const x = E - origin[0], y = N - origin[1];
      marker.visible = sight.visible = true; marker.position.set(x, y, 0);
      const p = sight.geometry.attributes.position; p.setXYZ(0, camE - origin[0], camN - origin[1], 1); p.setXYZ(1, x, y, 1); p.needsUpdate = true; request();
    },
    hideMarker() { marker.visible = sight.visible = false; request(); },
    dispose() { renderer.dispose(); },
  };
}

// ---------- 三维建模页
export function createModelPane(pane, host) {
  const { api, toast, esc, engine, snapshot } = host;
  pane.innerHTML = `<div class="ew-cad">
    <div class="ew-cad-bar">
     <button class="primary" data-m="build">生成三维模型</button>
     <div class="ew-cad-steps" data-m="steps"></div>
     <div class="ew-cad-modes"><button data-mode="2d">二维</button><button data-mode="link">联动</button><button data-mode="3d">三维</button></div>
     <button class="button" data-m="fit">全图</button><button class="button" data-m="qc">模型质检</button>
    </div>
    <div class="ew-cad-body">
     <aside class="ew-cad-tree"><h5>清洗成果</h5><div data-m="files"></div><h5>三维图层</h5><div data-m="layers3d"></div></aside>
     <div class="ew-cad-views" data-m="views">
      <div class="ew-cad-view" data-m="box2d"><span class="ew-cad-tag">二维</span><canvas data-m="c2d"></canvas><div class="ew-cad-empty" data-m="empty2d"></div></div>
      <div class="ew-cad-view" data-m="box3d"><span class="ew-cad-tag">三维</span><canvas data-m="c3d"></canvas><div class="ew-cad-empty" data-m="empty3d"></div>
       <div class="ew-cad-3dtools"><button data-v="iso">鸟瞰</button><button data-v="top">俯视</button><button data-v="under">地下</button></div></div>
     </div>
    </div>
    <div class="ew-cad-status"><span data-m="coord">E —　N —</span><span data-m="pickInfo"></span><span data-m="modelInfo"></span></div>
    <div class="ew-cad-qc" data-m="qcPanel" hidden></div></div>`;
  const $ = k => pane.querySelector(`[data-m="${k}"]`);
  let state = null, mode = 'link', shown = '', active = false, scene3 = null, files = [], loaded = new Set(), opened = new Set(), reportTime = '', quality = null, lastSteps = '';
  const has3d = () => Boolean(state?.earthwork?.model && engine);

  const map = createMap($('c2d'), {
    onHover: (E, N) => { $('coord').textContent = `E ${E.toFixed(2)}　N ${N.toFixed(2)}`; },
    onClick: (E, N, text) => { $('pickInfo').textContent = text; if (scene3 && mode !== '2d') scene3.focus(E, N); },
    onView: (E, N, span) => { if (scene3 && mode === 'link') scene3.focus(E, N, span * 1.3); },
  });

  // ---------- 三维窗口：当前模型的地表、地层、钻孔
  const LAYERS_3D = [['ground', '现状地表'], ['contours', '等高线'], ['land', '征地线'], ['platform', '场平范围'], ['holes', '钻孔柱'], ['labels', '钻孔编号'],
    ...UNITS.filter(u => u.key !== 'fillU').map(u => [u.key, u.name])];
  function ensure3d() {
    if (scene3 || !has3d()) return;
    scene3 = createScene(engine, $('c3d'), { background: '#1c2026', onMove: m => { if (mode !== '2d') map.setMarker(m.E, m.N, m.camE, m.camN); } });
    scene3.setVisible('design', false); scene3.setVisible('walls', false); scene3.setVisible('fillU', false);
    scene3.platformOnGround(); scene3.setGroundOpacity(0.75);
    $('layers3d').innerHTML = LAYERS_3D.map(([k, name]) => `<label><input type="checkbox" checked data-l3="${k}">${esc(name)}</label>`).join('')
      + `<label class="ew-cad-slider">地表透明度<input type="range" min="0" max="1" step="0.05" value="0.75" data-m="op"></label><label class="ew-cad-slider">竖向夸张<input type="range" min="1" max="4" step="0.25" value="1.5" data-m="vex"></label>`;
    $('layers3d').querySelectorAll('[data-l3]').forEach(c => c.onchange = () => set3d(c.dataset.l3, c.checked));
    $('op').oninput = e => scene3.setGroundOpacity(+e.target.value);
    $('vex').oninput = e => scene3.setExaggeration(+e.target.value);
  }
  // 场平范围线在三维场景里没有单独的开关键：借用设计面的线对象
  function set3d(key, on) { if (!scene3) return; if (key === 'platform') scene3.setPlatformVisible?.(on); else scene3.setVisible(key, on); }
  pane.querySelectorAll('[data-v]').forEach(b => b.onclick = () => scene3?.setView(b.dataset.v));

  function layout() {
    const three = has3d(), m = three ? mode : '2d';
    pane.querySelectorAll('[data-mode]').forEach(b => { b.classList.toggle('on', b.dataset.mode === m); b.disabled = !three && b.dataset.mode !== '2d'; });
    $('views').dataset.mode = m;
    $('qc').disabled = !three; $('qc').title = three ? '' : '生成三维模型后可做模型质检';
    $('empty3d').textContent = three ? '' : '生成三维模型后在这里显示';
    $('empty3d').hidden = three;
    if (three) ensure3d();
    scene3?.setActive(active && m !== '2d');
    if (m === '2d') map.hideMarker();
    // 窗口布局变了（二维 / 联动 / 三维切换，或页签刚显示）：重新铺满二维图形
    const key = m + '|' + active;
    const settle = () => { map.resize(); scene3?.resize(); if (key !== shown && active && map.hasData()) { shown = key; map.fit(); } };
    settle(); requestAnimationFrame(settle);      // 立即算一次（窗口在后台时动画帧不触发），下一帧布局稳定后再算一次
  }
  pane.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => { mode = b.dataset.mode; layout(); });
  $('fit').onclick = () => { map.fit(); scene3?.setView('iso'); };

  // ---------- 左侧：清洗成果里的 DXF 文件和图层
  async function loadFile(name) {
    if (loaded.has(name)) return;
    loaded.add(name);
    try {
      const data = await api('earthwork/clean/view', { file: name, output: true });
      const first = !map.hasData();
      map.addDrawing(name, data);
      files = files.map(f => f.name === name ? { ...f, layers: data.layers.map(l => ({ name: l.name, count: l.lines.length + l.points.length, on: true })), simplified: data.simplified } : f);
      if (first) { map.resize(); map.fit(); }
      $('empty2d').hidden = true;
    } catch (e) { loaded.delete(name); toast(e.message); }
    renderFiles();
  }
  function renderFiles() {
    const box = $('files');
    if (!files.length) { box.innerHTML = '<p class="ew-cad-hint">还没有清洗成果。请先在“资料清洗”页签处理资料。</p>'; $('empty2d').hidden = false; $('empty2d').textContent = '清洗后的 DXF 在这里显示'; return; }
    box.innerHTML = files.map(f => `<div class="ew-cad-file"><label><input type="checkbox" data-file="${esc(f.name)}" ${f.on ? 'checked' : ''}><b>${esc(f.name)}</b></label>
      ${f.layers ? `<button class="ew-cad-fold" data-fold="${esc(f.name)}">${opened.has(f.name) ? '收起' : `${f.layers.length} 个图层`}</button>` : f.on ? '<small>读取中…</small>' : ''}
      ${f.layers && opened.has(f.name) ? `<div class="ew-cad-layers">${f.layers.map(l => `<label title="${esc(l.name)}"><input type="checkbox" data-layer="${esc(f.name + '|' + l.name)}" ${l.on ? 'checked' : ''}><i style="background:${LAYER_COLOR[l.name] || '#8b96a8'}"></i>${esc(l.name)}<small>${l.count}</small></label>`).join('')}</div>` : ''}</div>`).join('');
    box.querySelectorAll('[data-file]').forEach(c => c.onchange = () => {
      const f = files.find(x => x.name === c.dataset.file); f.on = c.checked;
      if (c.checked) { if (f.layers) f.layers.forEach(l => map.setLayer(f.name + '|' + l.name, l.on)); else loadFile(f.name); }
      else (f.layers || []).forEach(l => map.setLayer(f.name + '|' + l.name, false));
      renderFiles();
    });
    box.querySelectorAll('[data-fold]').forEach(b => b.onclick = () => { opened.has(b.dataset.fold) ? opened.delete(b.dataset.fold) : opened.add(b.dataset.fold); renderFiles(); });
    box.querySelectorAll('[data-layer]').forEach(c => c.onchange = () => {
      const [file, layer] = c.dataset.layer.split('|'), f = files.find(x => x.name === file), l = f.layers.find(x => x.name === layer);
      l.on = c.checked; map.setLayer(c.dataset.layer, c.checked && f.on);
      // 同名要素在三维窗口里一并开关
      const link = LINK_3D.find(([re]) => re.test(layer));
      if (link && scene3) { const any = f.layers.some(x => link[0].test(x.name) && x.on); set3d(link[1], any); const box3 = pane.querySelector(`[data-l3="${link[1]}"]`); if (box3) box3.checked = any; }
    });
  }

  // ---------- 顶部：建模按钮与步骤进度
  function renderSteps() {
    const ew = state?.earthwork || {}, b = ew.building, check = ew.cleaning?.report?.checklist, model = ew.model, running = Boolean(b && !b.done && !b.failed && state.running);
    const key = JSON.stringify([b, check?.ready, model?.stats?.time, running]);
    if (key === lastSteps) return; lastSteps = key;
    const btn = $('build');
    btn.textContent = running ? '正在建模…' : model ? '重新生成三维模型' : '生成三维模型';
    btn.disabled = running || !check?.ready;
    btn.title = check?.ready ? '由清洗成果生成地形格网和地层界面，约 10 秒' : check ? '建模必需的数据还缺：' + check.missing.join('、') : '请先在“资料清洗”页签处理资料';
    const names = b?.steps || ['读取清洗成果', '构建地形三角网', '重采样格网地形', '整理钻孔分层', '插值地层界面', '场平范围与校核', '写出模型'];
    const current = b ? b.step : model ? names.length : -1, done = b ? b.done : Boolean(model), percent = Math.round(Math.max(0, Math.min(current + (running ? 0.5 : 0), names.length)) / names.length * 100);
    $('steps').innerHTML = `<div class="ew-cad-progress"><i style="width:${done ? 100 : percent}%" class="${b?.failed ? 'bad' : ''}"></i></div>
      <ol>${names.map((n, i) => `<li class="${done || i < current ? 'done' : i === current && running ? 'now' : i === current && b?.failed ? 'bad' : ''}"><em>${done || i < current ? '✓' : i + 1}</em>${esc(n)}</li>`).join('')}</ol>
      <span class="ew-cad-pct">${b?.failed ? '建模未完成' : done ? '已完成' : running ? percent + '%' : check?.ready ? '待建模' : '待清洗'}</span>`;
    const st = model?.stats;
    $('modelInfo').textContent = st ? `模型 ${st.grid.replace('×', ' x ')} 格点 · 间距 ${st.dx} m · 地面 ${st.dem_range[0]}～${st.dem_range[1]} m · ${st.holes} 个钻孔 · ${st.time}` : '尚未建模（“标高寻优”使用示例工程模型）';
  }
  $('build').onclick = async () => { try { await api('earthwork/model/build', { folder: state?.earthwork?.cleaning?.folder || '' }); } catch (e) { toast(e.message); } };

  // ---------- 模型质检（规则计算），可请模型解读
  $('qc').onclick = async () => {
    const panel = $('qcPanel');
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false; panel.innerHTML = '<p class="ew-cad-hint">正在读取…</p>';
    try { quality = quality || (await api('earthwork/sources')).quality; } catch (e) { panel.innerHTML = `<p class="ew-cad-hint">${esc(e.message)}</p>`; return; }
    renderQc();
  };
  function renderQc() {
    const panel = $('qcPanel'); if (panel.hidden || !quality) return;
    const levels = { ok: ['good', '正常'], info: ['', '提示'], warn: ['warn', '需关注'] }, st = state?.earthwork?.model?.stats;
    panel.innerHTML = `<div class="ew-cad-qc-head"><b>模型质检</b><span>${has3d() ? '当前模型' : '示例工程模型'} · 本机规则计算</span><button data-close>关闭</button></div>
      ${quality.findings.map(f => `<p><span class="badge ${levels[f.level][0]}">${levels[f.level][1]}</span> <b>${esc(f.title)}</b>　${esc(f.text)}</p>`).join('')}
      ${(st?.warnings || []).map(w => `<p><span class="badge warn">建模提示</span> ${esc(w)}</p>`).join('')}
      <div class="ew-cad-qc-foot"><small>模型解读只收到上面的统计量，不含坐标。</small><button class="button" data-ai>智能质检解读</button></div>
      ${state?.earthwork?.qc_note ? `<div class="ew-cad-note">${esc(state.earthwork.qc_note)}</div>` : ''}`;
    panel.querySelector('[data-close]').onclick = () => { panel.hidden = true; };
    panel.querySelector('[data-ai]').onclick = () => api('earthwork/qc', { snapshot: snapshot() }).catch(e => toast(e.message));
  }

  function sync(next) {
    state = next;
    const report = next?.earthwork?.cleaning?.report, time = report?.time || '';
    if (time !== reportTime) {
      // 清洗成果变了：重新列文件，已加载的图形作废
      reportTime = time;
      for (const name of loaded) map.removeFile(name);
      loaded = new Set(); quality = null;
      files = (report?.outputs || []).filter(n => n.toLowerCase().endsWith('.dxf')).map((name, i) => ({ name, on: i === 0, layers: null }));
      renderFiles();
      if (active) files.filter(f => f.on).forEach(f => loadFile(f.name));
    }
    renderSteps(); renderQc(); layout();
  }
  return {
    sync,
    setActive(on) {
      active = on;
      if (on) { files.filter(f => f.on).forEach(f => loadFile(f.name)); layout(); } else scene3?.setActive(false);
    },
    dispose() { scene3?.dispose(); map.dispose(); },
  };
}
