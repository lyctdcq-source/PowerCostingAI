# -*- coding: utf-8 -*-
"""多模态视觉精读强契约（严格原文输出 + 切框截断自外扩信号）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class VlmFieldProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    raw_value: str
    raw_unit: Optional[str] = None
    raw_quote: str = ""
    bbox_in_crop: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    model_self_conf: float = Field(default=0.7, ge=0.0, le=1.0)
    reasoning: str = ""


class VlmReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    crop_uris: list[str] = Field(min_length=1, max_length=3)
    target_schema_id: str
    constraint_hints: str = ""
    prior_conflict: str = ""
    prompt_version: str = "vlm_foundation_detail_v1"


class VlmReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    crop_uri: str
    proposals: list[VlmFieldProposal] = Field(default_factory=list)
    need_expand_crop: bool = False
    expand_direction: Literal["all", "left", "right", "top", "bottom", "none"] = "none"
    prompt_version: str = ""
    calibrated_conf: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_uri: Optional[str] = None
    token_usage: dict[str, int] = Field(default_factory=dict)
