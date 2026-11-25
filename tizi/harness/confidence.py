# -*- coding: utf-8 -*-
"""确定性校准置信度计算器（禁止裸用 VLM 自报分）。"""
from __future__ import annotations


def calibrate_confidence(
    *,
    source: str,
    chk_corroborated: bool = False,
    model_self_conf: float = 0.0,
    has_conflict: bool = False,
    human_confirmed: bool = False,
) -> tuple[float, str]:
    """返回 (calibrated_conf, status)。

    严格遵循方案 §B.2：
    - 人工确认：1.00 / confirmed
    - 存在冲突：<= 0.55 / conflict
    - T2 确定性直取 + CHK 通过：0.98~1.00 / confirmed
    - VLM 局部重读 + CHK 数值收敛印证：0.88~0.93 / vlm_verified
    - VLM 局部重读但无独立交叉印证：<= 0.78 / vlm_pending（必须进 N7 人工审核）
    """
    if human_confirmed:
        return 1.0, "confirmed"
    if has_conflict:
        return 0.5, "conflict"

    src = (source or "").lower()
    if src in ("deterministic", "extract", "grid", "std_lib_lookup", "derive"):
        return (0.99 if chk_corroborated else 0.96), "confirmed"

    if src in ("vlm", "vlm_visual_read", "vlm_learned"):
        clamped = max(0.0, min(1.0, float(model_self_conf or 0.7)))
        if chk_corroborated:
            score = round(0.88 + 0.05 * clamped, 3)
            return min(0.93, score), "vlm_verified"
        score = round(min(0.78, 0.55 + 0.23 * clamped), 3)
        return score, "vlm_pending"

    return 0.65, "pending"
