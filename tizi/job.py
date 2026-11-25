# -*- coding: utf-8 -*-
"""造价平台侧的提资作业对象（TiziJob）。

职责：
  - 为每个会话维护独立作业目录 DATA/tizi/<session_id>/（config/、data/、out/、job.json）
  - 提供线程安全的后台步骤调度，与物资评审 STATE['running'] 彻底隔离
  - 强路径校验：所有图纸相对路径必须落在绑定工程目录内，所有导出下载必须落在 out/ 内
  - 原子落盘与跨会话/重启无损恢复 job.json
"""
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from . import drawing, validate
from .harness.gateway import VlmGateway
from .harness.graph import TiziGraphRunner
from .harness.store import evaluate_export_gate, save_store_to_workspace
from .harness.workspace import TiziWorkspace
from .pipeline import Pipeline
from .provenance import normalize_evidences
from .store import SHEET_SCHEMAS, cell_display_status

_LOG = logging.getLogger("tizi")

STEP_ORDER = ("discover", "carrier", "parse", "mapping", "rules", "validate", "export")
EXTRACT_STEPS = ("discover", "carrier", "parse", "mapping", "rules", "validate")
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

EXPORT_DESCRIPTIONS = {
    "tizi_standard.xlsx": "院标提资单 · 标准导入版（原位保留 13 个 Sheet、公式与下拉校验，直接供下游导入）",
    "tizi_audit.xlsx": "院标提资单 · 复核溯源版（含状态底色、逐格出处批注与溯源/校验/修订 3 张审计页）",
    "result.xlsx": "杆塔单表人工复核版（含底色、溯源清单与修订清单）",
    "result.csv": "杆塔单表人工复核版纯数据 CSV",
    "result_machine.xlsx": "杆塔单表机器原值版（人工改动全部回放为机器原值）",
    "result_machine.csv": "杆塔单表机器原值版纯数据 CSV",
    "revision_log.csv": "逐格修订清单（行号 / 塔号 / 字段 / 原值 / 现值 / 类型 / 时间）",
    "run_report.md": "本次运行报告（载体判定、双源校验与覆盖率）",
}


class TiziError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _atomic_write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _cell_display_status(val, prov):
    method = (prov or {}).get("method", "extract")
    st = (prov or {}).get("status", "confirmed")
    flag = (prov or {}).get("flag")
    if flag == "pending" or st == "pending":
        return "pending"
    if method == "manual":
        return "manual"
    if method == "drawing_select":
        return "drawing"
    if val in (None, ""):
        return "empty"
    if method in ("derive", "lookup", "interval", "constant"):
        return "rule"
    return "extract"


class TiziJob:
    def __init__(self, job_dir, on_update=None, vision_config_fn=None):
        self.job_dir = Path(job_dir).resolve()
        self.config_dir = self.job_dir / "config"
        self.data_dir = self.job_dir / "data"
        self.out_dir = self.job_dir / "out"
        self.job_file = self.job_dir / "job.json"
        self.workspace = TiziWorkspace(self.job_dir, session_id=self.job_dir.name)
        self.on_update = on_update
        self.vision_config_fn = vision_config_fn
        drawing.set_render_cache_dir(self.job_dir.parent / "page-cache")

        self._lock = threading.RLock()
        self._event_cond = threading.Condition()
        self.status = "idle"  # idle | running | ready | error
        self.step = ""
        self.steps_done = []
        self.folder = ""
        self.project_name = ""
        self.error = ""
        self.exports = []
        self.logs = []
        self.stream_events = []
        self._event_seq = 0
        self._run_id = ""
        self._run_started_at = 0.0
        self._run_elapsed_ms = 0
        self._steps_plan = []
        self._running = False
        self.pipe = None

        if self.job_file.is_file():
            self._load()

    @property
    def running(self):
        return self._running

    # ------------------------------------------------------------------
    # 目录初始化与落盘恢复
    def _ensure_defaults(self, force=False):
        self.job_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("config", "data"):
            src = DEFAULTS_DIR / sub
            dst = self.job_dir / sub
            if force and dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            if not dst.exists():
                shutil.copytree(src, dst)
            else:
                for item in src.iterdir():
                    target = dst / item.name
                    if item.is_file() and not target.exists():
                        shutil.copy2(item, target)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _new_pipeline(self):
        return Pipeline(
            config_dir=str(self.config_dir),
            data_root=self.folder or None,
            out_dir=str(self.out_dir),
            emit=self._on_emit,
            project_root=str(self.job_dir),
        )

    def _build_stream_plan(self, step_list):
        """将执行的 step_list 映射为前端流式时间轴阶段清单（兼容全量解析与单步重跑）。"""
        plan = []
        seen = set()
        def add(sid, title):
            if sid not in seen:
                seen.add(sid)
                plan.append({"id": sid, "title": title})
        for st in step_list:
            if st == "discover":
                add("discover", "目录探查与去重")
            elif st == "carrier":
                add("carrier", "载体分级判定")
            elif st in ("parse", "mapping"):
                add("parse", "主表坐标还原与字段映射")
            elif st == "rules":
                add("workbook_basic", "基本信息与线路段注册")
                add("workbook_tower_mat", "杆塔明细与一源三表材料清册")
                add("workbook_str_fdn", "串型汇总、勘测土质与基础库联动")
            elif st == "validate":
                add("validate", "全工程双源交叉校验")
            elif st == "export":
                add("export", "生成院标双轨提资单")
        return plan

    def _push_stream_event(self, kind, data=None):
        """追加带单调序号 seq 的结构化流式事件，并立即唤醒所有 SSE 监听线程。"""
        now = time.time()
        elapsed = int((now - self._run_started_at) * 1000) if self._run_started_at else 0
        ev = {
            "kind": kind,
            "run_id": self._run_id,
            "ts": time.strftime("%H:%M:%S"),
            "elapsed_ms": max(0, elapsed),
        }
        if isinstance(data, dict):
            for k, v in data.items():
                if k not in ("event", "kind"):
                    ev[k] = v
        with self._event_cond:
            self._event_seq += 1
            ev["seq"] = self._event_seq
            self.stream_events.append(ev)
            if len(self.stream_events) > 320:
                # 裁剪时优先保留非 ticker 持久事件（阶段/发现/表就绪/校验/起止）
                keep = [e for e in self.stream_events[:-120] if e.get("kind") != "ticker"]
                self.stream_events = (keep + self.stream_events[-120:])[-320:]
            self._run_elapsed_ms = max(self._run_elapsed_ms, ev["elapsed_ms"])
            self._event_cond.notify_all()
        return ev

    def wait_stream_events(self, since_seq=0, timeout=10.0):
        """阻塞等待序号 > since_seq 的流式事件；若任务已结束立即返回。"""
        since = int(since_seq or 0)
        with self._event_cond:
            pending = [e for e in self.stream_events if e.get("seq", 0) > since]
            if pending or not self._running:
                return list(pending), self._running, self.status
            self._event_cond.wait(timeout=timeout)
            pending = [e for e in self.stream_events if e.get("seq", 0) > since]
            return list(pending), self._running, self.status

    def stream_trace_payload(self):
        """返回供前端初始加载/刷新回放的流式轨迹快照。"""
        with self._event_cond:
            evs = list(self.stream_events)
            seq = self._event_seq
        return {
            "run_id": self._run_id,
            "status": self.status,
            "running": self._running,
            "elapsed_ms": self._run_elapsed_ms,
            "steps_plan": list(self._steps_plan),
            "last_seq": seq,
            "events": evs,
        }

    def _on_emit(self, kind, payload=None):
        payload = payload or {}
        if kind == "stream":
            ev_kind = str(payload.get("event") or "ticker")
            self._push_stream_event(ev_kind, payload)
            return
        if kind == "step_start":
            step = payload.get("step") or ""
            if step:
                self.step = step
                if step == "export":
                    self._push_stream_event("stage_start", {"stage": "export", "title": "生成院标双轨提资单"})
                self._notify()
        elif kind == "step_done":
            step = payload.get("step") or ""
            if step and step not in self.steps_done:
                self.steps_done.append(step)
            if step == "export":
                self._push_stream_event("stage_done", {"stage": "export", "summary": payload.get("summary") or "导出完成"})
            self.logs.append({
                "time": time.strftime("%H:%M:%S"),
                "step": step,
                "text": payload.get("summary") or "完成",
            })
            self.logs = self.logs[-80:]
            self._notify()
        elif kind == "line":
            txt = str(payload.get("text") or "").strip()
            if txt:
                self.logs.append({"time": time.strftime("%H:%M:%S"), "step": self.step, "text": txt})
                self.logs = self.logs[-80:]

    def _notify(self):
        if self.on_update:
            try:
                self.on_update(self.summary_state())
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("TiziJob on_update callback failed: %s", exc)

    def _save(self):
        with self._lock:
            # 落盘时仅保留高价值轨迹事件（剔除中间态 ticker 以减小体积）
            with self._event_cond:
                saved_evs = [e for e in self.stream_events if e.get("kind") != "ticker"][-180:]
                last_seq = self._event_seq
            payload = {
                "version": 1,
                "status": "ready" if self.status == "running" else self.status,
                "step": self.step,
                "steps_done": list(self.steps_done),
                "folder": self.folder,
                "project_name": self.project_name,
                "error": self.error,
                "exports": list(self.exports),
                "logs": list(self.logs[-80:]),
                "stream_trace": {
                    "run_id": self._run_id,
                    "elapsed_ms": self._run_elapsed_ms,
                    "steps_plan": list(self._steps_plan),
                    "last_seq": last_seq,
                    "events": saved_evs,
                },
                "pipeline": self.pipe.to_snapshot() if self.pipe else None,
            }
            _atomic_write_json(self.job_file, payload)

    def _load(self):
        with self._lock:
            try:
                raw = json.loads(self.job_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            self.folder = str(raw.get("folder") or "")
            self.project_name = str(raw.get("project_name") or (Path(self.folder).name if self.folder else ""))
            self.status = str(raw.get("status") or "idle")
            if self.status == "running":
                self.status = "ready" if (raw.get("pipeline") or {}).get("rows") else "idle"
            self.step = str(raw.get("step") or "")
            self.steps_done = list(raw.get("steps_done") or [])
            self.error = str(raw.get("error") or "")
            self.exports = list(raw.get("exports") or [])
            self.logs = list(raw.get("logs") or [])
            st_trace = raw.get("stream_trace") or {}
            self._run_id = str(st_trace.get("run_id") or "")
            self._run_elapsed_ms = int(st_trace.get("elapsed_ms") or 0)
            self._steps_plan = list(st_trace.get("steps_plan") or [])
            self.stream_events = list(st_trace.get("events") or [])
            max_seq = max((int(e.get("seq") or 0) for e in self.stream_events), default=0)
            self._event_seq = max(int(st_trace.get("last_seq") or 0), max_seq)
            self._ensure_defaults(force=False)
            self.pipe = self._new_pipeline()
            if raw.get("pipeline"):
                self.pipe.load_snapshot(raw["pipeline"])

    # ------------------------------------------------------------------
    # 安全路径校验
    def safe_drawing_path(self, rel_path: str) -> Path:
        if not self.folder:
            raise TiziError("请先绑定施工图目录", 400)
        if not rel_path or not isinstance(rel_path, str):
            raise TiziError("缺少图纸路径参数", 400)
        clean = rel_path.replace("\\", "/").strip()
        if clean.startswith("/") or clean.startswith("\\") or (len(clean) >= 2 and clean[1] == ":"):
            raise TiziError("路径越权：不允许绝对路径", 403)
        parts = [p for p in clean.split("/") if p]
        if any(p == ".." for p in parts):
            raise TiziError("路径越权：不允许上级目录跳转", 403)
        root = Path(self.folder).resolve()
        try:
            target = (root / Path(*parts)).resolve()
        except OSError as exc:
            raise TiziError(f"非法路径：{exc}", 400) from exc
        if root != target and root not in target.parents:
            raise TiziError("路径越权：目标超出当前施工图目录", 403)
        if not target.is_file():
            raise TiziError("图纸文件不存在", 404)
        return target

    def safe_export_path(self, filename: str) -> Path:
        if not filename or not isinstance(filename, str):
            raise TiziError("缺少文件名参数", 400)
        if "/" in filename or "\\" in filename or ".." in filename or Path(filename).name != filename:
            raise TiziError("路径越权：仅允许下载当前作业导出文件", 403)
        root = self.out_dir.resolve()
        target = (root / filename).resolve()
        if root not in target.parents:
            raise TiziError("路径越权：目标超出导出目录", 403)
        if not target.is_file():
            raise TiziError("导出文件不存在", 404)
        return target

    # ------------------------------------------------------------------
    # 摘要与状态
    def _unmapped_fields(self):
        if not self.pipe or not self.pipe.rows:
            return []
        try:
            ms = self.pipe.mapping_state()
            return [f["target"] for f in ms.get("fields") or [] if f.get("state") == "unmapped"]
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("TiziJob _unmapped_fields failed: %s", exc)
            return []

    def summary_state(self):
        with self._lock:
            rows = self.pipe.rows if self.pipe else []
            targets = self.pipe.targets if self.pipe else []
            checks = self.pipe.checks if self.pipe else []
            overrides = self.pipe.overrides if self.pipe else {}
            total_cells = len(rows) * len(targets)
            filled = 0
            pending = 0
            for r in rows:
                out = r.get("out") or {}
                prov = r.get("prov") or {}
                for t in targets:
                    v = out.get(t)
                    st = _cell_display_status(v, prov.get(t))
                    if v is not None:
                        filled += 1
                    if st == "pending":
                        pending += 1
            cov = round(filled / total_cells * 100, 1) if total_cells else 0.0
            checks_ok = sum(1 for c in checks if c.get("ok"))

            # V2.0 全工作簿权威摘要（供顶栏徽标、底部步骤条与对话通知统一口径，不破坏旧 summary 回归契约）
            wb_sheets = self.pipe.store.sheet_summaries() if self.pipe else []
            wb_checks = list(self.pipe.workbook_checks or []) if self.pipe else []
            tower_sh = next((s for s in wb_sheets if s["name"] == "杆塔明细表"), None)
            wb_checks_ok = sum(1 for c in wb_checks if c.get("ok"))
            wb_pending = sum(int(s.get("pending") or 0) + int(s.get("conflict") or 0) for s in wb_sheets)
            wb_sheets_ready = sum(1 for s in wb_sheets if int(s.get("rows") or 0) > 0)
            wb_summary = {
                "towers": int(tower_sh["rows"]) if tower_sh else len(rows),
                "tower_coverage": float(tower_sh["coverage"]) if tower_sh else cov,
                "sheets_ready": wb_sheets_ready,
                "sheets_total": len(wb_sheets) or 9,
                "pending": wb_pending,
                "checks_ok": wb_checks_ok if wb_checks else checks_ok,
                "checks_total": len(wb_checks) if wb_checks else len(checks),
                "revisions": len(self.pipe.store.revisions) if self.pipe else len(overrides),
                "exports": len(self.exports),
            }
            return {
                "status": "running" if self._running else self.status,
                "step": self.step,
                "steps_done": list(self.steps_done),
                "folder": self.folder,
                "project_name": self.project_name,
                "cfg_mode": self.pipe.cfg_mode if self.pipe else "manual",
                "summary": {
                    "rows": len(rows),
                    "targets": len(targets),
                    "filled": filled,
                    "total_cells": total_cells,
                    "coverage": cov,
                    "pending": pending,
                    "revisions": len(overrides),
                    "checks_ok": checks_ok,
                    "checks_total": len(checks),
                    "unmapped": len(self._unmapped_fields()),
                    "exports": len(self.exports),
                },
                "workbook_summary": wb_summary,
                "error": self.error,
            }

    def open_folder(self, folder: str):
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请稍后再切换目录", 409)
            if not folder or not str(folder).strip():
                raise TiziError("请提供施工图目录路径", 400)
            p = Path(str(folder).strip())
            if not p.is_dir():
                raise TiziError(f"目录不存在：{folder}", 400)
            resolved = str(p.resolve())
            changed = resolved != self.folder
            self.folder = resolved
            self.project_name = p.resolve().name
            self.error = ""
            if changed or self.pipe is None:
                self._ensure_defaults(force=True)
                self.status = "idle"
                self.step = ""
                self.steps_done = []
                self.exports = []
                self.logs = []
                with self._event_cond:
                    self.stream_events = []
                    self._run_id = ""
                    self._run_elapsed_ms = 0
                    self._steps_plan = []
                self.pipe = self._new_pipeline()
            self._save()
            self._notify()
            return self.summary_state()

    # ------------------------------------------------------------------
    # 运行步骤
    def _normalize_steps(self, steps):
        if steps in (None, "", "extract"):
            return list(EXTRACT_STEPS)
        if steps == "all":
            return list(STEP_ORDER)
        if isinstance(steps, str):
            steps = [steps]
        if not isinstance(steps, list) or not steps:
            raise TiziError("steps 参数不能为空", 400)
        invalid = [s for s in steps if s not in STEP_ORDER]
        if invalid:
            raise TiziError(f"未知步骤：{', '.join(map(str, invalid))}", 400)
        return list(steps)

    def run_steps(self, steps="extract", confirm=False, async_run=True, on_complete=None):
        step_list = self._normalize_steps(steps)
        with self._lock:
            if not self.folder or not Path(self.folder).is_dir():
                raise TiziError("请先绑定有效的施工图目录", 400)
            if self._running:
                raise TiziError("提资任务正在执行中，请勿重复启动", 409)
            if self.pipe is None:
                self._ensure_defaults(force=False)
                self.pipe = self._new_pipeline()
            if "parse" in step_list and self.pipe.overrides and not confirm:
                return {
                    "ok": True,
                    "need_confirm": True,
                    "revisions": len(self.pipe.overrides),
                    "message": f"当前已有 {len(self.pipe.overrides)} 处人工修订，重新解析会清空这些修订，确认继续？",
                }
            self._running = True
            self.status = "running"
            self.error = ""
            self.step = step_list[0]
            self._run_id = f"run-{int(time.time() * 1000)}"
            self._run_started_at = time.time()
            self._run_elapsed_ms = 0
            self._steps_plan = self._build_stream_plan(step_list)
            with self._event_cond:
                self.stream_events = []
            if "discover" in step_list:
                self.steps_done = []
                self.logs = []
            if "parse" in step_list and "export" not in step_list:
                self.exports = []
                self.steps_done = [s for s in self.steps_done if s != "export"]
            sheets_order = [
                {"name": s.get("name"), "short_title": s.get("short_title") or s.get("name")}
                for s in (self.pipe.store.sheet_summaries() if self.pipe else [])
            ]
            self._push_stream_event("run_start", {
                "steps_plan": list(self._steps_plan),
                "sheets_order": sheets_order,
                "is_full": len(step_list) > 1,
            })
            self._notify()

        def worker():
            err_msg = ""
            try:
                method_map = {
                    "discover": lambda: self.pipe.step0_discover(self.folder),
                    "carrier": lambda: self.pipe.step1_carrier(self.folder),
                    "parse": self.pipe.step2_parse,
                    "mapping": self.pipe.step3_mapping,
                    "rules": self.pipe.step4_rules,
                    "validate": self.pipe.step5_validate,
                    "export": self.pipe.step6_output,
                }
                for st in step_list:
                    if st == "mapping" and "parse" not in step_list:
                        self._push_stream_event("stage_start", {"stage": "parse", "title": "模板字段映射"})
                    res = method_map[st]()
                    if st == "parse" and "mapping" not in step_list:
                        self._push_stream_event("stage_done", {"stage": "parse", "summary": f"{res.get('rows', 0)} 行还原完成"})
                    if st == "export" and isinstance(res, dict):
                        self._record_exports(res.get("names") or [])
                with self._lock:
                    self.status = "ready"
                    self.step = ""
                    self.error = ""
            except Exception as exc:  # noqa: BLE001
                err_msg = str(exc)
                with self._lock:
                    self.status = "error"
                    self.error = err_msg
            finally:
                with self._lock:
                    self._running = False
                    summary = self.summary_state()
                    sheets_snap = self.pipe.store.sheet_summaries() if self.pipe else []
                    checks_snap = list(self.pipe.workbook_checks or []) if self.pipe else []
                if err_msg:
                    self._push_stream_event("run_error", {"error": err_msg})
                else:
                    self._push_stream_event("run_done", {
                        "workbook_summary": summary.get("workbook_summary") or {},
                        "sheets": sheets_snap,
                        "workbook_checks": checks_snap,
                    })
                with self._lock:
                    self._save()
                self._notify()
                if on_complete:
                    try:
                        on_complete(summary, err_msg)
                    except Exception as exc:  # noqa: BLE001
                        _LOG.warning("TiziJob on_complete callback failed: %s", exc)

        if async_run:
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            return {"ok": True, "started": True, "run_id": self._run_id, "steps": step_list, "state": self.summary_state()}
        worker()
        return {"ok": self.status != "error", "started": False, "steps": step_list,
                "state": self.summary_state(), "error": self.error}

    def _record_exports(self, names):
        items = []
        for name in names:
            p = self.out_dir / name
            size = p.stat().st_size if p.is_file() else 0
            items.append({
                "name": name,
                "size": size,
                "desc": EXPORT_DESCRIPTIONS.get(name, "导出产物"),
            })
        self.exports = items

    # ------------------------------------------------------------------
    # 查询：状态 / 表格 / 单元格 / 修订 / 例外
    def _revisions_list(self):
        if not self.pipe:
            return []
        revs = []
        for (r, c), rec in sorted(self.pipe.overrides.items()):
            tower = ""
            if 0 <= r < len(self.pipe.rows):
                out = self.pipe.rows[r].get("out") or {}
                tower = out.get("杆塔号") or out.get("杆号") or ""
            kind = ("标待复核" if rec.get("flag") == "pending"
                    else "图纸框选" if rec.get("kind") == "drawing_select"
                    else "改值")
            revs.append({
                "row": r,
                "line_no": r + 1,
                "tower": tower,
                "column": c,
                "old": rec.get("old"),
                "value": rec.get("value"),
                "kind": kind,
                "time": rec.get("time", ""),
            })
        return revs

    def _exceptions(self):
        if not self.pipe:
            return {"pending": [], "checks": [], "unextractable": []}
        excluded_ids = {
            str(g.get("tower_id") or "")
            for g in (getattr(self.pipe.store, "excluded_gantries", None) or [])
            if g.get("tower_id")
        }
        pending = []
        v2_towers = (self.pipe.store.get_sheet("杆塔明细表") or {}).get("rows") or []
        if v2_towers:
            # 基于 V2 工作簿读取高优先级例外队列（冲突 + 缺失核心计算值），彻底排除已剥离构架（如 AN0/GJ-15）
            target_specs = [
                ("基本信息", None, True),
                ("结构设备材料表", None, True),
                ("电气设备材料表", None, True),
                ("光缆设备材料表", None, True),
                ("杆塔明细表", {"杆塔类型", "杆塔.全重(t)", "杆塔.呼高（m）", "接地装置.接地型式", "接地装置.常规接地镀锌接地材料重量（t）"}, False),
            ]
            for sname, only_cols, include_all_pending in target_specs:
                sh = self.pipe.store.get_sheet(sname) or {}
                for i, r in enumerate(sh.get("rows") or []):
                    out = r.get("out") or {}
                    anchor = out.get("杆塔号") or out.get("线路段名称") or out.get("名称及规格") or out.get("基础型号") or f"第 {i + 1} 行"
                    if sname == "杆塔明细表" and anchor in excluded_ids:
                        continue
                    provs = r.get("prov") or {}
                    cols = [c for c in out.keys() if (only_cols is None or c in only_cols)]
                    for col in cols:
                        v = out.get(col)
                        pv = provs.get(col) or {}
                        st = cell_display_status(v, pv)
                        if st == "conflict" or (st == "pending" and (include_all_pending or v in (None, "") or only_cols)):
                            pending.append({
                                "sheet": sname,
                                "row": i,
                                "tower": anchor,
                                "column": col,
                                "value": v,
                                "status": st,
                                "note": pv.get("note") or "待人工复核",
                                "method": pv.get("method") or "",
                            })
            # 冲突置顶，方便造价员先拍板冲突项
            pending.sort(key=lambda x: (0 if x.get("status") == "conflict" else 1))
        else:
            for i, r in enumerate(self.pipe.rows):
                out = r.get("out") or {}
                tower = out.get("杆塔号") or out.get("杆号") or f"第 {i + 1} 行"
                if tower in excluded_ids:
                    continue
                for t in self.pipe.targets:
                    v = out.get(t)
                    pv = (r.get("prov") or {}).get(t) or {}
                    if _cell_display_status(v, pv) == "pending":
                        pending.append({
                            "sheet": "杆塔明细表",
                            "row": i,
                            "tower": tower,
                            "column": t,
                            "value": v,
                            "status": "pending",
                            "note": pv.get("note") or "待人工复核",
                            "method": pv.get("method") or "",
                        })
        wb_chks = list(self.pipe.workbook_checks or [])
        checks = [c for c in (wb_chks or self.pipe.checks or []) if not c.get("ok")]
        unextractable = []
        prof = self.pipe.profile or {}
        for f in prof.get("files") or []:
            if f.get("kind") in ("drawing", "scan", "other"):
                unextractable.append({
                    "name": f.get("name") or f.get("rel"),
                    "rel": f.get("rel") or "",
                    "kind": f.get("kind") or "",
                    "pages": f.get("pages") or 0,
                    "reason": "；".join(f.get("evidence") or []) or "非 T2 坐标表格",
                })
        for s in self.pipe.src_info or []:
            if s.get("skipped") or s.get("error") or (s.get("carrier") and s.get("carrier") != "T2"):
                unextractable.append({
                    "name": s.get("name") or "",
                    "rel": s.get("path_rel") or "",
                    "kind": s.get("carrier") or "跳过",
                    "pages": s.get("pages") or 0,
                    "reason": s.get("reason") or s.get("error") or s.get("pipeline") or "",
                })
        return {"pending": pending, "checks": checks, "unextractable": unextractable}

    def status_payload(self):
        with self._lock:
            st = self.summary_state()
            prof = self.pipe.profile if self.pipe else None
            return {
                "ok": True,
                "tizi": st,
                "checks": list(self.pipe.checks or []) if self.pipe else [],
                "src_info": list(self.pipe.src_info or []) if self.pipe else [],
                "discover": {
                    "scanned": prof.get("scanned", 0) if prof else 0,
                    "duplicates": prof.get("duplicates", 0) if prof else 0,
                    "counts": prof.get("counts", {}) if prof else {},
                    "primary": prof.get("primary") if prof else None,
                    "files": prof.get("files", []) if prof else [],
                },
                "artifacts": dict(self.pipe.artifacts or {}) if self.pipe else {},
                "exceptions": self._exceptions(),
                "revisions": self._revisions_list(),
                "exports": list(self.exports),
                "unmapped": self._unmapped_fields(),
                "logs": list(self.logs[-40:]),
                "stream_trace": self.stream_trace_payload(),
                "sheets": self.pipe.store.sheet_summaries() if self.pipe else [],
                "workbook_checks": list(self.pipe.workbook_checks or []) if self.pipe else [],
                "excluded_gantries": list(self.pipe.store.excluded_gantries or []) if self.pipe else [],
                "hitl_review": self._hitl_review_summary(),
                "aux_sources": {
                    k: ({"name": v.get("name"), "rel": v.get("rel"), "pages": v.get("pages")}
                        if isinstance(v, dict) else [{"name": x.get("name"), "rel": x.get("rel")} for x in v])
                    for k, v in (self.pipe.aux_sources or {}).items()
                } if self.pipe else {},
            }

    def list_sheets(self):
        with self._lock:
            if not self.pipe:
                return {"ok": True, "sheets": [], "excluded_gantries": [], "workbook_checks": []}
            return {
                "ok": True,
                "sheets": self.pipe.store.sheet_summaries(),
                "excluded_gantries": list(self.pipe.store.excluded_gantries or []),
                "workbook_checks": list(self.pipe.workbook_checks or []),
            }

    def _serialize_sheet_cell(self, val, pv):
        pv = pv or {}
        evs = normalize_evidences(pv, resolve_rel=self.pipe.source_rel if self.pipe else None)
        first_loc = next((e for e in evs if e.get("file", "").lower().endswith(".pdf") and e.get("page")), None)
        return {
            "value": val,
            "machine_value": pv.get("machine_value", val),
            "status": cell_display_status(val, pv),
            "method": pv.get("method", ""),
            "source": pv.get("source", ""),
            "unit": pv.get("unit", ""),
            "formula_trace": pv.get("formula_trace", ""),
            "evidences": evs,
            "revisions": list(pv.get("revisions") or []),
            "conflict_candidates": list(pv.get("conflict_candidates") or []),
            "rel": first_loc["file"] if first_loc else "",
            "page": first_loc["page"] if first_loc else pv.get("page"),
            "bbox": first_loc["bbox"] if first_loc else pv.get("bbox"),
            "locate_rel": first_loc["file"] if first_loc else "",
            "locate_page": first_loc["page"] if first_loc else None,
            "locate_bbox": first_loc["bbox"] if first_loc else None,
            "conf": pv.get("conf"),
            "note": pv.get("note", ""),
            "revised": bool(pv.get("revisions")),
        }

    def table_page(self, offset=0, limit=100, flt="all", q="", sheet=None, show_defaults=False):
        if sheet:
            return self._sheet_table_page(sheet=sheet, offset=offset, limit=limit, flt=flt, q=q, show_defaults=show_defaults)
        with self._lock:
            if not self.pipe or not self.pipe.rows:
                return {
                    "ok": True, "offset": 0, "limit": limit, "total": 0,
                    "filtered_total": 0, "targets": [], "rows": [],
                    "unmapped": [], "summary": self.summary_state()["summary"],
                }
            targets = list(self.pipe.targets)
            q_low = (q or "").strip().lower()
            matched_indices = []
            for idx, r in enumerate(self.pipe.rows):
                out = r.get("out") or {}
                if q_low:
                    hay = " ".join(str(out.get(t) or "") for t in targets).lower()
                    if q_low not in hay:
                        continue
                if flt == "pending":
                    if not any(_cell_display_status(out.get(t), (r.get("prov") or {}).get(t)) == "pending"
                               for t in targets):
                        continue
                elif flt == "revised":
                    if not any((idx, t) in self.pipe.overrides for t in targets):
                        continue
                elif flt == "drawing":
                    if not any(((r.get("prov") or {}).get(t) or {}).get("method") == "drawing_select"
                               for t in targets):
                        continue
                matched_indices.append(idx)

            offset = max(0, int(offset))
            limit = max(1, min(500, int(limit)))
            page_indices = matched_indices[offset:offset + limit]
            out_rows = []
            for idx in page_indices:
                r = self.pipe.rows[idx]
                out = r.get("out") or {}
                cells = {}
                for t in targets:
                    val = out.get(t)
                    pv = (r.get("prov") or {}).get(t) or {}
                    ov = self.pipe.overrides.get((idx, t))
                    rel = self.pipe.source_rel(pv.get("source"))
                    loc_pv = pv if (rel.lower().endswith(".pdf") and pv.get("page") and pv.get("bbox")) else ((ov or {}).get("old_prov") or {})
                    loc_rel = rel if (rel.lower().endswith(".pdf") and pv.get("page") and pv.get("bbox")) else self.pipe.source_rel(loc_pv.get("source"))
                    cells[t] = {
                        "value": val,
                        "status": _cell_display_status(val, pv),
                        "method": pv.get("method", ""),
                        "source": pv.get("source", ""),
                        "rel": rel,
                        "page": pv.get("page"),
                        "bbox": pv.get("bbox"),
                        "locate_rel": loc_rel if loc_rel.lower().endswith(".pdf") else "",
                        "locate_page": loc_pv.get("page"),
                        "locate_bbox": loc_pv.get("bbox"),
                        "conf": pv.get("conf"),
                        "note": pv.get("note", ""),
                        "revised": bool(ov),
                        "old": ov.get("old") if ov else None,
                        "time": ov.get("time", "") if ov else "",
                    }
                out_rows.append({"row": idx, "line_no": idx + 1, "cells": cells})

            return {
                "ok": True,
                "offset": offset,
                "limit": limit,
                "total": len(self.pipe.rows),
                "filtered_total": len(matched_indices),
                "targets": targets,
                "rows": out_rows,
                "unmapped": self._unmapped_fields(),
                "summary": self.summary_state()["summary"],
            }

    def _sheet_table_page(self, sheet, offset=0, limit=100, flt="all", q="", show_defaults=False):
        with self._lock:
            if sheet not in SHEET_SCHEMAS:
                raise TiziError(f"未知工作表：{sheet}", 404)
            sc = SHEET_SCHEMAS[sheet]
            cols = [c for c in sc["columns"] if show_defaults or not c["is_default"]]
            col_keys = [c["key"] for c in cols]
            if not self.pipe:
                return {
                    "ok": True, "sheet": sheet, "view_mode": sc["view_mode"],
                    "offset": 0, "limit": limit, "total": 0, "filtered_total": 0,
                    "columns": cols, "targets": col_keys, "default_col_start": sc["default_col_start"],
                    "has_defaults": any(c["is_default"] for c in sc["columns"]),
                    "show_defaults": bool(show_defaults), "missing_source": "", "source_files": [],
                    "rows": [], "sheets": [], "summary": self.summary_state()["summary"],
                }
            sd = self.pipe.store.get_sheet(sheet)
            all_rows = sd.get("rows") or []
            q_low = (q or "").strip().lower()
            matched = []
            for idx, r in enumerate(all_rows):
                out = r.get("out") or {}
                prov = r.get("prov") or {}
                if q_low:
                    hay = " ".join(str(out.get(k) or "") for k in col_keys).lower()
                    if q_low not in hay:
                        continue
                if flt == "pending":
                    if not any(cell_display_status(out.get(k), prov.get(k)) in ("pending", "conflict") for k in col_keys):
                        continue
                elif flt == "conflict":
                    if not any(cell_display_status(out.get(k), prov.get(k)) == "conflict" for k in col_keys):
                        continue
                elif flt == "revised":
                    if not any((prov.get(k) or {}).get("revisions") for k in col_keys):
                        continue
                elif flt == "drawing":
                    if not any((prov.get(k) or {}).get("method") == "drawing_select" for k in col_keys):
                        continue
                matched.append(idx)

            offset = max(0, int(offset))
            limit = max(1, min(500, int(limit)))
            page_indices = matched[offset:offset + limit]
            out_rows = []
            for idx in page_indices:
                r = all_rows[idx]
                out = r.get("out") or {}
                prov = r.get("prov") or {}
                cells = {k: self._serialize_sheet_cell(out.get(k), prov.get(k)) for k in col_keys}
                out_rows.append({
                    "row": idx,
                    "line_no": idx + 1,
                    "row_id": r.get("row_id") or str(idx + 1),
                    "cells": cells,
                })
            return {
                "ok": True,
                "sheet": sheet,
                "view_mode": sc["view_mode"],
                "offset": offset,
                "limit": limit,
                "total": len(all_rows),
                "filtered_total": len(matched),
                "columns": cols,
                "targets": col_keys,
                "default_col_start": sc["default_col_start"],
                "has_defaults": any(c["is_default"] for c in sc["columns"]),
                "show_defaults": bool(show_defaults),
                "missing_source": sd.get("missing_source") or "",
                "source_files": list(sd.get("source_files") or []),
                "rows": out_rows,
                "sheets": self.pipe.store.sheet_summaries(),
                "summary": self.summary_state()["summary"],
            }

    def get_cell(self, row: int, column: str, sheet=None):
        if sheet:
            with self._lock:
                if not self.pipe:
                    raise TiziError("尚未解析数据", 400)
                sd = self.pipe.store.get_sheet(sheet)
                rows = sd.get("rows") or []
                if not (0 <= row < len(rows)):
                    raise TiziError("行号超出范围", 404)
                r = rows[row]
                out = r.get("out") or {}
                pv = (r.get("prov") or {}).get(column) or {}
                cell_ser = self._serialize_sheet_cell(out.get(column), pv)
                return {
                    "ok": True,
                    "sheet": sheet,
                    "row": row,
                    "tower": out.get("杆塔号") or out.get("基础型号") or out.get("材料名称") or "",
                    "column": column,
                    "value": cell_ser["value"],
                    "status": cell_ser["status"],
                    "prov": dict(pv, **cell_ser),
                    "revised": cell_ser["revised"],
                    "old": cell_ser["machine_value"],
                }
        with self._lock:
            if not self.pipe or not (0 <= row < len(self.pipe.rows)):
                raise TiziError("行号超出范围", 404)
            if column not in self.pipe.targets:
                raise TiziError(f"字段不存在：{column}", 404)
            r = self.pipe.rows[row]
            out = r.get("out") or {}
            val = out.get(column)
            pv = (r.get("prov") or {}).get(column) or {}
            ov = self.pipe.overrides.get((row, column))
            rel = self.pipe.source_rel(pv.get("source"))
            loc_pv = pv if (rel.lower().endswith(".pdf") and pv.get("page") and pv.get("bbox")) else ((ov or {}).get("old_prov") or {})
            loc_rel = rel if (rel.lower().endswith(".pdf") and pv.get("page") and pv.get("bbox")) else self.pipe.source_rel(loc_pv.get("source"))
            return {
                "ok": True,
                "row": row,
                "tower": out.get("杆塔号") or out.get("杆号") or "",
                "column": column,
                "value": val,
                "status": _cell_display_status(val, pv),
                "prov": dict(
                    pv,
                    rel=rel,
                    locate_rel=loc_rel if loc_rel.lower().endswith(".pdf") else "",
                    locate_page=loc_pv.get("page"),
                    locate_bbox=loc_pv.get("bbox"),
                ),
                "revised": bool(ov),
                "old": ov.get("old") if ov else val,
                "time": ov.get("time", "") if ov else "",
            }

    def edit_cell(self, row: int, column: str, value=None, flag=None, sheet=None, action="edit"):
        if sheet:
            with self._lock:
                if self._running:
                    raise TiziError("提资任务正在执行中，请完成后再修改", 409)
                if not self.pipe:
                    raise TiziError("尚未解析数据", 400)
                try:
                    res = self.pipe.store.edit_cell(
                        sheet, int(row), str(column), value=value, flag=flag, action=action,
                    )
                except ValueError as exc:
                    raise TiziError(str(exc), 400) from exc
                if hasattr(self.pipe, "on_sheet_cell_edited"):
                    self.pipe.on_sheet_cell_edited(sheet, int(row), str(column))
                self.pipe.recompute_workbook_checks()
                self._save()
                self._notify()
                return {
                    "ok": True, **res,
                    "cell": self.get_cell(int(row), str(column), sheet=sheet),
                    "sheets": self.pipe.store.sheet_summaries(),
                    "workbook_checks": self.pipe.workbook_checks,
                    "summary": self.summary_state()["summary"],
                    "checks": self.pipe.checks,
                }
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再修改", 409)
            if not self.pipe or not self.pipe.rows:
                raise TiziError("尚未解析数据", 400)
            res = self.pipe.apply_edit(int(row), str(column), value, flag=flag)
            if self.pipe.checks:
                self.pipe.checks = validate.run_checks(self.pipe.rows, self.pipe.rule_cfg)
            self._save()
            self._notify()
            return {"ok": True, **res, "cell": self.get_cell(int(row), str(column)),
                    "summary": self.summary_state()["summary"], "checks": self.pipe.checks}

    def revert_cell(self, row=None, column=None, sheet=None):
        if sheet:
            with self._lock:
                if self._running:
                    raise TiziError("提资任务正在执行中，请完成后再操作", 409)
                if not self.pipe:
                    raise TiziError("尚未解析数据", 400)
                res = self.pipe.store.revert_cell(
                    sheet,
                    int(row) if row is not None else None,
                    str(column) if column is not None else None,
                )
                self.pipe.recompute_workbook_checks()
                self._save()
                self._notify()
                return {
                    "ok": True,
                    "reverted": res.get("reverted", 0),
                    "sheets": self.pipe.store.sheet_summaries(),
                    "workbook_checks": self.pipe.workbook_checks,
                    "summary": self.summary_state()["summary"],
                    "checks": self.pipe.checks,
                }
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再操作", 409)
            if not self.pipe or not self.pipe.rows:
                raise TiziError("尚未解析数据", 400)
            n = self.pipe.revert_edits(
                int(row) if row is not None else None,
                str(column) if column is not None else None,
            )
            if self.pipe.checks:
                self.pipe.checks = validate.run_checks(self.pipe.rows, self.pipe.rule_cfg)
            self._save()
            self._notify()
            return {"ok": True, "reverted": n, "summary": self.summary_state()["summary"],
                    "checks": self.pipe.checks}

    # ------------------------------------------------------------------
    # 字段配对
    def get_mapping(self):
        with self._lock:
            if not self.pipe or not self.pipe.rows:
                return {"ok": True, "cfg_mode": "manual", "layout_id": "",
                        "columns": [], "fields": [], "stale": [],
                        "summary": {"total": 0, "auto": 0, "manual": 0, "confirmed": 0,
                                    "derived": 0, "rule": 0, "excluded": 0, "unmapped": 0,
                                    "needs_review": 0}}
            return self.pipe.mapping_state()

    def apply_mapping(self, action, target="", key="", label=""):
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再改配对", 409)
            if not self.pipe:
                raise TiziError("尚未打开提资作业", 400)
            ok, msg, view = self.pipe.apply_mapping(action, target=target, key=key, label=label)
            if not ok:
                raise TiziError(msg, 400)
            self._save()
            self._notify()
            return {"ok": True, "message": msg, "mapping": view,
                    "summary": self.summary_state()["summary"], "checks": self.pipe.checks}

    # ------------------------------------------------------------------
    # 图纸列表、探测、渲染、框选文字层、框选落格
    def list_drawings(self):
        with self._lock:
            if not self.folder or not Path(self.folder).is_dir():
                return {"ok": True, "files": []}
            prof = self.pipe.profile if self.pipe else None
            if prof and prof.get("files"):
                files = []
                for f in prof["files"]:
                    rel = str(f.get("rel") or "").replace("\\", "/")
                    p = Path(self.folder) / rel
                    files.append({
                        "rel": rel,
                        "name": f.get("name") or Path(rel).name,
                        "size": p.stat().st_size if p.is_file() else 0,
                        "kind": f.get("kind") or "",
                        "pages": f.get("pages") or 0,
                    })
                return {"ok": True, "files": files}

            seen = {}
            out = []
            root = Path(self.folder).resolve()
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if not d.startswith(".")]
                for fn in sorted(fns):
                    if not fn.lower().endswith(".pdf"):
                        continue
                    fp = Path(dp) / fn
                    rel = fp.relative_to(root).as_posix()
                    sz = fp.stat().st_size
                    key = (fn.lower(), sz)
                    if key in seen and len(rel) >= len(seen[key]["rel"]):
                        continue
                    rec = {"rel": rel, "name": fn, "size": sz, "kind": "", "pages": 0}
                    if key in seen:
                        out.remove(seen[key])
                    seen[key] = rec
                    out.append(rec)
            return {"ok": True, "files": out}

    def probe_drawing(self, rel_path: str):
        target = self.safe_drawing_path(rel_path)
        info = drawing.probe(str(target))
        info["rel"] = rel_path.replace("\\", "/")
        info["ok"] = True
        return info

    def render_drawing_page(self, rel_path: str, page=0, dpi=170, highlight=None, clip=None):
        target = self.safe_drawing_path(rel_path)
        try:
            data, size = drawing.render_page(
                str(target), page=int(page), dpi=int(dpi), clip=clip, highlight=highlight,
            )
        except IndexError as exc:
            raise TiziError(str(exc), 400) from exc
        return data, size

    def read_drawing_region(self, rel_path: str, page: int, bbox):
        target = self.safe_drawing_path(rel_path)
        try:
            reg = drawing.region_words(str(target), int(page), bbox)
        except IndexError as exc:
            raise TiziError(str(exc), 400) from exc
        raw_text = (reg.get("text") or "").strip()
        first_line = raw_text.splitlines()[0].strip() if raw_text else ""
        looks_value = bool(first_line) and first_line[0].isascii() \
                      and first_line[0].isalnum() and len(first_line) <= 24
        return {
            "ok": True,
            "readable": bool(reg["has_text"]),
            "has_text": bool(reg["has_text"]),
            "looks_value": looks_value,
            "suggested_value": first_line if looks_value else "",
            "text": reg["text"],
            "lines": reg["lines"],
            "bbox": reg["bbox"],
            "reason": reg["reason"],
            "page": int(page),
        }

    def ocr_drawing_region(self, rel_path: str, page: int, bbox):
        """对框选区域执行本机离线 OCR（候选值强制由人工确认，严禁自动落格）。"""
        from . import ocr_local
        target = self.safe_drawing_path(rel_path)
        try:
            res = ocr_local.ocr_pdf_region(str(target), int(page), bbox)
        except (IndexError, ValueError) as exc:
            raise TiziError(str(exc), 400) from exc
        return {"ok": True, "status": "pending", **res}

    def apply_drawing_value(self, row: int, column: str, value, rel_path: str, page: int, bbox, text="", sheet=None):
        self.safe_drawing_path(rel_path)
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise TiziError("bbox 必须为 4 个数值 [x0, y0, x1, y1]", 400)
        if sheet:
            with self._lock:
                if self._running:
                    raise TiziError("提资任务正在执行中，请完成后再写入", 409)
                if not self.pipe:
                    raise TiziError("尚未解析数据", 400)
                ev = {
                    "path": rel_path.replace("\\", "/"),
                    "page": int(page),
                    "bbox": [float(v) for v in bbox],
                    "text": str(text or ""),
                }
                res = self.pipe.store.edit_cell(sheet, int(row), str(column), value=value, evidence=ev)
                if hasattr(self.pipe, "on_sheet_cell_edited"):
                    self.pipe.on_sheet_cell_edited(sheet, int(row), str(column))
                self.pipe.recompute_workbook_checks()
                self._save()
                self._notify()
                return {
                    "ok": True, **res,
                    "cell": self.get_cell(int(row), str(column), sheet=sheet),
                    "sheets": self.pipe.store.sheet_summaries(),
                    "workbook_checks": self.pipe.workbook_checks,
                    "summary": self.summary_state()["summary"],
                    "checks": self.pipe.checks,
                }
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再写入", 409)
            if not self.pipe or not self.pipe.rows:
                raise TiziError("尚未解析数据", 400)
            ev = {
                "path": rel_path.replace("\\", "/"),
                "page": int(page),
                "bbox": [float(v) for v in bbox],
                "text": str(text or ""),
            }
            res = self.pipe.apply_edit(int(row), str(column), value, evidence=ev)
            if self.pipe.checks:
                self.pipe.checks = validate.run_checks(self.pipe.rows, self.pipe.rule_cfg)
            self._save()
            self._notify()
            return {"ok": True, **res, "cell": self.get_cell(int(row), str(column)),
                    "summary": self.summary_state()["summary"], "checks": self.pipe.checks}

    # ------------------------------------------------------------------
    # 价格库只读联动匹配（绝不写回平台价格库）
    def match_prices(self, sheet_name: str, prices_snapshot: list):
        from . import price_match
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再匹配参考单价", 409)
            if not self.pipe:
                raise TiziError("尚未解析数据", 400)
            try:
                res = price_match.match_sheet_prices(self.pipe.store, sheet_name, prices_snapshot)
            except ValueError as exc:
                raise TiziError(str(exc), 400) from exc
            self._save()
            self._notify()
            return {
                "ok": True,
                **res,
                "sheets": self.pipe.store.sheet_summaries(),
            }

    # ------------------------------------------------------------------
    # 双版本导出
    def export_files(self):
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请完成后再导出", 409)
            if not self.pipe or not self.pipe.checks:
                raise TiziError("请先完成扫描解析与双源校验，再导出", 400)
            bundle = self.pipe.export_bundle()
            self._record_exports(bundle.get("names") or [])
            if "export" not in self.steps_done:
                self.steps_done.append("export")
            self._save()
            self._notify()
            return {
                "ok": True,
                "exports": list(self.exports),
                "n_revisions": bundle.get("n_revisions", 0),
                "warnings": bundle.get("warnings", []),
                "summary": self.summary_state()["summary"],
            }

    # ------------------------------------------------------------------
    # Graph Harness 运行时集成（N1~N9 状态机 + SQLite Checkpoint + HITL 中断/恢复）
    def _build_graph_runner(self, gateway=None):
        if self.pipe is None:
            self._ensure_defaults(force=False)
            self.pipe = self._new_pipeline()
        if gateway is None and callable(self.vision_config_fn):
            try:
                vc = self.vision_config_fn() or {}
                if vc.get("base_url") and vc.get("api_key"):
                    gateway = VlmGateway(
                        endpoint=str(vc["base_url"]),
                        model=str(vc.get("model") or "qwen3.8-max"),
                        api_key=str(vc["api_key"]),
                    )
            except Exception:  # noqa: BLE001
                gateway = None
        return TiziGraphRunner(
            workspace_dir=self.job_dir,
            folder=self.folder,
            session_id=self.workspace.session_id,
            pipeline=self.pipe,
            store=self.pipe.store,
            gateway=gateway,
            cache_dir=str(self.job_dir.parent / "cache"),
            out_dir=str(self.out_dir),
            emit_cb=lambda kind, payload: self._push_stream_event(kind, payload),
        )

    def run_harness_graph(
        self,
        *,
        max_revisits: int = 2,
        max_vlm_crops: int = 24,
        max_vlm_schema_crops: int = 4,
        hitl_required: bool = True,
        gateway=None,
        async_run: bool = False,
        on_complete=None,
    ):
        """通过 LangGraph StateGraph 执行全流程（支持冲突视觉重访与 N7 HITL 持久化挂起）。"""
        with self._lock:
            if not self.folder or not Path(self.folder).is_dir():
                raise TiziError("请先绑定有效的施工图目录", 400)
            if self._running:
                raise TiziError("提资任务正在执行中，请勿重复启动", 409)
            self._ensure_defaults(force=False)
            if self.pipe is None:
                self.pipe = self._new_pipeline()
            self._running = True
            self.status = "running"
            self.error = ""
            self.exports = []
            self._run_id = f"graph-{int(time.time() * 1000)}"
            self._run_started_at = time.time()
            with self._event_cond:
                self.stream_events = []
            self._push_stream_event("run_start", {
                "steps_plan": self._build_stream_plan(list(EXTRACT_STEPS)),
                "is_full": True,
                "harness": "langgraph",
            })

        def _exec():
            err_msg = ""
            snap = {}
            runner = None
            try:
                runner = self._build_graph_runner(gateway=gateway)
                snap = runner.start(
                    run_id=self._run_id,
                    max_revisits=max_revisits,
                    max_vlm_crops=max_vlm_crops,
                    max_vlm_schema_crops=max_vlm_schema_crops,
                    hitl_required=hitl_required,
                )
                with self._lock:
                    if snap.get("status") == "paused_hitl":
                        self.status = "paused_hitl"
                    else:
                        self.status = "ready"
                    if snap.get("export_files"):
                        self._record_exports(snap["export_files"])
                    self.steps_done = list(EXTRACT_STEPS) + (["export"] if snap.get("export_files") else [])
                    save_store_to_workspace(self.pipe.store, self.workspace)
            except Exception as exc:  # noqa: BLE001
                err_msg = str(exc)
                with self._lock:
                    self.status = "error"
                    self.error = err_msg
            finally:
                if runner is not None:
                    runner.close()
                with self._lock:
                    self._running = False
                    summary = self.summary_state()
                    self._save()
                if err_msg:
                    self._push_stream_event("run_error", {"error": err_msg})
                else:
                    self._push_stream_event("run_done", {
                        "harness_status": snap.get("status", self.status),
                        "interrupt": snap.get("interrupt"),
                        "workbook_summary": summary.get("workbook_summary") or {},
                    })
                self._notify()
                if on_complete:
                    try:
                        on_complete(summary, err_msg)
                    except Exception as exc:  # noqa: BLE001
                        _LOG.warning("TiziJob harness on_complete failed: %s", exc)
            return snap

        if async_run:
            t = threading.Thread(target=_exec, daemon=True)
            t.start()
            return {"ok": True, "started": True, "run_id": self._run_id, "state": self.summary_state()}
        snap = _exec()
        return {"ok": self.status != "error", "started": False, "graph": snap, "state": self.summary_state(), "error": self.error}

    def resume_hitl(
        self,
        *,
        decisions=None,
        approve_export: bool = True,
        promote_memory: bool = False,
        allow_draft_skip: bool = False,
        gateway=None,
    ):
        """从 N7 SQLite 检查点恢复执行（支持 server.py 跨重启无缝接续 N8 -> N9）。"""
        with self._lock:
            if self._running:
                raise TiziError("提资任务正在执行中，请稍后恢复", 409)
            runner = self._build_graph_runner(gateway=gateway)
        try:
            snap = runner.resume(
                decisions=decisions or [],
                approve_export=approve_export,
                promote_memory=promote_memory,
                allow_draft_skip=allow_draft_skip,
            )
            with self._lock:
                self.status = snap.get("status") or "ready"
                if snap.get("export_files"):
                    self._record_exports(snap["export_files"])
                    if "export" not in self.steps_done:
                        self.steps_done.append("export")
                self._save()
                self._notify()
            return {"ok": True, "graph": snap, "exports": list(self.exports), "state": self.summary_state()}
        finally:
            runner.close()

    def _hitl_review_summary(self):
        saved = self.workspace.read_json("07_hitl/review_queue.json", default=None) or {}
        decisions = self.workspace.read_jsonl("07_hitl/human_decisions.jsonl")
        gate = None
        live_items = []
        if self.pipe and self.pipe.store:
            gate = evaluate_export_gate(self.pipe.store, human_decisions=decisions).model_dump()
            # 实时基于当前 store 门禁重建 P0/P1 条目，并附带 conflict_candidates 供中间栏审核卡片一键裁决
            for raw_it in list(gate.get("p0_blocking") or []) + list(gate.get("p1_unconfirmed") or []):
                it = dict(raw_it)
                sd = self.pipe.store.get_sheet(it.get("sheet") or "") or {}
                r_idx = it.get("row_idx")
                col_key = it.get("col_key") or ""
                if r_idx is not None and 0 <= int(r_idx) < len(sd.get("rows") or []):
                    pv = ((sd["rows"][int(r_idx)].get("prov") or {}).get(col_key)) or {}
                    cands = pv.get("conflict_candidates")
                    if isinstance(cands, list) and cands:
                        it["conflict_candidates"] = cands
                    if not it.get("evidence_uri") and pv.get("rel"):
                        it["evidence_file"] = pv.get("rel")
                        it["evidence_page"] = pv.get("page")
                live_items.append(it)
            items = live_items[:40]
        else:
            items = [it for it in (saved.get("items") or []) if it.get("priority") in ("P0", "P1")][:40]
        return {
            "p0_count": len((gate or {}).get("p0_blocking") or []) if gate else int(saved.get("p0_count") or 0),
            "p1_count": len((gate or {}).get("p1_unconfirmed") or []) if gate else int(saved.get("p1_count") or 0),
            "p2_count": int(saved.get("p2_count") or 0),
            "export_gate": gate,
            "items": items,
        }

    def safe_artifact_bytes(self, rel_uri: str) -> tuple[bytes, str]:
        try:
            target = self.workspace.resolve_safe(rel_uri)
        except Exception as exc:
            raise TiziError(f"非法工件路径：{exc}", 403) from exc
        if not target.is_file():
            raise TiziError("工件文件不存在", 404)
        suf = target.suffix.lower()
        ctype = "image/png" if suf == ".png" else ("application/json; charset=utf-8" if suf == ".json" else "application/octet-stream")
        return target.read_bytes(), ctype

    def get_review_queue(self):
        """读取 07_hitl/review_queue.json 与实时导出门禁状态。"""
        saved = self.workspace.read_json("07_hitl/review_queue.json", default=None)
        decisions = self.workspace.read_jsonl("07_hitl/human_decisions.jsonl")
        gate = None
        if self.pipe and self.pipe.store:
            gate = evaluate_export_gate(self.pipe.store, human_decisions=decisions).model_dump()
        return {
            "ok": True,
            "status": self.status,
            "review_queue": saved or {"items": [], "p0_count": 0, "p1_count": 0, "p2_count": 0},
            "decisions_count": len(decisions),
            "export_gate": gate,
        }

    def list_checkpoints(self, limit: int = 20):
        runner = self._build_graph_runner()
        try:
            return {"ok": True, "checkpoints": runner.list_checkpoints(limit=limit)}
        finally:
            runner.close()

    def replay_checkpoint(self, checkpoint_id: str, gateway=None):
        if not checkpoint_id:
            raise TiziError("checkpoint_id 不能为空", 400)
        runner = self._build_graph_runner(gateway=gateway)
        try:
            snap = runner.replay_from_checkpoint(checkpoint_id)
            with self._lock:
                self.status = snap.get("status") or "ready"
                self._save()
                self._notify()
            return {"ok": True, "graph": snap, "state": self.summary_state()}
        finally:
            runner.close()
