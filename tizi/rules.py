# -*- coding: utf-8 -*-
"""阶段四：规则补全。

图纸上没有直接写、但可以查或算出来的字段。
三类规则足够覆盖绝大多数场景：查表、区间插值、统一赋值。
"""
import csv
import os
import re


def _load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _norm(v, strip_prefix=""):
    s = str(v or "").strip().upper().replace(" ", "")
    if strip_prefix:
        s = s.replace(strip_prefix.upper(), "")
    return s


def _num(v):
    """把单元格值转成数值，转不了就返回 None。

    解析出来的值一律是字符串（"24"、"-0.9"），
    规则里做区间比较前必须先转，否则 float 和 str 没法比。
    转不动的不当成 0，直接判为缺失——宁可不填也不猜。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    # 「24.0m」「H=24」这类带单位的写法
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _set(row, tgt, val, prov):
    row["out"][tgt] = val
    row["prov"][tgt] = prov


def apply_rules(rows, cfg, project_root):
    """执行全部规则，返回统计信息。"""
    stats = []

    for rule in cfg.get("rules", []):
        tgt = rule["target"]
        kind = rule["type"]
        conf = float(rule.get("confidence", 0.95))
        note = rule.get("note", "")

        # ---- 统一赋值（可限定条件、只补空值）----
        if kind == "constant":
            where = rule.get("where") or {}
            only_empty = bool(rule.get("only_if_empty", False))
            ok = miss = 0
            for r in rows:
                if where and any(r["out"].get(k) != v for k, v in where.items()):
                    continue
                if only_empty and r["out"].get(tgt) is not None:
                    ok += 1
                    continue
                _set(r, tgt, rule["value"],
                     {"method": "constant", "source": "配置", "page": None, "bbox": None,
                      "conf": conf, "status": "confirmed", "note": note})
                ok += 1
            stats.append({"target": tgt, "type": kind, "ok": ok, "miss": miss})
            continue

        table = _load(os.path.join(project_root, rule["table"]))
        kcol = rule["key"]["column"]
        prefix = rule["key"].get("strip_prefix", "")
        kfield = rule["key"]["field"]
        index = {_norm(t[kcol], prefix): t for t in table}

        if kind == "lookup":
            ok = miss = 0
            for r in rows:
                k = _norm(r["out"].get(kfield), prefix)
                hit = index.get(k)
                if hit and hit.get(rule["value"]) not in (None, ""):
                    ok += 1
                    _set(r, tgt, hit[rule["value"]],
                         {"method": "lookup", "source": rule["table"], "page": None,
                          "bbox": None, "conf": conf, "status": "confirmed", "note": note})
                else:
                    miss += 1
                    _set(r, tgt, None,
                         {"method": "lookup", "source": rule["table"], "page": None,
                          "bbox": None, "conf": 0.0, "status": "pending",
                          "note": f"查表未命中：{k}"})
            stats.append({"target": tgt, "type": kind, "ok": ok, "miss": miss})

        elif kind == "interval":
            lo_k, lo_v = rule["from"]
            hi_k, hi_v = rule["to"]
            axis = rule["axis"]
            clamp = bool(rule.get("clamp", False))
            ok = miss = 0
            for r in rows:
                k = _norm(r["out"].get(kfield), prefix)
                hit = index.get(k)
                h_raw = r["out"].get(axis)
                h = _num(h_raw)
                if not hit or h is None:
                    miss += 1
                    why = f"无区间或缺 {axis}" if not hit else f"{axis} 非数值：{h_raw}"
                    _set(r, tgt, None,
                         {"method": "interval", "source": rule["table"], "page": None,
                          "bbox": None, "conf": 0.0, "status": "pending",
                          "note": why})
                    continue

                h1, h2 = float(hit[lo_k]), float(hit[hi_k])
                w1, w2 = float(hit[lo_v]), float(hit[hi_v])

                if h2 == h1:
                    w = w1
                elif not (h1 <= h <= h2) and not clamp:
                    miss += 1
                    _set(r, tgt, None,
                         {"method": "interval", "source": rule["table"], "page": None,
                          "bbox": None, "conf": 0.0, "status": "pending",
                          "note": f"{axis} {_fmt(h)} 超出区间 {_fmt(h1)}~{_fmt(h2)}，按规则不外推"})
                    continue
                else:
                    hh = min(max(h, h1), h2)
                    w = w1 + (w2 - w1) * (hh - h1) / (h2 - h1)

                ok += 1
                _set(r, tgt, round(w, 2),
                     {"method": "interval", "source": rule["table"], "page": None,
                      "bbox": None, "conf": conf, "status": "confirmed", "note": note})
            stats.append({"target": tgt, "type": kind, "ok": ok, "miss": miss})

    return stats
