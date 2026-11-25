# -*- coding: utf-8 -*-
"""阶段 A 测试：查表、插值不外推、校验超差、字段配对局部重算与快照无损恢复。"""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from tizi import Pipeline, rules, validate

REPO = Path(__file__).resolve().parent


class TiziRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tizi_rules_"))
        defaults = REPO / "tizi" / "defaults"
        shutil.copytree(defaults / "config", self.tmp / "config")
        shutil.copytree(defaults / "data", self.tmp / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lookup_interval_no_extrapolation_and_constant(self):
        """查表未命中标 pending；超区间插值不外推留空且标 pending；条件常量只补空值。"""
        cfg = {
            "rules": [
                {
                    "target": "杆塔类型",
                    "type": "lookup",
                    "table": "data/tower_kind.csv",
                    "key": {"field": "杆塔型号", "column": "type", "strip_prefix": "V3-"},
                    "value": "kind",
                    "confidence": 0.98,
                },
                {
                    "target": "全重",
                    "type": "interval",
                    "table": "data/tower_weight.csv",
                    "key": {"field": "杆塔型号", "column": "type", "strip_prefix": "V3-"},
                    "axis": "呼称高",
                    "from": ["h_min", "w_min"],
                    "to": ["h_max", "w_max"],
                    "clamp": False,
                    "confidence": 0.95,
                },
                {
                    "target": "是否高低腿",
                    "type": "constant",
                    "value": "否",
                    "only_if_empty": True,
                    "where": {"杆塔类型": "钢管杆"},
                    "confidence": 0.90,
                },
            ]
        }
        # 取 tower_weight.csv 第一条真实塔型，构造区间内、超区间、未知塔型三行
        import csv
        with open(self.tmp / "data" / "tower_weight.csv", encoding="utf-8-sig") as f:
            first_w = next(csv.DictReader(f))
        t_type = first_w["type"]
        h_min, h_max = float(first_w["h_min"]), float(first_w["h_max"])
        h_mid = (h_min + h_max) / 2.0
        w_mid_expect = round((float(first_w["w_min"]) + float(first_w["w_max"])) / 2.0, 2)

        with open(self.tmp / "data" / "tower_kind.csv", encoding="utf-8-sig") as f:
            pole = next(r for r in csv.DictReader(f) if r["kind"] == "钢管杆")

        rows = [
            {"cells": {}, "out": {"杆塔型号": t_type, "呼称高": h_mid}, "prov": {}},
            {"cells": {}, "out": {"杆塔型号": t_type, "呼称高": h_max + 15.0}, "prov": {}},
            {"cells": {}, "out": {"杆塔型号": "UNKNOWN-999", "呼称高": 24.0}, "prov": {}},
            {"cells": {}, "out": {"杆塔型号": pole["type"], "呼称高": 24.0}, "prov": {}},
        ]

        stats = rules.apply_rules(rows, cfg, str(self.tmp))
        self.assertEqual(len(stats), 3)

        # 行 0：区间内插值命中
        self.assertEqual(rows[0]["out"]["全重"], w_mid_expect)
        self.assertEqual(rows[0]["prov"]["全重"]["status"], "confirmed")

        # 行 1：超区间不外推，值为 None 且 status=pending
        self.assertIsNone(rows[1]["out"]["全重"])
        self.assertEqual(rows[1]["prov"]["全重"]["status"], "pending")
        self.assertIn("不外推", rows[1]["prov"]["全重"]["note"])

        # 行 2：查表未命中，值为 None 且 status=pending
        self.assertIsNone(rows[2]["out"]["杆塔类型"])
        self.assertEqual(rows[2]["prov"]["杆塔类型"]["status"], "pending")

        # 行 3：钢管杆条件常量补全
        self.assertEqual(rows[3]["out"]["杆塔类型"], "钢管杆")
        self.assertEqual(rows[3]["out"]["是否高低腿"], "否")

    def test_validate_reports_over_tolerance_without_mutating_expect(self):
        """校验能如实报出超差，且不改写配置基准。"""
        cfg = {
            "crosschecks": [
                {"name": "角钢塔耗钢合计", "type": "sum", "field": "全重",
                 "where": {"杆塔类型": "角钢塔"}, "expect": 100.0, "tolerance": 0.02, "unit": "t"},
                {"name": "钢管杆耗钢合计", "type": "sum", "field": "全重",
                 "where": {"杆塔类型": "钢管杆"}, "expect": 100.0, "tolerance": 0.05, "unit": "t"},
                {"name": "塔位总数", "type": "count", "expect": 2, "tolerance": 0, "unit": "基"},
            ]
        }
        before = copy.deepcopy(cfg)
        rows = [
            {"out": {"杆塔类型": "角钢塔", "全重": 99.0}},
            {"out": {"杆塔类型": "钢管杆", "全重": 90.0}},
            {"out": {"杆塔类型": "角钢塔", "全重": None}},
        ]
        res = validate.run_checks(rows, cfg)
        self.assertEqual(cfg, before)
        by_name = {c["name"]: c for c in res}
        self.assertTrue(by_name["角钢塔耗钢合计"]["ok"])
        self.assertFalse(by_name["钢管杆耗钢合计"]["ok"])
        self.assertAlmostEqual(by_name["钢管杆耗钢合计"]["rate"], 0.1, places=4)
        self.assertFalse(by_name["塔位总数"]["ok"])
        self.assertEqual(by_name["塔位总数"]["actual"], 3.0)

    def test_mapping_recalc_preserves_manual_edits_and_snapshot_roundtrip(self):
        """改字段配对局部重算不冲掉已有人工修订，且快照序列化/反序列化无损。"""
        pipe = Pipeline(config_dir=str(self.tmp / "config"), project_root=str(self.tmp))
        pipe.layout = {
            "layout_id": "unit_layout",
            "source": "struct",
            "body_top": 0,
            "row_pattern": r"^N\d+$",
            "row_key": "c1",
            "columns": [
                {"key": "c1", "label": "杆塔号", "x0": 0, "x1": 50},
                {"key": "c2", "label": "杆塔型号", "x0": 50, "x1": 120},
                {"key": "c3", "label": "呼称高", "x0": 120, "x1": 180},
                {"key": "c4", "label": "备注列", "x0": 180, "x1": 260},
            ],
        }
        pipe.mapping_base = {
            "template": "unit",
            "layout": "unit_layout",
            "columns": [
                {"target": "杆塔号", "from": "c1"},
                {"target": "杆塔型号", "from": "c2"},
                {"target": "呼称高", "from": "c3", "cast": "float"},
            ],
        }
        pipe._apply_mapping_ov()
        pipe.rows = [
            {"cells": {"c1": "N1", "c2": "2E1-Z1", "c3": "24", "c4": "-1.5"},
             "cell_bbox": {"c1": [1, 1, 20, 10], "c3": [125, 1, 150, 10]},
             "page": 1, "bbox": [1, 1, 200, 10]},
        ]
        pipe.step3_mapping()
        pipe.step4_rules()
        pipe.step5_validate()

        # 人工修订第 0 行呼称高：24 -> 30
        edit_res = pipe.apply_edit(0, "呼称高", "30")
        self.assertTrue(edit_res["changed"])
        self.assertEqual(pipe.rows[0]["out"]["呼称高"], 30)

        # 改配对（把『定位高差』配到 c4）触发局部重算
        ok, _msg, state = pipe.apply_mapping("set", target="定位高差", key="c4")
        self.assertTrue(ok)
        self.assertIsNotNone(state)
        self.assertEqual(pipe.rows[0]["out"]["定位高差"], -1.5)
        # 已有人工修订必须保留
        self.assertEqual(pipe.rows[0]["out"]["呼称高"], 30)
        self.assertIn((0, "呼称高"), pipe.overrides)

        # 快照序列化 -> 新 Pipeline 实例反序列化 -> 可撤销恢复原值 24.0
        import json
        snap = json.loads(json.dumps(pipe.to_snapshot(), ensure_ascii=False))
        pipe2 = Pipeline(config_dir=str(self.tmp / "config"), project_root=str(self.tmp))
        pipe2.load_snapshot(snap)
        self.assertEqual(pipe2.rows[0]["out"]["呼称高"], 30)
        self.assertEqual(pipe2.revert_edits(0, "呼称高"), 1)
        self.assertEqual(pipe2.rows[0]["out"]["呼称高"], 24.0)


if __name__ == "__main__":
    unittest.main()
