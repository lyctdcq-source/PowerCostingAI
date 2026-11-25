# -*- coding: utf-8 -*-
"""院标提资单模板原位回填器（TemplateWorkbookWriter）。

基于 110kV某线路工程提资单.xlsx（13 个 Sheet：9 张业务表 + 4 张说明/字典表），实现双轨输出：
1. 标准导入版（tizi_standard.xlsx）：
   - 原位保留全部 13 个 Sheet、隐藏字典表、表头合并样式与 C20+ 默认值列；
   - 扩行时复制模板数据行样式，使用 openpyxl Translator 平移内嵌 Excel 公式；
   - 同步维护 DataValidation 区间并清理坏外部引用（#REF!），不新增多余 Sheet，100% 兼容下游计价导入。
2. 复核溯源版（tizi_audit.xlsx）：
   - 同结构副本，叠加状态底色与单元格批注（出处文件、页码、推导算式）；
   - 附加《溯源总表》《双源校验报告》《人工修订记录》三张审计页。
"""
import copy
import functools
import io
import os
import re
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .store import SHEET_ORDER, SHEET_SCHEMAS, cell_display_status

DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "defaults" / "templates" / "line_tizi_template.xlsx"
)

_FILLS = {
    "extract": PatternFill("solid", fgColor="E8F5E9"),   # 绿：图纸直取
    "rule": PatternFill("solid", fgColor="E3F2FD"),      # 蓝：多源推导/查表
    "pending": PatternFill("solid", fgColor="FFF8E1"),   # 黄：待复核
    "conflict": PatternFill("solid", fgColor="FFEBEE"),  # 红：多源冲突
    "manual": PatternFill("solid", fgColor="F3E5F5"),    # 紫：人工修订
    "drawing": PatternFill("solid", fgColor="E0F7FA"),   # 青：图纸框选
}
_THIN = Side(style="thin", color="D4D4D4")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HDR_FILL = PatternFill("solid", fgColor="252525")
_HDR_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
_BODY_FONT = Font(name="Microsoft YaHei", size=9)


_TPL_BYTES_CACHE: dict[str, bytes] = {}
_BAD_DV_RX = re.compile(r"(\[\d+\]|#REF!)")


def _load_template_wb(tpl: Path):
    key = str(tpl.resolve())
    raw = _TPL_BYTES_CACHE.get(key)
    if raw is None:
        raw = tpl.read_bytes()
        _TPL_BYTES_CACHE[key] = raw
    return openpyxl.load_workbook(io.BytesIO(raw))


@functools.lru_cache(maxsize=64)
def _row_ref_rx(src_row: int):
    return re.compile(rf"(?<=[A-Z]){src_row}(?!\d)")


def _copy_cell_style(src_cell, dst_cell):
    if src_cell.has_style:
        dst_cell.font = copy.copy(src_cell.font)
        dst_cell.fill = copy.copy(src_cell.fill)
        dst_cell.border = copy.copy(src_cell.border)
        dst_cell.alignment = copy.copy(src_cell.alignment)
        dst_cell.number_format = src_cell.number_format


def _translate_value(val, src_row: int, dst_row: int, col_idx: int):
    if not isinstance(val, str) or not val.startswith("=") or src_row == dst_row:
        return val
    # 快速通道：常见行相对公式（如 '=P4*50'、'=C4*10'），避免 openpyxl Tokenizer 每格重建 AST
    if "$" not in val and "!" not in val and ":" not in val:
        return _row_ref_rx(src_row).sub(str(dst_row), val)
    col_letter = get_column_letter(col_idx)
    origin = f"{col_letter}{src_row}"
    target = f"{col_letter}{dst_row}"
    try:
        return Translator(val, origin=origin).translate_formula(target)
    except Exception:  # noqa: BLE001
        return val


def _sanitize_data_validations(ws):
    """全面清理所有外部工作簿死链（如 '[1]...' / '[2]...' / '#REF!'），防止 Excel 弹出修复告警。"""
    if not getattr(ws, "data_validations", None):
        return
    clean = []
    for dv in list(ws.data_validations.dataValidation):
        f1 = str(dv.formula1 or "")
        f2 = str(dv.formula2 or "")
        if _BAD_DV_RX.search(f1) or _BAD_DV_RX.search(f2):
            continue
        clean.append(dv)
    ws.data_validations.dataValidation = clean


def _snapshot_proto_row(ws, proto_row: int, max_col: int):
    snap = {}
    for c in range(1, max_col + 1):
        cell = ws.cell(proto_row, c)
        snap[c] = {
            "value": cell.value,
            "_style": copy.copy(cell._style) if cell.has_style else None,
        }
    return snap


def _apply_proto_style(dst_cell, proto_info: dict):
    st = proto_info.get("_style")
    if st is not None:
        dst_cell._style = copy.copy(st)


def _unmerge_data_area(ws, start_row: int):
    """只解除数据区（>= start_row）中的示例合并单元格，绝不触碰表头区合并。"""
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= start_row:
            ws.unmerge_cells(str(rng))


def _clear_sample_rows(ws, start_row: int, max_col: int):
    """清空模板自带的示例行内容（仅遍历已存在单元格，避免分配数万空 Cell 拖慢保存）。"""
    _unmerge_data_area(ws, start_row)
    for (r, c), cell in list(ws._cells.items()):
        if r >= start_row and c <= max_col and cell.value is not None:
            cell.value = None


def _build_comment_text(val, prov: dict):
    if not prov:
        return ""
    lines = []
    method = prov.get("method") or "extract"
    status = prov.get("status") or "confirmed"
    lines.append(f"方式：{method} | 状态：{status}")
    if prov.get("formula_trace"):
        lines.append(f"算式：{prov['formula_trace']}")
    evs = prov.get("evidences") or []
    for idx, ev in enumerate(evs[:3], 1):
        f = os.path.basename(ev.get("file") or "")
        pg = f"P{ev['page']}" if ev.get("page") else ""
        lines.append(f"证据{idx} [{ev.get('role', '出处')}] {f} {pg} {ev.get('raw_text', '')[:60]}".strip())
    if not evs and prov.get("source"):
        pg = f"P{prov['page']}" if prov.get("page") else ""
        lines.append(f"来源：{prov['source']} {pg}".strip())
    if prov.get("note"):
        lines.append(f"说明：{prov['note']}")
    return "\n".join(lines)[:450]


def _fill_dynamic_sheet(ws, schema: dict, rows: list, *, audit: bool = False):
    start_row = schema["data_start_row"]
    default_start = schema["default_col_start"]
    max_col = ws.max_column

    proto = _snapshot_proto_row(ws, start_row, max_col)
    _clear_sample_rows(ws, start_row, max_col)

    col_items = [(c["key"], c["col_idx"]) for c in schema["columns"] if c["col_idx"] <= max_col]
    written_cols_by_row = {c_idx for _, c_idx in col_items}
    # 预先筛选出需跨行复制/平移的非映射默认值列与公式列，避免 214×145 空循环
    extra_proto = []
    for c_idx in range(1, max_col + 1):
        if c_idx in written_cols_by_row:
            continue
        pinfo = proto.get(c_idx)
        if not pinfo:
            continue
        pval = pinfo["value"]
        is_formula = isinstance(pval, str) and pval.startswith("=")
        if is_formula or (default_start and c_idx >= default_start and pval is not None):
            extra_proto.append((c_idx, pinfo, pval))

    for idx, row_obj in enumerate(rows):
        dst_r = start_row + idx
        out_map = row_obj.get("out") or {}
        prov_map = row_obj.get("prov") or {}

        # 1) 写入 schema 映射列
        for key, col_idx in col_items:
            val = out_map.get(key)
            cell = ws.cell(dst_r, col_idx)
            pinfo = proto.get(col_idx)
            if pinfo:
                _apply_proto_style(cell, pinfo)
            if val not in (None, ""):
                cell.value = val
            elif default_start and col_idx >= default_start and pinfo:
                cell.value = _translate_value(pinfo["value"], start_row, dst_r, col_idx)
            elif pinfo and isinstance(pinfo.get("value"), str) and pinfo["value"].startswith("="):
                cell.value = _translate_value(pinfo["value"], start_row, dst_r, col_idx)

            if audit:
                pv = prov_map.get(key)
                st = cell_display_status(val, pv)
                if st in _FILLS and (val not in (None, "") or st in ("pending", "conflict")):
                    cell.fill = _FILLS[st]
                # 为关键溯源/复核格附加 Excel 批注（全量溯源同时完整写入《溯源总表》），兼顾可读性与导出速度
                need_comment = bool(pv) and (
                    st in ("pending", "conflict", "manual", "drawing")
                    or (pv.get("method") in ("multi_join_lookup", "cross_doc_extract", "std_lib_lookup", "price_ref", "rollup")
                        and val not in (None, ""))
                )
                if need_comment:
                    ctext = _build_comment_text(val, pv)
                    if ctext:
                        cell.comment = Comment(ctext, "PowerCostingAI")

        # 2) 对未被 schema 显式覆盖的列：仅处理预筛出的默认值列及公式列
        for c_idx, pinfo, pval in extra_proto:
            cell = ws.cell(dst_r, c_idx)
            _apply_proto_style(cell, pinfo)
            cell.value = _translate_value(pval, start_row, dst_r, c_idx)


def _fill_fixed_struct_sheet(ws, schema: dict, rows: list, *, audit: bool = False):
    """原位填充《结构设备材料表》固定 26 行（R03–R28），绝不改动 C01–C03 固定项目名。"""
    # 按 (材料名称, 规格) 或行号对齐
    by_spec = {}
    for r in rows:
        out = r.get("out") or {}
        key = (str(out.get("材料名称") or "").strip(), str(out.get("规格") or "").strip())
        by_spec[key] = r
        if r.get("template_row"):
            by_spec[int(r["template_row"])] = r

    cur_cat = ""
    for tr in range(3, 29):
        c1 = ws.cell(tr, 1).value
        if c1:
            cur_cat = str(c1).strip()
        spec = str(ws.cell(tr, 2).value or "").strip()
        rec = by_spec.get(tr) or by_spec.get((cur_cat, spec))
        if not rec:
            continue
        out_map = rec.get("out") or {}
        prov_map = rec.get("prov") or {}
        for col_key, col_idx in [
            ("数量（不含损耗）", 4),
            ("合同单价含税（元）", 5),
            ("合同单价不含税（元）", 6),
            ("供货方", 7),
        ]:
            val = out_map.get(col_key)
            if val not in (None, ""):
                cell = ws.cell(tr, col_idx)
                cell.value = val
                if audit:
                    pv = prov_map.get(col_key)
                    st = cell_display_status(val, pv)
                    if st in _FILLS:
                        cell.fill = _FILLS[st]
                    ctext = _build_comment_text(val, pv)
                    if ctext:
                        cell.comment = Comment(ctext, "PowerCostingAI")


def _append_audit_sheets(wb, store, checks):
    # 1) 溯源总表
    ws_p = wb.create_sheet("溯源总表")
    hdrs = ["工作表", "行号", "关键标识", "字段", "现值", "机器原值", "状态", "方式", "推导算式", "证据1出处", "证据数量"]
    ws_p.append(hdrs)
    for c in range(1, len(hdrs) + 1):
        cell = ws_p.cell(1, c)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for sname in SHEET_ORDER:
        sd = store.get_sheet(sname)
        rows = sd.get("rows") or []
        if not rows:
            continue
        for idx, r in enumerate(rows):
            out = r.get("out") or {}
            prov = r.get("prov") or {}
            anchor = out.get("杆塔号") or out.get("基础型号") or out.get("材料名称") or out.get("线路段名称") or f"R{idx + 1}"
            for col_k, pv in prov.items():
                val = out.get(col_k)
                st_disp = cell_display_status(val, pv)
                if val in (None, "") and st_disp not in ("pending", "conflict"):
                    continue
                # 跳过无溯源争议的纯固定模板常量，保持《溯源总表》精炼高效
                if pv.get("method") == "constant" and not pv.get("revisions") and st_disp not in ("pending", "conflict"):
                    continue
                evs = pv.get("evidences") or []
                ev1 = ""
                if evs:
                    ev1 = f"{os.path.basename(evs[0].get('file') or '')} P{evs[0].get('page') or ''}".strip()
                elif pv.get("source"):
                    ev1 = f"{pv.get('source')} P{pv.get('page') or ''}".strip()
                ws_p.append([
                    sname, idx + 1, str(anchor), col_k,
                    val, pv.get("machine_value"),
                    cell_display_status(val, pv), pv.get("method", ""),
                    pv.get("formula_trace", ""), ev1, len(evs),
                ])

    # 2) 双源校验报告
    ws_c = wb.create_sheet("双源校验报告")
    chdrs = ["编号", "校验项目", "提取实得", "独立基准", "单位", "差值", "偏差率", "容差", "结论", "说明"]
    ws_c.append(chdrs)
    for c in range(1, len(chdrs) + 1):
        cell = ws_c.cell(1, c)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
    for i, chk in enumerate(checks or [], 1):
        ws_c.append([
            chk.get("code") or f"CHK-{i:02d}",
            chk.get("name", ""),
            chk.get("actual"),
            chk.get("expect"),
            chk.get("unit", ""),
            chk.get("diff"),
            f"{(chk.get('rate') or 0) * 100:.2f}%",
            f"{(chk.get('tolerance') or 0) * 100:.2f}%",
            "通过" if chk.get("ok") else "超差",
            chk.get("note", ""),
        ])

    # 3) 人工修订记录
    ws_r = wb.create_sheet("人工修订记录")
    rhdrs = ["工作表", "行号", "标识", "字段", "原值", "新值", "类型", "时间"]
    ws_r.append(rhdrs)
    for c in range(1, len(rhdrs) + 1):
        cell = ws_r.cell(1, c)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
    for rev in store.revisions or []:
        ws_r.append([
            rev.get("sheet", ""),
            rev.get("line_no", ""),
            rev.get("anchor", ""),
            rev.get("column", ""),
            rev.get("old"),
            rev.get("value"),
            rev.get("kind", ""),
            rev.get("time", ""),
        ])


def export_single_workbook(store, checks, out_dir, *, audit_mode: bool = False, template_path=None):
    """按需导出单份院标提资单（标准导入版或复核溯源版），将标准版单次导出控制在 ~1s 内。"""
    tpl = Path(template_path) if template_path else DEFAULT_TEMPLATE_PATH
    if not tpl.is_file():
        raise FileNotFoundError(f"找不到院标提资单模板：{tpl}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst_path = out_dir / ("tizi_audit.xlsx" if audit_mode else "tizi_standard.xlsx")

    wb = _load_template_wb(tpl)
    for sname in SHEET_ORDER:
        if sname not in wb.sheetnames:
            continue
        ws = wb[sname]
        _sanitize_data_validations(ws)
        sd = store.get_sheet(sname)
        rows = sd.get("rows") or []
        if not rows:
            continue
        schema = SHEET_SCHEMAS[sname]
        if schema.get("fixed_rows"):
            _fill_fixed_struct_sheet(ws, schema, rows, audit=audit_mode)
        else:
            _fill_dynamic_sheet(ws, schema, rows, audit=audit_mode)
    if audit_mode:
        _append_audit_sheets(wb, store, checks)
    wb.save(str(dst_path))
    wb.close()
    return str(dst_path)


def export_workbook_pair(store, checks, out_dir, template_path=None):
    """生成双轨院标提资单：tizi_standard.xlsx（标准导入版）+ tizi_audit.xlsx（复核溯源版）。"""
    std_path = export_single_workbook(store, checks, out_dir, audit_mode=False, template_path=template_path)
    audit_path = export_single_workbook(store, checks, out_dir, audit_mode=True, template_path=template_path)
    return {
        "standard": std_path,
        "audit": audit_path,
        "names": [Path(std_path).name, Path(audit_path).name],
    }
