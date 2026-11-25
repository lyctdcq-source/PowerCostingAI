# -*- coding: utf-8 -*-
"""探查阶段：先弄清文件夹里有什么，再谈取数。

这是整条链路的第 0 步，也是之前缺的那一步。

原来的做法是人工先分析一遍，把「哪份文件提供什么字段」写进 sources.yaml，
系统只是照着执行。换个工程就得重来一遍，等于没有产品。

现在的顺序反过来了：

    扫描目录 → 判定每份文件是什么 → 探查表格结构 → 匹配字段 → 生成配置

产物就是原来的 sources / layout / mapping 三个配置，只不过由系统自己推导，
再交给人确认。

判定的依据是**结构特征优先、文件名辅助**——因为文件名叫什么不可控，
但「一行行对齐排列的文字」这个结构特征是客观的。
"""
import hashlib
import os
import re
from collections import Counter

import pymupdf

from . import cache as feat_cache

# ============================================================ 文件名线索
# 名字只是弱证据：命名习惯各院不同，所以只用来加分，不用来做最终判定。
NAME_HINTS = [
    ("table", ["明细表", "清册", "一览表", "汇总", "清单", "统计表", "跟开表",
               "参数表", "材料表", "设备表"]),
    ("doc", ["说明", "报告", "协议", "意见", "纪要", "函", "批复", "专章",
             "计算书", "任务书", "勘测", "勘察"]),
    ("drawing", ["图", "平面", "断面", "结构图", "组装图", "布置图", "示意图"]),
    ("budget", ["预算", "概算", "估算", "造价"]),
]

# 卷册目录也是线索：施工图按 综合/电气/结构/地质 分册，这个结构比较稳定
DIR_HINTS = [
    ("doc", ["综合"]),
    ("drawing", ["电气", "结构"]),
    ("doc", ["地质"]),
]


def _norm_name(s):
    return re.sub(r"[\s_\-（）()【】\[\]]+", "", s or "")


def name_score(name, kind):
    """文件名对这个类型的支持度，0~1。"""
    n = _norm_name(name)
    hits = 0
    for kw in dict(NAME_HINTS).get(kind, []):
        if kw in n:
            hits += 1
    return min(1.0, hits * 0.6)


# ============================================================ 结构信号
def _x_profile(words):
    """统计「哪几个横坐标反复出现」。

    表格的本质是：一大群文字对齐在少数几个 x 位置上。
    段落文字做不到这一点——每行的起始位置都在变。
    """
    per_row = {}
    for w in words:
        yc = round((w[1] + w[3]) / 2, 0)
        per_row.setdefault(yc, []).append(round((w[0] + w[2]) / 2, 0))

    if len(per_row) < 8:
        return 0.0, 0

    # 每个 x 位置被多少行用到
    cnt = Counter()
    for _y, xs in per_row.items():
        for x in set(xs):
            cnt[x] += 1

    rows = len(per_row)
    # 出现在 30% 以上行里的 x 位置，视为「列」
    cols = [x for x, c in cnt.items() if c >= max(3, rows * 0.3)]
    if not cols:
        return 0.0, 0

    # 列数够多、且覆盖率高的，才像表格
    cover = sum(cnt[x] for x in cols) / (rows * len(cols))
    score = min(1.0, len(cols) / 10) * min(1.0, cover)
    return round(score, 2), len(cols)


def _rows_of(words, tol=3.5):
    lines, cur = [], []
    for w in sorted(words, key=lambda x: ((x[1] + x[3]) / 2, x[0])):
        yc = (w[1] + w[3]) / 2
        if cur and abs(yc - cur[-1][0]) > tol:
            lines.append(cur)
            cur = []
        cur.append((yc, w))
    if cur:
        lines.append(cur)
    return lines


def _text_ratio(words):
    """词数 / 字符数，段落文字更碎，表格单元格里常是长串。"""
    if not words:
        return 0.0
    return sum(len(w[4]) for w in words) / max(len(words), 1)


def _ink(pg, doc):
    """用「最大图片像素」和「内容流字节」代替 get_drawings()。

    get_drawings() 对矢量图纸要 400ms 以上，一张 17490 条路径的图就是半秒，
    扫一百份图纸光这一步就要一分多钟。而这两项在 10ms 内给出同样的判断力：
      整页位图（扫描件）→ 图片像素极大
      矢量图纸          → 内容流很大、图片很小
    """
    big = 0
    try:
        for im in pg.get_images(full=True):
            big = max(big, (im[2] or 0) * (im[3] or 0))
    except Exception:  # noqa: BLE001
        pass
    size = 0
    try:
        for x in pg.get_contents():
            size += len(doc.xref_stream_raw(x) or b"")
    except Exception:  # noqa: BLE001
        pass
    return big, size


# ============================================================ 单文件探查
def _sample_pages(total, k=6):
    """跨页抽样。

    只读前几页会撞上封面和目录——那两页几乎没字，会把一份数据表
    误判成文档。所以前、中、后各取两页，覆盖面更接近全文。
    """
    if total <= k:
        return list(range(total))
    idx = {0, 1, total // 2, min(total // 2 + 1, total - 1),
           max(total - 3, 0), max(total - 2, 0)}
    return sorted(i for i in idx if 0 <= i < total)


def probe_pdf(path, max_pages=6):
    """打开一份 PDF，复用 PageFeatureCache 取特征，避免后续各表重复读文件。"""
    rec = {"path": path, "name": os.path.basename(path),
           "size": os.path.getsize(path) if os.path.exists(path) else 0,
           "pages": 0, "chars_pg": 0, "words_pg": 0, "draws_pg": 0,
           "table_score": 0.0, "cols": 0, "page_size": None, "error": None}
    try:
        doc = feat_cache.get_doc_features(path)
        pages = doc["pages"]
        rec["pages"] = len(pages)
        idx = _sample_pages(len(pages), max_pages)
        n = max(len(idx), 1)
        chars = words = 0
        big_img = 0
        ink_max = 0
        best_score, best_cols = 0.0, 0
        page_scores = []
        for i in idx:
            pg = pages[i]
            ws = pg["words"]
            chars += pg["chars"]
            words += pg["n_words"]
            big_img = max(big_img, pg["big_img_px"])
            ink_max = max(ink_max, pg["ink_bytes"])
            if rec["page_size"] is None:
                rec["page_size"] = [round(pg["width"]), round(pg["height"])]
            sc, cols = _x_profile(ws)
            page_scores.append((sc, cols))
            if sc > best_score:
                best_score, best_cols = sc, cols
        rec["chars_pg"] = round(chars / max(n, 1))
        rec["words_pg"] = round(words / max(n, 1))
        rec["big_img"] = big_img
        rec["ink_kb"] = round(ink_max / 1024)
        rec["table_score"] = best_score
        rec["cols"] = best_cols
        rec["sampled"] = [i + 1 for i in idx]
        rec["table_ratio"] = round(
            sum(1 for sc, c in page_scores if sc >= 0.35 and c >= 4) / max(len(page_scores), 1), 2)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}"
    return rec


def classify(rec):
    """判定文件类型。返回 (kind, confidence, evidence)。

    顺序很重要：先排除「根本读不出文字」的，再看像不像表格，
    最后才用文件名兜底。
    """
    ev = []
    wpg = rec.get("words_pg") or 0
    cpg = rec.get("chars_pg") or 0
    big = rec.get("big_img") or 0
    ink = rec.get("ink_kb") or 0
    tscore = rec.get("table_score") or 0
    cols = rec.get("cols") or 0

    if rec.get("error"):
        return "error", 0.0, [f"打不开：{rec['error']}"]

    # ① 无文字层：扫描件（整页位图）或矢量图（内容流大、图小）
    if wpg < 3:
        if big >= 1_000_000:
            ev.append(f"无文字层、整页位图 {big // 10000} 万像素")
            return "scan", 0.9, ev
        if ink >= 50:
            ev.append(f"无文字层、矢量内容 {ink} KB")
            return "drawing", 0.9, ev
        ev.append(f"无文字层、内容很少（{ink} KB）")
        return "scan", 0.5, ev

    # ② 有明显表格结构：列位置高度重复，而且不止一两页这样
    ratio = rec.get("table_ratio") or 0
    if tscore >= 0.35 and cols >= 4:
        ev.append(f"{round(ratio * 100)}% 的采样页像表格、约 {cols} 列")
        if ratio >= 0.5:
            ns = name_score(rec["name"], "table")
            if ns > 0:
                ev.append("文件名也像表格")
            return "table", round(min(0.95, 0.55 + tscore * 0.3 + ratio * 0.2), 2), ev
        # 大部分页面不成表：是文档里夹了几张参数表
        ev.append("多数页面不成表，按文档处理")

    # ③ 文字很多但不成表：文档
    if cpg >= 300:
        ev.append(f"{cpg} 字/页、列位置不成规律")
        return "doc", 0.8, ev

    # ④ 文字少、矢量内容多：图纸
    if ink >= 50 and cpg < 200:
        ev.append(f"{cpg} 字/页、矢量内容 {ink} KB")
        return "drawing", 0.7, ev

    # ⑤ 兜底：看名字
    for kind in ("table", "doc", "drawing", "budget"):
        if name_score(rec["name"], kind) >= 0.5:
            ev.append("只能靠文件名判断")
            return kind, 0.4, ev

    ev.append(f"{wpg} 词/页，结构不明显")
    return "doc", 0.3, ev


# ============================================================ 表头探查
def _infer_pattern(values):
    r"""从第一列的取值推断行识别正则。

    「NA1 / NA33 / AN0」这种要能一起匹配，所以连续数字统一放宽成 \d{1,3}——
    只按出现最多的字形生成 ^[A-Z][A-Z]\d$ 会把 NA33 漏掉，
    而漏行的代价比多认几行大得多。
    """
    vals = [v for v in values if v]
    if not vals:
        return "^.$", "空"

    def shape(s):
        out = []
        for ch in s:
            if ch.isdigit():
                out.append("9")
            elif ch.isascii() and ch.isalpha():
                out.append("A" if ch.isupper() else "a")
            elif "\u4e00" <= ch <= "\u9fff":
                out.append("H")
            else:
                out.append(ch)
        return "".join(out)

    def compress(sh):
        out, prev, cnt = [], None, 0
        for ch in sh:
            if ch == prev:
                cnt += 1
            else:
                if prev:
                    out.append((prev, cnt))
                prev, cnt = ch, 1
        out.append((prev, cnt))
        return out

    shapes = Counter(shape(v) for v in vals)
    sh = shapes.most_common(1)[0][0]

    rx = []
    for ch, cnt in compress(sh):
        if ch == "9":
            rx.append(r"\d{1,4}")
        elif ch == "A":
            rx.append(r"[A-Z]{1,3}" if cnt <= 3 else r"[A-Z]+")
        elif ch == "a":
            rx.append(r"[a-z]{1,3}")
        elif ch == "H":
            rx.append(r"[\u4e00-\u9fff]+")
        else:
            rx.append(re.escape(ch))
    return "^" + "".join(rx) + "$", sh


def _digit_ratio(words):
    """含数字的词占多少。表头几乎没数字，数据行满是数字。"""
    if not words:
        return 0.0
    return sum(1 for w in words if any(c.isdigit() for c in w[4])) / len(words)


def _avg_len(words):
    return sum(len(w[4]) for w in words) / max(len(words), 1)


def _cluster_x(xs, gap=6.0):
    """把 x 中心聚成一簇簇，每簇就是一个列的锚点。"""
    if not xs:
        return []
    xs = sorted(xs)
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [sum(g) / len(g) for g in groups]


def _find_data_band(lines, allow_gap=1):
    """找数据区：连续若干行，数字多、词多。

    允许中间跳过一两行——工程表里常插一条跨列的中文备注，
    它自己不成列，但把一页数据切成两半，一刀切会把起点推到中间去。

    返回 (起始行下标, 行数)。表头长什么样不用先知道：
    先把数据认出来，表头就是它上面那一块。
    """
    flags = []
    for line in lines:
        words = [w for _, w in line]
        # 0.25 而不是 0.5：材料表里型号、名称占多数，数字只有数量那几列
        flags.append(len(words) >= 4 and _digit_ratio(words) >= 0.25)

    best = (0, 0)
    i = 0
    n = len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j, gap = i, 0
        while j < n:
            if flags[j]:
                gap = 0
            else:
                gap += 1
                if gap > allow_gap:
                    break
            j += 1
        end = j - gap
        if end - i > best[1]:
            best = (i, end - i)
        i = j
    return best


def probe_table(path, max_pages=4, tol=3.5):
    """探查表格的结构。

    顺序上先认数据、再认表头，比反过来稳得多：

      1. 数据区 —— 连续多行、数字多、词数稳定；
      2. 列锚点 —— 把数据区所有词的 x 中心聚类，得到每一列的真实位置；
      3. 列名   —— 数据区上方那些行里的词，按 x 归到最近的一列，
                  多行表头（列名 + 单位 + 腿号）自然就拼起来了；
      4. 行正则 —— 用数据区第一列的取值推断。

    之所以不先找表头：中文工程表常见两三行表头，还有单位行和腿号子行，
    单行判断必错；而数据行的列位置是稳定且可聚类的。
    """
    out = {"ok": False, "headers": [], "columns": [], "body_top": 0,
           "row_pattern": None, "rows_per_page": 0, "sample": [],
           "header_rows": 0, "note": ""}
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        out["note"] = f"打不开：{type(e).__name__}"
        return out

    cands = []
    for pno in range(min(len(doc), max_pages)):
        pg = doc[pno]
        lines = _rows_of(pg.get_text("words"), tol)
        if len(lines) < 8:
            continue
        start, run = _find_data_band(lines)
        if run >= 5:
            cands.append((run, pno, start, lines, pg.rect))

    if not cands:
        doc.close()
        out["note"] = "没找到成片的数据行，可能不是表格，或表格是矢量的"
        return out

    # 表头一般只在第一页出现，后面的页直接是数据。
    # 所以只要有第一页的候选就用它，哪怕它的数据行不是最多的。
    first = [c for c in cands if c[1] == 0]
    best = first[0] if first else max(cands, key=lambda c: c[0])
    run, pno, start, lines, rect = best
    data = lines[start:start + run]

    # 列锚点：数据区所有词的 x 中心聚类
    xs = []
    for line in data:
        for _, w in line:
            xs.append((w[0] + w[2]) / 2)
    anchors = _cluster_x(xs, gap=6.0)
    if len(anchors) < 4:
        doc.close()
        out["note"] = f"数据行只聚出 {len(anchors)} 列，列结构不清晰"
        return out

    # 列边界 = 相邻锚点中点
    edges = [0.0]
    for i in range(len(anchors) - 1):
        edges.append((anchors[i] + anchors[i + 1]) / 2)
    edges.append(float(rect.width))

    # 表头：从数据区往上找连续的低数字行，一旦遇到数据行就停。
    # 不设这个「遇数据即停」，会把上一页末尾的数据行也吸进来，
    # 列名就变成「杆塔序号 NA32」这种半截货。
    head_rows = []
    misses = 0
    for line in reversed(lines[max(0, start - 10):start]):
        words = [w for _, w in line]
        if len(words) < 2:
            continue
        if _digit_ratio(words) >= 0.4:
            misses += 1
            if misses >= 2:      # 连续碰到两行数据，说明已经越过表头
                break
            continue
        misses = 0
        if _avg_len(words) <= 1.05 and len(words) > 6:
            continue          # 标题被拆成单字，跳过
        head_rows.append(line)
    head_rows.reverse()

    head_words = [w for line in head_rows for _, w in line]
    hdr_rows = len(head_rows)

    labels = ["" for _ in anchors]
    for w in head_words:
        xc = (w[0] + w[2]) / 2
        k = min(range(len(anchors)), key=lambda i: abs(anchors[i] - xc))
        if abs(anchors[k] - xc) <= 60:
            labels[k] = (labels[k] + " " + w[4]).strip() if labels[k] else w[4]

    columns = []
    for i, a in enumerate(anchors):
        columns.append({
            "key": f"c{i + 1}",
            "label": labels[i] or f"第{i + 1}列",
            "x": round(a, 1),
            "x0": round(edges[i], 1),
            "x1": round(edges[i + 1], 1),
        })

    body_top = round(min(w[1] for _, w in data[0]) - 1, 1) if data else 0

    sample, firsts = [], []
    for line in data[:14]:
        cells = {}
        for _, w in sorted(line, key=lambda t: t[1][0]):
            xc = (w[0] + w[2]) / 2
            k = min(range(len(anchors)), key=lambda i: abs(anchors[i] - xc))
            cells[f"c{k + 1}"] = (cells.get(f"c{k + 1}", "") + w[4]).strip()
        if cells:
            sample.append([cells.get(c["key"], "") for c in columns])
            firsts.append(cells.get("c1", ""))

    pattern, shape = _infer_pattern(firsts)
    out.update({
        "ok": True,
        "headers": [{"text": c["label"], "x": c["x"]} for c in columns],
        "columns": columns,
        "body_top": body_top,
        "row_pattern": pattern,
        "pattern_shape": shape,
        "rows_per_page": run,
        "sample": sample,
        "header_page": pno + 1,
        "header_rows": hdr_rows,
        "note": "" if hdr_rows else "数据区上方没找到表头文字，列名需人工补",
    })
    doc.close()
    return out


# ============================================================ 目录扫描
def scan(root, limit=400):
    """遍历目录，返回每份 PDF 的探查记录。"""
    recs = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in sorted(fns):
            if not fn.lower().endswith(".pdf"):
                continue
            p = os.path.join(dp, fn)
            rec = probe_pdf(p)
            rec["rel"] = os.path.relpath(p, root)
            rec["dir"] = os.path.relpath(dp, root) if dp != root else ""
            kind, conf, ev = classify(rec)
            rec["kind"], rec["confidence"], rec["evidence"] = kind, conf, ev
            recs.append(rec)
            if len(recs) >= limit:
                return recs
    return recs


def fingerprint(root, recs):
    """目录指纹：文件数 + 总字节。内容一变就重算，没变就复用上次结果。"""
    h = hashlib.md5()
    h.update(str(len(recs)).encode())
    for r in sorted(recs, key=lambda x: x["rel"]):
        h.update(r["rel"].encode("utf-8", "ignore"))
        h.update(str(r.get("size", 0)).encode())
    return h.hexdigest()[:12]


# ============================================================ 字段匹配
def load_template(path=None):
    """读模板字段表。没有就返回内置的一份，保证总能跑。"""
    import csv
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    p = path or os.path.join(pkg_dir, "defaults", "data", "template_fields.csv")
    out = []
    try:
        with open(p, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = (row.get("target") or "").strip()
                if not t:
                    continue
                syn = [x.strip() for x in (row.get("synonyms") or "").split("|") if x.strip()]
                out.append({"target": t, "synonyms": syn,
                            "type": (row.get("type") or "text").strip()})
    except FileNotFoundError:
        out = [{"target": "杆塔号", "synonyms": ["塔号", "杆塔序号"], "type": "text"},
               {"target": "杆塔型号", "synonyms": ["塔型", "杆塔型式"], "type": "text"},
               {"target": "呼称高", "synonyms": ["呼高"], "type": "number"}]
    return out


def _norm(s):
    """归一化：去括号内容、去单位、去空白和标点，全角转半角。

    「呼称高（m）」和「呼高(m)」要能对上，靠的就是这一步。
    """
    s = (s or "").replace("（", "(").replace("）", ")")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[\s\-_·.,，、:：;；'\"“”]+", "", s)
    return s.lower()


def _score(col_label, cand):
    """一列名与一个候选词（字段名或同义词）的匹配度。"""
    a, b = _norm(col_label), _norm(cand)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.9 if min(len(a), len(b)) >= 3 else 0.7
    if b in a or a in b:
        return 0.8 if min(len(a), len(b)) >= 3 else 0.5
    import difflib
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return round(r * 0.75, 2) if r >= 0.75 else 0.0


def match_fields(columns, template):
    """把探查到的列与目标字段配对，一对一，宁缺勿错。

    先给所有（列, 字段）打分，再按分数从高到低分配，
    这样不会出现「杆塔号」和「杆塔型号」抢同一列的情况。
    """
    pairs = []
    for ci, col in enumerate(columns):
        label = col.get("label") or ""
        if not label or label.startswith("第"):
            continue
        for t in template:
            best = _score(label, t["target"])
            method = "同名" if best >= 0.99 else "同义词"
            for syn in t["synonyms"]:
                s = _score(label, syn)
                if s > best:
                    best, method = s, "同义词"
            if best >= 0.75:
                pairs.append((best, ci, t["target"], method))

    pairs.sort(key=lambda x: -x[0])
    used_col, used_tgt, out = set(), set(), []
    for sc, ci, tgt, method in pairs:
        if ci in used_col or tgt in used_tgt:
            continue
        used_col.add(ci)
        used_tgt.add(tgt)
        out.append({"target": tgt, "column": columns[ci]["key"],
                    "header": columns[ci].get("label", ""),
                    "score": round(sc, 2), "method": method})

    out.sort(key=lambda x: x["score"], reverse=True)

    missing = [t["target"] for t in template if t["target"] not in used_tgt]
    return out, missing


# ============================================================ 完整探查
# 派生字段：图纸上没有这一列，但能从别的列算出来。
# 这是领域知识，放在代码里，而不是让模型去猜。
DERIVED = {
    "线路段": {"from": "杆塔号", "transform": "segment",
               "note": "由杆塔号前缀切段，如 NA1 → A 段"},
    "转向前缀": {"from": "线路转角", "transform": "angle_dir",
                 "note": "从「左:9°7′」里取出左右方向"},
    "是否高低腿": {"from": "接腿高度A", "transform": "uneven_legs",
                   "note": "四条腿接腿高度不一致即为高低腿"},
}


def build(root, template_path=None, progress=None, exclude_dirs=None):
    """完整探查：扫描 → 分类 → 探表格 → 匹配字段 → 给出配置草稿。

    exclude_dirs 用来排除自己的输出目录，避免把脱敏副本当成新图纸。
    """
    tpl = load_template(template_path)
    exclude_dirs = exclude_dirs or []
    ex_abs = {os.path.abspath(d) for d in exclude_dirs}

    recs = []
    seen_copy = {}
    dup = 0
    for dp, dns, fns in os.walk(root):
        # 自己生成的输出目录不参与探查，否则会把脱敏副本当成新图纸
        dns[:] = [d for d in dns
                  if not d.startswith(".")
                  and os.path.abspath(os.path.join(dp, d)) not in ex_abs]
        for fn in sorted(fns):
            if not fn.lower().endswith(".pdf"):
                continue
            p = os.path.join(dp, fn)
            rec = probe_pdf(p)
            rec["rel"] = os.path.relpath(p, root)
            rec["dir"] = os.path.relpath(dp, root) if dp != root else ""

            # 同一份图纸常被复制到「脱敏输出」之类的目录里。
            # 以（文件名, 字节数, 页数）为指纹，只保留路径最短的那份——
            # 副本和原件内容一致，重复计入会让分类统计和主数据源判断都偏。
            key = (fn.lower(), rec.get("size"), rec.get("pages"))
            prev = seen_copy.get(key)
            if prev is not None:
                dup += 1
                if len(rec["rel"]) >= len(prev["rel"]):
                    continue
                recs.remove(next(r for r in recs if r is prev))
            seen_copy[key] = rec

            kind, conf, ev = classify(rec)
            rec["kind"], rec["confidence"], rec["evidence"] = kind, conf, ev
            recs.append(rec)
            if progress:
                progress(len(recs), rec)

    # 表格类才探查结构
    cands = []
    for rec in recs:
        if rec["kind"] != "table":
            continue
        pr = probe_table(rec["path"])
        matches, missing = ([], []) if not pr["ok"] else match_fields(pr["columns"], tpl)
        rec["probe"] = {k: pr.get(k) for k in
                        ("ok", "columns", "row_pattern", "body_top",
                         "rows_per_page", "sample", "header_rows",
                         "header_page", "note")}
        rec["matches"], rec["missing"] = matches, missing
        rec["match_score"] = len(matches)
        if pr["ok"]:
            cands.append(rec)

    # 主数据源：匹配到字段最多的那份
    primary = max(cands, key=lambda r: r["match_score"], default=None)

    counts = Counter(r["kind"] for r in recs)
    return {
        "root": os.path.abspath(root),
        "fingerprint": fingerprint(root, recs),
        "scanned": len(recs),
        "duplicates": dup,
        "counts": dict(counts),
        "files": [{k: r.get(k) for k in
                   ("rel", "name", "kind", "confidence", "evidence", "pages",
                    "words_pg", "table_score", "table_ratio", "cols", "match_score")}
                  for r in recs],
        "candidates": [{"rel": r["rel"], "name": r["name"],
                        "probe": r["probe"], "matches": r["matches"],
                        "missing": r["missing"], "match_score": r["match_score"]}
                       for r in cands],
        "primary": primary["rel"] if primary else None,
        "primary_fields": [m["target"] for m in primary["matches"]] if primary else [],
        "template": [t["target"] for t in tpl],
    }


def to_configs(profile):
    """把探查结果转成三份配置（sources / layout / mapping）。

    生成的键名是 c1..cN，与探查时的列一一对应；
    下游的 parse_table 只认这些 key，所以不需要再翻译一次。
    """
    prim = next((c for c in profile["candidates"] if c["rel"] == profile["primary"]), None)
    if not prim:
        raise ValueError("没有可用的主数据源")

    src_id = "primary"
    sources = []
    for i, c in enumerate(profile["candidates"]):
        sources.append({
            "id": src_id if c["rel"] == profile["primary"] else f"aux{i}",
            "name": c["name"],
            "path": c["rel"].replace("\\", "/"),
            "role": "主数据源" if c["rel"] == profile["primary"] else "参考/校验",
            "expect_carrier": "T2",
        })

    cols = prim["probe"]["columns"] or []
    layout = {
        "layout_id": "auto_" + (profile.get("fingerprint") or "0"),
        "source": src_id,
        "body_top": prim["probe"].get("body_top") or 0,
        "row_pattern": prim["probe"].get("row_pattern") or "^\\S+$",
        "row_key": cols[0]["key"] if cols else "c1",
        "row_tolerance": 3.5,
        "columns": [{"key": c["key"], "label": c.get("label", ""),
                     "x0": c["x0"], "x1": c["x1"]} for c in cols],
    }

    mapping_cols = []
    matched = {m["column"]: m for m in prim["matches"]}
    by_target = {m["target"]: m for m in prim["matches"]}

    for m in prim["matches"]:
        mapping_cols.append({"target": m["target"], "from": m["column"],
                             "note": f"自动匹配：{m['header']}（{m['method']}）"})

    # 派生字段：源列命中时补上
    for tgt, d in DERIVED.items():
        if tgt in by_target:
            continue
        if d["from"] in by_target:
            mapping_cols.append({
                "target": tgt, "from": by_target[d["from"]]["column"],
                "transform": d["transform"], "note": d["note"]})

    return {
        "sources": {"project": os.path.basename(profile["root"]) or "未命名工程",
                    "root": profile["root"].replace("\\", "/"),
                    "sources": sources},
        "layout": layout,
        "mapping": {"template": "自动识别", "layout": layout["layout_id"],
                    "columns": mapping_cols},
    }


def write_configs(profile, out_dir):
    """把生成的配置落到磁盘，返回写出的文件路径。"""
    import yaml
    os.makedirs(out_dir, exist_ok=True)
    cfg = to_configs(profile)

    paths = {}
    for name, data in (("sources.yaml", cfg["sources"]),
                       ("layout.auto.yaml", cfg["layout"]),
                       ("mapping.auto.yaml", cfg["mapping"])):
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        paths[name] = p

    import json
    pf = os.path.join(out_dir, "profile.json")
    with open(pf, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=1)
    paths["profile.json"] = pf
    return paths


def detect_aux_sources(files, root=""):
    """从目录探查文件列表中自动识别多源辅助施工图分册角色。

    返回 dict：rho / grounding / crossing / materials / manual / survey / foundation
    """
    aux = {}
    foundation_list = []

    def _first_text(path, max_p=3):
        try:
            doc = feat_cache.get_doc_features(path)
            return "\n".join((p.get("text") or "")[:1200] for p in doc["pages"][:max_p])
        except Exception:  # noqa: BLE001
            return ""

    for f in files or []:
        name = str(f.get("name") or "")
        rel = str(f.get("rel") or "").replace("\\", "/")
        path = f.get("path") or (os.path.join(root, rel) if root and rel else "")
        if not path or not os.path.isfile(path):
            continue
        item = {"name": name, "rel": rel, "path": path, "pages": f.get("pages") or 0}
        low = name.lower()

        if "电阻率" in name and "rho" not in aux:
            aux["rho"] = item
        elif ("接地" in name or "防雷" in name) and "grounding" not in aux:
            aux["grounding"] = item
        elif ("敏感因素" in name or "交叉跨越" in name) and "crossing" not in aux:
            aux["crossing"] = item
        elif ("设备材料清册" in name or "材料清册" in name or "材料汇总" in name) and "materials" not in aux:
            aux["materials"] = item
        elif ("勘察" in name or "岩土" in name) and "电阻率" not in name and "survey" not in aux:
            aux["survey"] = item
        elif ("基础" in name and ("图" in name or "详图" in name)) and "明细表" not in name:
            foundation_list.append(item)
        elif ("说明书" in name or "总说明" in name or "pilot_text" in low) and "manual" not in aux:
            aux["manual"] = item

    # 二次文本特征兜底（不依赖文件名）
    for f in files or []:
        rel = str(f.get("rel") or "").replace("\\", "/")
        path = f.get("path") or (os.path.join(root, rel) if root and rel else "")
        if not path or not os.path.isfile(path):
            continue
        item = {"name": f.get("name") or "", "rel": rel, "path": path, "pages": f.get("pages") or 0}
        if all(k in aux for k in ("rho", "grounding", "crossing", "materials", "manual")):
            break
        head = _first_text(path, max_p=3)
        if not head:
            continue
        if "rho" not in aux and ("土壤电阻率" in head and ("推荐" in head or "Ω" in head)):
            aux["rho"] = item
        if "grounding" not in aux and ("防雷接地" in head or "接地装置" in head) and re.search(r"[12][DS]-[ZN]\d{2}", head):
            aux["grounding"] = item
        if "crossing" not in aux and "重要交叉跨越" in head:
            aux["crossing"] = item
        if "materials" not in aux and ("设备材料清册" in head or "线路设备材料" in head):
            aux["materials"] = item
        if "manual" not in aux and ("施工图设计说明书" in head or "设计总说明" in head):
            aux["manual"] = item
        if "survey" not in aux and ("岩土工程勘察报告" in head or "土质分层" in head):
            aux["survey"] = item

    if "survey" not in aux and root and os.path.isdir(root):
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith(".")]
            for fn in sorted(fns):
                if fn.startswith("~$") or not fn.lower().endswith(".docx"):
                    continue
                if any(k in fn for k in ("勘察", "岩土", "地质")) and "电阻率" not in fn:
                    p = os.path.join(dp, fn)
                    rel = os.path.relpath(p, root).replace("\\", "/")
                    aux["survey"] = {"name": fn, "rel": rel, "path": p, "pages": 0, "format": "docx"}
                    break
            if "survey" in aux:
                break

    if foundation_list:
        aux["foundation"] = foundation_list
    return aux
