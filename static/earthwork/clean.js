// 土石方数智化算量 · 资料清洗页：逐项添加建模要用的三份资料、预览其中的 PDF、执行清洗（本机规则）；清洗完成后转到“三维建模”。
const KIND_LABEL = { layout_pdf: '布置图 PDF', survey_pdf: '勘察报告 PDF', base_dxf: '基础数据 DXF', plan_dxf: '勘探点平面图 DXF', holes_csv: '钻孔分层 CSV',
  other_pdf: '其他 PDF', other_dxf: '其他 DXF', other_csv: '其他 CSV', dwg: 'DWG（需另存为 DXF）', unreadable: '无法读取' };
const ROLE_LABEL = { contour: '等高线', point: '高程点', land: '征地线', platform: '场平范围' };
const LAYER_COLOR = { '等高线-计曲线': '#a0522d', '等高线-首曲线': '#c9a27e', '等高线-高程待定': '#d63384', '高程点': '#1a8a3a', '高程注记': '#1a8a3a',
  '高程点-高程待定': '#d63384', '征地线': '#d0202a', '场平范围': '#1f5fae', '钻孔': '#0b7285' };
const PALETTE = ['#5c677d', '#7d8597', '#6c757d', '#8d99ae', '#84a59d', '#b08968', '#9d8189', '#6d9dc5', '#a3b18a', '#b5838d'];
const fmt = v => (v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });

// ---------- 二维图形查看器：滚轮缩放、拖动平移、图层开关、点击查询
export function createViewer(box, esc) {
  box.innerHTML = `<div class="ew-v2"><canvas></canvas><div class="ew-v2-tip"></div><div class="ew-v2-tools"><button class="button" data-fit>全图</button><button class="button" data-layers>图层</button></div><div class="ew-v2-layers" hidden></div></div>`;
  const canvas = box.querySelector('canvas'), tip = box.querySelector('.ew-v2-tip'), panel = box.querySelector('.ew-v2-layers');
  const ctx = canvas.getContext('2d');
  let data = null, layers = [], scale = 1, tx = 0, ty = 0, image = null, pending = false;
  const dpr = () => devicePixelRatio || 1;
  function resize() { const r = canvas.getBoundingClientRect(); canvas.width = Math.max(1, r.width * dpr()); canvas.height = Math.max(1, r.height * dpr()); draw(); }
  function extent() { return image ? [image.naturalWidth, image.naturalHeight] : data ? data.size : [1, 1]; }
  function fit() {
    const r = canvas.getBoundingClientRect(), [w, h] = extent();
    scale = Math.min(r.width / (w || 1), r.height / (h || 1)) * 0.95;
    tx = (r.width - w * scale) / 2; ty = (r.height - h * scale) / 2; draw();
  }
  function draw() { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; paint(); }); }
  function paint() {
    const r = canvas.getBoundingClientRect(), k = dpr();
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (image) { ctx.setTransform(scale * k, 0, 0, scale * k, tx * k, ty * k); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(image, 0, 0); return; }
    if (!data) return;
    const H = data.size[1];
    // 工程坐标 y 向上：画布上翻转
    ctx.setTransform(scale * k, 0, 0, -scale * k, tx * k, (ty + H * scale) * k);
    for (const layer of layers) {
      if (!layer.on) continue;
      ctx.strokeStyle = ctx.fillStyle = layer.color;
      ctx.lineWidth = (layer.bold ? 1.6 : 0.9) / scale;
      if (layer.path) ctx.stroke(layer.path);
      const size = 2.2 / scale;
      if (layer.points.length) { ctx.beginPath(); for (const p of layer.points) { ctx.moveTo(p[0] + size, p[1]); ctx.arc(p[0], p[1], size, 0, 6.2832); } ctx.fill(); }
    }
    // 文字：放大到够看清时才画，只画视野内的
    ctx.setTransform(k, 0, 0, k, 0, 0); ctx.textBaseline = 'alphabetic';
    for (const layer of layers) {
      if (!layer.on || !layer.texts.length) continue;
      ctx.fillStyle = layer.color;
      let shown = 0;
      for (const t of layer.texts) {
        const px = Math.max(t[3], 0.6) * scale; if (px < 6) continue;
        const x = tx + t[0] * scale, y = ty + (H - t[1]) * scale;
        if (x < -200 || y < -50 || x > r.width + 50 || y > r.height + 50) continue;
        ctx.font = `${Math.min(px, 40)}px "Microsoft YaHei",sans-serif`;
        if (t[4]) { ctx.save(); ctx.translate(x, y); ctx.rotate(-t[4]); ctx.fillText(t[2], 0, 0); ctx.restore(); } else ctx.fillText(t[2], x, y);
        if (++shown > 3000) break;
      }
      if (layer.labels) for (const p of layer.points) {
        if (!p[3] || scale < 1.2) break;
        const x = tx + p[0] * scale, y = ty + (H - p[1]) * scale;
        if (x < -100 || y < -20 || x > r.width + 20 || y > r.height + 20) continue;
        ctx.font = '11px "Microsoft YaHei",sans-serif'; ctx.fillText(p[3], x + 4, y - 4);
      }
    }
  }
  function renderLayers() {
    panel.innerHTML = `<label><input type="checkbox" data-all checked> 全部图层</label>` + layers.map((l, i) => `<label><input type="checkbox" data-i="${i}" ${l.on ? 'checked' : ''}><span class="ew-sw" style="background:${l.color}"></span>${esc(l.name)} <small>${l.count}</small></label>`).join('');
  }
  panel.addEventListener('change', e => {
    if (e.target.dataset.all !== undefined) { layers.forEach(l => l.on = e.target.checked); renderLayers(); panel.querySelector('[data-all]').checked = e.target.checked; }
    else layers[+e.target.dataset.i].on = e.target.checked;
    draw();
  });
  box.querySelector('[data-fit]').onclick = fit;
  box.querySelector('[data-layers]').onclick = () => { panel.hidden = !panel.hidden; };
  canvas.addEventListener('wheel', e => {
    e.preventDefault(); const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, f = Math.exp(-e.deltaY * 0.0015);
    const next = Math.min(Math.max(scale * f, 1e-4), 1e4); tx = x - (x - tx) * next / scale; ty = y - (y - ty) * next / scale; scale = next; draw();
  }, { passive: false });
  let drag = null;
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, tx, ty, moved: false }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => {
    if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; tx = drag.tx + dx; ty = drag.ty + dy; draw(); return; }
    if (!data || image) return;
    const r = canvas.getBoundingClientRect(), wx = (e.clientX - r.left - tx) / scale, wy = data.size[1] - (e.clientY - r.top - ty) / scale;
    tip.textContent = `E ${(data.origin[0] + wx).toFixed(2)}　N ${(data.origin[1] + wy).toFixed(2)}`;
  });
  canvas.addEventListener('pointerup', e => {
    const was = drag; drag = null; if (!was || was.moved || !data || image) return;
    // 点击查询：离点击处最近的线或点（6 像素内）
    const r = canvas.getBoundingClientRect(), wx = (e.clientX - r.left - tx) / scale, wy = data.size[1] - (e.clientY - r.top - ty) / scale, reach = 6 / scale;
    let best = null;
    for (const layer of layers) {
      if (!layer.on) continue;
      for (const p of layer.points) { const d = Math.hypot(p[0] - wx, p[1] - wy); if (d < reach && (!best || d < best.d)) best = { d, text: `${layer.name}${p[3] ? ' ' + p[3] : ''}${p[2] != null ? '　高程 ' + p[2] : ''}` }; }
      for (const line of layer.lines) {
        const q = line.p;
        for (let i = 0; i + 3 < q.length; i += 2) {
          const ax = q[i], ay = q[i + 1], bx = q[i + 2], by = q[i + 3], vx = bx - ax, vy = by - ay, len = vx * vx + vy * vy;
          const u = len ? Math.max(0, Math.min(1, ((wx - ax) * vx + (wy - ay) * vy) / len)) : 0, d = Math.hypot(ax + u * vx - wx, ay + u * vy - wy);
          if (d < reach && (!best || d < best.d)) best = { d, text: `${layer.name}${line.z != null ? '　高程 ' + line.z + ' m' : ''}` };
        }
      }
    }
    tip.textContent = best ? best.text : tip.textContent;
  });
  new ResizeObserver(resize).observe(canvas);
  return {
    showDrawing(d) {
      image = null; data = d;
      layers = d.layers.map((l, i) => {
        const path = new Path2D();
        for (const line of l.lines) { const q = line.p; path.moveTo(q[0], q[1]); for (let k = 2; k < q.length; k += 2) path.lineTo(q[k], q[k + 1]); if (line.c) path.closePath(); }
        return { name: l.name, color: LAYER_COLOR[l.name] || PALETTE[i % PALETTE.length], bold: /计曲线|征地线|场平范围/.test(l.name), path: l.lines.length ? path : null,
          lines: l.lines, points: l.points, texts: l.texts, labels: l.name === '钻孔', on: true, count: l.lines.length + l.points.length + l.texts.length };
      });
      renderLayers(); box.querySelector('[data-layers]').hidden = false; tip.textContent = d.simplified ? '图形较大，预览已抽稀' : ''; resize(); fit();
    },
    // keep：换成同一页的高清图时保持当前缩放和位置
    showImage(url, keep = false) {
      data = null; layers = []; panel.hidden = true; box.querySelector('[data-layers]').hidden = true;
      const img = new Image();
      img.onload = () => { const old = image; image = img; if (keep && old) { scale *= old.naturalWidth / img.naturalWidth; resize(); } else { resize(); fit(); } };
      img.onerror = () => { tip.textContent = '页面图像加载失败'; }; img.src = url;
    },
    status(text) { tip.textContent = text; },
    clear(text = '') { data = null; image = null; layers = []; tip.textContent = text; draw(); },
  };
}

// ---------- 资料清洗页：左侧是建立三维模型要用的三项资料，逐项添加文件；右侧预览其中的 PDF；右下角开始清洗
const SOURCES = [
  { key: 'terrain', name: '地形与场地图', formats: 'DXF 或 CAD 导出的 PDF', hint: '含等高线、高程点、征地线和场平范围的图纸，如总平面图、护坡挡墙布置图' },
  { key: 'holes', name: '钻孔资料', formats: '钻孔分层 CSV 或勘察报告 PDF', hint: '岩土工程勘察报告（含钻孔柱状图），或已整理好的钻孔分层数据表' },
  { key: 'plan', name: '勘探点平面位置图', formats: 'DXF 或勘察报告 PDF', hint: '地形与场地图是 PDF 时必需，用图上的钻孔坐标取得工程坐标；是 DXF 时可不添加' },
];
const ext = name => (name || '').split('.').pop().toLowerCase();
const base = path => (path || '').split(/[\\/]/).pop();

export function createCleanPane(pane, host) {
  const { api, toast, esc } = host;
  let picks = { terrain: '', holes: '', plan: '' }, prefix = '', state = null, restored = false, awaiting = false, seenReport = '', previewing = '';
  pane.innerHTML = `<div class="ew-prep">
    <div class="ew-prep-left"><h3>建立三维模型需要的资料</h3><p class="ew-prep-sub">在本机按规则处理，不调用在线模型；不改动原文件。</p><div data-c="items"></div><div data-c="notes"></div></div>
    <div class="ew-prep-right"><div class="ew-prep-head"><b>PDF 预览</b><select data-c="pdfSel" hidden></select><span class="ew-prep-pager" data-c="pager" hidden><button type="button" data-c="prev" title="上一页" aria-label="上一页">‹</button><select data-c="pageSel" aria-label="页码"></select><button type="button" data-c="next" title="下一页" aria-label="下一页">›</button></span><span>滚轮缩放 · 拖动平移</span></div><div class="ew-prep-view" data-c="view"></div></div>
    <div class="ew-prep-foot"><small data-c="footNote"></small><button class="primary" data-c="run">开始清洗</button></div></div>`;
  const $ = k => pane.querySelector(`[data-c="${k}"]`);
  const viewer = createViewer($('view'), esc);

  const problems = () => {
    const out = [];
    if (!picks.terrain) out.push('还没有添加地形与场地图');
    if (!picks.holes) out.push('还没有添加钻孔资料');
    if (ext(picks.terrain) === 'pdf' && ext(picks.plan) !== 'pdf') out.push('地形与场地图是 PDF 时，勘探点平面位置图须添加勘察报告 PDF（靠图上的钻孔坐标取得工程坐标）');
    return out;
  };
  function render() {
    const planRequired = ext(picks.terrain) === 'pdf', running = Boolean(state?.running);
    $('items').innerHTML = SOURCES.map((s, i) => {
      const file = picks[s.key], optional = s.key === 'plan' && !planRequired;
      const badge = !file ? (optional ? '<span class="tag">可不添加</span>' : '<span class="badge bad">未添加</span>')
        : ext(file) === 'pdf' ? '<span class="badge warn">PDF · 需转换</span>' : `<span class="badge good">${ext(file).toUpperCase()} · 可直接使用</span>`;
      return `<div class="ew-prep-item ${file ? 'has' : ''}"><em>${i + 1}</em><div class="ew-prep-body"><div class="ew-prep-title"><b>${s.name}</b>${badge}</div>
        <small>${esc(s.hint)}</small>
        <div class="ew-prep-file" title="${esc(file)}">${file ? esc(base(file)) : `<span>可用格式：${s.formats}</span>`}</div></div>
        <div class="ew-prep-acts"><button class="ew-prep-add" data-add="${s.key}" title="${file ? '更换文件' : '添加文件'}" aria-label="${file ? '更换' : '添加'}${s.name}">＋</button>
         ${file ? `<button class="ew-prep-del" data-del="${s.key}" title="移除" aria-label="移除${s.name}">×</button>` : ''}</div></div>`;
    }).join('') + (ext(picks.holes) === 'pdf' ? `<label class="ew-prep-prefix">东坐标带号<input data-c="prefix" inputmode="numeric" maxlength="3" value="${esc(prefix)}" placeholder="如 34"><small>钻孔柱状图表头的坐标不带带号、设计图带带号时填写</small></label>` : '');
    const list = problems();
    $('notes').innerHTML = list.filter((_, i) => i >= (picks.terrain || picks.holes ? 0 : 9)).map(x => `<div class="notice warning">${esc(x)}</div>`).join('');
    const anyPdf = SOURCES.some(s => ext(picks[s.key]) === 'pdf');
    $('footNote').textContent = running ? '正在处理，进度见左侧对话中的执行过程…' : anyPdf ? 'PDF 需在本机提取图形和文字、配准坐标，约 1 分钟。完成后转到“三维建模”。' : '全部是 DXF / CSV 时不需要转换，只做校验整理，几秒完成。完成后转到“三维建模”。';
    const run = $('run'); run.disabled = running || list.length > 0; run.textContent = running && awaiting ? '正在清洗…' : '开始清洗';
    pane.querySelectorAll('[data-add]').forEach(b => b.onclick = () => add(b.dataset.add));
    pane.querySelectorAll('[data-del]').forEach(b => b.onclick = () => { picks[b.dataset.del] = ''; render(); renderPreview(); });
    const input = $('prefix'); if (input) input.oninput = () => { prefix = input.value.trim(); };
  }
  // 右侧只预览 PDF：三项资料里是 PDF 的列出来；多页的可翻页，勘察报告默认看勘探点平面位置图页。
  // 页面在后台进程里渲染并缓存：先出低分辨率图，再换高清图；图形很多的页首次要 1～2 分钟。
  let pdfInfo = null, pageNo = 0, token = 0;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function fetchReady(url, my, waiting) {
    for (;;) {
      if (my !== token) return null;
      const r = await fetch(url);
      if (r.status === 202) { const j = await r.json().catch(() => ({})); waiting?.(j.seconds || 0); await sleep(1200); continue; }
      if (r.status === 401) { location.href = '/login'; return null; }
      if (!r.ok) throw Error((await r.json().catch(() => ({}))).error || '页面渲染失败');
      return r.headers.get('Content-Type')?.startsWith('image/') ? URL.createObjectURL(await r.blob()) : r.json();
    }
  }
  const pageLabel = n => !pdfInfo ? '' : pdfInfo.plan.includes(n) ? '勘探点平面位置图' : pdfInfo.columns.includes(n) ? '钻孔柱状图' : pdfInfo.sections?.includes(n) ? '工程地质剖面图' : '';
  function renderPager() {
    const pager = $('pager'), many = pdfInfo && pdfInfo.pages > 1;
    pager.hidden = !many;
    if (!many) return;
    $('pageSel').innerHTML = Array.from({ length: pdfInfo.pages }, (_, i) => i + 1).map(n => `<option value="${n}" ${n === pageNo ? 'selected' : ''}>第 ${n} / ${pdfInfo.pages} 页${pageLabel(n) ? ' · ' + pageLabel(n) : ''}</option>`).join('');
    $('prev').disabled = pageNo <= 1; $('next').disabled = pageNo >= pdfInfo.pages;
  }
  async function showPage(n) {
    const my = ++token, file = previewing;
    pageNo = n; renderPager();
    const url = `/api/earthwork/clean/page?file=${encodeURIComponent(file)}&page=${n}`;
    viewer.clear(`正在渲染第 ${n} 页…`);
    try {
      const low = await fetchReady(url + '&side=900', my, s => viewer.status(`正在渲染第 ${n} 页…${s >= 5 ? `已用 ${s} 秒（这一页图形很多）` : ''}`));
      if (!low) return;
      viewer.showImage(low); viewer.status('已显示预览图，正在渲染高清图…');
      const high = await fetchReady(url + '&side=2200', my, s => viewer.status(`已显示预览图，正在渲染高清图…${s >= 5 ? `已用 ${s} 秒，图形很多的页首次约需 1～2 分钟，之后直接取缓存` : ''}`));
      if (!high) return;
      viewer.showImage(high, true); viewer.status('');
    } catch (e) { if (my === token) viewer.clear(e.message); }
  }
  async function openPdf(file) {
    const my = ++token;
    previewing = file; pdfInfo = null; pageNo = 0; renderPager();
    if (!file) return;
    viewer.clear('正在读取 PDF 页面信息…');
    try {
      const info = await fetchReady(`/api/earthwork/clean/pdfinfo?file=${encodeURIComponent(file)}`, my);
      if (!info || my !== token) return;
      pdfInfo = info;
      showPage(info.default || 1);
    } catch (e) { if (my === token) viewer.clear(e.message); }
  }
  function renderPreview() {
    const pdfs = [...new Set(SOURCES.map(s => picks[s.key]).filter(f => ext(f) === 'pdf'))], sel = $('pdfSel');
    sel.hidden = pdfs.length < 2;
    const next = pdfs.includes(previewing) ? previewing : pdfs[0] || '';
    sel.innerHTML = pdfs.map(f => `<option value="${esc(f)}" ${f === next ? 'selected' : ''}>${esc(base(f))}</option>`).join('');
    if (next === previewing && next) return;
    if (!next) { ++token; previewing = ''; pdfInfo = null; renderPager(); return viewer.clear(SOURCES.some(s => picks[s.key]) ? '所添加的资料里没有 PDF；DXF 在“三维建模”页签查看' : '添加 PDF 资料后在这里预览'); }
    openPdf(next);
  }
  $('pdfSel').onchange = () => openPdf($('pdfSel').value);
  $('pageSel').onchange = () => showPage(+$('pageSel').value);
  $('prev').onclick = () => pageNo > 1 && showPage(pageNo - 1);
  $('next').onclick = () => pdfInfo && pageNo < pdfInfo.pages && showPage(pageNo + 1);

  async function add(slot) {
    let path = '';
    try { path = (await api('earthwork/clean/pick', { slot })).path; }
    catch (e) {
      // 没有原生文件选择框（浏览器里打开开发服务）时手动输入完整路径
      const typed = prompt(`${e.message}\n\n${SOURCES.find(s => s.key === slot).name}：`, picks[slot] || '');
      if (!typed) return;
      try { path = (await api('earthwork/clean/check', { slot, path: typed })).path; } catch (err) { return toast(err.message); }
    }
    if (!path) return;
    picks[slot] = path;
    // 勘察报告 PDF 同时含钻孔柱状图和平面图：添加其一时，另一项空着就一并填上
    if (ext(path) === 'pdf' && slot === 'holes' && !picks.plan && ext(picks.terrain) !== 'dxf') picks.plan = path;
    if (ext(path) === 'pdf' && slot === 'plan' && !picks.holes) picks.holes = path;
    render(); renderPreview();
  }
  $('run').onclick = async () => {
    if (problems().length) return;
    try { awaiting = true; await api('earthwork/clean/run', { sources: picks, east_prefix: prefix }); render(); }
    catch (e) { awaiting = false; toast(e.message); }
  };
  render(); renderPreview();

  return {
    sync(next) {
      state = next;
      const cleaning = next?.earthwork?.cleaning;
      // 打开页面时带回上次清洗用的文件
      if (!restored && cleaning?.sources) { restored = true; picks = { terrain: '', holes: '', plan: '', ...cleaning.sources }; prefix = cleaning.east_prefix || ''; seenReport = cleaning.report?.time || ''; render(); renderPreview(); return; }
      if (!restored && next) { restored = true; seenReport = cleaning?.report?.time || ''; }
      const time = cleaning?.report?.time || '';
      if (awaiting && time && time !== seenReport) {
        // 清洗完成：转到三维建模
        awaiting = false; seenReport = time; render(); host.openTab?.('build'); return;
      }
      if (awaiting && !next.running && next.error) awaiting = false;      // 清洗失败：留在本页，原因见对话
      if (time) seenReport = awaiting ? seenReport : time;
      render();
    },
  };
}
