"""评审·修改校验：在对话中修改数量评审结果前，逐条检查这次修改是否允许，以及是否必须由用户批准。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。对话里的写入类工具（改结论、人工确认、改配对、撤销修改）
执行前，宿主都会调用 check_edit；返回不允许时不修改，并把原因交给模型。每次保存除整条流程样例外，
还必须通过平台的修改校验底线测试（重复占用、不存在的编号、非法档位、缺少依据、批量须批准等），否则不生效。

check_edit(op, view) 的参数：
- op：{"type": "set_verdict" | "confirm" | "change_pair" | "undo", "items": [...]}
  set_verdict 的条目为 {source_id, target_id, band, reason}；confirm 为 {source_id, target_id, note}；
  change_pair 为一条 {source_id, target_id, reason}，target_id 为空表示解除配对；undo 为 {edit_id}。
- view：{"rows": 当前结果行（source_id、target_id、band、confirmed、covered_by、in_set、covers、covers_sources、set_pairs），
  "source_ids": 造价编号, "target_ids": 清册编号, "pairs": {造价编号: 清册编号}, "admin": 是否管理员,
  "edits": 修改记录（id、undone）}
返回 {"allow": 是否允许, "reason": 不允许的原因, "confirm": 即使在自动修改模式下也须用户批准, "notes": [提示]}。
"""
BANDS = ("一致", "基本一致", "部分一致", "不一致", "不参与核对")
CONFIRM_OVER = 5        # 一次修改超过这么多条时，自动修改模式下也要先请用户批准
MIN_REASON = 4          # 修改结论、确认复核都要写依据，至少这么多字
SINGLE_SIDE_BANDS = ("不一致", "不参与核对")   # 没有对应物资的单侧条目只能定为这几档


def _key(item):
    return (str(item.get("source_id") or ""), str(item.get("target_id") or ""))


def _label(key):
    source, target = key
    return "、".join(x for x in (source and "造价" + source, target and "清册" + target) if x) or "空条目"


def _anchor(row):
    return bool(row.get("covers") or row.get("covers_sources") or row.get("set_pairs"))


def _refuse(reason):
    return {"allow": False, "reason": reason, "confirm": False, "notes": []}


def _check_verdicts(items, rows):
    notes, seen = [], set()
    for item in items:
        key = _key(item)
        row = rows.get(key)
        if row is None:
            return _refuse(f"没有找到{_label(key)}这条匹配结果，请先查结果确认编号")
        if key in seen:
            return _refuse(f"{_label(key)}在本次修改中重复出现")
        seen.add(key)
        band = str(item.get("band") or "")
        if band not in BANDS:
            return _refuse(f"结论只能是：{'、'.join(BANDS)}；“{band}”不在其中")
        if len(str(item.get("reason") or "").strip()) < MIN_REASON:
            return _refuse(f"修改{_label(key)}的结论须写明依据（至少 {MIN_REASON} 个字）")
        if row.get("covered_by"):
            return _refuse(f"{_label(key)}是造价成套项（序号 {row['covered_by']}）的组成件，结论随成套项整体核对；"
                           f"请修改成套项（造价{row['covered_by']}）的结论")
        single = not (key[0] and key[1]) and not _anchor(row)
        if single and band not in SINGLE_SIDE_BANDS:
            return _refuse(f"{_label(key)}没有对应物资，只能定为{'或'.join(SINGLE_SIDE_BANDS)}；确有对应物资时请先改配对")
        if row.get("confirmed"):
            notes.append(f"{_label(key)}已人工确认过，本次修改会覆盖原复核意见")
    return {"allow": True, "reason": "", "confirm": len(items) > CONFIRM_OVER, "notes": notes}


def _check_confirms(items, rows):
    for item in items:
        key = _key(item)
        if key not in rows:
            return _refuse(f"没有找到{_label(key)}这条匹配结果，请先查结果确认编号")
        if len(str(item.get("note") or "").strip()) < MIN_REASON:
            return _refuse(f"确认{_label(key)}须写明复核依据（至少 {MIN_REASON} 个字）")
    return {"allow": True, "reason": "", "confirm": len(items) > CONFIRM_OVER, "notes": []}


def _check_pair(items, view, rows):
    if not view.get("admin"):
        return _refuse("改配对需要管理员权限；可以改为修改结论或填写复核意见")
    if len(items) != 1:
        return _refuse("一次只能改一条配对")
    item = items[0]
    source, target = str(item.get("source_id") or ""), str(item.get("target_id") or "")
    pairs = {str(k): str(v) for k, v in (view.get("pairs") or {}).items()}
    if source not in {str(x) for x in view.get("source_ids") or []}:
        return _refuse(f"造价{source}不存在")
    if target and target not in {str(x) for x in view.get("target_ids") or []}:
        return _refuse(f"清册{target}不存在")
    if len(str(item.get("reason") or "").strip()) < MIN_REASON:
        return _refuse(f"改配对须写明依据（至少 {MIN_REASON} 个字）")
    if pairs.get(source, "") == target:
        return _refuse(f"造价{source}当前已经是这个配对，无需修改" if target else f"造价{source}当前没有配对，无需解除")
    owner = next((s for s, t in pairs.items() if t == target and s != source), None) if target else None
    if owner:
        return _refuse(f"清册{target}已与造价{owner}配对；请先解除那条配对，再改这一条")
    for row in rows.values():
        if row.get("source_id") and str(row["source_id"]) == source and _anchor(row):
            return _refuse(f"造价{source}是成套项（整体覆盖组成件），改配对会解散成套对应组；请在条目详情中处理")
        if target and row.get("target_id") and str(row["target_id"]) == target and row.get("covered_by"):
            return _refuse(f"清册{target}是造价成套项（序号 {row['covered_by']}）的组成件，请先把它移出成套对应组")
    notes = ["改配对后新形成的配对需要重新核对数量和规格，结论先标为待复核"]
    if pairs.get(source):
        notes.append(f"清册{pairs[source]}将变为清册单侧")
    return {"allow": True, "reason": "", "confirm": True, "notes": notes}


def _check_undo(items, view):
    edits = [e for e in view.get("edits") or [] if not e.get("undone")]
    if not edits:
        return _refuse("没有可撤销的修改")
    wanted = str((items[0] if items else {}).get("edit_id") or "")
    if wanted and wanted != edits[-1]["id"]:
        return _refuse(f"只能撤销最近一次修改（{edits[-1]['id']}），请先撤销它")
    return {"allow": True, "reason": "", "confirm": False, "notes": []}


def check_edit(op, view):
    kind = str((op or {}).get("type") or "")
    items = [x for x in (op or {}).get("items") or [] if isinstance(x, dict)]
    rows = {_key(r): r for r in (view or {}).get("rows") or []}
    if kind == "undo":
        return _check_undo(items, view)
    if not items:
        return _refuse("没有需要修改的条目")
    if kind == "set_verdict":
        return _check_verdicts(items, rows)
    if kind == "confirm":
        return _check_confirms(items, rows)
    if kind == "change_pair":
        return _check_pair(items, view, rows)
    return _refuse(f"未知的修改类型：{kind}")
