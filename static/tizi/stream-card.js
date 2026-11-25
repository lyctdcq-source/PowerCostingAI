// 中栏流式 Live Trace 引擎（SSE + 60fps 局部刷新 + 智能贴底 + 9表实时点亮）
(function () {
  const T = window.__TIZI__;
  if (!T) return;
  const { escHtml, STREAM_SHEETS_ORDER } = T;

  T.closeTiziStream = function () {
    if (T.streamES) {
      try { T.streamES.close(); } catch {}
      T.streamES = null;
    }
    if (T.streamClockTimer) {
      clearInterval(T.streamClockTimer);
      T.streamClockTimer = null;
    }
  };

  function formatElapsed(ms) {
    const s = Math.max(0, Number(ms || 0)) / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, '0')}:${(s % 60).toFixed(1).padStart(4, '0')}`;
  }

  function startStreamClock() {
    if (T.streamClockTimer) return;
    T.streamClockTimer = setInterval(() => {
      if (T.streamState.status !== 'running') {
        clearInterval(T.streamClockTimer);
        T.streamClockTimer = null;
        return;
      }
      const el = T.streamHostEl?.querySelector('.tizi-sc-timer');
      if (el) {
        const ms = T.streamState.startedAtMs ? (Date.now() - T.streamState.startedAtMs) : T.streamState.elapsedMs;
        el.textContent = formatElapsed(Math.max(ms, T.streamState.elapsedMs));
      }
    }, 100);
  }

  function ensureStreamStep(stageId, title = '') {
    let st = T.streamState.steps.find(x => x.id === stageId);
    if (!st) {
      st = {
        id: stageId, title: title || stageId,
        status: 'pending', summary: '', durationMs: 0,
        ticker: '', findings: [], checks: [],
      };
      T.streamState.steps.push(st);
    } else if (title) {
      st.title = title;
    }
    return st;
  }

  function applyStreamEvent(ev, isReplay = false) {
    if (!ev || typeof ev !== 'object') return;
    const seq = Number(ev.seq || 0);
    if (seq && seq <= T.streamState.lastSeq) return;
    if (seq) T.streamState.lastSeq = seq;
    if (ev.run_id && !T.streamState.runId) T.streamState.runId = ev.run_id;
    if (typeof ev.elapsed_ms === 'number' && ev.elapsed_ms >= T.streamState.elapsedMs) {
      T.streamState.elapsedMs = ev.elapsed_ms;
    }
    const kind = ev.kind;
    if (kind === 'run_start') {
      T.streamState = T.freshStreamState();
      T.streamState.runId = ev.run_id || `run-${Date.now()}`;
      T.streamState.status = 'running';
      T.streamState.startedAtMs = isReplay ? 0 : Date.now();
      T.streamState.lastSeq = seq;
      T.streamState.isFull = ev.is_full !== false;
      T.streamState.steps = (ev.steps_plan || []).map(p => ({
        id: p.id, title: p.title, status: 'pending',
        summary: '', durationMs: 0, ticker: '', findings: [], checks: [],
      }));
      if (!isReplay) startStreamClock();
    } else if (kind === 'stage_start') {
      T.streamState.status = 'running';
      const st = ensureStreamStep(ev.stage, ev.title);
      st.status = 'running';
      if (!st.ticker) st.ticker = `正在执行：${st.title}…`;
    } else if (kind === 'ticker') {
      const st = ensureStreamStep(ev.stage || 'parse');
      if (st.status === 'pending') st.status = 'running';
      st.ticker = String(ev.text || '');
    } else if (kind === 'finding') {
      const st = ensureStreamStep(ev.stage || 'workbook_tower_mat');
      st.findings.push({
        level: ev.level || 'info',
        text: String(ev.text || ''),
        sheet: ev.sheet || '',
        row: ev.row,
        col: ev.col || '',
      });
    } else if (kind === 'sheet_ready') {
      const sname = ev.sheet;
      if (sname) {
        T.streamState.sheets[sname] = {
          name: sname,
          short_title: ev.short_title || sname,
          rows: Number(ev.rows || 0),
          pending: Number(ev.pending || 0),
          conflict: Number(ev.conflict || 0),
          coverage: Number(ev.coverage || 0),
          missing_source: ev.missing_source || '',
        };
        if (!isReplay && T.statusData) {
          const list = Array.isArray(T.statusData.sheets) ? T.statusData.sheets : [];
          const idx = list.findIndex(x => x.name === sname);
          const merged = { ...(idx >= 0 ? list[idx] : {}), ...T.streamState.sheets[sname] };
          if (idx >= 0) list[idx] = merged; else list.push(merged);
          T.statusData.sheets = list;
          if (T.statusData.tizi) {
            T.statusData.tizi.workbook_summary = {
              ...(T.statusData.tizi.workbook_summary || {}),
              towers: sname === '杆塔明细表' ? merged.rows : (T.statusData.tizi.workbook_summary?.towers || 0),
              sheets_ready: list.filter(x => x.rows > 0).length,
            };
          }
          T.renderTabs();
        }
      }
    } else if (kind === 'check_result') {
      const st = ensureStreamStep(ev.stage || 'validate', '全工程双源交叉校验');
      if (st.status === 'pending') st.status = 'running';
      const idx = st.checks.findIndex(x => x.code === ev.code);
      const item = {
        code: ev.code || 'CHK', name: ev.name || '',
        actual: ev.actual, expect: ev.expect, unit: ev.unit || '',
        rate: Number(ev.rate || 0), ok: Boolean(ev.ok),
        kind: ev.check_kind || ev.kind_label || 'normal', coverage: ev.coverage,
        note: ev.note || '',
      };
      if (idx >= 0) st.checks[idx] = item; else st.checks.push(item);
    } else if (kind === 'stage_done') {
      const st = ensureStreamStep(ev.stage);
      st.status = 'done';
      st.summary = String(ev.summary || '完成');
      st.durationMs = Number(ev.duration_ms || 0);
      if (ev.metrics?.pages) T.streamState.totalPages = Number(ev.metrics.pages);
    } else if (kind === 'run_done') {
      T.streamState.status = 'ready';
      T.streamState.workbookSummary = ev.workbook_summary || T.streamState.workbookSummary;
      if (Array.isArray(ev.sheets)) {
        ev.sheets.forEach(s => {
          T.streamState.sheets[s.name] = {
            name: s.name, short_title: s.short_title || s.name,
            rows: Number(s.rows || 0), pending: Number(s.pending || 0),
            conflict: Number(s.conflict || 0), coverage: Number(s.coverage || 0),
            missing_source: s.missing_source || '',
          };
        });
      }
      T.streamState.steps.forEach(st => { if (st.status === 'running') st.status = 'done'; });
      T.streamState.traceCollapsed = true;
      T.closeTiziStream();
    } else if (kind === 'run_error') {
      T.streamState.status = 'error';
      T.streamState.error = String(ev.error || '解析异常');
      const cur = T.streamState.steps.find(st => st.status === 'running');
      if (cur) cur.status = 'err';
      T.streamState.traceCollapsed = false;
      T.closeTiziStream();
    }
  }

  T.hydrateStreamFromTrace = function (trace) {
    if (!trace || !trace.run_id || !Array.isArray(trace.events) || !trace.events.length) {
      if (T.streamState.status !== 'running' && !trace?.running) {
        T.streamState = T.freshStreamState();
        T.renderStreamCard(false);
      }
      return;
    }
    const needReset = trace.run_id !== T.streamState.runId;
    if (needReset) {
      T.streamState = T.freshStreamState();
      T.streamState.runId = trace.run_id;
      T.streamState.steps = (trace.steps_plan || []).map(p => ({
        id: p.id, title: p.title, status: 'pending',
        summary: '', durationMs: 0, ticker: '', findings: [], checks: [],
      }));
    }
    for (const ev of trace.events) {
      applyStreamEvent(ev, true);
    }
    if (trace.running) {
      T.streamState.status = 'running';
      T.streamState.traceCollapsed = false;
      if (!T.streamState.startedAtMs) {
        T.streamState.startedAtMs = Date.now() - Number(trace.elapsed_ms || 0);
      }
      startStreamClock();
      if (!T.streamES) T.connectTiziStream(T.streamState.lastSeq);
    } else {
      if (T.streamState.status === 'running') {
        T.streamState.status = trace.status === 'error' ? 'error' : 'ready';
      }
      if (trace.elapsed_ms) T.streamState.elapsedMs = Math.max(T.streamState.elapsedMs, Number(trace.elapsed_ms));
      if (needReset && T.streamState.status === 'ready') T.streamState.traceCollapsed = true;
    }
    T.renderStreamCard(false);
  };

  function scheduleStreamRender() {
    if (T.streamRafId) return;
    T.streamRafId = requestAnimationFrame(() => {
      T.streamRafId = 0;
      T.renderStreamCard(true);
    });
  }

  T.connectTiziStream = function (sinceSeq = 0) {
    T.closeTiziStream();
    if (typeof EventSource === 'undefined') {
      T.schedulePoll();
      return;
    }
    const url = `/api/tizi/stream?since=${Math.max(0, Number(sinceSeq) || 0)}`;
    const es = new EventSource(url);
    T.streamES = es;
    startStreamClock();

    const onEv = e => {
      try {
        const ev = JSON.parse(e.data);
        applyStreamEvent(ev, false);
        scheduleStreamRender();
        if (ev.kind === 'run_done' || ev.kind === 'run_error') {
          T.wasRunning = false;
          T.closeTiziStream();
          T.loadStatus(true).then(() => {
            if (typeof refresh === 'function') refresh(true);
          });
        }
      } catch {}
    };
    ['run_start', 'stage_start', 'ticker', 'finding', 'sheet_ready', 'check_result', 'stage_done', 'run_done', 'run_error']
      .forEach(k => es.addEventListener(k, onEv));
    es.addEventListener('stream_end', () => {
      T.closeTiziStream();
      if (T.wasRunning) {
        T.wasRunning = false;
        T.loadStatus(true);
      }
    });
    es.onerror = () => {
      T.closeTiziStream();
      if (T.streamState.status === 'running' || T.isRunning()) {
        T.schedulePoll();
      }
    };
  };

  function ensureStreamHost() {
    if (!T.streamHostEl) {
      T.streamHostEl = document.createElement('div');
      T.streamHostEl.id = 'tiziStreamHost';
      T.streamHostEl.className = 'tizi-stream-host';
      T.streamHostEl.hidden = true;
    }
    const chat = $('chat');
    if (chat && !chat.dataset.tiziScrollBound) {
      chat.dataset.tiziScrollBound = '1';
      chat.addEventListener('scroll', () => {
        const nearBottom = (chat.scrollHeight - chat.scrollTop - chat.clientHeight) < 48;
        T.streamState.userScrolledAway = !nearBottom;
        const btn = T.streamHostEl?.querySelector('#tiziScJumpBottom');
        if (btn) btn.hidden = nearBottom || T.streamState.status !== 'running';
      }, { passive: true });
    }
    return T.streamHostEl;
  }

  function shouldShowStreamCard() {
    if (view !== 'drawing') return false;
    const tz = T.tiziSummary();
    return Boolean(
      T.streamState.runId
      || tz.status === 'paused_hitl'
      || (T.statusData?.exports || []).length
      || T.hitlSubmitting
    );
  }

  T.attachStreamHost = function () {
    const host = ensureStreamHost();
    const chat = $('chat');
    const show = shouldShowStreamCard();
    host.hidden = !show;
    if (chat && show && (host.parentElement !== chat || chat.lastElementChild !== host)) {
      chat.append(host);
    }
  };

  function computeStreamProgress() {
    if (T.streamState.status === 'ready') return 100;
    const total = Math.max(1, T.streamState.steps.length);
    let score = 0;
    for (const st of T.streamState.steps) {
      if (st.status === 'done') score += 1;
      else if (st.status === 'running') score += 0.45;
    }
    return Math.min(98, Math.max(4, Math.round((score / total) * 100)));
  }

  T.renderStreamCard = function (autoScroll = false) {
    const host = ensureStreamHost();
    T.attachStreamHost();
    if (!shouldShowStreamCard()) {
      host.hidden = true;
      return;
    }
    const chat = $('chat');
    const wasNearBottom = chat ? (chat.scrollHeight - chat.scrollTop - chat.clientHeight) < 56 : true;

    const tz = T.tiziSummary();
    const hitl = T.statusData?.hitl_review || {};
    const p0Cnt = Number(hitl.p0_count || 0);
    const p1Cnt = Number(hitl.p1_count || 0);
    const exportsList = T.statusData?.exports || [];

    const isRun = T.streamState.status === 'running' || tz.status === 'running';
    const isErr = !isRun && (T.streamState.status === 'error' || tz.status === 'error');
    const isPausedHitl = !isRun && !isErr && (tz.status === 'paused_hitl' || p0Cnt > 0 || p1Cnt > 0);
    const isExported = !isRun && !isErr && !isPausedHitl && exportsList.length > 0;

    const pct = T.hitlSubmitting ? 96 : (isRun ? computeStreamProgress() : 100);
    const doneCnt = T.streamState.steps.filter(s => s.status === 'done').length;
    const totCnt = Math.max(1, T.streamState.steps.length);
    const activeIdx = T.streamState.steps.findIndex(s => s.status === 'running');
    const stepOrdinal = activeIdx >= 0 ? activeIdx + 1 : Math.min(totCnt, doneCnt + 1);
    const headTitle = isRun
      ? `正在解析施工图 · 第 ${stepOrdinal}/${totCnt} 步`
      : T.hitlSubmitting
        ? '审核已确认 · 正在继续生成院标双轨交付包…'
        : isErr
          ? '提资解析未完成'
          : isPausedHitl
            ? (p0Cnt > 0 ? `等待审核确认 · 检测到 ${p0Cnt} 处冲突` : (p1Cnt > 0 ? `等待审核确认 · ${p1Cnt} 项视觉补全` : '审核已就绪 · 等待确认放行'))
            : isExported
              ? '提资完成 · 院标双轨交付包已输出'
              : (T.streamState.isFull ? '已完成全工作簿提资解析' : '已完成单步重跑解析');
    const dotHtml = (isRun || T.hitlSubmitting)
      ? '<span class="tizi-sc-dot live" aria-hidden="true"></span>'
      : isErr
        ? '<span class="tizi-sc-dot err" aria-hidden="true">!</span>'
        : isPausedHitl
          ? '<span class="tizi-sc-dot paused" aria-hidden="true">⏸</span>'
          : '<span class="tizi-sc-dot done" aria-hidden="true">✓</span>';
    const dispMs = isRun && T.streamState.startedAtMs
      ? Math.max(Date.now() - T.streamState.startedAtMs, T.streamState.elapsedMs)
      : T.streamState.elapsedMs;

    const allFindings = T.streamState.steps.flatMap(s => s.findings || []);
    const hasSteps = T.streamState.steps.length > 0;
    const foldBarHtml = !isRun && hasSteps ? `
      <button type="button" class="tizi-sc-fold-btn" id="tiziScFoldBtn" aria-expanded="${!T.streamState.traceCollapsed}">
        <span>${T.streamState.traceCollapsed ? '▸' : '▾'} 查看解析过程（<b>${totCnt} 步</b>${T.streamState.totalPages ? ` · ${T.streamState.totalPages} 页` : ''}${allFindings.length ? ` · ${allFindings.length} 条发现` : ''}）</span>
        <span>${T.streamState.traceCollapsed ? '展开' : '收起'}</span>
      </button>` : '';

    const stepsHtml = T.streamState.steps.map(st => {
      const icon = st.status === 'done' ? '✓' : st.status === 'err' ? '!' : st.status === 'running' ? '•' : '';
      const manual = T.streamState.userToggledStages[st.id];
      const expanded = manual !== undefined ? Boolean(manual) : (st.status === 'running' || st.status === 'err');
      const hasBody = Boolean(st.ticker || st.findings.length || st.checks.length);
      const findingsHtml = st.findings.map((f, idx) => {
        const ico = f.level === 'conflict' ? '🔴' : f.level === 'warn' ? '⚠' : f.level === 'good' ? '✓' : 'ℹ';
        const clickable = Boolean(f.sheet);
        return `<div class="tizi-sc-finding ${escHtml(f.level)}${clickable ? ' clickable' : ''}" ${clickable ? `data-sc-finding-sheet="${escHtml(f.sheet)}" data-sc-finding-row="${f.row ?? ''}" data-sc-finding-col="${escHtml(f.col || '')}" title="点击打开右侧对应工作表"` : ''} data-fidx="${idx}"><span>${ico}</span><span>${escHtml(f.text)}</span></div>`;
      }).join('');
      const checksHtml = st.checks.length
        ? `<div class="tizi-sc-checks">${st.checks.map(c => {
            const cls = c.ok ? 'ok' : (c.kind === 'incomplete' ? 'warn' : 'bad');
            const stTxt = c.ok ? '通过' : (c.kind === 'incomplete' ? (c.coverage != null ? `覆盖${Math.round(c.coverage * 100)}%` : '待补全') : '超差');
            return `<div class="tizi-sc-chk ${cls}" title="${escHtml(c.note || c.name)}"><b>${c.ok ? '✓' : '!'} ${escHtml(c.code)}</b><span>${escHtml(c.name)}</span><em>${escHtml(stTxt)}</em></div>`;
          }).join('')}</div>`
        : '';
      const bodyHtml = expanded && hasBody
        ? `<div class="tizi-sc-step-body">${st.status === 'running' && st.ticker ? `<div class="tizi-sc-ticker">${escHtml(st.ticker)}</div>` : ''}${findingsHtml}${checksHtml}</div>`
        : '';
      return `<div class="tizi-sc-step ${st.status}">
        <span class="tizi-sc-step-icon">${icon}</span>
        <button type="button" class="tizi-sc-step-row" data-sc-stage="${escHtml(st.id)}" aria-expanded="${expanded}">
          <span class="tizi-sc-step-name">${escHtml(st.title)}</span>
          ${st.summary ? `<span class="tizi-sc-step-sum">${escHtml(st.summary)}</span>` : ''}
          ${st.durationMs ? `<span class="tizi-sc-step-dur">${formatElapsed(st.durationMs)}</span>` : ''}
        </button>
        ${bodyHtml}
      </div>`;
    }).join('');

    const sheetMap = { ...T.streamState.sheets };
    if (Array.isArray(T.statusData?.sheets)) {
      T.statusData.sheets.forEach(s => {
        if (s?.name && (!sheetMap[s.name] || !isRun)) sheetMap[s.name] = s;
      });
    }
    const activeStageId = activeIdx >= 0 ? T.streamState.steps[activeIdx].id : '';
    const hasAnySheet = Object.keys(sheetMap).length > 0;
    const showMatrix = (T.streamState.isFull && hasSteps) || hasAnySheet;
    const readySheetCnt = STREAM_SHEETS_ORDER.filter(s => (sheetMap[s.name]?.rows || 0) > 0).length;
    const matrixHtml = showMatrix ? `
      <div class="tizi-sc-matrix-wrap">
        <div class="tizi-sc-matrix-head">
          <span>提资工作簿实时就绪矩阵（点击任意表在右侧查看）</span>
          <span><b>${readySheetCnt}</b> / ${STREAM_SHEETS_ORDER.length} 表已点亮</span>
        </div>
        <div class="tizi-sc-matrix">${STREAM_SHEETS_ORDER.map(meta => {
          const info = sheetMap[meta.name];
          const isWorking = isRun && !info && activeStageId === meta.stage;
          let cls = '', dot = 'muted', sub = '等待中';
          if (info) {
            if (info.conflict > 0) { cls = 'conflict'; dot = 'bad'; sub = `${info.rows}行 · 冲突${info.conflict}`; }
            else if (info.rows > 0) { cls = info.pending > 0 ? 'warn' : 'ready'; dot = info.pending > 0 ? 'warn' : 'ok'; sub = `${info.rows}行${info.pending ? ` · 待${info.pending}` : ''}`; }
            else { cls = 'warn'; dot = 'muted'; sub = '待补源'; }
          } else if (isWorking) {
            cls = 'active'; dot = 'ok'; sub = '解析中…';
          }
          return `<button type="button" class="tizi-sc-tile ${cls}" ${info ? `data-sc-sheet="${escHtml(meta.name)}"` : 'disabled'} title="${info ? `点击在右侧打开《${meta.name}》` : meta.name}">
            <span class="tizi-sc-tile-top"><i class="tizi-sheet-dot ${dot}"></i><span>${escHtml(meta.short)}</span></span>
            <span class="tizi-sc-tile-cnt">${escHtml(sub)}</span>
          </button>`;
        }).join('')}</div>
      </div>` : '';

    const hitlInlineHtml = (isPausedHitl && typeof T.buildHitlInlineHtml === 'function')
      ? T.buildHitlInlineHtml()
      : '';
    const deliveryInlineHtml = (isExported && typeof T.buildDeliveryInlineHtml === 'function')
      ? T.buildDeliveryInlineHtml(exportsList)
      : '';

    const footHtml = isErr
      ? `<div class="tizi-sc-foot"><span style="font-size:11.5px;color:#a52822">${escHtml(T.streamState.error || tz.error || '执行异常')}</span></div>`
      : (!isRun ? `
        <div class="tizi-sc-foot">
          <button type="button" class="tizi-sc-act" id="tiziScGoSplit">◫ 打开右侧明细表与原图对照</button>
          <button type="button" class="tizi-sc-act" id="tiziScGoDelivery">📊 查看交叉校验报告</button>
        </div>` : '');

    host.hidden = false;
    const cardStateCls = (isRun || T.hitlSubmitting) ? 'running' : isErr ? 'error' : isPausedHitl ? 'paused' : 'ready';
    host.innerHTML = `
      <div class="tizi-sc-card ${cardStateCls}">
        <div class="tizi-sc-head">
          ${dotHtml}
          <span class="tizi-sc-title">${escHtml(headTitle)}</span>
          <span class="tizi-sc-meta">
            ${dispMs ? `<span class="tizi-sc-timer">${formatElapsed(dispMs)}</span>` : ''}
            <span class="tizi-sc-pct">${pct}%</span>
          </span>
        </div>
        <div class="tizi-sc-prog"><div class="tizi-sc-prog-fill" style="width:${pct}%"></div></div>
        ${foldBarHtml}
        ${hasSteps ? `<div class="tizi-sc-steps" ${!isRun && T.streamState.traceCollapsed ? 'hidden' : ''}>${stepsHtml}</div>` : ''}
        ${matrixHtml}
        ${hitlInlineHtml}
        ${deliveryInlineHtml}
        ${footHtml}
      </div>
      <button type="button" class="tizi-sc-jump-bottom" id="tiziScJumpBottom" ${!(isRun && T.streamState.userScrolledAway) ? 'hidden' : ''}>↓ 查看最新解析进度</button>
    `;

    host.querySelector('#tiziScFoldBtn')?.addEventListener('click', () => {
      T.streamState.traceCollapsed = !T.streamState.traceCollapsed;
      T.renderStreamCard(false);
    });
    host.querySelectorAll('[data-sc-stage]').forEach(btn => {
      btn.onclick = () => {
        const sid = btn.dataset.scStage;
        const st = T.streamState.steps.find(x => x.id === sid);
        const cur = T.streamState.userToggledStages[sid] !== undefined
          ? T.streamState.userToggledStages[sid]
          : (st?.status === 'running' || st?.status === 'err');
        T.streamState.userToggledStages[sid] = !cur;
        T.renderStreamCard(false);
      };
    });
    host.querySelectorAll('[data-sc-sheet]').forEach(btn => {
      btn.onclick = () => T.jumpToSheetCell(btn.dataset.scSheet, null, '', false);
    });
    host.querySelectorAll('[data-sc-finding-sheet]').forEach(el => {
      el.onclick = () => T.jumpToSheetCell(
        el.dataset.scFindingSheet,
        el.dataset.scFindingRow !== '' ? Number(el.dataset.scFindingRow) : null,
        el.dataset.scFindingCol || '',
        true,
      );
    });
    if (typeof T.bindHitlInlineEvents === 'function') T.bindHitlInlineEvents(host);

    host.querySelector('#tiziScGoSplit')?.addEventListener('click', () => {
      T.jumpToSheetCell('杆塔明细表', 0, '杆塔型号', true);
    });
    host.querySelector('#tiziScGoDelivery')?.addEventListener('click', () => {
      if (view !== 'drawing') switchView('drawing');
      if (typeof togglePanel === 'function') togglePanel(true);
      T.switchTiziTab('delivery', true);
    });
    host.querySelector('#tiziScOpenDeliveryBtn')?.addEventListener('click', () => {
      if (view !== 'drawing') switchView('drawing');
      if (typeof togglePanel === 'function') togglePanel(true);
      T.switchTiziTab('delivery', true);
    });
    host.querySelector('#tiziScJumpBottom')?.addEventListener('click', () => {
      if (chat) chat.scrollTo({ top: chat.scrollHeight, behavior: 'smooth' });
      T.streamState.userScrolledAway = false;
    });

    if ((autoScroll || T.streamState.status === 'ready') && chat && (wasNearBottom || !T.streamState.userScrolledAway)) {
      chat.scrollTop = chat.scrollHeight;
      requestAnimationFrame(() => {
        chat.scrollTop = chat.scrollHeight;
        if (typeof updateJumpButton === 'function') updateJumpButton();
      });
    } else if (typeof updateJumpButton === 'function') {
      updateJumpButton();
    }
  };
})();
