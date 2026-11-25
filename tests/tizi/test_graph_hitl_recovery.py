# -*- coding: utf-8 -*-
"""L3 LangGraph 全链路图编排测试：N4->N5->N6 视觉重访闭环 + Send 扇出 + N7 HITL 跨重启恢复 + 长期记忆沉淀。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from tizi.harness.gateway import VlmGateway
from tizi.harness.graph import TiziGraphRunner
from tizi.harness.memory import EnterpriseMemory
from tizi.harness.store import ProjectTiziStore
from tizi.provenance import make_prov


class _MockPipeline:
    """轻量可注入 Pipeline，用于在隔离沙箱中精准测试 LangGraph 状态图、视觉回边与跨重启恢复。"""

    def __init__(self, store: ProjectTiziStore):
        self.store = store
        self.workbook_checks = []
        self.step_calls = []

    def step0_discover(self, folder):
        self.step_calls.append("step0")

    def step1_carrier(self, folder):
        self.step_calls.append("step1")

    def step2_parse(self):
        self.step_calls.append("step2")

    def step3_mapping(self):
        self.step_calls.append("step3")

    def step4_rules(self):
        self.step_calls.append("step4")

    def step5_validate(self):
        self.step_calls.append("step5")
        self.recompute_workbook_checks()

    def recompute_workbook_checks(self):
        fdn_rows = (self.store.get_sheet("基础库") or {}).get("rows") or []
        filled = sum(1 for r in fdn_rows if (r.get("out") or {}).get("基础.砼量(m³)") not in (None, ""))
        self.workbook_checks = [{
            "code": "CHK-08",
            "name": "基础总砼量守恒",
            "actual": float(filled),
            "expect": float(len(fdn_rows)),
            "diff": float(filled - len(fdn_rows)),
            "rate": 0.0 if filled == len(fdn_rows) else 1.0,
            "tolerance": 0.05,
            "unit": "m³",
            "ok": filled == len(fdn_rows),
            "n": len(fdn_rows),
            "note": f"基础库已填 {filled}/{len(fdn_rows)}",
        }]


class LangGraphHitlAndRecoveryTests(unittest.TestCase):
    def test_revisit_loop_send_fanout_and_cross_restart_hitl_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            drawings_dir = tmp_path / "drawings"
            drawings_dir.mkdir()
            ws_dir = tmp_path / "sessions" / "sess_graph_01"
            mem_dir = tmp_path / "sessions" / "enterprise_memory"

            # 构造 1 份含 WZ1055 与 WK1065 型号线索的基础详图 PDF（触发 N2 visual_index + N5 Send 2 路并行扇出）
            fdn_pdf = drawings_dir / "非标基础详图.pdf"
            doc = pymupdf.open()
            pg1 = doc.new_page(width=842, height=595)
            pg1.insert_text((100, 100), "WZ1055 掏挖基础详图 材料表 混凝土 16.5m3 HRB400 1420kg HPB300 210kg", fontsize=11, fontname="china-s")
            pg2 = doc.new_page(width=842, height=595)
            pg2.insert_text((100, 100), "WK1065 掏挖基础详图 材料表 混凝土 18.2m3 HRB400 1650kg HPB300 240kg", fontsize=11, fontname="china-s")
            doc.save(str(fdn_pdf))
            doc.close()

            store = ProjectTiziStore()
            store.set_sheet("基础明细表", [
                {"out": {"线路段名称": "主干段", "杆塔号": "NA1", "基数": 1, "基础型号.A腿": "WZ1055", "基础型号.B腿": "WZ1055", "基础型号.C腿": "WZ1055", "基础型号.D腿": "WZ1055"}, "prov": {}},
                {"out": {"线路段名称": "主干段", "杆塔号": "NA2", "基数": 1, "基础型号.A腿": "WK1065", "基础型号.B腿": "WK1065", "基础型号.C腿": "WK1065", "基础型号.D腿": "WK1065"}, "prov": {}},
            ])
            store.set_sheet("基础库", [
                {
                    "out": {"基础型号": "WZ1055", "基础类型": "掏挖基础", "基础.砼量(m³)": None, "基础钢筋量（kg）.HRB400": None, "基础钢筋量（kg）.HPB300": None},
                    "prov": {"基础.砼量(m³)": make_prov("extract", conf=0.0, status="pending")},
                },
                {
                    "out": {"基础型号": "WK1065", "基础类型": "掏挖基础", "基础.砼量(m³)": None, "基础钢筋量（kg）.HRB400": None, "基础钢筋量（kg）.HPB300": None},
                    "prov": {"基础.砼量(m³)": make_prov("extract", conf=0.0, status="pending")},
                },
            ])
            pipe = _MockPipeline(store)

            def mock_vlm(prompt_ver, img_paths, user_prompt):
                if "WZ1055" in user_prompt:
                    return {
                        "entity_id": "WZ1055",
                        "proposals": [
                            {"field_name": "基础.砼量(m³)", "raw_value": "16.5", "raw_unit": "m³", "raw_quote": "16.5m3", "bbox_in_crop": [0.1, 0.1, 0.4, 0.3], "model_self_conf": 0.91, "reasoning": "材料表砼量"},
                            {"field_name": "基础钢筋量（kg）.HRB400", "raw_value": "1.42", "raw_unit": "t", "raw_quote": "1.42t", "bbox_in_crop": [0.1, 0.3, 0.4, 0.5], "model_self_conf": 0.9, "reasoning": "材料表钢筋(验证确定性t->kg换算)"},
                            {"field_name": "基础钢筋量（kg）.HPB300", "raw_value": "210", "raw_unit": "kg", "raw_quote": "210kg", "bbox_in_crop": [0.1, 0.5, 0.4, 0.7], "model_self_conf": 0.9, "reasoning": "材料表光圆钢筋"},
                        ],
                        "need_expand_crop": False,
                        "expand_direction": "none",
                    }
                return {
                    "entity_id": "WK1065",
                    "proposals": [
                        {"field_name": "基础.砼量(m³)", "raw_value": "18.2", "raw_unit": "m³", "raw_quote": "18.2m3", "bbox_in_crop": [0.1, 0.1, 0.4, 0.3], "model_self_conf": 0.93, "reasoning": "材料表砼量"},
                        {"field_name": "基础钢筋量（kg）.HRB400", "raw_value": "1650", "raw_unit": "kg", "raw_quote": "1650kg", "bbox_in_crop": [0.1, 0.3, 0.4, 0.5], "model_self_conf": 0.92, "reasoning": "材料表钢筋"},
                        {"field_name": "基础钢筋量（kg）.HPB300", "raw_value": "240", "raw_unit": "kg", "raw_quote": "240kg", "bbox_in_crop": [0.1, 0.5, 0.4, 0.7], "model_self_conf": 0.9, "reasoning": "材料表光圆钢筋"},
                    ],
                    "need_expand_crop": False,
                    "expand_direction": "none",
                }

            events_emitted: list[str] = []
            runner1 = TiziGraphRunner(
                workspace_dir=ws_dir,
                folder=str(drawings_dir),
                session_id="sess_graph_01",
                pipeline=pipe,
                store=store,
                gateway=VlmGateway(mock_handler=mock_vlm),
                memory=EnterpriseMemory(mem_dir),
                emit_cb=lambda k, p: events_emitted.append(k),
            )
            try:
                snap1 = runner1.start(max_revisits=2, max_vlm_crops=10, hitl_required=True)
                ckpts = runner1.list_checkpoints()
            finally:
                runner1.close()
                del runner1

            # 1) 验证 N4->N5->N6 Send 并行重访补全了 WZ1055 与 WK1065，且 t->kg 换算 1.42t == 1420kg
            self.assertEqual(snap1["status"], "paused_hitl")
            self.assertIsNotNone(snap1["interrupt"])
            self.assertEqual(snap1["revisit_round"], 1)
            self.assertEqual(snap1["tried_crops"], 2)
            self.assertIn("vlm_patch_applied", events_emitted)
            self.assertIn("hitl_interrupt", events_emitted)

            fdn_rows = store.get_sheet("基础库")["rows"]
            self.assertEqual(fdn_rows[0]["out"]["基础.砼量(m³)"], 16.5)
            self.assertEqual(fdn_rows[0]["out"]["基础钢筋量（kg）.HRB400"], 1420)
            self.assertEqual(fdn_rows[1]["out"]["基础.砼量(m³)"], 18.2)
            # 验证 DAG 联动自动刷新了《基础明细表》四腿参考砼量（16.5 * 4 = 66.0）
            det_rows = store.get_sheet("基础明细表")["rows"]
            self.assertEqual(det_rows[0]["out"]["基础总砼量(参考m³)"], 66.0)

            # 验证检查点历史可查询
            self.assertTrue(len(ckpts) >= 4)

            # 2) 模拟 server.py 进程强杀重启：仅从磁盘 09_checkpoints/langgraph.sqlite + 08_store/workbook_v2.json 恢复
            runner2 = TiziGraphRunner(
                workspace_dir=ws_dir,
                folder=str(drawings_dir),
                session_id="sess_graph_01",
                memory=EnterpriseMemory(mem_dir),
            )
            try:
                # 首次不确认 VLM P1 项直接尝试放行 -> N8 门禁必须拦截并退回 N7 paused_hitl！
                snap_blocked = runner2.resume(decisions=[], approve_export=True)
                self.assertEqual(snap_blocked["status"], "paused_hitl")

                # 人工审核确认全部 6 项 VLM 补全值并勾选沉淀至企业长期记忆 -> N8 放行 -> N9 双轨导出
                decisions = []
                for r_idx in (0, 1):
                    for col in ("基础.砼量(m³)", "基础钢筋量（kg）.HRB400", "基础钢筋量（kg）.HPB300"):
                        decisions.append({"sheet": "基础库", "row_idx": r_idx, "col_key": col, "action": "confirm"})

                snap_done = runner2.resume(decisions=decisions, approve_export=True, promote_memory=True)
                self.assertEqual(snap_done["status"], "ready")
                self.assertIn("tizi_standard.xlsx", snap_done["export_files"])
                self.assertIn("tizi_audit.xlsx", snap_done["export_files"])
                self.assertTrue((ws_dir / "11_exports" / "tizi_standard.xlsx").is_file())
                self.assertTrue((ws_dir / "07_hitl" / "human_decisions.jsonl").is_file())

                # 4) 验证经 HITL 确认的基础库已沉淀至企业长期记忆
                promoted_lib = EnterpriseMemory(mem_dir).load_foundation_lib()
                self.assertIn("WZ1055", promoted_lib)
                self.assertEqual(promoted_lib["WZ1055"]["concrete_m3"], 16.5)
                self.assertIn("WK1065", promoted_lib)
            finally:
                runner2.close()


if __name__ == "__main__":
    unittest.main()
