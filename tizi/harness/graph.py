# -*- coding: utf-8 -*-
"""L3 执行编排层（tizi/harness/graph.py）：基于 LangGraph StateGraph + SqliteSaver 的 N1~N9 有向状态图。

核心拓扑（严格对齐方案 §A.2）：
  START -> N1(ingest) -> N2(route+schema) -> N3(det_extract) -> N3-V(vis_extract)
        -> N4(validate) --[revisitable & round<max & VLM configured]--> N5(plan_revisit)
        --[Send fan-out]--> N6(visual_revisit) --[回边重验]--> N4(validate)
        --[收敛或达上限]--> N7(hitl_gate: interrupt) -> N8(post_hitl)
        --[未过门禁]--> N7 / --[批准通过]--> N9(export) -> END
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .. import tools as _register_all_tools  # noqa: F401
from ..nodes import NodeContext
from ..nodes.n1_ingest import make_n1_ingest
from ..nodes.n2_route_schema import make_n2_route_schema
from ..nodes.n3_extract_det import make_n3_extract_det
from ..nodes.n3v_extract_vis import make_n3v_extract_vis
from ..nodes.n4_validate import make_n4_validate
from ..nodes.n5_plan_revisit import make_n5_plan_revisit
from ..nodes.n6_visual_revisit import make_n6_visual_revisit
from ..nodes.n7_hitl_gate import make_n7_hitl_gate
from ..nodes.n8_post_hitl import make_n8_post_hitl
from ..nodes.n9_export import make_n9_export
from ..schemas.state import GraphState
from .gateway import VlmGateway
from .memory import EnterpriseMemory
from .runtime import (
    END,
    START,
    Command,
    Send,
    StateGraph,
    extract_interrupt_payload,
    make_thread_config,
    open_sqlite_checkpointer,
)
from .store import ProjectTiziStore
from .tracer import TraceWriter
from .workspace import TiziWorkspace

_LOG = logging.getLogger("tizi.harness.graph")


class TiziGraphRunner:
    """图纸识别与提资智能体图运行时（支持跨重启 Checkpoint 恢复、Send 并行扇出与时间旅行重放）。"""

    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        folder: str,
        session_id: str = "",
        pipeline: Any = None,
        store: Optional[ProjectTiziStore] = None,
        gateway: Optional[VlmGateway] = None,
        memory: Optional[EnterpriseMemory] = None,
        cache_dir: Optional[str] = None,
        out_dir: Optional[str] = None,
        emit_cb: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ):
        self.workspace = TiziWorkspace(workspace_dir, session_id=session_id)
        self.session_id = self.workspace.session_id
        self.folder = str(folder or "")
        self.memory = memory or EnterpriseMemory(self.workspace.root.parent / "enterprise_memory")
        self.gateway = gateway or VlmGateway()
        self.tracer = TraceWriter(self.workspace)

        self.ctx = NodeContext(
            workspace=self.workspace,
            folder=self.folder,
            pipeline=pipeline,
            store=store,
            gateway=self.gateway,
            memory=self.memory,
            tracer=self.tracer,
            cache_dir=cache_dir or str(self.workspace.root.parent / "cache"),
            out_dir=out_dir or str(self.workspace.root / "out"),
            emit_cb=emit_cb,
        )

        self._checkpointer, self._conn = open_sqlite_checkpointer(self.workspace.checkpoint_db_path)
        self.graph = self._compile_graph()

    def _compile_graph(self):
        builder = StateGraph(GraphState)

        builder.add_node("n1_ingest", make_n1_ingest(self.ctx))
        builder.add_node("n2_route_schema", make_n2_route_schema(self.ctx))
        builder.add_node("n3_extract_det", make_n3_extract_det(self.ctx))
        builder.add_node("n3v_extract_vis", make_n3v_extract_vis(self.ctx))
        builder.add_node("n4_validate", make_n4_validate(self.ctx))
        builder.add_node("n5_plan_revisit", make_n5_plan_revisit(self.ctx))
        builder.add_node("n6_visual_revisit", make_n6_visual_revisit(self.ctx))
        builder.add_node("n7_hitl_gate", make_n7_hitl_gate(self.ctx))
        builder.add_node("n8_post_hitl", make_n8_post_hitl(self.ctx))
        builder.add_node("n9_export", make_n9_export(self.ctx))

        builder.add_edge(START, "n1_ingest")
        builder.add_edge("n1_ingest", "n2_route_schema")
        builder.add_edge("n2_route_schema", "n3_extract_det")
        builder.add_edge("n3_extract_det", "n3v_extract_vis")
        builder.add_edge("n3v_extract_vis", "n4_validate")

        def route_after_n4(state: GraphState) -> str:
            revisit_round = int(state.get("revisit_round") or 0)
            max_revisits = int(state.get("max_revisits") or 2)
            crops_used = len(state.get("tried_crops") or [])
            max_crops = int(state.get("max_vlm_crops") or 24)
            vlm_ready = bool(self.ctx.gateway and self.ctx.gateway.configured)
            if (
                not state.get("is_converged", True)
                and vlm_ready
                and revisit_round < max_revisits
                and crops_used < max_crops
            ):
                return "n5_plan_revisit"
            return "n7_hitl_gate"

        builder.add_conditional_edges(
            "n4_validate",
            route_after_n4,
            {"n5_plan_revisit": "n5_plan_revisit", "n7_hitl_gate": "n7_hitl_gate"},
        )

        def route_after_n5(state: GraphState):
            tasks = list(state.get("revisit_tasks") or [])
            if not tasks:
                return "n7_hitl_gate"
            return [Send("n6_visual_revisit", {"_send_task": t}) for t in tasks]

        builder.add_conditional_edges("n5_plan_revisit", route_after_n5, ["n6_visual_revisit", "n7_hitl_gate"])
        builder.add_edge("n6_visual_revisit", "n4_validate")
        builder.add_edge("n7_hitl_gate", "n8_post_hitl")

        def route_after_n8(state: GraphState) -> str:
            return "n9_export" if state.get("hitl_approved") else "n7_hitl_gate"

        builder.add_conditional_edges(
            "n8_post_hitl",
            route_after_n8,
            {"n9_export": "n9_export", "n7_hitl_gate": "n7_hitl_gate"},
        )
        builder.add_edge("n9_export", END)

        return builder.compile(checkpointer=self._checkpointer)

    def start(
        self,
        *,
        run_id: str = "",
        max_revisits: int = 2,
        max_vlm_crops: int = 24,
        max_vlm_schema_crops: int = 4,
        hitl_required: bool = True,
        on_stream: Optional[Callable[[str, Any], None]] = None,
    ) -> dict[str, Any]:
        """启动 N1~N9 图执行，直至完成或在 N7 HITL 闸门处持久化挂起。"""
        self.tracer.run_id = run_id or f"run-{self.session_id}"
        init_state: GraphState = {
            "session_id": self.session_id,
            "folder": self.folder,
            "workspace_dir": str(self.workspace.root),
            "run_id": self.tracer.run_id,
            "revisit_round": 0,
            "max_revisits": int(max_revisits),
            "vlm_crops_used": 0,
            "max_vlm_crops": int(max_vlm_crops),
            "vlm_schema_crops_used": 0,
            "max_vlm_schema_crops": int(max_vlm_schema_crops),
            "hitl_required": bool(hitl_required),
            "hitl_approved": False,
            "allow_draft_skip": False,
            "revisit_tasks": [],
            "revisit_results": [],
            "tried_crops": [],
            "status": "running",
        }
        cfg = make_thread_config(self.session_id)
        for mode, chunk in self.graph.stream(init_state, config=cfg, stream_mode=["updates", "custom"]):
            if on_stream is not None:
                on_stream(mode, chunk)
        return self.snapshot_summary()

    def resume(
        self,
        *,
        decisions: Optional[list[dict[str, Any]]] = None,
        approve_export: bool = True,
        promote_memory: bool = False,
        allow_draft_skip: bool = False,
        on_stream: Optional[Callable[[str, Any], None]] = None,
    ) -> dict[str, Any]:
        """从 N7 持久化 Checkpoint 注入人工审核决策并恢复执行至 N8 -> N9（支持跨进程重启）。"""
        cfg = make_thread_config(self.session_id)
        payload = {
            "decisions": list(decisions or []),
            "approve_export": bool(approve_export),
            "promote_memory": bool(promote_memory),
            "allow_draft_skip": bool(allow_draft_skip),
        }
        for mode, chunk in self.graph.stream(Command(resume=payload), config=cfg, stream_mode=["updates", "custom"]):
            if on_stream is not None:
                on_stream(mode, chunk)
        return self.snapshot_summary()

    def list_checkpoints(self, limit: int = 20) -> list[dict[str, Any]]:
        cfg = make_thread_config(self.session_id)
        out = []
        for snap in self.graph.get_state_history(cfg):
            c_cfg = (snap.config or {}).get("configurable") or {}
            vals = snap.values or {}
            out.append({
                "checkpoint_id": c_cfg.get("checkpoint_id", ""),
                "next": list(snap.next or ()),
                "current_node": vals.get("current_node", ""),
                "revisit_round": vals.get("revisit_round", 0),
                "status": vals.get("status", ""),
            })
            if len(out) >= limit:
                break
        return out

    def replay_from_checkpoint(self, checkpoint_id: str, on_stream: Optional[Callable[[str, Any], None]] = None) -> dict[str, Any]:
        """节点级时间旅行重放（例如 VLM 502 后直接从 N4 检查点重跑，零重算 N1~N3）。"""
        cfg = make_thread_config(self.session_id, checkpoint_id=checkpoint_id)
        for mode, chunk in self.graph.stream(None, config=cfg, stream_mode=["updates", "custom"]):
            if on_stream is not None:
                on_stream(mode, chunk)
        return self.snapshot_summary()

    def snapshot_summary(self) -> dict[str, Any]:
        cfg = make_thread_config(self.session_id)
        snap = self.graph.get_state(cfg)
        vals = dict(snap.values or {}) if snap else {}
        intr = extract_interrupt_payload(snap) if snap else None
        status = "paused_hitl" if intr is not None else str(vals.get("status") or ("ready" if not getattr(snap, "next", None) else "running"))
        return {
            "session_id": self.session_id,
            "status": status,
            "current_node": vals.get("current_node", ""),
            "next_nodes": list(getattr(snap, "next", ()) or ()),
            "interrupt": intr,
            "revisit_round": int(vals.get("revisit_round") or 0),
            "tried_crops": len(vals.get("tried_crops") or []),
            "revisit_results": list(vals.get("revisit_results") or []),
            "export_files": list(vals.get("export_files") or []),
            "review_queue_uri": vals.get("review_queue_uri", ""),
            "chk_report_uri": vals.get("chk_report_uri", ""),
            "layout_profiles_uri": vals.get("layout_profiles_uri", ""),
            "trace_summary": self.tracer.summarize(),
        }

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
