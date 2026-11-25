// 图纸识别与提资工作台 · 模块入口、状态总线与平台钩子（PowerCostingAI）
// 默认仅展开中间对话栏（togglePanel(false)），由对话驱动工作空间绑定、流式解析、内联审核与双轨输出；
// 右侧结果画布作为纯 Inspector 按需滑出。
(function () {
  const STEP_META = [
    { id: 'discover', name: '目录探查' },
    { id: 'carrier', name: '载体判定' },
    { id: 'parse', name: '表格解析' },
    { id: 'mapping', name: '字段映射' },
    { id: 'rules', name: '规则补全' },
    { id: 'validate', name: '双源校验' },
  ];
  const METHOD_CN = {
    extract: '图纸直取', derive: '规则推导', lookup: '查表',
    interval: '区间插值', constant: '统一赋值', manual: '人工修订', drawing_select: '图纸框选',
    multi_join_lookup: '多源关联查表', cross_doc_extract: '跨册提取',
    std_lib_lookup: '标准基础库', price_ref: '价格库参考', rollup: '分项汇总',
    vlm_visual_read: '视觉高清重读', vlm_learned: '跨院视觉学习',
  };
  const KIND_CN = { table: '表格', drawing: '图纸（矢量）', doc: '文档', scan: '扫描件', other: '其它' };
  const MAP_STATE_CN = {
    auto: ['自动·待确认', 'warn'],
    unmapped: ['未配对', 'bad'],
    manual: ['人工改配', 'good'],
    confirmed: ['已确认', 'good'],
    derived: ['派生（只读）', 'basic'],
    rule: ['规则（只读）', 'basic'],
    excluded: ['已排除', ''],
  };
  const STREAM_SHEETS_ORDER = [
    { name: '基本信息', short: '基本信息', stage: 'workbook_basic' },
    { name: '杆塔明细表', short: '杆塔明细', stage: 'workbook_tower_mat' },
    { name: '基础明细表', short: '基础明细', stage: 'workbook_str_fdn' },
    { name: '基础库', short: '基础库', stage: 'workbook_str_fdn' },
    { name: '串型汇总表', short: '串型汇总', stage: 'workbook_str_fdn' },
    { name: '勘测信息一览表', short: '勘测一览', stage: 'workbook_str_fdn' },
    { name: '结构设备材料表', short: '结构材料', stage: 'workbook_tower_mat' },
    { name: '电气设备材料表', short: '电气材料', stage: 'workbook_tower_mat' },
    { name: '光缆设备材料表', short: '光缆材料', stage: 'workbook_tower_mat' },
  ];

  function freshStreamState() {
    return {
      runId: '',
      status: 'idle',            // idle | running | ready | error
      error: '',
      startedAtMs: 0,
      elapsedMs: 0,
      lastSeq: 0,
      isFull: true,
      totalPages: 0,
      steps: [],
      sheets: {},
      workbookSummary: null,
      traceCollapsed: false,
      userToggledStages: {},
      userScrolledAway: false,
    };
  }

  const escHtml = v => typeof esc === 'function'
    ? esc(v)
    : String(v ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

  const T = window.__TIZI__ = {
    STEP_META,
    METHOD_CN,
    KIND_CN,
    MAP_STATE_CN,
    STREAM_SHEETS_ORDER,
    TABLE_LIMIT: 100,
    escHtml,
    freshStreamState,

    wbEl: null,
    paneEl: null,
    tabBarEl: null,
    streamHostEl: null,

    activeTab: 'discover',        // discover | table | delivery
    activeSheet: '杆塔明细表',
    showDefaults: false,
    splitPdfMode: false,
    splitFocusCrop: true,
    focusWideMode: false,
    splitPdfRatio: 44,
    activeEvIndex: 0,
    tabChosenByUser: false,
    statusData: null,
    tableData: null,
    tableFilter: 'all',
    tableSearch: '',
    tablePage: 0,
    tableExpanded: false,
    selectedCell: null,
    editingCellKey: '',
    lastSessionId: '',
    initialViewSynced: false,
    pollTimer: null,
    wasRunning: false,
    folderInputDraft: '',

    streamState: freshStreamState(),
    streamES: null,
    streamClockTimer: null,
    streamRafId: 0,
    hitlSubmitting: false,
    hitlPromoteMem: false,

    mapData: null,
    mapFilter: 'all',
    mapSearch: '',
    mapSelected: '',

    drawFiles: [],
    drawRel: '',
    drawPage: 0,
    drawProbe: null,
    drawZoom: 1,
    drawNaturalW: 0,
    drawNaturalH: 0,
    drawPdfSize: [842, 595],
    drawTarget: null,
    drawHighlight: null,
    drawSelBbox: null,
    drawRegion: null,
    drawImgLoading: false,
  };

  T.tiziGet = async function (path) {
    const r = await fetch('/api/tizi/' + path);
    if (r.status === 401) { location.href = '/login'; throw Error('请先登录'); }
    const d = await r.json();
    if (!r.ok) throw Error(d.error || '请求失败');
    return d;
  };

  T.tiziPost = async function (path, body = {}) {
    const r = await fetch('/api/tizi/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Agent-Demo': '1' },
      body: JSON.stringify(body),
    });
    if (r.status === 401) { location.href = '/login'; throw Error('请先登录'); }
    const d = await r.json();
    if (!r.ok) throw Error(d.error || '操作失败');
    return d;
  };

  T.tiziSummary = function () {
    return T.statusData?.tizi || state?.tizi || { status: 'idle', folder: '', summary: {}, workbook_summary: {} };
  };

  T.workbookSummary = function () {
    const tz = T.tiziSummary();
    const wb = tz.workbook_summary || {};
    const sm = tz.summary || {};
    return {
      towers: wb.towers != null ? wb.towers : (sm.rows || 0),
      sheets_ready: wb.sheets_ready != null ? wb.sheets_ready : (sm.rows ? 1 : 0),
      pending: wb.pending != null ? wb.pending : (sm.pending || 0),
      checks_ok: wb.checks_ok != null ? wb.checks_ok : (sm.checks_ok || 0),
      checks_total: wb.checks_total != null ? wb.checks_total : (sm.checks_total || 0),
      exports: wb.exports != null ? wb.exports : (sm.exports || 0),
    };
  };

  T.isRunning = () => T.tiziSummary().status === 'running';
  T.hasRows = () => Number(T.tiziSummary().summary?.rows || 0) > 0;

  T.ensureWorkbench = function () {
    if (T.wbEl) return T.wbEl;
    const oldView = $('drawingView');
    if (!oldView || !oldView.parentElement) return null;
    T.wbEl = document.createElement('div');
    T.wbEl.id = 'drawingWorkbench';
    T.wbEl.className = 'tizi-wb';
    T.wbEl.hidden = true;

    T.tabBarEl = document.createElement('div');
    T.tabBarEl.className = 'tabs tizi-tabs';
    T.tabBarEl.setAttribute('role', 'tablist');

    T.paneEl = document.createElement('div');
    T.paneEl.id = 'tiziPane';
    T.paneEl.className = 'tizi-pane';

    oldView.parentElement.insertBefore(T.wbEl, oldView);
    T.wbEl.append(T.tabBarEl, T.paneEl, oldView);
    if (typeof T.ensureDialogs === 'function') T.ensureDialogs();
    return T.wbEl;
  };

  T.renderTabs = function () {
    if (!T.tabBarEl) return;
    const tz = T.tiziSummary(), wb = T.workbookSummary();
    const badChecks = Math.max(0, (wb.checks_total || 0) - (wb.checks_ok || 0));
    const tabs = [
      { id: 'discover', label: '资料与探查', badge: tz.folder && !wb.towers ? '已选目录' : '' },
      { id: 'table', label: '提资明细表', badge: wb.towers ? `${wb.towers} 基 · ${wb.sheets_ready}表` : '', warn: wb.pending > 0 },
      { id: 'delivery', label: '校验与导出', badge: badChecks > 0 ? `${badChecks} 项提醒` : (wb.exports ? `${wb.exports} 文件` : (wb.checks_total ? `${wb.checks_ok}/${wb.checks_total}` : '')), warn: badChecks > 0 },
    ];
    T.tabBarEl.innerHTML = tabs.map(t =>
      `<button type="button" role="tab" aria-selected="${T.activeTab === t.id}" class="${T.activeTab === t.id ? 'selected' : ''}" data-tizi-tab="${t.id}">${t.label}${t.badge ? `<span class="tizi-tab-pill ${t.warn ? 'warn' : ''}">${escHtml(t.badge)}</span>` : ''}</button>`
    ).join('');
    T.tabBarEl.querySelectorAll('[data-tizi-tab]').forEach(btn => {
      btn.onclick = () => T.switchTiziTab(btn.dataset.tiziTab, true);
    });
  };

  T.switchTiziTab = async function (tab, userTriggered = false) {
    if (userTriggered) T.tabChosenByUser = true;
    T.activeTab = tab === 'vision' ? 'discover' : tab;
    T.applyVisibility();
    T.renderTabs();
    if (T.activeTab === 'table' && T.hasRows() && !T.tableData) {
      await T.loadTable();
    }
    T.renderPane();
  };

  T.applyVisibility = function () {
    T.ensureWorkbench();
    if (!T.wbEl) return;
    const onDrawing = view === 'drawing';
    document.body.classList.toggle('tizi-view', onDrawing);
    document.body.classList.toggle('tizi-focus-wide', onDrawing && T.activeTab === 'table' && T.focusWideMode);
    T.wbEl.hidden = !onDrawing;
    if (typeof T.attachStreamHost === 'function') T.attachStreamHost();
    if (!onDrawing) return;
    T.paneEl.hidden = false;
    if ($('drawingView')) $('drawingView').hidden = true;
    if ($('runStatus')) {
      const tz = T.tiziSummary();
      const map = { idle: '等待施工图', running: '提资解析中…', paused_hitl: '待人工审核', ready: '已就绪', error: '执行异常' };
      $('runStatus').textContent = map[tz.status] || '图纸提资';
    }
  };

  T.loadStatus = async function (alsoTable = false) {
    try {
      const data = await T.tiziGet('status');
      T.statusData = data;
      if (typeof T.hydrateStreamFromTrace === 'function') T.hydrateStreamFromTrace(data.stream_trace);
      const runningNow = data.tizi?.status === 'running';
      if (!T.tabChosenByUser && !runningNow) {
        T.activeTab = Number(data.tizi?.summary?.rows || 0) > 0 ? 'table' : 'discover';
      }
      if (T.wasRunning && !runningNow) {
        T.wasRunning = false;
        if (data.tizi?.status === 'ready' && Number(data.tizi?.summary?.rows || 0) > 0) {
          T.activeTab = 'table';
          T.tabChosenByUser = true;
          await T.loadTable();
          if (typeof refresh === 'function') refresh(true);
        }
      } else if (runningNow) {
        T.wasRunning = true;
      }
      if (alsoTable && Number(data.tizi?.summary?.rows || 0) > 0) {
        await T.loadTable();
      }
      T.schedulePoll();
      T.renderTabs();
      T.applyVisibility();
      if (typeof T.renderStreamCard === 'function') T.renderStreamCard(false);
      if (typeof syncWorkspaceLabel === 'function') syncWorkspaceLabel();
      if (view === 'drawing') T.renderPane();
      renderTaskSteps();
    } catch {}
  };

  T.loadTable = async function () {
    try {
      const params = new URLSearchParams({
        sheet: T.activeSheet,
        show_defaults: T.showDefaults ? '1' : '0',
        offset: String(T.tablePage * T.TABLE_LIMIT),
        limit: String(T.TABLE_LIMIT),
        filter: T.tableFilter,
        q: T.tableSearch,
      });
      T.tableData = await T.tiziGet('table?' + params.toString());
      if (T.selectedCell && T.selectedCell.sheet === T.activeSheet) {
        const hitRow = (T.tableData.rows || []).find(r => r.row === T.selectedCell.row);
        if (hitRow && hitRow.cells[T.selectedCell.column]) {
          T.selectedCell.cell = hitRow.cells[T.selectedCell.column];
        }
      }
      if (view === 'drawing' && T.activeTab === 'table') T.renderPane();
    } catch (e) {
      toast(e.message);
    }
  };

  T.switchSheet = async function (sheetName) {
    if (!sheetName || sheetName === T.activeSheet) return;
    T.activeSheet = sheetName;
    T.tableFilter = 'all';
    T.tableSearch = '';
    T.tablePage = 0;
    T.selectedCell = null;
    T.editingCellKey = '';
    T.activeEvIndex = 0;
    await T.loadTable();
  };

  T.schedulePoll = function () {
    clearTimeout(T.pollTimer);
    if (T.isRunning() || T.streamState.status === 'running') {
      const gap = T.streamES && T.streamES.readyState === 1 ? 2500 : 450;
      T.pollTimer = setTimeout(() => T.loadStatus(false), gap);
    }
  };

  T.openTiziFolder = async function (folderPath) {
    const p = String(folderPath || '').trim();
    if (!p) return toast('请先输入施工图目录路径。');
    try {
      await T.tiziPost('open', { folder: p });
      T.folderInputDraft = p;
      T.tableData = null;
      T.selectedCell = null;
      T.tablePage = 0;
      toast('已绑定施工图目录，可开始扫描解析。');
      if (typeof refresh === 'function') await refresh(true);
      await T.loadStatus(false);
    } catch (e) {
      toast(e.message);
    }
  };

  T.runTizi = async function (steps = 'extract', confirmFlag = false) {
    const tz = T.tiziSummary();
    if (!tz.folder) {
      toast('请先在中间栏下方选择或绑定施工图目录。');
      return;
    }
    try {
      const res = await T.tiziPost('run', { steps, confirm: confirmFlag, notify_chat: steps === 'extract' || steps === 'all' });
      if (res.need_confirm) {
        if (confirm(res.message || '重新解析会清空当前人工修订，确认继续？')) {
          return T.runTizi(steps, true);
        }
        return;
      }
      T.wasRunning = true;
      T.streamState = freshStreamState();
      T.streamState.runId = res.run_id || `run-${Date.now()}`;
      T.streamState.status = 'running';
      T.streamState.startedAtMs = Date.now();
      T.streamState.isFull = steps === 'extract' || steps === 'all';
      T.streamState.userScrolledAway = false;
      if (typeof T.renderStreamCard === 'function') T.renderStreamCard(true);
      if (typeof T.connectTiziStream === 'function') T.connectTiziStream(0);
      toast('已开始提资解析，中间栏实时流式反馈中…');
      T.schedulePoll();
    } catch (e) {
      toast(e.message);
    }
  };

  T.saveCellEdit = async function (row, column, value, flag = null, action = 'edit') {
    T.editingCellKey = '';
    try {
      const res = await T.tiziPost('cell', { sheet: T.activeSheet, row, column, value, flag, action });
      if (res.cell) {
        T.selectedCell = {
          sheet: T.activeSheet, row, column, tower: res.cell.tower,
          cell: { ...res.cell.prov, value: res.cell.value, status: res.cell.status, revised: res.cell.revised, old: res.cell.old },
        };
      }
      const msg = action === 'confirm' ? '已确认无误（标绿）'
        : (flag === 'pending' ? '已标为待复核（原值与坐标保留）' : (res.changed ? '已保存人工修订' : '值未变化'));
      toast(msg);
      await T.loadStatus(true);
    } catch (e) {
      toast(e.message);
      T.renderPane();
    }
  };

  T.revertEdits = async function (row = null, column = null) {
    if (row === null && !confirm(`确定撤销《${T.activeSheet}》全部人工修订并恢复机器原值？`)) return;
    try {
      const body = row === null ? { sheet: T.activeSheet } : { sheet: T.activeSheet, row, column };
      const res = await T.tiziPost('cell/revert', body);
      toast(`已撤销 ${res.reverted} 处人工修订`);
      await T.loadStatus(true);
      if (T.selectedCell && T.selectedCell.sheet === T.activeSheet) {
        try {
          const c = await T.tiziGet(`cell?sheet=${encodeURIComponent(T.activeSheet)}&row=${T.selectedCell.row}&column=${encodeURIComponent(T.selectedCell.column)}`);
          T.selectedCell.cell = { ...c.prov, value: c.value, status: c.status, revised: c.revised, old: c.old };
        } catch {}
      }
      T.renderPane();
    } catch (e) {
      toast(e.message);
    }
  };

  T.jumpNextPending = function () {
    const rows = T.tableData?.rows || [];
    const cols = (T.tableData?.columns || []).map(c => c.key);
    if (!rows.length || !cols.length) return toast('当前页无数据行');
    const items = [];
    for (const r of rows) {
      for (const col of cols) {
        const st = r.cells?.[col]?.status;
        if (st === 'pending' || st === 'conflict') {
          items.push({ row: r.row, column: col, rowObj: r });
        }
      }
    }
    if (!items.length) return toast('当前页已无待复核或冲突单元格');
    let nextIdx = 0;
    if (T.selectedCell && T.selectedCell.sheet === T.activeSheet) {
      const curIdx = items.findIndex(x => x.row === T.selectedCell.row && x.column === T.selectedCell.column);
      nextIdx = curIdx >= 0 ? (curIdx + 1) % items.length : 0;
    }
    const target = items[nextIdx];
    const anchor = target.rowObj.cells?.['杆塔号']?.value || target.rowObj.cells?.['基础型号']?.value || target.rowObj.cells?.['线路段名称']?.value || target.rowObj.row_id || '';
    T.selectedCell = { sheet: T.activeSheet, row: target.row, column: target.column, tower: anchor, cell: target.rowObj.cells[target.column] };
    T.activeEvIndex = 0;
    T.updateSelectionUI();
    const tdEl = T.paneEl?.querySelector(
      `.tizi-table td[data-row="${target.row}"][data-col="${CSS.escape(target.column)}"], .tizi-form-field[data-row="${target.row}"][data-col="${CSS.escape(target.column)}"]`
    );
    tdEl?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  };

  T.runExport = async function () {
    try {
      toast('正在导出院标双轨提资单（标准导入版 + 复核溯源版）与修订清单…');
      const res = await T.tiziPost('export', { notify_chat: true });
      const warn = (res.warnings || []).length ? `（提示：${res.warnings.join('；')}）` : '';
      toast(`院标双轨与单表成果已生成（共 ${(res.exports || []).length} 个文件）${warn}`);
      await T.loadStatus(false);
      if (typeof refresh === 'function') refresh(true);
    } catch (e) {
      toast(e.message);
    }
  };

  T.jumpToSheetCell = async function (sheetName, rowIdx = null, colKey = '', openSplit = false) {
    if (view !== 'drawing') switchView('drawing');
    if (typeof togglePanel === 'function') togglePanel(true);
    T.activeTab = 'table';
    T.tabChosenByUser = true;
    if (openSplit) T.splitPdfMode = true;
    T.applyVisibility();
    T.renderTabs();
    if (sheetName && sheetName !== T.activeSheet) {
      T.activeSheet = sheetName;
      T.tableFilter = 'all';
      T.tableSearch = '';
      T.tablePage = 0;
      T.selectedCell = null;
      T.editingCellKey = '';
      T.activeEvIndex = 0;
    }
    if (rowIdx !== null && rowIdx !== undefined && rowIdx !== '') {
      const rNum = Number(rowIdx);
      if (!Number.isNaN(rNum)) T.tablePage = Math.floor(rNum / T.TABLE_LIMIT);
    }
    await T.loadTable();
    const rows = T.tableData?.rows || [];
    let targetRow = null;
    if (rowIdx !== null && rowIdx !== undefined && rowIdx !== '') {
      targetRow = rows.find(x => x.row === Number(rowIdx));
    }
    if (!targetRow && rows.length && openSplit) {
      targetRow = rows[0];
    }
    if (targetRow) {
      const col = colKey || (T.tableData?.columns?.[1]?.key || T.tableData?.columns?.[0]?.key || '');
      if (col && targetRow.cells?.[col]) {
        const anchor = targetRow.cells?.['杆塔号']?.value || targetRow.cells?.['基础型号']?.value || targetRow.cells?.['线路段名称']?.value || targetRow.row_id || '';
        T.selectedCell = { sheet: T.activeSheet, row: targetRow.row, column: col, tower: anchor, cell: targetRow.cells[col] };
        T.activeEvIndex = 0;
        T.renderPane();
        const el = T.paneEl?.querySelector(
          `.tizi-table td[data-row="${targetRow.row}"][data-col="${CSS.escape(col)}"], .tizi-form-field[data-row="${targetRow.row}"][data-col="${CSS.escape(col)}"]`
        );
        el?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
      }
    }
  };

  // ---------- 接入 PowerCostingAI 装饰器链
  renderDrawing = function () {
    T.ensureWorkbench();
    T.applyVisibility();
    T.renderTabs();
    T.renderPane();
  };

  const prevSwitchView = switchView;
  switchView = function (v) {
    prevSwitchView(v);
    if (v === 'drawing') {
      if (typeof togglePanel === 'function') togglePanel(false);
      $('viewTitle').textContent = '图纸识别与提资';
      $('canvasTitle').textContent = '图纸提资结果';
      if (typeof TASK_TYPES !== 'undefined' && TASK_TYPES.drawing) {
        TASK_TYPES.drawing.placeholder = '输入指令，如「开始提资」「有哪些待复核」「确认并导出」…';
      }
      if (taskType !== 'drawing') { taskType = 'drawing'; pendingStep = ''; renderTaskUI(); }
      if ($('chatInput')) $('chatInput').placeholder = '输入指令，如「开始提资」「有哪些待复核」「确认并导出」…';
      T.ensureWorkbench();
      T.applyVisibility();
      T.loadStatus(true).then(() => {
        if (typeof syncWorkspaceLabel === 'function') syncWorkspaceLabel();
      });
    } else {
      document.body.classList.remove('tizi-view', 'tizi-focus-wide');
      if (T.wbEl) T.wbEl.hidden = true;
      if ($('chatInput')) $('chatInput').placeholder = '向造价助手发送消息，输入任务或修改要求…';
      if (typeof T.attachStreamHost === 'function') T.attachStreamHost();
      if (taskType === 'drawing') { taskType = 'review'; renderTaskUI(); }
      if (typeof syncWorkspaceLabel === 'function') syncWorkspaceLabel();
    }
  };

  if (typeof selectEngineeringFolder === 'function') {
    const prevSelectFolder = selectEngineeringFolder;
    selectEngineeringFolder = async function (path) {
      if (view === 'drawing') {
        selectedFolder = path;
        recentFolders = [path, ...recentFolders.filter(p => folderKey(p) !== folderKey(path))].slice(0, 20);
        removedFolders = removedFolders.filter(p => p !== folderKey(path));
        saveFolderRecords();
        $('selectedFolderLabel').textContent = folderName(path);
        $('folderTrigger').title = path;
        $('cleanSelectedFolder').textContent = '开始智能体提资 →';
        $('cleanSelectedFolder').hidden = false;
        closeFolderChoices(true);
        await T.openTiziFolder(path);
        return;
      }
      return prevSelectFolder(path);
    };
  }

  if (typeof cleanSelectedFolder === 'function') {
    const prevCleanSelected = cleanSelectedFolder;
    cleanSelectedFolder = async function () {
      if (view === 'drawing') {
        if (!selectedFolder) return openFolder();
        await sendText('开始提资');
        return;
      }
      return prevCleanSelected();
    };
  }

  if (typeof openFolder === 'function') {
    const prevOpenFolder = openFolder;
    openFolder = function () {
      const keepDrawing = (view === 'drawing');
      prevOpenFolder();
      if (keepDrawing && typeof showConversation === 'function') {
        const panel = $('folderPopover');
        document.body.classList.add('tizi-view');
        if (T.wbEl) T.wbEl.hidden = false;
        if (panel && !panel.hidden) $('folderTrigger').setAttribute('aria-expanded', 'true');
      }
    };
  }

  if (typeof syncWorkspaceLabel === 'function') {
    const prevSyncLabel = syncWorkspaceLabel;
    syncWorkspaceLabel = function () {
      if (view === 'drawing') {
        const path = T.tiziSummary().folder || currentWorkspace() || selectedFolder;
        if (path) {
          selectedFolder = path;
          $('selectedFolderLabel').textContent = folderName(path);
          $('folderTrigger').title = path;
          $('cleanSelectedFolder').textContent = '开始智能体提资 →';
          $('cleanSelectedFolder').hidden = false;
        } else {
          $('selectedFolderLabel').textContent = '选择工作空间';
          $('cleanSelectedFolder').hidden = true;
        }
        return;
      }
      if ($('cleanSelectedFolder')) $('cleanSelectedFolder').textContent = '开始清洗 →';
      return prevSyncLabel();
    };
  }

  if (typeof sendText === 'function') {
    const prevSendText = sendText;
    sendText = async function (t, refs = []) {
      if (view === 'drawing') {
        const txt = String(t || '').trim();
        if (!txt) return;
        try {
          const res = await T.tiziPost('chat', { text: txt });
          if (res.stream && typeof T.connectTiziStream === 'function') {
            T.connectTiziStream(0);
          }
          if (typeof refresh === 'function') await refresh(true);
          await T.loadStatus(true);
          const chatEl = $('chat');
          if (chatEl) chatEl.scrollTop = chatEl.scrollHeight;
        } catch (err) {
          toast(err.message);
        }
        return;
      }
      return prevSendText(t, refs);
    };
  }

  if (typeof renderConversation === 'function') {
    const prevRenderConversation = renderConversation;
    renderConversation = function () {
      prevRenderConversation();
      if (typeof T.attachStreamHost === 'function') T.attachStreamHost();
    };
  }

  const prevRenderCanvas = renderCanvas;
  renderCanvas = function () {
    T.ensureWorkbench();
    if (view === 'drawing') {
      ['quantityView', 'databaseView', 'knowledgeView', 'earthworkView'].forEach(id => { if ($(id)) $(id).hidden = true; });
      T.applyVisibility();
      T.renderTabs();
      T.renderPane();
      return;
    }
    document.body.classList.remove('tizi-view', 'tizi-focus-wide');
    if (T.wbEl) T.wbEl.hidden = true;
    prevRenderCanvas();
  };

  const prevTaskSteps = renderTaskSteps;
  renderTaskSteps = function () {
    if (taskType !== 'drawing') return prevTaskSteps();
    const box = $('taskSteps'); if (!box) return;
    box.hidden = false; box.replaceChildren();
    const wb = T.workbookSummary();
    const steps = [
      {
        name: '扫描与解析',
        status: wb.towers > 0 ? 'done' : 'ready',
        title: '查看已绑定目录与载体判定结果',
        run: () => { if (view !== 'drawing') switchView('drawing'); if (typeof togglePanel === 'function') togglePanel(true); T.switchTiziTab('discover', true); },
      },
      {
        name: '复核与校对',
        status: !wb.towers ? 'blocked' : (wb.pending === 0 ? 'done' : 'ready'),
        title: '在右侧提资明细表核查待复核格与同屏对照原图',
        run: () => { if (!wb.towers) return toast('请先在中间栏选择工作空间并发送「开始提资」。'); if (view !== 'drawing') switchView('drawing'); if (typeof togglePanel === 'function') togglePanel(true); T.switchTiziTab('table', true); },
      },
      {
        name: '校验与导出',
        status: !wb.towers ? 'blocked' : (wb.exports > 0 ? 'done' : 'ready'),
        title: '查看全工程双源交叉校验与双轨交付单',
        run: () => { if (!wb.towers) return toast('请先在中间栏选择工作空间并发送「开始提资」。'); if (view !== 'drawing') switchView('drawing'); if (typeof togglePanel === 'function') togglePanel(true); T.switchTiziTab('delivery', true); },
      },
    ];
    steps.forEach((step, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `task-step ${step.status}${T.isRunning() && i === 0 ? ' running' : ''}`;
      btn.title = step.title;
      btn.innerHTML = `<em>${step.status === 'done' ? '✓' : i + 1}</em>${step.name}`;
      btn.onclick = step.run;
      if (i) box.append(Object.assign(document.createElement('span'), { className: 'task-step-line' }));
      box.append(btn);
    });
  };

  const prevNewTask = newTask;
  newTask = async function () {
    if (T.isRunning()) return toast('杆塔提资正在解析中，请完成后再新建任务。');
    await prevNewTask();
    document.body.classList.remove('tizi-view', 'tizi-focus-wide');
    if (T.wbEl) T.wbEl.hidden = true;
    if (typeof T.closeTiziStream === 'function') T.closeTiziStream();
    T.streamState = freshStreamState();
    if (typeof T.renderStreamCard === 'function') T.renderStreamCard(false);
    T.statusData = null; T.tableData = null; T.selectedCell = null; T.tabChosenByUser = false; T.activeTab = 'discover'; T.focusWideMode = false;
  };

  const prevRender = render;
  render = function () {
    prevRender();
    if (!T.initialViewSynced && state) {
      T.initialViewSynced = true;
      if (state.session_kind === 'drawing' || (!state.dataset && state.tizi?.folder)) {
        if (view !== 'drawing') switchView('drawing');
      }
    }
    if (typeof T.attachStreamHost === 'function') T.attachStreamHost();
    const sid = state?.session_id || '';
    if (sid && sid !== T.lastSessionId) {
      T.lastSessionId = sid;
      if (typeof T.closeTiziStream === 'function') T.closeTiziStream();
      T.streamState = freshStreamState();
      if (typeof T.renderStreamCard === 'function') T.renderStreamCard(false);
      T.statusData = null; T.tableData = null; T.selectedCell = null; T.tabChosenByUser = false;
      if (view === 'drawing') T.loadStatus(true);
    }
    if (state?.tizi?.status === 'running' && !T.pollTimer) {
      T.schedulePoll();
    }
  };

  document.addEventListener('keydown', e => {
    if (view !== 'drawing' || T.activeTab !== 'table' || T.editingCellKey) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (document.querySelector('dialog[open]')) return;
    if (e.altKey && e.key === 'ArrowDown') {
      e.preventDefault();
      T.jumpNextPending();
    } else if (e.key === ' ' && T.selectedCell && T.selectedCell.sheet === T.activeSheet) {
      e.preventDefault();
      T.saveCellEdit(T.selectedCell.row, T.selectedCell.column, null, null, 'confirm');
    }
  });
})();
