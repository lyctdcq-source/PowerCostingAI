# -*- coding: utf-8 -*-
"""全局唯一强类型数据契约（Pydantic v2），零内部业务模块依赖。"""
from .base import ErrorCode, ProvenanceRef, SideEffectLevel, ToolResult
from .conflict import ConflictItem, ConflictReport, RevisitTask
from .hitl import ExportGateResult, HumanDecision, ReviewQueueItem
from .layout import HeaderFingerprint, LayoutProfile, SampleSanityReport
from .state import GraphState, merge_by_entity_id, merge_dict_shallow
from .vlm import VlmFieldProposal, VlmReadRequest, VlmReadResult

__all__ = [
    "ErrorCode",
    "SideEffectLevel",
    "ProvenanceRef",
    "ToolResult",
    "HeaderFingerprint",
    "SampleSanityReport",
    "LayoutProfile",
    "ConflictItem",
    "ConflictReport",
    "RevisitTask",
    "VlmFieldProposal",
    "VlmReadRequest",
    "VlmReadResult",
    "ReviewQueueItem",
    "HumanDecision",
    "ExportGateResult",
    "GraphState",
    "merge_by_entity_id",
    "merge_dict_shallow",
]
