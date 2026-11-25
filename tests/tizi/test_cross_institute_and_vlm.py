# -*- coding: utf-8 -*-
"""跨设计院表头学习（5 行抽样值域自检门）+ 企业长期记忆缓存 + VLM 截断自外扩测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from tizi.harness.gateway import VlmGateway
from tizi.harness.memory import EnterpriseMemory
from tizi.harness.workspace import TiziWorkspace
from tizi.schemas.base import ErrorCode
from tizi.schemas.layout import LayoutProfile
from tizi.tools.vlm_reader import vlm_visual_read
from tizi.tools.vlm_schema import run_sample_sanity_check, vlm_learn_layout_schema


def _make_grid_table_pdf(pdf_path: Path, headers: list[str], rows: list[list[str]]) -> None:
    """用 PyMuPDF 构造带真实矢量框线的异院表格 PDF。"""
    doc = pymupdf.open()
    pg = doc.new_page(width=700, height=500)
    x0, y0 = 40.0, 50.0
    col_w = 120.0
    row_h = 28.0
    all_rows = [headers] + rows
    ncols = len(headers)
    nrows = len(all_rows)

    for r in range(nrows + 1):
        y = y0 + r * row_h
        pg.draw_line((x0, y), (x0 + ncols * col_w, y))
    for c in range(ncols + 1):
        x = x0 + c * col_w
        pg.draw_line((x, y0), (x, y0 + nrows * row_h))

    for ri, row in enumerate(all_rows):
        for ci, val in enumerate(row):
            pg.insert_text((x0 + ci * col_w + 6, y0 + ri * row_h + 18), str(val), fontsize=10, fontname="china-s")
    doc.save(str(pdf_path))
    doc.close()


class CrossInstituteAndVlmToolsTests(unittest.TestCase):
    def test_5row_sanity_gate_blocks_swapped_columns(self):
        """验证高杠杆防翻车风控：VLM 把塔号和塔重列映射反了 -> 5 行值域自检必须拦截。"""
        bad_profile = LayoutProfile(
            doc_id="other_inst",
            target_sheet="杆塔明细表",
            header_fingerprint="fp123",
            source="vlm_learned",
            column_mapping={0: "杆塔.全重(t)", 1: "杆塔号", 2: "呼称高"},
            tower_id_regex=r"^J\d+$",
        )
        sample_rows = [
            ["J1", "12.5", "30"],
            ["J2", "14.2", "33"],
            ["J3", "11.8", "27"],
        ]
        rep = run_sample_sanity_check(bad_profile, sample_rows)
        self.assertFalse(rep.passed)
        self.assertTrue(len(rep.violations) >= 2)

        good_profile = bad_profile.model_copy(
            update={"column_mapping": {0: "杆塔号", 1: "杆塔.全重(t)", 2: "呼称高"}}
        )
        rep_ok = run_sample_sanity_check(good_profile, sample_rows)
        self.assertTrue(rep_ok.passed, f"合法列映射不应报错：{rep_ok.violations}")

    def test_vlm_schema_learning_then_enterprise_cache_zero_token_second_run(self):
        """异院首跑 VLM 学习 1 次通过 5 行自检 -> 人工确认沉淀 -> 同院二跑零 VLM 调用直接命中缓存。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = TiziWorkspace(Path(tmp) / "sess1", session_id="sess1")
            mem = EnterpriseMemory(Path(tmp) / "enterprise_memory")
            pdf_path = Path(tmp) / "new_institute_tower.pdf"
            _make_grid_table_pdf(
                pdf_path,
                headers=["杆号", "呼高(m)", "单基耗钢(kg)", "塔型"],
                rows=[
                    ["J1", "30", "12500", "2E2-ZM1"],
                    ["J2", "33", "14200", "2E2-ZM2"],
                    ["J3", "27", "11800", "2E2-J1"],
                    ["J4", "36", "15600", "2E2-ZM1"],
                    ["J5", "30", "13100", "2E2-ZM1"],
                ],
            )

            call_count = {"n": 0}

            def mock_vlm(prompt_ver, img_paths, user_prompt):
                call_count["n"] += 1
                self.assertEqual(prompt_ver, "vlm_layout_schema_v1")
                return {
                    "institute_hint": "某省电院标准版式",
                    "column_mapping": {"0": "杆塔号", "1": "呼称高", "2": "杆塔.全重(t)", "3": "杆塔型号"},
                    "unit_overrides": {"杆塔.全重(t)": "kg"},
                    "tower_id_regex": r"^J\d+$",
                    "confidence": 0.94,
                }

            gw = VlmGateway(mock_handler=mock_vlm)
            r1 = vlm_learn_layout_schema(
                workspace=ws,
                pdf_path=str(pdf_path),
                doc_id="inst_b_struct",
                target_sheet="杆塔明细表",
                page=0,
                gateway=gw,
                memory=mem,
            )
            self.assertTrue(r1.ok, r1.summary)
            self.assertEqual(call_count["n"], 1)
            self.assertTrue(r1.data["sanity_passed"])
            self.assertEqual(r1.data["unit_overrides"]["杆塔.全重(t)"], "kg")

            # 未经人工确认禁止沉淀企业记忆
            prof = LayoutProfile.model_validate(r1.data)
            with self.assertRaises(PermissionError):
                mem.promote_layout_profile(prof, human_confirmed=False)

            # 经 HITL 人工确认沉淀
            mem.promote_layout_profile(prof, human_confirmed=True)

            # 同院二跑：直接命中 enterprise_cache，VLM 调用次数保持 1（零新增调用）
            r2 = vlm_learn_layout_schema(
                workspace=ws,
                pdf_path=str(pdf_path),
                doc_id="inst_b_struct_2",
                target_sheet="杆塔明细表",
                page=0,
                gateway=gw,
                memory=mem,
            )
            self.assertTrue(r2.ok)
            self.assertEqual(r2.data["source"], "enterprise_cache")
            self.assertEqual(call_count["n"], 1)

    def test_vlm_visual_read_auto_expand_crop_on_truncation(self):
        """验证切框截断自外扩机制：首次返回 need_expand_crop=True -> 自动向右外扩 +20% 重裁并成功读数。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = TiziWorkspace(Path(tmp) / "sess_crop", session_id="sess_crop")
            pdf_path = Path(tmp) / "fdn_wz1055.pdf"
            doc = pymupdf.open()
            pg = doc.new_page(width=842, height=595)
            pg.insert_text((450, 120), "WZ1055 基础材料表: C25砼 14.8m3 HRB400 1280kg", fontsize=12, fontname="china-s")
            doc.save(str(pdf_path))
            doc.close()

            crops_seen: list[str] = []

            def mock_vlm(prompt_ver, img_paths, user_prompt):
                crops_seen.append(img_paths[0])
                if len(crops_seen) == 1:
                    return {
                        "entity_id": "WZ1055",
                        "proposals": [],
                        "need_expand_crop": True,
                        "expand_direction": "right",
                    }
                return {
                    "entity_id": "WZ1055",
                    "proposals": [
                        {
                            "field_name": "基础.砼量(m³)",
                            "raw_value": "14.8",
                            "raw_unit": "m³",
                            "raw_quote": "C25砼 14.8m3",
                            "bbox_in_crop": [0.1, 0.2, 0.5, 0.4],
                            "model_self_conf": 0.92,
                            "reasoning": "外扩后完整可见砼量栏",
                        }
                    ],
                    "need_expand_crop": False,
                    "expand_direction": "none",
                }

            gw = VlmGateway(mock_handler=mock_vlm)
            res = vlm_visual_read(
                workspace=ws,
                entity_id="WZ1055",
                target_schema_id="foundation_detail",
                missing_fields=["基础.砼量(m³)"],
                pdf_path=str(pdf_path),
                doc_id="WZ1055",
                page=0,
                bbox=[420, 90, 560, 180],
                round_index=1,
                gateway=gw,
            )
            self.assertTrue(res.ok)
            self.assertEqual(res.code, ErrorCode.OK)
            self.assertEqual(len(crops_seen), 2, "应自动触发 1 次外扩重裁")
            self.assertNotEqual(crops_seen[0], crops_seen[1], "外扩后的裁切图文件应与首次不同")
            self.assertEqual(len(res.data["proposals"]), 1)
            self.assertLessEqual(res.data["calibrated_conf"], 0.78)
            self.assertTrue(ws.resolve_safe(res.artifact_uri).is_file())


if __name__ == "__main__":
    unittest.main()
