# -*- coding: utf-8 -*-
"""平台物资价格库只读联动匹配器（Price Library Read-Only Hook）。

核心原则：
1. 严格只读访问平台 `STATE['prices']` 快照，绝不修改、不写回价格库；
2. 优先匹配规格型号完全一致且单位一致的条目，其次按名称+规格重合度匹配；
3. 匹配结果写入 `合同单价含税（元）` 时强制标 `method="price_ref"`、`status="pending"`（黄格待造价员确认），
   并记录价格库来源、期次与匹配分值至 `formula_trace`。
"""
import difflib
import re

from .provenance import make_prov


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_/（）()【】\[\]，,。:：]+", "", str(s or "")).upper()


def _canon_unit(u: str) -> str:
    s = str(u or "").strip().lower()
    m = {
        "t": "吨", "吨": "吨", "kg": "公斤", "千克": "公斤", "公斤": "公斤",
        "km": "千米", "千米": "千米", "公里": "千米",
        "m": "米", "米": "米", "m3": "立方米", "m³": "立方米", "立方米": "立方米", "方": "立方米",
        "套": "套", "串": "串", "片": "片", "个": "个", "只": "只", "支": "支", "付": "付", "副": "付", "根": "根", "块": "块",
    }
    return m.get(s, s)


def _score_match(mat_name: str, mat_spec: str, mat_unit: str, p_item: dict) -> float:
    p_unit = _canon_unit(p_item.get("unit") or "")
    m_unit = _canon_unit(mat_unit)
    if m_unit and p_unit and m_unit != p_unit:
        return 0.0

    ms = _norm(mat_spec)
    ps = _norm(p_item.get("spec") or "")
    mn = _norm(mat_name)
    pn = _norm(p_item.get("name") or "")

    if ms and ps and ms == ps:
        n_sim = difflib.SequenceMatcher(None, mn, pn).ratio() if (mn and pn) else 0.8
        return round(0.85 + 0.15 * n_sim, 3)

    if mn and pn:
        sim_n = difflib.SequenceMatcher(None, mn, pn).ratio()
        sim_s = difflib.SequenceMatcher(None, ms, ps).ratio() if (ms and ps) else (0.5 if not ms else 0.0)
        score = 0.55 * sim_n + 0.45 * sim_s
        return round(score, 3)
    return 0.0


def match_sheet_prices(store, sheet_name: str, prices_snapshot: list, min_score: float = 0.72):
    """只读匹配指定材料表的参考单价，返回匹配统计。"""
    if sheet_name not in ("电气设备材料表", "光缆设备材料表", "结构设备材料表"):
        raise ValueError(f"仅支持材料表匹配价格库，不支持：{sheet_name}")

    valid_prices = [
        p for p in (prices_snapshot or [])
        if isinstance(p, dict) and p.get("price") not in (None, "")
    ]
    sd = store.get_sheet(sheet_name)
    rows = sd.get("rows") or []
    matched = 0
    skipped = 0

    for r in rows:
        out = r.get("out") or {}
        prov = r.setdefault("prov", {})
        # 已有人工确认单价的格不覆盖
        if out.get("合同单价含税（元）") not in (None, "") and (prov.get("合同单价含税（元）") or {}).get("method") == "manual":
            skipped += 1
            continue

        mat_name = str(out.get("材料名称") or "")
        mat_spec = str(out.get("规格型号") or out.get("规格") or "")
        mat_unit = str(out.get("单位") or "")
        if not mat_name and not mat_spec:
            continue

        best_item = None
        best_sc = 0.0
        for p in valid_prices:
            sc = _score_match(mat_name, mat_spec, mat_unit, p)
            if sc > best_sc:
                best_sc = sc
                best_item = p

        if best_item and best_sc >= min_score:
            try:
                price_val = round(float(str(best_item["price"]).replace(",", "")), 2)
            except (TypeError, ValueError):
                continue
            out["合同单价含税（元）"] = price_val
            period = best_item.get("period") or best_item.get("version") or "价格库"
            p_name = best_item.get("name") or ""
            p_spec = best_item.get("spec") or ""
            prov["合同单价含税（元）"] = make_prov(
                "price_ref",
                conf=best_sc,
                status="pending",
                unit="元",
                machine_value=price_val,
                note=f"来自平台价格库参考价（匹配度 {int(best_sc * 100)}%，请造价员确认）",
                formula_trace=f"价格库[{period}] {p_name} {p_spec} → {price_val} 元/{best_item.get('unit') or mat_unit}",
                source=f"价格库:{period}",
            )
            matched += 1

    return {
        "sheet": sheet_name,
        "total_rows": len(rows),
        "matched": matched,
        "skipped": skipped,
        "price_pool_size": len(valid_prices),
    }
