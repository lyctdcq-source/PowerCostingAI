# -*- coding: utf-8 -*-
"""《线路设备材料清册》一源三表状态机（MaterialSectionFSM）。

一次扫描《第二册 线路设备材料清册.pdf》，使用 GridCellExtractor 精确还原全部折行单元格与合并单元格，
按「单项工程 / 线路段 → 专业章节（电气材料 / 光缆材料 / 结构材料表）」状态机路由，投射产出：
1. 《结构设备材料表》（原位汇总填满固定 26 行 R03–R28，保留多工程累加算式与证据链）；
2. 《电气设备材料表》（C01–C09 逐条明细 + 供货方）；
3. 《光缆设备材料表》（C01–C09 逐条明细 + 供货方）；
4. 清册小计内生自平衡校验池（CHK-06）及独立钢材/接地基准（供 CHK-03/04/05 对账）。
"""
import re

from .. import cache
from ..grid_parser import GridCellExtractor
from ..provenance import UnitConverter, make_evidence, make_prov
from ..store import STRUCT_MATERIAL_FIXED_ROWS

_SUBTOTAL_RE = re.compile(r"^(小计|合计|总计|共\s*计)")
_NOTE_RE = re.compile(r"^(注[:：]|说明[:：]|备注[:：]|以上为)")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_WIRE_NAME_RE = re.compile(r"(铝绞线|铝包钢绞线|钢芯铝绞线|架空地线|OPGW|导地线|架空绝缘导线)", re.IGNORECASE)

# 严格对齐母版《隐藏表（勿删）》D44:D58 合法计量单位符号
_STD_UNIT_MAP = {
    "米": "m", "m": "m", "延米": "m",
    "千米": "km", "公里": "km", "km": "km",
    "吨": "t", "t": "t",
    "千克": "kg", "公斤": "kg", "kg": "kg",
    "克": "g", "g": "g",
    "立方米": "m³", "方": "m³", "m3": "m³", "m³": "m³",
    "平方米": "m²", "平米": "m²", "m2": "m²", "m²": "m²",
    "套": "套", "付": "套", "副": "套",
    "个": "个", "只": "个", "支": "个", "根": "个", "基": "个", "条": "个",
    "片": "片", "块": "块", "台": "台", "组": "组", "串": "组",
}


def _std_unit(raw_u: str) -> str:
    s = re.sub(r"\s+", "", str(raw_u or ""))
    if not s:
        return ""
    return _STD_UNIT_MAP.get(s, _STD_UNIT_MAP.get(s.lower(), s))


def _to_num(val):
    if val in (None, ""):
        return None
    s = str(val).strip().replace(",", "").replace("，", "")
    if not s or s in ("-", "—", "/"):
        return None
    try:
        f = float(s)
        return int(f) if f == int(f) else round(f, 4)
    except ValueError:
        m = _NUM_RE.search(s)
        if not m:
            return None
        f = float(m.group())
        return int(f) if f == int(f) else round(f, 4)


def _norm_hdr(s: str) -> str:
    return re.sub(r"[\s（）()]+", "", s or "")


def _is_header_row(texts: list) -> bool:
    joined = "".join(_norm_hdr(t) for t in texts)
    has_name = any(k in joined for k in ("材料名称", "设备名称", "器材名称", "物资名称", "名称"))
    has_qty = any(k in joined for k in ("数量", "设计用量", "用量", "净量", "计价数量"))
    has_unit = ("单位" in joined)
    return has_name and (has_qty or has_unit)


def _map_header_cols(cells: list, layout_profile=None) -> list:
    """将表头行单元格映射为标准语义键及 x 中心区间（兼容跨院 LayoutProfile 覆盖）。"""
    lp_map: dict[int, str] = {}
    if isinstance(layout_profile, dict):
        for k, v in (layout_profile.get("column_mapping") or {}).items():
            try:
                lp_map[int(k)] = str(v)
            except (TypeError, ValueError):
                continue

    col_defs = []
    for idx, c in enumerate(cells):
        h = _norm_hdr(c["text"])
        lp_field = lp_map.get(idx, "")
        role = "other"
        if lp_field == "序号" or "序号" in h or "编号" == h:
            role = "seq"
        elif lp_field == "材料名称" or any(k in h for k in ("材料名称", "设备名称", "器材名称", "物资名称")) or h == "名称":
            role = "name"
        elif lp_field == "规格型号" or any(k in h for k in ("规格", "型号", "图号")):
            role = "spec"
        elif lp_field == "单重" or "单位重" in h or "单重" in h:
            role = "unit_wt"
        elif "总重" in h:
            role = "total_wt"
        elif lp_field == "单位" or "单位" in h:
            role = "unit"
        elif "设计用量" in h or "设计" in h:
            role = "qty_design"
        elif "损耗" in h:
            role = "qty_loss"  # 施工损耗：严禁填入「试验或备用量」，防止定额重复计取损耗！
        elif "试验" in h or "备用" in h:
            role = "qty_spare"
        elif "最终" in h or "总用量" in h:
            role = "qty_final"
        elif lp_field == "数量" or any(k in h for k in ("数量", "净量", "计价数量")):
            role = "qty_design"
        elif "物资类别" in h or "供货" in h:
            role = "supplier"
        elif "备注2" in h:
            role = "remark2"
        elif lp_field == "备注" or "备注" in h:
            role = "remark1"
        col_defs.append({
            "role": role,
            "header": h,
            "x0": c["x0"],
            "x1": c["x1"],
            "xc": (c["x0"] + c["x1"]) / 2.0,
        })
    return col_defs


def _bind_row_to_cols(cells: list, col_defs: list) -> dict:
    """按 x 中心把当前行单元格对齐到表头定义（免疫偶发列合并）。"""
    out = {}
    out_bbox = {}
    for c in cells:
        xc = (c["x0"] + c["x1"]) / 2.0
        best = None
        best_dist = 1e9
        for cd in col_defs:
            if cd["x0"] - 4.0 <= xc <= cd["x1"] + 4.0:
                best = cd
                break
            d = abs(xc - cd["xc"])
            if d < best_dist:
                best_dist = d
                best = cd
        if best and best["role"] != "other":
            role = best["role"]
            txt = (c["text"] or "").strip()
            if txt:
                if role in out and out[role] != txt:
                    out[role] = f"{out[role]} {txt}".strip()
                else:
                    out[role] = txt
                out_bbox[role] = c["bbox"]
    return out, out_bbox


def _extract_supplier(*texts):
    for t in texts:
        s = str(t or "")
        if "甲供" in s:
            return "甲供"
        if "乙供" in s:
            return "乙供"
    return ""


def _detect_segment_label(banner_text: str, current_seg: str, sub_idx: int) -> str:
    txt = re.sub(r"\s+", "", banner_text or "")
    if not txt:
        return current_seg
    m_bc = re.search(r"[（(]([A-F][、,，]?[A-F]?回)[）)]", txt)
    if m_bc:
        return m_bc.group(1)
    m_eng = re.search(r"工程名[:：]([^序]{2,36})", txt)
    if m_eng:
        seg = m_eng.group(1).strip("-～~")
        if seg:
            if "220kV" in seg and ("π" in seg or "Ⅲ" in seg):
                return "B、C回（220kVπ接段）"
            if "110kV" in seg and "双回" in seg:
                return "D回（110kV双回段）"
            if "110kV" in seg:
                return f"110kV线路段（单项{sub_idx}）"
            if "220kV" in seg:
                return f"220kV线路段（单项{sub_idx}）"
            return seg[:28]
    if "结构材料表" in txt:
        if "B、C" in txt or "B,C" in txt:
            return "B、C回（220kVπ接段）"
        if "110kV" in txt:
            return f"110kV线路段（单项{sub_idx}）"
    return current_seg


def _detect_header_section(texts: list, current_section: str) -> str:
    """根据表头独有列指纹自动精准路由到 electrical / optical / structural，兼容跨院语义措辞。"""
    joined = "".join(_norm_hdr(t) for t in texts)
    if any(k in joined for k in ("材料规格或型号", "物资类别", "结构材料")):
        return "structural"
    if any(k in joined for k in ("设备名称", "光缆", "OPGW")) and "单位重" not in joined:
        return "optical"
    if any(k in joined for k in ("材料名称", "器材名称")) and any(k in joined for k in ("单位重", "规格或图号", "单重", "型号规格")):
        return "electrical"
    return current_section


def _classify_struct_row(name: str, spec: str, unit: str, sub_part: str, remark: str = ""):
    """将结构材料表原始条目映射到母版《结构设备材料表》固定行号 R03–R28。"""
    n = (name or "").replace(" ", "")
    sp = (spec or "").replace(" ", "")
    rem = (remark or "").replace(" ", "")
    part = (sub_part or "").replace(" ", "")
    combo = f"{n}|{sp}|{part}|{rem}"

    # 跳过基础检测/钢护筒/灌注桩充盈量等（灌注桩预算定额子目已含充盈系数，严禁混入本体砼重复计取）
    if any(k in combo for k in ("检测", "试块", "静载", "声波", "应变", "钢护筒", "拉线", "接地", "充盈")):
        return None
    if "角钢塔" in n or ("铁塔" in n and "角钢" in combo):
        if any(k in sp.upper() for k in ("Q420", "Q460", "高强")):
            return 3  # 角钢（含高强钢）
        if any(k in sp.upper() for k in ("Q235", "Q355")):
            return 4  # 角钢（不含高强钢）
        return 3
    if "钢管杆" in n or "钢管塔" in n:
        if any(k in sp.upper() for k in ("Q420", "Q460", "高强")):
            return 5
        return 5  # 钢管（含高强钢，含 Q355B 钢管杆常规归类）
    if "护板" in combo and "钢筋" in combo:
        return 7
    if "钢筋" in n or "钢筋" in sp:
        if "护壁" in rem or "护壁" in n or "护壁" in sp:
            return 9
        return 8  # 基础钢筋
    if "地脚螺栓" in n:
        return 10
    if "垫层" in n or "垫层" in rem:
        return 13
    if "保护帽" in n or "保护帽" in rem:
        return 14
    if "混凝土" in n or "砼" in n or re.match(r"^C\d{2}", sp):
        if "护壁" in rem or "护壁" in n:
            return 12
        return 11  # 基础本体（含附加）
    if "排水沟" in n:
        return 15
    if "护坡" in n:
        return 16
    if "挡土墙" in n:
        return 17
    if "防撞" in n:
        return 18
    if "防坠落" in combo or "钢绞线" in sp or "防坠器" in sp or "张紧" in sp or "导轨" in sp or "转向器" in sp or "截止器" in sp or "连接器" in sp:
        if "钢绞线" in combo:
            return 19
        if "导轨" in combo:
            return 23
        if "防坠器" in combo:
            return 20
        if "张紧" in combo:
            return 22
        if "转向器" in combo:
            return 25
        if "截止器" in combo:
            return 26
        if "连接器" in combo:
            return 21
    return None


def parse_materials_pdf(path, rel_path="", cache_dir=None, segment_registry=None, layout_profile=None):
    """运行 MaterialSectionFSM，一次扫描清册产出 3 张材料表与校验数据。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    rel = (rel_path or "").replace("\\", "/")

    section = ""          # electrical | optical | structural
    sub_part = ""         # 结构表内子节：铁塔部分 / 基础本体 / 基础护壁 / 拉线
    elec_sub_idx = 0
    struct_sub_idx = 0
    cur_seg = segment_registry.for_subproject_index(1) if segment_registry else "线路段1"
    col_defs = []

    elec_rows = []
    opt_rows = []
    struct_raw_items = []  # 原始结构条目列表，汇总至固定 26 行
    excluded_overpour = [] # 剔除的灌注桩充盈量审计记录（防定额重复计取）
    subtotal_checks = []   # CHK-06 清册分章小计自平衡

    # 用于按块追踪小计自平衡
    block_sum_wt = 0.0
    block_sum_qty = 0.0
    block_items = 0

    def _resolve_seg(idx_1based: int, hint_txt: str, fallback: str):
        if segment_registry:
            return segment_registry.for_subproject_index(idx_1based, hint_text=hint_txt)
        return _detect_segment_label(hint_txt, fallback, idx_1based)

    def _update_section_from_text(txt: str):
        nonlocal section, sub_part, elec_sub_idx, struct_sub_idx, cur_seg, col_defs, block_sum_wt, block_sum_qty, block_items
        t = re.sub(r"\s+", "", txt or "")
        if not t:
            return
        if any(k in t for k in ("电气材料", "导地线及金具", "金具绝缘子")) and "编制说明" not in t:
            section = "electrical"
            elec_sub_idx += 1
            cur_seg = _resolve_seg(elec_sub_idx, t, f"线路段{elec_sub_idx}")
            block_sum_wt = block_sum_qty = 0.0
            block_items = 0
        elif any(k in t for k in ("光缆材料", "光缆及附件", "通信光缆")) and "编制说明" not in t:
            section = "optical"
            cur_seg = _resolve_seg(elec_sub_idx or 1, t, cur_seg)
            block_sum_wt = block_sum_qty = 0.0
            block_items = 0
        elif any(k in t for k in ("结构材料表", "铁塔及基础材料", "杆塔材料", "（一）铁塔部分", "(一)铁塔部分")) or ("结构部分" in t and "材料表" in t):
            if section != "structural" or "（一）铁塔部分" in t or "结构材料表" in t:
                struct_sub_idx += 1
            section = "structural"
            sub_part = "铁塔部分" if "铁塔" in t else ""
            cur_seg = _resolve_seg(struct_sub_idx, t, f"结构段{struct_sub_idx}")
            block_sum_wt = block_sum_qty = 0.0
            block_items = 0
        elif "工程名" in t or ("220kV线路" in t and "π" in t) or ("110kV线路" in t):
            idx_now = struct_sub_idx if section == "structural" else (elec_sub_idx or 1)
            cur_seg = _resolve_seg(idx_now, t, cur_seg)

        if section == "structural":
            if "铁塔部分" in t or "杆塔部分" in t:
                sub_part = "铁塔部分"
            elif "基础护壁" in t:
                sub_part = "基础护壁"
            elif "基础部分" in t or "基础本体" in t:
                sub_part = "基础本体"
            elif "基础检测" in t:
                sub_part = "基础检测"
            elif "拉线" in t and "注" not in t:
                sub_part = "拉线部分"

    for p_idx, pg in enumerate(doc["pages"]):
        txt_all = pg.get("text") or ""
        # 跳过封面与编制说明前期文字页（未进入任何清册章节且本页不含清册章节触发词）
        if not section and not any(k in txt_all for k in ("电气材料", "光缆材料", "结构材料表", "导地线及金具", "光缆及附件", "铁塔及基础材料", "杆塔材料")):
            continue

        grid = GridCellExtractor.extract_page(path, p_idx, cache_dir=cache_dir)
        for ln in grid.get("pre_lines") or []:
            _update_section_from_text(ln)

        for item in grid.get("rows") or []:
            if item.get("kind") == "banner":
                _update_section_from_text(item.get("text") or "")
                continue

            texts = item.get("texts") or []
            cells = item.get("cells") or []
            row_joined = " ".join(t for t in texts if t).strip()
            if not row_joined:
                continue

            # 行内也会出现大标题/子节标题（例如表内合并单元格「（一）铁塔部分」「电气材料」）
            if len([t for t in texts if t]) <= 2 and any(k in row_joined for k in ("电气材料", "光缆材料", "结构材料表", "工程名:", "工程名：", "（一）", "（二）", "铁塔部分", "基础部分", "基础护壁")):
                _update_section_from_text(row_joined)
                continue

            if _is_header_row(texts):
                new_sec = _detect_header_section(texts, section)
                if new_sec != section:
                    if new_sec == "electrical":
                        elec_sub_idx += 1
                        cur_seg = _resolve_seg(elec_sub_idx, "", f"线路段{elec_sub_idx}")
                    elif new_sec == "structural":
                        struct_sub_idx += 1
                        cur_seg = _resolve_seg(struct_sub_idx, "", f"结构段{struct_sub_idx}")
                    section = new_sec
                    block_sum_wt = block_sum_qty = 0.0
                    block_items = 0
                col_defs = _map_header_cols(cells, layout_profile=layout_profile)
                continue

            # 无数字且含「变～」「线路」的跨格工程标题行（如 P22/P38/P41 顶行）
            if any(k in row_joined for k in ("变～", "π”接入", "线路工程")) and not any(_to_num(t) is not None for t in texts):
                _update_section_from_text(row_joined)
                continue

            if not section or not col_defs:
                continue

            # 附注行拦截
            first_nonempty = next((t for t in texts if t), "")
            if _NOTE_RE.match(first_nonempty):
                continue

            # 小计/合计拦截 → 写入 CHK-06 自平衡校验池，不进明细输出
            if _SUBTOTAL_RE.match(first_nonempty) or any(_SUBTOTAL_RE.match(t) for t in texts[:2]):
                vals_map, _ = _bind_row_to_cols(cells, col_defs)
                stated_wt = _to_num(vals_map.get("total_wt"))
                stated_qty = _to_num(vals_map.get("qty_final") or vals_map.get("qty_design"))
                if stated_wt is not None and block_items > 0:
                    diff = round(block_sum_wt - stated_wt, 2)
                    rate = abs(diff) / stated_wt if stated_wt else 0.0
                    subtotal_checks.append({
                        "section": section,
                        "segment": cur_seg,
                        "page": p_idx + 1,
                        "metric": "总重(kg)",
                        "actual": round(block_sum_wt, 2),
                        "expect": round(stated_wt, 2),
                        "diff": diff,
                        "rate": rate,
                        "ok": rate <= 0.005,
                        "items": block_items,
                    })
                elif stated_qty is not None and block_items > 0:
                    diff = round(block_sum_qty - stated_qty, 3)
                    rate = abs(diff) / stated_qty if stated_qty else 0.0
                    subtotal_checks.append({
                        "section": section,
                        "segment": cur_seg,
                        "page": p_idx + 1,
                        "metric": "数量",
                        "actual": round(block_sum_qty, 3),
                        "expect": round(stated_qty, 3),
                        "diff": diff,
                        "rate": rate,
                        "ok": rate <= 0.005,
                        "items": block_items,
                    })
                block_sum_wt = block_sum_qty = 0.0
                block_items = 0
                continue

            vals, bboxes = _bind_row_to_cols(cells, col_defs)
            name = (vals.get("name") or "").strip()
            spec = (vals.get("spec") or "").strip()
            unit_raw = (vals.get("unit") or "").strip()
            qty_design = _to_num(vals.get("qty_design"))
            qty_loss = _to_num(vals.get("qty_loss"))
            qty_spare = _to_num(vals.get("qty_spare"))
            qty_final = _to_num(vals.get("qty_final"))
            unit_wt = _to_num(vals.get("unit_wt"))
            total_wt = _to_num(vals.get("total_wt"))
            supplier = _extract_supplier(vals.get("supplier"), vals.get("remark2"), vals.get("remark1"))

            # 过滤纯空行或拆散的表尾碎片
            if not name and not spec:
                continue
            if qty_design is None and qty_final is None and not unit_raw:
                continue
            # 过滤表内子节标签行（如「基础本体」「基础护壁」）
            if name in ("基础本体", "基础护壁", "铁塔部分", "拉线部分") and qty_design is None:
                sub_part = name
                continue

            eff_qty = qty_design if qty_design is not None else qty_final
            if total_wt is not None:
                block_sum_wt += float(total_wt)
            if eff_qty is not None:
                block_sum_qty += float(eff_qty)
            block_items += 1

            row_bbox = item["bbox"]
            ev = make_evidence(
                f"材料清册({cur_seg})", rel,
                page=p_idx + 1, bbox=row_bbox,
                raw_text=f"{name} | {spec} | {eff_qty} {unit_raw}".strip(" |"),
            )

            if section == "structural":
                rem_txt = (vals.get("remark1") or "") + " " + (vals.get("remark2") or "")
                if "充盈" in name or "充盈" in rem_txt:
                    excluded_overpour.append({"segment": cur_seg, "qty": eff_qty, "unit": unit_raw, "page": p_idx + 1})
                local_part = sub_part
                if "护壁" in name or "护壁" in spec or "护壁" in rem_txt:
                    local_part = "基础护壁"
                struct_raw_items.append({
                    "segment": cur_seg,
                    "sub_part": local_part,
                    "name": name,
                    "spec": spec,
                    "unit": unit_raw,
                    "qty": eff_qty,
                    "supplier": supplier,
                    "remark": rem_txt.strip(),
                    "page": p_idx + 1,
                    "bbox": row_bbox,
                    "ev": ev,
                })
            else:
                target_list = opt_rows if section == "optical" else elec_rows
                std_u = _std_unit(unit_raw)
                qty_out = eff_qty
                qty_trace = f"清册设计用量（不含损耗）= {eff_qty} {unit_raw}"
                # 架空导地线按定额计量规范将 米(m) 显式换算为 千米(km)，防止 1000 倍数量级错乱
                if std_u == "m" and eff_qty is not None and float(eff_qty) >= 100 and _WIRE_NAME_RE.search(name):
                    qty_out, conv_trace = UnitConverter.convert(eff_qty, "m", "km", decimals=3)
                    std_u = "km"
                    qty_trace = f"导地线定额单位归一：{conv_trace}"
                loss_note = f"（清册施工损耗 {qty_loss} 已剥离，定额软件自动计取损耗，不填入试验/备用量）" if qty_loss not in (None, 0) else ""

                out_row = {
                    "线路段名称": cur_seg,
                    "材料名称": name,
                    "规格型号": spec or None,
                    "单位": std_u or None,
                    "设计用量（不含损耗）": qty_out,
                    "试验或备用量": qty_spare,
                    "单重（kg）": unit_wt,
                    "合同单价含税（元）": None,
                    "供货方": supplier or None,
                }
                prov_row = {}
                for k, v in out_row.items():
                    note_k = f"框线网格提取 · 清册 P{p_idx + 1}"
                    trace_k = ""
                    if k == "设计用量（不含损耗）":
                        trace_k = qty_trace
                        note_k = note_k + loss_note
                    elif k == "试验或备用量" and loss_note:
                        note_k = loss_note.strip("（）")
                    prov_row[k] = make_prov(
                        "derive" if (k == "设计用量（不含损耗）" and std_u == "km" and _std_unit(unit_raw) == "m") else "extract",
                        conf=0.98 if v is not None else 1.0,
                        status="confirmed" if v is not None else "empty",
                        unit=std_u if k == "设计用量（不含损耗）" else "",
                        machine_value=v,
                        note=note_k,
                        formula_trace=trace_k,
                        evidences=[ev],
                    )
                target_list.append({
                    "row_id": f"{'O' if section == 'optical' else 'E'}{len(target_list) + 1}",
                    "page": p_idx + 1,
                    "bbox": row_bbox,
                    "out": out_row,
                    "prov": prov_row,
                })

        for ln in grid.get("post_lines") or []:
            _update_section_from_text(ln)

    # 汇总投射至《结构设备材料表》固定 26 行（R03–R28）
    buckets = {}  # template_row -> list[item]
    for it in struct_raw_items:
        tr = _classify_struct_row(it["name"], it["spec"], it["unit"], it["sub_part"], it.get("remark", ""))
        if tr is not None and it["qty"] is not None:
            buckets.setdefault(tr, []).append(it)

    struct_rows = []
    for tr, cat, spec_lbl, unit_lbl in STRUCT_MATERIAL_FIXED_ROWS:
        hits = buckets.get(tr) or []
        # 严格量纲守护：例如清册《排水沟》给长度「米(m)」，而母版 R15 要求体积「m³」→ 严禁把米直接当方填入！
        unit_mismatch = [
            x for x in hits
            if unit_lbl == "m³" and _std_unit(x.get("unit") or "") == "m"
        ]
        if unit_mismatch and len(unit_mismatch) == len(hits):
            total_qty = None
            raw_m = round(sum(float(x["qty"]) for x in hits), 3)
            qty_status = "pending"
            qty_note = f"量纲拦截：清册为长度 {raw_m:g} m，母版 R{tr:02d} 要求实体方量 {unit_lbl}，请按设计断面换算后录入"
            trace = f"清册原文：{' + '.join(f'{x['name']}({x['qty']:g}{x['unit']})' for x in hits)} → 缺断面系数，禁止按 m³ 直填"
        else:
            valid_hits = [x for x in hits if x not in unit_mismatch]
            total_qty = round(sum(float(x["qty"]) for x in valid_hits), 3) if valid_hits else None
            qty_status = "confirmed" if total_qty is not None else "empty"
            overpour_note = ""
            if tr == 11 and excluded_overpour:
                ov_sum = round(sum(float(x["qty"] or 0) for x in excluded_overpour), 3)
                overpour_note = f"（已剔除单列灌注桩充盈量 {ov_sum:g} m³，防定额重复计取充盈系数）"
            qty_note = (f"汇总清册结构材料表 {len(valid_hits)} 项{overpour_note}" if valid_hits else "本工程清册未出现该项")
            trace = " + ".join(f"{x['segment']}:{x['name']}({x['qty']:g}{x['unit']})" for x in valid_hits[:10])
            if total_qty is not None:
                trace = f"{trace} = {total_qty:g} {unit_lbl}"

        suppliers = sorted({x["supplier"] for x in hits if x.get("supplier")})
        sup_val = suppliers[0] if len(suppliers) == 1 else ("/".join(suppliers) if suppliers else None)
        evs = [x["ev"] for x in hits[:8]]

        out_r = {
            "材料名称": cat,
            "规格": spec_lbl,
            "单位": unit_lbl,
            "数量（不含损耗）": total_qty,
            "合同单价含税（元）": None,
            "合同单价不含税（元）": None,
            "供货方": sup_val,
        }
        prov_r = {
            "材料名称": make_prov("constant", conf=1.0, status="confirmed", machine_value=cat, note="院标母版固定项目"),
            "规格": make_prov("constant", conf=1.0, status="confirmed", machine_value=spec_lbl, note="院标母版固定规格"),
            "单位": make_prov("constant", conf=1.0, status="confirmed", machine_value=unit_lbl, note="院标母版固定单位"),
            "数量（不含损耗）": make_prov(
                "rollup", conf=0.98 if total_qty is not None else 0.0,
                status=qty_status,
                unit=unit_lbl, machine_value=total_qty,
                note=qty_note,
                formula_trace=trace, evidences=evs,
            ),
            "合同单价含税（元）": make_prov("extract", conf=0.0, status="empty", unit="元", machine_value=None),
            "合同单价不含税（元）": make_prov("extract", conf=0.0, status="empty", unit="元", machine_value=None),
            "供货方": make_prov(
                "extract", conf=0.98 if sup_val else 0.0,
                status="confirmed" if sup_val else "empty",
                machine_value=sup_val, evidences=evs[:2],
            ),
        }
        struct_rows.append({
            "row_id": f"R{tr:02d}",
            "template_row": tr,
            "out": out_r,
            "prov": prov_r,
        })

    # 提取可供全工程交叉校验（CHK-03/04/05/08）使用的清册独立基准
    def _sval(tr_no):
        return next((r["out"]["数量（不含损耗）"] for r in struct_rows if r["template_row"] == tr_no), None)

    angle_bench = round((_sval(3) or 0) + (_sval(4) or 0), 3) or None
    pole_bench = _sval(5)

    return {
        "structural_rows": struct_rows,
        "electrical_rows": elec_rows,
        "optical_rows": opt_rows,
        "subtotal_checks": subtotal_checks,
        "excluded_overpour": excluded_overpour,
        "benchmarks": {
            "angle_tower_t": angle_bench,
            "steel_pole_t": pole_bench,
            "rebar_body_t": _sval(8),
            "rebar_wall_t": _sval(9),
            "concrete_body_m3": _sval(11),
            "concrete_wall_m3": _sval(12),
            "raw_structural_items": len(struct_raw_items),
        },
        "source_files": [rel] if rel else [],
    }
