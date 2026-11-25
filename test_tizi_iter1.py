# -*- coding: utf-8 -*-
"""迭代 1（v0.9.0）专项自动化测试：
- PageFeatureCache 内容指纹与缓存命中
- ProvenanceDAG 多源证据链与 UnitConverter 显式量纲换算
- 杆塔明细表 6 项修正（剥离 AN0 构架至 214 基、直线塔转角留空、全高缺塔头高标 pending、三源接地 C11-C14、跨越 C15-C19）
- TemplateWorkbookWriter 院标母版 13 Sheet 双轨输出（标准导入版 + 复核溯源版）
- 多表 HTTP API（/api/tizi/sheets、/api/tizi/table?sheet=...、Space 一键确认、/api/tizi/export?mode=...）
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
from tizi import cache as feat_cache
from tizi.provenance import UnitConverter, append_revision, make_evidence, make_prov

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


class Iter1UnitTests(unittest.TestCase):
    def test_unit_converter_and_provenance_dag(self):
        val, trace = UnitConverter.convert(146.2, "kg", "t", decimals=3)
        self.assertEqual(val, 0.146)
        self.assertIn("0.001", trace)
        self.assertIn("0.146 t", trace)

        deg, dtrace = UnitConverter.dms_to_decimal("左:9°7′16″", decimals=4)
        self.assertEqual(deg, 9.1211)
        self.assertIn("9.1211", dtrace)

        ev1 = make_evidence("结构册塔位", "struct.pdf", page=1, bbox=[10, 20, 30, 40], raw_text="NA1")
        ev2 = make_evidence("推荐电阻率", "rho.pdf", page=3, bbox=[50, 60, 70, 80], raw_text="350")
        pv = make_prov(
            "multi_join_lookup", status="confirmed", machine_value=0.146,
            unit="t", formula_trace=trace, evidences=[ev1, ev2],
        )
        self.assertEqual(len(pv["evidences"]), 2)
        self.assertEqual(pv["machine_value"], 0.146)

        pv2 = append_revision(pv, action="manual_edit", old_val=0.146, new_val=0.15, note="人工微调")
        self.assertEqual(pv2["machine_value"], 0.146)
        self.assertEqual(len(pv2["revisions"]), 1)
        self.assertEqual(pv2["revisions"][0]["new"], 0.15)


@unittest.skipUnless(REAL_WORKSPACE.is_dir(), "需要真实施工图样本目录 提资前期技术预演")
class Iter1RealWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tizi_iter1_")
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
        app.STATE.update(app.session_defaults(), running=False, session_id="iter1-sess", sessions=[])

        self.httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = _HttpClient(f"http://127.0.0.1:{self.port}")

        salt = "11223344556677889900aabbccddeeff"
        protected.write_text(
            app.DATA / "users.json",
            json.dumps({
                "users": [{
                    "username": "tester",
                    "display": "tester",
                    "role": auth.ADMIN,
                    "salt": salt,
                    "hash": auth.password_hash("Iter1Pass!1", salt),
                }]
            }, ensure_ascii=False),
        )
        code, data, _ = self.client.post_json("/api/auth/login", {"username": "tester", "password": "Iter1Pass!1"})
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

    def test_iter1_full_pipeline_and_template_export(self):
        before_mtimes = {str(p): p.stat().st_mtime_ns for p in REAL_WORKSPACE.rglob("*.pdf")}
        feat_cache.clear_memory_cache()

        code, _res, _ = self.client.post_json("/api/tizi/open", {"folder": str(REAL_WORKSPACE)})
        self.assertEqual(code, 200)

        t0 = time.perf_counter()
        code, run_res, _ = self.client.post_json("/api/tizi/run", {"steps": "extract", "sync": True})
        dt = time.perf_counter() - t0
        self.assertEqual(code, 200, run_res)
        self.assertTrue(run_res["ok"], run_res)
        self.assertLess(dt, 10.0, f"全量解析耗时过长：{dt:.2f}s")

        # 1) 检查 /api/tizi/sheets：9 张表摘要、构架剥离与 CHK-01 闭环 214 基
        code, sh_res, _ = self.client.get_json("/api/tizi/sheets")
        self.assertEqual(code, 200)
        self.assertEqual(len(sh_res["sheets"]), 9)
        tower_meta = next(s for s in sh_res["sheets"] if s["name"] == "杆塔明细表")
        self.assertEqual(tower_meta["rows"], 214)
        self.assertEqual(len(sh_res["excluded_gantries"]), 1)
        self.assertEqual(sh_res["excluded_gantries"][0]["tower_id"], "AN0")

        chk_by_code = {c["code"]: c for c in sh_res["workbook_checks"]}
        self.assertTrue(chk_by_code["CHK-01"]["ok"])
        self.assertEqual(chk_by_code["CHK-01"]["actual"], 214.0)

        # 2) 拉取杆塔明细表（默认核心列 19 列 vs 展开默认值列 26 列）
        code, tbl_core, _ = self.client.get_json("/api/tizi/table?sheet=%E6%9D%86%E5%A1%94%E6%98%8E%E7%BB%86%E8%A1%A8&limit=250&show_defaults=0")
        self.assertEqual(code, 200)
        self.assertEqual(tbl_core["total"], 214)
        self.assertEqual(len(tbl_core["columns"]), 19)

        code, tbl_all, _ = self.client.get_json("/api/tizi/table?sheet=%E6%9D%86%E5%A1%94%E6%98%8E%E7%BB%86%E8%A1%A8&limit=20&show_defaults=1")
        self.assertEqual(code, 200)
        self.assertEqual(len(tbl_all["columns"]), 26)

        # 验证 6 项准确性修正
        rows_by_id = {r["cells"]["杆塔号"]["value"]: r["cells"] for r in tbl_core["rows"]}
        self.assertNotIn("AN0", rows_by_id)
        self.assertIn("NA1", rows_by_id)

        # 接地 3 源证据链与 kg->t 换算
        g_matched = sum(1 for c in rows_by_id.values() if c["接地装置.型号"]["value"])
        self.assertGreaterEqual(g_matched, 200)
        na1_gw = rows_by_id["NA1"]["接地装置.常规接地镀锌接地材料重量（t）"]
        self.assertIsNotNone(na1_gw["value"])
        self.assertLess(na1_gw["value"], 1.0)  # t，非 kg
        self.assertGreaterEqual(len(na1_gw["evidences"]), 3)
        self.assertIn("0.001", na1_gw["formula_trace"])

        # 呼高 vs 全高告警（未提供塔头高时必须 pending，不得静默冒充）
        self.assertEqual(rows_by_id["NA1"]["杆塔.全高（m）"]["status"], "pending")

        # 直线塔转角强制留空，耐张塔转十进制度
        straight_towers = [c for c in rows_by_id.values() if "-Z" in str(c["杆塔型号"]["value"] or "")]
        self.assertGreater(len(straight_towers), 50)
        for st_c in straight_towers[:20]:
            self.assertIsNone(st_c["线路转角度数（耐张），直线塔不填"]["value"])

        # 交叉跨越归属大号塔
        cross_matched = [tid for tid, c in rows_by_id.items() if c["交叉跨越.障碍物种类"]["value"]]
        self.assertGreaterEqual(len(cross_matched), 6)
        self.assertIn("NA134", cross_matched)
        self.assertIn("NA4", cross_matched)

        # 3) Space 一键确认 + 撤销修订
        code, conf_res, _ = self.client.post_json("/api/tizi/cell", {
            "sheet": "杆塔明细表", "row": 0, "column": "杆塔.全高（m）", "action": "confirm",
        })
        self.assertEqual(code, 200, conf_res)
        self.assertEqual(conf_res["cell"]["status"], "extract")  # confirmed extract

        code, rev_res, _ = self.client.post_json("/api/tizi/cell/revert", {
            "sheet": "杆塔明细表", "row": 0, "column": "杆塔.全高（m）",
        })
        self.assertEqual(code, 200)
        self.assertEqual(rev_res["reverted"], 1)

        # 4) 导出院标双轨提资单（标准导入版 + 复核溯源版）
        t_exp = time.perf_counter()
        code_std, std_bytes, _ = self.client.request("GET", "/api/tizi/export?mode=standard")
        code_aud, aud_bytes, _ = self.client.request("GET", "/api/tizi/export?mode=audit")
        dt_exp = time.perf_counter() - t_exp
        self.assertEqual((code_std, code_aud), (200, 200))
        self.assertLess(dt_exp, 5.0)

        wb_std = openpyxl.load_workbook(io.BytesIO(std_bytes), data_only=False)
        self.assertEqual(len(wb_std.sheetnames), 13)
        self.assertIn("隐藏表（勿删）", wb_std.sheetnames)
        self.assertIn("杆塔明细表", wb_std.sheetnames)
        ws_t = wb_std["杆塔明细表"]
        # R4..R217 共 214 行，C02=杆塔号，C11=接地型号，C20=杆塔类型
        filled_towers = [ws_t.cell(r, 2).value for r in range(4, 218)]
        self.assertEqual(len([x for x in filled_towers if x]), 214)
        self.assertNotIn("AN0", filled_towers)
        self.assertIsNotNone(ws_t.cell(4, 11).value)  # 接地型号
        self.assertIsNotNone(ws_t.cell(4, 20).value)  # 默认值列保留

        wb_aud = openpyxl.load_workbook(io.BytesIO(aud_bytes), data_only=False)
        self.assertIn("溯源总表", wb_aud.sheetnames)
        self.assertIn("双源校验报告", wb_aud.sheetnames)
        self.assertIn("人工修订记录", wb_aud.sheetnames)
        self.assertIsNotNone(wb_aud["杆塔明细表"].cell(4, 14).comment)

        # 5) 源 PDF 绝对只读
        after_mtimes = {str(p): p.stat().st_mtime_ns for p in REAL_WORKSPACE.rglob("*.pdf")}
        self.assertEqual(before_mtimes, after_mtimes)


if __name__ == "__main__":
    unittest.main()
