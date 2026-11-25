# -*- coding: utf-8 -*-
"""字段配对覆盖层。

机器结论（mapping.tower.yaml 或第 ⓪ 步探查出的 auto 映射）与人工判断分开存：
人工在界面上确认 / 改配 / 排除的结果写进 config/mapping.overrides.yaml，
每次载入配置或重新探查后套用到内存映射上。这样

  * 不用去改 YAML，界面上点一下就生效；
  * 一键就能回到机器的原始结论，不会把机器版改脏；
  * 重新探查生成新 mapping 时，人工判断不会被冲掉。

本模块只做纯函数（读写覆盖层、套用、算建议），不碰管线状态、不发事件。
"""
import os

import yaml

from . import discover

FILE = "mapping.overrides.yaml"


# ---------------------------------------------------------------- 读写
def load(config_dir):
    path = os.path.join(config_dir, FILE)
    try:
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        d = {}
    return {
        "pairs": {str(k): (v if isinstance(v, dict) else {"key": v, "label": ""})
                  for k, v in (d.get("pairs") or {}).items()},
        "excluded": [str(x) for x in (d.get("excluded") or [])],
        "confirmed": [str(x) for x in (d.get("confirmed") or [])],
    }


def save(config_dir, ov):
    path = os.path.join(config_dir, FILE)
    body = {
        "version": 1,
        "pairs": {k: {"key": v.get("key", ""), "label": v.get("label", "")}
                  for k, v in (ov.get("pairs") or {}).items()},
        "excluded": list(ov.get("excluded") or []),
        "confirmed": list(ov.get("confirmed") or []),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 套用
def resolve_column(layout, key, label=""):
    """覆盖层里存的 key 可能因重新探查变了，先按 key 找，再按表头文字找。"""
    cols = layout["columns"]
    for c in cols:
        if c["key"] == key:
            return c["key"]
    nl = discover._norm(label)
    if nl:
        for c in cols:
            if discover._norm(c.get("label") or c.get("name") or "") == nl:
                return c["key"]
    return None


def label_of(layout, key):
    for c in layout["columns"]:
        if c["key"] == key:
            return c.get("label") or c.get("name") or key
    return key


def apply(mapping, layout, ov, number_fields=()):
    """把覆盖层套到内存 mapping 上，返回 (mapping, stale)。

    stale 是「这次探查里找不到对应列」的人工配对，界面要显式提示，
    不能静默丢掉——否则人会以为配对还在。
    """
    import copy
    m = copy.deepcopy(mapping)
    cols = list(m.get("columns") or [])
    by_target = {c["target"]: c for c in cols}
    stale = []

    # 1) 人工改配 / 新配
    for tgt, rec in (ov.get("pairs") or {}).items():
        key = resolve_column(layout, rec.get("key", ""), rec.get("label", ""))
        if not key:
            stale.append({"target": tgt, "label": rec.get("label") or rec.get("key", "")})
            continue
        item = {"target": tgt, "from": key,
                "note": f"人工指定：{label_of(layout, key)}"}
        if tgt in number_fields:
            item["cast"] = "float"
        if tgt in by_target:
            cols[cols.index(by_target[tgt])] = item
            by_target[tgt] = item
        else:
            cols.append(item)
            by_target[tgt] = item

    # 2) 人工排除（标为「本工程无此数据」）
    excluded = set(ov.get("excluded") or [])
    if excluded:
        cols = [c for c in cols if c["target"] not in excluded]

    m["columns"] = cols
    return m, stale


# ---------------------------------------------------------------- 建议
def suggest(target, synonyms, layout, used_keys=(), limit=4):
    """给一个字段列出语义最接近的源表列，供人一眼确认。

    只作提示，绝不自动采纳；「第 7 列」这类没有语义的占位表头不参与。
    """
    out = []
    for c in layout["columns"]:
        label = c.get("label") or c.get("name") or ""
        if not label or label.startswith("第"):
            continue
        best = discover._score(label, target)
        reason = "同名"
        for syn in synonyms or []:
            sc = discover._score(label, syn)
            if sc > best:
                best, reason = sc, "同义词"
        # 0.7 以下基本只是「型号 / 高度」这类通用词撞上了，
        # 列出来反而会把人带偏。宁可少给建议，也不给看似合理的错建议。
        used = set(used_keys)
        if best >= 0.7:
            out.append({"key": c["key"], "label": label,
                        "score": round(best, 2), "reason": reason,
                        "weak": best < 0.9,
                        "used": c["key"] in used})
    out.sort(key=lambda x: (-x["score"], x["key"]))
    return out[:limit]
