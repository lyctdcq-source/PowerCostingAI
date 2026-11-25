// 右栏同屏 PDF 红框对照、多源证据链及框选取值/离线 OCR 弹窗
(function () {
  const T = window.__TIZI__;
  if (!T) return;
  const { escHtml, METHOD_CN } = T;

  function getCellEvidences(cell) {
    if (!cell) return [];
    if (Array.isArray(cell.evidences) && cell.evidences.length) return cell.evidences;
    const locRel = cell.rel && cell.rel.toLowerCase().endsWith('.pdf') ? cell.rel : (cell.locate_rel || '');
    const locPage = cell.page || cell.locate_page;
    const locBbox = (Array.isArray(cell.bbox) && cell.bbox.length === 4) ? cell.bbox : cell.locate_bbox;
    if (locRel && locPage) {
      return [{ role: '原始出处', file: locRel, page: locPage, bbox: locBbox, raw_text: cell.note || '' }];
    }
    return [];
  }

  function evChipLabel(e, i, withDot = false) {
    const num = ['①', '②', '③', '④', '⑤'][i] || String(i + 1);
    const role = String(e.role || '出处').trim();
    const pg = e.page ? `P${e.page}` : '';
    const hasPg = Boolean(pg && new RegExp(`(?:^|\\s|·)${pg}$`).test(role));
    return `${num} ${role}${pg && !hasPg ? `${withDot ? ' · ' : ' '}${pg}` : ''}`;
  }

  T.splitPdfHTML = function () {
    if (!T.splitPdfMode) return '';
    if (!T.selectedCell || !T.selectedCell.cell) {
      return `<div class="tizi-split-pdf"><div class="tizi-split-pdf-head"><strong>◫ 同屏原图对照</strong><button type="button" class="button" id="tiziCloseSplitBtn" style="padding:3px 7px;font-size:11.5px">✕</button></div><div class="tizi-split-pdf-stage"><div class="card empty" style="margin:20px"><p>单击左侧任意单元格，此处立即同步聚焦定位对应 PDF 原页红框。</p></div></div></div>`;
    }
    const { row, column, tower, cell } = T.selectedCell;
    const evs = getCellEvidences(cell);
    const idx = Math.min(T.activeEvIndex, Math.max(0, evs.length - 1));
    const curEv = evs[idx] || null;
    const sid = encodeURIComponent(state?.session_id || '');
    let imgHtml = `<div class="card empty" style="margin:20px"><p>当前单元格无 PDF 坐标证据（${escHtml(METHOD_CN[cell.method] || cell.method || '默认值/留空')}）。</p></div>`;
    let headTitle = `${escHtml(tower || `第${row + 1}行`)} · ${escHtml(column)}`;
    const hasBbox = Boolean(curEv && Array.isArray(curEv.bbox) && curEv.bbox.length === 4);
    if (curEv && curEv.file && curEv.file.toLowerCase().endsWith('.pdf') && curEv.page) {
      const pg0 = Math.max(0, Number(curEv.page) - 1);
      const hlParam = hasBbox ? `&highlight=${curEv.bbox.join(',')}` : '';
      let clipParam = '';
      let dpi = 140;
      if (T.splitFocusCrop && hasBbox) {
        const [bx0, by0, bx1, by1] = curEv.bbox.map(Number);
        const cx0 = Math.max(0, Math.round(bx0 - 220));
        const cy0 = Math.max(0, Math.round(by0 - 110));
        const cx1 = Math.round(bx1 + 220);
        const cy1 = Math.round(by1 + 140);
        clipParam = `&clip=${cx0},${cy0},${cx1},${cy1}`;
        dpi = 185;
      }
      const src = `/api/tizi/drawing/page?path=${encodeURIComponent(curEv.file)}&page=${pg0}&dpi=${dpi}${hlParam}${clipParam}&sid=${sid}`;
      const baseName = curEv.file.split('/').pop();
      headTitle = `📄 ${escHtml(baseName)} · P${curEv.page}`;
      imgHtml = `<div class="tizi-split-paper"><img src="${src}" alt="原图定位" loading="eager"></div>`;
    }
    const cropSegHtml = hasBbox
      ? `<div class="tizi-seg" role="group" aria-label="原图视图"><button type="button" id="tiziSplitCropBtn" aria-pressed="${T.splitFocusCrop}" title="高清裁切放大红框附近区域">聚焦红框</button><button type="button" id="tiziSplitFullBtn" aria-pressed="${!T.splitFocusCrop}" title="查看整页概览">整页概览</button></div>`
      : '';
    const chipsHtml = evs.length
      ? `<div class="tizi-ev-chips">${evs.map((e, i) => `<button type="button" class="tizi-ev-chip${i === idx ? ' active' : ''}" data-ev-idx="${i}" title="${escHtml(e.raw_text || e.file)}">${escHtml(evChipLabel(e, i, false))}</button>`).join('')}</div>`
      : '';
    const valStr = cell.revised
      ? `${cell.machine_value ?? cell.old ?? '空'} → ${cell.value ?? '空'}`
      : (cell.value ?? '空');
    const stText = `${METHOD_CN[cell.method] || cell.method || '—'}${cell.conf != null ? ` (${Math.round(cell.conf * 100)}%)` : ''}`;
    const metaRowHtml = `<div class="tizi-split-meta-row">
      <div><small>${escHtml(tower || `第${row + 1}行`)} · ${escHtml(column)}</small><b>${escHtml(valStr)}</b></div>
      <div><small>取数方式 / 置信度</small><b>${escHtml(stText)}</b></div>
    </div>`;
    const traceHtml = cell.formula_trace
      ? `<div class="tizi-formula-bar"><b>推导算式：</b>${escHtml(cell.formula_trace)}</div>`
      : (curEv?.raw_text ? `<div class="tizi-formula-bar"><b>原文切片：</b>${escHtml(curEv.raw_text)}</div>` : '');

    return `
      <div class="tizi-split-pdf">
        <div class="tizi-split-pdf-head">
          <strong title="${escHtml(curEv?.file || '')}">${headTitle}</strong>
          <div style="display:flex;align-items:center;gap:5px;flex-shrink:0">
            ${cropSegHtml}
            <button type="button" class="button" id="tiziSplitPopoutBtn" title="在全屏弹窗中缩放与框选" style="padding:3px 8px;font-size:11.5px">⤢ 放大</button>
            <button type="button" class="button" id="tiziCloseSplitBtn" title="收起同屏对照" style="padding:3px 7px;font-size:11.5px">✕</button>
          </div>
        </div>
        <div class="tizi-split-pdf-stage">${imgHtml}</div>
        <div class="tizi-split-pdf-foot">
          ${chipsHtml}
          ${metaRowHtml}
          ${traceHtml}
          <div class="tizi-ev-actions">
            <button type="button" class="primary" id="tiziConfirmCellBtn" title="快捷键 Space">✓ 确认无误 (Space)</button>
            <button type="button" class="button" id="tiziPickFromDrawBtn">框选重取</button>
            <button type="button" class="button" id="tiziFlagPendingBtn">标待复核</button>
            ${T.activeSheet === '基础明细表' && column.startsWith('基础型号.') && cell.value ? `<button type="button" class="button" id="tiziJumpFdnLibBtn" data-fdn-model="${escHtml(cell.value)}">基础库 →</button>` : ''}
            ${cell.revised ? `<button type="button" class="button" id="tiziRevertOneBtn">撤销修订</button>` : ''}
            ${T.tiziSummary().status === 'paused_hitl' && Number(T.statusData?.hitl_review?.p0_count || 0) === 0 ? `<button type="button" class="button" id="tiziBackToChatApproveBtn" style="margin-left:auto;border-color:#24745b;color:#1a5e47">← 调好了，回中间确认输出</button>` : ''}
          </div>
        </div>
      </div>`;
  };

  T.evidenceHTML = function () {
    if (!T.selectedCell || !T.selectedCell.cell) {
      return `<div class="card tizi-evidence"><div class="muted" style="font-size:12.5px">单击上方任意单元格，在此查看多源证据链（ProvenanceDAG）、推导算式与坐标；支持「◫ 同屏对照原图」「Space 一键确认」与撤销修订。</div></div>`;
    }
    const { row, column, tower, cell } = T.selectedCell;
    const evs = getCellEvidences(cell);
    const idx = Math.min(T.activeEvIndex, Math.max(0, evs.length - 1));
    const curEv = evs[idx] || null;
    const canLocate = Boolean(curEv && curEv.file && curEv.file.toLowerCase().endsWith('.pdf') && curEv.page);
    const valStr = cell.revised
      ? `${cell.machine_value ?? cell.old ?? '空'} → ${cell.value ?? '空'}`
      : (cell.value ?? '空');
    const chipsHtml = evs.length
      ? `<div class="tizi-ev-chips" style="margin-bottom:8px">${evs.map((e, i) => `<button type="button" class="tizi-ev-chip${i === idx ? ' active' : ''}" data-ev-idx="${i}" title="${escHtml(e.raw_text || e.file)}">${escHtml(evChipLabel(e, i, true))}</button>`).join('')}</div>`
      : '';
    return `
      <div class="card tizi-evidence">
        <div class="card-head" style="margin-bottom:6px">
          <h3>证据链 · ${escHtml(T.activeSheet)} 第 ${row + 1} 行 ${escHtml(tower || '')} / ${escHtml(column)}</h3>
          <span class="badge ${cell.status === 'conflict' ? 'bad' : (cell.status === 'pending' ? 'warn' : (cell.revised ? 'basic' : 'good'))}">${escHtml(METHOD_CN[cell.method] || cell.method || '—')}</span>
        </div>
        ${chipsHtml}
        ${cell.formula_trace ? `<div class="tizi-formula-bar" style="margin-bottom:8px"><b>推导算式：</b>${escHtml(cell.formula_trace)}</div>` : ''}
        <div class="tizi-ev-grid">
          <div><small>当前值${cell.revised ? '（机器原值 → 现值）' : ''}</small><strong>${escHtml(valStr)}</strong></div>
          <div><small>当前证据来源</small><strong>${escHtml(curEv?.file || cell.rel || cell.source || '—')}</strong></div>
          <div><small>页码 / 原文切片</small><strong>${curEv?.page ? `第 ${curEv.page} 页` : '—'}${curEv?.raw_text ? ` · ${escHtml(curEv.raw_text.slice(0, 60))}` : ''}</strong></div>
          <div><small>说明 / 置信度</small><strong>${escHtml(cell.note || '—')}${cell.conf != null ? ` (${Math.round(cell.conf * 100)}%)` : ''}</strong></div>
        </div>
        <div class="tizi-ev-actions">
          <button type="button" class="primary" id="tiziConfirmCellBtn" title="快捷键 Space">✓ 确认无误 (Space)</button>
          <button type="button" class="button" id="tiziLocateBtn" ${canLocate ? '' : 'disabled'}>查看原页并高亮</button>
          <button type="button" class="button" id="tiziPickFromDrawBtn">从图纸框选取值</button>
          <button type="button" class="button" id="tiziFlagPendingBtn">标为待复核</button>
          ${T.activeSheet === '基础明细表' && column.startsWith('基础型号.') && cell.value ? `<button type="button" class="button" id="tiziJumpFdnLibBtn" data-fdn-model="${escHtml(cell.value)}">跳转《基础库》查看 ${escHtml(cell.value)} →</button>` : ''}
          ${cell.revised ? `<button type="button" class="button" id="tiziRevertOneBtn">撤销本格修订</button>` : ''}
          ${T.tiziSummary().status === 'paused_hitl' && Number(T.statusData?.hitl_review?.p0_count || 0) === 0 ? `<button type="button" class="button" id="tiziBackToChatApproveBtn" style="margin-left:auto;border-color:#24745b;color:#1a5e47">← 调好了，回中间确认输出</button>` : ''}
        </div>
      </div>
    `;
  };

  T.bindEvidenceEvents = function (scopeEl) {
    if (!scopeEl) return;
    scopeEl.querySelectorAll('[data-ev-idx]').forEach(b => {
      b.onclick = () => { T.activeEvIndex = Number(b.dataset.evIdx) || 0; T.updateSelectionUI(); };
    });
    scopeEl.querySelector('#tiziSplitCropBtn')?.addEventListener('click', () => { T.splitFocusCrop = true; T.updateSelectionUI(); });
    scopeEl.querySelector('#tiziSplitFullBtn')?.addEventListener('click', () => { T.splitFocusCrop = false; T.updateSelectionUI(); });
    scopeEl.querySelector('#tiziCloseSplitBtn')?.addEventListener('click', () => { T.splitPdfMode = false; T.renderPane(); });
    scopeEl.querySelector('#tiziSplitPopoutBtn')?.addEventListener('click', () => T.openDrawingDialog(true));
    scopeEl.querySelector('#tiziConfirmCellBtn')?.addEventListener('click', () => {
      if (T.selectedCell) T.saveCellEdit(T.selectedCell.row, T.selectedCell.column, null, null, 'confirm');
    });
    scopeEl.querySelector('#tiziLocateBtn')?.addEventListener('click', () => T.openDrawingDialog(true));
    scopeEl.querySelector('#tiziPickFromDrawBtn')?.addEventListener('click', () => T.openDrawingDialog(false));
    scopeEl.querySelector('#tiziFlagPendingBtn')?.addEventListener('click', () => {
      if (T.selectedCell) T.saveCellEdit(T.selectedCell.row, T.selectedCell.column, null, 'pending');
    });
    scopeEl.querySelector('#tiziRevertOneBtn')?.addEventListener('click', () => {
      if (T.selectedCell) T.revertEdits(T.selectedCell.row, T.selectedCell.column);
    });
    scopeEl.querySelector('#tiziJumpFdnLibBtn')?.addEventListener('click', async e => {
      const m = e.currentTarget.dataset.fdnModel || '';
      T.activeSheet = '基础库';
      T.tableFilter = 'all';
      T.tableSearch = m;
      T.tablePage = 0;
      T.selectedCell = null;
      await T.loadTable();
    });
    scopeEl.querySelector('#tiziBackToChatApproveBtn')?.addEventListener('click', () => {
      if (typeof togglePanel === 'function') togglePanel(false);
      T.streamHostEl?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  };

  // ---------- 图纸查看与框选弹窗
  function populateDrawSelect(q) {
    const sel = $('tiziDrawingDialog').querySelector('#tiziDrawSelect');
    const low = (q || '').trim().toLowerCase();
    const list = T.drawFiles.filter(f => !low || `${f.name} ${f.rel}`.toLowerCase().includes(low));
    sel.innerHTML = list.map(f => `<option value="${escHtml(f.rel)}">${escHtml(f.name)} (${escHtml(f.rel)})</option>`).join('');
    if (list.some(f => f.rel === T.drawRel)) sel.value = T.drawRel;
  }

  function updateHighlightOverlay() {
    const d = $('tiziDrawingDialog'), hl = d.querySelector('#tiziDrawHl'), selBox = d.querySelector('#tiziDrawSel'), img = d.querySelector('#tiziDrawImg');
    if (!T.drawPdfSize[0] || !img.clientWidth) { hl.hidden = true; return; }
    const sx = img.clientWidth / T.drawPdfSize[0], sy = img.clientHeight / T.drawPdfSize[1];
    if (T.drawHighlight) {
      const [x0, y0, x1, y1] = T.drawHighlight;
      hl.style.left = (x0 * sx) + 'px';
      hl.style.top = (y0 * sy) + 'px';
      hl.style.width = Math.max(6, (x1 - x0) * sx) + 'px';
      hl.style.height = Math.max(6, (y1 - y0) * sy) + 'px';
      hl.hidden = false;
    } else {
      hl.hidden = true;
    }
    if (T.drawSelBbox) {
      const [x0, y0, x1, y1] = T.drawSelBbox;
      selBox.style.left = (x0 * sx) + 'px';
      selBox.style.top = (y0 * sy) + 'px';
      selBox.style.width = Math.max(4, (x1 - x0) * sx) + 'px';
      selBox.style.height = Math.max(4, (y1 - y0) * sy) + 'px';
      selBox.hidden = false;
    }
  }

  function applyDrawingZoom(nextZoom, anchorX = null, anchorY = null) {
    const d = $('tiziDrawingDialog'), stage = d.querySelector('#tiziDrawStage'), img = d.querySelector('#tiziDrawImg');
    if (!T.drawNaturalW) return;
    const oldZoom = T.drawZoom;
    T.drawZoom = Math.max(0.08, Math.min(6, nextZoom));
    img.style.width = Math.round(T.drawNaturalW * T.drawZoom) + 'px';
    img.style.height = Math.round(T.drawNaturalH * T.drawZoom) + 'px';
    d.querySelector('#tiziDrawZoomLbl').textContent = Math.round(T.drawZoom * 100) + '%';
    if (anchorX != null && anchorY != null && oldZoom) {
      const ratio = T.drawZoom / oldZoom;
      stage.scrollLeft = (stage.scrollLeft + anchorX) * ratio - anchorX;
      stage.scrollTop = (stage.scrollTop + anchorY) * ratio - anchorY;
    }
    updateHighlightOverlay();
  }

  function fitDrawingWidth() {
    const stage = $('tiziDrawingDialog').querySelector('#tiziDrawStage');
    const avail = Math.max(300, stage.clientWidth - 48);
    applyDrawingZoom(T.drawNaturalW ? avail / T.drawNaturalW : 1);
  }

  function focusHighlightBox() {
    const d = $('tiziDrawingDialog'), stage = d.querySelector('#tiziDrawStage'), img = d.querySelector('#tiziDrawImg');
    if (!T.drawHighlight || !T.drawNaturalW) return;
    const [x0, y0, x1, y1] = T.drawHighlight;
    const bw = Math.max(20, x1 - x0);
    const targetZoom = Math.min(2.2, Math.max(T.drawZoom, (stage.clientWidth * 0.42) / (bw / T.drawPdfSize[0] * T.drawNaturalW)));
    applyDrawingZoom(targetZoom);
    const cx = ((x0 + x1) / 2 / T.drawPdfSize[0]) * img.clientWidth + 20;
    const cy = ((y0 + y1) / 2 / T.drawPdfSize[1]) * img.clientHeight + 20;
    stage.scrollLeft = Math.max(0, cx - stage.clientWidth / 2);
    stage.scrollTop = Math.max(0, cy - stage.clientHeight / 2);
  }

  async function renderDrawingPageImg(focusHighlight = false) {
    const d = $('tiziDrawingDialog');
    const pgInfo = T.drawProbe?.pages?.[T.drawPage];
    T.drawPdfSize = pgInfo?.size || [842, 595];
    d.querySelector('#tiziDrawPageLabel').textContent = `${T.drawPage + 1} / ${T.drawProbe?.n_pages || 1}`;
    d.querySelector('#tiziDrawPrev').disabled = T.drawPage <= 0;
    d.querySelector('#tiziDrawNext').disabled = T.drawPage >= (T.drawProbe?.n_pages || 1) - 1;
    d.querySelector('#tiziDrawCapTag').textContent = pgInfo?.hint || T.drawProbe?.summary || '';

    const img = d.querySelector('#tiziDrawImg');
    T.drawImgLoading = true;
    const sid = encodeURIComponent(state?.session_id || '');
    const url = `/api/tizi/drawing/page?path=${encodeURIComponent(T.drawRel)}&page=${T.drawPage}&dpi=170&sid=${sid}`;
    await new Promise((resolve, reject) => {
      img.onload = () => { T.drawNaturalW = img.naturalWidth; T.drawNaturalH = img.naturalHeight; T.drawImgLoading = false; resolve(); };
      img.onerror = () => { T.drawImgLoading = false; reject(Error('图纸页面渲染失败')); };
      img.src = url;
    });
    T.drawSelBbox = null;
    d.querySelector('#tiziDrawSel').hidden = true;
    fitDrawingWidth();
    if (focusHighlight && T.drawHighlight) {
      updateHighlightOverlay();
      focusHighlightBox();
    } else {
      d.querySelector('#tiziDrawHl').hidden = true;
    }
  }

  async function loadDrawingFile(rel, page = 0, focusHighlight = false) {
    T.drawRel = rel;
    T.drawPage = page;
    try {
      T.drawProbe = await T.tiziGet('drawing/probe?path=' + encodeURIComponent(rel));
      if (T.drawPage >= T.drawProbe.n_pages) T.drawPage = 0;
      await renderDrawingPageImg(focusHighlight);
    } catch (e) { toast(e.message); }
  }

  T.bindDrawingDialogEvents = function (d) {
    const sel = d.querySelector('#tiziDrawSelect');
    const stage = d.querySelector('#tiziDrawStage');
    const paper = d.querySelector('#tiziDrawPaper');
    const img = d.querySelector('#tiziDrawImg');
    const selBox = d.querySelector('#tiziDrawSel');

    sel.onchange = () => { T.drawHighlight = null; loadDrawingFile(sel.value, 0, false); };
    d.querySelector('#tiziDrawFileSearch').oninput = e => populateDrawSelect(e.target.value);
    d.querySelector('#tiziDrawPrev').onclick = () => { if (T.drawPage > 0) { T.drawHighlight = null; T.drawPage -= 1; renderDrawingPageImg(false); } };
    d.querySelector('#tiziDrawNext').onclick = () => { if (T.drawPage < (T.drawProbe?.n_pages || 1) - 1) { T.drawHighlight = null; T.drawPage += 1; renderDrawingPageImg(false); } };
    d.querySelector('#tiziDrawOut').onclick = () => applyDrawingZoom(T.drawZoom / 1.25);
    d.querySelector('#tiziDrawIn').onclick = () => applyDrawingZoom(T.drawZoom * 1.25);
    d.querySelector('#tiziDrawFit').onclick = fitDrawingWidth;
    d.querySelector('#tiziDraw100').onclick = () => applyDrawingZoom(1);

    stage.addEventListener('wheel', e => {
      e.preventDefault();
      const r = stage.getBoundingClientRect();
      applyDrawingZoom(T.drawZoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    let mode = '', startX = 0, startY = 0, scrollL = 0, scrollT = 0;
    paper.addEventListener('contextmenu', e => e.preventDefault());
    paper.addEventListener('pointerdown', e => {
      if (T.drawImgLoading || !img.clientWidth) return;
      if (e.button === 1 || e.button === 2 || e.shiftKey) {
        mode = 'pan'; startX = e.clientX; startY = e.clientY;
        scrollL = stage.scrollLeft; scrollT = stage.scrollTop;
        paper.classList.add('panning');
        paper.setPointerCapture(e.pointerId);
        return;
      }
      if (e.button !== 0) return;
      mode = 'select';
      const r = img.getBoundingClientRect();
      startX = Math.max(0, Math.min(r.width, e.clientX - r.left));
      startY = Math.max(0, Math.min(r.height, e.clientY - r.top));
      selBox.style.left = startX + 'px'; selBox.style.top = startY + 'px';
      selBox.style.width = '0px'; selBox.style.height = '0px';
      selBox.hidden = false;
      paper.setPointerCapture(e.pointerId);
    });
    paper.addEventListener('pointermove', e => {
      if (!mode) return;
      if (mode === 'pan') {
        stage.scrollLeft = scrollL - (e.clientX - startX);
        stage.scrollTop = scrollT - (e.clientY - startY);
        return;
      }
      const r = img.getBoundingClientRect();
      const cx = Math.max(0, Math.min(r.width, e.clientX - r.left));
      const cy = Math.max(0, Math.min(r.height, e.clientY - r.top));
      selBox.style.left = Math.min(startX, cx) + 'px';
      selBox.style.top = Math.min(startY, cy) + 'px';
      selBox.style.width = Math.abs(cx - startX) + 'px';
      selBox.style.height = Math.abs(cy - startY) + 'px';
    });
    paper.addEventListener('pointerup', async () => {
      if (!mode) return;
      const curMode = mode; mode = '';
      paper.classList.remove('panning');
      if (curMode !== 'select') return;
      const r = img.getBoundingClientRect();
      const cx = parseFloat(selBox.style.left) + parseFloat(selBox.style.width);
      const cy = parseFloat(selBox.style.top) + parseFloat(selBox.style.height);
      const sx0 = parseFloat(selBox.style.left), sy0 = parseFloat(selBox.style.top);
      if (parseFloat(selBox.style.width) < 4 || parseFloat(selBox.style.height) < 4) { selBox.hidden = true; return; }
      const x0 = Math.round(sx0 / r.width * T.drawPdfSize[0] * 10) / 10;
      const y0 = Math.round(sy0 / r.height * T.drawPdfSize[1] * 10) / 10;
      const x1 = Math.round(cx / r.width * T.drawPdfSize[0] * 10) / 10;
      const y1 = Math.round(cy / r.height * T.drawPdfSize[1] * 10) / 10;
      T.drawSelBbox = [x0, y0, x1, y1];
      try {
        T.drawRegion = await T.tiziGet(`drawing/region?path=${encodeURIComponent(T.drawRel)}&page=${T.drawPage}&x0=${x0}&y0=${y0}&x1=${x1}&y1=${y1}`);
        d.querySelector('#tiziDrawBbox').textContent = `PDF bbox: [${x0}, ${y0}, ${x1}, ${y1}]`;
        if (!T.drawRegion.readable) {
          d.querySelector('#tiziDrawText').textContent = '无文字层（矢量路径），请照图人工录入';
        } else if (T.drawRegion.looks_value) {
          d.querySelector('#tiziDrawText').textContent = T.drawRegion.text;
          d.querySelector('#tiziDrawValue').value = T.drawRegion.suggested_value;
        } else {
          d.querySelector('#tiziDrawText').textContent = `框内文字非数值（${T.drawRegion.text.slice(0, 24)}），请照图录入`;
        }
        d.querySelector('#tiziDrawApply').disabled = !T.drawTarget;
        const ocrBtn = d.querySelector('#tiziDrawOcrBtn');
        if (ocrBtn) ocrBtn.disabled = false;
      } catch (err) { toast(err.message); }
    });

    d.querySelector('#tiziDrawOcrBtn')?.addEventListener('click', async () => {
      if (!T.drawSelBbox || !T.drawRel) return toast('请先在图纸上拖框选择区域。');
      try {
        toast('正在调用本机离线 OCR 识别框选区域…');
        const res = await T.tiziPost('drawing/ocr', { path: T.drawRel, page: T.drawPage, bbox: T.drawSelBbox });
        if (!res.available) {
          toast(res.reason || '本机未检测到可用离线 OCR 引擎');
          return;
        }
        if (res.text) {
          d.querySelector('#tiziDrawText').textContent = `[离线OCR] ${res.text.slice(0, 60)}`;
          d.querySelector('#tiziDrawValue').value = (res.lines && res.lines[0]) || res.text;
          toast('离线 OCR 已填入候选值，请人工核对后再点击「写入这一格」');
        } else {
          toast(res.reason || '离线 OCR 未识别到文字');
        }
      } catch (err) { toast(err.message); }
    });

    d.querySelector('#tiziDrawApply').onclick = async () => {
      if (!T.drawTarget || !T.drawRegion) return;
      const val = d.querySelector('#tiziDrawValue').value.trim();
      if (!val) return toast('请先填写确认后的数值。');
      try {
        const res = await T.tiziPost('drawing/apply', {
          sheet: T.activeSheet,
          row: T.drawTarget.row,
          column: T.drawTarget.column,
          value: val,
          path: T.drawRel,
          page: T.drawPage,
          bbox: T.drawRegion.bbox,
          text: T.drawRegion.text || '',
        });
        if (res.cell) {
          T.selectedCell = {
            sheet: T.activeSheet, row: T.drawTarget.row, column: T.drawTarget.column,
            tower: res.cell.tower,
            cell: { ...res.cell.prov, value: res.cell.value, status: res.cell.status, revised: res.cell.revised, old: res.cell.old },
          };
        }
        d.close();
        toast('已按图纸框选写入单元格并保存坐标溯源');
        await T.loadStatus(true);
      } catch (err) { toast(err.message); }
    };
  };

  T.openDrawingDialog = async function (locateMode = false, explicitRel = '') {
    try {
      const res = await T.tiziGet('drawing/list');
      T.drawFiles = res.files || [];
      if (!T.drawFiles.length) return toast('当前施工图目录下没有 PDF 文件。');
      T.drawTarget = T.selectedCell ? { row: T.selectedCell.row, column: T.selectedCell.column, tower: T.selectedCell.tower } : null;
      T.drawHighlight = null;
      T.drawSelBbox = null;
      T.drawRegion = null;

      let targetRel = explicitRel || '';
      let targetPage = 0;
      const sc = T.selectedCell?.cell;
      const evs = getCellEvidences(sc);
      const curEv = evs[Math.min(T.activeEvIndex, Math.max(0, evs.length - 1))] || null;
      const scRel = curEv?.file || ((sc?.rel && sc.rel.toLowerCase().endsWith('.pdf')) ? sc.rel : (sc?.locate_rel || ''));
      const scPage = curEv?.page || sc?.page || sc?.locate_page || 1;
      const scBbox = (Array.isArray(curEv?.bbox) && curEv.bbox.length === 4)
        ? curEv.bbox
        : ((Array.isArray(sc?.bbox) && sc.bbox.length === 4) ? sc.bbox : sc?.locate_bbox);
      if (locateMode && scRel) {
        targetRel = scRel;
        targetPage = Math.max(0, Number(scPage) - 1);
        T.drawHighlight = scBbox || null;
      } else if (!targetRel && scRel && T.drawFiles.some(f => f.rel === scRel)) {
        targetRel = scRel;
        targetPage = Math.max(0, Number(scPage) - 1);
      }
      if (!targetRel || !T.drawFiles.some(f => f.rel === targetRel)) targetRel = T.drawFiles[0].rel;

      const d = $('tiziDrawingDialog');
      d.querySelector('#tiziDrawTargetTag').textContent = T.drawTarget
        ? `目标格：第 ${T.drawTarget.row + 1} 行 · ${T.drawTarget.tower || ''} · ${T.drawTarget.column}`
        : '只读看图（先在明细表选一格即可写入）';
      d.querySelector('#tiziDrawFileSearch').value = '';
      populateDrawSelect('');
      T.drawRel = targetRel;
      T.drawPage = targetPage;
      d.querySelector('#tiziDrawSelect').value = T.drawRel;
      d.querySelector('#tiziDrawText').textContent = '在图上按住左键拖框选择数值';
      d.querySelector('#tiziDrawValue').value = '';
      d.querySelector('#tiziDrawBbox').textContent = '';
      d.querySelector('#tiziDrawApply').disabled = true;
      d.querySelector('#tiziDrawSel').hidden = true;
      d.showModal();
      await loadDrawingFile(T.drawRel, T.drawPage, Boolean(T.drawHighlight));
    } catch (e) { toast(e.message); }
  };
})();
