# -*- coding: utf-8 -*-
"""阶段 B 自动化测试：鉴权、路径防越权、会话/检查点/运行态隔离、跨会话恢复与真实工作空间全链路回归。"""
import copy
import csv
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import openpyxl

import auth
import server as app
from test_tizi_parse import _make_sample_t2_pdf

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


class _HttpClient:
    def __init__(self, base_url):
        self.base = base_url
        self.cookie = ""

    def request(self, method, path, body=None, headers=None, include_demo=True, include_cookie=True):
        hdrs = {}
        if include_demo and method == "POST":
            hdrs["X-Agent-Demo"] = "1"
            hdrs["Content-Type"] = "application/json"
        if include_cookie and self.cookie:
            hdrs["Cookie"] = self.cookie
        if headers:
            hdrs.update(headers)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else (b"{}" if method == "POST" else None)
        req = Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read()
                sc = resp.headers.get("Set-Cookie")
                if sc:
                    self.cookie = sc.split(";", 1)[0]
                return resp.status, raw, dict(resp.headers)
        except HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def get_json(self, path, **kw):
        code, raw, hdrs = self.request("GET", path, **kw)
        return code, json.loads(raw.decode("utf-8")), hdrs

    def post_json(self, path, body=None, **kw):
        code, raw, hdrs = self.request("POST", path, body=body or {}, **kw)
        return code, json.loads(raw.decode("utf-8")), hdrs


class TiziJobAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tizi_job_test_")
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
            session_id="tizi-sess-1",
            session_created="2026-11-24 12:00:00",
            sessions=[],
            drawing=[{"field": "接地装置套数", "value": "10", "unit": "套", "source": "测试", "confidence": 0.9, "confirmed": False}],
        )

        self.httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = _HttpClient(f"http://127.0.0.1:{self.port}")

        # 在临时数据目录写入测试专用账号并通过 /api/auth/login 登录
        import protected
        salt = "00112233445566778899aabbccddeeff"
        protected.write_text(
            app.DATA / "users.json",
            json.dumps({
                "users": [{
                    "username": "tester",
                    "display": "tester",
                    "role": auth.ADMIN,
                    "salt": salt,
                    "hash": auth.password_hash("UnitTestPass!1", salt),
                }]
            }, ensure_ascii=False),
        )
        code, data, _ = self.client.post_json("/api/auth/login", {"username": "tester", "password": "UnitTestPass!1"})
        self.assertEqual(code, 200, data)

        # 小型夹具图纸目录
        self.fixture_dir = self.tmp_root / "夹具施工图"
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        _make_sample_t2_pdf(self.fixture_dir / "sample.pdf")

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

    def test_auth_and_header_guards(self):
        """未登录返回 401；缺 X-Agent-Demo 请求头的 POST 被拒绝。"""
        code, _, _ = self.client.get_json("/api/tizi/status", include_cookie=False)
        self.assertEqual(code, 401)
        code, _, _ = self.client.post_json("/api/tizi/open", {"folder": str(self.fixture_dir)}, include_cookie=False)
        self.assertEqual(code, 401)
        code, data, _ = self.client.post_json("/api/tizi/open", {"folder": str(self.fixture_dir)}, include_demo=False)
        self.assertEqual(code, 400)
        self.assertIn("仅允许工作台本地请求", data["error"])

    def test_path_traversal_rejected(self):
        """越权路径（.. / 绝对路径 / 下载逃逸）一律拒绝返回 403。"""
        code, _, _ = self.client.post_json("/api/tizi/open", {"folder": str(self.fixture_dir)})
        self.assertEqual(code, 200)

        for bad_path in ("../data/state.json", r"C:\Windows\win.ini", "/etc/passwd", "sub/../../secret.pdf"):
            c1, _, _ = self.client.get_json(f"/api/tizi/drawing/probe?path={quote(bad_path)}")
            self.assertEqual(c1, 403, f"probe should reject {bad_path}")
            c2, _, _ = self.client.get_json(f"/api/tizi/drawing/region?path={quote(bad_path)}&page=0&x0=0&y0=0&x1=10&y1=10")
            self.assertEqual(c2, 403, f"region should reject {bad_path}")

        c3, _, _ = self.client.get_json(f"/api/tizi/download?file={quote('../job.json')}")
        self.assertEqual(c3, 403)

    def test_running_checkpoint_and_session_isolation(self):
        """与物资 running 隔离、检查点恢复不冲掉提资、仅提资会话可存档并跨重启恢复。"""
        # 1) 物资评审 STATE['running']=True 时，提资仍可打开与查询状态
        app.STATE["running"] = True
        code, data, _ = self.client.post_json("/api/tizi/open", {"folder": str(self.fixture_dir)})
        self.assertEqual(code, 200, data)
        code, st, _ = self.client.get_json("/api/tizi/status")
        self.assertEqual(code, 200)
        self.assertEqual(st["tizi"]["folder"], str(self.fixture_dir.resolve()))
        app.STATE["running"] = False

        # 2) 物资 checkpoint 恢复不影响提资状态
        app.STATE["project"] = "物资测试"
        cid = app.checkpoint("测试检查点")
        # 修改物资字段后恢复检查点，提资 folder 与 job.json 仍在
        app.STATE["stage"] = 2
        snap = json.loads((app.DATA / f"{cid}.json").read_text(encoding="utf-8"))
        self.assertNotIn("tizi", snap)
        code, _, _ = self.client.post_json("/api/restore", {"id": cid})
        self.assertEqual(code, 200)
        self.assertEqual(app.STATE["tizi"]["folder"], str(self.fixture_dir.resolve()))

        # 3) 提资运行中拒绝切换/删除会话，且不修改 STATE['running']
        job = app.tizi_job()
        job._running = True
        try:
            self.assertFalse(app.STATE["running"])
            with self.assertRaisesRegex(ValueError, "正在执行"):
                app.start_session()
            with self.assertRaisesRegex(ValueError, "正在执行"):
                app.delete_session(app.STATE["session_id"])
            c409, _, _ = self.client.post_json("/api/tizi/run", {"steps": "extract"})
            self.assertEqual(c409, 409)
        finally:
            job._running = False

        # 4) 仅含提资（无 messages、无 dataset）时 session_empty 为 False，可存档、切走再切回恢复
        app.STATE["messages"] = []
        app.STATE["dataset"] = None
        self.assertFalse(app.session_empty())
        first_sid = app.STATE["session_id"]
        app.start_session()
        second_sid = app.STATE["session_id"]
        self.assertNotEqual(first_sid, second_sid)
        self.assertEqual(app.STATE["tizi"]["folder"], "")

        # 清空内存缓存模拟重启，再打开第一个会话
        app.TIZI_JOBS.clear()
        app.open_session(first_sid)
        self.assertEqual(app.STATE["tizi"]["folder"], str(self.fixture_dir.resolve()))

        # 删除会话同时清理 DATA/tizi/<sid>
        tizi_dir = app.DATA / "tizi" / first_sid
        self.assertTrue(tizi_dir.is_dir())
        app.delete_session(first_sid)
        self.assertFalse(tizi_dir.exists())

    def test_legacy_routes_unchanged(self):
        """原有 /api/state 与 /api/drawing/review 返回结构不变。"""
        code, st, _ = self.client.get_json("/api/state")
        self.assertEqual(code, 200)
        for key in ("project", "stage", "rows", "drawing", "drawing_conflicts", "sessions", "tizi"):
            self.assertIn(key, st)
        code, res, _ = self.client.post_json("/api/drawing/review", {"index": 0, "value": "12", "note": "核对"})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(app.STATE["drawing"][0]["value"], "12")
        self.assertTrue(app.STATE["drawing"][0]["confirmed"])

    @unittest.skipUnless(REAL_WORKSPACE.is_dir(), "未找到真实测试工作空间：提资前期技术预演")
    def test_real_workspace_full_http_e2e_and_source_mtime_unchanged(self):
        """在真实测试工作空间『提资前期技术预演』上跑通全套 HTTP 接口、双版本导出与源码只读验证。"""
        # 记录源目录所有 PDF 修改时间
        before_mtimes = {
            str(p): p.stat().st_mtime_ns
            for p in REAL_WORKSPACE.rglob("*.pdf")
        }
        self.assertGreater(len(before_mtimes), 0)

        # 1) 绑定目录
        code, data, _ = self.client.post_json("/api/tizi/open", {"folder": str(REAL_WORKSPACE)})
        self.assertEqual(code, 200, data)

        # 2) 后台启动 extract (discover -> validate)，轮询至完成
        code, run_res, _ = self.client.post_json("/api/tizi/run", {"steps": "extract"})
        self.assertEqual(code, 200, run_res)
        self.assertTrue(run_res["started"])

        deadline = time.time() + 60
        st_payload = None
        while time.time() < deadline:
            code, st_payload, _ = self.client.get_json("/api/tizi/status")
            self.assertEqual(code, 200)
            if st_payload["tizi"]["status"] in ("ready", "error"):
                break
            time.sleep(0.15)
        self.assertEqual(st_payload["tizi"]["status"], "ready", st_payload["tizi"].get("error"))

        # 3) 断言龙泉山自动配置回归基准
        sm = st_payload["tizi"]["summary"]
        self.assertEqual(sm["rows"], 215)
        self.assertEqual(sm["filled"], 2174)
        self.assertEqual(sm["total_cells"], 2795)
        self.assertAlmostEqual(sm["coverage"], 77.8, places=1)

        checks = {c["name"]: c for c in st_payload["checks"]}
        self.assertAlmostEqual(checks["角钢塔耗钢合计"]["actual"], 3462.05, places=2)
        self.assertTrue(checks["角钢塔耗钢合计"]["ok"])
        self.assertAlmostEqual(checks["钢管杆耗钢合计"]["actual"], 290.4, places=2)
        self.assertFalse(checks["钢管杆耗钢合计"]["ok"])
        self.assertEqual(checks["塔位总数"]["actual"], 215.0)
        self.assertFalse(checks["塔位总数"]["ok"])

        # 4) 分页与筛选查询
        code, tbl, _ = self.client.get_json("/api/tizi/table?offset=0&limit=50")
        self.assertEqual(code, 200)
        self.assertEqual(tbl["total"], 215)
        self.assertEqual(len(tbl["rows"]), 50)
        self.assertIn("呼称高", tbl["targets"])

        code, pend_tbl, _ = self.client.get_json("/api/tizi/table?filter=pending&limit=50")
        self.assertEqual(code, 200)
        self.assertGreater(pend_tbl["filtered_total"], 0)

        # 5) 改一个呼称高 + 图纸渲染与框选写入另一格
        orig_cell_code, orig_cell, _ = self.client.get_json(f"/api/tizi/cell?row=1&column={quote('呼称高')}")
        self.assertEqual(orig_cell_code, 200)
        orig_val = orig_cell["value"]
        new_val = 99
        self.assertNotEqual(orig_val, new_val)

        code, edit_res, _ = self.client.post_json("/api/tizi/cell", {"row": 1, "column": "呼称高", "value": str(new_val)})
        self.assertEqual(code, 200)
        self.assertTrue(edit_res["changed"])

        # 重解析在有人工修订且未 confirm 时必须返回 need_confirm=True
        code, need_res, _ = self.client.post_json("/api/tizi/run", {"steps": ["parse"]})
        self.assertEqual(code, 200)
        self.assertTrue(need_res.get("need_confirm"))

        # 图纸列表、探测、渲染 PNG、框选读取并写入
        code, dlist, _ = self.client.get_json("/api/tizi/drawing/list")
        self.assertEqual(code, 200)
        self.assertGreater(len(dlist["files"]), 0)
        rel_pdf = orig_cell["prov"]["rel"]
        self.assertTrue(rel_pdf.lower().endswith(".pdf"))

        code, probe_res, _ = self.client.get_json(f"/api/tizi/drawing/probe?path={quote(rel_pdf)}")
        self.assertEqual(code, 200)
        self.assertGreater(probe_res["n_pages"], 0)

        code, png_bytes, hdrs = self.client.request("GET", f"/api/tizi/drawing/page?path={quote(rel_pdf)}&page=0&dpi=110")
        self.assertEqual(code, 200)
        self.assertTrue(png_bytes.startswith(b"\x89PNG"))

        bbox = orig_cell["prov"]["bbox"]
        pg0 = int(orig_cell["prov"]["page"]) - 1
        code, reg_res, _ = self.client.get_json(
            f"/api/tizi/drawing/region?path={quote(rel_pdf)}&page={pg0}&x0={bbox[0]}&y0={bbox[1]}&x1={bbox[2]}&y1={bbox[3]}"
        )
        self.assertEqual(code, 200)
        self.assertTrue(reg_res["ok"])

        code, draw_apply, _ = self.client.post_json("/api/tizi/drawing/apply", {
            "row": 2, "column": "呼称高", "value": "88",
            "path": rel_pdf, "page": pg0, "bbox": bbox, "text": reg_res.get("text", ""),
        })
        self.assertEqual(code, 200)
        self.assertEqual(draw_apply["cell"]["prov"]["method"], "drawing_select")

        # 6) 字段配对查询与确认
        code, mp, _ = self.client.get_json("/api/tizi/mapping")
        self.assertEqual(code, 200)
        self.assertGreater(len(mp["fields"]), 0)
        code, mp_post, _ = self.client.post_json("/api/tizi/mapping", {"action": "confirm", "target": "呼称高"})
        self.assertEqual(code, 200, mp_post)
        # 人工修订不能被改配对冲掉
        _, check_cell, _ = self.client.get_json(f"/api/tizi/cell?row=1&column={quote('呼称高')}")
        self.assertEqual(check_cell["value"], new_val)

        # 7) 导出双版本并校验人工版 vs 机器版 vs 修订清单
        code, exp, _ = self.client.post_json("/api/tizi/export")
        self.assertEqual(code, 200, exp)
        self.assertEqual(exp["n_revisions"], 2)

        c_m, man_bytes, _ = self.client.request("GET", "/api/tizi/download?file=result.xlsx")
        c_k, mac_bytes, _ = self.client.request("GET", "/api/tizi/download?file=result_machine.xlsx")
        c_r, rev_bytes, _ = self.client.request("GET", "/api/tizi/download?file=revision_log.csv")
        self.assertEqual((c_m, c_k, c_r), (200, 200, 200))

        wb_man = openpyxl.load_workbook(io.BytesIO(man_bytes))
        wb_mac = openpyxl.load_workbook(io.BytesIO(mac_bytes))
        ws_man, ws_mac = wb_man["提资结果"], wb_mac["提资结果"]
        headers = [ws_man.cell(1, j).value for j in range(1, ws_man.max_column + 1)]
        h_col = headers.index("呼称高") + 1
        # row=1 对应 Excel 第 3 行（第 1 行表头，第 2 行 row=0）
        self.assertEqual(ws_man.cell(3, h_col).value, new_val)
        self.assertEqual(str(ws_mac.cell(3, h_col).value), str(orig_val))
        self.assertIn("修订清单", wb_man.sheetnames)
        self.assertNotIn("修订清单", wb_mac.sheetnames)

        rev_rows = list(csv.reader(io.StringIO(rev_bytes.decode("utf-8-sig"))))
        self.assertEqual(len(rev_rows), 3)  # 表头 + 2 条修订（改值 + 图纸框选）
        kinds = {r[5] for r in rev_rows[1:]}
        self.assertEqual(kinds, {"改值", "图纸框选"})

        # 8) 撤销全部修订后重导，人工版与机器版完全一致
        code, rev_res, _ = self.client.post_json("/api/tizi/cell/revert")
        self.assertEqual(code, 200)
        self.assertEqual(rev_res["reverted"], 2)
        code, exp2, _ = self.client.post_json("/api/tizi/export")
        self.assertEqual(code, 200)
        self.assertEqual(exp2["n_revisions"], 0)
        _, man_csv, _ = self.client.request("GET", "/api/tizi/download?file=result.csv")
        _, mac_csv, _ = self.client.request("GET", "/api/tizi/download?file=result_machine.csv")
        self.assertEqual(man_csv, mac_csv)

        # 9) 确认重解析（confirm=True）不会因旧 targets 报 KeyError，会清空旧 exports，且对话通知不含目录/工程原名
        code, reparse_res, _ = self.client.post_json(
            "/api/tizi/run",
            {"steps": ["parse", "mapping", "rules", "validate"], "confirm": True, "sync": True, "notify_chat": True},
        )
        self.assertEqual(code, 200, reparse_res)
        self.assertTrue(reparse_res["ok"])
        code, st_after, _ = self.client.get_json("/api/tizi/status")
        self.assertEqual(code, 200)
        self.assertEqual(st_after["tizi"]["summary"]["exports"], 0)
        self.assertTrue(app.STATE["messages"])
        last_msg = app.STATE["messages"][-1]["text"]
        self.assertNotIn(REAL_WORKSPACE.name, last_msg)

        # 10) 源施工图目录内所有 PDF 修改时间保持不变
        after_mtimes = {
            str(p): p.stat().st_mtime_ns
            for p in REAL_WORKSPACE.rglob("*.pdf")
        }
        self.assertEqual(before_mtimes, after_mtimes)


if __name__ == "__main__":
    unittest.main()
