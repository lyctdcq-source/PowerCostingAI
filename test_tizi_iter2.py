# -*- coding: utf-8 -*-
"""迭代 2（v0.10.0）专项自动化测试：
- GridCellExtractor 框线网格还原与 HeaderTreeBuilder 多层合并表头
- MaterialSectionFSM 一源三表投射（结构设备材料表固定 26 行、电气设备材料表、光缆设备材料表）
- 平台价格库只读联动匹配（POST /api/tizi/price-match，写入 price_ref + pending，绝不写回价格库）
- 院标标准导入版 .xlsx 原位回填三张材料表且保持结构表固定 26 行行位零偏移
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

import auth
import protected
import server as app
from test_tizi_job import _HttpClient
from tizi.grid_parser import HeaderTreeBuilder

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


class Iter2UnitTests(unittest.TestCase):
    def test_header_tree_builder(self):
        rows = [
            {"cells": [
                {"x0": 0, "x1": 60, "text": "杆塔号"},
                {"x0": 60, "x1": 180, "text": "接地装置"},
            ]},
            {"cells": [
                {"x0": 0, "x1": 60, "text": ""},
                {"x0": 60, "x1": 120, "text": "型号"},
                {"x0": 120, "x1": 180, "text": "长度(m)"},
            ]},
        ]
        names = HeaderTreeBuilder.build(rows)
        self.assertEqual(names, ["杆塔号", "接地装置.型号", "接地装置.长度(m)"])


@unittest.skipUnless(REAL_WORKSPACE.is_dir(), "需要真实施工图样本目录 提资前期技术预演")
class Iter2MaterialBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tizi_iter2_")
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
        app.STATE.update(
            app.session_defaults(),
            running=False,
            session_id="iter2-sess",
            sessions=[],
            prices=[
                {"name": "铝包钢芯铝绞线", "spec": "JL/LB20A-500/45", "unit": "km", "price": "38500", "period": "2026Q2"},
                {"name": "耐污玻璃绝缘子", "spec": "UG160BH", "unit": "片", "price": "126.0", "period": "2026Q2"},
                {"name": "OPGW光缆", "spec": "OPGW-48B1-120", "unit": "km", "price": "24800", "period": "2026Q2"},
            ],
        )

        self.httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = _HttpClient(f"http://127.0.0.1:{self.port}")

        salt = "22334455667788990011aabbccddeeff"
        protected.write_text(
            app.DATA / "users.json",
            json.dumps({
                "users": [{
                    "username": "tester",
                    "display": "tester",
                    "role": auth.ADMIN,
                    "salt": salt,
                    "hash": auth.password_hash("Iter2Pass!1", salt),
                }]
            }, ensure_ascii=False),
        )
        code, data, _ = self.client.post_json("/api/auth/login", {"username": "tester", "password": "Iter2Pass!1"})
        self.assertEqual(code, 200, data)

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

    def test_one_source_three_material_sheets_and_price_match(self):
        self.assertEqual(self.client.post_json("/api/tizi/open", {"folder": str(REAL_WORKSPACE)})[0], 200)
        code, run_res, _ = self.client.post_json("/api/tizi/run", {"steps": "extract", "sync": True})
        self.assertEqual(code, 200, run_res)
        self.assertTrue(run_res["ok"])

        # 1) 检查三张材料表摘要与 CHK-06
        code, sh, _ = self.client.get_json("/api/tizi/sheets")
        self.assertEqual(code, 200)
        by_name = {s["name"]: s for s in sh["sheets"]}
        self.assertEqual(by_name["结构设备材料表"]["rows"], 26)
        self.assertGreaterEqual(by_name["电气设备材料表"]["rows"], 250)
        self.assertGreaterEqual(by_name["光缆设备材料表"]["rows"], 80)

        chks = {c["code"]: c for c in sh["workbook_checks"]}
        self.assertIn("CHK-06", chks)
        self.assertTrue(chks["CHK-06"]["ok"])
        self.assertAlmostEqual(chks["CHK-03"]["expect"], 3483.27, places=2)
        self.assertAlmostEqual(chks["CHK-04"]["expect"], 314.14, places=2)

        # 2) 验证结构设备材料表 R03 / R05 / R08 / R14 汇总值与多工程证据链
        code, st_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E7%BB%93%E6%9E%84%E8%AE%BE%E5%A4%87%E6%9D%90%E6%96%99%E8%A1%A8&limit=50")
        self.assertEqual(code, 200)
        by_rid = {r["row_id"]: r["cells"] for r in st_tbl["rows"]}
        self.assertAlmostEqual(by_rid["R03"]["数量（不含损耗）"]["value"], 3483.267, places=2)
        self.assertAlmostEqual(by_rid["R05"]["数量（不含损耗）"]["value"], 314.136, places=2)
        self.assertAlmostEqual(by_rid["R08"]["数量（不含损耗）"]["value"], 626.939, places=2)
        self.assertAlmostEqual(by_rid["R14"]["数量（不含损耗）"]["value"], 107.0, places=1)
        self.assertGreaterEqual(len(by_rid["R03"]["数量（不含损耗）"]["evidences"]), 4)

        # 3) 只读价格库参考单价匹配（不得修改 STATE['prices']）
        prices_before = copy.deepcopy(app.STATE["prices"])
        code, pm_res, _ = self.client.post_json("/api/tizi/price-match", {"sheet": "电气设备材料表"})
        self.assertEqual(code, 200, pm_res)
        self.assertGreaterEqual(pm_res["matched"], 2)
        self.assertEqual(app.STATE["prices"], prices_before)

        # 验证命中的价格格状态为 pending（待造价员确认）且 method=price_ref
        code, el_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E7%94%B5%E6%B0%94%E8%AE%BE%E5%A4%87%E6%9D%90%E6%96%99%E8%A1%A8&limit=100")
        self.assertEqual(code, 200)
        priced = [r["cells"]["合同单价含税（元）"] for r in el_tbl["rows"] if r["cells"]["合同单价含税（元）"]["value"] is not None]
        self.assertGreaterEqual(len(priced), 2)
        self.assertEqual(priced[0]["method"], "price_ref")
        self.assertEqual(priced[0]["status"], "pending")

        # 4) 验证标准导入版 .xlsx 三张材料表原位回填准确
        code, std_bytes, _ = self.client.request("GET", "/api/tizi/export?mode=standard")
        self.assertEqual(code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(std_bytes), data_only=False)
        ws_s = wb["结构设备材料表"]
        self.assertEqual(ws_s.cell(3, 1).value, "杆塔")
        self.assertEqual(ws_s.cell(3, 2).value, "角钢（含高强钢）")
        self.assertAlmostEqual(ws_s.cell(3, 4).value, 3483.267, places=2)
        self.assertAlmostEqual(ws_s.cell(5, 4).value, 314.136, places=2)
        self.assertAlmostEqual(ws_s.cell(14, 4).value, 107.0, places=1)
        self.assertGreaterEqual(wb["电气设备材料表"].max_row, 250)
        self.assertGreaterEqual(wb["光缆设备材料表"].max_row, 80)


if __name__ == "__main__":
    unittest.main()
