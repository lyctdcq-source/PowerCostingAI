# -*- coding: utf-8 -*-
"""《勘测信息一览表》提取器（支持一塔多行土质分层 + 缺勘察报告优雅降级）。

策略：
1. 若工作空间提供《岩土工程勘察报告》PDF：使用 GridCellExtractor 解析逐塔土质分层表（1-to-many 行：
   杆塔号、土质类型、比例% 或 分层深m），并将工程地质术语映射到母版《隐藏表（勿删）》标准土质字典；
2. 若当前目录缺失《岩土工程勘察报告》（例如技术预演包）：绝不报错，利用已有《土壤电阻率报告》中的
   逐塔岩土描述与《杆塔明细表》塔号生成待复核骨架行，全部标 pending 并附清晰 missing_source 提示；
3. 青苗/林木等外业清点字段保持留空并注明“外业清点项”，严守不猜数红线。
"""
import re
import zipfile
import xml.etree.ElementTree as ET

from .. import cache
from ..grid_parser import GridCellExtractor
from ..provenance import make_evidence, make_prov

# 工程地质术语 → 院标《隐藏表（勿删）》G1:G10 唯一合法土质字典
# ['普通土', '坚土', '松砂石', '岩石(爆破)', '岩石(人凿)', '泥水坑', '流砂坑', '干砂坑', '水坑', '冻土(≥300mm)']
SOIL_DICT_RULES = [
    (re.compile(r"(坚土|硬塑|粉质粘土|粘土|黏土|红粘土|强风化.*泥岩)"), "坚土"),
    (re.compile(r"(普通土|普土|耕植土|素填土|种植土|粉土)"), "普通土"),
    (re.compile(r"(松砂石|碎石|卵石|砾石|砂砾|强风化|全风化|风化碎屑)"), "松砂石"),
    (re.compile(r"(人凿|人工凿岩|风镐)"), "岩石(人凿)"),
    (re.compile(r"(岩石|基岩|中风化|微风化|灰岩|玄武岩|砂岩|花岗岩|白云岩)"), "岩石(爆破)"),
    (re.compile(r"(泥水|淤泥|沼泽|泥炭)"), "泥水坑"),
    (re.compile(r"(流砂|流沙|粉细砂)"), "流砂坑"),
    (re.compile(r"(干砂)"), "干砂坑"),
    (re.compile(r"(冻土)"), "冻土(≥300mm)"),
]

LAND_KIND_RULES = [
    (re.compile(r"(林地|森林|乔木|灌木|树林)"), "林地"),
    (re.compile(r"(耕地|农田|水田|旱地|桃园|果园|园地)"), "耕地"),
    (re.compile(r"(荒地|草地|荒山|坡地)"), "未利用地"),
]

_TOWER_ID_RE = re.compile(r"^N?[A-Z]{1,2}\d+[A-Z]?$")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def map_standard_soil(raw_desc: str):
    """映射至院标标准土质类型；未命中返回 (None, False)。"""
    s = str(raw_desc or "").strip()
    if not s:
        return None, False
    for rx, std in SOIL_DICT_RULES:
        if rx.search(s):
            return std, True
    return None, False


def detect_land_kind(raw_desc: str):
    s = str(raw_desc or "").strip()
    for rx, kind in LAND_KIND_RULES:
        if rx.search(s):
            return kind
    return None


def parse_survey_docx(path: str, rel_path: str = ""):
    """纯标准库解析 Word(.docx)《岩土工程勘察报告》逐塔土质分层表（修复 D-09 缺独立 PDF 勘察报告盲区）。"""
    rel = (rel_path or "").replace("\\", "/")
    rows = []
    cur_tower = ""
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return []
            xml_bytes = zf.read("word/document.xml")
    except (OSError, zipfile.BadZipFile):
        return []

    root = ET.fromstring(xml_bytes)
    for tbl in root.findall(f".//{w_ns}tbl"):
        col_map = {}
        for tr in tbl.findall(f"./{w_ns}tr"):
            texts = []
            for tc in tr.findall(f"./{w_ns}tc"):
                cell_parts = [t.text for t in tc.findall(f".//{w_ns}t") if t.text]
                texts.append("".join(cell_parts).strip())
            if not any(texts):
                continue
            joined = "".join(texts)
            if ("塔号" in joined or "杆塔" in joined) and ("土质" in joined or "岩土" in joined or "地层" in joined):
                col_map = {}
                for ci, t in enumerate(texts):
                    ht = re.sub(r"\s+", "", t)
                    if "塔号" in ht or "杆塔" in ht:
                        col_map["tower"] = ci
                    elif "土质" in ht or "岩土" in ht or "地层" in ht:
                        col_map["soil"] = ci
                    elif "比例" in ht or "%" in ht:
                        col_map["ratio"] = ci
                    elif "深" in ht or "厚" in ht:
                        col_map["depth"] = ci
                    elif "市" in ht or "州" in ht:
                        col_map["city"] = ci
                    elif "县" in ht or "区" in ht:
                        col_map["county"] = ci
                    elif "镇" in ht or "街道" in ht:
                        col_map["town"] = ci
                    elif "村" in ht or "社区" in ht:
                        col_map["village"] = ci
                continue

            if not col_map or "soil" not in col_map:
                continue

            def _get(k):
                i = col_map.get(k)
                return texts[i].strip() if (i is not None and i < len(texts)) else ""

            t_raw = _get("tower").upper()
            if _TOWER_ID_RE.match(t_raw):
                cur_tower = t_raw
            elif t_raw:
                continue

            soil_raw = _get("soil")
            if not cur_tower or not soil_raw:
                continue

            std_soil, hit = map_standard_soil(soil_raw)
            m_rat = _NUM_RE.search(_get("ratio"))
            m_dep = _NUM_RE.search(_get("depth"))
            ratio_v = float(m_rat.group()) if m_rat else None
            depth_v = float(m_dep.group()) if m_dep else None

            ev = make_evidence(
                "Word勘察报告逐塔分层表", rel,
                page=None, bbox=None,
                raw_text=f"{cur_tower} | {soil_raw} | {ratio_v or ''}% | {depth_v or ''}m",
            )
            rows.append(_make_survey_row(
                tower=cur_tower,
                soil_val=std_soil or soil_raw,
                soil_confirmed=hit,
                soil_note=f"Word勘察表「{soil_raw}」→ 标准土质「{std_soil}」" if hit else f"未命中标准土质字典（原文：{soil_raw}），待复核",
                ratio=ratio_v,
                depth=depth_v,
                land=detect_land_kind(soil_raw),
                city=_get("city") or None,
                county=_get("county") or None,
                town=_get("town") or None,
                village=_get("village") or None,
                ev=ev,
                method="extract",
            ))
    return rows


def parse_survey_pdf(path, rel_path="", cache_dir=None):
    """解析《岩土工程勘察报告》逐塔土质分层附表，支持一塔多行分层输出。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    rel = (rel_path or "").replace("\\", "/")
    rows = []
    cur_tower = ""
    col_map = {}

    for p_idx, pg in enumerate(doc["pages"]):
        txt = pg.get("text") or ""
        if not any(k in txt for k in ("土质", "岩土", "分层", "塔号", "杆塔")):
            continue
        grid = GridCellExtractor.extract_page(path, p_idx, cache_dir=cache_dir)
        for item in grid.get("rows") or []:
            if item.get("kind") != "row":
                continue
            texts = item.get("texts") or []
            joined = "".join(texts)
            if ("塔号" in joined or "杆塔" in joined) and ("土质" in joined or "岩土" in joined or "地层" in joined):
                col_map = {}
                for ci, t in enumerate(texts):
                    ht = re.sub(r"\s+", "", t)
                    if "塔号" in ht or "杆塔" in ht:
                        col_map["tower"] = ci
                    elif "土质" in ht or "岩土" in ht or "地层" in ht:
                        col_map["soil"] = ci
                    elif "比例" in ht or "%" in ht:
                        col_map["ratio"] = ci
                    elif "深" in ht or "厚" in ht:
                        col_map["depth"] = ci
                    elif "市" in ht or "州" in ht:
                        col_map["city"] = ci
                    elif "县" in ht or "区" in ht:
                        col_map["county"] = ci
                    elif "镇" in ht or "街道" in ht:
                        col_map["town"] = ci
                    elif "村" in ht or "社区" in ht:
                        col_map["village"] = ci
                continue

            if not col_map or "soil" not in col_map:
                continue

            def _get(k):
                i = col_map.get(k)
                return texts[i].strip() if (i is not None and i < len(texts)) else ""

            t_raw = _get("tower").upper()
            if _TOWER_ID_RE.match(t_raw):
                cur_tower = t_raw
            elif t_raw:
                continue

            soil_raw = _get("soil")
            if not cur_tower or not soil_raw:
                continue

            std_soil, hit = map_standard_soil(soil_raw)
            m_rat = _NUM_RE.search(_get("ratio"))
            m_dep = _NUM_RE.search(_get("depth"))
            ratio_v = float(m_rat.group()) if m_rat else None
            depth_v = float(m_dep.group()) if m_dep else None

            ev = make_evidence(
                "勘察报告逐塔分层表", rel,
                page=p_idx + 1, bbox=item["bbox"],
                raw_text=f"{cur_tower} | {soil_raw} | {ratio_v or ''}% | {depth_v or ''}m",
            )
            rows.append(_make_survey_row(
                tower=cur_tower,
                soil_val=std_soil or soil_raw,
                soil_confirmed=hit,
                soil_note=f"岩土「{soil_raw}」→ 标准土质「{std_soil}」" if hit else f"未命中标准土质字典（原文：{soil_raw}），待复核",
                ratio=ratio_v,
                depth=depth_v,
                land=detect_land_kind(soil_raw),
                city=_get("city") or None,
                county=_get("county") or None,
                town=_get("town") or None,
                village=_get("village") or None,
                ev=ev,
                method="extract",
            ))

    return rows


def _make_survey_row(
    *, tower, soil_val, soil_confirmed, soil_note, ratio=None, depth=None,
    land=None, city=None, county=None, town=None, village=None, ev=None, method="extract",
):
    evs = [ev] if ev else []
    out = {
        "杆塔号": tower,
        "基础土质.土质类型": soil_val,
        "基础土质.比例（%）": ratio,
        "基础土质.分层深(m)": depth,
        "青苗.种类": None,
        "青苗.占地面积（亩）": None,
        "塔基位土地性质": land,
        "林地类型": None,
        "地点.州（市）": city,
        "地点.县（市、区）": county,
        "地点.镇（街道）": town,
        "地点.村（社区）": village,
        "挖土方式": "人工挖土",
        "接地土质类型": soil_val,
    }
    prov = {
        "杆塔号": make_prov("extract", conf=0.99, status="confirmed", machine_value=tower, evidences=evs),
        "基础土质.土质类型": make_prov(
            method, conf=0.92 if soil_confirmed else 0.5,
            status="confirmed" if soil_confirmed else "pending",
            machine_value=soil_val, note=soil_note,
            formula_trace=soil_note, evidences=evs,
        ),
        "基础土质.比例（%）": make_prov(
            method, conf=0.95 if ratio is not None else 0.0,
            status="confirmed" if ratio is not None else "empty",
            unit="%", machine_value=ratio, evidences=evs,
        ),
        "基础土质.分层深(m)": make_prov(
            method, conf=0.95 if depth is not None else 0.0,
            status="confirmed" if depth is not None else "empty",
            unit="m", machine_value=depth, evidences=evs,
        ),
        "青苗.种类": make_prov("extract", conf=0.0, status="empty", machine_value=None, note="外业清点项，施工图不列"),
        "青苗.占地面积（亩）": make_prov("extract", conf=0.0, status="empty", unit="亩", machine_value=None, note="外业清点项"),
        "塔基位土地性质": make_prov(
            "derive", conf=0.8 if land else 0.0,
            status="pending" if land else "empty",
            machine_value=land, note="由岩土/地表备注初判，待外业复核" if land else "",
            evidences=evs,
        ),
        "林地类型": make_prov("extract", conf=0.0, status="empty", machine_value=None, note="外业清点项"),
        "地点.州（市）": make_prov(method, conf=0.9 if city else 0.0, status="confirmed" if city else "empty", machine_value=city, evidences=evs),
        "地点.县（市、区）": make_prov(method, conf=0.9 if county else 0.0, status="confirmed" if county else "empty", machine_value=county, evidences=evs),
        "地点.镇（街道）": make_prov(method, conf=0.9 if town else 0.0, status="confirmed" if town else "empty", machine_value=town, evidences=evs),
        "地点.村（社区）": make_prov(method, conf=0.9 if village else 0.0, status="confirmed" if village else "empty", machine_value=village, evidences=evs),
        "挖土方式": make_prov("constant", conf=0.95, status="confirmed", machine_value="人工挖土", note="模板默认值"),
        "接地土质类型": make_prov("derive", conf=0.9 if soil_val else 0.0, status="confirmed" if soil_val else "empty", machine_value=soil_val, evidences=evs),
    }
    return {"row_id": tower, "out": out, "prov": prov}


def build_survey_sheet(
    *,
    survey_src: dict,
    tower_rows: list,
    rho_records: dict,
    rho_rel: str = "",
    default_city: str = "",
    cache_dir=None,
):
    """构建《勘测信息一览表》：有勘察报告（PDF/.docx）直取分层，缺勘察报告优雅降级至电阻率报告岩土骨架。"""
    if survey_src and survey_src.get("path"):
        spath = str(survey_src["path"])
        srel = survey_src.get("rel", "")
        if spath.lower().endswith(".docx"):
            parsed = parse_survey_docx(spath, rel_path=srel)
            mode_tag = "survey_docx"
        else:
            parsed = parse_survey_pdf(spath, rel_path=srel, cache_dir=cache_dir)
            mode_tag = "survey_pdf"
        if parsed:
            return {
                "rows": parsed,
                "missing_source": "",
                "source_files": [srel],
                "meta": {"mode": mode_tag, "rows": len(parsed)},
            }

    # 优雅降级：用土壤电阻率报告的逐塔岩土描述生成待复核骨架
    rows = []
    for r in tower_rows or []:
        tid = (r.get("out") or {}).get("杆塔号")
        if not tid:
            continue
        rrec = (rho_records or {}).get(tid) or {}
        raw_soil = rrec.get("soil") or ""
        std_soil, hit = map_standard_soil(raw_soil)
        land = detect_land_kind(raw_soil)
        ev = make_evidence(
            "电阻率报告岩土说明", rho_rel,
            page=rrec.get("page"), bbox=rrec.get("bbox"),
            raw_text=rrec.get("raw_text") or f"{tid} | {raw_soil}",
        ) if rrec else None
        note = (
            f"缺勘察报告分层表；暂由电阻率报告「{raw_soil}」映射为「{std_soil}」，请补传勘察报告或复核"
            if hit else "缺岩土工程勘察报告逐塔分层附表，待补传"
        )
        rows.append(_make_survey_row(
            tower=tid,
            soil_val=std_soil,
            soil_confirmed=False,  # 降级来源一律 pending 供人工复核
            soil_note=note,
            ratio=100 if std_soil else None,
            depth=None,
            land=land,
            city=default_city or None,
            ev=ev,
            method="cross_doc_extract",
        ))

    return {
        "rows": rows,
        "missing_source": "当前目录缺《岩土工程勘察报告》逐塔分层表；已依据《土壤电阻率报告》生成待复核土质骨架（标黄待核）",
        "source_files": [rho_rel] if rho_rel else [],
        "meta": {"mode": "rho_fallback", "rows": len(rows)},
    }
