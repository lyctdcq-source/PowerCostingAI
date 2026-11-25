# -*- coding: utf-8 -*-
"""阶段五：双源校验。

单份文件自洽不代表正确。用独立数据源对总量、条数做比对，
偏差超出容差即告警——这是提资能被打上"可信"的唯一依据。
"""
import re


def _num(v):
    """字符串转数值。解析出来的格子里都是 "24"、"-0.9" 这种文本，
    直接求和会把它们全跳过，导致校验永远显示 0。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group()) if m else None


def _match(row, where):
    for k, v in (where or {}).items():
        if row["out"].get(k) != v:
            return False
    return True


def run_checks(rows, cfg):
    results = []

    for c in cfg.get("crosschecks", []):
        kind = c["type"]
        expect = float(c["expect"])
        tol = float(c.get("tolerance", 0))
        unit = c.get("unit", "")

        if kind == "count":
            actual = float(len(rows))
            used = len(rows)
        elif kind == "sum":
            picked = [r for r in rows if _match(r, c.get("where"))]
            vals = [r["out"].get(c["field"]) for r in picked]
            nums = [x for x in (_num(v) for v in vals) if x is not None]
            actual = round(sum(nums), 2)
            used = len(nums)
        else:
            continue

        diff = round(actual - expect, 4)
        rate = abs(diff) / expect if expect else 0.0

        results.append({
            "name": c["name"],
            "actual": actual,
            "expect": expect,
            "diff": diff,
            "rate": rate,
            "tolerance": tol,
            "unit": unit,
            "ok": rate <= tol + 1e-9,
            "n": used,
        })

    return results
