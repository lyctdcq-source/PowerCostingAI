# -*- coding: utf-8 -*-
"""阶段一：载体判定（基于 PageFeatureCache，杜绝全页 get_drawings 性能陷阱）。

使用页级 words / chars / ink_bytes（内容流字节）与最大图片像素判定载体类型，
并支持识别「混合型图纸」（例如防雷接地装置图：既有矢量线条又有参数表文字层）。
"""
from . import cache

PIPELINE = {
    "T0": "整页 OCR",
    "T1": "段落抽取 + 正则",
    "T2": "列边界 / 框线网格还原",
    "T3": "矢量图纸（局部框选 / 离线 OCR）",
    "T4": "区域裁切 + OCR",
    "T2+T3": "混合型图纸（文字参数表自动提取 + 矢量图框选）",
}


def probe(path, cache_dir=None):
    """返回每页平均的 chars / words / ink_kb / draws_pg（估算值，不调用昂贵 get_drawings）。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    pages = doc["pages"]
    n = max(len(pages), 1)
    chars = sum(p["chars"] for p in pages)
    words = sum(p["n_words"] for p in pages)
    ink_kb = sum(p["ink_bytes"] for p in pages) / 1024.0
    max_img = max((p["big_img_px"] for p in pages), default=0)
    # 估算矢量复杂度指标（每 KB 约对应若干路径，仅供展示与阈值判定）
    est_draws = round((ink_kb / n) * 2.0, 1)
    text_pages = sum(1 for p in pages if p["n_words"] >= 40)
    vector_pages = sum(1 for p in pages if p["ink_bytes"] >= 50 * 1024 and p["big_img_px"] < 1_000_000)
    return {
        "pages": len(pages),
        "chars_pg": round(chars / n, 1),
        "words_pg": round(words / n, 1),
        "draws_pg": est_draws,
        "ink_kb_pg": round(ink_kb / n, 1),
        "big_img_px": max_img,
        "text_pages": text_pages,
        "vector_pages": vector_pages,
    }


def classify(m):
    """页级/区域级混合载体判定。"""
    c = m.get("chars_pg") or 0
    w = m.get("words_pg") or 0
    d = m.get("draws_pg") or 0
    ink = m.get("ink_kb_pg") or 0
    big_img = m.get("big_img_px") or 0
    text_pg = m.get("text_pages") or 0
    vec_pg = m.get("vector_pages") or 0

    if w < 5 and c < 50:
        if big_img >= 1_000_000:
            return "T0"
        return "T3" if (d > 100 or ink >= 50) else "T0"
    if (d > 1000 or ink >= 500) and w < 60:
        return "T3"
    if text_pg >= 1 and vec_pg >= 1 and w >= 60 and ink >= 120:
        return "T2+T3"
    if w >= 60:
        return "T2"
    return "T1"


def inspect(path, cache_dir=None):
    m = probe(path, cache_dir=cache_dir)
    m["carrier"] = classify(m)
    m["pipeline"] = PIPELINE.get(m["carrier"], "列边界 / 框线网格还原")
    return m
