# -*- coding: utf-8 -*-
"""GraphState 权威状态定义与声明式并发归并 Reducer（兼容 LangGraph 与自建运行时）。"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict


def merge_by_entity_id(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 entity_id / task_id 确定性归并并行 Send 子任务产出，防止并发竞态。"""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for seq in (left or [], right or []):
        for item in seq:
            key = str(item.get("entity_id") or item.get("task_id") or len(order))
            if key not in merged:
                order.append(key)
            merged[key] = dict(item)
    return [merged[k] for k in order]


def merge_dict_shallow(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    out = dict(left or {})
    if right:
        out.update(right)
    return out


class GraphState(TypedDict, total=False):
    session_id: str
    folder: str
    workspace_dir: str
    run_id: str
    steps_requested: list[str]

    # 工件指针（Artifact-by-Reference，大对象不入状态）
    catalog_uri: str
    visual_index_uri: str
    layout_profiles_uri: str
    enum_card_uri: str
    store_uri: str
    chk_report_uri: str
    review_queue_uri: str

    # 受控闭环与预算控制
    revisit_round: int
    max_revisits: int
    vlm_crops_used: int
    max_vlm_crops: int
    vlm_schema_crops_used: int
    max_vlm_schema_crops: int
    is_converged: bool

    # 并行扇出规划与归并结果
    revisit_tasks: list[dict[str, Any]]
    revisit_results: Annotated[list[dict[str, Any]], merge_by_entity_id]
    tried_crops: Annotated[list[dict[str, Any]], merge_by_entity_id]

    # HITL 审核与导出状态
    hitl_required: bool
    hitl_approved: bool
    allow_draft_skip: bool
    hitl_decisions: list[dict[str, Any]]
    export_files: list[str]
    export_warnings: list[str]
    promote_memory: bool

    # 状态摘要与错误
    status: str  # running | paused_hitl | ready | error
    current_node: str
    error: str
    metrics: Annotated[dict[str, Any], merge_dict_shallow]
