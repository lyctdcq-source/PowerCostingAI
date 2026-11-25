"""流程·匹配与评审编排：决定匹配和评审各步骤的先后顺序。

匹配：候选配对（模型）→ 规则校验 → 配对修正（未配与数量不符的条目重判，含数量合计对应）
      → 成套对应（模型，多对多、跨层级；未编组的按原表结构兜底）。
评审：逐行规范化 → 标记成套项相关行 → 领域知识（配对行作证据、单侧行定档）→ 逐行评审（模型同时给结论与原因，程序校验、重试）
      → 成套项整体复核（模型，同样校验重试）→ 核对单元原因（模型）→ 组级汇总定档 → 回填折算算式 → 附加知识提示 → 清理内部字段。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
"""
PAIR = skill("candidate_pairing")
RULE = skill("rule_check")
SETS = skill("set_coverage")
GROUPS = skill("set_matching")
ROW = skill("row_review")
GROUP = skill("group_review")
KNOW = skill("knowledge_apply")


def pair(sources, targets, api_key, task_id):
    """返回 (一对一配对 {造价序号: 清册序号}, 成套对应 {造价主项序号: [组成员]})。"""
    ctx.set_api_key(api_key)   # 配对修正中的联网检索使用同一密钥
    pairs, _candidates = PAIR.llm_pair(sources, targets, api_key, task_id)
    ctx.log(task_id, "LLM 配对 %d 对，进入规则校验" % len(pairs), 0.35)
    pairs = RULE.rule_refine(pairs, sources, targets, task_id)
    strings = GROUPS.string_groups(sources, targets)   # 绝缘子串及金具按数量规则成组，不参与配对修正
    pairs, merges = PAIR.repair_pairs(sources, targets, pairs, api_key, task_id,
                                      {k for g in strings for k in g["kesheng"]}, {q for g in strings for q in g["qingce"]})
    return GROUPS.match_sets(sources, targets, pairs, api_key, task_id, merges)


def review(rows, api_key, task_id, sources, targets):
    ctx.set_api_key(api_key)
    ctx.log(task_id, "步骤 4/5：逐行一致性复核（含单位折算）...", 0.60)
    ROW.normalize_final_match_results(rows)
    sets = SETS.mark_set_rows(rows, sources, targets)
    known = KNOW._apply_domain_knowledge(rows, sources, targets)
    if known or sets:
        ctx.log(task_id, "步骤 4/5：成套项相关 %d 行，领域知识参与 %d 行" % (sets, known), 0.62)
    findings = ROW._format_final_match_findings(rows, api_key)
    whole = SETS.review_sets(rows, sources, targets, api_key)
    ctx.log(task_id, "步骤 5/5：成套项整体复核 %d 项，统一核对单元原因、组级定档、回填折算算式..." % whole, 0.85)
    reasons = GROUP._apply_final_group_reasons(rows, api_key)
    banded = GROUP._apply_group_level_scores(rows)
    filled = GROUP._ensure_conversion_process_in_finding(rows)
    KNOW._append_knowledge_notes(rows)
    GROUP._clear_alternative_candidates(rows)
    for r in rows:
        r.pop("语义分组标签", None)
    ctx.log(task_id, "评审完成：逐行结论 %d 行、核对单元原因 %d 行、组级定档 %d 行、回填算式 %d 行"
            % (findings, reasons, banded, filled), 0.95)
    return rows
