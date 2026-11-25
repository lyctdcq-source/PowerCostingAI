// 中栏内联 HITL 审核确认卡片 & 交付成果下载卡片
(function () {
  const T = window.__TIZI__;
  if (!T) return;
  const { escHtml } = T;

  T.submitInlineHitlResume = async function (allowDraftSkip = false) {
    if (T.hitlSubmitting) return;
    const hitl = T.statusData?.hitl_review || {};
    const p0Cnt = Number(hitl.p0_count || 0);
    if (p0Cnt > 0) {
      toast(`仍有 ${p0Cnt} 处 P0 冲突待裁决，请先采纳方案或去右侧对照原图调整`);
      return;
    }
    T.hitlSubmitting = true;
    if (typeof T.renderStreamCard === 'function') T.renderStreamCard(true);
    try {
      const p1Items = allowDraftSkip
        ? []
        : (hitl.items || []).filter(x => x.priority === 'P1' && x.row_idx != null);
      const decisions = p1Items.map(x => ({
        sheet: x.sheet,
        row_idx: x.row_idx,
        col_key: x.col_key,
        action: 'confirm',
      }));
      const res = await T.tiziPost('resume', {
        decisions,
        approve_export: true,
        promote_memory: T.hitlPromoteMem,
        allow_draft_skip: allowDraftSkip,
        notify_chat: true,
      });
      const st = res?.graph?.status || '';
      if (st === 'paused_hitl') {
        toast('门禁拦截：仍有未解决的阻断项，请核对后再试');
      } else {
        if (typeof togglePanel === 'function') togglePanel(false);
        toast(allowDraftSkip ? '已跳过未确认 P1 项并输出草稿交付包' : '审核确认通过，双轨提资交付包已输出');
      }
      if (typeof refresh === 'function') await refresh(true);
      await T.loadStatus(true);
    } catch (e) {
      toast(e.message);
    } finally {
      T.hitlSubmitting = false;
      if (typeof T.renderStreamCard === 'function') T.renderStreamCard(true);
    }
  };

  T.buildHitlInlineHtml = function () {
    const hitl = T.statusData?.hitl_review || {};
    const hitlItems = Array.isArray(hitl.items) ? hitl.items : [];
    const hitlGate = hitl.export_gate || null;
    const p0Cnt = Number(hitl.p0_count || 0);
    const p1Cnt = Number(hitl.p1_count || 0);
    const p2Cnt = Number(hitl.p2_count || 0);
    const clearNow = p0Cnt === 0 && p1Cnt === 0;

    const itemsHtml = hitlItems.slice(0, 12).map(it => {
      const isP0 = it.priority === 'P0';
      const cands = Array.isArray(it.conflict_candidates) ? it.conflict_candidates : [];
      const candBtns = cands.map(cd =>
        `<button type="button" class="tizi-sc-act primary" data-sc-adopt-sheet="${escHtml(it.sheet)}" data-sc-adopt-row="${it.row_idx}" data-sc-adopt-col="${escHtml(it.col_key)}" data-sc-adopt-val="${escHtml(cd.value)}">✓ 采纳：${escHtml(cd.label)}</button>`
      ).join('');
      const confirmBtn = (!cands.length && it.row_idx != null)
        ? `<button type="button" class="tizi-sc-act primary" data-sc-confirm-sheet="${escHtml(it.sheet)}" data-sc-confirm-row="${it.row_idx}" data-sc-confirm-col="${escHtml(it.col_key)}">✓ 确认此项</button>`
        : '';
      const inspectBtn = it.row_idx != null
        ? `<button type="button" class="tizi-sc-act" data-sc-inspect-sheet="${escHtml(it.sheet)}" data-sc-inspect-row="${it.row_idx}" data-sc-inspect-col="${escHtml(it.col_key)}">◫ 去右侧对照原图调整 →</button>`
        : '';
      const cropLink = it.crop_uri
        ? `<a class="tizi-sc-act" href="/api/tizi/artifact?uri=${encodeURIComponent(it.crop_uri)}" target="_blank" style="text-decoration:none">查看裁切图 ↗</a>`
        : '';
      return `<div class="tizi-sc-hitl-item ${isP0 ? 'p0' : 'p1'}">
        <div class="tizi-sc-hitl-item-top">
          <div class="tizi-sc-hitl-item-info">
            <span class="tizi-sc-pill ${isP0 ? 'p0' : 'p1'}" style="margin-right:4px">${isP0 ? 'P0 冲突' : 'P1 视觉'}</span>
            <b>《${escHtml(it.sheet)}》${escHtml(it.entity_id || '')} · ${escHtml(it.col_key)}</b> = <code>${escHtml(it.current_value ?? '空')}</code>
            <small>${escHtml(it.reason || '')}${it.calibrated_conf ? ` · 校准置信 ${Math.round(it.calibrated_conf * 100)}%` : ''}</small>
          </div>
        </div>
        <div class="tizi-sc-hitl-item-acts">
          ${candBtns}${confirmBtn}${inspectBtn}${cropLink}
        </div>
      </div>`;
    }).join('');

    const descText = clearNow
      ? '所有阻断冲突与待审项均已核对完成。确认后将继续执行最终校核，并在当前对话中输出院标双轨提资单。'
      : (hitlGate?.message || '请直接在下方采纳或确认，如需核对原始图纸可点击「去右侧对照原图调整」，调整完毕后点击下方确认继续输出。');
    const primaryLabel = T.hitlSubmitting
      ? '● 正在继续生成双轨交付包…'
      : p0Cnt > 0
        ? `请先处理 ${p0Cnt} 处 P0 冲突`
        : (p1Cnt > 0 ? `✓ 确认全部 ${p1Cnt} 项并继续提资输出` : '✓ 确认审核并继续提资输出');

    return `
      <div class="tizi-sc-hitl${clearNow ? ' clear' : ''}" id="tiziScHitlBox">
        <div class="tizi-sc-hitl-head">
          <span class="tizi-sc-hitl-title">${clearNow ? '✓ 审核已就绪 · 确认后继续输出' : '⏸ 审核确认 · 请核对以下条目'}</span>
          <span class="tizi-sc-hitl-badges">
            ${p0Cnt > 0 ? `<span class="tizi-sc-pill p0">P0 冲突 ${p0Cnt}</span>` : '<span class="tizi-sc-pill ok">P0 已清零</span>'}
            ${p1Cnt > 0 ? `<span class="tizi-sc-pill p1">P1 视觉 ${p1Cnt}</span>` : ''}
            ${p2Cnt > 0 ? `<span class="tizi-sc-pill muted">P2 提示 ${p2Cnt}</span>` : ''}
          </span>
        </div>
        <p class="tizi-sc-hitl-desc">${escHtml(descText)}</p>
        ${itemsHtml ? `<div class="tizi-sc-hitl-list">${itemsHtml}</div>` : ''}
        <div class="tizi-sc-hitl-bar">
          <label class="tizi-sc-hitl-chk"><input type="checkbox" id="tiziScPromoteChk" ${T.hitlPromoteMem ? 'checked' : ''} ${T.hitlSubmitting ? 'disabled' : ''}> 沉淀已确认项至企业库</label>
          <div class="tizi-sc-hitl-cta">
            ${p0Cnt === 0 && p1Cnt > 0 && !T.hitlSubmitting ? '<button type="button" class="tizi-sc-act" id="tiziScSkipP1Btn">跳过 P1 生成草稿</button>' : ''}
            <button type="button" class="tizi-sc-act primary" id="tiziScApproveBtn" ${(p0Cnt > 0 || T.hitlSubmitting) ? 'disabled' : ''}>${escHtml(primaryLabel)}</button>
          </div>
        </div>
      </div>`;
  };

  T.buildDeliveryInlineHtml = function (exportsList) {
    const filesHtml = exportsList.map(f => {
      const ext = f.name.endsWith('.xlsx') ? 'XLSX' : (f.name.endsWith('.csv') ? 'CSV' : 'MD');
      return `<a class="tizi-sc-file" href="/api/tizi/download?file=${encodeURIComponent(f.name)}" download="${escHtml(f.name)}">
        <span class="tizi-sc-file-ext">${ext}</span>
        <span class="tizi-sc-file-info"><b>${escHtml(f.name)}</b><small>${escHtml(f.desc || '')} · ${(Number(f.size || 0) / 1024).toFixed(1)} KB</small></span>
        <span style="font-size:11px;color:#1b6049;font-weight:600;flex-shrink:0">↓</span>
      </a>`;
    }).join('');
    return `
      <div class="tizi-sc-delivery" id="tiziScDeliveryBox">
        <div class="tizi-sc-delivery-head">
          <span>✓ 双轨提资交付包已生成（${exportsList.length} 个文件）</span>
          <button type="button" class="tizi-sc-act" id="tiziScOpenDeliveryBtn">◫ 在右侧查看校验明细</button>
        </div>
        <div class="tizi-sc-files">${filesHtml}</div>
      </div>`;
  };

  T.bindHitlInlineEvents = function (host) {
    host.querySelectorAll('[data-sc-adopt-sheet]').forEach(btn => {
      btn.onclick = async () => {
        try {
          await T.tiziPost('cell', {
            sheet: btn.dataset.scAdoptSheet,
            row: Number(btn.dataset.scAdoptRow),
            column: btn.dataset.scAdoptCol,
            value: btn.dataset.scAdoptVal,
            action: 'confirm',
          });
          toast('已采纳并确认该冲突项');
          await T.loadStatus(true);
        } catch (e) { toast(e.message); }
      };
    });
    host.querySelectorAll('[data-sc-confirm-sheet]').forEach(btn => {
      btn.onclick = async () => {
        try {
          await T.tiziPost('cell', {
            sheet: btn.dataset.scConfirmSheet,
            row: Number(btn.dataset.scConfirmRow),
            column: btn.dataset.scConfirmCol,
            action: 'confirm',
          });
          toast('已确认该待审项');
          await T.loadStatus(true);
        } catch (e) { toast(e.message); }
      };
    });
    host.querySelectorAll('[data-sc-inspect-sheet]').forEach(btn => {
      btn.onclick = () => T.jumpToSheetCell(
        btn.dataset.scInspectSheet,
        Number(btn.dataset.scInspectRow),
        btn.dataset.scInspectCol,
        true,
      );
    });
    host.querySelector('#tiziScPromoteChk')?.addEventListener('change', e => {
      T.hitlPromoteMem = Boolean(e.target.checked);
    });
    host.querySelector('#tiziScApproveBtn')?.addEventListener('click', () => T.submitInlineHitlResume(false));
    host.querySelector('#tiziScSkipP1Btn')?.addEventListener('click', () => T.submitInlineHitlResume(true));
  };
})();
