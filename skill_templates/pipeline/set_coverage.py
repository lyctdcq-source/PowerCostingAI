"""匹配·成套项整体比对：造价按整套计价的成套项，与清册中对应的成套行及其组成件整体比对，
组成件不再逐条匹配；造价逐项计价的系统（如计算机监控系统下各屏柜、装置单独计价）仍逐条核对。
成套项与其组成件按多对多匹配：评审时把成套项连同两侧组成件交给模型整体推理定档，组成件取成套项的同一结论，
配置差异作为提示写入结论，不计为数量差异；找不到所属成套项的组成件按未匹配处理。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
"""
import json
import re
import unicodedata

TXT = skill("text_rules")
QTY = skill("quantity_rules")
ROW = skill("row_review")
RULE = skill("rule_check")

BAND_MEMBER = ROW.BAND_PENDING   # 组成件在成套项整体复核前的占位结论，复核后改为成套项的结论
SET_NAME = r"系统|成套"           # 以套/组计量、名称含这些词且其下无另行计价子项的造价条目按整套计价
SET_UNITS = ("套", "组")
LLM_SET_BATCH = 8
LLM_COVER_BATCH = 60


def head(name):
    """去掉编号、冒号后的说明与规格，得到成套项主名。"""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = re.sub(r"^\s*(?:\d+(?:\.\d+)*)\s*[、\t．.]+\s*", "", text)
    text = re.split(r"[：:　]|\s{2,}", text.strip())[0]
    return text.strip(" ,，、") or str(name or "").strip()


def _priced(item):
    return QTY._to_float_or_zero(item.get("Estimation_Price")) > 0


def _brief(item, spec_limit=40):
    name = TXT._src_name(item) if "Estimation_Name" in item else TXT._tgt_name(item)
    spec = TXT._src_spec(item) if "Estimation_Name" in item else TXT._tgt_spec(item)
    qty = item.get("Estimation_Quantity", item.get("数量", ""))
    unit = item.get("Estimation_Unit", item.get("单位", ""))
    return f"{TXT._clean(name, 40)} {TXT._clean(spec, spec_limit)} {qty}{unit}".strip()


SET_SPEC_LIMIT = 400   # 成套行规格常写“内含：18片绝缘子，2只U-21挂环……”，截短会让组成件看起来缺失
MODEL_CODE = r"[A-Za-z]{1,4}[A-Za-z]*-?\d+(?:[-/]\d+)*"


def _written_in_spec(comp, item):
    """组成件的型号编码是否写在成套行的规格里（如清册耐张串规格“内含：……2只U-21型U型挂环”）。"""
    if not item:
        return False
    text = TXT._clean_compare_text(TXT._tgt_spec(item) if "设备材料名称" in item else TXT._src_spec(item))
    codes = [TXT._clean_compare_text(c) for c in re.findall(MODEL_CODE, str(comp.get("name") or ""))]
    return bool(codes) and all(c and c in text for c in codes)


INLINE_PART = r"(\d+)\s*(面|台|块|只)\s*([一-鿿A-Za-z0-9]{1,12}?(?:屏|柜|装置|箱))"


def components(item):
    """造价成套项的组成件：原表在其下声明的组成行；没有时取名称里写明的组成（如“直流系统 4 面馈线屏，2 面充电屏”）。"""
    if item.get("成套组成"):
        return item["成套组成"]
    if str(item.get("Estimation_Unit") or "").strip() not in SET_UNITS:
        return []
    text = unicodedata.normalize("NFKC", TXT._src_name(item))
    found = [dict(name=name, unit=unit, quantity=qty, row="名称") for qty, unit, name in re.findall(INLINE_PART, text)]
    return found if len(found) >= 2 else []


def is_whole_priced(item, sources):
    """造价条目是否整套计价：原表声明了不另计价的组成件（费用包含/含以下/内含），
    或以套、组计量的系统级条目且其下没有另行计价的子项。"""
    if item.get("成套组成"):
        return True
    if str(item.get("Estimation_Unit") or "").strip() not in SET_UNITS:
        return False
    if not re.search(SET_NAME, head(TXT._src_name(item))):
        return False
    children = [s for s in sources if s.get("上级物资") == item.get("序号")]
    return not any(_priced(c) for c in children)


def _component_match(target, component):
    kg = ctx.knowledge()
    if kg:
        hit = kg.match_component(target, [component])
        return hit[1] if hit else 0.0
    a, b = head(TXT._tgt_name(target)), head(component.get("name"))
    return 0.9 if a and b and (a in b or b in a) else 0.0


def cover_sets(sources, targets, pairs, api_key, task_id):
    """确定每个整套计价的造价成套项覆盖哪些清册物资。只覆盖没有一对一对应的清册物资。

    依据顺序：清册原表成套结构（成套行、编号下级）→ 造价原表声明的组成 → 模型判定。
    返回 {造价序号: [{"target": 清册序号, "basis": 依据}]}。
    """
    tgt_by = {TXT._seq(t): t for t in targets}
    used, taken, covers = set(pairs.values()), set(), {}
    sets = [s for s in sources if is_whole_priced(s, sources)]

    def add(s_seq, t_seq, basis):
        if t_seq in tgt_by and t_seq not in used and t_seq not in taken:
            covers.setdefault(s_seq, []).append({"target": t_seq, "basis": basis})
            taken.add(t_seq)

    for s in sets:
        s_seq, t = TXT._seq(s), tgt_by.get(pairs.get(TXT._seq(s), ""))
        if t:
            for child in t.get("下级物资") or []:
                add(s_seq, str(child), f"清册原表中列在“{head(TXT._tgt_name(t))}”之下")
        for comp in components(s):
            # 成套项自己的一对一对应物也参与比对：它对上的组成件不再去别处找
            pool = ([t] if t else []) + [x for x in targets if TXT._seq(x) not in used and TXT._seq(x) not in taken]
            best = max(pool, key=lambda x: _component_match(x, comp), default=None)
            if best is not None and best is not t and _component_match(best, comp) >= 0.5:
                where = "名称中" if comp.get("row") == "名称" else f"原表第{comp.get('row')}行"
                add(s_seq, TXT._seq(best), f"造价{where}列为该成套项组成“{comp.get('name')}”")

    remaining = [t for t in targets if TXT._seq(t) not in used and TXT._seq(t) not in taken and not t.get("计量性质")]
    if api_key and sets and remaining:
        for start in range(0, len(remaining), LLM_COVER_BATCH):
            for s_seq, items, reason in _llm_cover(sets, remaining[start:start + LLM_COVER_BATCH], pairs, tgt_by, api_key):
                for t_seq in items:
                    add(s_seq, t_seq, "模型判定：" + reason)
    if covers:
        ctx.log(task_id, "成套项整体比对：%d 个造价成套项覆盖清册组成件 %d 条，组成件不再逐条匹配" % (
            len(covers), sum(len(v) for v in covers.values())), 0.37)
    return covers


COVER_PROMPT = """你是电力工程造价复核专家。可研估算中下列成套项按“整套”计价，组成件不另计价。设计清册（基准）中还有一些物资没有一对一对应的造价条目。
请判断这些清册物资中，哪些属于某个造价成套项的组成部分（该成套系统或成套设备内含的屏柜、装置、板卡、主机、附件），应随成套项整体核对。

要求：
- 名称叫法不同但属于同一系统的可以归入，例如“GPS对时屏、主时钟、从时钟”属于“时钟同步系统”。
- 只归入明确属于该成套系统的物资；造价中会另行计价的设备、独立材料（电缆、钢材、螺栓、防火材料等）不得归入；不确定的不要归入。
- 一条清册物资最多归入一个成套项。

造价成套项（序号|名称规格|数量单位|原表声明的组成|已一对一对应的清册物资）：
{sets}

待判断的清册物资（序号|名称规格|数量单位|清册章节）：
{items}

只输出 JSON：{{"covers":[{{"set":造价序号,"items":[清册序号],"reason":"归入依据，30字内"}}]}}，没有可归入的返回 {{"covers":[]}}。"""


def _llm_cover(sets, items, pairs, tgt_by, api_key):
    set_lines = []
    for s in sets:
        comps = "、".join(c.get("name", "") for c in components(s)[:12]) or "未列明"
        paired = tgt_by.get(pairs.get(TXT._seq(s), ""))
        set_lines.append("%s|%s|%s%s|%s|%s" % (TXT._seq(s), TXT._clean(TXT._src_name(s), 60), s.get("Estimation_Quantity", ""),
                                               s.get("Estimation_Unit", ""), comps, _brief(paired) if paired else "无"))
    item_lines = ["%s|%s %s|%s%s|%s" % (TXT._seq(t), TXT._clean(TXT._tgt_name(t), 30), TXT._clean(TXT._tgt_spec(t), 40),
                                        t.get("数量", ""), t.get("单位", ""), " / ".join(t.get("层级路径") or [t.get("层级", "")]))
                  for t in items]
    try:
        data = ctx.llm_json(COVER_PROMPT.format(sets="\n".join(set_lines), items="\n".join(item_lines)), api_key, max_tokens=3000)
    except Exception as exc:
        print(f"成套项归属判定失败，保留原表依据：{exc}")
        return []
    valid_sets = {TXT._seq(s) for s in sets}
    valid_items = {TXT._seq(t) for t in items}
    out = []
    for entry in data.get("covers", []) or []:
        s_seq = str(entry.get("set", "")).strip()
        picked = [str(x).strip() for x in entry.get("items", []) or [] if str(x).strip() in valid_items]
        if s_seq in valid_sets and picked:
            out.append((s_seq, picked, TXT._clean(entry.get("reason", ""), 40) or "属于该成套系统"))
    return out


def mark_set_rows(rows, sources, targets):
    """标记成套项相关的结果行：被覆盖的组成件与成套项构成多对多匹配，结论待成套项整体复核后统一；
    清册成套行若其组成件已在造价逐项计价并逐条对应，本行不单独核对数量。"""
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    paired_targets = {str(r.get("匹配_序号")) for r in rows if str(r.get("源_序号") or "").strip() and str(r.get("匹配_序号") or "").strip()}
    marked = 0
    for row in rows:
        s_seq, t_seq = str(row.get("源_序号") or ""), str(row.get("匹配_序号") or "")
        owner = str(row.get("成套项_可研序号") or "")
        if owner and not (s_seq and t_seq):
            set_item = src_by.get(owner)
            name = head(TXT._src_name(set_item)) if set_item else owner
            row["_set_role"], row["_set_band"] = "covered", BAND_MEMBER
            row["_set_finding"] = _member_finding(row, name, "待成套项整体复核")
            marked += 1
        elif s_seq and s_seq in src_by and (row.get("成套项_覆盖清册序号") or row.get("成套项_覆盖可研序号") or row.get("成套项_组内配对")
                                            or (src_by[s_seq].get("成套组成") and is_whole_priced(src_by[s_seq], sources))):
            row["_set_role"] = "set"
            marked += 1
        elif t_seq and not s_seq and tgt_by.get(t_seq, {}).get("成套行"):
            children = [str(c) for c in tgt_by[t_seq].get("下级物资") or []]
            if children and all(c in paired_targets for c in children):
                row["_set_role"], row["_set_band"] = "header", ROW.BAND_EXCLUDED
                row["_set_finding"] = "清册成套行，其组成件在造价中逐项计价并已逐条核对，本行不单独比对数量"
                marked += 1
    return marked


def _member_finding(row, name, verdict):
    side = "造价组成件" if str(row.get("源_序号") or "").strip() else "清册组成件"
    if re.match(r"\s*(%s)\s*[：:]" % "|".join(SET_DIFF_LABELS), str(verdict)):
        # 整组有差异时差异写在最前，组成件关系作为补充
        return f"{str(verdict).rstrip('。')}（本条为{side}，随造价成套项“{name}”整体核对，不单独比对数量）"
    return (f"成套对应（多对多）：{side}，与造价成套项“{name}”整体匹配（{row.get('成套项_依据') or '成套组成'}），"
            f"不单独比对数量。整组结论：{verdict}")


def _merge_total(s, t, covered, members, src_rows, src_members):
    """配对修正得到的合计对应组（同一物资分几行列出）：两侧各自合计后比对数量。不是合计组返回 None。"""
    merged = [str(r.get("成套项_依据") or "").startswith(("数量合计", "造价多条对应")) for r in covered + src_rows]
    if not merged or not all(merged) or not t:
        return None
    left = [RULE._comparable(x, True) for x in [s] + src_members]
    right = [RULE._comparable(x, False) for x in [t] + members]
    if not all(left + right) or len({b for b, _ in left + right}) != 1:
        return None
    a, b = sum(q for _, q in left), sum(q for _, q in right)
    same = abs(a - b) / max(a, b) <= 0.01
    unit = str(s.get("Estimation_Unit") or "")
    return {"same": same, "text": "造价合计 %g%s ↔ 清册合计 %g%s：%s" % (a, unit, b, unit, "数量一致" if same else "数量不同")}


def _set_payload(row_id, row, src_by, tgt_by, rows):
    s = src_by[str(row["源_序号"])]
    t = tgt_by.get(str(row.get("匹配_序号") or ""))
    anchor = str(row["源_序号"])
    covered = [r for r in rows if str(r.get("成套项_可研序号") or "") == anchor and str(r.get("匹配_序号") or "") in tgt_by]
    members = [tgt_by[str(r["匹配_序号"])] for r in covered]
    src_rows = [r for r in rows
                if str(r.get("成套项_可研序号") or "") == anchor and str(r.get("源_序号") or "") in src_by and not r.get("匹配_序号")]
    src_members = [src_by[str(r["源_序号"])] for r in src_rows]
    total = _merge_total(s, t, covered, members, src_rows, src_members)
    inner = [r for r in rows if str(r.get("成套项_所属") or "") == anchor]
    # 一对一对应的清册物资可能就是成套项的主设备（如光传输设备对应光端机），同样参与组成比对
    pool = ([t] if t else []) + members
    compare = []
    for comp in components(s):
        best = max(pool, key=lambda x: _component_match(x, comp), default=None)
        if best is not None and _component_match(best, comp) >= 0.5:
            same = str(comp.get("quantity")) == str(best.get("数量")) or QTY._to_float_or_zero(comp.get("quantity")) == QTY._to_float_or_zero(best.get("数量"))
            compare.append(f"{comp['name']} {comp.get('quantity')}{comp.get('unit')} ↔ {_brief(best)}：{'数量一致' if same else '数量不同'}")
        elif _written_in_spec(comp, t):
            compare.append(f"{comp['name']} {comp.get('quantity')}{comp.get('unit')} ↔ 清册成套行规格中写明（见“清册对应成套行”）")
        else:
            compare.append(f"{comp['name']} {comp.get('quantity')}{comp.get('unit')} ↔ 清册未列")
    return {
        "row_id": row_id,
        "造价成套项": _brief(s, SET_SPEC_LIMIT),
        "造价声明组成": [f"{c.get('name')} {c.get('quantity')}{c.get('unit')}" for c in components(s)],
        "清册对应成套行": _brief(t, SET_SPEC_LIMIT) if t else "无（清册以组成件形式列出）",
        "清册组成件": [_brief(m) + "（" + str(r.get("成套项_依据") or "") + "）" for m, r in zip(members, covered)],
        "造价组成件": [_brief(m) for m in src_members],
        "组内逐项配对": ["%s ↔ %s：%s" % (_brief(src_by[str(r["源_序号"])]), _brief(tgt_by[str(r["匹配_序号"])]),
                                     str(r.get("匹配_发现") or "逐项核对")[:60])
                     for r in inner if str(r.get("源_序号")) in src_by and str(r.get("匹配_序号")) in tgt_by],
        "组成比对": compare,
        **({"合计数量核对": total["text"], "_total_same": total["same"]} if total else {}),
    }


def _rule_verdict(payload, row):
    """程序比对整套数量与配置的结果。只作为评审未完成时给人工复核的事实，不作为结论。"""
    diffs = [c for c in payload["组成比对"] if not c.endswith("数量一致")]
    qty_note = ""
    if "_total_same" in payload:
        # 同一物资分几行列出的合计对应组：按两侧合计数量定档
        if not payload["_total_same"]:
            return ROW.BAND_PARTIAL, f"部分一致。数量不一致：{payload['合计数量核对']}"
        return ROW.BAND_CONSISTENT, f"一致。同一物资分行列出，{payload['合计数量核对']}"
    if row.get("匹配_序号"):
        review = ROW._row_match_review_parts(row)
        if review["qty_state"] == "diff":
            return ROW.BAND_PARTIAL, f"部分一致。数量不一致：{review['qty_text'].removeprefix('数量：')}" + (f"；配置不一致：{'；'.join(diffs)}" if diffs else "")
        qty_note = "整套数量相符"
    else:
        qty_note = "清册以组成件形式列出该成套系统"
    if diffs:
        return ROW.BAND_BASIC, f"基本一致。成套项整体对应，{qty_note}；配置差异提示：{'；'.join(diffs)}"
    band = ROW.BAND_CONSISTENT if row.get("匹配_序号") and payload["组成比对"] else ROW.BAND_BASIC
    return band, f"{band}。成套项整体对应，{qty_note}" + ("，原表声明的组成件均已核对" if payload["组成比对"] else "")


SET_REVIEW_PROMPT = """你是资深电力工程造价复核工程师。下面每一条是一个成套对应组：造价成套项与清册中对应的成套行及组成件，可能是一对多、多对一或多对多（造价也可能把同一套拆成几行列出，见“造价组成件”）。
请把两侧作为一个整体判断，不要逐条孤立比对：成套系统是否同一套、整套数量是否一致、配置（组成件的型号与数量）是否相符。
“组内逐项配对”是组内已逐项核对数量的同一件物资，其结论可直接引用。

四档：
1. 一致：同一成套系统，整套数量一致，配置相符。成套行规格中写明的“内含……”组成件视同已列出（“组成比对”标“清册成套行规格中写明”的即是），按每串/每套配置×数量折算相符即为配置相符，不得说该侧未列组成件。
2. 基本一致：同一成套系统，整套数量一致，配置写法或个别组成件有差异（差异作为提示写明，不算数量不一致）。
3. 部分一致：同一成套系统，但整套数量不一致，或主要组成件缺失、数量明显不同，可能影响成套单价。
4. 不一致：清册中找不到该成套系统。

只依据两侧原表写明的内容判断，不得推断原表未写的规格（例如造价只写了绝缘子片数和金具只数，就不能认定它的串型与清册不同；应按每串片数×串数、每串金具数×串数核对总数）。
finding 以档位词开头，后接句号，120字内；两侧称“造价”（造价成套项一侧）和“清册”。
- “一致”“基本一致”：用一段工程师口吻的复核意见说明整套对应关系，并具体点明配置差异（型号、数量）。
- “部分一致”“不一致”：只写不一致的地方，一致的地方不写（不写“同为××”“数量一致”之类）。档位词和句号之后直接以差异类别开头，类别只能是 数量不一致、配置不一致、组成件缺失、名称不一致，后接冒号写清两侧各是什么；多处差异用“；”分开，例如“部分一致。数量不一致：造价12串，清册6串；组成件缺失：清册未列QP-10球头挂环。”
- 判“部分一致”前先确认差异是真的：按每串（每套）配置×数量折算后相符的，不算数量或配置不一致。
“组成比对”是系统按原表声明逐项比对的结果，可直接引用。
有“上次评审未通过校验”字段的组，是你上次的回答被程序校验退回，请针对其中指出的问题重新评审。
有“合计数量核对”的组是同一物资在一侧或两侧分几行列出：整套数量按两侧合计比对，以“合计数量核对”为准，不得只拿其中一行比数量。
输出 JSON：{{"sets":[{{"row_id":0,"band":"基本一致","finding":"基本一致。……"}}]}}

待整体复核的成套项：
{payload}"""


def _set_verdict_issue(entry, payload, row):
    """校验模型对成套项的评审：结论合法、原因以结论开头且有实质内容、与程序核实的整套数量不矛盾。"""
    band, finding = str(entry.get("band") or "").strip(), re.sub(r"\s+", " ", str(entry.get("finding") or "")).strip()
    if band not in ROW.REVIEW_BANDS:
        return f"结论“{band}”不是四档之一"
    prefix = ROW._BAND_PREFIX_RE.match(finding)
    if not prefix or prefix.group(1) != band:
        return f"原因没有以结论“{band}”开头"
    if len(finding) <= len(band) + 4:
        return "原因过短，没有说明依据"
    agreeing = band in (ROW.BAND_CONSISTENT, ROW.BAND_BASIC)
    if agreeing and payload.get("_total_same") is False:
        return f"程序核实两侧合计数量不同（{payload['合计数量核对']}），与结论“{band}”矛盾"
    if agreeing and row.get("匹配_序号") and "_total_same" not in payload:
        checks = ROW._program_checks(row)
        if checks["_qty_verified"] and checks["_qty_state"] == "diff":
            return f"程序核实整套数量相对差{checks['_qty_ratio'] * 100:.2f}%，超过1%，与结论“{band}”矛盾"
    if not agreeing:
        body = ROW._strip_band_prefix(finding)
        if not re.match(r"\s*(%s)\s*[：:]" % "|".join(SET_DIFF_LABELS), body):
            return f"结论“{band}”的原因须在档位词后直接以差异类别开头（{'、'.join(SET_DIFF_LABELS)}之一，后接冒号）"
        phrase = next((w for w in ROW._AGREEING_PHRASES if w in body), "")
        if phrase:
            return f"结论“{band}”的原因只写不一致之处，不要写一致的地方（出现“{phrase}”）"
        if payload.get("_total_same") and body.lstrip().startswith("数量不一致"):
            return f"程序核实两侧合计数量相同（{payload['合计数量核对']}），原因却写“数量不一致”"
    return ""


SET_DIFF_LABELS = ("数量不一致", "配置不一致", "组成件缺失", "名称不一致")


def review_sets(rows, sources, targets, api_key):
    """成套项整体复核：模型整体推理，同时给出结论与原因；校验不通过的重新评审，仍不通过的标为“评审未完成”。"""
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    payloads = {i: _set_payload(i, r, src_by, tgt_by, rows) for i, r in enumerate(rows) if r.get("_set_role") == "set"}
    verdicts = {}
    issues = {i: "未配置在线模型，未进行模型评审" for i in payloads}
    pending, attempts = (sorted(payloads) if api_key else []), 0
    for attempt in range(1 + ROW.ROW_REVIEW_RETRIES):
        if not pending:
            break
        attempts = attempt + 1
        size = LLM_SET_BATCH if attempt == 0 else ROW.ROW_REVIEW_RETRY_BATCH
        for start in range(0, len(pending), size):
            ids = pending[start:start + size]
            batch = []
            for i in ids:
                item = {k: v for k, v in payloads[i].items() if not k.startswith("_")}
                if attempt:
                    item["上次评审未通过校验"] = issues[i]
                batch.append(item)
            try:
                data = ctx.llm_json(SET_REVIEW_PROMPT.format(payload=json.dumps(batch, ensure_ascii=False)), api_key, max_tokens=4000)
            except Exception as exc:
                print(f"成套项整体复核失败：{exc}")
                for i in ids:
                    issues[i] = f"模型调用失败（{str(exc)[:60]}）"
                continue
            answered = {}
            for entry in (data or {}).get("sets", []) or []:
                try:
                    row_id = int(entry.get("row_id"))
                except (TypeError, ValueError):
                    continue
                if row_id in ids:
                    answered[row_id] = None if row_id in answered else entry   # 重复返回的作废
            for i in ids:
                entry = answered.get(i)
                issue = "模型未返回本项评审" if i not in answered else "模型对本项返回了多个结论" if entry is None \
                    else _set_verdict_issue(entry, payloads[i], rows[i])
                if issue:
                    issues[i] = issue
                    continue
                band = str(entry["band"]).strip()
                verdicts[i] = (band, re.sub(r"\s+", " ", str(entry["finding"])).strip()[:400])
                issues.pop(i, None)
        pending = [i for i in pending if i in issues]
    for row_id, payload in payloads.items():
        row = rows[row_id]
        if row_id in verdicts:
            band, finding = verdicts[row_id]
            row["成套项_复核"] = "模型整体复核"
        else:
            reason = issues[row_id] if not attempts else f"模型评审{attempts}次未通过校验：{issues[row_id]}"
            evidence = ROW._strip_band_prefix(_rule_verdict(payload, row)[1])
            band, finding = ROW.BAND_UNREVIEWED, f"{ROW.BAND_UNREVIEWED}。{reason}。程序比对（仅供人工复核，非结论）：{evidence}"
            row["成套项_复核"] = "评审未完成"
        row["_set_band"], row["_set_finding"] = band, ROW._strip_band_prefix(finding)
        row["匹配_发现"] = f"{band}。{row['_set_finding']}"
    # 多对多匹配：组成件取所属成套项的同一结论；所属成套项未参与整体复核的保持待复核
    owners = {str(rows[i]["源_序号"]): rows[i] for i in payloads}
    for row in rows:
        owner = owners.get(str(row.get("成套项_可研序号") or "")) if row.get("_set_role") == "covered" else None
        if owner:
            name = head(TXT._src_name(src_by[str(owner["源_序号"])]))
            row["_set_band"] = owner["_set_band"]
            row["_set_finding"] = _member_finding(row, name, owner["_set_finding"])
            row["匹配_发现"] = f"{row['_set_band']}。{row['_set_finding']}"
    return len(payloads)
