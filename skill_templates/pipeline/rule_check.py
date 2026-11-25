"""匹配·规则校验：对模型配对做确定性复核：型号后缀冲突交换、领域知识身份属性改配、未配项补配，
以及同名同规格的多条物资按数量重新分配。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
跨技能调用写作“别名.函数名”，别名由 skill("技能ID") 取得；模型调用、日志、领域知识等由宿主通过 ctx 提供。
"""

import re
from collections import defaultdict

TXT = skill("text_rules")
QTY = skill("quantity_rules")


RULE_RECOVER_MIN = 55         # 规则层补配未配项的最低分
REBALANCE_MAX = 7             # 同规格组超过此条数不做排列搜索
PIECE_UNITS = {"片", "只", "个", "支", "根"}
PACK_UNITS = {"串", "组"}
_PACK_COUNT = re.compile(r"内含[:：]?\s*(\d+)\s*(片|只|个|支|根)")
_CODE = re.compile(r"[A-Z]{1,5}\d{2,5}[A-Z]{0,4}(?:/\d{2,4}[A-Z]?)?")   # U100BP/146D、TJR120、LB20A 一类型号编码

# ------------------------------------------------------- 规则校验（确定性）
_TOKEN_CACHE = {}

def _tokens_of(text):
    key = str(text)[:160]
    if key not in _TOKEN_CACHE:
        try:
            _TOKEN_CACHE[key] = TXT._extract_strong_model_tokens(key) or set()
        except Exception:
            _TOKEN_CACHE[key] = set()
    return _TOKEN_CACHE[key]

def _token_affinity(src_item, tgt_item):
    """型号强 token 的匹配强度：1=明确匹配，-1=明确冲突，0=信息不足。

    必须用"包含式"比较而不是集合精确相交：YH5WZ-17/45GY 与 YH5WZ-17/45 是同一型号的
    两种写法，精确相交会判成冲突。早期版本正是因此把避雷器的正确配对罚到 9.7 分、
    而把无型号信息的熔断器抬到 35.7 分，2-opt 于是换出了一串置换环错配。
    """
    s_tok = _tokens_of("%s %s" % (TXT._src_name(src_item), TXT._src_spec(src_item)))
    t_tok = _tokens_of("%s %s" % (TXT._tgt_name(tgt_item), TXT._tgt_spec(tgt_item)))
    if not s_tok or not t_tok:
        return 0
    for a in s_tok:
        for b in t_tok:
            if _token_pair_affinity(_norm_model_token(a), _norm_model_token(b)) > 0:
                return 1
    return -1

_CABLE_SERIES = r"(YJLV22|YJLV|YJV22|YJV62|YJV32|YJV42|YJV|KVVP22|KVVP|KVV22|KVV|VLV|VV|YJY)"

def _norm_model_token(tok):
    """归一化型号 token，供跨表比较。

    两步：
      1. 剥阻燃/耐火等级前缀——ZR-YJV22 与 ZAN-YJV22 是同一型号的两种写法；
      2. 电缆型号只取"系列+铠装代号"——原始 token 常把电压和芯数粘进来
         （ZR-YJV22 1kV 双芯 10 → "ZR-YJV221KV"，清册侧 → "ZAN-YJV22-0.6/1-2×10"），
         整串比较永远对不上，取核心后才能看出都是 YJV22。
    """
    raw = re.sub(r"^(?:WDZN|WDZ|WDN|ZRN|ZAN|ZRA|ZA|ZR|ZN|NH)[-]?", "", str(tok or "").upper())
    match = re.match(r"^%s(\d{0,2})" % _CABLE_SERIES, raw)
    if not match:
        return raw
    series, code = match.group(1), match.group(2)
    # 紧跟 KV/V 的那两位是电压不是铠装代号：YJV1KV 的 "1" 属于 1kV
    if code and re.match(r"^\s*K?V", raw[match.end():], re.IGNORECASE):
        code = ""
    return series + code

def _common_prefix(a, b):
    size = 0
    while size < min(len(a), len(b)) and a[size] == b[size]:
        size += 1
    return a[:size]


def _token_pair_affinity(a, b):
    """归一化后的两个型号 token 是否指同一产品。

    关键区分：多出来的是字母后缀还是数字后缀。
      YH5WZ-17/45GY 与 YH5WZ-17/45 —— 多出 "GY"，是同型号的产地/工况变体，算匹配；
      YJV22 与 YJV       —— 多出 "22"，是钢带铠装与无铠装两种产品，算冲突。
    """
    if not a or not b:
        return 0
    if a == b:
        return 1
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    if longer.startswith(shorter):
        extra = longer[len(shorter):].lstrip("-/ ")
        return 1 if extra and not extra[0].isdigit() else -1
    common = _common_prefix(a, b)
    if len(common) >= 6 and any(ch.isdigit() for ch in common):
        return 1
    return -1

def _name_affinity(src_item, tgt_item):
    """名称字面相似度，用于补配时的兜底判断。"""
    try:
        return TXT._jaccard_similarity("%s %s" % (TXT._src_name(src_item), TXT._src_spec(src_item)),
                                   "%s %s" % (TXT._tgt_name(tgt_item), TXT._tgt_spec(tgt_item)))
    except Exception:
        return 0.0

def _qty_consistent(src_item, tgt_item):
    """两侧数量折算到同一基准后是否一致（基准不同则返回 None 表示无从判断）。"""
    try:
        s_base, s_qty = QTY._quantity_unit_base_value(src_item)
        t_base, t_qty = QTY._quantity_unit_base_value(tgt_item)
    except Exception:
        return None
    if not (s_base and t_base and s_base == t_base and s_qty > 0 and t_qty > 0):
        return None
    return abs(s_qty - t_qty) / max(s_qty, t_qty) <= 0.01

# ------------------------------------------------- 同名同规格物资按数量分配
def _identity(item, source):
    """同一物资的识别键：去掉章节编号和空白后的名称、规格与单位。"""
    name = TXT._src_name(item) if source else TXT._tgt_name(item)
    spec = TXT._src_spec(item) if source else TXT._tgt_spec(item)
    unit = TXT._src_unit(item) if source else item.get("单位", "")
    name = re.sub(r"^[\s\"“”\d.、．\t]+", "", name)
    return tuple(re.sub(r"[\s\"“”　]", "", str(v)).lower() for v in (name, spec, unit))


def _comparable(item, source):
    """数量折算到可比基准：长度/重量/面积按单位换算；片、只等件数按各自单位比（片只与片比，
    挂环的“只”不与绝缘子的“片”比）；“串、组”按规格写明的“内含18片”折成片数；其余台、套、面等按件数比。
    返回 (基准, 数量) 或 None。"""
    try:
        base, qty = QTY._quantity_unit_base_value(item)
    except Exception:
        return None
    if base and qty > 0:
        return base, qty
    unit = str(TXT._src_unit(item) if source else item.get("单位", "")).strip()
    qty = QTY._to_float_or_zero(TXT._src_qty(item) if source else item.get("数量"))
    if qty <= 0 or not unit:
        return None
    if unit in PACK_UNITS:
        found = _PACK_COUNT.search("%s %s" % (TXT._tgt_name(item), TXT._tgt_spec(item)))
        if found:
            return "件:" + found.group(2), qty * float(found.group(1))
    return ("件:" + unit if unit in PIECE_UNITS else "计数"), qty


def _codes(text):
    return set(_CODE.findall(re.sub(r"[-—\s]", "", str(text or "").upper())))


def _code_conflict(src_item, tgt_item):
    """两侧都写了字母加数字的型号编码却没有一个相同（如 U100BP/146D 与 U70BP/146D），视为不同物资。"""
    a = _codes("%s %s" % (TXT._src_name(src_item), TXT._src_spec(src_item)))
    b = _codes("%s %s" % (TXT._tgt_name(tgt_item), TXT._tgt_spec(tgt_item)))
    return bool(a and b and not a & b)


def _quantity_cost(src_item, tgt_item, kg):
    """一对物资的数量相对差（0 为一致）；无从比较返回 None。"""
    a, b = _comparable(src_item, True), _comparable(tgt_item, False)
    if a and b and a[0] == b[0]:
        return abs(a[1] - b[1]) / max(a[1], b[1])
    check = kg.quantity_check(src_item, tgt_item) if kg else None
    if check and "ratio" in check:
        return float(check["ratio"])
    return None


def _ratio_spread(assigned, src_by, tgt_by):
    """单位无法直接换算时（如 t 与 m 缺单重参数），同规格各对的数量比应接近同一常数，取最大比/最小比。"""
    ratios = []
    for s, t in assigned:
        sq = QTY._to_float_or_zero(TXT._src_qty(src_by[s]))
        tq = QTY._to_float_or_zero(tgt_by[t].get("数量"))
        if sq <= 0 or tq <= 0:
            return None
        ratios.append(tq / sq)
    return max(ratios) / min(ratios) if ratios else None


def _assignment_score(assigned, missing, src_by, tgt_by, costs):
    costs = [costs[pair] for pair in assigned]
    if all(c is not None for c in costs):
        return ("差", sum(costs) + missing)
    return ("比", _ratio_spread(assigned, src_by, tgt_by))


def _assignments(members, slots):
    """每条估算物资分到一个未占用的清册项或不分配（None）的全部方案。"""
    if not members:
        yield []
        return
    head, rest = members[0], members[1:]
    for slot in [None] + slots:
        for tail in _assignments(rest, [t for t in slots if t != slot]):
            yield [(head, slot)] + tail


def _rebalance_same_spec(pairs, sources, targets, task_id):
    """同名同规格的多条估算物资（如三条“裸铜绞线 TJR-120”、三条“绝缘子 U100BP/146D”），在它们已配的
    清册项和同规格的空闲清册项之间，按折算后的数量重新分配。只有数量吻合度确实变好才改动；
    数量相同的并列项（如多台同型号屏柜）保留模型按上下文作出的选择；不新建型号冲突的配对。"""
    kg = ctx.knowledge()
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    groups = defaultdict(list)
    for s in sources:
        groups[_identity(s, True)].append(TXT._seq(s))
    moved = []
    for members in groups.values():
        current = {m: pairs.get(m) for m in members}
        slots = [t for t in current.values() if t]
        if len(members) < 2 or not slots:
            continue
        kinds = {_identity(tgt_by[t], False) for t in slots}
        used = set(pairs.values())
        slots += [TXT._seq(t) for t in targets if TXT._seq(t) not in used and _identity(t, False) in kinds]
        if len(members) > REBALANCE_MAX or len(slots) > REBALANCE_MAX:
            continue
        size = min(len(members), len(slots))
        now = [(s, t) for s, t in current.items() if t]
        costs = {(s, t): _quantity_cost(src_by[s], tgt_by[t], kg) for s in members for t in slots}
        # 模型已给出的配对保留可选；新配对不得型号冲突或身份属性冲突
        allowed = {(s, t): (s, t) in now or not (_token_affinity(src_by[s], tgt_by[t]) < 0 or _code_conflict(src_by[s], tgt_by[t])
                                                 or (kg and kg.conflicts(src_by[s], tgt_by[t])))
                   for s in members for t in slots}
        base = _assignment_score(now, size - len(now), src_by, tgt_by, costs)
        if base[1] is None:
            continue
        best, best_score = None, None
        for plan in _assignments(members, slots):
            assigned = [(s, t) for s, t in plan if t]
            if base[0] == "比" and len(assigned) != len(now):
                continue  # 只按数量比重排时不增删配对，否则少配一条也能让比值“更整齐”
            if not all(allowed[pair] for pair in assigned):
                continue
            score = _assignment_score(assigned, size - len(assigned), src_by, tgt_by, costs)
            if score[0] != base[0] or score[1] is None:
                continue
            if best_score is None or score[1] < best_score[1] - 1e-9:
                best, best_score = dict(assigned), score
        if best is None or not (best_score[1] < base[1] - 1e-9 if base[0] == "差" else best_score[1] < base[1] * 0.8 and best_score[1] <= 1.2):
            continue
        for s in members:
            if pairs.get(s) != best.get(s):
                moved.append((s, pairs.get(s) or "无", best.get(s) or "无"))
                if best.get(s):
                    pairs[s] = best[s]
                else:
                    pairs.pop(s, None)
    if moved:
        ctx.log(task_id, "步骤 2/5：同名同规格物资按数量重新分配 %d 条：%s" % (
            len(moved), "；".join("估算%s 清册%s→%s" % m for m in moved)))
    return len(moved)


def rule_refine(pairs, sources, targets, task_id):
    """对 LLM 配对做确定性复核。

    刻意做得很窄：LLM 配对本身准确率已近 99%，规则层只负责它系统性弱的那一项——
    型号后缀（YJV22 钢带铠装 / YJV 无铠装）分辨不清导致的两条对调。
    交换条件是"当前至少一条型号明确冲突，且交换后两条都明确匹配"，
    不满足就一律不动，避免规则层反过来污染正确配对。
    """
    src_by, tgt_by = {TXT._seq(s): s for s in sources}, {TXT._seq(t): t for t in targets}
    conflict = [(s, t) for s, t in pairs.items()
                if s in src_by and t in tgt_by and _token_affinity(src_by[s], tgt_by[t]) < 0]
    swapped = 0
    for i in range(len(conflict)):
        for j in range(i + 1, len(conflict)):
            (s1, t1), (s2, t2) = conflict[i], conflict[j]
            if pairs.get(s1) != t1 or pairs.get(s2) != t2:
                continue
            if (_token_affinity(src_by[s1], tgt_by[t2]) > 0
                    and _token_affinity(src_by[s2], tgt_by[t1]) > 0):
                # 数量若能判断，交换后不应变差
                q_now = [_qty_consistent(src_by[s1], tgt_by[t1]), _qty_consistent(src_by[s2], tgt_by[t2])]
                q_new = [_qty_consistent(src_by[s1], tgt_by[t2]), _qty_consistent(src_by[s2], tgt_by[t1])]
                if q_new.count(False) > q_now.count(False):
                    continue
                pairs[s1], pairs[s2] = t2, t1
                conflict[i], conflict[j] = (s1, t2), (s2, t1)
                swapped += 1

    # 领域知识：身份属性（芯数×截面、规格尺寸等）冲突的配对在同品类内重新分配
    kg = ctx.knowledge()
    moved = kg.resolve_conflicts(pairs, src_by, tgt_by) if kg else []
    if moved:
        ctx.log(task_id, "步骤 2/5：领域知识按身份属性改配 %d 条：%s" % (
            len(moved), "；".join("估算%s 清册%s→%s" % m for m in moved)))

    # 未配源项补配：要求型号明确匹配，或名称高度相似且数量不矛盾
    used = set(pairs.values())
    recovered = 0
    for s in sources:
        s_seq = TXT._seq(s)
        if s_seq in pairs:
            continue
        best, best_score = None, 0.0
        for t in targets:
            t_seq = TXT._seq(t)
            if t_seq in used:
                continue
            aff = _token_affinity(s, t)
            if aff < 0 or (kg and kg.conflicts(s, t)):
                continue
            name_sim = _name_affinity(s, t)
            if _qty_consistent(s, t) is False and aff <= 0:
                continue
            score = (60.0 if aff > 0 else 0.0) + name_sim * 100.0
            if score > best_score:
                best, best_score = t_seq, score
        if best and best_score >= RULE_RECOVER_MIN:
            pairs[s_seq] = best
            used.add(best)
            recovered += 1
    rebalanced = _rebalance_same_spec(pairs, sources, targets, task_id)
    ctx.log(task_id, "步骤 2/5：规则校验完成，型号冲突交换 %d 处、补配 %d 条、同规格按数量改配 %d 条"
            % (swapped, recovered, rebalanced), 0.38)
    return pairs
