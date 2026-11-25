# -*- coding: utf-8 -*-
"""HITL 人工审核队列、追加式审计决策流与导出门禁契约。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    priority: Literal["P0", "P1", "P2"]
    sheet: str
    row_idx: Optional[int] = None
    col_key: str
    entity_id: str = ""
    current_value: Any = None
    status: str = "pending"
    calibrated_conf: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    crop_uri: Optional[str] = None
    evidence_uri: Optional[str] = None
    conflict_candidates: list[dict[str, Any]] = Field(default_factory=list)


class HumanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = ""
    operator: str = "engineer"
    ts: str = ""
    sheet: str
    row_idx: int = Field(ge=0)
    col_key: str
    action: Literal["confirm", "edit", "drawing_select", "flag_pending", "revert", "approve_export"]
    old_val: Any = None
    new_val: Any = None
    note: str = ""
    promote_to_memory: bool = False


class ExportGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    p0_blocking: list[ReviewQueueItem] = Field(default_factory=list)
    p1_unconfirmed: list[ReviewQueueItem] = Field(default_factory=list)
    message: str = ""
