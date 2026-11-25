# -*- coding: utf-8 -*-
"""迭代 3（v0.11.0）专项自动化测试：
- 《串型汇总表》C01–C12（悬垂串、耐张串、跳线串、防振锤）多段聚合与标准串型字典映射
- 《勘测信息一览表》：
  1) 合成《岩土工程勘察报告》逐塔多行分层 PDF 测试（1-to-many 土质分层 + 字典映射 + 省市县镇）
  2) 真实样本（缺独立勘察报告）优雅降级：不报错、标 missing_source、由电阻率报告生成 214 行待复核骨架
- 院标标准导入版 .xlsx 同步回填《串型汇总表》与《勘测信息一览表》
"""
import copy
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

import openpyxl
import pymupdf

import auth
import protected
import server as app
from test_tizi_job import _HttpClient
from tizi.extractors.survey import map_standard_soil, parse_survey_pdf

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


def _make_multilayer_survey_pdf(dst: Path):
    """生成带矢量框线的合成《岩土工程勘察报告》逐塔分层表（含一塔多行分层）。"""
    doc = pymupdf.open()
    pg = doc.new_page(width=600, height=400)
    # 绘 6 列 × 5 行框线网格
    xs = [30, 100, 220, 290, 360, 460, 560]
    ys = [40, 70, 100, 130, 160, 190]
    shape = pg.new_shape()
    for y in ys:
        shape.draw_line((xs[0], y), (xs[-1], y))
    for x in xs:
        shape.draw_line((x, ys[0]), (x, ys[-1]))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()

    grid_text = [
        ["塔号", "土质类型", "比例%", "分层深m", "州市", "县区"],
        ["NA1", "粉质粘土", "40", "2.0", "玉溪市", "红塔区"],
        ["", "强风化砂岩", "60", "3.5", "玉溪市", "红塔区"],
        ["NA2", "中风化灰岩", "100", "4.5", "玉溪市", "红塔区"],
        ["NA3", "耕植土", "100", "1.5", "玉溪市", "江川区"],
    ]
    for ri, row in enumerate(grid_text):
        for ci, txt in enumerate(row):
            if txt:
                pg.insert_text((xs[ci] + 6, ys[ri] + 19), txt, fontname="china-s", fontsize=10)
    doc.save(str(dst))
    doc.close()


class Iter3UnitTests(unittest.TestCase):
    def test_soil_dict_and_multilayer_survey_pdf(self):
        self.assertEqual(map_standard_soil("以坚土为主桃园耕地")[0], "坚土")
        self.assertEqual(map_standard_soil("以普土为主")[0], "普通土")
        self.assertEqual(map_standard_soil("以松砂石为主林地")[0], "松砂石")
        self.assertEqual(map_standard_soil("中风化玄武岩")[0], "岩石(爆破)")

        with tempfile.TemporaryDirectory(prefix="tizi_surv_") as td:
            pdf_path = Path(td) / "岩土工程勘察报告.pdf"
            _make_multilayer_survey_pdf(pdf_path)
            rows = parse_survey_pdf(str(pdf_path), rel_path="岩土工程勘察报告.pdf")
            # NA1 两层 + NA2 一层 + NA3 一层 = 4 行
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["out"]["杆塔号"], "NA1")
            self.assertEqual(rows[0]["out"]["基础土质.土质类型"], "坚土")
            self.assertEqual(rows[0]["out"]["基础土质.比例（%）"], 40.0)
            self.assertEqual(rows[1]["out"]["杆塔号"], "NA1")
            self.assertEqual(rows[1]["out"]["基础土质.土质类型"], "松砂石")
            self.assertEqual(rows[1]["out"]["基础土质.分层深(m)"], 3.5)
            self.assertEqual(rows[2]["out"]["基础土质.土质类型"], "岩石(爆破)")
            self.assertEqual(rows[3]["out"]["基础土质.土质类型"], "普通土")
            self.assertEqual(rows[3]["out"]["地点.县（市、区）"], "江川区")


@unittest.skipUnless(REAL_WORKSPACE.is_dir(), "需要真实施工图样本目录 提资前期技术预演")
class Iter3RealWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tizi_iter3_")
        self.tmp_root = Path(self.temp.name)
        self.old_data = app.DATA
        self.old_sessions = app.SESSIONS
        self.old_auth = app.AUTH
        self.old_state = copy.deepcopy(app.STATE)
        self.old_jobs = dict(app.TIZI_JOBS)

        app.DATA = self.tmp_root / "data"
        app.DATA.mkdir(parents=True, exist_ok=True)
        app.SESSIONS = app.DATA / "sessions"
        app.AUTH = auth.UserStore(app.DATA)
        app.TIZI_JOBS.clear()
        app.STATE.clear()
        app.STATE.update(self.old_state)
        app.STATE.update(app.session_defaults(), running=False, session_id="iter3-sess", sessions=[])

        self.httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = _HttpClient(f"http://127.0.0.1:{self.port}")

        salt = "33445566778899001122aabbccddeeff"
        protected.write_text(
            app.DATA / "users.json",
            json.dumps({
                "users": [{
                    "username": "tester",
                    "display": "tester",
                    "role": auth.ADMIN,
                    "salt": salt,
                    "hash": auth.password_hash("Iter3Pass!1", salt),
                }]
            }, ensure_ascii=False),
        )
        self.assertEqual(self.client.post_json("/api/auth/login", {"username": "tester", "password": "Iter3Pass!1"})[0], 200)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        app.TIZI_JOBS.clear()
        app.TIZI_JOBS.update(self.old_jobs)
        app.DATA = self.old_data
        app.SESSIONS = self.old_sessions
        app.AUTH = self.old_auth
        app.STATE.clear()
        app.STATE.update(self.old_state)
        self.temp.cleanup()

    def test_strings_and_survey_fallback(self):
        self.assertEqual(self.client.post_json("/api/tizi/open", {"folder": str(REAL_WORKSPACE)})[0], 200)
        code, run_res, _ = self.client.post_json("/api/tizi/run", {"steps": "extract", "sync": True})
        self.assertEqual(code, 200, run_res)

        # 1) 串型汇总表验证
        code, st_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E4%B8%B2%E5%9E%8B%E6%B1%87%E6%80%BB%E8%A1%A8&limit=100")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(st_tbl["total"], 10)
        has_susp = any(r["cells"]["导线悬垂串.型号"]["value"] for r in st_tbl["rows"])
        has_tens = any(r["cells"]["导线耐张串.型号"]["value"] for r in st_tbl["rows"])
        has_jump = any(r["cells"]["导线跳线串.型号"]["value"] for r in st_tbl["rows"])
        has_damp = any(r["cells"]["防振锤.型号"]["value"] for r in st_tbl["rows"])
        self.assertTrue(has_susp and has_tens and has_jump and has_damp)

        # 2) 勘测信息一览表（预演包缺独立勘察报告 → 优雅降级为 214 行待复核骨架 + missing_source 提示）
        code, sv_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E5%8B%98%E6%B5%8B%E4%BF%A1%E6%81%AF%E4%B8%80%E8%A7%88%E8%A1%A8&limit=250")
        self.assertEqual(code, 200)
        self.assertEqual(sv_tbl["total"], 214)
        self.assertIn("岩土工程勘察报告", sv_tbl["missing_source"])
        soils_filled = [r["cells"]["基础土质.土质类型"] for r in sv_tbl["rows"] if r["cells"]["基础土质.土质类型"]["value"]]
        self.assertGreaterEqual(len(soils_filled), 200)
        self.assertEqual(soils_filled[0]["status"], "pending")

        # 3) 导出标准版包含串型与勘测两表数据
        code, std_bytes, _ = self.client.request("GET", "/api/tizi/export?mode=standard")
        self.assertEqual(code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(std_bytes), data_only=False)
        self.assertIsNotNone(wb["串型汇总表"].cell(4, 2).value)
        self.assertIsNotNone(wb["勘测信息一览表"].cell(4, 2).value)


if __name__ == "__main__":
    unittest.main()
