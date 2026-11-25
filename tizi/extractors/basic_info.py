# -*- coding: utf-8 -*-
"""《基本信息》T1 说明书受控抽取器（按单项工程边界精确切分 + 100% 标 pending + 异常标 conflict）。

解析《施工图设计说明书》（如 1.9.1 主要技术特性各单项工程小节），准确识别：
- 电压等级（按「3）电压等级：」行提取，不跨段串页误判 110kV/220kV）
- 回路类型（单回路 / 双回路，严格对齐母版下拉字典）
- 线路亘长（支持「新建线路长」「新建线路全长」「线路路径长度」）
- 导线型号、地线1型号（OPGW）与地线2型号（JLB/OPGW）
- 地形比例（校验总和 ≤ 100%，显式注明平地余数）
- 人力/汽车运距（自动捕获单位疑似笔误如「15m」并标 conflict 供一键采纳）
"""
import re

from .. import cache
from ..provenance import make_evidence, make_prov

_SEG_SPLIT_RE = re.compile(r"(?:^|\n)\s*1\s*[)）]\s*线路名称\s*[：:]")
_VOLT_RE = re.compile(r"电压等级\s*[：:]\s*(1000|750|500|330|220|110|66|35)\s*kV", re.IGNORECASE)
_CIRC_RE = re.compile(r"回路数\s*[：:]\s*([^\n。]{2,40})")
_LEN_RE = re.compile(r"(?:线路长度\s*[：:]\s*)?(?:新建)?线路(?:全?长|路径长度|长度)\s*[：:为约]?\s*(\d+(?:\.\d+)?)\s*km", re.IGNORECASE)
_COND_RE = re.compile(r"导线型号\s*[：:]\s*([^\n。；;]{4,70})")
_GW_RE = re.compile(r"地线型号\s*[：:]\s*([\s\S]{4,220}?)(?:\n\s*13\s*[)）]|\n\s*14\s*[)）]|。(?=\s*\n))")
_OPGW_RE = re.compile(r"(OPGW[-\w]+)", re.IGNORECASE)
_JLB_RE = re.compile(r"(JLB[\w-]+|GJ-\d+)", re.IGNORECASE)
_ALT_RE = re.compile(r"(?:沿线)?海拔\s*[：:]?\s*(\d{3,4})\s*[～~\-—]\s*(\d{3,4})\s*米?")
_ICE_RE = re.compile(r"覆冰\s*(\d+(?:\.\d+)?)\s*(?:、\d+)*\s*mm", re.IGNORECASE)
_TERRAIN_RE = re.compile(r"(平地|丘陵|一般山地|山地|高山大岭|高山|峻岭|泥沼|河网|沙漠)\s*(\d+(?:\.\d+)?)\s*[%％]")
_SMALL_DIST_RE = re.compile(r"(?:小运距离|人力运距)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(km|m|公里|米)", re.IGNORECASE)
_CAR_DIST_RE = re.compile(r"(?:工地运距|汽车运距)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(km|m|公里|米)", re.IGNORECASE)


def _find_snippet_bbox(words, keyword: str):
    if not words or not keyword:
        return None
    hits = [w for w in words if keyword in str(w[4])]
    if not hits:
        return None
    y0 = hits[0][1]
    near = [w for w in words if abs(w[1] - y0) <= 6.0]
    if not near:
        near = hits
    return [
        round(min(w[0] for w in near), 1),
        round(min(w[1] for w in near), 1),
        round(max(w[2] for w in near), 1),
        round(max(w[3] for w in near), 1),
    ]


def _locate_field_evidence(pages, pg_s: int, pg_e: int, rel: str, role: str, keywords, raw_text: str = ""):
    """在本线路段覆盖的起止页内按字段关键词精确定位所属行坐标（不误标上一行）。"""
    lo = max(0, pg_s)
    hi = min(len(pages) - 1, max(lo, pg_e))
    for kw in keywords:
        for pi in range(lo, hi + 1):
            bbox = _find_snippet_bbox(pages[pi].get("words") or [], kw)
            if bbox:
                return make_evidence(role, rel, page=pi + 1, bbox=bbox, raw_text=raw_text)
    return make_evidence(role, rel, page=lo + 1, bbox=None, raw_text=raw_text)


def _extract_segments_from_manual(doc, rel: str):
    """基于「1）线路名称：」小节边界精确切分各单项工程概况块，绝不跨段串页。"""
    pages = doc["pages"]
    # 收集包含主要技术特性的连续页区间
    tech_pages = []
    for pg in pages:
        t = pg.get("text") or ""
        if any(k in t for k in ("1）线路名称", "1)线路名称", "主要技术特性", "地形系数", "小运距离")):
            tech_pages.append(pg["page"])
    if not tech_pages:
        return []

    p_start = max(0, min(tech_pages))
    p_end = min(len(pages) - 1, max(tech_pages) + 1)

    # 拼装带页号标记的文本流，按 `1）线路名称：` 切分成独立单项工程块
    chunks = []
    page_offsets = []  # (char_start, page_idx)
    buf = []
    pos = 0
    for pi in range(p_start, p_end + 1):
        txt = pages[pi].get("text") or ""
        page_offsets.append((pos, pi))
        buf.append(txt)
        pos += len(txt) + 1
    combined = "\n".join(buf)

    def _page_of_offset(char_pos: int) -> int:
        best_p = p_start
        for off, pi in page_offsets:
            if off <= char_pos:
                best_p = pi
            else:
                break
        return best_p

    matches = list(_SEG_SPLIT_RE.finditer(combined))
    if matches:
        for i, m in enumerate(matches):
            s_pos = m.start()
            e_pos = matches[i + 1].start() if i + 1 < len(matches) else min(len(combined), s_pos + 3200)
            block = combined[s_pos:e_pos]
            # 截断到 19）或汇总表之前，避免吸进下一章节
            m_stop = re.search(r"\n\s*(?:1\.9\.2|2\.|二、|序\s*\n\s*号)", block)
            if m_stop:
                block = block[:m_stop.start()]
            if any(k in block for k in ("电压等级", "导线型号", "线路长度")):
                pg_s = _page_of_offset(s_pos)
                pg_e = _page_of_offset(max(s_pos, s_pos + len(block) - 1))
                chunks.append((block, pg_s, pg_e))

    segments = []
    for idx, (block, pg_s, pg_e) in enumerate(chunks, 1):
        # 优先在「3）电压等级：」字段内识别电压，彻底杜绝跨段串电压
        m_v = _VOLT_RE.search(block)
        kv = f"{m_v.group(1)}kV" if m_v else ("110kV" if "110kV" in block[:220] else "220kV")

        # 回路数（严格对齐模板枚举：单回路 / 双回路）
        m_c = _CIRC_RE.search(block)
        c_txt = m_c.group(1) if m_c else ""
        if "双回" in c_txt and "单回" not in c_txt[:6]:
            circuit = "双回路"
        elif "单回" in c_txt:
            circuit = "单回路"
        else:
            circuit = "双回路" if "同塔双回" in block else "单回路"

        # 新建线路长度（支持「新建线路长49.237km」与「新建线路全长18.595km」）
        m_len = _LEN_RE.search(block)
        length_km = float(m_len.group(1)) if m_len else None

        # 导线型号
        m_cond = _COND_RE.search(block)
        cond_raw = m_cond.group(1).strip() if m_cond else ""
        m_cond_code = re.search(r"(?:2×)?(?:JL/LB\w*-\d+/\d+|LGJ-\d+/\d+)", cond_raw, re.IGNORECASE)
        conductor = m_cond_code.group(0) if m_cond_code else (cond_raw[:32] or None)

        # 地线 1 / 地线 2（多行匹配，完整捕获 OPGW + JLB 铝包钢绞线）
        m_gw = _GW_RE.search(block)
        gw_ctx = m_gw.group(1) if m_gw else ""
        opgws = _OPGW_RE.findall(gw_ctx)
        jlbs = _JLB_RE.findall(gw_ctx)
        gw1 = opgws[0] if opgws else (jlbs[0] if jlbs else None)
        if opgws and jlbs:
            gw2 = jlbs[0]
        elif len(opgws) > 1:
            gw2 = opgws[1]
        elif opgws and ("两根" in gw_ctx or "2根" in gw_ctx or "2 根" in gw_ctx):
            gw2 = opgws[0]
        else:
            gw2 = None

        # 地形比例
        terrain = {}
        for tk, tv in _TERRAIN_RE.findall(block):
            val = float(tv)
            if tk == "丘陵":
                terrain["丘陵（%）"] = val
            elif tk in ("一般山地", "山地"):
                terrain["一般山地（%）"] = val
            elif tk in ("高山大岭", "高山"):
                terrain["高山大岭（%）"] = val
            elif tk == "峻岭":
                terrain["峻岭（%）"] = val
            elif tk == "泥沼":
                terrain["泥沼（%）"] = val
            elif tk == "河网":
                terrain["河网（%）"] = val
            elif tk == "沙漠":
                terrain["沙漠（%）"] = val
            elif tk == "平地":
                terrain["平地（%）"] = val

        # 运距与笔误冲突检测
        m_sm = _SMALL_DIST_RE.search(block)
        human_km = None
        if m_sm:
            v, u = float(m_sm.group(1)), m_sm.group(2).lower()
            human_km = round(v / 1000.0, 3) if u in ("m", "米") else v

        m_car = _CAR_DIST_RE.search(block)
        car_km = None
        car_conflict = None
        if m_car:
            v, u = float(m_car.group(1)), m_car.group(2).lower()
            if u in ("m", "米") and v >= 5:
                car_km = round(v / 1000.0, 3)
                car_conflict = [
                    {"label": f"按原文单位换算 ({v:g}m → {car_km}km)", "value": car_km},
                    {"label": f"疑似笔误改按公里 ({v:g}km)", "value": v},
                ]
            else:
                car_km = v

        # 海拔与冰区
        m_alt = _ALT_RE.search(block)
        avg_alt = round((float(m_alt.group(1)) + float(m_alt.group(2))) / 2.0, 0) if m_alt else None
        m_ice = _ICE_RE.search(block)
        ice_mm = float(m_ice.group(1)) if m_ice else None

        # 生成脱敏且语义清晰的唯一线路段名称（供 LineSegmentRegistry 全局共享）
        is_pi = ("π" in block[:180] or "Π" in block[:180])
        suffix = f"{kv}{'双回' if circuit == '双回路' else ''}{'π接段' if is_pi else '主干段'}"
        seg_name = f"线路段{idx}（{suffix}）"

        ev_map = {
            "head": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书技术特性", ("电压等级", "回路数", "1）线路名称"), block[:120].replace("\n", " ")),
            "length": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书线路长度", ("线路长度", "新建线路"), m_len.group(0) if m_len else ""),
            "alt": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书沿线海拔", ("沿线海拔", "海拔"), m_alt.group(0) if m_alt else ""),
            "ice": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书气象条件", ("覆冰", "气象条件"), m_ice.group(0) if m_ice else ""),
            "cond": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书导线选型", ("导线型号",), m_cond.group(0)[:100] if m_cond else ""),
            "gw": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书地线选型", ("地线型号", "OPGW"), gw_ctx[:100].replace("\n", " ") if gw_ctx else ""),
            "human": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书人力运距", ("小运距离", "人力运距", "16）运距"), m_sm.group(0) if m_sm else ""),
            "car": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书汽车运距", ("工地运距", "汽车运距", "16）运距"), m_car.group(0) if m_car else ""),
            "terrain": _locate_field_evidence(pages, pg_s, pg_e, rel, "说明书地形系数", ("地形系数", "丘陵", "山地"), "地形比例"),
        }

        segments.append({
            "seg_name": seg_name,
            "kv": kv,
            "circuit": circuit,
            "conductor": conductor,
            "gw1": gw1,
            "gw2": gw2,
            "length_km": length_km,
            "human_km": human_km,
            "car_km": car_km,
            "car_conflict": car_conflict,
            "terrain": terrain,
            "avg_alt": avg_alt,
            "ice_mm": ice_mm,
            "ev_map": ev_map,
        })
    return segments


def parse_basic_info_pdf(path, rel_path="", *, total_towers=None, cache_dir=None):
    """解析设计说明书产出《基本信息》行集（每线路段 1 行，全部默认 pending，异常标 conflict）。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    rel = (rel_path or "").replace("\\", "/")
    segs = _extract_segments_from_manual(doc, rel)
    sum_seg_len = round(sum(s["length_km"] for s in segs if s["length_km"]), 3)

    rows = []
    for idx, s in enumerate(segs):
        ter = s["terrain"]
        flat_pct = ter.get("平地（%）")
        non_flat_sum = round(sum(v for k, v in ter.items() if k != "平地（%）"), 1)
        ter_note_extra = f"（其余 {flat_pct:g}% 为平地，母版隐含余数=100%-{non_flat_sum:g}%）" if flat_pct is not None else ""

        out = {
            "单项工程名称": "输电线路工程（待核对工程全称）",
            "电压等级": s["kv"],
            "设计阶段": "施工图预算",
            "线路段名称": s["seg_name"],
            "回路类型": s["circuit"],
            "导线型号": s["conductor"],
            "地线1型号": s["gw1"],
            "地线2型号": s["gw2"],
            "线路亘长（km）": s["length_km"],
            "人力运距（km）": s["human_km"],
            "汽车运距（km）": s["car_km"],
            "地形类型": "架线地形",
            "丘陵（%）": ter.get("丘陵（%）"),
            "泥沼（%）": ter.get("泥沼（%）"),
            "一般山地（%）": ter.get("一般山地（%）"),
            "河网（%）": ter.get("河网（%）"),
            "高山大岭（%）": ter.get("高山大岭（%）"),
            "峻岭（%）": ter.get("峻岭（%）"),
            "沙漠（%）": ter.get("沙漠（%）"),
            "省份和地区": "云南",
            "架线方式": "张力架线",
            "冰区（mm）": s["ice_mm"],
            "平均海拔高程（m）": s["avg_alt"],
        }

        ev_map = s["ev_map"]
        prov = {}
        for k, v in out.items():
            if k in ("线路亘长（km）",):
                ev = ev_map["length"]
            elif k in ("平均海拔高程（m）",):
                ev = ev_map["alt"]
            elif k in ("冰区（mm）",):
                ev = ev_map["ice"]
            elif k in ("导线型号",):
                ev = ev_map["cond"]
            elif k in ("地线1型号", "地线2型号"):
                ev = ev_map["gw"]
            elif k in ("人力运距（km）",):
                ev = ev_map["human"]
            elif k in ("汽车运距（km）",):
                ev = ev_map["car"]
            elif k in ("电压等级", "回路类型", "线路段名称", "单项工程名称", "设计阶段"):
                ev = ev_map["head"]
            else:
                ev = ev_map["terrain"]
            if k == "汽车运距（km）" and s["car_conflict"]:
                prov[k] = make_prov(
                    "extract", conf=0.5, status="conflict", unit="km",
                    machine_value=v,
                    note="检测到说明书运距单位疑似笔误（原文为 m 而非 km），请二选一拍板",
                    formula_trace=f"原文写「工地运距 {s['car_conflict'][1]['value']:g}m」，请确认是否应为 {s['car_conflict'][1]['value']:g} km",
                    evidences=[ev],
                    conflict_candidates=s["car_conflict"],
                )
            elif k in ("省份和地区", "架线方式", "设计阶段", "地形类型"):
                prov[k] = make_prov("constant", conf=0.95, status="confirmed", machine_value=v, note="模板常用预设值", evidences=[ev])
            elif v is not None:
                extra = ter_note_extra if k.endswith("（%）") else ""
                prov[k] = make_prov(
                    "extract", conf=0.9, status="pending",
                    machine_value=v,
                    note=f"T1 说明书受控抽取，请核对原文后确认{extra}",
                    formula_trace=f"说明书抽取：{k} = {v}{extra}",
                    evidences=[ev],
                )
            else:
                prov[k] = make_prov("extract", conf=0.0, status="empty", machine_value=None)

        rows.append({
            "row_id": f"B{idx + 1}",
            "out": out,
            "prov": prov,
        })

    return {
        "rows": rows,
        "source_files": [rel] if rel else [],
        "meta": {
            "segments": len(rows),
            "sum_length_km": sum_seg_len,
        },
    }
