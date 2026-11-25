"""评审·领域知识定档：用已审核领域知识找出配对行的身份属性冲突（作为证据交模型评审），为单侧行判定计量性质、成套组成与附件。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
跨技能调用写作“别名.函数名”，别名由 skill("技能ID") 取得；模型调用、日志、领域知识等由宿主通过 ctx 提供。
"""

TXT = skill("text_rules")
ROW = skill("row_review")


# ---------------------------------------------------------------- 评审 & 导出
def _apply_domain_knowledge(rows, sources, targets) -> int:
    """配对行：身份属性冲突写入 _kg_evidence，交模型综合评审并用于校验其结论，不预先定档。
    单侧行：按计量性质/成套组成/附件/模板定档。

    只有“已审核”知识写入 _kg_evidence/_kg_band；待审核知识只写提示。所用知识记入“领域知识”字段。
    """
    kg = ctx.knowledge()
    if not kg or not (sources or targets):
        return 0
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    applied = 0
    for row in rows:
        if row.get("_set_role") in ("covered", "header"):
            continue  # 已按成套结构定档
        s, t = src_by.get(str(row.get("源_序号") or "")), tgt_by.get(str(row.get("匹配_序号") or ""))
        used = []
        if s and t:
            findings = kg.compare(s, t)
            conflicts = [f for f in findings if f["severity"] == "冲突"]
            hints = [f for f in findings if f["severity"] == "提示"]
            if conflicts and conflicts[0]["decisive"]:
                f = conflicts[0]
                row["_kg_evidence"] = f"{f['label']}不同（造价{f['a']}，清册{f['b']}），{f['note']}"
            notes = [f"{f['label']}：造价{f['a']}，清册{f['b']}，{f['note']}" for f in hints + [c for c in conflicts if not c["decisive"]]]
            if notes:
                row["_kg_note"] = "；".join(notes)
            used = [dict(type="身份属性", **ref) for f in findings for ref in f["knowledge"]]
            check = kg.quantity_check(s, t)
            if check:
                used += [dict(type="计量换算", **ref) for ref in check["knowledge"]]
        elif s or t:
            exp = kg.explain_unpaired(s or t, "source" if s else "target", sources, targets)
            if exp:
                if exp["decisive"]:
                    row["_kg_band"], row["_kg_finding"] = exp["band"], exp["finding"]
                else:
                    row["_kg_note"] = "待审核知识提示：" + exp["finding"]
                used = [dict(type=exp["type"], related=exp["related"], **ref) for ref in exp["knowledge"]]
        if used:
            unique = {(u["type"], u["id"]): u for u in used}
            row["领域知识"] = list(unique.values())
            applied += 1
    return applied

def _append_knowledge_notes(rows) -> int:
    count = 0
    for row in rows:
        note = row.get("_kg_note")
        if note and note not in str(row.get("匹配_发现") or ""):
            row["匹配_发现"] = f"{str(row.get('匹配_发现') or '').rstrip('。')}。领域知识提示：{note}。"
            count += 1
    return count
