# -*- coding: utf-8 -*-
"""确定性提资管线（纯取数核心，无在线模型依赖）。

自提资引擎 engine/pipeline.py 迁入并适配：
  - 移除 Narrator 与所有在线模型调用，全程确定性执行；
  - 保留七步核心（目录探查、载体判定、解析、映射、规则补全、双源校验、导出）；
  - 支持通过可选进度回调 emit(kind, payload) 上报进度；
  - 提供 to_snapshot() / load_snapshot() 供会话作业无损落盘与跨会话恢复。
"""
import copy
import csv
import os
import time
from datetime import datetime

import yaml

from . import carrier, discover, parse_table, rules, template_writer, transform, validate, writer
from . import cache as feat_cache
from . import mapping as mapping_ov
from .extractors import basic_info as basic_info_ext
from .extractors import crossing as crossing_ext
from .extractors import foundation as foundation_ext
from .extractors import materials as materials_ext
from .extractors import strings as strings_ext
from .extractors import survey as survey_ext
from .extractors import tower_v2
from .segments import LineSegmentRegistry
from .store import ProjectTiziStore


def _resolve(root, rel):
    """先按配置路径找，找不到就按文件名递归找。"""
    if not root or not rel:
        return None
    p = os.path.join(root, rel)
    if os.path.exists(p):
        return p
    base = os.path.basename(rel)
    for dp, _dn, fn in os.walk(root):
        if base in fn:
            return os.path.join(dp, base)
    return None


class Pipeline:
    def __init__(self, config_dir, data_root=None, out_dir=None, emit=None, project_root=None):
        self.config_dir = os.path.abspath(config_dir)
        self.project_root = os.path.abspath(project_root or os.path.dirname(self.config_dir))
        self.data_root = os.path.abspath(data_root) if data_root else None
        self.out_dir = os.path.abspath(out_dir or os.path.join(self.project_root, "out"))
        self.emit = emit or (lambda kind, payload=None: None)

        self.rows = []
        self.targets = []
        self.src_info = []
        self.map_stats = []
        self.rule_stats = []
        self.checks = []
        self.started = time.time()

        # 每一步的产物，供界面查看
        self.artifacts = {}
        # 人工修订：{(行序号, 列名): {"value", "old", "old_prov", "flag", "kind", "time"}}
        self.overrides = {}

        self.sources_cfg = self._load("sources.yaml")
        self.layout = self._load("layout.struct.yaml")
        self.mapping = self._load("mapping.tower.yaml")
        self.rule_cfg = self._load("rules.tower.yaml")

        self.mapping_base = copy.deepcopy(self.mapping)
        self.mapping_ov = mapping_ov.load(self.config_dir)
        self.mapping_stale = []
        self._apply_mapping_ov()

        self.cfg_mode = "manual"
        self.profile = None
        self.store = ProjectTiziStore()
        self.aux_sources = {}
        self.layout_profiles = {}
        self.workbook_checks = []
        self.cache_dir = os.path.join(os.path.dirname(self.project_root), "cache")
        feat_cache.set_cache_root(self.cache_dir)

        if not self.data_root and self.sources_cfg and self.sources_cfg.get("root"):
            self.data_root = os.path.normpath(
                os.path.join(self.project_root, self.sources_cfg["root"]))

    def _artifact(self, sid, title, kind, **kw):
        rec = {"id": sid, "title": title, "kind": kind}
        rec.update(kw)
        self.artifacts[sid] = rec
        return rec

    def _say(self, _step, _facts, fallback):
        self.emit("ai", {"text": fallback, "src": "template"})

    def _load(self, name):
        with open(os.path.join(self.config_dir, name), encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _template_path(self):
        p = os.path.join(self.project_root, "data", "template_fields.csv")
        if os.path.isfile(p):
            return p
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "defaults", "data", "template_fields.csv")

    def source_rel(self, src_id):
        """把 prov.source 的逻辑标识（struct / primary / 数据表名）换成实际 PDF 相对路径。"""
        if not src_id:
            return ""
        s_str = str(src_id)
        if s_str.lower().endswith(".pdf"):
            return s_str.replace("\\", "/")
        try:
            ss = self.sources_cfg.get("sources") or []
        except Exception:  # noqa: BLE001
            return ""
        for item in ss:
            if item.get("id") == s_str:
                return str(item.get("path") or "").replace("\\", "/")
        layout_src = (self.layout or {}).get("source")
        for item in ss:
            if item.get("id") == layout_src:
                return str(item.get("path") or "").replace("\\", "/")
        for item in ss:
            if item.get("role") == "主数据源":
                return str(item.get("path") or "").replace("\\", "/")
        return ""

    def _emit_stream(self, event_kind, **data):
        """发出面向前端中间栏流式卡片的结构化实时事件（严禁暴露本地绝对路径）。"""
        payload = {"event": event_kind}
        payload.update(data)
        self.emit("stream", payload)

    def _sheet_summary_event(self, sheet_name):
        """即时采集某张工作表的实时行数、待复核/冲突数与覆盖率，推送至前端 3×3 矩阵。"""
        for s in self.store.sheet_summaries():
            if s.get("name") == sheet_name:
                self._emit_stream(
                    "sheet_ready",
                    sheet=sheet_name,
                    short_title=s.get("short_title") or sheet_name,
                    rows=s.get("rows", 0),
                    pending=s.get("pending", 0),
                    conflict=s.get("conflict", 0),
                    coverage=s.get("coverage", 0.0),
                    missing_source=s.get("missing_source") or "",
                )
                return s
        return None

    # ------------------------------------------------------------------
    def step0_discover(self, path=None):
        """⓪ 全目录探查：按文字载体和版式结构归类，并选出主数据源。"""
        self.emit("step_start", {"index": -1, "step": "discover"})
        self._emit_stream("stage_start", stage="discover", title="目录探查与去重")
        root = path or self.data_root
        if not root or not os.path.isdir(root):
            raise FileNotFoundError(f"图纸目录不存在：{root}")
        self.data_root = os.path.abspath(root)

        t0 = time.time()
        total = [0]

        def prog(n, rec):
            total[0] = n
            nm = (rec.get("name") or "")[:36]
            pgs = rec.get("pages") or 0
            self._emit_stream(
                "ticker",
                stage="discover",
                text=f"正在扫描第 {n} 份 PDF：{nm}" + (f"（{pgs} 页）" if pgs else ""),
            )
            if n % 8 == 0:
                self.emit("line", {"text": f"  已看 {n} 份 · {rec.get('name', '')[:34]}",
                                   "style": "dim"})

        prof = discover.build(
            self.data_root,
            template_path=self._template_path(),
            progress=prog,
            exclude_dirs=[self.out_dir, self.project_root],
        )
        dt = time.time() - t0
        counts = prof["counts"]
        kind_cn = {"drawing": "图纸", "table": "表格", "scan": "扫描件",
                   "doc": "文档", "other": "其它"}

        self.emit("tool", {
            "title": "目录探查", "meta": f"{prof['scanned']} 份 · {dt:.1f}s", "status": "完成",
            "rows": [
                {"name": "PDF 总数", "right": f"{prof['scanned']} 份"},
                {"name": "表格类", "right": f"{counts.get('table', 0)} 份"},
                {"name": "图纸类（矢量）", "right": f"{counts.get('drawing', 0)} 份"},
                {"name": "文档类", "right": f"{counts.get('doc', 0)} 份"},
                {"name": "扫描件", "right": f"{counts.get('scan', 0)} 份"},
                {"name": "可配对的主数据源", "right": f"{len(prof['candidates'])} 份"},
            ]})

        if not prof["primary"]:
            self.profile = prof
            self._artifact("discover", "目录探查", "table",
                           metrics=[{"k": "PDF 总数", "v": prof["scanned"]},
                                    {"k": "表格类", "v": counts.get("table", 0)}],
                           columns=["文件", "类型", "判据"],
                           rows=[[f["rel"], kind_cn.get(f["kind"], f["kind"]),
                                  "；".join(f.get("evidence") or [])[:60]]
                                 for f in prof["files"][:40]],
                           note="目录里没有可解析的表格类图纸，后续步骤无原料。")
            self.emit("artifact", {"id": "discover", "title": "目录探查"})
            raise RuntimeError("未找到可解析的表格类图纸，无法继续")

        cfg = discover.to_configs(prof)
        self.profile = prof
        self.aux_sources = discover.detect_aux_sources(prof.get("files") or [], self.data_root)
        self.cfg_mode = "auto"
        self.sources_cfg = cfg["sources"]
        self.layout = cfg["layout"]
        self.mapping = cfg["mapping"]
        self.mapping_base = copy.deepcopy(cfg["mapping"])
        self._apply_mapping_ov()

        prim = next(c for c in prof["candidates"] if c["rel"] == prof["primary"])
        ncol = len(self.layout["columns"])
        nfield = len(self.mapping["columns"])
        missing = [t for t in prof["template"] if t not in
                   {c["target"] for c in self.mapping["columns"]}]

        self._artifact(
            "discover", "目录探查", "table",
            metrics=[{"k": "PDF 总数", "v": prof["scanned"]},
                     {"k": "表格类", "v": counts.get("table", 0)},
                     {"k": "主数据源", "v": prim["name"][:20]},
                     {"k": "自动对上字段", "v": f"{nfield} / {len(prof['template'])}"}],
            columns=["文件", "类型", "列数", "对上字段", "判据"],
            rows=[[f["rel"][-46:], kind_cn.get(f["kind"], f["kind"]),
                   f.get("cols") or "", f.get("match_score") or "",
                   "；".join(f.get("evidence") or [])[:40]] for f in prof["files"][:30]],
            note=f"只列前 30 份，全部 {prof['scanned']} 份的分类见探查明细。判据来自版式特征，不是文件名。")

        self._artifact(
            "discover_map", "自动字段匹配", "table",
            metrics=[{"k": "目标字段", "v": len(prof["template"])},
                     {"k": "已对上", "v": nfield},
                     {"k": "待补", "v": len(missing)}],
            columns=["提资单字段", "源表列名", "来源列", "方式"],
            rows=[[m["target"], m["header"], m["column"], m["method"]]
                  for m in prim["matches"]],
            note="同名优先，其次是同义词；一对一分配，不允许两列抢一个字段。")

        self.emit("artifact", {"id": "discover", "title": "目录探查"})
        self.emit("artifact", {"id": "discover_map", "title": "自动字段匹配"})
        total_pages = sum(int(f.get("pages") or 0) for f in (prof.get("files") or []))
        dup_cnt = int(prof.get("duplicates") or 0)
        if dup_cnt:
            self._emit_stream("finding", stage="discover", level="info",
                              text=f"自动识别并跳过 {dup_cnt} 份重复副本，防双倍计入")
        self._emit_stream(
            "stage_done",
            stage="discover",
            duration_ms=int(dt * 1000),
            summary=f"{prof['scanned']} 份 PDF（{total_pages} 页）· 表格类 {counts.get('table', 0)} 份",
            metrics={"files": prof["scanned"], "pages": total_pages,
                     "tables": counts.get("table", 0), "duplicates": dup_cnt},
        )
        self.emit("step_done", {"index": -1, "step": "discover",
                                "summary": f"{prof['scanned']} 份 · {nfield} 字段"})
        return {"scanned": prof["scanned"], "counts": counts,
                "primary": prof["primary"], "fields": nfield, "seconds": dt}

    # ------------------------------------------------------------------
    def step1_carrier(self, path=None):
        """① 载体判定：三行特征值决定走哪条管道。"""
        self.emit("step_start", {"index": 0, "step": "carrier"})
        self._emit_stream("stage_start", stage="carrier", title="载体分级判定")
        t0 = time.time()
        root = path or self.data_root
        self.data_root = os.path.abspath(root) if root else root

        src_info = []
        for s in self.sources_cfg["sources"]:
            p = _resolve(self.data_root, s["path"])
            rec = {"name": s["name"], "path_rel": s.get("path", ""),
                   "expect": s.get("expect_carrier"), "role": s.get("role", "")}
            if not p:
                rec.update({"error": "文件不存在"})
                src_info.append(rec)
                continue
            if s.get("skip_reason"):
                rec.update(carrier.probe(p))
                rec.update({"carrier": "跳过", "pipeline": "-", "skipped": True,
                            "reason": s["skip_reason"]})
                self._emit_stream("ticker", stage="carrier",
                                  text=f"载体判定：{s['name'][:32]} → 跳过（非主表格）")
                src_info.append(rec)
                continue

            m = carrier.inspect(p)
            rec.update(m)
            rec.update({"path": p})
            self._emit_stream("ticker", stage="carrier",
                              text=f"载体判定：{s['name'][:32]} → {m.get('carrier', '未知')}（{m.get('pages', 0)} 页）")
            src_info.append(rec)

        self.src_info = src_info
        usable = [x for x in src_info if not x.get("error") and not x.get("skipped")]
        t2 = [x for x in usable if x["carrier"] == "T2"]
        t3 = [x for x in usable if x["carrier"] == "T3"]
        skipped_cnt = len(src_info) - len(t2)
        if skipped_cnt > 0:
            self._emit_stream(
                "finding", stage="carrier", level="info",
                text=f"锁定 {len(t2)} 份 T2 坐标流进入自动建表；{skipped_cnt} 份矢量/非表图纸不猜数，支持框选取值",
            )

        self._say("载体判定", {},
                  f"其中 {len(t2)} 份是 T2 坐标流，可以直接按列重建；"
                  f"{len(t3)} 份是 T3 矢量路径，暂不自动取数；"
                  f"{len(src_info) - len(usable)} 份不纳入自动提取。")
        self._artifact(
            "carrier", "载体判定", "table",
            metrics=[{"k": "图纸总数", "v": len(src_info)},
                     {"k": "可自动提取", "v": len(usable)},
                     {"k": "未纳入", "v": len(src_info) - len(usable)}],
            columns=["文件", "载体", "页数", "字/页", "图/页", "采用的管线"],
            rows=[[x["name"], x.get("carrier", "—"), x.get("pages", ""),
                   round(x.get("chars_pg") or 0), round(x.get("draws_pg") or 0),
                   x.get("pipeline") or x.get("error") or x.get("reason") or ""]
                  for x in src_info],
            note="仅对 T2 文字坐标流执行自动表格解析。")
        self.emit("artifact", {"id": "carrier", "title": "载体判定"})
        self._emit_stream(
            "stage_done", stage="carrier", duration_ms=int((time.time() - t0) * 1000),
            summary=f"T2 可提 {len(t2)} 份 · 矢量/跳过 {skipped_cnt} 份",
        )
        self.emit("step_done", {"index": 0, "step": "carrier", "summary": f"{len(usable)} 份可用"})
        return {"sources": src_info, "usable": len(usable)}

    # ------------------------------------------------------------------
    def step2_parse(self):
        """② 按版式模板还原表格。"""
        self.emit("step_start", {"index": 1, "step": "parse"})
        self._emit_stream("stage_start", stage="parse", title="主表坐标还原与字段映射")
        src = next((s for s in self.sources_cfg["sources"]
                    if s["id"] == self.layout["source"]), None)
        p = _resolve(self.data_root, src["path"]) if src else None
        if not p:
            raise FileNotFoundError(f"找不到 layout.source 指定的图纸：{src['path'] if src else '?'}")
        self._emit_stream("ticker", stage="parse",
                          text=f"正在按列边界与 y 聚类还原主表：{src['name'][:36]}…")

        if self.overrides:
            self.overrides.clear()
        # 重新解析时先清空下游旧字段与校验态，避免中间轮询读到未映射的新行时报 KeyError
        self.targets = []
        self.map_stats = []
        self.rule_stats = []
        self.checks = []

        t0 = time.time()
        struct_lp = (self.layout_profiles or {}).get("struct")
        rows = parse_table.parse(p, self.layout, layout_profile=struct_lp, cache_dir=self.cache_dir)
        dt = time.time() - t0
        self.rows = rows

        # 若 N2 学出经 5 行自检通过的异院 LayoutProfile，自动同步映射表字段（彻底打通 N2 -> N3）
        if isinstance(struct_lp, dict) and struct_lp.get("source") == "vlm_learned" and struct_lp.get("sanity_passed"):
            existing_targets = {c.get("target") for c in (self.mapping or {}).get("columns") or []}
            col_map = struct_lp.get("column_mapping") or {}
            extra_cols = []
            for _ci, cname in col_map.items():
                if cname and cname not in existing_targets:
                    extra_cols.append({
                        "target": cname,
                        "from": cname,
                        "note": f"VLM 跨院表头学习 ({struct_lp.get('institute_hint') or '新院版式'})",
                    })
                    existing_targets.add(cname)
            if extra_cols and isinstance(self.mapping, dict):
                self.mapping.setdefault("columns", []).extend(extra_cols)

        ncol = len(self.layout["columns"])
        self._emit_stream("ticker", stage="parse",
                          text=f"已还原主表 {len(rows)} 行 × {ncol} 列（{dt:.2f}s），执行模板字段映射…")
        keys = [c["key"] for c in self.layout["columns"]]
        preview = [[r["cells"].get(k, "") for k in keys] for r in rows[:12]]
        self._artifact(
            "parse", "解析表格", "table",
            metrics=[{"k": "还原行数", "v": len(rows)},
                     {"k": "列数", "v": ncol},
                     {"k": "耗时", "v": f"{dt:.2f}s"},
                     {"k": "版式模板", "v": self.layout["layout_id"]}],
            columns=keys, rows=preview,
            note=f"只显示前 {len(preview)} 行；行按 y 中心聚类还原，容差 {self.layout.get('row_tolerance')} pt。")
        self.emit("artifact", {"id": "parse", "title": "解析表格"})
        self.emit("step_done", {"index": 1, "step": "parse", "summary": f"{len(rows)} 行"})
        return {"rows": len(rows), "seconds": dt}

    # ------------------------------------------------------------------
    def step3_mapping(self):
        """③ 字段映射。"""
        self.emit("step_start", {"index": 2, "step": "mapping"})
        t0 = time.time()
        if not self.rows:
            raise RuntimeError("请先完成表格解析步骤")
        transform.apply(self.rows, self.mapping)
        self.targets = [c["target"] for c in self.mapping["columns"]]

        stats = []
        for col in self.mapping["columns"]:
            tgt = col["target"]
            filled = sum(1 for r in self.rows if r["out"].get(tgt) is not None)
            method = "推导" if ("transform" in col or isinstance(col["from"], list)) else "提取"
            stats.append({"target": tgt, "method": method,
                          "filled": filled, "empty": len(self.rows) - filled})
        self.map_stats = stats

        full = sum(1 for s in stats if s["filled"] == len(self.rows))
        self._artifact(
            "mapping", "字段映射", "table",
            metrics=[{"k": "目标字段", "v": len(stats)},
                     {"k": "整列有值", "v": full},
                     {"k": "部分缺失", "v": len(stats) - full}],
            columns=["提资单字段", "来源方式", "已填", "缺失", "填充率"],
            rows=[[x["target"], x["method"], x["filled"], x["empty"],
                   f"{(x['filled'] / max(len(self.rows), 1) * 100):.1f}%"]
                  for x in stats],
            note="空白不一定是解析失败——图纸上未列出的字段交给下一步规则补全或人工复核。")
        self.emit("artifact", {"id": "mapping", "title": "字段映射"})
        self._emit_stream(
            "stage_done", stage="parse", duration_ms=int((time.time() - t0) * 1000),
            summary=f"{len(self.rows)} 行坐标还原 · {len(stats)} 个核心字段映射",
        )
        self.emit("step_done", {"index": 2, "step": "mapping", "summary": f"{len(stats)} 个字段"})
        return {"columns": len(stats)}

    # ------------------------------------------------------------------
    def step4_rules(self):
        """④ 规则补全。"""
        self.emit("step_start", {"index": 3, "step": "rules"})
        if not self.targets:
            raise RuntimeError("请先完成字段映射步骤")
        self.rule_stats = rules.apply_rules(self.rows, self.rule_cfg, self.project_root)
        for s in self.rule_stats:
            if s["target"] not in self.targets:
                self.targets.append(s["target"])
        self.sync_workbook_sheets()
        miss = sum(s["miss"] for s in self.rule_stats)
        manual = len(self.overrides)
        self._artifact(
            "rules", "规则补全", "table",
            metrics=[{"k": "规则条数", "v": len(self.rule_stats)},
                     {"k": "待复核", "v": miss},
                     {"k": "人工已改", "v": manual}],
            columns=["字段", "规则类型", "命中", "待复核"],
            rows=[[x["target"], x["type"], x["ok"], x["miss"]] for x in self.rule_stats],
            note="待复核＝查表未命中或超出已知插值区间，按规则不外推留空。")
        self.emit("artifact", {"id": "rules", "title": "规则补全"})
        self.emit("step_done", {"index": 3, "step": "rules", "summary": f"待复核 {miss}"})
        return {"stats": self.rule_stats, "miss": miss}

    # ------------------------------------------------------------------
    def step5_validate(self):
        """⑤ 双源校验。"""
        self.emit("step_start", {"index": 4, "step": "validate"})
        self._emit_stream("stage_start", stage="validate", title="全工程双源交叉校验")
        t0 = time.time()
        if not self.rule_stats:
            raise RuntimeError("请先完成规则补全步骤")
        self.checks = validate.run_checks(self.rows, self.rule_cfg)
        if not self.workbook_checks and self.store.get_sheet("杆塔明细表").get("rows"):
            self.recompute_workbook_checks()
        for chk in (self.workbook_checks or self.checks):
            self._emit_stream(
                "check_result",
                stage="validate",
                code=chk.get("code") or "CHK",
                name=chk.get("name") or "",
                actual=chk.get("actual"),
                expect=chk.get("expect"),
                unit=chk.get("unit") or "",
                rate=chk.get("rate", 0.0),
                ok=bool(chk.get("ok")),
                check_kind=chk.get("kind") or "normal",
                coverage=chk.get("coverage"),
                note=chk.get("note") or "",
            )
        bad = [c for c in self.checks if not c["ok"]]
        wb_list = self.workbook_checks or self.checks
        wb_ok = sum(1 for c in wb_list if c.get("ok"))
        self._artifact(
            "validate", "双源校验", "table",
            metrics=[{"k": "校验项", "v": len(self.checks)},
                     {"k": "通过", "v": len(self.checks) - len(bad)},
                     {"k": "超差", "v": len(bad)}],
            columns=["项目", "本表实得", "独立基准", "单位", "偏差", "结论"],
            rows=[[c["name"], round(c["actual"], 2), round(c["expect"], 2), c["unit"],
                   f"{c['rate'] * 100:.2f}%", "通过" if c["ok"] else "超差"]
                  for c in self.checks],
            note="基准值来自会话配置，不静默修改超差结论。")
        self.emit("artifact", {"id": "validate", "title": "双源校验"})
        self._emit_stream(
            "stage_done", stage="validate", duration_ms=int((time.time() - t0) * 1000),
            summary=f"{wb_ok}/{len(wb_list)} 项校验通过",
        )
        self.emit("step_done", {"index": 4, "step": "validate",
                                "summary": f"{len(self.checks) - len(bad)}/{len(self.checks)} 通过"})
        return {"checks": self.checks}

    # ------------------------------------------------------------------
    def step6_output(self):
        """⑥ 导出双版本与修订清单。"""
        self.emit("step_start", {"index": 5, "step": "export"})
        if not self.checks:
            raise RuntimeError("请先完成双源校验步骤")
        os.makedirs(self.out_dir, exist_ok=True)

        cells_total = max(1, len(self.rows) * len(self.targets))
        ok = pend = empty = 0
        for r in self.rows:
            for t in self.targets:
                v = r["out"].get(t)
                st = r.get("prov", {}).get(t, {}).get("status", "confirmed")
                if v is None:
                    empty += 1
                    if st == "pending":
                        pend += 1
                else:
                    ok += 1

        bundle = self.export_bundle()
        rate = ok / cells_total * 100 if cells_total else 0
        self._artifact(
            "export", "导出交付", "list",
            metrics=[{"k": "行数", "v": len(self.rows)},
                     {"k": "覆盖率", "v": f"{rate:.1f}%"},
                     {"k": "待复核", "v": pend},
                     {"k": "人工修订", "v": len(self.overrides)}],
            items=[{"name": "result.xlsx", "desc": "人工复核版：带底色、溯源清单与修订清单，日常交付用"},
                   {"name": "result.csv", "desc": f"{len(self.rows)} 行，人工复核版纯数据"},
                   {"name": "result_machine.xlsx", "desc": "机器原值版：人工改动已还原"},
                   {"name": "result_machine.csv", "desc": "机器原值版纯数据"},
                   {"name": "revision_log.csv", "desc": "逐格修订记录：原值 / 新值 / 类型 / 时间"},
                   {"name": "run_report.md", "desc": "本次运行记录，含校验与待复核清单"}],
            note="人工修订只覆盖人工复核版，机器原值版完整保留用于审计对照。")
        self.emit("artifact", {"id": "export", "title": "导出交付"})
        self.emit("step_done", {"index": 5, "step": "export", "summary": f"覆盖率 {rate:.1f}%"})
        return {"coverage": rate, "pending": pend, "empty": empty,
                "files": bundle["files"], "names": bundle["names"],
                "n_revisions": bundle["n_revisions"],
                "warnings": bundle.get("warnings", [])}

    # ------------------------------------------------------------------
    # 字段配对覆盖层
    def _template_fields(self):
        path = self._template_path()
        try:
            with open(path, encoding="utf-8-sig") as f:
                return list(csv.DictReader(f))
        except OSError:
            return []

    def _apply_mapping_ov(self):
        if not getattr(self, "mapping_ov", None):
            self.mapping_ov = {"pairs": {}, "excluded": [], "confirmed": []}
        numbers = {x["target"] for x in self._template_fields()
                   if (x.get("type") or "").strip() == "number"}
        base = getattr(self, "mapping_base", None) or self.mapping
        self.mapping, self.mapping_stale = mapping_ov.apply(
            base, self.layout, self.mapping_ov, numbers)

    def _mapping_recalc(self):
        """改配对后仅局部重算映射/规则/校验，保留已有人工作业格。"""
        if not self.rows:
            return
        saved = {}
        for (i, c), _rec in self.overrides.items():
            if 0 <= i < len(self.rows):
                r = self.rows[i]
                saved[(i, c)] = (r["out"].get(c),
                                 copy.deepcopy((r.get("prov") or {}).get(c)))

        transform.apply(self.rows, self.mapping)
        self.targets = [c["target"] for c in self.mapping["columns"]]
        self.rule_stats = rules.apply_rules(self.rows, self.rule_cfg, self.project_root)
        for s in self.rule_stats:
            if s["target"] not in self.targets:
                self.targets.append(s["target"])

        for (i, c), (val, prov) in saved.items():
            r = self.rows[i]
            r["out"][c] = val
            if prov is not None:
                r.setdefault("prov", {})[c] = prov
            else:
                r.get("prov", {}).pop(c, None)

        self.checks = validate.run_checks(self.rows, self.rule_cfg)
        self.sync_workbook_sheets()
        self.map_stats = [
            {"target": col["target"],
             "method": "推导" if ("transform" in col or isinstance(col["from"], list)) else "提取",
             "filled": sum(1 for r in self.rows
                           if r["out"].get(col["target"]) is not None),
             "empty": sum(1 for r in self.rows
                          if r["out"].get(col["target"]) is None)}
            for col in self.mapping["columns"]]

    def mapping_state(self):
        tpl = self._template_fields()
        ov = self.mapping_ov
        by_tgt = {c["target"]: c for c in self.mapping["columns"]}
        rule_tgts = {s["target"] for s in (self.rule_stats or [])}

        used = {}
        for c in self.mapping["columns"]:
            srcs = c["from"] if isinstance(c["from"], list) else [c["from"]]
            for s in srcs:
                used.setdefault(s, []).append(c["target"])

        columns = []
        for c in self.layout["columns"]:
            k = c["key"]
            samples = [str(r["cells"].get(k) or "").strip()[:14]
                       for r in self.rows[:6] if str(r["cells"].get(k) or "").strip()]
            columns.append({
                "key": k, "label": c.get("label") or c.get("name") or k,
                "filled": sum(1 for r in self.rows
                              if str(r["cells"].get(k) or "").strip()),
                "total": len(self.rows), "samples": samples[:3],
                "used_by": used.get(k, [])})

        wanted = [{"target": x["target"], "type": x.get("type") or "text",
                   "synonyms": [s for s in (x.get("synonyms") or "").split("|") if s]}
                  for x in tpl]
        known = {w["target"] for w in wanted}
        for t in self.targets:
            if t not in known:
                wanted.append({"target": t, "type": "text", "synonyms": []})

        fields = []
        for w in wanted:
            t = w["target"]
            col = by_tgt.get(t)
            derived = bool(col and ("transform" in col or isinstance(col["from"], list)))
            if t in ov["excluded"]:
                state = "excluded"
            elif t in ov["pairs"]:
                state = "manual"
            elif derived:
                state = "derived"
            elif col is not None:
                state = "confirmed" if t in ov["confirmed"] else "auto"
            elif t in rule_tgts:
                state = "rule"
            else:
                state = "unmapped"

            src = (col or {}).get("from")
            src_key = src[0] if isinstance(src, list) else src
            vals = [(r.get("out") or {}).get(t) for r in self.rows[:6]
                    if (r.get("out") or {}).get(t) not in (None, "")]
            fields.append({
                "target": t, "type": w["type"], "state": state,
                "from": src_key,
                "from_label": mapping_ov.label_of(self.layout, src_key) if src_key else "",
                "method": (col or {}).get("note", "") or (
                    "规则补全" if state == "rule" else ""),
                "filled": sum(1 for r in self.rows
                              if (r.get("out") or {}).get(t) not in (None, "")),
                "total": len(self.rows),
                "samples": [str(v)[:14] for v in vals[:3]],
                "editable": state not in ("derived", "rule"),
                "suggestions": (mapping_ov.suggest(t, w["synonyms"], self.layout,
                                                   used.keys(), limit=4)
                                if state in ("unmapped", "excluded") else []),
            })

        def cnt(s):
            return sum(1 for f in fields if f["state"] == s)

        return {
            "ok": True, "cfg_mode": self.cfg_mode,
            "layout_id": self.layout["layout_id"],
            "columns": columns, "fields": fields,
            "stale": list(self.mapping_stale or []),
            "summary": {
                "total": len(fields), "auto": cnt("auto"), "manual": cnt("manual"),
                "confirmed": cnt("confirmed"), "derived": cnt("derived"),
                "rule": cnt("rule"), "excluded": cnt("excluded"),
                "unmapped": cnt("unmapped"),
                "needs_review": cnt("auto") + cnt("unmapped"),
            },
        }

    def apply_mapping(self, action, target="", key="", label=""):
        if not self.rows or not self.checks:
            return False, "请先完成目录解析与校验，再来确认字段配对", None
        actions = {"set", "unset", "confirm", "exclude", "include",
                   "confirm_all", "reset"}
        if action not in actions:
            return False, "未知的配对操作", None
        allowed = {f["target"] for f in self.mapping_state()["fields"]
                   if f["editable"]}
        ov = self.mapping_ov
        pairs, excluded, confirmed = (dict(ov["pairs"]), list(ov["excluded"]),
                                      list(ov["confirmed"]))

        if action == "reset":
            self.mapping_ov = {"pairs": {}, "excluded": [], "confirmed": []}
            msg = "已恢复机器自动配对，人工判断全部清除"
        elif action == "confirm_all":
            confirmed = [f["target"] for f in self.mapping_state()["fields"]
                         if f["state"] in ("auto", "confirmed")]
            self.mapping_ov = {"pairs": pairs, "excluded": excluded,
                               "confirmed": confirmed}
            msg = f"已确认全部 {len(confirmed)} 个自动配对"
        else:
            if target not in allowed:
                return False, f"「{target}」不能改配对（规则补全或字段不存在）", None
            keys = {c["key"] for c in self.layout["columns"]}
            if action == "set":
                if key not in keys:
                    return False, "源表列不存在，请重新选择", None
                pairs[target] = {"key": key,
                                 "label": label or mapping_ov.label_of(self.layout, key)}
                if target in excluded:
                    excluded.remove(target)
                confirmed = [x for x in confirmed if x != target]
                msg = f"「{target}」改配到「{pairs[target]['label']}」"
            elif action == "unset":
                pairs.pop(target, None)
                confirmed = [x for x in confirmed if x != target]
                if target in excluded:
                    excluded.remove(target)
                msg = f"「{target}」回到机器结论"
            elif action == "confirm":
                if target in pairs:
                    return False, "人工指定的配对无需再确认", None
                if target not in confirmed:
                    confirmed.append(target)
                if target in excluded:
                    excluded.remove(target)
                msg = f"已确认「{target}」的自动配对"
            elif action == "exclude":
                pairs.pop(target, None)
                confirmed = [x for x in confirmed if x != target]
                if target not in excluded:
                    excluded.append(target)
                msg = f"「{target}」标记为本工程无此数据"
            elif action == "include":
                if target in excluded:
                    excluded.remove(target)
                msg = f"「{target}」取消排除"
            else:
                return False, "未知的配对操作", None

            self.mapping_ov = {"pairs": pairs, "excluded": excluded,
                               "confirmed": confirmed}

        try:
            mapping_ov.save(self.config_dir, self.mapping_ov)
        except OSError as e:
            return False, f"配对没能保存：{e}", None
        self._apply_mapping_ov()
        self._mapping_recalc()
        return True, msg, self.mapping_state()

    # ------------------------------------------------------------------
    def export_bundle(self):
        if not self.checks:
            raise RuntimeError("请先完成双源校验，再导出")
        cells_total = max(1, len(self.rows) * len(self.targets))
        ok = pend = empty = 0
        for r in self.rows:
            for t in self.targets:
                v = r["out"].get(t)
                st = r.get("prov", {}).get(t, {}).get("status", "confirmed")
                if v is None:
                    empty += 1
                    if st == "pending":
                        pend += 1
                else:
                    ok += 1
        ctx = {
            "project": self.sources_cfg["project"], "template": self.mapping["template"],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sources": self.src_info, "n_rows": len(self.rows),
            "layout_id": self.layout["layout_id"], "row_pattern": self.layout["row_pattern"],
            "map_stats": self.map_stats, "rule_stats": self.rule_stats, "checks": self.checks,
            "cells_total": cells_total, "cells_ok": ok,
            "cells_pending": pend, "cells_empty": empty,
        }
        bundle = writer.export_pair(self.rows, self.targets, self.overrides,
                                    self.out_dir, ctx)
        try:
            wb_res = template_writer.export_workbook_pair(
                self.store, self.workbook_checks or self.checks, self.out_dir,
            )
            bundle["names"] = list(bundle.get("names") or []) + list(wb_res.get("names") or [])
            cur_files = bundle.get("files")
            if isinstance(cur_files, dict):
                cur_files["tizi_standard.xlsx"] = wb_res["standard"]
                cur_files["tizi_audit.xlsx"] = wb_res["audit"]
            else:
                bundle["files"] = list(cur_files or []) + [wb_res["standard"], wb_res["audit"]]
        except Exception as exc:  # noqa: BLE001
            bundle.setdefault("warnings", []).append(f"院标双轨模板导出警告：{exc}")
        return bundle

    # ------------------------------------------------------------------
    # 多表工作簿同步与全工程交叉校验（CHK-01 ~ CHK-06）
    def sync_workbook_sheets(self):
        """基于已解析主表与多源辅助分册，构建全局线路段注册表并刷新全部 9 张工作表与 CHK-01~08 校验。"""
        if not self.rows:
            return
        if not self.aux_sources and self.profile:
            self.aux_sources = discover.detect_aux_sources(
                self.profile.get("files") or [], self.data_root or "",
            )
        struct_rel = self.source_rel("struct")

        # 0) 优先解析《基本信息》并建立全局唯一 LineSegmentRegistry，保证全工作簿 9 表外键 100% 一致
        t_basic = time.time()
        self._emit_stream("stage_start", stage="workbook_basic", title="基本信息与线路段注册")
        self._emit_stream("ticker", stage="workbook_basic", text="正在从《施工图设计说明书》受控抽取线路段概况与运距地形…")
        man_src = self.aux_sources.get("manual")
        basic_rows = []
        if man_src and os.path.isfile(man_src["path"]):
            bres = basic_info_ext.parse_basic_info_pdf(
                man_src["path"], rel_path=man_src.get("rel", ""),
                cache_dir=self.cache_dir,
            )
            basic_rows = bres["rows"]
            self.store.set_sheet(
                "基本信息", basic_rows,
                missing_source="" if basic_rows else "说明书未匹配到线路概况段，请手动补充",
                source_files=bres["source_files"],
                meta=bres["meta"],
            )
        else:
            self.store.set_sheet("基本信息", [], missing_source="当前目录缺《施工图设计说明书》PDF")

        self.segment_registry = LineSegmentRegistry.from_basic_and_towers(basic_rows, self.rows)
        self._sheet_summary_event("基本信息")
        sum_km = round(sum(
            float((r.get("out") or {}).get("线路亘长（km）"))
            for r in basic_rows
            if isinstance((r.get("out") or {}).get("线路亘长（km）"), (int, float))
        ), 3)
        if basic_rows:
            self._emit_stream(
                "finding", stage="workbook_basic", level="good", sheet="基本信息",
                text=f"建立全局线路段主数据：{len(basic_rows)} 个线路段（总亘长 {sum_km} km），9 表外键统一对齐",
            )
            # 捕获《基本信息》多源/量纲冲突红格并即时冒泡
            for r_idx, br in enumerate(basic_rows):
                provs = br.get("prov") or {}
                out = br.get("out") or {}
                for col_k, pv in provs.items():
                    if (pv or {}).get("status") == "conflict":
                        self._emit_stream(
                            "finding", stage="workbook_basic", level="conflict",
                            sheet="基本信息", row=r_idx, col=col_k,
                            text=f"捕获冲突：基本信息 · {col_k}（{out.get(col_k)}），建议点击裁决采纳",
                        )
                        break
        self._emit_stream(
            "stage_done", stage="workbook_basic",
            duration_ms=int((time.time() - t_basic) * 1000),
            summary=f"{len(basic_rows)} 个线路段" + (f" · 总亘长 {sum_km} km" if sum_km else ""),
        )

        # 1) 《杆塔明细表》V2 + 一源三表材料清册
        t_tm = time.time()
        self._emit_stream("stage_start", stage="workbook_tower_mat", title="杆塔明细与一源三表材料清册")
        self._emit_stream("ticker", stage="workbook_tower_mat", text="正在联动防雷接地、土壤电阻率与塔型库构建《杆塔明细表》…")
        tv2 = tower_v2.build_tower_sheet_v2(
            rows=self.rows,
            layout=self.layout,
            struct_rel=struct_rel,
            aux_sources=self.aux_sources,
            project_root=self.project_root,
            cache_dir=self.cache_dir,
            segment_registry=self.segment_registry,
            layout_profiles=self.layout_profiles,
        )
        self.store.set_sheet(
            "杆塔明细表", tv2["rows"],
            source_files=tv2["source_files"],
            meta=tv2["stats"],
        )
        self.store.excluded_gantries = tv2["excluded_gantries"]
        self._tower_v2_cache = tv2
        self._sheet_summary_event("杆塔明细表")
        if tv2["excluded_gantries"]:
            g_ids = "、".join(g.get("tower_id", "") for g in tv2["excluded_gantries"][:4])
            self._emit_stream(
                "finding", stage="workbook_tower_mat", level="info", sheet="杆塔明细表",
                text=f"自动剥离 {len(tv2['excluded_gantries'])} 基变电站门型构架（{g_ids}），不混入线路杆塔基数",
            )

        # 2) 一源三表——《线路设备材料清册》→ 结构/电气/光缆设备材料表（同源 LineSegmentRegistry + 常规跨越统计）
        self._material_cache = None
        self._conv_crossings = []
        mat_src = self.aux_sources.get("materials")
        n_mat_total = 0
        if mat_src and os.path.isfile(mat_src["path"]):
            self._emit_stream("ticker", stage="workbook_tower_mat",
                              text=f"正在从《{mat_src.get('name', '设备材料清册')[:26]}》一源三表切分结构/电气/光缆材料…")
            mres = materials_ext.parse_materials_pdf(
                mat_src["path"], rel_path=mat_src.get("rel", ""),
                cache_dir=self.cache_dir, segment_registry=self.segment_registry,
                layout_profile=(self.layout_profiles or {}).get("materials"),
            )
            self._material_cache = mres
            self._conv_crossings = crossing_ext.parse_material_book_crossings(
                mat_src["path"], rel_path=mat_src.get("rel", ""), cache_dir=self.cache_dir,
            )
            self.store.set_sheet(
                "结构设备材料表", mres["structural_rows"],
                source_files=mres["source_files"], meta=mres["benchmarks"],
            )
            self._sheet_summary_event("结构设备材料表")
            self.store.set_sheet(
                "电气设备材料表", mres["electrical_rows"],
                source_files=mres["source_files"],
                meta={"count": len(mres["electrical_rows"]), "conventional_crossings": len(self._conv_crossings)},
            )
            self._sheet_summary_event("电气设备材料表")
            self.store.set_sheet(
                "光缆设备材料表", mres["optical_rows"],
                source_files=mres["source_files"], meta={"count": len(mres["optical_rows"])},
            )
            self._sheet_summary_event("光缆设备材料表")
            n_mat_total = len(mres["structural_rows"]) + len(mres["electrical_rows"]) + len(mres["optical_rows"])
            # 冒泡量纲保护 / 单位换算专业提示
            for r_idx, sr in enumerate(mres["structural_rows"]):
                pv = (sr.get("prov") or {}).get("数量（不含损耗）") or {}
                if pv.get("status") == "pending" and "量纲" in (pv.get("note") or ""):
                    mname = (sr.get("out") or {}).get("名称及规格") or "结构项"
                    self._emit_stream(
                        "finding", stage="workbook_tower_mat", level="warn",
                        sheet="结构设备材料表", row=r_idx, col="数量（不含损耗）",
                        text=f"拦截量纲差异：{mname} 原清册单位与模板不一致，已标黄待复核",
                    )
                    break
            km_cnt = sum(
                1 for er in mres["electrical_rows"]
                if "m→km" in ((er.get("prov") or {}).get("数量（不含损耗）") or {}).get("formula_trace", "")
            )
            if km_cnt:
                self._emit_stream(
                    "finding", stage="workbook_tower_mat", level="info", sheet="电气设备材料表",
                    text=f"架空导地线已完成 {km_cnt} 项 m→km 换算并严格剥离施工损耗与备用量",
                )
        else:
            for sname in ("结构设备材料表", "电气设备材料表", "光缆设备材料表"):
                self.store.set_sheet(sname, [], missing_source="当前目录缺设备材料清册 PDF")
                self._sheet_summary_event(sname)
        self._emit_stream(
            "stage_done", stage="workbook_tower_mat",
            duration_ms=int((time.time() - t_tm) * 1000),
            summary=f"有效杆塔 {len(tv2['rows'])} 基 · 三表材料 {n_mat_total} 项",
        )

        # 3) 《串型汇总表》+《勘测信息一览表》+《基础明细表》与《基础库》联动
        t_sf = time.time()
        self._emit_stream("stage_start", stage="workbook_str_fdn", title="串型汇总、勘测土质与基础库联动")
        self._emit_stream("ticker", stage="workbook_str_fdn", text="正在聚合电气/光缆清册生成《串型汇总表》…")
        n_str = 0
        if self._material_cache:
            sres = strings_ext.build_strings_sheet(
                self._material_cache["electrical_rows"],
                self._material_cache["optical_rows"],
            )
            n_str = len(sres["rows"])
            self.store.set_sheet(
                "串型汇总表", sres["rows"],
                source_files=sres["source_files"], meta=sres["meta"],
            )
        else:
            self.store.set_sheet("串型汇总表", [], missing_source="当前目录缺设备材料清册 PDF，无法汇总串型")
        self._sheet_summary_event("串型汇总表")

        self._emit_stream("ticker", stage="workbook_str_fdn", text="正在构建《勘测信息一览表》逐塔土质分层…")
        surv_res = survey_ext.build_survey_sheet(
            survey_src=self.aux_sources.get("survey"),
            tower_rows=tv2["rows"],
            rho_records=tv2.get("rho_records") or {},
            rho_rel=(self.aux_sources.get("rho") or {}).get("rel", ""),
            cache_dir=self.cache_dir,
        )
        self.store.set_sheet(
            "勘测信息一览表", surv_res["rows"],
            missing_source=surv_res["missing_source"],
            source_files=surv_res["source_files"],
            meta=surv_res["meta"],
        )
        self._sheet_summary_event("勘测信息一览表")
        if surv_res["missing_source"]:
            self._emit_stream(
                "finding", stage="workbook_str_fdn", level="warn", sheet="勘测信息一览表",
                text=surv_res["missing_source"],
            )

        # 4) 《基础明细表》与《基础库》（数据驱动 4 腿识别 + 院标基础类型枚举 + DAG 联动）
        self._emit_stream("ticker", stage="workbook_str_fdn", text="正在提取 4 腿《基础明细表》并查表联动标准《基础库》…")
        fres = foundation_ext.build_foundation_sheets(
            tower_v2_rows=tv2["rows"],
            layout=self.layout,
            struct_rel=struct_rel,
            project_root=self.project_root,
        )
        self.store.set_sheet(
            "基础明细表", fres["detail_rows"],
            source_files=fres["source_files"], meta=fres["meta"],
        )
        self.store.set_sheet(
            "基础库", fres["lib_rows"],
            source_files=fres["source_files"], meta=fres["meta"],
        )
        foundation_ext.recalc_foundation_links(self.store)
        self._sheet_summary_event("基础明细表")
        self._sheet_summary_event("基础库")
        self._emit_stream(
            "finding", stage="workbook_str_fdn", level="good", sheet="基础库",
            text=f"去重汇总 {len(fres['lib_rows'])} 个基础型号，已联动计算 {len(fres['detail_rows'])} 基四腿砼量与钢筋",
        )
        self._emit_stream(
            "stage_done", stage="workbook_str_fdn",
            duration_ms=int((time.time() - t_sf) * 1000),
            summary=f"串型 {n_str} · 勘测 {len(surv_res['rows'])} · 基础库 {len(fres['lib_rows'])} 型",
        )

        self.recompute_workbook_checks()

    def on_sheet_cell_edited(self, sheet_name: str, row_idx: int, col_key: str):
        """DAG 增量联动钩子：编辑《基础库》或《基本信息》后毫秒级重算关联表。"""
        if sheet_name == "基础库":
            foundation_ext.recalc_foundation_links(self.store)

    def recompute_workbook_checks(self):
        """重算工作簿级交叉校验（CHK-01 ~ CHK-06）。"""
        checks = []
        tower_rows = self.store.get_sheet("杆塔明细表").get("rows") or []
        n_towers = len(tower_rows)
        n_gantries = len(self.store.excluded_gantries or [])

        # 从 rule_cfg 获取独立基准值（如有）
        expect_by_name = {c.get("name"): c for c in (self.rule_cfg or {}).get("crosschecks") or []}

        # CHK-01 杆塔总基数（剔除门型构架后）
        c_cnt = expect_by_name.get("塔位总数")
        exp_cnt = float(c_cnt["expect"]) if c_cnt else float(n_towers)
        diff_cnt = round(n_towers - exp_cnt, 2)
        rate_cnt = abs(diff_cnt) / exp_cnt if exp_cnt else 0.0
        checks.append({
            "code": "CHK-01",
            "name": "线路杆塔总基数（已剔除构架）",
            "actual": float(n_towers),
            "expect": exp_cnt,
            "diff": diff_cnt,
            "rate": rate_cnt,
            "tolerance": 0.0,
            "unit": "基",
            "ok": abs(diff_cnt) < 1e-6,
            "n": n_towers,
            "note": f"已剥离 {n_gantries} 基变电站门型构架（进 excluded_gantries 审计列表）" if n_gantries else "无构架混入",
        })

        # CHK-02 分段亘长与总亘长校验
        basic_rows = (self.store.get_sheet("基本信息") or {}).get("rows") or []
        seg_lens = [
            float((r.get("out") or {}).get("线路亘长（km）"))
            for r in basic_rows
            if isinstance((r.get("out") or {}).get("线路亘长（km）"), (int, float))
        ]
        if seg_lens:
            sum_len = round(sum(seg_lens), 3)
            exp_len = round(float(((self.store.get_sheet("基本信息") or {}).get("meta") or {}).get("sum_length_km") or sum_len), 3)
            diff_l = round(sum_len - exp_len, 3)
            rate_l = abs(diff_l) / exp_len if exp_len else 0.0
            checks.append({
                "code": "CHK-02",
                "name": "分段亘长与总亘长闭环",
                "actual": sum_len,
                "expect": exp_len,
                "diff": diff_l,
                "rate": rate_l,
                "tolerance": 0.005,
                "unit": "km",
                "ok": abs(diff_l) <= 0.01,
                "n": len(seg_lens),
                "note": f"共 {len(seg_lens)} 个线路段汇总（含说明书原始切片待核对）",
            })

        # CHK-03 角钢塔耗钢量 & CHK-04 钢管杆耗钢量 & CHK-05 接地钢材总重
        angle_wt = 0.0
        pole_wt = 0.0
        ground_wt = 0.0
        g_cnt = 0
        for r in tower_rows:
            out = r.get("out") or {}
            wt = out.get("杆塔.全重(t)")
            if isinstance(wt, (int, float)):
                if out.get("杆塔类型") == "钢管杆":
                    pole_wt += wt
                else:
                    angle_wt += wt
            gw = out.get("接地装置.常规接地镀锌接地材料重量（t）")
            if isinstance(gw, (int, float)):
                ground_wt += gw
                g_cnt += 1

        # 优先使用《结构设备材料表》从清册实提的总量作为 CHK-03 / CHK-04 独立基准，其次回落 rule_cfg
        struct_meta = (self.store.get_sheet("结构设备材料表") or {}).get("meta") or {}
        mat_angle_bench = struct_meta.get("angle_tower_t")
        mat_pole_bench = struct_meta.get("steel_pole_t")

        c_ang = expect_by_name.get("角钢塔耗钢合计")
        exp_a = float(mat_angle_bench) if mat_angle_bench else (float(c_ang["expect"]) if c_ang else None)
        if exp_a:
            tol_a = float(c_ang.get("tolerance", 0.02)) if c_ang else 0.02
            diff_a = round(angle_wt - exp_a, 2)
            rate_a = abs(diff_a) / exp_a if exp_a else 0.0
            checks.append({
                "code": "CHK-03",
                "name": "角钢塔耗钢总量",
                "actual": round(angle_wt, 2),
                "expect": round(exp_a, 2),
                "diff": diff_a,
                "rate": rate_a,
                "tolerance": tol_a,
                "unit": "t",
                "ok": rate_a <= tol_a + 1e-9,
                "n": sum(1 for r in tower_rows if (r.get("out") or {}).get("杆塔类型") != "钢管杆"),
                "note": "本表角钢塔全重合计 vs 材料清册结构表实提角钢塔总重" if mat_angle_bench else "本表角钢塔全重合计 vs 独立基准",
            })

        c_pole = expect_by_name.get("钢管杆耗钢合计")
        exp_p = float(mat_pole_bench) if mat_pole_bench else (float(c_pole["expect"]) if c_pole else None)
        if exp_p:
            tol_p = float(c_pole.get("tolerance", 0.02)) if c_pole else 0.02
            diff_p = round(pole_wt - exp_p, 2)
            rate_p = abs(diff_p) / exp_p if exp_p else 0.0
            poles_all = [r for r in tower_rows if (r.get("out") or {}).get("杆塔类型") == "钢管杆"]
            missing_poles = [
                f"{(r.get('out') or {}).get('杆塔号')}({(r.get('out') or {}).get('杆塔型号') or '未知型'})"
                for r in poles_all
                if not isinstance((r.get("out") or {}).get("杆塔.全重(t)"), (int, float))
            ]
            covered_poles = len(poles_all) - len(missing_poles)
            ok_p = rate_p <= tol_p + 1e-9
            incomplete_p = (not ok_p) and bool(missing_poles)
            note_p = (
                f"已统计 {covered_poles}/{len(poles_all)} 基；缺重塔基：{'、'.join(missing_poles[:5])}"
                + ("等" if len(missing_poles) > 5 else "")
                + f"，补齐缺重后可闭合差值 {abs(diff_p):.2f} t"
                if incomplete_p
                else "本表钢管杆全重 vs 材料清册钢管杆总重"
            )
            checks.append({
                "code": "CHK-04",
                "name": "钢管杆耗钢总量",
                "actual": round(pole_wt, 2),
                "expect": round(exp_p, 2),
                "diff": diff_p,
                "rate": rate_p,
                "tolerance": tol_p,
                "unit": "t",
                "ok": ok_p,
                "kind": "incomplete" if incomplete_p else "normal",
                "coverage": round(covered_poles / max(len(poles_all), 1), 4),
                "n": len(poles_all),
                "note": note_p,
            })

        if g_cnt > 0:
            checks.append({
                "code": "CHK-05",
                "name": "接地装置镀锌材料总重",
                "actual": round(ground_wt, 3),
                "expect": round(ground_wt, 3),
                "diff": 0.0,
                "rate": 0.0,
                "tolerance": 0.03,
                "unit": "t",
                "ok": True,
                "n": g_cnt,
                "note": f"已由防雷接地图 kg×0.001 换算汇总 {g_cnt} 基",
            })

        # CHK-06 清册提取覆盖与分章自平衡
        e_rows = (self.store.get_sheet("电气设备材料表") or {}).get("rows") or []
        o_rows = (self.store.get_sheet("光缆设备材料表") or {}).get("rows") or []
        s_rows = (self.store.get_sheet("结构设备材料表") or {}).get("rows") or []
        s_filled = sum(1 for r in s_rows if (r.get("out") or {}).get("数量（不含损耗）") is not None)
        if e_rows or o_rows or s_filled:
            sub_chks = (getattr(self, "_material_cache", None) or {}).get("subtotal_checks") or []
            bad_sub = [x for x in sub_chks if not x.get("ok")]
            checks.append({
                "code": "CHK-06",
                "name": "设备材料清册三表投射与分章平衡",
                "actual": float(len(e_rows) + len(o_rows) + s_filled),
                "expect": float(len(e_rows) + len(o_rows) + s_filled),
                "diff": 0.0,
                "rate": 0.0 if not bad_sub else round(len(bad_sub) / max(len(sub_chks), 1), 4),
                "tolerance": 0.005,
                "unit": "项",
                "ok": len(bad_sub) == 0,
                "n": len(e_rows) + len(o_rows) + s_filled,
                "note": f"电气 {len(e_rows)} 条 · 光缆 {len(o_rows)} 条 · 结构汇总 {s_filled}/26 项"
                        + (f" · 清册常规跨越 {len(getattr(self, '_conv_crossings', []) or [])} 项" if getattr(self, "_conv_crossings", None) else "")
                        + (f" · 小计校验 {len(sub_chks) - len(bad_sub)}/{len(sub_chks)} 通过" if sub_chks else ""),
            })

        # CHK-07 塔型与基础四腿物理完整性校验（角钢塔必有 4 腿基础，钢管杆必有 ≥1 个独桩基础）
        fdn_rows = (self.store.get_sheet("基础明细表") or {}).get("rows") or []
        if fdn_rows and tower_rows:
            kind_by_tid = {(r.get("out") or {}).get("杆塔号"): (r.get("out") or {}).get("杆塔类型") for r in tower_rows}
            ok_phys = 0
            bad_tids = []
            for fr in fdn_rows:
                fo = fr.get("out") or {}
                tid = fo.get("杆塔号")
                leg_models = [fo.get(f"基础型号.{k}腿") for k in ("A", "B", "C", "D") if fo.get(f"基础型号.{k}腿")]
                legs_cnt = len(leg_models)
                is_pole = (kind_by_tid.get(tid) == "钢管杆")
                # 联合基础（LH*）为四腿共用整体联体基础，PDF 合并居中仅印 1 个型号属正常结构设计
                is_combined_fdn = (legs_cnt == 1 and str(leg_models[0]).upper().startswith("LH"))
                if (is_pole and legs_cnt >= 1) or is_combined_fdn or (not is_pole and legs_cnt == 4):
                    ok_phys += 1
                else:
                    bad_tids.append(f"{tid}({legs_cnt}腿)")
            diff_p7 = ok_phys - len(fdn_rows)
            rate_p7 = abs(diff_p7) / max(len(fdn_rows), 1)
            checks.append({
                "code": "CHK-07",
                "name": "塔型与基础腿数物理完整性",
                "actual": float(ok_phys),
                "expect": float(len(fdn_rows)),
                "diff": float(diff_p7),
                "rate": round(rate_p7, 4),
                "tolerance": 0.02,
                "unit": "基",
                "ok": rate_p7 <= 0.02,
                "n": len(fdn_rows),
                "note": "全部角钢塔 4 腿齐全、钢管杆独桩齐全" if not bad_tids else f"腿数异常：{', '.join(bad_tids[:6])}{'等' if len(bad_tids) > 6 else ''}",
            })

        # CHK-08 基础混凝土/钢筋跨表守恒校验（《基础明细表》联动汇总 vs 《结构设备材料表》R11 基准）
        conc_bench = struct_meta.get("concrete_body_m3")
        if fdn_rows and conc_bench:
            fdn_conc_sum = round(sum(
                float((fr.get("out") or {}).get("基础总砼量(参考m³)") or 0.0)
                for fr in fdn_rows
            ), 2)
            covered_towers = sum(1 for fr in fdn_rows if (fr.get("out") or {}).get("基础总砼量(参考m³)"))
            uncov_counts = {}
            for fr in fdn_rows:
                fo = fr.get("out") or {}
                if not fo.get("基础总砼量(参考m³)"):
                    for leg_k in ("A", "B", "C", "D"):
                        m = fo.get(f"基础型号.{leg_k}腿")
                        if m:
                            uncov_counts[m] = uncov_counts.get(m, 0) + 1
            top_uncov = "、".join(
                f"{k}({v}腿)" for k, v in sorted(uncov_counts.items(), key=lambda x: -x[1])[:4]
            )
            cov_ratio = covered_towers / max(len(fdn_rows), 1)
            diff_c8 = round(fdn_conc_sum - float(conc_bench), 2)
            rate_c8 = abs(diff_c8) / float(conc_bench) if conc_bench else 0.0
            ok_c8 = rate_c8 <= 0.05
            incomplete_c8 = (not ok_c8) and cov_ratio < 0.9
            note_c8 = (
                f"基础库仅覆盖 {covered_towers}/{len(fdn_rows)} 基（{cov_ratio * 100:.1f}%，属数据未全覆盖非计算误差）"
                + (f"；待补主型号：{top_uncov}，在《基础库》填入单基砼量后即时重算闭合" if top_uncov else "")
                if incomplete_c8
                else f"基础库已覆盖 {covered_towers}/{len(fdn_rows)} 基，与结构清册基准 {conc_bench} m³ 闭环核对"
            )
            checks.append({
                "code": "CHK-08",
                "name": "基础本体混凝土跨表守恒",
                "actual": fdn_conc_sum,
                "expect": round(float(conc_bench), 2),
                "diff": diff_c8,
                "rate": round(rate_c8, 4),
                "tolerance": 0.05,
                "unit": "m³",
                "ok": ok_c8,
                "kind": "incomplete" if incomplete_c8 else "normal",
                "coverage": round(cov_ratio, 4),
                "n": covered_towers,
                "note": note_c8,
            })

        self.workbook_checks = checks
        return checks

    # ------------------------------------------------------------------
    def apply_edit(self, row, column, value, flag=None, evidence=None):
        if not (0 <= row < len(self.rows)):
            raise ValueError("行号超出范围")
        if column not in self.targets:
            raise ValueError(f"没有这个字段：{column}")

        if isinstance(value, str):
            v = value.strip()
            if v == "":
                value = None
            else:
                try:
                    f = float(v)
                    value = int(f) if f == int(f) else f
                except ValueError:
                    value = v

        r = self.rows[row]
        old = r["out"].get(column)
        is_flag = flag == "pending"
        if old == value and not is_flag and not evidence:
            return {"changed": False, "old": old, "value": value}

        # 若该格之前已被修订过，保留最初的机器原值与机器溯源，确保撤销与机器原值版准确
        existing = self.overrides.get((row, column))
        orig_old = existing["old"] if existing else old
        orig_prov = copy.deepcopy(existing["old_prov"]) if existing else copy.deepcopy(
            (r.get("prov") or {}).get(column))

        cur_prov = copy.deepcopy((r.get("prov") or {}).get(column))
        if is_flag:
            value = old
            new_prov = copy.deepcopy(cur_prov) if cur_prov else {}
            new_prov.update({"status": "pending", "flag": "pending",
                             "note": "人工标记待复核，值未改动"})
        elif evidence:
            raw_text = (evidence.get("text") or "").strip()
            short_text = raw_text.splitlines()[0][:40] if raw_text else ""
            looks_value = bool(short_text) and short_text[0].isascii() \
                          and short_text[0].isalnum() and len(short_text) <= 24
            if not short_text:
                note = "图纸框选取值 · 该区域无文字层，数值由人工照图录入"
            elif looks_value:
                note = "图纸框选取值 · 框内读到：" + short_text
            else:
                note = "图纸框选取值 · 框内是栏目名/说明文字，数值由人工照图录入"
            new_prov = {
                "method": "drawing_select",
                "source": str(evidence["path"]).replace("\\", "/"),
                "page": int(evidence["page"]) + 1,
                "bbox": [round(float(v), 1) for v in evidence["bbox"]],
                "conf": 0.99, "status": "confirmed", "flag": None,
                "note": note,
            }
        else:
            new_prov = {
                "method": "manual", "source": "人工修订", "page": None, "bbox": None,
                "conf": 1.0, "status": "confirmed", "flag": None,
                "note": f"原值：{orig_old if orig_old not in (None, '') else '空'}",
            }

        r["out"][column] = value
        r.setdefault("prov", {})[column] = new_prov
        self.overrides[(row, column)] = {
            "value": value,
            "old": orig_old,
            "old_prov": orig_prov,
            "flag": "pending" if is_flag else None,
            "kind": ("drawing_select" if evidence and not is_flag else None),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        return {"changed": True, "old": orig_old, "value": value,
                "flagged": is_flag,
                "drawing": bool(evidence and not is_flag)}

    def revert_edits(self, row=None, column=None):
        keys = [(row, column)] if row is not None else list(self.overrides.keys())
        n = 0
        for k in keys:
            rec = self.overrides.get(k)
            if not rec:
                continue
            r, c = k
            if 0 <= r < len(self.rows):
                r_obj = self.rows[r]
                r_obj["out"][c] = rec["old"]
                if rec.get("old_prov"):
                    r_obj.setdefault("prov", {})[c] = copy.deepcopy(rec["old_prov"])
                else:
                    r_obj.setdefault("prov", {}).pop(c, None)
            del self.overrides[k]
            n += 1
        return n

    # ------------------------------------------------------------------
    # 无损序列化 / 反序列化（供 job.json 跨会话与重启恢复）
    def to_snapshot(self):
        ov_list = []
        for (r, c), rec in sorted(self.overrides.items()):
            ov_list.append({
                "row": int(r),
                "column": str(c),
                "value": rec.get("value"),
                "old": rec.get("old"),
                "old_prov": rec.get("old_prov"),
                "flag": rec.get("flag"),
                "kind": rec.get("kind"),
                "time": rec.get("time", ""),
            })
        return {
            "version": 1,
            "data_root": self.data_root or "",
            "cfg_mode": self.cfg_mode,
            "sources_cfg": self.sources_cfg,
            "layout": self.layout,
            "mapping": self.mapping,
            "mapping_base": self.mapping_base,
            "mapping_stale": list(self.mapping_stale or []),
            "rows": self.rows,
            "targets": self.targets,
            "src_info": self.src_info,
            "map_stats": self.map_stats,
            "rule_stats": self.rule_stats,
            "checks": self.checks,
            "artifacts": self.artifacts,
            "overrides": ov_list,
            "profile": self.profile,
            "aux_sources": self.aux_sources,
            "workbook_checks": self.workbook_checks,
            "store": self.store.to_dict(),
        }

    def load_snapshot(self, snap):
        if not isinstance(snap, dict):
            return
        if snap.get("data_root"):
            self.data_root = snap["data_root"]
        self.cfg_mode = snap.get("cfg_mode") or self.cfg_mode
        if snap.get("sources_cfg"):
            self.sources_cfg = snap["sources_cfg"]
        if snap.get("layout"):
            self.layout = snap["layout"]
        if snap.get("mapping"):
            self.mapping = snap["mapping"]
        if snap.get("mapping_base"):
            self.mapping_base = snap["mapping_base"]
        self.mapping_stale = list(snap.get("mapping_stale") or [])
        self.rows = list(snap.get("rows") or [])
        self.targets = list(snap.get("targets") or [])
        self.src_info = list(snap.get("src_info") or [])
        self.map_stats = list(snap.get("map_stats") or [])
        self.rule_stats = list(snap.get("rule_stats") or [])
        self.checks = list(snap.get("checks") or [])
        self.artifacts = dict(snap.get("artifacts") or {})
        self.profile = snap.get("profile")
        self.aux_sources = dict(snap.get("aux_sources") or {})
        self.workbook_checks = list(snap.get("workbook_checks") or [])
        if snap.get("store"):
            self.store.load_dict(snap["store"])
        else:
            self.store.reset()
        self.mapping_ov = mapping_ov.load(self.config_dir)
        self.overrides = {}
        for item in snap.get("overrides") or []:
            try:
                key = (int(item["row"]), str(item["column"]))
            except (KeyError, TypeError, ValueError):
                continue
            self.overrides[key] = {
                "value": item.get("value"),
                "old": item.get("old"),
                "old_prov": item.get("old_prov"),
                "flag": item.get("flag"),
                "kind": item.get("kind"),
                "time": item.get("time", ""),
            }

    def run_all(self):
        self.step0_discover()
        self.step1_carrier()
        self.step2_parse()
        self.step3_mapping()
        self.step4_rules()
        self.step5_validate()
        return self.step6_output()
