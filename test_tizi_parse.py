# -*- coding: utf-8 -*-
"""阶段 A 测试：T2 解析、两次运行一致性、中文路径、双缩放同 bbox 与龙泉山基准。"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pymupdf

from tizi import Pipeline, carrier, drawing, parse_table, transform

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


def _make_sample_t2_pdf(path: Path):
    """生成带精确坐标文字层的 T2 夹具 PDF（含表头、3 行塔位及 1 行矢量无字区）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    # 表头（y < 90）
    page.insert_text((30, 60), "NO", fontsize=10)
    page.insert_text((110, 60), "TYPE", fontsize=10)
    page.insert_text((240, 60), "HEIGHT", fontsize=10)
    page.insert_text((330, 60), "ANGLE", fontsize=10)
    # 3 行数据（y > 90）
    rows = [
        ("NA1", "V3-2E1-Z1", "24", "10"),
        ("NA2", "V3-2E1-J1", "30", "15"),
        ("NB1", "GFJ-220-1", "27", "0"),
    ]
    for idx, (no, typ, h, ang) in enumerate(rows):
        y = 120 + idx * 24
        page.insert_text((30, y), no, fontsize=10)
        page.insert_text((110, y), typ, fontsize=10)
        page.insert_text((240, y), h, fontsize=10)
        page.insert_text((330, y), ang, fontsize=10)
    # 页面底部画一块纯矢量矩形（无文字层），用于测试框选无字区
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 280, 180, 340))
    shape.finish(color=(0, 0, 0), width=1.0)
    shape.commit()
    doc.save(str(path))
    doc.close()


def _screen_box_to_pdf_bbox(screen_box, display_size, pdf_size):
    """前端框选坐标按实际显示尺寸折回 PDF 点坐标，与缩放比及 dpi 无关。"""
    sx0, sy0, sx1, sy1 = screen_box
    dw, dh = display_size
    pw, ph = pdf_size
    return [
        round(min(sx0, sx1) / dw * pw, 1),
        round(min(sy0, sy1) / dh * ph, 1),
        round(max(sx0, sx1) / dw * pw, 1),
        round(max(sy0, sy1) / dh * ph, 1),
    ]


class TiziParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tizi_parse_"))
        self.cn_dir = self.tmp / "中文工程目录_220kV线路"
        self.cn_dir.mkdir(parents=True, exist_ok=True)
        self.pdf = self.cn_dir / "杆塔明细夹具.pdf"
        _make_sample_t2_pdf(self.pdf)
        self.layout = {
            "layout_id": "test_fixture",
            "source": "struct",
            "body_top": 90,
            "row_tolerance": 3.5,
            "row_key": "tower_no",
            "row_pattern": r"^N[A-F]\d+$",
            "columns": [
                {"key": "tower_no", "label": "杆塔号", "x0": 0, "x1": 90},
                {"key": "tower_type", "label": "杆塔型号", "x0": 90, "x1": 210},
                {"key": "height", "label": "呼称高", "x0": 210, "x1": 300},
                {"key": "angle", "label": "线路转角", "x0": 300, "x1": 420},
            ],
        }
        self.mapping = {
            "template": "test",
            "layout": "test_fixture",
            "columns": [
                {"target": "杆塔号", "from": "tower_no"},
                {"target": "杆塔型号", "from": "tower_type"},
                {"target": "呼称高", "from": "height", "cast": "float"},
                {"target": "线路段", "from": "tower_no", "transform": "segment"},
            ],
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chinese_path_and_deterministic_twice_run(self):
        """中文路径下连续解析两次，cells 与 out 机器值必须完全一致。"""
        r1 = parse_table.parse(str(self.pdf), self.layout)
        r2 = parse_table.parse(str(self.pdf), self.layout)
        self.assertEqual(len(r1), 3)
        self.assertEqual(r1, r2)

        transform.apply(r1, self.mapping)
        transform.apply(r2, self.mapping)
        self.assertEqual([r["cells"] for r in r1], [r["cells"] for r in r2])
        self.assertEqual([r["out"] for r in r1], [r["out"] for r in r2])
        self.assertEqual(r1[0]["out"]["杆塔号"], "NA1")
        self.assertEqual(r1[0]["out"]["呼称高"], 24.0)
        self.assertEqual(r1[0]["out"]["线路段"], "A 段")
        self.assertEqual(r1[2]["out"]["线路段"], "B 段")

    def test_drawing_bbox_zoom_invariance_and_no_text_region(self):
        """两种缩放比例下框同一位置折算得到完全相同的 PDF bbox，且框内无文字层不猜测。"""
        png1, size1 = drawing.render_page(str(self.pdf), page=0, dpi=110)
        png2, size2 = drawing.render_page(str(self.pdf), page=0, dpi=170)
        self.assertTrue(png1.startswith(b"\x89PNG"))
        self.assertTrue(png2.startswith(b"\x89PNG"))
        self.assertGreater(size2[0], size1[0])

        pdf_size = (600.0, 400.0)
        # 对应 PDF 点框 [235, 108, 265, 126]（NA1 的呼称高 "24"）
        # 缩放 A：适宽 49%（相对 170dpi 像素）
        disp_a = (size2[0] * 0.49, size2[1] * 0.49)
        box_a = (235 / 600 * disp_a[0], 108 / 400 * disp_a[1],
                 265 / 600 * disp_a[0], 126 / 400 * disp_a[1])
        # 缩放 B：1:1 显示
        disp_b = (float(size2[0]), float(size2[1]))
        box_b = (235 / 600 * disp_b[0], 108 / 400 * disp_b[1],
                 265 / 600 * disp_b[0], 126 / 400 * disp_b[1])

        bbox_a = _screen_box_to_pdf_bbox(box_a, disp_a, pdf_size)
        bbox_b = _screen_box_to_pdf_bbox(box_b, disp_b, pdf_size)
        self.assertEqual(bbox_a, bbox_b)

        reg = drawing.region_words(str(self.pdf), 0, bbox_a)
        self.assertTrue(reg["has_text"])
        self.assertEqual(reg["text"].strip(), "24")

        # 框选无文字层的纯矢量区域 -> has_text=False，不猜测文字
        empty_reg = drawing.region_words(str(self.pdf), 0, [55, 285, 175, 335])
        self.assertFalse(empty_reg["has_text"])
        self.assertEqual(empty_reg["text"], "")
        self.assertIn("无文字层", empty_reg["reason"])

    @unittest.skipUnless(REAL_WORKSPACE.is_dir(), "未找到真实测试工作空间：提资前期技术预演")
    def test_real_longquanshan_pipeline_baseline_and_determinism(self):
        """对真实测试工作空间『提资前期技术预演』连续跑两次，验证龙泉山回归基准与确定性。"""
        defaults = REPO / "tizi" / "defaults"

        def run_once(tag):
            job_dir = self.tmp / tag
            shutil.copytree(defaults / "config", job_dir / "config")
            shutil.copytree(defaults / "data", job_dir / "data")
            pipe = Pipeline(
                config_dir=str(job_dir / "config"),
                data_root=str(REAL_WORKSPACE),
                out_dir=str(job_dir / "out"),
                project_root=str(job_dir),
            )
            pipe.step0_discover()
            pipe.step1_carrier()
            pipe.step2_parse()
            pipe.step3_mapping()
            pipe.step4_rules()
            pipe.step5_validate()
            return pipe

        p1 = run_once("run1")
        p2 = run_once("run2")

        # 两次运行机器结果完全一致
        self.assertEqual([r["cells"] for r in p1.rows], [r["cells"] for r in p2.rows])
        self.assertEqual([r["out"] for r in p1.rows], [r["out"] for r in p2.rows])

        # 龙泉山自动配置回归基准
        self.assertEqual(len(p1.rows), 215)
        self.assertEqual(len(p1.mapping["columns"]), 9)
        total_cells = len(p1.rows) * len(p1.targets)
        filled_cells = sum(1 for r in p1.rows for t in p1.targets if r["out"].get(t) is not None)
        self.assertEqual(total_cells, 2795)
        self.assertEqual(filled_cells, 2174)

        by_name = {c["name"]: c for c in p1.checks}
        self.assertAlmostEqual(by_name["角钢塔耗钢合计"]["actual"], 3462.05, places=2)
        self.assertTrue(by_name["角钢塔耗钢合计"]["ok"])
        self.assertAlmostEqual(by_name["钢管杆耗钢合计"]["actual"], 290.4, places=2)
        self.assertFalse(by_name["钢管杆耗钢合计"]["ok"])
        self.assertEqual(by_name["塔位总数"]["actual"], 215.0)
        self.assertFalse(by_name["塔位总数"]["ok"])

        # 载体判定确认主数据源为 T2
        primary_info = next(s for s in p1.src_info if s.get("carrier") == "T2")
        self.assertEqual(primary_info["carrier"], "T2")

    def test_discover_deduplicates_keeping_shorter_rel_path(self):
        """同名同大小同页数的副本只保留相对路径更短的一份。"""
        from tizi import discover
        deep = self.cn_dir / "a" / "b" / "c" / "杆塔明细夹具.pdf"
        deep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.pdf, deep)
        prof = discover.build(str(self.cn_dir))
        self.assertEqual(prof["duplicates"], 1)
        self.assertEqual(prof["scanned"], 1)
        self.assertEqual(prof["files"][0]["rel"], "杆塔明细夹具.pdf")


if __name__ == "__main__":
    unittest.main()
