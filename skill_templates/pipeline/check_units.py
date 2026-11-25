"""匹配·核对单元分组：按原表结构确定每一行所属的核对单元（同类项），组装评审结果行。

分组规则：
1. 成套项（成套对应组）及其两侧成员，核对单元为成套项名称；清册成套行下的物资同样用成套行名称。
2. 其余以清册为基准，取清册原表的直接上级章节；只有造价单侧的行取造价原表章节。
3. 原表没有章节时，用物资自身名称。
分组完全来自原表，可追溯、可复现，不调用模型。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
"""
from collections import defaultdict

TXT = skill("text_rules")
SETS = skill("set_coverage")


def _section(item):
    path = item.get("层级路径") or []
    return str(item.get("层级") or (path[-1] if path else "") or "").strip()


def unit_name(src, tgt, set_item, set_target):
    """一行的核对单元名称。set_item 为该行所属的造价成套项，set_target 为与成套项一一对应的清册成套行。"""
    if set_item is not None:
        return SETS.head(TXT._tgt_name(set_target) if set_target else TXT._src_name(set_item))
    if tgt is not None:
        if tgt.get("成套行"):
            return SETS.head(TXT._tgt_name(tgt))
        if tgt.get("成套项"):
            return SETS.head(tgt["成套项"].get("名称"))
        return _section(tgt) or SETS.head(TXT._tgt_name(tgt))
    return _section(src) or SETS.head(TXT._src_name(src))


def build_rows(sources, targets, pairs, covers, task_id, api_key):
    """pairs 为一对一配对 {造价序号: 清册序号}；covers 为成套对应 {造价主项序号: [组成员]}，
    成员为 {target}（清册组成件）、{source}（造价组成件）或 {source, target}（组内逐项配对）。"""
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    used = set(pairs.values())
    covered, covered_src, inner = {}, {}, {}
    for s_seq, items in (covers or {}).items():
        for c in items:
            t_seq, m_seq = str(c.get("target") or ""), str(c.get("source") or "")
            if m_seq and t_seq:
                if pairs.get(m_seq) == t_seq:
                    inner[m_seq] = s_seq
            elif t_seq and t_seq not in used:
                covered[t_seq] = (s_seq, c.get("basis", ""))
            elif m_seq and m_seq not in pairs:
                covered_src[m_seq] = (s_seq, c.get("basis", ""))
    units = []
    for s in sources:
        s_seq = TXT._seq(s)
        t_seq = pairs.get(s_seq)
        units.append({"kind": "pair" if t_seq else ("covered" if s_seq in covered_src else "src_only"), "src": s_seq, "tgt": t_seq})
    for t in targets:
        t_seq = TXT._seq(t)
        if t_seq not in used:
            units.append({"kind": "covered" if t_seq in covered else "tgt_only", "src": None, "tgt": t_seq})

    for u in units:
        s, t = src_by.get(u["src"] or ""), tgt_by.get(u["tgt"] or "")
        owner = (covered.get(u["tgt"]) or covered_src.get(u["src"])) if u["kind"] == "covered" else None
        set_seq = owner[0] if owner else (inner.get(u["src"]) or (u["src"] if u["src"] in (covers or {}) else None))
        set_item = src_by.get(set_seq) if set_seq else None
        set_target = tgt_by.get(pairs.get(set_seq, "")) if set_seq else None
        u["name"] = unit_name(s, t, set_item, set_target)
        u["set"] = set_seq
        u["basis"] = owner[1] if owner else ""
        # 以清册为基准排序；造价单侧排在清册之后
        u["order"] = int(u["tgt"]) if u["tgt"] and u["tgt"].isdigit() else 10 ** 6 + (int(u["src"]) if u["src"] and u["src"].isdigit() else 0)

    first = {}
    for u in units:
        first[u["name"]] = min(first.get(u["name"], 10 ** 9), u["order"])
    group_no = {name: i + 1 for i, name in enumerate(sorted(first, key=lambda n: first[n]))}
    grouped = defaultdict(list)
    for u in units:
        grouped[group_no[u["name"]]].append(u)

    rank = {"pair": 0, "src_only": 2, "covered": 1, "tgt_only": 3}
    rows = []
    for gid in sorted(grouped):
        members = sorted(grouped[gid], key=lambda u: (0 if u["src"] and u["src"] in (covers or {}) else 1, rank[u["kind"]], u["order"]))
        for idx, u in enumerate(members, 1):
            s, t = src_by.get(u["src"] or ""), tgt_by.get(u["tgt"] or "")
            row = {
                "分组序号": "%d_%d" % (gid, idx),
                "源_序号": int(u["src"]) if u["src"] and u["src"].isdigit() else "",
                "源_同类项名称": u["name"],
                "源_名称": TXT._src_name(s) if s else "",
                "源_型号": TXT._src_spec(s) if s else "",
                "源_数量": TXT._src_qty(s) if s else "",
                "源_单位": TXT._src_unit(s) if s else "",
                "匹配_同类项名称": u["name"],
                "匹配_发现": bool(t),
                "匹配_原因": "",
                "匹配_置信": "",
                "匹配_得分(%)": 99.0 if t and s else 0.0,
                "细项_得分(%)": 0.0,
                "匹配_序号": int(u["tgt"]) if u["tgt"] and u["tgt"].isdigit() else "",
                "匹配_数量": t.get("数量", "") if t else "",
                "匹配_单位": t.get("单位", "") if t else "",
                "匹配_名称": TXT._tgt_name(t) if t else "",
                "匹配_型号": TXT._tgt_spec(t) if t else "",
                "匹配_价格": t.get("价格", "") if t else "",
                "Top3_备选": [],
                "语义分组标签": str(gid),
            }
            if u["kind"] == "covered":
                row["成套项_可研序号"] = int(u["set"]) if str(u["set"]).isdigit() else u["set"]
                row["成套项_依据"] = u["basis"]
            elif u["src"] and u["src"] in (covers or {}):
                num = lambda v: int(v) if str(v).isdigit() else v
                row["成套项_覆盖清册序号"] = [num(c["target"]) for c in covers[u["src"]] if c.get("target") and not c.get("source")]
                row["成套项_覆盖可研序号"] = [num(c["source"]) for c in covers[u["src"]] if c.get("source") and not c.get("target")]
                row["成套项_组内配对"] = [[num(c["source"]), num(c["target"])] for c in covers[u["src"]] if c.get("source") and c.get("target")]
            elif u["src"] in inner:
                row["成套项_所属"] = int(inner[u["src"]]) if str(inner[u["src"]]).isdigit() else inner[u["src"]]
            rows.append(row)
    ctx.log(task_id, "核对单元分组：按原表结构分为 %d 个核对单元" % len(group_no), 0.58)
    return rows
