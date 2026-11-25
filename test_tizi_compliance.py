# -*- coding: utf-8 -*-
"""v0.13.0 电力行业专业合规与定额字典对齐专项测试：
1. 跨表外键一致性：全部业务表的「线路段名称」严格落在《基本信息》线路段枚举集合内；
2. 院标母版《隐藏表（勿删）》6 类下拉字典 100% 精确合规（跨越、防振锤避雷线、基础类型、土质岩石爆破、单位、#REF! 全清）；
3. 电力造价口径纠偏：施工损耗严禁填入「试验或备用量」、导地线 m→km 换算、排水沟 m/m³ 量纲拦截、灌注桩充盈量防重计；
4. 输电结构物理规则：数据驱动 4 腿识别修复 NA4 D腿缺失、通用设计塔头高自动算全高、CHK-07 腿数完整性与 CHK-08 跨表守恒。
"""
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import openpyxl

from tizi import template_writer
from tizi.extractors.grounding import pick_module
from tizi.pipeline import Pipeline

REPO = Path(__file__).resolve().parent
REAL_WORKSPACE = Path(
    os.environ.get("TIZI_TEST_WORKSPACE")
    or str(REPO.parent / "提资前期技术预演")
)


class PowerComplianceUnitTests(unittest.TestCase):
    def test_grounding_module_selection_no_hardcoded_letter(self):
        # 即使塔号前缀为任意编号（如 #15 / N99），仅凭标准塔型编码即可准确判定电压等级与回路
        self.assertEqual(pick_module("", "V3-2E1Y5-Z2", 33), "2D-Z36")
        self.assertEqual(pick_module("", "V3-2E1Y7-J4", 30), "2D-N36")
        self.assertEqual(pick_module("", "V3-1B2Y7-J2", 24), "1S-N30")
        self.assertEqual(pick_module("", "V3-1B1Y4-J4", 24), "1D-N30")
        self.assertEqual(pick_module("", "V3-1B2Y7-Z2", 30), "1S-Z54")


@unittest.skipUnless(REAL_WORKSPACE.is_dir(), "需要真实施工图样本目录 提资前期技术预演")
class PowerComplianceRealTests(unittest.TestCase):
    def test_full_domain_compliance_and_hidden_enums(self):
        with tempfile.TemporaryDirectory(prefix="tizi_v013_") as td:
            td_path = Path(td)
            shutil.copytree(REPO / "tizi" / "defaults" / "config", td_path / "config")
            shutil.copytree(REPO / "tizi" / "defaults" / "data", td_path / "data")
            pipe = Pipeline(
                config_dir=str(td_path / "config"),
                data_root=str(REAL_WORKSPACE),
                out_dir=str(td_path / "out"),
                project_root=str(td_path),
            )
            pipe.step0_discover()
            pipe.step1_carrier()
            pipe.step2_parse()
            pipe.step3_mapping()
            pipe.step4_rules()
            pipe.step5_validate()

            # 读取母版隐藏字典合法枚举
            wb_tpl = openpyxl.load_workbook(str(template_writer.DEFAULT_TEMPLATE_PATH), data_only=True)
            ws_h = wb_tpl["隐藏表（勿删）"]
            rng = lambda col, r1, r2: {ws_h[f"{col}{r}"].value for r in range(r1, r2 + 1) if ws_h[f"{col}{r}"].value is not None}
            valid_damper_kind = rng("A", 13, 15)     # {'导线', '避雷线', '光缆'}
            valid_obs = rng("B", 13, 31)
            valid_circ = rng("C", 13, 19)
            valid_fdn_type = rng("D", 34, 42)
            valid_soil = rng("G", 1, 10)
            valid_cross_method = {
                "脚手架普通跨越", "脚手架带电跨越", "多柱组合式跨越",
                "带羊角横担柱式跨越", "无跨越架索道封网式跨越", "穿越电力线",
            }
            wb_tpl.close()

            # 1) 《基本信息》4 段电压/亘长准确（220kV×2 + 110kV×2，总亘长 73.358 km）
            basic_rows = pipe.store.get_sheet("基本信息")["rows"]
            self.assertEqual(len(basic_rows), 4)
            self.assertEqual([r["out"]["电压等级"] for r in basic_rows], ["220kV", "220kV", "110kV", "110kV"])
            self.assertAlmostEqual(basic_rows[0]["out"]["线路亘长（km）"], 49.237, places=3)
            self.assertIsNotNone(basic_rows[0]["out"]["地线2型号"])
            chks = {c["code"]: c for c in pipe.workbook_checks}
            self.assertAlmostEqual(chks["CHK-02"]["actual"], 73.358, places=2)

            # 2) 跨表外键一致性：全部业务表的 线路段名称 必须严格属于 基本信息!D4:D15
            valid_segs = {r["out"]["线路段名称"] for r in basic_rows}
            for sname in ("杆塔明细表", "基础明细表", "串型汇总表", "电气设备材料表", "光缆设备材料表"):
                for r in pipe.store.get_sheet(sname)["rows"]:
                    seg = r["out"].get("线路段名称")
                    self.assertIn(seg, valid_segs, f"{sname} 存在非法线路段外键：{seg} not in {valid_segs}")

            # 3) 杆塔明细表：跨越枚举合规 + 标准塔型自动算全高（NA2 确认，特殊终端塔 NA1 待核）
            towers = {r["out"]["杆塔号"]: r for r in pipe.store.get_sheet("杆塔明细表")["rows"]}
            for tid, r in towers.items():
                o = r["out"]
                if o["交叉跨越.障碍物种类"]:
                    self.assertIn(o["交叉跨越.障碍物种类"], valid_obs)
                if o["交叉跨越.回路类型"]:
                    self.assertIn(o["交叉跨越.回路类型"], valid_circ)
                if o["交叉跨越.跨越方式"]:
                    self.assertIn(o["交叉跨越.跨越方式"], valid_cross_method)
            self.assertEqual(towers["NA1"]["prov"]["杆塔.全高（m）"]["status"], "pending")
            self.assertEqual(towers["NA2"]["prov"]["杆塔.全高（m）"]["status"], "confirmed")
            self.assertGreater(towers["NA2"]["out"]["杆塔.全高（m）"], 40.0)  # 呼高 30 + 塔头高 16.5 = 46.5

            # 4) 串型汇总表：防振锤类型合规（避雷线，不得出现「地线」）
            for r in pipe.store.get_sheet("串型汇总表")["rows"]:
                dk = r["out"].get("防振锤.类型")
                if dk:
                    self.assertIn(dk, valid_damper_kind)

            # 5) 勘测信息一览表：土质类型 100% 合规（岩石(爆破) 等）
            for r in pipe.store.get_sheet("勘测信息一览表")["rows"]:
                st = r["out"].get("基础土质.土质类型")
                if st:
                    self.assertIn(st, valid_soil)

            # 6) 电气材料表：导地线 m→km 换算 + 施工损耗严禁填入 试验或备用量
            elec_rows = pipe.store.get_sheet("电气设备材料表")["rows"]
            cond_row = next(r for r in elec_rows if "JL/LB20A-500/45" in str(r["out"].get("规格型号") or ""))
            self.assertEqual(cond_row["out"]["单位"], "km")
            self.assertAlmostEqual(cond_row["out"]["设计用量（不含损耗）"], 310.073, places=2)
            self.assertIsNone(cond_row["out"]["试验或备用量"])

            # 7) 结构材料表：排水沟 60m 量纲拦截标 pending，灌注桩充盈量不混入本体砼
            struct_by_id = {r["row_id"]: r for r in pipe.store.get_sheet("结构设备材料表")["rows"]}
            self.assertIsNone(struct_by_id["R15"]["out"]["数量（不含损耗）"])
            self.assertEqual(struct_by_id["R15"]["prov"]["数量（不含损耗）"]["status"], "pending")
            self.assertIn("量纲拦截", struct_by_id["R15"]["prov"]["数量（不含损耗）"]["note"])
            # 8783.945 - 184.211 (充盈量) = 8599.734
            self.assertAlmostEqual(struct_by_id["R11"]["out"]["数量（不含损耗）"], 8599.734, places=2)

            # 8) 基础明细表 NA4 四腿齐全 + 基础库枚举合规与 GZZ1090 错列 Bug 修复 + CHK-07/08
            fdn_by_tid = {r["out"]["杆塔号"]: r["out"] for r in pipe.store.get_sheet("基础明细表")["rows"]}
            self.assertEqual(fdn_by_tid["NA4"]["基础型号.A腿"], "WK1290")
            self.assertEqual(fdn_by_tid["NA4"]["基础型号.D腿"], "WK1295(9.0)")

            lib_by_model = {r["out"]["基础型号"]: r["out"] for r in pipe.store.get_sheet("基础库")["rows"]}
            for m, lo in lib_by_model.items():
                self.assertIn(lo["基础类型"], valid_fdn_type, f"非法基础类型：{m} -> {lo['基础类型']}")
            self.assertEqual(lib_by_model["GZZ1090"]["灌注桩.桩直径(m)"], 1)
            self.assertEqual(lib_by_model["GZZ1090"]["灌注桩.桩长(m)"], 9)
            self.assertIsNone(lib_by_model["GZZ1090"]["重力式.基础深度(m)"])

            self.assertIn("CHK-07", chks)
            self.assertTrue(chks["CHK-07"]["ok"], chks["CHK-07"])
            self.assertIn("CHK-08", chks)

            # 9) 导出的标准导入版 .xlsx 全部 13 Sheet 不含任何 [1]/[2]/#REF! 坏引用
            std_path = template_writer.export_single_workbook(pipe.store, pipe.workbook_checks, td_path / "out", audit_mode=False)
            wb_out = openpyxl.load_workbook(std_path, data_only=False)
            bad_rx = re.compile(r"(\[\d+\]|#REF!)")
            for ws in wb_out.worksheets:
                for dv in ws.data_validations.dataValidation:
                    self.assertFalse(bad_rx.search(str(dv.formula1 or "")), f"{ws.title} 残留坏引用: {dv.formula1}")
                    self.assertFalse(bad_rx.search(str(dv.formula2 or "")), f"{ws.title} 残留坏引用: {dv.formula2}")
            wb_out.close()


if __name__ == "__main__":
    unittest.main()
