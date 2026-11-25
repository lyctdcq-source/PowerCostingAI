# -*- coding: utf-8 -*-
"""L1 静态分层红线 + L2 工具契约 + D-01/D-04/D-08 缺陷修复回归测试。"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

import pymupdf

from tizi.extractors import crossing as crossing_ext
from tizi.extractors import rho as rho_ext
from tizi.harness.confidence import calibrate_confidence
from tizi.harness.gateway import DesensitizeGateway
from tizi.harness.registry import get_default_registry
from tizi.harness.store import ProjectTiziStore, evaluate_export_gate, patch_store_cell
from tizi.harness.workspace import TiziWorkspace, WorkspacePathError
from tizi.provenance import make_prov
from tizi.schemas.base import ErrorCode
from tizi.segments import LineSegmentRegistry
from tizi import tools as _tools  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]


class HarnessContractsAndGuardsTests(unittest.TestCase):
    def test_l1_static_import_boundaries(self):
        """验证 §F.2 包依赖红线：schemas 零内部依赖；extractors 严禁导入 langgraph 或 gateway。"""
        schemas_dir = REPO_ROOT / "tizi" / "schemas"
        for pyf in schemas_dir.glob("*.py"):
            tree = ast.parse(pyf.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level >= 2:
                    self.fail(f"{pyf.name} 违规向上导入父包：{node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith("tizi.harness"), f"{pyf.name} 违规依赖 harness")
                        self.assertFalse(alias.name.startswith("langgraph"), f"{pyf.name} 违规依赖 langgraph")

        ext_dir = REPO_ROOT / "tizi" / "extractors"
        for pyf in ext_dir.glob("*.py"):
            src = pyf.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = getattr(node, "module", "") or ""
                    names = [a.name for a in getattr(node, "names", [])]
                    for target in [mod] + names:
                        self.assertFalse("langgraph" in str(target), f"{pyf.name} 严禁导入 langgraph")
                        self.assertFalse("gateway" in str(target), f"{pyf.name} 严禁导入 gateway")

    def test_registry_has_all_12_tools_and_externalizes_large_output(self):
        reg = get_default_registry()
        names = {t["name"] for t in reg.list_tools()}
        expected = {
            "pdf_probe_catalog", "carrier_classify", "pdf_extract_grid",
            "cv_layout_segment", "pdf_crop_render", "local_ocr_region",
            "vlm_learn_layout_schema", "vlm_visual_read", "constraint_validator",
            "workbook_store_patch", "artifact_fs_io", "price_library_match", "tizi_excel_export",
        }
        self.assertTrue(expected.issubset(names), f"缺少已注册工具：{expected - names}")

        with tempfile.TemporaryDirectory() as tmp:
            ws = TiziWorkspace(tmp, session_id="s-ext")
            big_payload = {"rows": [{"idx": i, "txt": "X" * 120} for i in range(40)]}
            inline, uri, summary = ws.externalize_if_large("03_cv_segments", "big", big_payload)
            self.assertIsNone(inline)
            self.assertIsNotNone(uri)
            self.assertIn("03_cv_segments/", uri)
            self.assertIn("40 items", summary)
            sl = ws.read_slice(uri, offset=5, limit=3, key="rows")
            self.assertEqual(sl["total"], 40)
            self.assertEqual(len(sl["items"]), 3)
            self.assertEqual(sl["items"][0]["idx"], 5)

            with self.assertRaises(WorkspacePathError):
                ws.resolve_safe("../../etc/passwd")

    def test_desensitize_gateway_and_calibrated_confidence(self):
        gw = DesensitizeGateway(
            doc_aliases={r"E:\secret\龙泉山结构册.pdf": "DOC_STRUCT"},
            project_names=["220kV龙泉山输变电工程"],
        )
        masked = gw.mask_text(r"请核对 220kV龙泉山输变电工程 在 E:\secret\龙泉山结构册.pdf 和 C:\Users\a\b.pdf 的数据")
        self.assertNotIn("龙泉山", masked)
        self.assertNotIn(r"C:\Users", masked)
        self.assertIn("DOC_STRUCT", masked)
        self.assertIn("PROJECT_1", masked)
        unmasked = gw.unmask_text(masked)
        self.assertIn("220kV龙泉山输变电工程", unmasked)

        # 未经交叉印证的 VLM 自报 0.99 必须被校准钳制 <= 0.78 且状态 vlm_pending
        c_unv, st_unv = calibrate_confidence(source="vlm_visual_read", chk_corroborated=False, model_self_conf=0.99)
        self.assertLessEqual(c_unv, 0.78)
        self.assertEqual(st_unv, "vlm_pending")

        c_ver, st_ver = calibrate_confidence(source="vlm_visual_read", chk_corroborated=True, model_self_conf=0.95)
        self.assertGreaterEqual(c_ver, 0.88)
        self.assertLessEqual(c_ver, 0.93)
        self.assertEqual(st_ver, "vlm_verified")

    def test_d08_same_tower_multi_crossing_and_custom_prefix(self):
        """验证 D-08 修复：同塔多跨越不再静默丢弃，且支持非 N[A-F] 异院塔号前缀（如 J18-J19）。"""
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "cross.pdf"
            doc = pymupdf.open()
            pg = doc.new_page(width=595, height=842)
            lines = [
                "10.1 重要交叉跨越",
                "10.1.1 跨越 220kV 电力线（带电封网跨越）：J18-J19 档跨越 220kV 东山线单回",
                "10.1.2 跨越高速公路：J18-J19 档上跨 G50 高速公路",
            ]
            y = 80
            for ln in lines:
                pg.insert_text((50, y), ln, fontsize=11, fontname="china-s")
                y += 28
            doc.save(str(pdf_path))
            doc.close()

            recs = crossing_ext.parse_crossing_pdf(str(pdf_path), rel_path="cross.pdf")
            self.assertIn("J19", recs)
            self.assertEqual(recs["J19"]["count"], 2)
            self.assertEqual(len(recs["J19"]["items"]), 2)
            cells = crossing_ext.crossing_to_cells(recs["J19"])
            self.assertEqual(cells["交叉跨越.跨越数量"][0], 2)
            self.assertEqual(len(cells["交叉跨越.障碍物种类"][1]["evidences"]), 2)

    def test_d04_segments_no_hardcoded_adef_prefixes(self):
        """验证 D-04 修复：异院前缀 J/K/Z 动态按电压与回路特征归属，无 A/D/E/F 硬编码。"""
        basic_rows = [
            {"out": {"线路段名称": "220kV主干段", "电压等级": "220kV", "回路类型": "单回路", "线路亘长（km）": 12.5}},
            {"out": {"线路段名称": "110kV双回改造段", "电压等级": "110kV", "回路类型": "双回路", "线路亘长（km）": 4.2}},
            {"out": {"线路段名称": "110kV单回分支段", "电压等级": "110kV", "回路类型": "单回路", "线路亘长（km）": 2.1}},
        ]
        raw_towers = [
            {"out": {"杆塔号": "J1", "杆塔型号": "1E2-ZM1"}},
            {"out": {"杆塔号": "J2", "杆塔型号": "1E2-ZM1"}},
            {"out": {"杆塔号": "K1", "杆塔型号": "2B2-SJ2"}},
            {"out": {"杆塔号": "Z1", "杆塔型号": "1B2-ZM1"}},
        ]
        reg = LineSegmentRegistry.from_basic_and_towers(basic_rows, raw_towers)
        self.assertEqual(reg.for_tower_prefix("J"), "220kV主干段")
        self.assertEqual(reg.for_tower_prefix("K"), "110kV双回改造段")
        self.assertEqual(reg.for_tower_prefix("Z"), "110kV单回分支段")

    def test_d01_rho_shifted_bounds_not_hardcoded(self):
        """验证 D-01 修复：电阻率表列大幅左移/缩放时不再死板回落 _DEFAULT_BOUNDS。"""
        words = [
            (20.0, 60.0, 45.0, 72.0, "塔号"),
            (220.0, 60.0, 260.0, 72.0, "推荐电阻率"),
            (280.0, 60.0, 320.0, 72.0, "岩土"),
        ]
        bounds = rho_ext._detect_bounds(words)
        self.assertNotEqual(bounds, rho_ext._DEFAULT_BOUNDS)
        self.assertEqual(rho_ext._col_of(32.5, bounds), "塔号")
        self.assertEqual(rho_ext._col_of(240.0, bounds), "推荐")

    def test_human_protected_cell_and_p0_export_gate(self):
        store = ProjectTiziStore()
        store.set_sheet("基础库", [{
            "out": {"基础型号": "WZ1055", "基础类型": "掏挖基础", "基础.砼量(m³)": 10.5},
            "prov": {"基础.砼量(m³)": make_prov("extract", conf=0.95, status="confirmed", machine_value=10.5)},
        }])
        # 人工修订并确认
        store.edit_cell("基础库", 0, "基础.砼量(m³)", value=12.8, action="edit")
        # 后续 VLM 尝试覆写必须被保护跳过
        res = patch_store_cell(
            store, sheet_name="基础库", row_idx=0, col_key="基础.砼量(m³)",
            value=99.9, actor="vlm",
        )
        self.assertFalse(res["applied"])
        self.assertEqual(res["skipped_reason"], "human_confirmed_protected")
        self.assertEqual(store.get_sheet("基础库")["rows"][0]["out"]["基础.砼量(m³)"], 12.8)

        # 插入 P0 冲突 -> 导出门禁必须阻断
        store.set_sheet("基本信息", [{
            "out": {"线路段名称": "主干段", "人力运距（km）": 15.0},
            "prov": {"人力运距（km）": make_prov("extract", conf=0.5, status="conflict", note="运距 15m 冲突")},
        }])
        gate = evaluate_export_gate(store)
        self.assertFalse(gate.allowed)
        self.assertEqual(len(gate.p0_blocking), 1)

        # 经人工裁决确认后，门禁放行
        store.edit_cell("基本信息", 0, "人力运距（km）", value=0.015, action="edit")
        gate_after = evaluate_export_gate(store)
        self.assertTrue(gate_after.allowed)


if __name__ == "__main__":
    unittest.main()
