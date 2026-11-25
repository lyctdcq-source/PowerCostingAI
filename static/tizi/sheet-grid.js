// 右栏 9 表网格、基本信息表单、资料与探查 / 校验与导出 Inspector 及字段配对弹窗
(function () {
  const T = window.__TIZI__;
  if (!T) return;
  const { escHtml, STEP_META, KIND_CN, MAP_STATE_CN } = T;

  function discoverHTML() {
    const tz = T.tiziSummary(), disc = T.statusData?.discover || {}, srcInfo = T.statusData?.src_info || [];
    const doneSet = new Set(tz.steps_done || []);
    const running = T.isRunning();
    const counts = disc.counts || {};
    const nonExtract = (counts.drawing || 0) + (counts.scan || 0) + (counts.other || 0);
    const folderVal = tz.folder || T.folderInputDraft || '';

    const workflow = `<div class="tizi-workflow" aria-label="解析步骤进度">${STEP_META.map((s, i) => {
      const isCur = running && tz.step === s.id;
      const isDone = doneSet.has(s.id);
      return `<span class="tizi-step ${isCur ? 'current' : isDone ? 'done' : ''}"><em>${isDone && !isCur ? '✓' : i + 1}</em>${s.name}</span>`;
    }).join('')}</div>`;

    const filesRows = (disc.files || []).slice(0, 80).map(f => {
      const kindCls = f.kind === 'table' ? 'good' : (f.kind === 'doc' ? 'basic' : 'warn');
      return `<tr>
        <td title="${escHtml(f.rel)}">${escHtml(f.name || f.rel)}</td>
        <td><span class="badge ${kindCls}">${escHtml(KIND_CN[f.kind] || f.kind || '—')}</span></td>
        <td>${f.pages ?? '—'}</td>
        <td>${f.words_pg != null ? Math.round(f.words_pg) : '—'}</td>
        <td>${f.cols || '—'}</td>
        <td title="${escHtml((f.evidence || []).join('；'))}">${escHtml((f.evidence || []).join('；').slice(0, 48) || '—')}</td>
      </tr>`;
    }).join('');

    const carrierRows = srcInfo.map(s => {
      const cls = s.carrier === 'T2' ? 'good' : (s.skipped || s.error ? 'bad' : 'warn');
      return `<tr>
        <td>${escHtml(s.name)}</td>
        <td><span class="badge ${cls}">${escHtml(s.carrier || '失败')}</span></td>
        <td>${s.pages ?? '—'}</td>
        <td>${s.chars_pg != null ? Math.round(s.chars_pg) : '—'}</td>
        <td>${s.draws_pg != null ? Math.round(s.draws_pg) : '—'}</td>
        <td>${escHtml(s.pipeline || s.reason || s.error || '—')}</td>
      </tr>`;
    }).join('');

    const artifacts = Object.values(T.statusData?.artifacts || {});
    const artBlocks = artifacts.map(a => {
      const mStr = (a.metrics || []).map(m => `${m.k}: ${m.v}`).join(' · ');
      let body = '';
      if (a.kind === 'table' && a.columns) {
        body = `<div class="tizi-art-table"><table><thead><tr>${a.columns.map(c => `<th>${escHtml(c)}</th>`).join('')}</tr></thead><tbody>${(a.rows || []).slice(0, 15).map(r => `<tr>${r.map(c => `<td>${escHtml(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      } else if (a.items) {
        body = `<ul>${a.items.map(it => `<li><b>${escHtml(it.name)}</b>：${escHtml(it.desc)}</li>`).join('')}</ul>`;
      }
      return `<details class="skill-source"><summary>${escHtml(a.title)} <small>${escHtml(mStr)}</small></summary>${body}${a.note ? `<p class="hint">${escHtml(a.note)}</p>` : ''}</details>`;
    }).join('');

    return `
      <div class="card">
        <div class="card-head">
          <h3>施工图工作空间概览</h3>
          <span class="tag">${tz.status === 'paused_hitl' ? '待中间栏审核确认' : (tz.cfg_mode === 'auto' ? 'T2 本地 + T3 视觉重访' : '智能体工作台')}</span>
        </div>
        <div style="font-size:13px"><b>当前目录：</b><code>${escHtml(folderVal || '尚未绑定（请在中间栏下方点击「选择工作空间」）')}</code></div>
        <p class="hint" style="margin:6px 0 0">工作空间选择、启动提资与审核确认均在中间对话栏完成；本面板仅用于查看解析结果、红框原图对照与交付产物。</p>
        ${tz.error ? `<div class="error" style="margin-top:8px">${escHtml(tz.error)}</div>` : ''}
        ${workflow}
      </div>

      ${(disc.scanned || srcInfo.length) ? `
      <div class="tizi-metrics">
        <div class="tizi-metric"><label>扫描 PDF 总数</label><strong>${disc.scanned || srcInfo.length}</strong><small>${disc.duplicates ? `已去重 ${disc.duplicates} 份副本` : '已完成目录扫描'}</small></div>
        <div class="tizi-metric"><label>表格类（T2 可提）</label><strong class="good">${counts.table || srcInfo.filter(s => s.carrier === 'T2').length}</strong><small>列边界 + 行聚类</small></div>
        <div class="tizi-metric"><label>矢量 / 扫描件</label><strong class="${nonExtract ? 'warn' : ''}">${nonExtract}</strong><small>不自动猜数，支持框选填值</small></div>
        <div class="tizi-metric"><label>主数据源</label><strong style="font-size:15px">${escHtml((disc.primary || '').split(/[\\/]/).pop() || '配置主表')}</strong><small title="${escHtml(disc.primary || '')}">${escHtml(disc.primary || '按模板配置')}</small></div>
      </div>

      ${carrierRows ? `<div class="card table-card"><div class="card-head"><h3>载体判定矩阵</h3><span>仅 T2 坐标流进入自动表格解析</span></div><div class="table-scroll"><table><thead><tr><th>文件</th><th>载体</th><th>页数</th><th>字/页</th><th>图/页</th><th>采用管线 / 说明</th></tr></thead><tbody>${carrierRows}</tbody></table></div></div>` : ''}

      ${filesRows ? `<div class="card table-card"><div class="card-head"><h3>目录文件归类</h3><span>共 ${disc.scanned} 份 PDF（按版式特征判定）</span></div><div class="table-scroll"><table><thead><tr><th>文件</th><th>归类</th><th>页数</th><th>词/页</th><th>列数</th><th>判定依据</th></tr></thead><tbody>${filesRows}</tbody></table></div></div>` : ''}

      <div class="card">
        <details>
          <summary>高级：单步重跑与分步产物（${artifacts.length} 项）</summary>
          <div class="tizi-step-btns">
            ${STEP_META.map(s => `<button type="button" class="button" data-tizi-step="${s.id}" ${running ? 'disabled' : ''}>重跑：${s.name}</button>`).join('')}
          </div>
          ${artBlocks || '<p class="hint">尚无分步产物。</p>'}
        </details>
      </div>` : `
      <div class="card empty">
        <div class="empty-icon">▧</div>
        <h3>请先在中间对话栏绑定施工图目录</h3>
        <p>绑定目录并发送「开始提资」后，本面板将展示载体判定矩阵、9 张业务提资表与 PDF 红框原图对照。</p>
      </div>`}
    `;
  }

  function buildTableHead(columns) {
    const hasGroups = columns.some(c => c.group);
    if (!hasGroups) {
      return `<tr><th class="tizi-sticky-0">#</th>${columns.map((c, j) => `<th class="${j === 0 ? 'tizi-sticky-1' : ''}${c.is_default ? ' tizi-th-default' : ''}">${escHtml(c.label || c.key)}</th>`).join('')}</tr>`;
    }
    const topCells = [`<th class="tizi-sticky-0" rowspan="2">#</th>`];
    const subCells = [];
    let i = 0;
    while (i < columns.length) {
      const c = columns[i];
      if (!c.group) {
        topCells.push(`<th rowspan="2" class="${i === 0 ? 'tizi-sticky-1' : ''}${c.is_default ? ' tizi-th-default' : ''}">${escHtml(c.label || c.key)}</th>`);
        i += 1;
        continue;
      }
      let span = 1;
      while (i + span < columns.length && columns[i + span].group === c.group && columns[i + span].is_default === c.is_default) {
        span += 1;
      }
      topCells.push(`<th colspan="${span}" class="${c.is_default ? 'tizi-th-default' : ''}">${escHtml(c.group)}</th>`);
      for (let k = i; k < i + span; k++) {
        const sub = columns[k];
        subCells.push(`<th class="${sub.is_default ? 'tizi-th-default' : ''}">${escHtml(sub.label || sub.key)}</th>`);
      }
      i += span;
    }
    return `<tr class="tizi-hdr-top">${topCells.join('')}</tr><tr class="tizi-hdr-sub">${subCells.join('')}</tr>`;
  }

  function sheetBarHTML() {
    const sheets = T.tableData?.sheets || T.statusData?.sheets || [];
    if (!sheets.length) return '';
    return `<div class="tizi-sheet-dock">
      <button type="button" class="tizi-sheet-scroll-btn" id="tiziSheetScrollLeft" title="向左滚动工作表" aria-label="向左滚动工作表">‹</button>
      <div class="tizi-sheet-bar" role="tablist" aria-label="提资工作簿工作表">${sheets.map(s => {
        const dotCls = s.conflict > 0 ? 'bad' : (s.rows > 0 ? (s.pending > 0 ? 'warn' : 'ok') : 'muted');
        const cntTxt = s.rows > 0 ? `${s.rows}${s.pending ? ` · 待${s.pending}` : ''}` : '待补源';
        return `<button type="button" class="tizi-sheet-tab${T.activeSheet === s.name ? ' active' : ''}" data-tizi-sheet="${escHtml(s.name)}" title="${escHtml(s.missing_source || `${s.name}（${s.rows}行，覆盖率${s.coverage}%）`)}"><i class="tizi-sheet-dot ${dotCls}"></i><span>${escHtml(s.short_title || s.name)}</span><span class="tizi-sheet-cnt">${escHtml(cntTxt)}</span></button>`;
      }).join('')}</div>
      <button type="button" class="tizi-sheet-scroll-btn" id="tiziSheetScrollRight" title="向右滚动工作表" aria-label="向右滚动工作表">›</button>
    </div>`;
  }

  function basicInfoFormHTML(columns, rows) {
    if (!rows.length) {
      return `<div class="card empty" style="margin:14px"><p>暂无《基本信息》提取结果。</p></div>`;
    }
    const groupsOrder = [
      { title: '工程与线路参数', keys: ['单项工程名称', '电压等级', '设计阶段', '线路段名称', '回路类型', '导线型号', '地线1型号', '地线2型号', '线路亘长（km）'] },
      { title: '运距信息', keys: ['人力运距（km）', '汽车运距（km）'] },
      { title: '地形比例（%）', keys: ['地形类型', '丘陵（%）', '一般山地（%）', '高山大岭（%）', '峻岭（%）', '泥沼（%）', '河网（%）', '沙漠（%）'] },
    ];
    if (T.showDefaults) {
      groupsOrder.push({ title: '模板默认值', keys: ['省份和地区', '架线方式', '冰区（mm）', '平均海拔高程（m）'] });
    }
    const colLabel = Object.fromEntries(columns.map(c => [c.key, c.label || c.key]));
    return rows.map(r => {
      const segTitle = r.cells?.['线路段名称']?.value || `线路段 ${r.line_no}`;
      const cards = groupsOrder.map(g => {
        const fields = g.keys.filter(k => r.cells && k in r.cells).map(k => {
          const c = r.cells[k] || {};
          const key = `${r.row}::${k}`;
          const isSel = T.selectedCell && T.selectedCell.sheet === T.activeSheet && T.selectedCell.row === r.row && T.selectedCell.column === k;
          const isEdit = T.editingCellKey === key;
          const disp = c.value === null || c.value === undefined || c.value === '' ? '—' : String(c.value);
          const valHtml = isEdit
            ? `<input class="tizi-inline-edit" data-edit-row="${r.row}" data-edit-col="${escHtml(k)}" value="${escHtml(c.value ?? '')}" style="max-width:190px;text-align:right">`
            : `<strong>${escHtml(disp)}</strong>`;
          const conflictBtns = (c.status === 'conflict' && Array.isArray(c.conflict_candidates) && c.conflict_candidates.length)
            ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 6px">${c.conflict_candidates.map(cd => `<button type="button" class="button" style="font-size:11px;padding:3px 8px" data-adopt-row="${r.row}" data-adopt-col="${escHtml(k)}" data-adopt-val="${escHtml(cd.value)}">采纳：${escHtml(cd.label)}</button>`).join('')}</div>`
            : '';
          return `<div class="tizi-form-field tizi-st-${c.status || 'empty'}${isSel ? ' tizi-selected' : ''}" data-row="${r.row}" data-col="${escHtml(k)}" title="${escHtml(c.formula_trace || c.note || disp)}"><label>${escHtml(colLabel[k] || k)}</label>${valHtml}</div>${conflictBtns}`;
        }).join('');
        return `<div class="tizi-form-group"><h4>${escHtml(segTitle)} · ${escHtml(g.title)}</h4>${fields}</div>`;
      }).join('');
      return `<div class="tizi-form-grid">${cards}</div>`;
    }).join('');
  }

  function isNumericCellVal(v) {
    if (v === null || v === undefined || v === '') return false;
    return /^-?\d+(?:\.\d+)?$/.test(String(v).trim());
  }

  function tableHTML() {
    const tz = T.tiziSummary(), sm = tz.summary || {};
    if (!T.hasRows()) {
      return `<div class="card empty"><div class="empty-icon">◫</div><h3>尚无提资明细数据</h3><p>请先在中间对话栏选择工作空间并发送「开始提资」。</p><button type="button" class="primary" id="tiziGoDiscover">查看资料与探查 →</button></div>`;
    }
    const sheets = T.tableData?.sheets || T.statusData?.sheets || [];
    const curSheetMeta = sheets.find(s => s.name === T.activeSheet) || { rows: sm.rows, pending: sm.pending, conflict: 0, coverage: sm.coverage, core_cols: sm.targets };
    const td = T.tableData || { columns: [], targets: [], rows: [], total: curSheetMeta.rows, filtered_total: 0, unmapped: T.statusData?.unmapped || [] };
    const columns = td.columns && td.columns.length ? td.columns : (td.targets || []).map(k => ({ key: k, label: k, group: '', is_default: false }));
    const rows = td.rows || [];
    const pages = Math.max(1, Math.ceil((td.filtered_total || 0) / T.TABLE_LIMIT));
    const wbChecks = T.statusData?.workbook_checks || T.statusData?.checks || [];
    const wbOk = wbChecks.filter(c => c.ok).length;
    const wbBad = wbChecks.length - wbOk;
    const curIssueCnt = (curSheetMeta.pending || 0) + (curSheetMeta.conflict || 0);

    const headHtml = buildTableHead(columns);
    const bodyRows = rows.map(r => {
      const tds = columns.map((col, j) => {
        const t = col.key;
        const c = r.cells[t] || {};
        const key = `${r.row}::${t}`;
        const isSel = T.selectedCell && T.selectedCell.sheet === T.activeSheet && T.selectedCell.row === r.row && T.selectedCell.column === t;
        const isEdit = T.editingCellKey === key;
        const numCls = (j > 0 && !isEdit && isNumericCellVal(c.value)) ? ' tizi-num' : '';
        const cls = `tizi-st-${c.status || 'empty'}${j === 0 ? ' tizi-sticky-1' : ''}${col.is_default ? ' tizi-td-default' : ''}${numCls}${isSel ? ' tizi-selected' : ''}`;
        const disp = c.value === null || c.value === undefined || c.value === '' ? '—' : String(c.value);
        const content = isEdit
          ? `<input class="tizi-inline-edit" data-edit-row="${r.row}" data-edit-col="${escHtml(t)}" value="${escHtml(c.value ?? '')}">`
          : escHtml(disp);
        return `<td class="${cls}" data-row="${r.row}" data-col="${escHtml(t)}" title="${escHtml(c.formula_trace || c.note || disp)}">${content}</td>`;
      }).join('');
      return `<tr><td class="tizi-sticky-0">${r.line_no}</td>${tds}</tr>`;
    }).join('');

    const missingHtml = td.missing_source
      ? `<div class="notice warning tizi-unmapped-bar"><span>《${escHtml(T.activeSheet)}》当前状态：<b>${escHtml(td.missing_source)}</b></span></div>`
      : '';

    return `<div class="tizi-wb-shell">
      <div class="tizi-kpi-strip">
        <span class="tizi-kpi-item">${escHtml(T.activeSheet)} <b>${curSheetMeta.rows}</b> 行 · ${columns.length} 列${T.showDefaults ? '（含默认）' : ''}</span>
        <i class="tizi-kpi-sep"></i>
        <span class="tizi-kpi-item">填充率 <b class="good">${curSheetMeta.coverage ?? sm.coverage}%</b></span>
        <i class="tizi-kpi-sep"></i>
        <button type="button" class="tizi-kpi-link" id="tiziFilterPendingMetric" title="点击筛选待复核单元格">本表待核/冲突 <b class="${curIssueCnt ? 'warn' : 'good'}">${curIssueCnt}</b></button>
        <i class="tizi-kpi-sep"></i>
        <button type="button" class="tizi-kpi-link" id="tiziGoDeliveryMetric" title="查看全工程双源交叉校验明细">全工程校验 <b class="${wbBad ? 'bad' : 'good'}">${wbOk}/${wbChecks.length}</b></button>
        <span class="tizi-kpi-spacer"></span>
        <span class="tizi-kpi-item" style="color:#88928c;font-size:11px;white-space:nowrap">单击定位 · 双击改 · Space确认 · Alt+↓</span>
      </div>

      <div class="tizi-split${T.splitPdfMode ? ' split-on' : ''}">
        <div class="tizi-split-left">
          <div class="card table-card tizi-table-card${T.tableExpanded ? ' match-expanded' : ''}">
            <div class="tizi-toolbar">
              <h3>${escHtml(T.activeSheet)}</h3>
              <div class="tizi-seg" role="group" aria-label="单元格筛选">
                ${[['all', '全部'], ['pending', `待复核 (${curSheetMeta.pending || 0})`], ['revised', '已修订'], ['drawing', '框选']].map(([k, l]) =>
                  `<button type="button" aria-pressed="${T.tableFilter === k}" data-tizi-flt="${k}">${l}</button>`
                ).join('')}
              </div>
              <input id="tiziTableSearch" class="tizi-search" value="${escHtml(T.tableSearch)}" placeholder="搜塔号 / 型号 / 材料…">
              <div class="tizi-toolbar-actions">
                ${td.has_defaults ? `<button type="button" class="tizi-toggle-btn" id="tiziToggleDefaultsBtn" aria-pressed="${T.showDefaults}" title="展开或隐藏模板默认值列">${T.showDefaults ? '隐默认值' : '默认值列'}</button>` : ''}
                <button type="button" class="tizi-toggle-btn" id="tiziSplitBtn" aria-pressed="${T.splitPdfMode}" title="同屏左右对照 PDF 原图">◫ 同屏原图</button>
                <button type="button" class="tizi-toggle-btn" id="tiziFocusWideBtn" aria-pressed="${T.focusWideMode}" title="收起左侧对话栏，让宽表与原图独享全屏宽">◰ 专注双栏</button>
                <button type="button" class="tizi-toggle-btn" id="tiziNextPendingBtn" title="快捷键 Alt+↓">⚡ 待复核</button>
                ${T.activeSheet === '杆塔明细表' ? `<button type="button" class="tizi-toggle-btn" id="tiziOpenMappingBtn">字段配对${sm.unmapped ? ` (${sm.unmapped})` : ''}</button>` : ''}
                ${['结构设备材料表', '电气设备材料表', '光缆设备材料表'].includes(T.activeSheet) ? `<button type="button" class="tizi-toggle-btn" id="tiziPriceMatchBtn" title="只读查询平台物资价格库并填入参考单价（标黄待确认）">🏷️ 匹配价格库</button>` : ''}
                <div class="tizi-legend-wrap">
                  <button type="button" class="tizi-toggle-btn" tabindex="0" title="单元格颜色图例与快捷键">? 图例</button>
                  <div class="tizi-legend-pop" role="tooltip">
                    <div><i style="background:#f1f9f4"></i><span>原文直取（PDF 坐标流直提）</span></div>
                    <div><i style="background:#eff5fc"></i><span>多源推导 / 查表 / 标准基础库</span></div>
                    <div><i style="background:#fef6e4"></i><span>待复核（缺数、插值或参考价）</span></div>
                    <div><i style="background:#fdecea;border-color:#d94136"></i><span>多源冲突（需人工裁决采纳）</span></div>
                    <div><i style="background:#eef4ff;border-color:#2e6be6"></i><span>人工修订（可一键撤销回原值）</span></div>
                    <div><i style="background:#e7f7f4;border-color:#148a7a"></i><span>图纸框选（已绑定 PDF 坐标）</span></div>
                  </div>
                </div>
                <button type="button" class="tizi-toggle-btn" id="tiziExpandBtn">${T.tableExpanded ? '退全屏' : '全屏'}</button>
                <button type="button" class="primary" id="tiziQuickExportBtn">导出院标提资单</button>
              </div>
            </div>
            ${missingHtml}
            <div class="tizi-table-scroll">
              ${td.view_mode === 'form'
                ? basicInfoFormHTML(columns, rows)
                : `<table class="tizi-table"><thead>${headHtml}</thead><tbody>${bodyRows || `<tr><td colspan="${columns.length + 1}" class="empty">${td.missing_source ? escHtml(td.missing_source) : '当前筛选条件下无记录'}</td></tr>`}</tbody></table>`}
            </div>
            <div class="table-footer">
              <span>显示 ${rows.length} / ${td.filtered_total} 行（总 ${td.total} 行） · 第 ${T.tablePage + 1}/${pages} 页</span>
              <div>
                <button type="button" id="tiziPrevPage" ${T.tablePage <= 0 ? 'disabled' : ''}>← 上页</button>
                <button type="button" id="tiziNextPage" ${T.tablePage >= pages - 1 ? 'disabled' : ''}>下页 →</button>
              </div>
            </div>
          </div>
          ${T.splitPdfMode ? '' : `<div id="tiziEvidenceHost">${T.evidenceHTML()}</div>`}
        </div>
        ${T.splitPdfMode ? `<div class="tizi-split-resizer" id="tiziSplitResizer" title="左右拖动调整对照区宽度"></div><div class="tizi-split-right" id="tiziSplitPdfHost" style="--tizi-pdf-w:${T.splitPdfRatio}%">${T.splitPdfHTML()}</div>` : ''}
      </div>
      ${sheetBarHTML()}
    </div>`;
  }

  function deliveryHTML() {
    const wbChecks = T.statusData?.workbook_checks || [];
    const checks = wbChecks.length ? wbChecks : (T.statusData?.checks || []);
    const gantries = T.statusData?.excluded_gantries || [];
    const ex = T.statusData?.exceptions || { pending: [], checks: [], unextractable: [] };
    const revs = T.statusData?.revisions || [];
    const exportsList = T.statusData?.exports || [];

    if (!T.hasRows()) {
      return `<div class="card empty"><div class="empty-icon">▤</div><h3>尚无校验与交付结果</h3><p>请先在中间对话栏选择工作空间并完成解析。</p><button type="button" class="primary" id="tiziGoDiscover">查看资料与探查 →</button></div>`;
    }

    const checkRows = checks.map(c => {
      const badgeHtml = c.ok
        ? '<span class="badge good">通过</span>'
        : (c.kind === 'incomplete'
            ? `<span class="badge warn" title="因部分塔基或基础型号尚缺基础参数导致统计不完整">${c.coverage != null ? `部分覆盖 (${Math.round(c.coverage * 100)}%)` : '数据待补'}</span>`
            : '<span class="badge bad">超差</span>');
      return `<tr>
      <td><span class="tag">${escHtml(c.code || 'CHK')}</span> <b>${escHtml(c.name)}</b>${c.note ? `<small style="display:block;color:#7a827d;font-size:11px">${escHtml(c.note)}</small>` : ''}</td>
      <td>${c.actual} ${escHtml(c.unit || '')}</td>
      <td>${c.expect} ${escHtml(c.unit || '')}</td>
      <td>${c.diff > 0 ? '+' : ''}${c.diff} ${escHtml(c.unit || '')}</td>
      <td>${(c.rate * 100).toFixed(2)}%（容差 ${(c.tolerance * 100).toFixed(0)}%）</td>
      <td>${badgeHtml}</td>
    </tr>`;
    }).join('');

    const gantryBanner = gantries.length
      ? `<div class="notice info" style="margin-bottom:10px;font-size:12px">已自动剥离 <b>${gantries.length}</b> 基变电站门型构架（${escHtml(gantries.map(g => `${g.tower_id}/${g.tower_type}`).join('、'))}），不混入线路杆塔基数。</div>`
      : '';

    const exportCards = exportsList.length
      ? `<div class="excel-cards">${exportsList.map(f => `
          <a class="excel-card" href="/api/tizi/download?file=${encodeURIComponent(f.name)}" download="${escHtml(f.name)}" style="text-decoration:none;color:inherit">
            <span class="excel-symbol">${f.name.endsWith('.xlsx') ? 'XLSX' : (f.name.endsWith('.csv') ? 'CSV' : 'MD')}</span>
            <span><strong>${escHtml(f.name)}</strong><small>${escHtml(f.desc)} · ${(f.size / 1024).toFixed(1)} KB</small></span>
            <span class="button">↓ 下载</span>
          </a>`).join('')}</div>`
      : '<p class="hint">尚未生成全部交付包，请先在中间审核卡片中确认放行，或点击右上角按钮导出。</p>';

    const pendRows = (ex.pending || []).slice(0, 80).map(p => `
      <div class="tizi-ex-row">
        <div><b>${p.sheet ? `《${escHtml(p.sheet)}》` : ''}第 ${p.row + 1} 行 · ${escHtml(p.tower)} · ${escHtml(p.column)}</b><small>${escHtml(p.note)}</small></div>
        <button type="button" class="button" data-jump-sheet="${escHtml(p.sheet || '杆塔明细表')}" data-jump-row="${p.row}" data-jump-col="${escHtml(p.column)}">去复核 →</button>
      </div>`).join('');

    const unextRows = (ex.unextractable || []).slice(0, 40).map(u => `
      <div class="tizi-ex-row">
        <div><b>${escHtml(u.name)}</b><small>${escHtml(u.kind)} · ${u.pages || '?'} 页 · ${escHtml(u.reason)}</small></div>
        ${u.rel ? `<button type="button" class="button" data-open-pdf="${escHtml(u.rel)}">查看图纸</button>` : ''}
      </div>`).join('');

    const revRows = revs.map(r => `<tr>
      <td>${r.line_no}</td><td>${escHtml(r.tower)}</td><td>${escHtml(r.column)}</td>
      <td>${escHtml(r.old ?? '空')}</td><td><b>${escHtml(r.value ?? '空')}</b></td>
      <td><span class="badge basic">${escHtml(r.kind)}</span></td><td>${escHtml(r.time)}</td>
      <td><button type="button" class="link-button" data-rev-undo-row="${r.row}" data-rev-undo-col="${escHtml(r.column)}">撤销</button></td>
    </tr>`).join('');

    const hitl = T.statusData?.hitl_review || {};
    const p0Cnt = Number(hitl.p0_count || 0), p1Cnt = Number(hitl.p1_count || 0);
    const hitlNoticeHtml = (T.tiziSummary().status === 'paused_hitl' || p0Cnt > 0 || p1Cnt > 0)
      ? `<div class="notice info" style="font-size:12px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <span>当前处于审核阶段（P0 冲突 ${p0Cnt} 项 · P1 视觉 ${p1Cnt} 项）。请在中间对话审核卡片中确认放行，确认后将自动生成交付包。</span>
          <button type="button" class="button" id="tiziBackToChatFromDeliveryBtn" style="font-size:11.5px;padding:3px 9px">← 回到中间审核卡片</button>
        </div>`
      : '';

    return `<div class="tizi-delivery-grid">
      ${hitlNoticeHtml}
      <div class="card table-card">
        <div class="card-head"><h3>全工程双源交叉校验</h3><span>超差如实保留，绝不静默凑平</span></div>
        ${gantryBanner}
        <div class="table-scroll"><table><thead><tr><th>校验项</th><th>提取实得</th><th>独立基准</th><th>差值</th><th>偏差率 / 容差</th><th>结论</th></tr></thead><tbody>${checkRows || '<tr><td colspan="6" class="empty">暂无校验项</td></tr>'}</tbody></table></div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>院标双轨提资单成果</h3>
          <div class="match-switch">
            <a class="button" href="/api/tizi/export?mode=standard" download="tizi_standard.xlsx" style="text-decoration:none">↓ 标准导入版</a>
            <a class="button" href="/api/tizi/export?mode=audit" download="tizi_audit.xlsx" style="text-decoration:none">↓ 复核溯源版</a>
            <button type="button" class="button" id="tiziExportBtn">重新导出全部文件</button>
          </div>
        </div>
        <p class="hint" style="margin:0 0 8px">标准导入版原位保留母版 13 个 Sheet、公式与下拉校验，直接供下游软件导入；复核溯源版附加底色、逐格出处批注与审计页。</p>
        ${exportCards}
      </div>

      <div class="card">
        <div class="card-head"><h3>例外与待复核队列</h3><span>待复核 ${(ex.pending || []).length} 格 · 不可自动提取 ${(ex.unextractable || []).length} 份</span></div>
        <div class="tizi-ex-list">${pendRows || '<p class="hint">当前没有待复核单元格。</p>'}</div>
        ${unextRows ? `<details style="margin-top:10px"><summary>查看不可自动提取图纸（${(ex.unextractable || []).length} 份）</summary><div class="tizi-ex-list">${unextRows}</div></details>` : ''}
      </div>

      <div class="card table-card">
        <div class="card-head"><h3>人工修订清单</h3><span>共 ${revs.length} 条</span></div>
        <div class="table-scroll"><table><thead><tr><th>行号</th><th>塔号</th><th>字段</th><th>机器原值</th><th>现值</th><th>类型</th><th>时间</th><th></th></tr></thead><tbody>${revRows || '<tr><td colspan="8" class="empty">尚无人工修订记录</td></tr>'}</tbody></table></div>
      </div>
    </div>`;
  }

  T.updateSelectionUI = function () {
    if (!T.paneEl || T.activeTab !== 'table') return;
    T.paneEl.querySelectorAll('.tizi-table td.tizi-selected, .tizi-form-field.tizi-selected').forEach(el => {
      el.classList.remove('tizi-selected');
    });
    if (T.selectedCell && T.selectedCell.sheet === T.activeSheet) {
      const sel = T.paneEl.querySelector(
        `.tizi-table td[data-row="${T.selectedCell.row}"][data-col="${CSS.escape(T.selectedCell.column)}"], .tizi-form-field[data-row="${T.selectedCell.row}"][data-col="${CSS.escape(T.selectedCell.column)}"]`
      );
      if (sel) sel.classList.add('tizi-selected');
    }
    if (T.splitPdfMode) {
      const host = T.paneEl.querySelector('#tiziSplitPdfHost');
      if (host) {
        host.innerHTML = T.splitPdfHTML();
        T.bindEvidenceEvents(host);
      }
    } else {
      const host = T.paneEl.querySelector('#tiziEvidenceHost');
      if (host) {
        host.innerHTML = T.evidenceHTML();
        T.bindEvidenceEvents(host);
      }
    }
  };

  function renderMappingDialog() {
    if (!T.mapData) return;
    const d = $('tiziMappingDialog');
    d.querySelector('#tiziMapCfgTag').textContent = T.mapData.cfg_mode === 'auto' ? '自动探查结果' : '手工模板配置';
    const staleEl = d.querySelector('#tiziMapStale');
    if (T.mapData.stale?.length) {
      staleEl.hidden = false;
      staleEl.textContent = '以下人工配对在本次探查中找不到对应源列：' + T.mapData.stale.map(s => `${s.target}（原列：${s.label}）`).join('、');
    } else staleEl.hidden = true;

    const q = T.mapSearch.trim().toLowerCase();
    const fields = (T.mapData.fields || []).filter(f => {
      if (q && !`${f.target} ${f.from_label}`.toLowerCase().includes(q)) return false;
      if (T.mapFilter === 'needs') return f.state === 'auto' || f.state === 'unmapped';
      if (T.mapFilter === 'unmapped') return f.state === 'unmapped';
      if (T.mapFilter === 'manual') return f.state === 'manual';
      if (T.mapFilter === 'excluded') return f.state === 'excluded';
      return true;
    });
    d.querySelector('#tiziMapFieldCount').textContent = `${fields.length} 项`;
    d.querySelector('#tiziMapFields').innerHTML = fields.map(f => {
      const [lbl, cls] = MAP_STATE_CN[f.state] || [f.state, ''];
      return `<button type="button" class="tizi-map-item${T.mapSelected === f.target ? ' selected' : ''}" data-mtgt="${escHtml(f.target)}">
        <span><b>${escHtml(f.target)}</b><small>${f.from_label ? '← ' + escHtml(f.from_label) : (f.method || '未配源列')} · ${f.filled}/${f.total}</small></span>
        <span class="badge ${cls}">${lbl}</span>
      </button>`;
    }).join('') || '<div class="empty">无匹配字段</div>';
    d.querySelectorAll('[data-mtgt]').forEach(b => { b.onclick = () => { T.mapSelected = b.dataset.mtgt; renderMappingDialog(); }; });

    const cur = (T.mapData.fields || []).find(f => f.target === T.mapSelected);
    const cols = (T.mapData.columns || []).filter(c => !q || `${c.label} ${c.key}`.toLowerCase().includes(q));
    d.querySelector('#tiziMapColCount').textContent = `${cols.length} 列`;
    d.querySelector('#tiziMapCols').innerHTML = cols.map(c => `
      <button type="button" class="tizi-map-item${cur?.from === c.key ? ' selected' : ''}" data-mcol="${escHtml(c.key)}" ${cur && cur.editable ? '' : 'disabled'}>
        <span><b>${escHtml(c.label)}</b> <small>${escHtml(c.key)} · 样例：${escHtml((c.samples || []).join(' / ') || '空')}</small></span>
        <span class="tag">${c.used_by?.length ? '用于 ' + escHtml(c.used_by.join(',')) : '未使用'}</span>
      </button>`).join('');
    d.querySelectorAll('[data-mcol]').forEach(b => {
      b.onclick = () => { if (cur?.editable) doMappingAction({ action: 'set', target: cur.target, key: b.dataset.mcol }); };
    });

    const detail = d.querySelector('#tiziMapDetail');
    if (!cur) { detail.textContent = '左侧选一个字段，查看来源、样例值与可选操作。'; return; }
    const [lbl, cls] = MAP_STATE_CN[cur.state] || [cur.state, ''];
    const suggHtml = (cur.suggestions || []).length
      ? `<div>候选建议：${cur.suggestions.map(s => `<button type="button" class="tizi-sugg-chip${s.score < 0.9 ? ' fuzzy' : ''}" data-msug="${escHtml(s.key)}">${escHtml(s.label)} (${Math.round(s.score * 100)}%)</button>`).join('')}</div>`
      : '';
    const btns = !cur.editable
      ? '<span class="muted">派生或规则补全字段不可直接改源列（会被规则覆盖）。</span>'
      : [
          cur.state === 'auto' ? '<button type="button" class="primary" data-mact="confirm">确认这个配对</button>' : '',
          cur.state === 'manual' || cur.state === 'confirmed' ? '<button type="button" class="button" data-mact="unset">回到机器结论</button>' : '',
          cur.state !== 'excluded' ? '<button type="button" class="button" data-mact="exclude">标为本工程无此数据</button>' : '<button type="button" class="button" data-mact="include">取消排除</button>',
        ].join(' ');
    detail.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
      <div><b>${escHtml(cur.target)}</b> <span class="badge ${cls}">${lbl}</span> · 样例：${escHtml((cur.samples || []).join('、') || '空')}</div>
      <div class="match-switch">${btns}</div>
    </div>${suggHtml}`;
    detail.querySelectorAll('[data-mact]').forEach(b => { b.onclick = () => doMappingAction({ action: b.dataset.mact, target: cur.target }); });
    detail.querySelectorAll('[data-msug]').forEach(b => { b.onclick = () => doMappingAction({ action: 'set', target: cur.target, key: b.dataset.msug }); });
  }

  async function doMappingAction(body) {
    try {
      const res = await T.tiziPost('mapping', body);
      T.mapData = res.mapping;
      toast(res.message || '配对已更新');
      renderMappingDialog();
      await T.loadStatus(true);
    } catch (e) { toast(e.message); }
  }

  async function openMappingDialog() {
    try {
      T.mapData = await T.tiziGet('mapping');
      T.mapFilter = 'all'; T.mapSearch = '';
      if (!T.mapSelected && T.mapData.fields?.length) T.mapSelected = T.mapData.fields[0].target;
      const d = $('tiziMappingDialog');
      d.querySelector('#tiziMapSearch').value = '';
      d.querySelectorAll('[data-mf]').forEach(x => x.setAttribute('aria-pressed', String(x.dataset.mf === 'all')));
      renderMappingDialog();
      d.showModal();
    } catch (e) { toast(e.message); }
  }

  T.ensureDialogs = function () {
    if (!$('tiziMappingDialog')) {
      const d = document.createElement('dialog');
      d.id = 'tiziMappingDialog';
      d.className = 'tizi-dialog tizi-mapping-dialog';
      d.innerHTML = `
        <div class="dialog-head">
          <h2>字段配对 · 确认与纠正 <span class="tag" id="tiziMapCfgTag">自动探查</span></h2>
          <button type="button" aria-label="关闭" onclick="document.getElementById('tiziMappingDialog').close()">×</button>
        </div>
        <div id="tiziMapStale" class="error" hidden></div>
        <div class="tizi-map-bar">
          <input id="tiziMapSearch" placeholder="搜索字段名 / 源表列名…">
          <div class="match-switch" id="tiziMapFilters">
            <button type="button" class="button" data-mf="all" aria-pressed="true">全部</button>
            <button type="button" class="button" data-mf="needs" aria-pressed="false">待确认</button>
            <button type="button" class="button" data-mf="unmapped" aria-pressed="false">未配对</button>
            <button type="button" class="button" data-mf="manual" aria-pressed="false">已改配</button>
            <button type="button" class="button" data-mf="excluded" aria-pressed="false">已排除</button>
          </div>
          <span style="flex:1"></span>
          <button type="button" class="button" id="tiziMapConfirmAll">确认全部自动配对</button>
          <button type="button" class="button" id="tiziMapReset">恢复自动配对</button>
        </div>
        <div class="tizi-map-cols">
          <div class="tizi-map-col"><div class="tizi-map-col-head"><span>提资单模板字段</span><span id="tiziMapFieldCount"></span></div><div class="tizi-map-list" id="tiziMapFields"></div></div>
          <div class="tizi-map-col"><div class="tizi-map-col-head"><span>源表列（点选即改配）</span><span id="tiziMapColCount"></span></div><div class="tizi-map-list" id="tiziMapCols"></div></div>
        </div>
        <div class="tizi-map-detail" id="tiziMapDetail">左侧选一个字段，查看来源、样例值与可选操作。</div>
      `;
      document.body.append(d);
      d.querySelector('#tiziMapSearch').oninput = e => { T.mapSearch = e.target.value; renderMappingDialog(); };
      d.querySelectorAll('[data-mf]').forEach(b => {
        b.onclick = () => {
          T.mapFilter = b.dataset.mf;
          d.querySelectorAll('[data-mf]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
          renderMappingDialog();
        };
      });
      d.querySelector('#tiziMapConfirmAll').onclick = () => doMappingAction({ action: 'confirm_all' });
      d.querySelector('#tiziMapReset').onclick = () => { if (confirm('清除全部人工配对判断并恢复机器自动配对？')) doMappingAction({ action: 'reset' }); };
    }

    if (!$('tiziDrawingDialog')) {
      const d = document.createElement('dialog');
      d.id = 'tiziDrawingDialog';
      d.className = 'tizi-dialog tizi-drawing-dialog';
      d.innerHTML = `
        <div class="dialog-head">
          <h2>图纸查看与框选取值 <span class="tag" id="tiziDrawTargetTag">未选中目标格</span></h2>
          <button type="button" aria-label="关闭" onclick="document.getElementById('tiziDrawingDialog').close()">×</button>
        </div>
        <div class="tizi-draw-bar">
          <select id="tiziDrawSelect" aria-label="选择施工图 PDF"></select>
          <input id="tiziDrawFileSearch" placeholder="过滤文件名…">
          <button type="button" class="button" id="tiziDrawPrev">上一页</button>
          <span id="tiziDrawPageLabel" style="font-size:12px;color:#555">—</span>
          <button type="button" class="button" id="tiziDrawNext">下一页</button>
          <button type="button" class="button" id="tiziDrawOut">−</button>
          <span id="tiziDrawZoomLbl" style="font-size:12px;min-width:42px;text-align:center">100%</span>
          <button type="button" class="button" id="tiziDrawIn">＋</button>
          <button type="button" class="button" id="tiziDrawFit">适宽</button>
          <button type="button" class="button" id="tiziDraw100">1:1</button>
          <span class="tag" id="tiziDrawCapTag"></span>
        </div>
        <div class="tizi-draw-stage" id="tiziDrawStage">
          <div class="tizi-draw-paper" id="tiziDrawPaper">
            <img id="tiziDrawImg" alt="图纸原页">
            <div class="tizi-draw-hl" id="tiziDrawHl" hidden></div>
            <div class="tizi-draw-sel" id="tiziDrawSel" hidden></div>
          </div>
        </div>
        <div class="tizi-draw-form">
          <span>框内文字层：<b class="tizi-draw-text" id="tiziDrawText">在图上按住左键拖框选择数值</b></span>
          <label style="margin:0;display:flex;align-items:center;gap:6px">写入值 <input id="tiziDrawValue" placeholder="照图输入或由文字层自动带出"></label>
          <span class="muted" id="tiziDrawBbox" style="font-size:11px"></span>
          <span style="flex:1"></span>
          <button type="button" class="button" id="tiziDrawOcrBtn" disabled title="对无文字层的框选区域调用本机离线 OCR 识字（候选值仍需人工确认）">本机离线 OCR</button>
          <button type="button" class="primary" id="tiziDrawApply" disabled>写入这一格</button>
        </div>
      `;
      document.body.append(d);
      if (typeof T.bindDrawingDialogEvents === 'function') T.bindDrawingDialogEvents(d);
    }
  };

  function bindPaneEvents() {
    const paneEl = T.paneEl;
    if (!paneEl) return;
    paneEl.querySelector('#tiziBackToChatFromDeliveryBtn')?.addEventListener('click', () => {
      if (typeof togglePanel === 'function') togglePanel(false);
      T.streamHostEl?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
    paneEl.querySelectorAll('[data-tizi-step]').forEach(b => {
      b.onclick = () => T.runTizi([b.dataset.tiziStep]);
    });

    paneEl.querySelector('#tiziGoDiscover')?.addEventListener('click', () => T.switchTiziTab('discover', true));
    paneEl.querySelector('#tiziFilterPendingMetric')?.addEventListener('click', () => { T.tableFilter = 'pending'; T.tablePage = 0; T.loadTable(); });
    paneEl.querySelector('#tiziGoDeliveryMetric')?.addEventListener('click', () => T.switchTiziTab('delivery', true));
    paneEl.querySelector('#tiziToggleDefaultsBtn')?.addEventListener('click', () => { T.showDefaults = !T.showDefaults; T.loadTable(); });
    paneEl.querySelector('#tiziSplitBtn')?.addEventListener('click', () => { T.splitPdfMode = !T.splitPdfMode; T.renderPane(); });
    paneEl.querySelector('#tiziFocusWideBtn')?.addEventListener('click', () => { T.focusWideMode = !T.focusWideMode; T.applyVisibility(); T.renderPane(); });
    paneEl.querySelector('#tiziNextPendingBtn')?.addEventListener('click', T.jumpNextPending);
    paneEl.querySelector('#tiziOpenMappingBtn')?.addEventListener('click', openMappingDialog);
    paneEl.querySelector('#tiziPriceMatchBtn')?.addEventListener('click', async () => {
      try {
        const res = await T.tiziPost('price-match', { sheet: T.activeSheet });
        toast(`价格库只读匹配完成：命中 ${res.matched}/${res.total_rows} 条（参考价标黄待确认，库内共 ${res.price_pool_size} 条）`);
        await T.loadStatus(true);
      } catch (e) { toast(e.message); }
    });
    paneEl.querySelector('#tiziQuickExportBtn')?.addEventListener('click', async () => { await T.runExport(); T.switchTiziTab('delivery', true); });
    paneEl.querySelector('#tiziExportBtn')?.addEventListener('click', T.runExport);
    paneEl.querySelector('#tiziExpandBtn')?.addEventListener('click', () => { T.tableExpanded = !T.tableExpanded; T.renderPane(); });

    paneEl.querySelectorAll('[data-tizi-sheet]').forEach(b => {
      b.onclick = () => T.switchSheet(b.dataset.tiziSheet);
    });
    const sheetBar = paneEl.querySelector('.tizi-sheet-bar');
    paneEl.querySelector('#tiziSheetScrollLeft')?.addEventListener('click', () => {
      sheetBar?.scrollBy({ left: -240, behavior: 'smooth' });
    });
    paneEl.querySelector('#tiziSheetScrollRight')?.addEventListener('click', () => {
      sheetBar?.scrollBy({ left: 240, behavior: 'smooth' });
    });

    const resizer = paneEl.querySelector('#tiziSplitResizer');
    if (resizer) {
      resizer.onpointerdown = e => {
        const splitBox = paneEl.querySelector('.tizi-split');
        const rightHost = paneEl.querySelector('#tiziSplitPdfHost');
        if (!splitBox || !rightHost) return;
        e.preventDefault();
        resizer.classList.add('dragging');
        resizer.setPointerCapture(e.pointerId);
        const onMove = mv => {
          const rect = splitBox.getBoundingClientRect();
          if (rect.width <= 0) return;
          const rightPx = rect.right - mv.clientX;
          T.splitPdfRatio = Math.max(25, Math.min(65, Math.round((rightPx / rect.width) * 100)));
          rightHost.style.setProperty('--tizi-pdf-w', `${T.splitPdfRatio}%`);
        };
        const onUp = () => {
          resizer.classList.remove('dragging');
          resizer.removeEventListener('pointermove', onMove);
          resizer.removeEventListener('pointerup', onUp);
          resizer.removeEventListener('pointercancel', onUp);
        };
        resizer.addEventListener('pointermove', onMove);
        resizer.addEventListener('pointerup', onUp);
        resizer.addEventListener('pointercancel', onUp);
      };
    }

    paneEl.querySelectorAll('[data-tizi-flt]').forEach(b => {
      b.onclick = () => { T.tableFilter = b.dataset.tiziFlt; T.tablePage = 0; T.loadTable(); };
    });
    const searchInput = paneEl.querySelector('#tiziTableSearch');
    if (searchInput) {
      searchInput.onkeydown = e => { if (e.key === 'Enter') { T.tableSearch = searchInput.value; T.tablePage = 0; T.loadTable(); } };
      searchInput.onchange = () => { T.tableSearch = searchInput.value; T.tablePage = 0; T.loadTable(); };
    }
    paneEl.querySelector('#tiziPrevPage')?.addEventListener('click', () => { T.tablePage = Math.max(0, T.tablePage - 1); T.loadTable(); });
    paneEl.querySelector('#tiziNextPage')?.addEventListener('click', () => { T.tablePage += 1; T.loadTable(); });

    paneEl.querySelectorAll('.tizi-table td[data-row], .tizi-form-field[data-row]').forEach(td => {
      const r = Number(td.dataset.row), col = td.dataset.col;
      td.onclick = () => {
        if (T.editingCellKey) return;
        const rowObj = (T.tableData?.rows || []).find(x => x.row === r);
        const tower = rowObj?.cells?.['杆塔号']?.value || rowObj?.cells?.['基础型号']?.value || rowObj?.cells?.['线路段名称']?.value || rowObj?.row_id || '';
        T.selectedCell = { sheet: T.activeSheet, row: r, column: col, tower, cell: rowObj?.cells?.[col] };
        T.activeEvIndex = 0;
        T.updateSelectionUI();
      };
      td.ondblclick = () => {
        const rowObj = (T.tableData?.rows || []).find(x => x.row === r);
        const tower = rowObj?.cells?.['杆塔号']?.value || rowObj?.cells?.['基础型号']?.value || rowObj?.cells?.['线路段名称']?.value || rowObj?.row_id || '';
        T.selectedCell = { sheet: T.activeSheet, row: r, column: col, tower, cell: rowObj?.cells?.[col] };
        T.activeEvIndex = 0;
        T.editingCellKey = `${r}::${col}`;
        T.renderPane();
        const inp = paneEl.querySelector('.tizi-inline-edit');
        if (inp) { inp.focus(); inp.select(); }
      };
    });

    paneEl.querySelectorAll('[data-adopt-row]').forEach(btn => {
      btn.onclick = e => {
        e.stopPropagation();
        T.saveCellEdit(Number(btn.dataset.adoptRow), btn.dataset.adoptCol, btn.dataset.adoptVal, null, 'confirm');
      };
    });

    const editInput = paneEl.querySelector('.tizi-inline-edit');
    if (editInput) {
      let handled = false;
      const r = Number(editInput.dataset.editRow), col = editInput.dataset.editCol;
      editInput.onclick = e => e.stopPropagation();
      editInput.onkeydown = e => {
        if (e.key === 'Enter') { e.preventDefault(); handled = true; T.saveCellEdit(r, col, editInput.value); }
        else if (e.key === 'Escape') { e.preventDefault(); handled = true; T.editingCellKey = ''; T.renderPane(); }
      };
      editInput.onblur = () => {
        if (!handled) { T.editingCellKey = ''; T.renderPane(); }
      };
    }

    if (typeof T.bindEvidenceEvents === 'function') T.bindEvidenceEvents(paneEl);

    paneEl.querySelectorAll('[data-jump-row]').forEach(b => {
      b.onclick = () => {
        const sheet = b.dataset.jumpSheet || '杆塔明细表';
        const r = Number(b.dataset.jumpRow), col = b.dataset.jumpCol;
        T.jumpToSheetCell(sheet, r, col, false);
      };
    });
    paneEl.querySelectorAll('[data-open-pdf]').forEach(b => {
      b.onclick = () => T.openDrawingDialog(false, b.dataset.openPdf);
    });
    paneEl.querySelectorAll('[data-rev-undo-row]').forEach(b => {
      b.onclick = () => T.revertEdits(Number(b.dataset.revUndoRow), b.dataset.revUndoCol);
    });
  }

  T.renderPane = function () {
    const paneEl = T.paneEl;
    if (!paneEl || view !== 'drawing') return;
    if (T.editingCellKey && document.activeElement?.classList?.contains('tizi-inline-edit')) return;
    const prevScroll = paneEl.querySelector('.tizi-table-scroll');
    const savedTop = prevScroll ? prevScroll.scrollTop : 0;
    const savedLeft = prevScroll ? prevScroll.scrollLeft : 0;
    const savedPaneTop = paneEl.scrollTop;

    if (T.activeTab === 'discover') paneEl.innerHTML = discoverHTML();
    else if (T.activeTab === 'table') paneEl.innerHTML = tableHTML();
    else paneEl.innerHTML = deliveryHTML();
    bindPaneEvents();

    const nextScroll = paneEl.querySelector('.tizi-table-scroll');
    if (nextScroll && T.activeTab === 'table') {
      nextScroll.scrollTop = savedTop;
      nextScroll.scrollLeft = savedLeft;
    }
    paneEl.scrollTop = savedPaneTop;
  };
})();
