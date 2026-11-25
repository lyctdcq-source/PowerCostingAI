"""匹配·成套对应：成套设备、系统和屏柜在造价与清册中的拆分方式常常不同，用模型做跨层级的多对多对应。

造价常把一套设备按整套计价写成一行（“费用包含…/含以下/内含”），或在父项下逐项列出另行计价的子项；
清册常列成套行（屏、系统）再列组成件，组成件也可能分散在不同章节。两侧因此可能是一对一、一对多、
多对一或多对多。本技能把两侧的成套结构和尚未配对的条目交给模型，输出“成套对应组”：
组内两侧条目随整套核对，模型能确定是同一件的再逐项配对、逐项核对数量。

结果写入 covers：键为组的造价主项，值为组成员——
  {"target": 清册序号}：清册侧组成件（造价无单列对应）；
  {"source": 造价序号}：造价侧组成件（清册无单列对应）；
  {"source": 造价序号, "target": 清册序号}：组内逐项配对。
模型未接入、调用失败或没有编组时，按原表结构（成套项整体比对技能）兜底。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
"""
import json
import re

TXT = skill("text_rules")
SETS = skill("set_coverage")
QTY = skill("quantity_rules")

SET_LIKE = r"串|成套|系统"     # 已配对条目中名称带这些词的，也交给模型看能否与另一侧的多条组成对应组
BATCH_LINES = 90           # 每次送给模型的清册行数上限（按章节分批，并行调用）；造价侧每批都完整给出
# 实测一次给 240 行时模型只编出少数几组，大系统（火灾报警、视频监控等）的散列组成件被漏掉
MAX_TOKENS = 8000

SET_MATCH_PROMPT = """你是资深电力工程造价复核专家，负责核对可研估算与设计清册中的成套设备、系统和屏柜。

两表对同一套设备的拆分方式经常不同：
- 可研估算常把一套设备按整套计价写成一行（名称里写“费用包含…/含以下/内含”），或在父项下逐项列出另行计价的子项；
- 设计清册常先列成套行（屏、柜、系统），再列组成件；组成件也可能分散在不同章节。
所以两侧对应关系可能是一对一、一对多、多对一或多对多，并且可以跨章节、跨层级。

任务：找出两侧描述同一套成套设备或系统的条目，编成“成套对应组”。

判定规则：
1. 同一套 = 功能系统相同，且服务对象、安装位置相同。不同对象的实例（1号/2号主变、不同间隔或线路、本站/对侧电站、A网/B网、第一套/第二套）不得合并到一组。
2. 组成员只放：该套设备的屏柜本体、装置、主机、板卡、软件、成套附件，以及原表写明含在该套内的条目。
   造价按整套计价的系统（如火灾自动报警系统、环境及视频监控系统、计算机监控系统、直流系统），清册常在同名或相关章节下逐条列出探测器、模块、控制器、摄像机、门禁、测控装置等组成件而没有成套行：
   这些组成件应全部编入该系统的组。造价系统条目“[已配]”的清册条目通常是该系统的主机或主屏，与它同章节的组成件一并编入。
3. 造价中单独计价、清册中也单独列出的设备（如电子围栏、蓄电池组、UPS、交换机），应自成一组或保持一对一，不要并入更大的系统组。
4. 电缆、光缆、钢材、管材、螺栓、防火封堵、安装辅材、安装费/施工费等通用材料或费用，一般不放入组；
   但清册把它们列在某系统的章节或成套行之下（如视频监控系统下的“施工费（包含材料及施工费）”及其安装费、光缆、接地线、辅材等子项），
   而造价对该系统整套计价、没有单独列出这些材料或费用时，它们随该系统编入组。
5. 绝缘子串、金具串也是成套对应：清册常按“串”列整串（规格写明内含绝缘子片数和金具），造价常把绝缘子（片）和挂环、联板、挂板、线夹等金具逐项列出。
   按电压等级、安装位置（如110kV/35kV配电装置）和串型（耐张/悬垂）把清册整串与造价绝缘子、金具编为一组；不同电压等级或位置的串不得合并。
   用数量对上号：清册串数 × 每串绝缘子片数 = 造价绝缘子片数（如 12串×18片=216片、6串×18片=108片）；同一电压等级有几种串时，每种串各成一组。
   造价的金具通常紧跟在所属绝缘子行之后列出（编号连续），按这个顺序把金具归到对应的绝缘子行所在的组。
6. 已一对一配对的条目：配对正确且属于这一套的，放进组并在 pairs 中保留该配对；与组外物资的配对明显正确的，不要拆开放进组。
7. 组内能确定是同一件物资的，写进 pairs（造价编号, 清册编号），用于逐项核对数量；其余成员随整套核对。
8. 只为成套设备或系统编组，普通单件物资的一对一配对不需要输出。不确定的不要编组。每个编号最多出现在一个组里。
9. 每组至少要有一条造价条目和一条清册条目。

编号：造价条目以 K 开头，清册条目以 Q 开头，编号按原表行序递增。缩进的“└”行是上一行成套项的组成件或子项。
“[已配]”表示该条目当前已一对一配对的对方编号。本次只给出清册的一部分章节，造价条目在每次都会完整给出；
只为本次给出的清册条目编组，造价系统条目可以出现在你输出的组里，即使它的其余组成件在其他章节。

可研估算的成套结构与未配对条目：
{sources}

设计清册的成套结构与未配对条目：
{targets}

只输出 JSON：{{"groups":[{{"name":"成套名称","kesheng":["K1"],"qingce":["Q1","Q2"],"pairs":[["K1","Q1"]],"basis":"对应依据，60字内"}}]}}，没有可编组的返回 {{"groups":[]}}。"""


FITTING = r"挂环|联板|挂板|球头|碗头|豌头|调整板|延长环|均压环|招弧角|U型环|U形环"
INSULATOR_MODEL = r"U\d+[A-Z]*/\d+[A-Z]*"


def string_groups(sources, targets):
    """绝缘子串按规则编组：清册“N串、内含M片<型号>”对造价同型号“P片”绝缘子（N×M=P），
    造价紧跟在该绝缘子行之后列出的金具（挂环、联板、挂板等）一并编入。数量与顺序都是确定的，不交给模型。"""
    voltage = lambda item: (re.findall(r"\d+kV", " ".join(item.get("层级路径") or [item.get("层级") or ""])) or [""])[0]
    order = sorted(sources, key=lambda s: int(TXT._seq(s)) if TXT._seq(s).isdigit() else 0)
    pieces = []
    for i, s in enumerate(order):
        name = TXT._src_name(s)
        model = re.search(INSULATOR_MODEL, name or "")
        if "绝缘子" in name and model and "串" not in name and str(s.get("Estimation_Unit")).strip() == "片":
            fittings = []
            for nxt in order[i + 1:]:
                if not re.search(FITTING, TXT._src_name(nxt) or "") or (nxt.get("层级路径") != s.get("层级路径")):
                    break
                fittings.append(TXT._seq(nxt))
            pieces.append((s, model.group(0), QTY._to_float_or_zero(s.get("Estimation_Quantity")), fittings))
    groups, used = [], set()
    for t in targets:
        spec = TXT._tgt_spec(t) or ""
        model, per = re.search(INSULATOR_MODEL, spec), re.search(r"内含[:：]?\s*(\d+)\s*片", spec)
        count = QTY._to_float_or_zero(t.get("数量"))
        if "绝缘子串" not in (TXT._tgt_name(t) or "") or not model or not per or not count:
            continue
        need = count * int(per.group(1))
        hits = [p for p in pieces if TXT._seq(p[0]) not in used and p[1] == model.group(0) and abs(p[2] - need) < 0.5
                and TXT.same_site(p[0], t)]
        hits.sort(key=lambda p: voltage(p[0]) != voltage(t))      # 同电压等级章节优先
        if not hits:
            continue
        s, _model, qty, fittings = hits[0]
        used.add(TXT._seq(s))
        groups.append({"rule": True, "name": TXT._tgt_name(t), "kesheng": [TXT._seq(s)] + fittings, "qingce": [TXT._seq(t)],
                       "pairs": [[TXT._seq(s), TXT._seq(t)]],
                       "basis": "绝缘子串按数量对应：%g串×%s片=%g片，金具为造价紧随该绝缘子行列出的条目" % (count, per.group(1), qty)})
    return groups


def _price(item):
    return QTY._to_float_or_zero(item.get("Estimation_Price")) * (QTY._to_float_or_zero(item.get("Estimation_Quantity")) or 1)


def _structure(items, parent_key, child_key):
    """返回 {主项序号: [子项序号]}：清册用“下级物资”，造价用子项的“上级物资”反查。"""
    by_seq = {TXT._seq(x): x for x in items}
    tree = {}
    if child_key:
        for x in items:
            kids = [str(c) for c in x.get(child_key) or [] if str(c) in by_seq]
            if kids:
                tree[TXT._seq(x)] = kids
    if parent_key:
        for x in items:
            parent = str(x.get(parent_key) or "")
            if parent in by_seq:
                tree.setdefault(parent, []).append(TXT._seq(x))
    return tree


def _descendants(seq, tree):
    out = []
    for kid in tree.get(seq, []):
        out.append(kid)
        out += _descendants(kid, tree)
    return out


def _src_line(s, pairs, tgt_by, indent=""):
    t = tgt_by.get(pairs.get(TXT._seq(s), ""))
    comps = "、".join(c.get("name", "") for c in (s.get("成套组成") or [])[:12])
    price = QTY._to_float_or_zero(s.get("Estimation_Price"))
    return "%s%s K%s %s | %s%s | 单价%s | 章节:%s%s%s" % (
        indent, "└" if indent else "", TXT._seq(s), TXT._clean(TXT._src_name(s), 70), s.get("Estimation_Quantity", ""),
        s.get("Estimation_Unit", ""), ("%g" % price) if price else "未单列",
        " / ".join(s.get("层级路径") or [s.get("层级", "")]), ("| 原表声明组成:" + comps) if comps else "",
        (" | [已配]Q%s %s" % (TXT._seq(t), TXT._clean(TXT._tgt_name(t), 24))) if t else "")


def _tgt_line(t, paired_by, src_by, indent=""):
    s = src_by.get(paired_by.get(TXT._seq(t), ""))
    return "%s%s Q%s %s %s | %s%s | 章节:%s%s" % (
        indent, "└" if indent else "", TXT._seq(t), TXT._clean(TXT._tgt_name(t), 36), TXT._clean(TXT._tgt_spec(t), 50),
        t.get("数量", ""), t.get("单位", ""), " / ".join(t.get("层级路径") or [t.get("层级", "")]),
        (" | [已配]K%s %s" % (TXT._seq(s), TXT._clean(TXT._src_name(s), 24))) if s else "")


def _pool(sources, targets, pairs, reserved=()):
    """挑出需要做成套对应的条目：两侧成套结构（尚有未配对成员，或造价按整套计价），以及其余未配对的单行。"""
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    paired_by = {t: s for s, t in pairs.items()}
    material = lambda x: not x.get("计量性质")
    s_tree, t_tree = _structure(sources, "上级物资", None), _structure(targets, None, "下级物资")
    s_heads = [s for s in sources if (TXT._seq(s) in s_tree or SETS.is_whole_priced(s, sources)) and not s.get("上级物资")]
    t_heads = [t for t in targets if (TXT._seq(t) in t_tree or t.get("成套行")) and not t.get("上级物资")]

    s_blocks, s_used = [], set()
    for head in s_heads:
        seqs = [TXT._seq(head)] + _descendants(TXT._seq(head), s_tree)
        open_member = any(q not in pairs for q in seqs)
        if not (open_member or SETS.is_whole_priced(head, sources)):
            continue                        # 逐项计价且已逐条配对的结构不需要再编组
        s_blocks.append([(q, _src_line(src_by[q], pairs, tgt_by, "  " if i else "")) for i, q in enumerate(seqs)])
        s_used.update(seqs)
    t_blocks, t_used = [], set()
    for head in t_heads:
        seqs = [TXT._seq(head)] + _descendants(TXT._seq(head), t_tree)
        partner = src_by.get(paired_by.get(TXT._seq(head), ""))
        open_member = any(q not in paired_by for q in seqs)
        if not (open_member or (partner and SETS.is_whole_priced(partner, sources))):
            continue
        t_blocks.append([(q, _tgt_line(tgt_by[q], paired_by, src_by, "  " if i else "")) for i, q in enumerate(seqs)])
        t_used.update(seqs)
    # 未配对的单行，以及虽已配对但名称像成套（串、成套、系统）的单行，都交给模型判断能否编组
    set_like = lambda name: re.search(SET_LIKE, str(name or ""))
    s_single = [[(TXT._seq(s), _src_line(s, pairs, tgt_by))] for s in sources if TXT._seq(s) not in s_used and material(s)
                and (TXT._seq(s) not in pairs or set_like(TXT._src_name(s)))]
    t_single = [[(TXT._seq(t), _tgt_line(t, paired_by, src_by))] for t in targets if TXT._seq(t) not in t_used and material(t)
                and (TXT._seq(t) not in paired_by or set_like(TXT._tgt_name(t)))]
    if reserved:
        keep = lambda side, blocks: [b for b in ([x for x in block if (side, x[0]) not in reserved] for block in blocks) if b]
        s_blocks, s_single, t_blocks, t_single = keep("K", s_blocks), keep("K", s_single), keep("Q", t_blocks), keep("Q", t_single)
    # 清册按章节排序后分批，同一章节的散列组成件尽量落在同一批里
    chapter = lambda block: (" / ".join(tgt_by[block[0][0]].get("层级路径") or [tgt_by[block[0][0]].get("层级", "")]), int(block[0][0]) if block[0][0].isdigit() else 0)
    return s_blocks + s_single, sorted(t_blocks + t_single, key=chapter)


def _chunks(blocks, limit):
    """按块切分，不把一个成套结构拆到两批。"""
    batch, size = [], 0
    for block in blocks:
        if batch and size + len(block) > limit:
            yield batch
            batch, size = [], 0
        batch.append(block)
        size += len(block)
    if batch:
        yield batch


def _ask_groups(src_blocks, tgt_blocks, api_key, task_id):
    """清册分批并行询问；同一造价条目在不同批次里各自编出的组合并为一组（造价系统的组成件可能分散在多个章节）。"""
    batches = list(_chunks(tgt_blocks, BATCH_LINES)) or [[]]
    source_text = "\n".join(line for block in src_blocks for _seq, line in block) or "（无）"

    def run(batch):
        prompt = SET_MATCH_PROMPT.format(sources=source_text, targets="\n".join(line for block in batch for _seq, line in block) or "（无）")
        try:
            data = ctx.llm_json(prompt, api_key, max_tokens=MAX_TOKENS)
        except Exception as exc:
            ctx.log(task_id, "成套对应：一批模型调用失败，该批保留原表结构判断（%s）" % str(exc)[:60])
            return []
        return [g for g in (data.get("groups") or []) if isinstance(g, dict)]

    raw, done = [], 0
    for got in ctx.parallel(run, batches, ctx.setting("MAX_WORKERS", 4)):
        raw += got
        done += 1
        ctx.log(task_id, "成套对应：模型已完成 %d/%d 批，得到 %d 个对应组" % (done, len(batches), len(raw)), 0.36)
    return _merge(raw)


def _merge(groups):
    """含同一造价条目的组合并：成员取并集，组内配对与依据保留。"""
    merged = []
    for g in groups:
        ks = {str(k).strip().upper().lstrip("K") for k in g.get("kesheng") or []}
        hit = [m for m in merged if m["_k"] & ks]
        target = hit[0] if hit else {"_k": set(), "name": g.get("name"), "kesheng": [], "qingce": [], "pairs": [], "basis": g.get("basis") or ""}
        for other in hit[1:]:
            for key in ("kesheng", "qingce", "pairs"):
                target[key] += other[key]
            target["_k"] |= other["_k"]
            merged.remove(other)
        target["kesheng"] += list(g.get("kesheng") or [])
        target["qingce"] += list(g.get("qingce") or [])
        target["pairs"] += list(g.get("pairs") or [])
        target["_k"] |= ks
        if not hit:
            merged.append(target)
    for m in merged:
        m.pop("_k")
    return merged


def _ids(values, prefix, valid):
    out = []
    for v in values or []:
        text = str(v).strip().upper()
        if text.startswith(prefix):
            text = text[1:]
        if text in valid and text not in out:
            out.append(text)
    return out


def apply_groups(groups, sources, targets, pairs):
    """把模型给出的对应组落成配对与 covers。返回 (pairs, covers, 拆开的配对数)。

    原有的一对一配对视为硬约束：组成员的配对对象随之进组，作为组内逐项配对保留，模型不能改配；
    只有“成套主项 ↔ 单件组成件”这类层级错位的配对（如直流系统配到充电机屏）由编组纠正为主项 ↔ 成套行。
    """
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    pairs, covers, used_s, used_t, broken = dict(pairs), {}, set(), set(), 0
    s_head = lambda k: SETS.is_whole_priced(src_by[k], sources) or any(str(x.get("上级物资")) == k for x in sources)
    t_head = lambda q: bool(tgt_by[q].get("成套行") or tgt_by[q].get("下级物资"))
    # 原有配对是硬约束；只有“造价成套主项 ↔ 清册单件组成件”属于层级错位，允许编组纠正为主项 ↔ 成套行
    # （造价一行整套计价 ↔ 清册成套行，如电子围栏，是正常配对）
    same_name = lambda k, q: re.sub(r"\s", "", SETS.head(TXT._src_name(src_by[k]))) == re.sub(r"\s", "", SETS.head(TXT._tgt_name(tgt_by[q])))
    hard = lambda k, q: not (s_head(k) and not t_head(q)) or same_name(k, q)
    for g in groups:
        ks = [k for k in _ids(g.get("kesheng"), "K", src_by) if k not in used_s]
        qs = [q for q in _ids(g.get("qingce"), "Q", tgt_by) if q not in used_t]
        if not ks or not qs:
            continue
        # 本站与对侧电站的条目不能同组（模型偶尔会把对侧间隔的绝缘子串并入本站）
        ref = max(ks, key=lambda k: (s_head(k), _price(src_by[k])))
        ks = [k for k in ks if TXT.same_site(src_by[k], src_by[ref])]
        qs = [q for q in qs if TXT.same_site(tgt_by[q], src_by[ref])]
        # 绝缘子串组只吸收串内金具（挂环、联板、球头、碗头等）；耐张线夹、悬垂线夹属导线金具，清册单独列项
        if any(re.search(r"绝缘子串", TXT._tgt_name(tgt_by[q])) for q in qs):
            ks = [k for k in ks if re.search(r"绝缘子|" + FITTING, TXT._src_name(src_by[k]))]
            if not ks:
                continue
        # 造价条目已与清册成套行下的同名组成件配对（如智能网关屏 ↔ 智能网关屏），该成套行只是标题，不进组
        same_child = {pairs[k] for k in ks if pairs.get(k) in tgt_by and same_name(k, pairs[k])}
        qs = [q for q in qs if not (tgt_by[q].get("成套行") and q not in pairs.values()
                                    and same_child & {str(c) for c in tgt_by[q].get("下级物资") or []})]
        # 闭包：成员的硬配对对象一并进组（规则编组的成员和配对是确定的，不做闭包）
        changed = not g.get("rule")
        while changed:
            changed = False
            paired_by = {t: s for s, t in pairs.items()}
            for q in list(qs):
                k = paired_by.get(q)
                # 单件的配对对象跟着进组；对象是成套主项而模型把这条清册物资编进了别的组，以模型为准
                if k and k not in ks and k not in used_s and not s_head(k):
                    ks.append(k)
                    changed = True
            for k in list(ks):
                q = pairs.get(k)
                # 造价条目原来的对应物一律跟着进组（模型漏列主设备时，如光传输设备的光电端机，补回来）
                if q and q not in qs and q not in used_t:
                    qs.append(q)
                    changed = True
        if not qs:
            continue
        # 规则编组以规则给出的配对为准；模型编组以原有的硬配对为准，再补模型给出的组内配对
        inner = [] if g.get("rule") else [(k, pairs[k]) for k in ks if pairs.get(k) in qs and hard(k, pairs[k])]
        for pair in g.get("pairs") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                k, q = _ids([pair[0]], "K", src_by), _ids([pair[1]], "Q", tgt_by)
                if k and q and k[0] in ks and q[0] in qs and all(k[0] != a and q[0] != b for a, b in inner):
                    inner.append((k[0], q[0]))
        if len(ks) == 1 and len(qs) == 1:
            # 只有一对一：按普通配对处理，不形成成套对应组
            for k, t in list(pairs.items()):
                if (t == qs[0]) != (k == ks[0]):
                    pairs.pop(k)
                    broken += 1
            pairs[ks[0]] = qs[0]
            used_s.update(ks)
            used_t.update(qs)
            continue
        # 组的主项：造价取成套主项（整套计价或有子项）中金额最大的；清册取它的组内配对对象，否则取成套行
        anchor_s = ks[0] if g.get("rule") else max(ks, key=lambda k: (s_head(k), _price(src_by[k])))
        anchor_t = next((b for a, b in inner if a == anchor_s), None)
        if anchor_t is None:
            free = [q for q in qs if all(b != q for _a, b in inner)]
            anchor_t = next((q for q in free if t_head(q)), None) \
                or (pairs.get(anchor_s) if pairs.get(anchor_s) in free else None) or next(iter(free), None)
        if anchor_t is None:            # 清册成员都已在组内逐项配对：主项取其中一对
            anchor_s, anchor_t = inner[0]
        inner = [(a, b) for a, b in inner if a != anchor_s and b != anchor_t]
        # 组成员原来与组外物资的配对（层级错位的）在这里拆开
        for k in ks:
            if pairs.get(k) and pairs[k] not in qs:
                pairs.pop(k)
                broken += 1
        for k, t in list(pairs.items()):
            if t in qs and k not in ks:
                pairs.pop(k)
                broken += 1
        for k in ks:
            if pairs.get(k) in qs and (k, pairs[k]) not in inner and k != anchor_s:
                pairs.pop(k)            # 组内层级错位的配对，改为随整套核对
        for a, b in inner + [(anchor_s, anchor_t)]:
            pairs[a] = b
        basis = "成套对应：" + (TXT._clean(g.get("basis"), 80) or TXT._clean(g.get("name"), 30) or "模型判定为同一套")
        paired_s, paired_t = {a for a, _ in inner} | {anchor_s}, {b for _, b in inner} | {anchor_t}
        covers[anchor_s] = ([{"source": a, "target": b, "basis": basis} for a, b in inner]
                            + [{"target": q, "basis": basis} for q in qs if q not in paired_t]
                            + [{"source": k, "basis": basis} for k in ks if k not in paired_s])
        used_s.update(ks)
        used_t.update(qs)
    return pairs, covers, broken


SWEEP_SHARE = 0.5    # 章节中已有过半条目归入同一组时，该章节其余未配对条目随组归入


def sweep_chapters(covers, sources, targets, pairs, fixed=()):
    """章节补漏：模型分批时同一章节可能被拆开，个别组成件会漏编。某章节的条目过半已归入同一成套组时，
    该章节其余既未配对、也不在任何组里的实物条目随该组归入。返回补入条数。"""
    tgt_by = {TXT._seq(t): t for t in targets}
    chapter = lambda q: " / ".join(tgt_by[q].get("层级路径") or [tgt_by[q].get("层级") or ""])
    members = {a: {str(c["target"]) for c in v if c.get("target")} | ({pairs[a]} if pairs.get(a) else set()) for a, v in covers.items()}
    grouped = set().union(*members.values()) if members else set()
    paired = set(pairs.values())
    free = [q for q in tgt_by if q not in grouped and q not in paired and ("Q", q) not in fixed
            and not tgt_by[q].get("计量性质") and chapter(q)]
    added = 0
    for anchor, qs in members.items():
        by_chapter = {}
        for q in qs:
            by_chapter.setdefault(chapter(q), []).append(q)
        for name, inside in by_chapter.items():
            rest = [q for q in free if chapter(q) == name and q not in grouped]
            if not name or len(inside) < 3 or len(inside) < SWEEP_SHARE * (len(inside) + len(rest)):
                continue
            basis = "成套对应：同章节其余组成件随该组归入（章节过半条目已属该组）"
            for q in rest:
                covers[anchor].append({"target": q, "basis": basis})
                grouped.add(q)
                added += 1
    return added


def match_sets(sources, targets, pairs, api_key, task_id, merges=None):
    """成套对应：模型做两侧成套结构的多对多对应，其余条目按原表结构兜底。返回 (pairs, covers)。
    merges 为配对修正得到的合计对应组（如控制电缆 24km+7km 对 31km），原样保留，其成员不再参与编组。"""
    merges = {k: list(v) for k, v in (merges or {}).items()}
    fixed = ({("K", k) for k in merges} | {("Q", pairs[k]) for k in merges if pairs.get(k)}
             | {("K", str(c["source"])) for v in merges.values() for c in v if c.get("source")}
             | {("Q", str(c["target"])) for v in merges.values() for c in v if c.get("target")})
    rules = [g for g in string_groups(sources, targets)
             if not ({("K", str(k)) for k in g["kesheng"]} | {("Q", str(q)) for q in g["qingce"]}) & fixed]
    if rules:
        ctx.log(task_id, "成套对应：绝缘子串按数量规则对应 %d 组" % len(rules), 0.35)
    reserved = {("K", str(k)) for g in rules for k in g["kesheng"]} | {("Q", str(q)) for g in rules for q in g["qingce"]} | fixed
    groups = []
    if api_key:
        src_blocks, tgt_blocks = _pool(sources, targets, pairs, reserved)
        if src_blocks and tgt_blocks:
            ctx.log(task_id, "成套对应：造价 %d 块、清册 %d 块送模型判断跨层级对应" % (len(src_blocks), len(tgt_blocks)), 0.35)
            groups = _ask_groups(src_blocks, tgt_blocks, api_key, task_id)
    pairs, covers, broken = apply_groups(rules + groups, sources, targets, pairs)
    swept = sweep_chapters(covers, sources, targets, pairs, fixed)
    # 合计对应组放在章节补漏之后加入：它们是同一物资的数量合计，不随章节吸收其他条目
    for anchor, items in merges.items():
        covers.setdefault(anchor, []).extend(items)
    if swept:
        ctx.log(task_id, "成套对应：同章节补入漏编的组成件 %d 条" % swept, 0.37)
    if covers:
        members = sum(len(v) + 1 for v in covers.values())
        ctx.log(task_id, "成套对应：%d 个对应组、%d 条成员" % (len(covers), members)
                + ("，拆开组外配对 %d 对" % broken if broken else ""), 0.37)
    # 未编入对应组的条目按原表成套结构兜底（不再调用模型）
    in_group_s = set(covers) | {str(c["source"]) for v in covers.values() for c in v if c.get("source")}
    in_group_t = {pairs[k] for k in covers if k in pairs} | {str(c["target"]) for v in covers.values() for c in v if c.get("target")}
    rest_s = [s for s in sources if TXT._seq(s) not in in_group_s]
    rest_t = [t for t in targets if TXT._seq(t) not in in_group_t]
    rest_pairs = {s: t for s, t in pairs.items() if s not in in_group_s and t not in in_group_t}
    for anchor, items in SETS.cover_sets(rest_s, rest_t, rest_pairs, "" if covers else api_key, task_id).items():
        covers.setdefault(anchor, []).extend(items)
    return pairs, covers
