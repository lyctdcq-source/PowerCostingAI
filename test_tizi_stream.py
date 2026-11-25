# -*- coding: utf-8 -*-
"""v0.15.0 中间栏流式解析事件总线、持久化回放与 SSE 接口自动化回归测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from tizi.job import TiziJob

REAL_WORKSPACE = Path(r"E:\提资项目\提资前期技术预演")


class TestTiziStreamBus(unittest.TestCase):
    @unittest.skipUnless(REAL_WORKSPACE.is_dir(), "缺少真实工程样本目录")
    def test_full_extract_emits_structured_stream_and_persists(self):
        with tempfile.TemporaryDirectory(prefix="tizi_stream_") as td:
            job = TiziJob(Path(td) / "job")
            job.open_folder(str(REAL_WORKSPACE))
            res = job.run_steps(steps="extract", async_run=False)
            self.assertTrue(res["ok"])

            trace = job.status_payload()["stream_trace"]
            self.assertTrue(trace["run_id"].startswith("run-"))
            self.assertFalse(trace["running"])
            self.assertEqual(trace["status"], "ready")
            self.assertEqual(len(trace["steps_plan"]), 7)

            evs = trace["events"]
            self.assertGreater(len(evs), 20)
            seqs = [e["seq"] for e in evs]
            self.assertEqual(seqs, sorted(seqs))
            self.assertEqual(len(seqs), len(set(seqs)))

            kinds = [e["kind"] for e in evs]
            self.assertEqual(kinds[0], "run_start")
            self.assertEqual(kinds[-1], "run_done")
            self.assertIn("ticker", kinds)
            self.assertIn("finding", kinds)
            self.assertIn("check_result", kinds)

            # 全部 9 张提资表均应发出 sheet_ready 事件
            lit_sheets = {e["sheet"] for e in evs if e["kind"] == "sheet_ready"}
            expected_sheets = {
                "基本信息", "杆塔明细表", "基础明细表", "基础库",
                "串型汇总表", "勘测信息一览表",
                "结构设备材料表", "电气设备材料表", "光缆设备材料表",
            }
            self.assertEqual(lit_sheets, expected_sheets)

            # 所有 7 个计划阶段均完成
            done_stages = {e["stage"] for e in evs if e["kind"] == "stage_done"}
            self.assertEqual(done_stages, {p["id"] for p in trace["steps_plan"]})

            # 增量等待接口：传 last_seq 应立即返回空且 running=False
            more, running, st = job.wait_stream_events(since_seq=trace["last_seq"], timeout=0.1)
            self.assertEqual(more, [])
            self.assertFalse(running)
            self.assertEqual(st, "ready")

            # 持久化与跨实例无损回放（落盘剔除中间态 ticker，保留全部高价值事件）
            reloaded = TiziJob(Path(td) / "job")
            r_trace = reloaded.status_payload()["stream_trace"]
            self.assertEqual(r_trace["run_id"], trace["run_id"])
            self.assertTrue(all(e["kind"] != "ticker" for e in r_trace["events"]))
            self.assertEqual(
                {e["sheet"] for e in r_trace["events"] if e["kind"] == "sheet_ready"},
                expected_sheets,
            )

            # 单步重跑（validate）也产生干净的流式轨迹
            r2 = reloaded.run_steps(steps=["validate"], async_run=False)
            self.assertTrue(r2["ok"])
            t2 = reloaded.status_payload()["stream_trace"]
            self.assertNotEqual(t2["run_id"], trace["run_id"])
            self.assertEqual([p["id"] for p in t2["steps_plan"]], ["validate"])
            self.assertTrue(any(e["kind"] == "check_result" for e in t2["events"]))
            self.assertEqual(t2["events"][-1]["kind"], "run_done")

    @unittest.skipUnless(REAL_WORKSPACE.is_dir(), "缺少真实工程样本目录")
    def test_audit_fixes_provenance_gantry_and_incomplete_checks(self):
        with tempfile.TemporaryDirectory(prefix="tizi_audit_") as td:
            job = TiziJob(Path(td) / "job")
            job.open_folder(str(REAL_WORKSPACE))
            job.run_steps(steps="extract", async_run=False)
            st = job.status_payload()

            # 1) 基本信息汽车运距精准定位第16行「16）运距：小运距离0.52km，工地运距15m」，且 role 不含重复页码 P39
            basic_rows = job.pipe.store.get_sheet("基本信息")["rows"]
            car_ev = basic_rows[0]["prov"]["汽车运距（km）"]["evidences"][0]
            self.assertEqual(car_ev["page"], 39)
            self.assertNotRegex(car_ev["role"], r"P39")
            self.assertIsNotNone(car_ev["bbox"])
            self.assertGreaterEqual(car_ev["bbox"][1], 600.0)
            self.assertLessEqual(car_ev["bbox"][3], 635.0)

            # 2) 例外队列不得包含已剥离构架 AN0，且每项携带 V2 sheet 名
            pend = st["exceptions"]["pending"]
            self.assertGreater(len(pend), 0)
            self.assertFalse(any(p.get("tower") == "AN0" for p in pend))
            self.assertTrue(all(p.get("sheet") for p in pend))

            # 3) CHK-04 与 CHK-08 标注 kind=incomplete、覆盖率与明确原因说明
            chks = {c["code"]: c for c in st["workbook_checks"]}
            self.assertEqual(chks["CHK-04"]["kind"], "incomplete")
            self.assertIn("NA117", chks["CHK-04"]["note"])
            self.assertEqual(chks["CHK-08"]["kind"], "incomplete")
            self.assertLess(chks["CHK-08"]["coverage"], 0.9)
            self.assertIn("50/214", chks["CHK-08"]["note"])

            # 4) 交付打包导出：8 份产物完整且 zero warnings
            bundle = job.pipe.export_bundle()
            self.assertEqual(bundle.get("warnings") or [], [])
            self.assertEqual(len(bundle["names"]), 8)
            self.assertEqual(len(bundle["files"]), 8)


if __name__ == "__main__":
    unittest.main()
