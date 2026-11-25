"""评审·组级定档：按核对单元汇总逐行结论定组档，由模型为每个核对单元写复核原因，并回填折算算式。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
跨技能调用写作“别名.函数名”，别名由 skill("技能ID") 取得；模型调用、日志、领域知识等由宿主通过 ctx 提供。
"""
import re
import json
from collections import defaultdict

TXT = skill("text_rules")
QTY = skill("quantity_rules")
ROW = skill("row_review")


def _result_row_source_group(row: dict) -> str:
    group = str((row or {}).get("语义分组标签") or "").strip()
    if group:
        return group
    group_no = str((row or {}).get("分组序号") or "").strip()
    match = re.match(r"^(\d+)(?:_|$)", group_no)
    return match.group(1) if match else ""

def _source_group_sort_value(group: str) -> int:
    match = re.search(r"\d+", str(group or ""))
    return int(match.group(0)) if match else 999999

def _group_id_from_output_row(row: dict) -> str:
    group_no = str((row or {}).get("分组序号") or "").strip()
    match = re.match(r"^(\d+)(?:_|$)", group_no)
    return match.group(1) if match else _result_row_source_group(row)

def _fallback_final_group_reason(group_rows: list) -> str:
    label = next((str(row.get("源_同类项名称") or "").strip() for row in group_rows if row.get("源_同类项名称")), "")
    source_count = sum(1 for row in group_rows if any(str(row.get(key) or "").strip() for key in ("源_序号", "源_名称", "源_型号")))
    target_sequences = []
    for row in group_rows:
        for seq in TXT._split_match_sequences(row.get("匹配_序号")):
            if seq and seq not in target_sequences:
                target_sequences.append(seq)
    matched_count = sum(1 for row in group_rows if row.get("源_序号") and row.get("匹配_序号"))
    missing_target_count = sum(1 for row in group_rows if row.get("源_序号") and not row.get("匹配_序号"))
    extra_target_count = sum(1 for row in group_rows if row.get("匹配_序号") and not row.get("源_序号"))
    unmatched_count = missing_target_count + extra_target_count
    row_conclusions = [ROW._row_pair_consistency_label(row) for row in group_rows]
    issue_reasons = []
    if missing_target_count:
        issue_reasons.append(f"源表有{missing_target_count}条未找到对应匹配项")
    if extra_target_count:
        issue_reasons.append(f"匹配表有{extra_target_count}条为源表未列的补充项")
    if any("名称型号" in item and "不一致" in item for item in row_conclusions):
        issue_reasons.append("部分名称型号或规格口径不一致")
    if any("单位：不一致" in item for item in row_conclusions):
        issue_reasons.append("部分单位口径不同")
    if any("数量：不一致" in item for item in row_conclusions):
        issue_reasons.append("部分数量折算后不一致")
    subject = f"“{label}”" if label else "该组"
    if not issue_reasons:
        return f"{subject}各项与清册一一对应，名称型号、单位与数量均相符。"
    return f"{subject}整体可对应，但{'；'.join(issue_reasons)}，相应项需人工核对工程量。"

def _compact_final_group_for_reason(group_id: str, group_rows: list) -> dict:
    row_conclusions = [ROW._row_pair_consistency_label(row) for row in group_rows]
    conclusion_counts = defaultdict(int)
    for conclusion in row_conclusions:
        conclusion_counts[conclusion] += 1
    source_names, target_names = [], []
    for row in group_rows:
        source_name = TXT._brief_text(row.get("源_名称", ""), 34)
        target_name = TXT._brief_text(row.get("匹配_名称", ""), 34)
        if source_name and source_name not in source_names:
            source_names.append(source_name)
        if target_name and target_name not in target_names:
            target_names.append(target_name)
    missing_target_count = sum(1 for row in group_rows if row.get("源_序号") and not row.get("匹配_序号"))
    extra_target_count = sum(1 for row in group_rows if row.get("匹配_序号") and not row.get("源_序号"))
    matched_count = sum(1 for row in group_rows if row.get("源_序号") and row.get("匹配_序号"))
    quantity_issue_count = sum(1 for item in row_conclusions if "数量不一致" in item)
    name_issue_count = sum(1 for item in row_conclusions if "名称不一致" in item or ("名称型号" in item and "不一致" in item))
    unit_issue_count = sum(1 for item in row_conclusions if "单位不一致" in item or "单位：不一致" in item)
    return {
        "group_id": group_id,
        "source_class": next((row.get("源_同类项名称", "") for row in group_rows if row.get("源_同类项名称")), ""),
        "source_count": sum(1 for row in group_rows if row.get("源_序号")),
        "target_count": len({seq for row in group_rows for seq in TXT._split_match_sequences(row.get("匹配_序号"))}),
        "matched_rows": matched_count,
        "unmatched_rows": missing_target_count + extra_target_count,
        "source_name_samples": source_names[:5],
        "target_name_samples": target_names[:5],
        "row_conclusion_counts": dict(conclusion_counts),
        "main_differences": {
            "missing_target_rows": missing_target_count,
            "extra_target_rows": extra_target_count,
            "quantity_issue_rows": quantity_issue_count,
            "name_issue_rows": name_issue_count,
            "unit_issue_rows": unit_issue_count,
        },
        "fallback_reason": _fallback_final_group_reason(group_rows),
        "set_review": any(row.get("_set_role") in ("set", "covered") for row in group_rows),
    }

def _sanitize_final_group_reason(reason: str, fallback_reason: str) -> str:
    text = ROW._neutralize_match_reason_text(str(reason or "").strip())
    text = re.sub(r"\s+", " ", text)
    bad_markers = ("source_seq", "target_seq", "source_name", "target_name", "row_conclusion", "对应target", "对应 target")
    if not text or any(marker in text for marker in bad_markers):
        return fallback_reason
    # 不再强制“已匹配N条/未匹配N条”计数格式，改为允许自然语言专家批注；仅做长度与收尾约束。
    if len(text) > 120:
        return fallback_reason
    return text if text.endswith(("。", "！", "？")) else text + "。"

def _llm_final_group_reasons(group_payloads: list, api_key: str) -> dict:
    if not group_payloads or not api_key:
        return {}
    prompt = f"""你是一名资深电力工程造价复核工程师。下面每一项是一个“同类项组”（组内可能只有1条，也可能多条配对）。请为每个组写一句“匹配_原因”——即你对该组源清单与匹配清单整体对应情况的复核批注。
要求：
1. 用一句自然、专业、像人工手写的复核意见，说明本组物资的对应关系，以及需要提请注意之处（例如个别项无对应、附属配置项不单列、计量按重量与长度两种口径需换算、规格略有出入等）。
2. 严禁使用“已匹配X条、未匹配Y条”这类系统腔与计数式表述；严禁罗列序号、字段名、source_seq/target_seq 等内部信息；不要写“均为变压器”“但属同类物资”这类空话套话。
3. 只给组级总体判断，不要逐行展开。组内只有1条时，就针对这一条写它与匹配项的对应情况。
   组内含成套项时（set_review 为 true），把成套项与其组成件作为一个整体评价：说明整套是否对应、整套数量是否一致、配置有何差异，不要把组成件当作漏项。
4. 每组一句话，90 个汉字以内，口吻是资深造价工程师对账时的批注。
5. 输出 JSON：{{"reasons":[{{"group_id":"4","reason":"本组铜母排与紫铜按型号一一对应，绝缘护套为附属项不单列；铜材按重量与长度两种口径计量，工程量需折算核对。"}}]}}

待分析同类项组：
{json.dumps(group_payloads, ensure_ascii=False)}
"""
    try:
        data = ctx.llm_json(prompt, api_key, max_tokens=2000)
        reasons = {}
        fallback_by_group = {str(item.get("group_id")): item.get("fallback_reason", "") for item in group_payloads or []}
        for item in data.get("reasons", []) or []:
            group_id = str(item.get("group_id") or "").strip()
            reason = _sanitize_final_group_reason(item.get("reason"), fallback_by_group.get(group_id, ""))
            if group_id and reason:
                reasons[group_id] = reason
        return reasons
    except Exception as e:
        print(f"LLM 最终组级原因生成失败: {e}")
        return {}

def _apply_final_group_reasons(final_results: list, api_key: str) -> int:
    groups = defaultdict(list)
    for row in final_results or []:
        group_id = _group_id_from_output_row(row)
        if group_id:
            groups[group_id].append(row)

    # 处理所有同类项组（含仅1条的组），使每行“匹配_原因”都成为 LLM 专家口吻批注，
    # 而不是单行组退回机械的三段式规则文本。单行 payload 较小，加大批量以控制请求数。
    multi_groups = {gid: rows for gid, rows in groups.items() if rows}
    if not multi_groups:
        return 0

    reason_by_group = {}
    group_ids = sorted(multi_groups.keys(), key=_source_group_sort_value)
    for start in range(0, len(group_ids), 12):
        batch_ids = group_ids[start:start + 12]
        payloads = [_compact_final_group_for_reason(gid, multi_groups[gid]) for gid in batch_ids]
        batch_reasons = _llm_final_group_reasons(payloads, api_key)
        for payload in payloads:
            gid = payload["group_id"]
            reason_by_group[gid] = batch_reasons.get(gid) or payload["fallback_reason"]

    changed = 0
    for gid, rows in multi_groups.items():
        reason = reason_by_group.get(gid)
        if not reason:
            continue
        for row in rows:
            if row.get("匹配_原因") != reason:
                row["匹配_原因"] = reason
                changed += 1
    return changed

def _group_key_of_output_row(row: dict) -> str:
    group_no = str((row or {}).get("分组序号") or "").strip()
    match = re.match(r"^(\d+)(?:_|$)", group_no)
    return match.group(1) if match else ""

def _apply_group_level_scores(final_results: list) -> int:
    """按行统一“匹配_发现”档位前缀与置信说明，再按同类项组汇总定组档、统一“匹配_原因”档位前缀。

    不再按档位映射出分数：分数由档位换算而来，显示得很精细却没有相应精度的评估依据。
    组档取组内最严重的逐行结论，覆盖不全时再下调；组内有评审未完成的行，组档也是评审未完成。"""
    groups = defaultdict(list)
    solo_counter = 0
    for row in final_results or []:
        if not isinstance(row, dict):
            continue
        key = _group_key_of_output_row(row)
        if not key:
            solo_counter += 1
            key = f"~solo_{solo_counter}"
        groups[key].append(row)

    changed = 0
    for key, rows in groups.items():
        details = {}
        for r in rows:
            detail = ROW._classify_row_detail(r)
            details[id(r)] = detail
            r.pop("细项_得分(%)", None)
            r.pop("匹配_得分(%)", None)
            r["匹配_置信"] = ROW._row_review_confidence(r, detail)
            finding = row_finding = r.get("匹配_发现")
            if not isinstance(row_finding, str) or not str(row_finding).strip():
                finding = ROW._row_pair_consistency_label(r)
            body = ROW._strip_band_prefix(finding)
            r["匹配_发现"] = f"{detail['band']}。{body}" if body else f"{detail['band']}。"

        # 不参与核对的条目不计入组内覆盖率；领域知识认定的单侧组成件、附件按已对应计入
        counted = [r for r in rows if details[id(r)]["band"] != ROW.BAND_EXCLUDED]
        src_rows = [r for r in counted if str(r.get("源_序号") or "").strip()]
        # 成套项多对多匹配的组成件按已对应计入覆盖率，其结论与成套项相同
        pair_rows = [r for r in counted if (str(r.get("源_序号") or "").strip() and str(r.get("匹配_序号") or "").strip())
                     or details[id(r)]["band"] in (ROW.BAND_BASIC, ROW.BAND_PARTIAL) and r.get("_kg_band")
                     or r.get("_set_role") == "covered"]
        graded = [r for r in pair_rows if details[id(r)]["band"] in ROW.BAND_ORDER]
        target_seqs = set()
        for r in counted:
            for seq in TXT._split_match_sequences(r.get("匹配_序号")):
                if str(seq).strip():
                    target_seqs.add(str(seq).strip())
        ns, nt, m = len(src_rows), len(target_seqs), len(pair_rows)
        if not counted:
            group_band = ROW.BAND_EXCLUDED
        elif m == 0:
            group_band = ROW.BAND_INCONSISTENT
        elif any(details[id(r)]["band"] == ROW.BAND_UNREVIEWED for r in pair_rows):
            group_band = ROW.BAND_UNREVIEWED
        else:
            group_band = min((details[id(r)]["band"] for r in graded), key=lambda b: ROW.BAND_ORDER[b]) if graded else ROW.BAND_BASIC
            dice = (2.0 * m) / (ns + nt) if (ns + nt) else 0.0
            # 同类项组的定性结论必须综合组内每一条一对一匹配的结果，未配对的行同样计入：
            # 组内多数物资没对上时，整组结论不能停留在“基本一致”。
            if dice < 0.999:
                coverage_cap = ROW.BAND_BASIC if dice >= 0.85 else ROW.BAND_PARTIAL
                if ROW.BAND_ORDER[group_band] > ROW.BAND_ORDER[coverage_cap]:
                    group_band = coverage_cap
        for r in rows:
            reason_body = ROW._strip_band_prefix(r.get("匹配_原因"))
            reason = f"{group_band}。{reason_body}" if reason_body else f"{group_band}。"
            if r.get("匹配_原因") != reason:
                changed += 1
            r["匹配_原因"] = reason
    return changed

def _clear_alternative_candidates(final_results: list) -> None:
    """匹配已达免复核精度，取消 Top3_备选人工挑选功能，输出统一置空。
    同时清掉定档阶段之后才写入的内部字段，避免泄漏到结果文件。"""
    for row in final_results or []:
        if isinstance(row, dict):
            row["Top3_备选"] = []
            for key in list(row.keys()):
                if str(key).startswith("_"):
                    row.pop(key, None)

def _ensure_conversion_process_in_finding(final_results: list) -> int:
    """系统已算出折算过程、但批注被模型省略成“可换算/折算后一致”时，把算式补回批注末尾。

    单位不一致必须列明计算过程，这一步不依赖模型是否照做：prompt 已明确要求引用系统算式，
    实测仍会出现同类条目一条写全、另一条省略（4_1 写全、4_3/4_5 省略）。
    """
    changed = 0
    for row in final_results or []:
        if not isinstance(row, dict):
            continue
        if not (str(row.get("源_序号") or "").strip() and str(row.get("匹配_序号") or "").strip()):
            continue
        finding = str(row.get("匹配_发现") or "").strip()
        if not finding:
            continue
        # 只有真正给出了单位重量依据才算已列式。"折合/折算"这类词模型常用来含糊带过
        # （"按折算，5m折合0.005t" 就没有密度截面推导），不能当作跳过的理由。
        if re.search(r"kg/m|kg/套|kg/件|kg/个|÷ ?1000000|密度|单位重量|单件重|每箱|m/箱", finding):
            continue
        try:
            process = str(ROW._row_match_review_parts(row).get("conversion_process") or "").strip()
        except Exception:
            continue
        if not process:
            continue
        row["匹配_发现"] = f"{finding.rstrip('。')}。折算过程：{process}。"
        changed += 1
    return changed
