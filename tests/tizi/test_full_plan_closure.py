# -*- coding: utf-8 -*-
"""方案收口全量验证测试：N2->N3 跨院版式串联、N5 型号映射合并重访、N3-V 扫描件抽取、D-02 接地网格、D-09 docx 勘察。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tizi import discover, parse_table  # noqa: E402
from tizi.extractors import grounding as grounding_ext  # noqa: E402
from tizi.extractors import survey as survey_ext  # noqa: E402
from tizi.harness.gateway import VlmGateway  # noqa: E402
from tizi.harness.graph import TiziGraphRunner  # noqa: E402
from tizi.harness.registry import get_default_registry  # noqa: E402
from tizi.harness.store import ProjectTiziStore  # noqa: E402
from tizi.harness.workspace import TiziWorkspace  # noqa: E402
from tizi.nodes import NodeContext  # noqa: E402
from tizi.nodes.n3v_extract_vis import make_n3v_extract_vis  # noqa: E402
from tizi.nodes.n5_plan_revisit import make_n5_plan_revisit  # noqa: E402
from tizi.nodes.n6_visual_revisit import make_n6_visual_revisit  # noqa: E402
from tizi.tools.validator import build_conflict_report  # noqa: E402
from tizi.tools.vlm_schema import compute_local_header_match_score, vlm_learn_layout_schema  # noqa: E402


def _make_grid_pdf(pdf_path: Path, rows: list[list[str]], title: str = ""):
    doc = pymupdf.open()
    page = doc.new_page(width=840, height=560)
    x_edges = [40, 170, 320, 460, 620, 780][: len(rows[0]) + 1]
    y0 = 70 if title else 40
    row_h = 34
    if title:
        page.insert_text((42, 42), title, fontsize=11, fontname="china-s")
    for r_idx in range(len(rows) + 1):
        y = y0 + r_idx * row_h
        page.draw_line((x_edges[0], y), (x_edges[-1], y))
    for x in x_edges:
        page.draw_line((x, y0), (x, y0 + len(rows) * row_h))
    for r_idx, row in enumerate(rows):
        y_txt = y0 + r_idx * row_h + 21
        for c_idx, val in enumerate(row):
            if c_idx + 1 < len(x_edges):
                page.insert_text((x_edges[c_idx] + 8, y_txt), str(val), fontsize=10, fontname="china-s")
    doc.save(str(pdf_path))
    doc.close()


class FullPlanClosureTests(unittest.TestCase):
    def test_local_synonym_fast_path_and_n2_layout_drives_parse_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = TiziWorkspace(root / "ws", session_id="s1")

            # 1) 标准已知表头：本地同义词 >= 0.85 且通过 5 行自检，零 VLM 调用
            std_pdf = root / "std_tower.pdf"
            _make_grid_pdf(
                std_pdf,
                [
                    ["杆塔号", "杆塔型号", "呼称高", "全重(t)"],
                    ["NA1", "2E1Y7-J1", "30", "15.2"],
                    ["NA2", "2E1Y7-J2", "33", "18.6"],
                    ["NA3", "2E1Y7-Z1", "36", "12.4"],
                ],
            )
            called = {"count": 0}

            def _mock_fail_if_called(pv, imgs, prompt):
                called["count"] += 1
                return {}

            gw_guard = VlmGateway(mock_handler=_mock_fail_if_called)
            score, cmap = compute_local_header_match_score("杆塔明细表", ["杆塔号", "杆塔型号", "呼称高", "全重(t)"])
            self.assertGreaterEqual(score, 0.85)
            self.assertEqual(cmap[0], "杆塔号")
            res_fast = vlm_learn_layout_schema(
                workspace=ws, pdf_path=str(std_pdf), doc_id="struct_std",
                target_sheet="杆塔明细表", page=0, gateway=gw_guard,
            )
            self.assertTrue(res_fast.ok)
            self.assertEqual(called["count"], 0)
            self.assertTrue(res_fast.data["sanity_passed"])

            # 2) 异院陌生表头：触发 mock VLM 学习（单位为 kg），通过自检后直接驱动 parse_table._parse_with_grid
            foreign_pdf = root / "foreign_tower.pdf"
            _make_grid_pdf(
                foreign_pdf,
                [
                    ["位序标识", "结构型式代号", "基准高(m)", "单基重(kg)"],
                    ["K11", "2E1Y7-J1", "27", "15600"],
                    ["K12", "2E1Y7-Z2", "30", "11200"],
                ],
            )

            def _mock_foreign(pv, imgs, prompt):
                return {
                    "institute_hint": "丙院版式",
                    "column_mapping": {"0": "杆塔号", "1": "杆塔型号", "2": "呼称高", "3": "全重"},
                    "unit_overrides": {"全重": "kg"},
                    "tower_id_regex": r"^K\d+$",
                    "confidence": 0.94,
                }

            gw_for = VlmGateway(mock_handler=_mock_foreign)
            res_for = vlm_learn_layout_schema(
                workspace=ws, pdf_path=str(foreign_pdf), doc_id="struct_for",
                target_sheet="杆塔明细表", page=0, gateway=gw_for,
            )
            self.assertTrue(res_for.ok)
            self.assertEqual(res_for.data["source"], "vlm_learned")
            self.assertTrue(res_for.data["sanity_passed"])

            parsed = parse_table.parse(str(foreign_pdf), {"columns": [], "row_pattern": r"^K\d+$", "row_key": "c1"}, layout_profile=res_for.data)
            self.assertEqual(len(parsed), 2)
            self.assertEqual(parsed[0]["cells"]["杆塔号"], "K11")
            self.assertEqual(parsed[0]["cells"]["全重"], "15.6")

    def test_n5_tower_to_model_dedup_and_strict_doc_role_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = TiziWorkspace(root / "ws", session_id="s_revisit")
            folder = root / "proj"
            folder.mkdir()

            # 创建一个 3 页的《全线杆塔型式一览图.pdf》
            tower_pdf = folder / "全线杆塔型式一览图.pdf"
            doc = pymupdf.open()
            for idx in range(3):
                doc.new_page(width=600, height=400)
            doc.save(str(tower_pdf))
            doc.close()

            store = ProjectTiziStore()
            store.set_sheet("杆塔明细表", [
                {"row_id": "NA23", "out": {"杆塔号": "NA23", "杆塔型号": "V3-2E1Y9-J3", "杆塔.全高（m）": 42.0, "杆塔.全重(t)": None}, "prov": {}},
                {"row_id": "NA38", "out": {"杆塔号": "NA38", "杆塔型号": "V3-2E1Y9-J3", "杆塔.全高（m）": 45.0, "杆塔.全重(t)": None}, "prov": {}},
            ])
            store.set_sheet("基础库", [
                {"row_id": "WK1090", "out": {"基础型号": "WK1090", "基础.砼量(m³)": None}, "prov": {}},
            ])

            vis_idx = {
                "entities": {
                    "V3-2E1Y9-J3": [
                        {"doc_rel": "全线杆塔型式一览图.pdf", "doc_path": str(tower_pdf), "doc_role": "tower_profile", "page": 2, "bbox": [10, 10, 500, 350]}
                    ]
                }
            }
            detail_docs = [
                {"rel": "全线杆塔型式一览图.pdf", "name": "全线杆塔型式一览图.pdf", "doc_role": "tower_profile", "pages": 3, "path": str(tower_pdf)},
                {"rel": "明细表编制说明.pdf", "name": "明细表编制说明.pdf", "doc_role": "notes", "pages": 5, "path": str(tower_pdf)},
            ]

            report = build_conflict_report(store, [], visual_index=vis_idx, detail_docs=detail_docs)
            # 无 foundation_detail 时，CHK-08 绝不误标记为 revisitable
            chk08_gaps = [g for g in report.revisitable_gaps if g.chk_code == "CHK-08"]
            self.assertEqual(chk08_gaps, [])
            chk04_gaps = [g for g in report.revisitable_gaps if g.chk_code == "CHK-04"]
            self.assertEqual(len(chk04_gaps), 2)
            self.assertEqual({g.model_code for g in chk04_gaps}, {"V3-2E1Y9-J3"})

            ws.write_json("06_constraints/chk_round_0.json", report.model_dump())
            ws.write_json("01_catalog/visual_index.json", vis_idx)
            ws.write_json("01_catalog/catalog.json", {"detail_docs": detail_docs})

            vlm_calls = {"n": 0}

            def _mock_tower_vlm(pv, imgs, prompt):
                vlm_calls["n"] += 1
                return {
                    "entity_id": "V3-2E1Y9-J3",
                    "proposals": [{
                        "field_name": "杆塔.全重(t)",
                        "raw_value": "21.5",
                        "raw_unit": "t",
                        "raw_quote": "单基全重 21.5t",
                        "bbox_in_crop": [0.2, 0.3, 0.4, 0.4],
                        "model_self_conf": 0.9,
                        "reasoning": "图面第3页标注",
                    }],
                    "need_expand_crop": False,
                    "expand_direction": "none",
                }

            ctx = NodeContext(
                workspace=ws,
                folder=str(folder),
                store=store,
                gateway=VlmGateway(mock_handler=_mock_tower_vlm),
                registry=get_default_registry(),
            )
            n5 = make_n5_plan_revisit(ctx)
            n6 = make_n6_visual_revisit(ctx)

            plan_out = n5({"folder": str(folder), "revisit_round": 0, "max_vlm_crops": 24, "tried_crops": []})
            tasks = plan_out["revisit_tasks"]
            # NA23 与 NA38 同属 V3-2E1Y9-J3 -> 合并成 1 次 VLM 任务且定位到 page=2
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["page"], 2)
            self.assertEqual(sorted(tasks[0]["target_rows"]), [0, 1])

            n6_out = n6({"_send_task": tasks[0]})
            self.assertEqual(vlm_calls["n"], 1)
            self.assertEqual(n6_out["revisit_results"][0]["applied"], 2)
            t_rows = store.get_sheet("杆塔明细表")["rows"]
            self.assertEqual(t_rows[0]["out"]["杆塔.全重(t)"], 21.5)
            self.assertEqual(t_rows[1]["out"]["杆塔.全重(t)"], 21.5)
            self.assertEqual(t_rows[0]["prov"]["杆塔.全重(t)"]["status"], "vlm_pending")

    def test_n3v_scanned_pdf_extracts_rows_and_pauses_at_n7(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scan_pdf = root / "扫描杆塔明细表.pdf"
            img_path = root / "page.png"
            im = Image.new("RGB", (1600, 1100), "white")
            draw = ImageDraw.Draw(im)
            draw.rectangle([100, 100, 1500, 900], outline="black", width=3)
            im.save(str(img_path))

            doc = pymupdf.open()
            pg = doc.new_page(width=842, height=595)
            pg.insert_image(pymupdf.Rect(0, 0, 842, 595), filename=str(img_path))
            doc.save(str(scan_pdf))
            doc.close()

            ws = TiziWorkspace(root / "ws", session_id="s_scan")
            ws.write_json("01_catalog/catalog.json", {
                "files": [{"name": scan_pdf.name, "rel": scan_pdf.name, "kind": "scan", "carrier": "T0", "pages": 1, "path": str(scan_pdf)}]
            })
            store = ProjectTiziStore()

            def _mock_scan(pv, imgs, prompt):
                return {
                    "target_sheet": "杆塔明细表",
                    "rows": [{
                        "entity_id": "S1",
                        "fields": {"杆塔号": "S1", "杆塔型号": "2E1Y7-J1", "呼称高": 30},
                        "raw_quote": "S1 2E1Y7-J1 30",
                        "model_self_conf": 0.88,
                    }],
                }

            ctx = NodeContext(
                workspace=ws, folder=str(root), store=store,
                gateway=VlmGateway(mock_handler=_mock_scan),
                registry=get_default_registry(),
            )
            n3v = make_n3v_extract_vis(ctx)
            out = n3v({"folder": str(root), "vlm_crops_used": 0, "max_vlm_crops": 24})
            self.assertEqual(out["metrics"]["n3v_rows"], 1)
            rows = store.get_sheet("杆塔明细表")["rows"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["out"]["杆塔号"], "S1")
            self.assertEqual(rows[0]["prov"]["杆塔型号"]["status"], "vlm_pending")

    def test_d09_docx_survey_report_and_d02_grounding_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1) D-09 Word .docx 勘察报告
            docx_path = root / "220kV岩土工程勘察报告.docx"
            doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>杆塔号</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>岩土名称</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>比例(%)</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>层深(m)</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>NA1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>粉质粘土</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>60</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>3.5</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t></w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>中风化砂岩</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>40</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>2.0</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""
            with zipfile.ZipFile(docx_path, "w") as zf:
                zf.writestr("word/document.xml", doc_xml.encode("utf-8"))

            aux = discover.detect_aux_sources([], str(root))
            self.assertIn("survey", aux)
            self.assertEqual(aux["survey"]["format"], "docx")

            sres = survey_ext.build_survey_sheet(
                survey_src=aux["survey"], tower_rows=[], rho_records={},
            )
            self.assertEqual(sres["meta"]["mode"], "survey_docx")
            self.assertEqual(sres["missing_source"], "")
            self.assertEqual(len(sres["rows"]), 2)
            self.assertEqual(sres["rows"][0]["out"]["基础土质.土质类型"], "坚土")
            self.assertEqual(sres["rows"][1]["out"]["基础土质.土质类型"], "岩石(爆破)")

            # 2) D-02 接地模块网格提取
            g_pdf = root / "防雷接地装置图.pdf"
            _make_grid_pdf(
                g_pdf,
                [
                    ["模块代号", "2D-Z36-F1", "2D-Z36-F2", "2D-Z36-F3", "2D-Z36-F4"],
                    ["长度(m)", "10", "15", "20", "25"],
                    ["总重(kg)", "52", "78", "104", "130"],
                ],
                title="2D-Z36 接地装置参数表",
            )
            glib = grounding_ext.parse_grounding_pdf(str(g_pdf))
            self.assertIn("2D-Z36", glib)
            self.assertEqual(glib["2D-Z36"]["len"], [10.0, 15.0, 20.0, 25.0])
            self.assertEqual(glib["2D-Z36"]["wt_kg"], [52.0, 78.0, 104.0, 130.0])


if __name__ == "__main__":
    unittest.main()
