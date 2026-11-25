# -*- coding: utf-8 -*-
"""多表工作簿状态仓库（ProjectTiziStore）与 9 张院标提资表 Schema 定义。

严格对齐 110kV某线路工程提资单.xlsx 全部 9 张业务表的列位、双层表头分组与默认值分界线，
支持统一 ProvenanceDAG 溯源、Space 一键确认、修订栈追加与单格/整表回退。
"""
import copy
from datetime import datetime

from .provenance import append_revision, make_evidence, make_prov

SHEET_ORDER = [
    "基本信息",
    "杆塔明细表",
    "基础明细表",
    "基础库",
    "串型汇总表",
    "勘测信息一览表",
    "结构设备材料表",
    "电气设备材料表",
    "光缆设备材料表",
]


def _col(key, label, col_idx, group="", is_default=False, unit="", formula_col=False):
    return {
        "key": key,
        "group": group,
        "label": label,
        "col_idx": col_idx,
        "is_default": is_default,
        "unit": unit,
        "formula_col": formula_col,
    }


SHEET_SCHEMAS = {
    "基本信息": {
        "name": "基本信息",
        "short_title": "基本信息",
        "view_mode": "form",
        "data_start_row": 4,
        "default_col_start": 20,
        "fixed_rows": False,
        "columns": [
            _col("单项工程名称", "单项工程名称", 1),
            _col("电压等级", "电压等级", 2),
            _col("设计阶段", "设计阶段", 3),
            _col("线路段名称", "线路段名称", 4, "线路类型"),
            _col("回路类型", "回路类型", 5, "线路类型"),
            _col("导线型号", "导线型号", 6, "线路类型"),
            _col("地线1型号", "地线1型号", 7, "线路类型"),
            _col("地线2型号", "地线2型号", 8, "线路类型"),
            _col("线路亘长（km）", "线路亘长(km)", 9, "线路类型", unit="km"),
            _col("人力运距（km）", "人力运距(km)", 10, "运距信息", unit="km"),
            _col("汽车运距（km）", "汽车运距(km)", 11, "运距信息", unit="km"),
            _col("地形类型", "类型", 12, "地形比例"),
            _col("丘陵（%）", "丘陵(%)", 13, "地形比例", unit="%"),
            _col("泥沼（%）", "泥沼(%)", 14, "地形比例", unit="%"),
            _col("一般山地（%）", "一般山地(%)", 15, "地形比例", unit="%"),
            _col("河网（%）", "河网(%)", 16, "地形比例", unit="%"),
            _col("高山大岭（%）", "高山大岭(%)", 17, "地形比例", unit="%"),
            _col("峻岭（%）", "峻岭(%)", 18, "地形比例", unit="%"),
            _col("沙漠（%）", "沙漠(%)", 19, "地形比例", unit="%"),
            # 常用默认值列展示
            _col("省份和地区", "省份和地区", 20, "默认值", is_default=True),
            _col("架线方式", "架线方式", 25, "默认值", is_default=True),
            _col("冰区（mm）", "冰区(mm)", 26, "默认值", is_default=True, unit="mm"),
            _col("平均海拔高程（m）", "平均海拔(m)", 28, "默认值", is_default=True, unit="m"),
        ],
    },
    "杆塔明细表": {
        "name": "杆塔明细表",
        "short_title": "杆塔明细",
        "view_mode": "grid",
        "data_start_row": 4,
        "default_col_start": 20,
        "fixed_rows": False,
        "columns": [
            _col("线路段名称", "线路段名称", 1),
            _col("杆塔号", "杆塔号", 2),
            _col("杆塔型号", "杆塔型号", 3),
            _col("基数", "基数", 4),
            _col("线路转角度数（耐张），直线塔不填", "转角(°)", 5, unit="°"),
            _col("杆塔.全高（m）", "全高(m)", 6, "杆塔", unit="m"),
            _col("杆塔.全重(t)", "全重(t)", 7, "杆塔", unit="t"),
            _col("地脚螺栓（kg）", "地脚螺栓(kg)", 8, unit="kg"),
            _col("基础正面根开（m）", "正面根开(m)", 9, unit="m"),
            _col("是否高低腿", "高低腿", 10),
            _col("接地装置.型号", "型号", 11, "接地装置"),
            _col("接地装置.长度（m）", "长度(m)", 12, "接地装置", unit="m"),
            _col("接地装置.埋深（m）", "埋深(m)", 13, "接地装置", unit="m"),
            _col("接地装置.常规接地镀锌接地材料重量（t）", "镀锌重(t)", 14, "接地装置", unit="t"),
            _col("交叉跨越.障碍物种类", "障碍物种类", 15, "交叉跨越"),
            _col("交叉跨越.电压等级", "电压等级", 16, "交叉跨越"),
            _col("交叉跨越.回路类型", "回路类型", 17, "交叉跨越"),
            _col("交叉跨越.跨越方式", "跨越方式", 18, "交叉跨越"),
            _col("交叉跨越.跨越数量", "数量(处)", 19, "交叉跨越"),
            # 默认值列（C20–C26）
            _col("杆塔类型", "杆塔类型", 20, "默认值", is_default=True),
            _col("避雷器类型", "避雷器类型", 21, "默认值", is_default=True),
            _col("线路类型", "线路类型", 22, "默认值", is_default=True),
            _col("均压环数量", "均压环", 23, "默认值", is_default=True),
            _col("屏蔽环数量", "屏蔽环", 24, "默认值", is_default=True),
            _col("跳线类型", "跳线类型", 25, "默认值", is_default=True),
            _col("每侧导线横担水平排列最大相数", "最大相数", 26, "默认值", is_default=True),
        ],
    },
    "基础明细表": {
        "name": "基础明细表",
        "short_title": "基础明细",
        "view_mode": "grid",
        "data_start_row": 4,
        "default_col_start": 37,
        "fixed_rows": False,
        "columns": [
            _col("线路段名称", "线路段名称", 1),
            _col("杆塔号", "杆塔号", 2),
            _col("基数", "基数", 3),
            _col("基础型号.A腿", "A腿", 4, "基础型号"),
            _col("基础型号.B腿", "B腿", 5, "基础型号"),
            _col("基础型号.C腿", "C腿", 6, "基础型号"),
            _col("基础型号.D腿", "D腿", 7, "基础型号"),
            _col("露头高度.A腿", "A腿", 8, "露头高度(m)", unit="m"),
            _col("露头高度.B腿", "B腿", 9, "露头高度(m)", unit="m"),
            _col("露头高度.C腿", "C腿", 10, "露头高度(m)", unit="m"),
            _col("露头高度.D腿", "D腿", 11, "露头高度(m)", unit="m"),
            _col("本体附加砼.A腿", "A腿", 12, "本体附加砼(m³)", unit="m³"),
            _col("本体附加砼.B腿", "B腿", 13, "本体附加砼(m³)", unit="m³"),
            _col("本体附加砼.C腿", "C腿", 14, "本体附加砼(m³)", unit="m³"),
            _col("本体附加砼.D腿", "D腿", 15, "本体附加砼(m³)", unit="m³"),
            _col("有筋护壁砼.A腿", "A腿", 16, "护壁砼(m³)", unit="m³"),
            _col("有筋护壁砼.B腿", "B腿", 17, "护壁砼(m³)", unit="m³"),
            _col("有筋护壁砼.C腿", "C腿", 18, "护壁砼(m³)", unit="m³"),
            _col("有筋护壁砼.D腿", "D腿", 19, "护壁砼(m³)", unit="m³"),
            _col("排水沟.长度（m）", "长度(m)", 28, "排水沟", unit="m"),
            _col("护坡.面积或方量", "护坡", 31, "护坡"),
            _col("挡土墙.规格说明", "挡土墙", 34, "挡土墙"),
            # 默认值列代表
            _col("基础总砼量(参考m³)", "四腿参考砼(m³)", 37, "联动参考", is_default=True, unit="m³"),
            _col("基础总钢筋(参考kg)", "四腿参考钢筋(kg)", 38, "联动参考", is_default=True, unit="kg"),
        ],
    },
    "基础库": {
        "name": "基础库",
        "short_title": "基础库",
        "view_mode": "grid",
        "data_start_row": 4,
        "default_col_start": 16,
        "fixed_rows": False,
        "columns": [
            _col("基础型号", "基础型号", 1),
            _col("基础类型", "基础类型", 2),
            _col("基础.砼量(m³)", "砼量(m³)", 3, unit="m³"),
            _col("基础钢筋量（kg）.HRB400", "HRB400(kg)", 4, unit="kg"),
            _col("基础钢筋量（kg）.HPB300", "HPB300(kg)", 5, unit="kg"),
            _col("原状土.基础全高(m)", "全高(m)", 6, "原状土基础", unit="m"),
            _col("原状土.基础直径(m)", "直径(m)", 7, "原状土基础", unit="m"),
            _col("原状土.扩大头高度(m)", "扩大头高(m)", 8, "原状土基础", unit="m"),
            _col("重力式.底板长(m)", "底板长(m)", 9, "重力式基础", unit="m"),
            _col("重力式.底板宽(m)", "底板宽(m)", 10, "重力式基础", unit="m"),
            _col("重力式.基础深度(m)", "埋深(m)", 11, "重力式基础", unit="m"),
            _col("重力式.立柱宽(m)", "立柱宽(m)", 12, "重力式基础", unit="m"),
            _col("灌注桩.桩直径(m)", "桩径(m)", 13, "灌注桩基础", unit="m"),
            _col("灌注桩.桩长(m)", "桩长(m)", 14, "灌注桩基础", unit="m"),
            _col("灌注桩.挤扩支盘桩", "挤扩支盘", 15, "灌注桩基础"),
            _col("混凝土标号", "砼标号", 17, "默认值", is_default=True),
        ],
    },
    "串型汇总表": {
        "name": "串型汇总表",
        "short_title": "串型汇总",
        "view_mode": "grid",
        "data_start_row": 4,
        "default_col_start": 13,
        "fixed_rows": False,
        "columns": [
            _col("线路段名称", "线路段名称", 1),
            _col("导线悬垂串.型号", "型号", 2, "导线悬垂串"),
            _col("导线悬垂串.类型", "类型", 3, "导线悬垂串"),
            _col("导线悬垂串.串数", "串数", 4, "导线悬垂串"),
            _col("导线耐张串.型号", "型号", 5, "导线耐张串"),
            _col("导线耐张串.数量(组)", "数量(组)", 6, "导线耐张串"),
            _col("导线跳线串.型号", "型号", 7, "导线跳线串"),
            _col("导线跳线串.类型", "类型", 8, "导线跳线串"),
            _col("导线跳线串.串数", "串数", 9, "导线跳线串"),
            _col("防振锤.型号", "型号", 10, "防振锤"),
            _col("防振锤.类型", "类型", 11, "防振锤"),
            _col("防振锤.数量（个）", "数量(个)", 12, "防振锤"),
            _col("悬垂线夹类型", "线夹类型", 13, "默认值", is_default=True),
            _col("悬垂安装方式", "安装方式", 14, "默认值", is_default=True),
        ],
    },
    "勘测信息一览表": {
        "name": "勘测信息一览表",
        "short_title": "勘测一览",
        "view_mode": "grid",
        "data_start_row": 4,
        "default_col_start": 22,
        "fixed_rows": False,
        "columns": [
            _col("杆塔号", "杆塔号", 1),
            _col("基础土质.土质类型", "土质类型", 2, "基础土质信息"),
            _col("基础土质.比例（%）", "比例(%)", 3, "基础土质信息", unit="%"),
            _col("基础土质.分层深(m)", "分层深(m)", 4, "基础土质信息", unit="m"),
            _col("青苗.种类", "种类", 5, "青苗"),
            _col("青苗.占地面积（亩）", "面积(亩)", 6, "青苗", unit="亩"),
            _col("塔基位土地性质", "土地性质", 16),
            _col("林地类型", "林地类型", 17),
            _col("地点.州（市）", "州(市)", 18, "地点"),
            _col("地点.县（市、区）", "县(市/区)", 19, "地点"),
            _col("地点.镇（街道）", "镇(街道)", 20, "地点"),
            _col("地点.村（社区）", "村(社区)", 21, "地点"),
            _col("挖土方式", "挖土方式", 23, "默认值", is_default=True),
            _col("接地土质类型", "接地土质", 24, "默认值", is_default=True),
        ],
    },
    "结构设备材料表": {
        "name": "结构设备材料表",
        "short_title": "结构材料",
        "view_mode": "grid",
        "data_start_row": 3,
        "default_col_start": None,
        "fixed_rows": True,
        "columns": [
            _col("材料名称", "材料名称", 1),
            _col("规格", "规格", 2),
            _col("单位", "单位", 3),
            _col("数量（不含损耗）", "数量(不含损耗)", 4),
            _col("合同单价含税（元）", "含税单价(元)", 5, unit="元"),
            _col("合同单价不含税（元）", "不含税单价(元)", 6, unit="元"),
            _col("供货方", "供货方", 7),
        ],
    },
    "电气设备材料表": {
        "name": "电气设备材料表",
        "short_title": "电气材料",
        "view_mode": "grid",
        "data_start_row": 3,
        "default_col_start": None,
        "fixed_rows": False,
        "columns": [
            _col("线路段名称", "线路段名称", 1),
            _col("材料名称", "材料名称", 2),
            _col("规格型号", "规格型号", 3),
            _col("单位", "单位", 4),
            _col("设计用量（不含损耗）", "设计用量", 5),
            _col("试验或备用量", "试验/备用量", 6),
            _col("单重（kg）", "单重(kg)", 7, unit="kg"),
            _col("合同单价含税（元）", "含税单价(元)", 8, unit="元"),
            _col("供货方", "供货方", 9),
        ],
    },
    "光缆设备材料表": {
        "name": "光缆设备材料表",
        "short_title": "光缆材料",
        "view_mode": "grid",
        "data_start_row": 3,
        "default_col_start": None,
        "fixed_rows": False,
        "columns": [
            _col("线路段名称", "线路段名称", 1),
            _col("材料名称", "材料名称", 2),
            _col("规格型号", "规格型号", 3),
            _col("单位", "单位", 4),
            _col("设计用量（不含损耗）", "设计用量", 5),
            _col("试验或备用量", "试验/备用量", 6),
            _col("单重（kg）", "单重(kg)", 7, unit="kg"),
            _col("合同单价含税（元）", "含税单价(元)", 8, unit="元"),
            _col("供货方", "供货方", 9),
        ],
    },
}

# 结构设备材料表固定 26 行母版骨架（R03–R28）
STRUCT_MATERIAL_FIXED_ROWS = [
    (3, "杆塔", "角钢（含高强钢）", "t"),
    (4, "杆塔", "角钢（不含高强钢）", "t"),
    (5, "杆塔", "钢管（含高强钢）", "t"),
    (6, "杆塔", "钢管（不含高强钢）", "t"),
    (7, "钢筋", "基础护板", "t"),
    (8, "钢筋", "基础钢筋", "t"),
    (9, "钢筋", "护壁钢筋", "t"),
    (10, "地脚螺栓", "综合", "t"),
    (11, "混凝土", "基础本体（含附加）", "m³"),
    (12, "混凝土", "基础护壁", "m³"),
    (13, "混凝土", "基础垫层", "m³"),
    (14, "混凝土", "基础保护帽", "m³"),
    (15, "排水沟", "综合", "m³"),
    (16, "护坡", "综合", "m³"),
    (17, "挡土墙", "综合", "m³"),
    (18, "防撞墩或防撞墙", "综合", "m³"),
    (19, "防坠落装置", "不锈钢钢绞线1", "m"),
    (20, "防坠落装置", "防坠器1", "个"),
    (21, "防坠落装置", "连接器1", "个"),
    (22, "防坠落装置", "张紧装置1", "套"),
    (23, "防坠落装置", "导轨2", "m"),
    (24, "防坠落装置", "防坠器2", "个"),
    (25, "防坠落装置", "转向器2", "个"),
    (26, "防坠落装置", "截止器2", "个"),
    (27, "防坠落装置", "连接器2", "个"),
    (28, "防坠落装置", "其他部件2", "个"),
]


def cell_display_status(val, prov):
    method = (prov or {}).get("method", "extract")
    st = (prov or {}).get("status", "confirmed")
    flag = (prov or {}).get("flag")
    if st == "conflict":
        return "conflict"
    if flag == "pending" or st == "pending":
        return "pending"
    if method == "manual":
        return "manual"
    if method == "drawing_select":
        return "drawing"
    if val in (None, ""):
        return "empty"
    if method in ("derive", "lookup", "interval", "constant", "multi_join_lookup", "std_lib_lookup", "price_ref", "rollup"):
        return "rule"
    return "extract"


class ProjectTiziStore:
    """多表工作簿状态仓库，持久化在 job.json['store'] 中。"""

    def __init__(self):
        self.sheets = {}
        self.revisions = []  # 全局修订记录栈
        self.excluded_gantries = []  # 从杆塔表中剥离的变电站构架审计清单
        self.reset()

    def reset(self):
        self.sheets = {}
        for name in SHEET_ORDER:
            self.sheets[name] = {
                "rows": [],
                "missing_source": "",
                "source_files": [],
                "meta": {},
            }
        self.revisions = []
        self.excluded_gantries = []

    def set_sheet(self, name: str, rows: list, *, missing_source: str = "", source_files=None, meta=None):
        if name not in SHEET_SCHEMAS:
            raise ValueError(f"未知提资表：{name}")
        self.sheets[name] = {
            "rows": rows,
            "missing_source": missing_source or "",
            "source_files": list(source_files or []),
            "meta": dict(meta or {}),
        }

    def get_sheet(self, name: str) -> dict:
        if name not in self.sheets:
            raise ValueError(f"未知提资表：{name}")
        return self.sheets[name]

    def schema(self, name: str) -> dict:
        if name not in SHEET_SCHEMAS:
            raise ValueError(f"未知提资表：{name}")
        return SHEET_SCHEMAS[name]

    def sheet_summaries(self):
        out = []
        for name in SHEET_ORDER:
            sc = SHEET_SCHEMAS[name]
            sd = self.sheets.get(name) or {"rows": [], "missing_source": ""}
            rows = sd.get("rows") or []
            core_cols = [c["key"] for c in sc["columns"] if not c["is_default"]]
            filled = pending = conflict = 0
            for r in rows:
                out_map = r.get("out") or {}
                prov_map = r.get("prov") or {}
                for col in core_cols:
                    v = out_map.get(col)
                    st = cell_display_status(v, prov_map.get(col))
                    if v not in (None, ""):
                        filled += 1
                    if st == "pending":
                        pending += 1
                    elif st == "conflict":
                        conflict += 1
            total_core = len(rows) * len(core_cols)
            out.append({
                "name": name,
                "short_title": sc["short_title"],
                "view_mode": sc["view_mode"],
                "rows": len(rows),
                "core_cols": len(core_cols),
                "total_cols": len(sc["columns"]),
                "filled": filled,
                "coverage": round(filled / total_core * 100, 1) if total_core else 0.0,
                "pending": pending,
                "conflict": conflict,
                "missing_source": sd.get("missing_source") or "",
                "source_files": list(sd.get("source_files") or []),
                "ready": bool(rows) and not sd.get("missing_source"),
            })
        return out

    def edit_cell(self, sheet_name: str, row_idx: int, col_key: str, value=None,
                  *, flag=None, evidence=None, action="edit"):
        sd = self.get_sheet(sheet_name)
        rows = sd["rows"]
        if not (0 <= row_idx < len(rows)):
            raise ValueError(f"行号超出范围：{row_idx}")
        col_keys = {c["key"] for c in SHEET_SCHEMAS[sheet_name]["columns"]}
        if col_key not in col_keys:
            raise ValueError(f"字段不存在：{sheet_name}.{col_key}")

        r = rows[row_idx]
        out_map = r.setdefault("out", {})
        prov_map = r.setdefault("prov", {})
        old_val = out_map.get(col_key)
        cur_prov = copy.deepcopy(prov_map.get(col_key)) or make_prov("extract", machine_value=old_val)
        machine_val = cur_prov.get("machine_value", old_val)

        if action == "confirm":
            # 极速确认（Space）：保留当前值（或传入的候选值），状态置 confirmed
            new_val = old_val if value is None else self._coerce(value)
            out_map[col_key] = new_val
            new_prov = append_revision(
                cur_prov, action="confirm", old_val=old_val, new_val=new_val,
                note="人工确认无误",
            )
            new_prov["status"] = "confirmed"
            new_prov["flag"] = None
            new_prov["conf"] = 1.0
            new_prov["note"] = (new_prov.get("note") or "") + (" · 已人工确认" if "已人工确认" not in (new_prov.get("note") or "") else "")
            prov_map[col_key] = new_prov
            self._log_rev(sheet_name, row_idx, r, col_key, old_val, new_val, "确认")
            return {"changed": True, "old": old_val, "value": new_val, "status": "confirmed"}

        if action == "revert":
            return self.revert_cell(sheet_name, row_idx, col_key)

        new_val = self._coerce(value)
        is_flag = (flag == "pending")
        if is_flag:
            new_val = old_val
            new_prov = append_revision(
                cur_prov, action="flag_pending", old_val=old_val, new_val=old_val,
                note="人工标记待复核",
            )
            new_prov["status"] = "pending"
            new_prov["flag"] = "pending"
            new_prov["note"] = "人工标记待复核，值未改动"
            prov_map[col_key] = new_prov
            self._log_rev(sheet_name, row_idx, r, col_key, old_val, new_val, "标待复核")
            return {"changed": True, "old": old_val, "value": new_val, "flagged": True}

        if evidence:
            ev_obj = make_evidence(
                "图纸框选取值", evidence["path"],
                page=int(evidence["page"]) + 1,
                bbox=evidence["bbox"],
                raw_text=(evidence.get("text") or "").strip()[:120],
            )
            new_prov = append_revision(
                cur_prov, action="drawing_select", old_val=old_val, new_val=new_val,
                note="图纸框选取值", evidence=ev_obj,
            )
            new_prov["method"] = "drawing_select"
            new_prov["status"] = "confirmed"
            new_prov["flag"] = None
            new_prov["conf"] = 0.99
            new_prov["note"] = f"图纸框选取值 · {ev_obj['raw_text'][:40]}" if ev_obj["raw_text"] else "图纸框选取值"
            out_map[col_key] = new_val
            prov_map[col_key] = new_prov
            self._log_rev(sheet_name, row_idx, r, col_key, old_val, new_val, "图纸框选")
            return {"changed": True, "old": machine_val, "value": new_val, "drawing": True}

        if new_val == old_val and cur_prov.get("status") not in ("pending", "conflict"):
            return {"changed": False, "old": old_val, "value": new_val}

        new_prov = append_revision(
            cur_prov, action="manual_edit", old_val=old_val, new_val=new_val,
            note=f"人工修订（机器原值：{machine_val}）",
        )
        new_prov["method"] = "manual"
        new_prov["status"] = "confirmed"
        new_prov["flag"] = None
        new_prov["conf"] = 1.0
        new_prov["note"] = f"人工修订（原值：{machine_val if machine_val not in (None, '') else '空'}）"
        out_map[col_key] = new_val
        prov_map[col_key] = new_prov
        self._log_rev(sheet_name, row_idx, r, col_key, old_val, new_val, "改值")
        return {"changed": True, "old": machine_val, "value": new_val}

    def revert_cell(self, sheet_name: str, row_idx=None, col_key=None):
        sd = self.get_sheet(sheet_name)
        rows = sd["rows"]
        reverted = 0
        indices = [row_idx] if row_idx is not None else list(range(len(rows)))
        for idx in indices:
            if not (0 <= idx < len(rows)):
                continue
            r = rows[idx]
            prov_map = r.get("prov") or {}
            cols = [col_key] if col_key is not None else list(prov_map.keys())
            for col in cols:
                pv = prov_map.get(col)
                if not pv or not pv.get("revisions"):
                    continue
                mval = pv.get("machine_value")
                r["out"][col] = mval
                pv["revisions"] = []
                pv["flag"] = None
                if pv.get("method") in ("manual", "drawing_select"):
                    pv["method"] = "extract" if pv.get("evidences") else "rule"
                pv["status"] = "confirmed" if mval not in (None, "") else "pending"
                reverted += 1
        return {"changed": reverted > 0, "reverted": reverted}

    def _log_rev(self, sheet, row_idx, row_obj, col_key, old_val, new_val, kind):
        out = row_obj.get("out") or {}
        anchor = out.get("杆塔号") or out.get("基础型号") or out.get("材料名称") or out.get("线路段名称") or f"第{row_idx + 1}行"
        self.revisions.append({
            "sheet": sheet,
            "row": row_idx,
            "line_no": row_idx + 1,
            "anchor": str(anchor)[:30],
            "column": col_key,
            "old": old_val,
            "value": new_val,
            "kind": kind,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        self.revisions = self.revisions[-500:]

    @staticmethod
    def _coerce(value):
        if isinstance(value, str):
            s = value.strip()
            if s == "":
                return None
            try:
                f = float(s)
                return int(f) if f == int(f) else f
            except ValueError:
                return s
        return value

    def to_dict(self):
        return {
            "version": 1,
            "sheets": self.sheets,
            "revisions": self.revisions,
            "excluded_gantries": self.excluded_gantries,
        }

    def load_dict(self, data: dict):
        self.reset()
        if not isinstance(data, dict):
            return
        for name, val in (data.get("sheets") or {}).items():
            if name in self.sheets and isinstance(val, dict):
                self.sheets[name] = {
                    "rows": list(val.get("rows") or []),
                    "missing_source": str(val.get("missing_source") or ""),
                    "source_files": list(val.get("source_files") or []),
                    "meta": dict(val.get("meta") or {}),
                }
        self.revisions = list(data.get("revisions") or [])
        self.excluded_gantries = list(data.get("excluded_gantries") or [])
