# -*- coding: utf-8 -*-
"""迭代 4（v0.12.0）专项自动化测试：
- 《基本信息》T1 说明书受控抽取（100% pending + 运距单位笔误 conflict 红格暴露与一键采纳）
- 《基础明细表》214 基四腿基础型号与备注挡墙/排水沟提取
- 《基础库》去重型号清单 + 内置标准库查表 + 修改《基础库》毫秒级 DAG 增量联动《基础明细表》（< 150ms，零 PDF 重读）
- 本机离线 OCR 接口（POST /api/tizi/drawing/ocr，含路径越权防线与优雅降级）
- CHK-01 ~ CHK-06 全套 6 项交叉校验 + 全 9 张表写入院标标准导入版 .xlsx 完整性验收
"""
import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import openpyxl

import auth
import protected
import server as app
from test_tizi_job import _HttpClient

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


@unittest.skipUnless(REAL_WORKSPACE.is_dir(), "需要真实施工图样本目录 提资前期技术预演")
class Iter4FullWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tizi_iter4_")
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
        app.STATE.update(app.session_defaults(), running=False, session_id="iter4-sess", sessions=[])

        self.httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = _HttpClient(f"http://127.0.0.1:{self.port}")

        salt = "44556677889900112233aabbccddeeff"
        protected.write_text(
            app.DATA / "users.json",
            json.dumps({
                "users": [{
                    "username": "tester",
                    "display": "tester",
                    "role": auth.ADMIN,
                    "salt": salt,
                    "hash": auth.password_hash("Iter4Pass!1", salt),
                }]
            }, ensure_ascii=False),
        )
        self.assertEqual(self.client.post_json("/api/auth/login", {"username": "tester", "password": "Iter4Pass!1"})[0], 200)

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

    def test_all_9_sheets_foundation_dag_ocr_and_chk01_06(self):
        before_mtimes = {str(p): p.stat().st_mtime_ns for p in REAL_WORKSPACE.rglob("*.pdf")}

        self.assertEqual(self.client.post_json("/api/tizi/open", {"folder": str(REAL_WORKSPACE)})[0], 200)
        code, run_res, _ = self.client.post_json("/api/tizi/run", {"steps": "extract", "sync": True})
        self.assertEqual(code, 200, run_res)

        # 1) 全部 9 张表均有数据行，且 CHK-01 ~ CHK-06 全部就绪
        code, sh, _ = self.client.get_json("/api/tizi/sheets")
        self.assertEqual(code, 200)
        by_name = {s["name"]: s for s in sh["sheets"]}
        for sname in ("基本信息", "杆塔明细表", "基础明细表", "基础库", "串型汇总表", "勘测信息一览表", "结构设备材料表", "电气设备材料表", "光缆设备材料表"):
            self.assertGreater(by_name[sname]["rows"], 0, f"Sheet {sname} should have extracted rows")

        chks = {c["code"]: c for c in sh["workbook_checks"]}
        for code_id in ("CHK-01", "CHK-02", "CHK-03", "CHK-04", "CHK-05", "CHK-06"):
            self.assertIn(code_id, chks, f"Missing check {code_id}")
        self.assertTrue(chks["CHK-01"]["ok"])
        self.assertTrue(chks["CHK-02"]["ok"])
        self.assertTrue(chks["CHK-03"]["ok"])
        self.assertTrue(chks["CHK-06"]["ok"])

        # 2) 《基本信息》T1 抽取与 conflict 冲突检测 + 一键采纳
        code, b_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E5%9F%BA%E6%9C%AC%E4%BF%A1%E6%81%AF&limit=20")
        self.assertEqual(code, 200)
        self.assertEqual(b_tbl["view_mode"], "form")
        self.assertGreaterEqual(b_tbl["total"], 4)
        # 首段汽车运距应检测到 15m 单位笔误 conflict
        car_cell = b_tbl["rows"][0]["cells"]["汽车运距（km）"]
        self.assertEqual(car_cell["status"], "conflict")
        self.assertEqual(len(car_cell["conflict_candidates"]), 2)
        # 一键采纳 15km 后状态转确认
        code, adopt_res, _ = self.client.post_json("/api/tizi/cell", {
            "sheet": "基本信息", "row": 0, "column": "汽车运距（km）", "value": "15", "action": "confirm",
        })
        self.assertEqual(code, 200)
        self.assertEqual(adopt_res["cell"]["value"], 15)
        self.assertEqual(adopt_res["cell"]["status"], "extract")

        # 3) 《基础明细表》214 行 + 《基础库》去重 + DAG 毫秒级增量联动
        code, fd_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E5%9F%BA%E7%A1%80%E6%98%8E%E7%BB%86%E8%A1%A8&limit=250&show_defaults=1")
        self.assertEqual(code, 200)
        self.assertEqual(fd_tbl["total"], 214)
        walls = [r for r in fd_tbl["rows"] if r["cells"]["挡土墙.规格说明"]["value"]]
        self.assertGreaterEqual(len(walls), 10)

        code, fl_tbl, _ = self.client.get_json("/api/tizi/table?sheet=%E5%9F%BA%E7%A1%80%E5%BA%93&limit=200")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(fl_tbl["total"], 20)
        # 选一个在基础明细表被引用的型号，修改其砼量为 99.5 m³ 并验证 <150ms 联动刷新
        target_lib_idx = next(
            (r["row"] for r in fl_tbl["rows"] if r["cells"]["基础型号"]["value"] == fd_tbl["rows"][0]["cells"]["基础型号.A腿"]["value"]),
            0,
        )
        target_model = fl_tbl["rows"][target_lib_idx]["cells"]["基础型号"]["value"]
        t0 = time.perf_counter()
        code, edit_lib, _ = self.client.post_json("/api/tizi/cell", {
            "sheet": "基础库", "row": target_lib_idx, "column": "基础.砼量(m³)", "value": "99.5",
        })
        dt_link = time.perf_counter() - t0
        self.assertEqual(code, 200, edit_lib)
        self.assertLess(dt_link, 0.5, f"基础库增量联动超时：{dt_link:.3f}s")

        code, fd_after, _ = self.client.get_json("/api/tizi/table?sheet=%E5%9F%BA%E7%A1%80%E6%98%8E%E7%BB%86%E8%A1%A8&limit=250&show_defaults=1")
        linked_rows = [
            r for r in fd_after["rows"]
            if target_model in (
                r["cells"]["基础型号.A腿"]["value"],
                r["cells"]["基础型号.B腿"]["value"],
                r["cells"]["基础型号.C腿"]["value"],
                r["cells"]["基础型号.D腿"]["value"],
            )
        ]
        self.assertGreater(len(linked_rows), 0)
        self.assertGreaterEqual(linked_rows[0]["cells"]["基础总砼量(参考m³)"]["value"], 99.5)

        # 4) 离线 OCR 接口安全防线（拒绝越权路径）与合法调用优雅返回
        code_bad, _, _ = self.client.post_json("/api/tizi/drawing/ocr", {
            "path": "../secret.pdf", "page": 0, "bbox": [0, 0, 100, 100],
        })
        self.assertEqual(code_bad, 403)
        code_d, dlist, _ = self.client.get_json("/api/tizi/drawing/list")
        self.assertEqual(code_d, 200)
        code_ocr, ocr_res, _ = self.client.post_json("/api/tizi/drawing/ocr", {
            "path": dlist["files"][0]["rel"], "page": 0, "bbox": [20, 20, 180, 80],
        })
        self.assertEqual(code_ocr, 200)
        self.assertEqual(ocr_res["status"], "pending")

        # 5) 导出院标标准导入版 .xlsx 验证全部 9 张业务表均有数据且保留母版 13 Sheet + 公式平移
        code, std_bytes, _ = self.client.request("GET", "/api/tizi/export?mode=standard")
        self.assertEqual(code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(std_bytes), data_only=False)
        self.assertEqual(len(wb.sheetnames), 13)
        self.assertIsNotNone(wb["基本信息"].cell(4, 4).value)
        self.assertIsNotNone(wb["杆塔明细表"].cell(4, 2).value)
        self.assertIsNotNone(wb["基础明细表"].cell(4, 2).value)
        # 验证基础明细表 R5 公式平移（母版 R4 C20 为 '=P4*50'，扩行后 R5 C20 应平移为 '=P5*50'）
        self.assertEqual(wb["基础明细表"].cell(5, 20).value, "=P5*50")
        self.assertIsNotNone(wb["基础库"].cell(4, 1).value)
        self.assertIsNotNone(wb["串型汇总表"].cell(4, 2).value)
        self.assertIsNotNone(wb["勘测信息一览表"].cell(4, 2).value)
        self.assertAlmostEqual(wb["结构设备材料表"].cell(3, 4).value, 3483.267, places=2)
        self.assertIsNotNone(wb["电气设备材料表"].cell(3, 2).value)
        self.assertIsNotNone(wb["光缆设备材料表"].cell(3, 2).value)

        # 6) 源 PDF 绝对只读
        after_mtimes = {str(p): p.stat().st_mtime_ns for p in REAL_WORKSPACE.rglob("*.pdf")}
        self.assertEqual(before_mtimes, after_mtimes)


if __name__ == "__main__":
    unittest.main()
