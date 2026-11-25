# -*- coding: utf-8 -*-
"""Tool: vlm_visual_read — 局部高清图 Visual-First 读数（含截断自外扩 +20% 重裁与校准置信度）。"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import ValidationError

from ..harness.confidence import calibrate_confidence
from ..harness.gateway import VlmGateway
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..prompts import load_prompt
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult
from ..schemas.vlm import VlmFieldProposal, VlmReadResult
from .pdf_render import pdf_crop_render

_SCHEMA_TO_PROMPT = {
    "foundation_detail": "vlm_foundation_detail_v1",
    "tower_profile": "vlm_tower_profile_v1",
    "conflict_context": "vlm_tower_profile_v1",
    "generic_table": "vlm_foundation_detail_v1",
}


def _safe_entity_slug(entity_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(entity_id or "entity"))[:40]


@register_tool(
    "vlm_visual_read",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="05_vlm_evidence",
    description="对高清局部裁切图执行 Visual-First 结构化读数，支持切框截断自外扩 1 次并落盘证据",
)
def vlm_visual_read(
    *,
    workspace: Optional[TiziWorkspace] = None,
    entity_id: str,
    target_schema_id: str = "foundation_detail",
    missing_fields: Optional[list[str]] = None,
    constraint_hints: str = "",
    pdf_path: str = "",
    doc_id: str = "",
    page: int = 0,
    bbox: Optional[list[float]] = None,
    crop_abs_path: str = "",
    crop_uri: str = "",
    round_index: int = 1,
    gateway: Optional[VlmGateway] = None,
    tracer: Any = None,
) -> ToolResult[dict[str, Any]]:
    if gateway is None or not gateway.configured:
        return ToolResult(
            ok=False,
            code=ErrorCode.VLM_UNAVAILABLE,
            summary="VLM 未配置，跳过视觉重读并转交人工审核",
            recoverable=True,
        )

    prompt_ver = _SCHEMA_TO_PROMPT.get(target_schema_id, "vlm_foundation_detail_v1")
    sys_prompt, user_tmpl = load_prompt(prompt_ver)
    user_prompt = user_tmpl.format(
        entity_id=entity_id,
        missing_fields=", ".join(missing_fields or []),
        constraint_hints=constraint_hints or "严格按图纸原文输出数值与单位，禁止估算",
    )

    cur_crop_abs = crop_abs_path
    cur_crop_uri = crop_uri
    expand_level = 0
    expand_dir = "none"
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    parsed_result: Optional[VlmReadResult] = None

    # 最多尝试 2 次：首次读数 + 若模型报告 need_expand_crop=True 则按方向外扩 +20% 重裁 1 次
    for attempt in range(2):
        if not cur_crop_abs and pdf_path:
            cr = pdf_crop_render(
                workspace=workspace,
                pdf_path=pdf_path,
                doc_id=doc_id or entity_id,
                page=int(page),
                bbox=bbox,
                dpi=240,
                expand_level=expand_level,
                expand_direction=expand_dir if expand_level > 0 else "all",
            )
            if not cr.ok or not cr.data:
                return ToolResult(ok=False, code=cr.code, summary=f"裁切失败：{cr.summary}", recoverable=True)
            cur_crop_abs = cr.data["abs_path"]
            cur_crop_uri = cr.data["png_uri"]

        if not cur_crop_abs:
            return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary="缺少裁切图路径或 PDF 参数", recoverable=False)

        raw, usage = gateway.call_vision_json(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            image_paths=[cur_crop_abs],
            prompt_version=prompt_ver,
        )
        for k in total_usage:
            total_usage[k] += int((usage or {}).get(k) or 0)

        # Pydantic 强 Schema 校验（失败时附带错误自修复 1 次）
        proposals: list[VlmFieldProposal] = []
        raw_props = raw.get("proposals") if isinstance(raw, dict) else []
        for p in raw_props or []:
            try:
                proposals.append(VlmFieldProposal.model_validate(p))
            except ValidationError:
                continue

        need_expand = bool((raw or {}).get("need_expand_crop", False))
        exp_dir = str((raw or {}).get("expand_direction") or "all").lower()
        if exp_dir not in ("all", "left", "right", "top", "bottom", "none"):
            exp_dir = "all"

        avg_self_conf = (
            sum(p.model_self_conf for p in proposals) / len(proposals) if proposals else 0.0
        )
        cal_conf, _ = calibrate_confidence(source="vlm_visual_read", chk_corroborated=False, model_self_conf=avg_self_conf)

        parsed_result = VlmReadResult(
            entity_id=entity_id,
            crop_uri=cur_crop_uri,
            proposals=proposals,
            need_expand_crop=need_expand,
            expand_direction=exp_dir if need_expand else "none",
            prompt_version=prompt_ver,
            calibrated_conf=cal_conf,
            token_usage=total_usage,
        )

        if tracer is not None:
            tracer.log_vlm(
                entity_id=entity_id,
                prompt_version=prompt_ver,
                crop_uri=cur_crop_uri,
                usage=usage,
                proposals=len(proposals),
                need_expand_crop=need_expand,
                expand_level=expand_level,
            )

        # 若触发截断信号且有原始 PDF 可重裁，则外扩 +20% 重读一次
        if need_expand and attempt == 0 and pdf_path:
            expand_level = 1
            expand_dir = exp_dir if exp_dir != "none" else "all"
            cur_crop_abs = ""
            continue
        break

    assert parsed_result is not None
    ev_uri = None
    if workspace is not None:
        slug = _safe_entity_slug(entity_id)
        ev_uri = workspace.write_json(f"05_vlm_evidence/rev{int(round_index)}_{slug}.json", parsed_result.model_dump())
        parsed_result = parsed_result.model_copy(update={"evidence_uri": ev_uri})

    return ToolResult(
        ok=True,
        code=ErrorCode.CROP_TRUNCATED if (parsed_result.need_expand_crop and not parsed_result.proposals) else ErrorCode.OK,
        data=parsed_result.model_dump(),
        artifact_uri=ev_uri,
        summary=f"VLM 视觉读数 {entity_id}：产出 {len(parsed_result.proposals)} 项提议（校准置信度 {parsed_result.calibrated_conf:.2f}）",
    )
