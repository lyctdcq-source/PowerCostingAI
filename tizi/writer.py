# -*- coding: utf-8 -*-
"""阶段六：输出。

三个产物：
  result.csv    —— 纯数据，便于对接下游系统
  result.xlsx   —— 带底色与溯源页，供人复核
  run_report.md —— 本次运行的完整记录（判了什么、缺了什么、校验结果）
"""
import csv
import os

FILL = {
    "extract": "E9F5F1",
    "derive": "ECF2FA",
    "lookup": "ECF2FA",
    "interval": "ECF2FA",
    "constant": "F4F5F7",
    "manual": "FFF1E6",
    "drawing_select": "FFF1E6",
}
PENDING_FILL = "FDF3E3"
STATUS_CN = {"confirmed": "已确认", "pending": "待复核"}
METHOD_CN = {
    "extract": "图纸提取",
    "derive": "规则推导",
    "lookup": "查表",
    "interval": "区间插值",
    "constant": "统一赋值",
    "manual": "人工修订",
    "drawing_select": "图纸框选",
}


def to_csv(rows, targets, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(targets)
        for r in rows:
            w.writerow(["" if r["out"].get(t) is None else r["out"][t] for t in targets])


def to_xlsx(rows, targets, prov_all, path, meta, revisions=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "提资结果"
    ws.freeze_panes = "A2"

    head_fill = PatternFill("solid", fgColor="F7F8F9")
    thin = Side(style="thin", color="E6E8EB")
    border = Border(bottom=thin)

    for j, t in enumerate(targets, 1):
        c = ws.cell(1, j, t)
        c.font = Font(bold=True, size=10)
        c.fill = head_fill
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.border = border

    for i, r in enumerate(rows, 2):
        for j, t in enumerate(targets, 1):
            v = r["out"].get(t)
            cell = ws.cell(i, j, "" if v is None else v)
            cell.font = Font(size=10)
            p = r.get("prov", {}).get(t, {})
            status = p.get("status", "confirmed")
            method = p.get("method", "extract")
            color = PENDING_FILL if status == "pending" else FILL.get(method, FILL["extract"])
            cell.fill = PatternFill("solid", fgColor=color)
            cell.alignment = Alignment(horizontal="left", vertical="center")

    widths = {"线路段": 8, "杆塔号": 8, "杆塔型号": 13, "杆塔类型": 9, "呼称高": 8,
              "线路转角": 10, "转向前缀": 9, "是否高低腿": 10, "位置桩号": 9,
              "定位高差": 9, "基础型号A": 13, "全重": 9, "线路类型": 9}
    for j, t in enumerate(targets, 1):
        ws.column_dimensions[ws.cell(1, j).column_letter].width = widths.get(t, 10)

    # ---- 溯源页 ----
    ws2 = wb.create_sheet("溯源清单")
    heads = ["行号", "字段", "值", "来源", "页码", "区域", "方法", "置信度", "状态", "说明"]
    for j, h in enumerate(heads, 1):
        c = ws2.cell(1, j, h)
        c.font = Font(bold=True, size=10)
        c.fill = head_fill
    row_i = 2
    for i, r in enumerate(rows, 1):
        for t in targets:
            p = r.get("prov", {}).get(t)
            if not p:
                continue
            vals = [i, t, r["out"].get(t), p.get("source", ""), p.get("page"),
                    ",".join(str(x) for x in p["bbox"]) if p.get("bbox") else "",
                    METHOD_CN.get(p.get("method"), p.get("method")),
                    p.get("conf"), STATUS_CN.get(p.get("status"), p.get("status")), (p.get("note") or "")[:40]]
            for j, v in enumerate(vals, 1):
                ws2.cell(row_i, j, "" if v is None else v).font = Font(size=9)
            row_i += 1
    for j, w in enumerate([6, 12, 16, 18, 6, 22, 10, 8, 10, 30], 1):
        ws2.column_dimensions[ws2.cell(1, j).column_letter].width = w

    # ---- 人工修订清单（只在「人工复核版」里出现） ----
    if revisions:
        ws3 = wb.create_sheet("修订清单")
        heads3 = ["行号", "塔号", "字段", "机器原值", "人工复核值", "类型", "时间"]
        for j, h in enumerate(heads3, 1):
            c = ws3.cell(1, j, h)
            c.font = Font(bold=True, size=10)
            c.fill = head_fill
        for i, rv in enumerate(revisions, 2):
            for j, v in enumerate(rv, 1):
                ws3.cell(i, j, "" if v is None else v).font = Font(size=9)
        for j, w in enumerate([6, 10, 14, 16, 16, 12, 10], 1):
            ws3.column_dimensions[ws3.cell(1, j).column_letter].width = w

    wb.save(path)


def to_report(ctx, path, revs=None):
    L = []
    L.append(f"# 提资提取运行报告\n")
    L.append(f"- 工程：{ctx['project']}")
    L.append(f"- 模板：{ctx['template']}")
    L.append(f"- 生成时间：{ctx['time']}\n")

    L.append("## 一、载体判定\n")
    L.append("| 源 | 页数 | 字/页 | 词/页 | 图形/页 | 判定 | 管道 | 自检 |")
    L.append("|---|---:|---:|---:|---:|---|---|---|")
    for s in ctx["sources"]:
        if s.get("error"):
            L.append(f"| {s['name']} | - | - | - | - | 读取失败 | - | {s['error']} |")
            continue
        flag = "✓" if s["carrier"] == s.get("expect") else f"⚠ 预期 {s.get('expect')}"
        if s.get("skipped"):
            flag = "跳过"
        L.append(f"| {s['name']} | {s['pages']} | {s['chars_pg']:g} | {s['words_pg']:g} | "
                 f"{s['draws_pg']:g} | {s['carrier']} | {s['pipeline']} | {flag} |")

    L.append(f"\n## 二、解析\n")
    L.append(f"- 还原 {ctx['n_rows']} 行（版式模板 `{ctx['layout_id']}`）")
    L.append(f"- 行识别正则 `{ctx['row_pattern']}`")

    L.append("\n## 三、字段映射（规则补全前）\n")
    L.append("| 字段 | 方法 | 已填 | 空值 |")
    L.append("|---|---|---:|---:|")
    for m in ctx["map_stats"]:
        L.append(f"| {m['target']} | {m['method']} | {m['filled']} | {m['empty']} |")

    L.append("\n## 四、规则补全\n")
    L.append("| 字段 | 规则 | 命中 | 待复核 |")
    L.append("|---|---|---:|---:|")
    for s in ctx["rule_stats"]:
        L.append(f"| {s['target']} | {s['type']} | {s['ok']} | {s['miss']} |")

    L.append("\n## 五、双源校验\n")
    L.append("| 校验项 | 实得 | 基准 | 偏差 | 容差 | 结论 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for c in ctx["checks"]:
        L.append(f"| {c['name']} | {c['actual']:g} {c['unit']} | {c['expect']:g} {c['unit']} | "
                 f"{c['rate']*100:.2f}% | {c['tolerance']*100:.0f}% | {'通过' if c['ok'] else '超差'} |")

    L.append("\n## 六、覆盖率\n")
    L.append(f"- 单元格总数：{ctx['cells_total']}")
    L.append(f"- 已确认：{ctx['cells_ok']}（{ctx['cells_ok']/ctx['cells_total']*100:.1f}%）")
    L.append(f"- 待复核：{ctx['cells_pending']}")
    L.append(f"- 空值：{ctx['cells_empty']}")

    L.append("\n## 七、声明\n")
    L.append("- 所有数值均由确定性代码提取，可回溯到页码与区域坐标；模型仅参与配置生成与结果解释。")
    L.append("- 标为「待复核」的单元格**未做任何推测**，需人工确认或补充资料。")
    L.append("- 本引擎不承诺 100% 覆盖：图纸中不存在的信息（如纯矢量图中的标注、缺失的预算册）无法提取。")

    if revs:
        L.append("\n## 八、人工修订\n")
        L.append(f"- 共 {len(revs)} 格由人工处理（改值或标待复核），详见 xlsx 的「修订清单」页。")
        L.append("- 机器原值完整保留在 `result_machine.xlsx` / `result_machine.csv`，可逐格对照。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------------------
# 双版本导出：机器原值 / 人工复核版 + 修订清单
#
# 原则：人工改过的格子不能覆盖掉机器结果。两份都导出，谁改了什么单独列清楚，
# 这样复核人、审批人、后续审计都能看到「原始判断」和「人的判断」的差异。
# ---------------------------------------------------------------------------

def _rev_rows(rows, targets, overrides):
    """把 overrides 摊成一张可读的修订清单，并还原一份机器原值数据。"""
    import copy
    machine = copy.deepcopy(rows)
    revs = []
    for (r, c), rec in sorted(overrides.items()):
        if 0 <= r < len(machine):
            machine[r]["out"][c] = rec.get("old")
            if rec.get("old_prov"):
                machine[r].setdefault("prov", {})[c] = rec["old_prov"]
            else:
                machine[r].setdefault("prov", {}).pop(c, None)
        tower = ""
        if 0 <= r < len(rows):
            tower = rows[r]["out"].get("杆塔号") or rows[r]["out"].get("杆号") or ""
        old, new = rec.get("old"), rec.get("value")
        kind = ("标待复核" if rec.get("flag") == "pending"
                else "图纸框选" if rec.get("kind") == "drawing_select"
                else "改值")
        revs.append([r + 2, tower, c,
                     "" if old in (None, "") else old,
                     "" if new in (None, "") else new,
                     kind, rec.get("time", "")])
    return machine, revs


def to_revision_csv(revs, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["行号", "塔号", "字段", "机器原值", "人工复核值", "类型", "时间"])
        w.writerows(revs)


def _safe_write(fn, path):
    """写一个产物；目标被 Excel / 预览器占用时另存带时间戳的副本。

    用户常常一边开着 result.xlsx 看、一边点「导出交付」。Windows 会锁住
    文件，此时不能让整批导出失败，也不能偷偷跳过——另存一份并明确告知。
    """
    import time
    try:
        fn(path)
        return os.path.basename(path), ""
    except PermissionError:
        base, ext = os.path.splitext(path)
        alt = f"{base}_{time.strftime('%H%M%S')}{ext}"
        fn(alt)
        return os.path.basename(alt), (f"{os.path.basename(path)} 正被其它程序占用，"
                                       f"已另存为 {os.path.basename(alt)}")


def export_pair(rows, targets, overrides, out_dir, ctx):
    """一次写出 6 个产物，返回文件名清单。

    result.*          —— 人工复核版（含修订清单页），日常交付用这个
    result_machine.*  —— 机器原值版，人工改过的格子已还原
    revision_log.csv  —— 逐格修订记录
    run_report.md     —— 运行记录
    """
    os.makedirs(out_dir, exist_ok=True)
    machine, revs = _rev_rows(rows, targets, overrides)

    jobs = [
        ("result.csv", lambda p: to_csv(rows, targets, p)),
        ("result_machine.csv", lambda p: to_csv(machine, targets, p)),
        ("result.xlsx", lambda p: to_xlsx(rows, targets, None, p, ctx, revisions=revs)),
        ("result_machine.xlsx", lambda p: to_xlsx(machine, targets, None, p, ctx)),
        ("revision_log.csv", lambda p: to_revision_csv(revs, p)),
        ("run_report.md", lambda p: to_report(ctx, p, revs=revs)),
    ]
    names, warnings = [], []
    for name, fn in jobs:
        actual, warn = _safe_write(fn, os.path.join(out_dir, name))
        names.append(actual)
        if warn:
            warnings.append(warn)

    return {"files": [os.path.join(out_dir, n) for n in names],
            "n_revisions": len(revs), "names": names, "warnings": warnings}
