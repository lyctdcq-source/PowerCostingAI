# -*- coding: utf-8 -*-
"""离线多设计院 Golden Dataset 自动评测脚本（输出 §E 8 项验收 KPI 报告）。

用法：
  python eval/run_golden_eval.py --folder <施工图目录> [--golden <人工收口真值JSON>] [--out <报告JSON>]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tizi.job import TiziJob  # noqa: E402
from tizi.store import SHEET_ORDER, SHEET_SCHEMAS  # noqa: E402


def compute_metrics(job: TiziJob, golden: dict | None = None, elapsed_s: float = 0.0) -> dict:
    store = job.pipe.store
    summaries = store.sheet_summaries()
    total_core = sum(s["rows"] * s["core_cols"] for s in summaries)
    total_filled = sum(s["filled"] for s in summaries)
    overall_completeness = round(total_filled / total_core * 100, 2) if total_core else 0.0

    chks = list(job.pipe.workbook_checks or [])
    chks_ok = sum(1 for c in chks if c.get("ok"))
    chk08 = next((c for c in chks if c.get("code") == "CHK-08"), None)
    chk04 = next((c for c in chks if c.get("code") == "CHK-04"), None)

    fdn_lib = (store.get_sheet("基础库") or {}).get("rows") or []
    fdn_filled = sum(1 for r in fdn_lib if (r.get("out") or {}).get("基础.砼量(m³)") not in (None, ""))

    report = {
        "elapsed_s": round(elapsed_s, 3),
        "overall_core_completeness_pct": overall_completeness,
        "checks_passed": f"{chks_ok}/{len(chks)}",
        "chk04_ok": bool(chk04.get("ok")) if chk04 else None,
        "chk08_ok": bool(chk08.get("ok")) if chk08 else None,
        "foundation_models_filled": f"{fdn_filled}/{len(fdn_lib)}",
        "sheets": {s["name"]: {"rows": s["rows"], "coverage": s["coverage"], "pending": s["pending"], "conflict": s["conflict"]} for s in summaries},
        "trace": job.workspace.read_jsonl("10_traces/trace.jsonl")[-5:],
    }

    if golden and isinstance(golden.get("sheets"), dict):
        tp = fp = fn = 0
        for sname, g_rows in golden["sheets"].items():
            if sname not in SHEET_SCHEMAS:
                continue
            pred_rows = (store.get_sheet(sname) or {}).get("rows") or []
            for idx, gr in enumerate(g_rows):
                pr = (pred_rows[idx].get("out") or {}) if idx < len(pred_rows) else {}
                for k, gv in gr.items():
                    pv = pr.get(k)
                    if gv in (None, "") and pv in (None, ""):
                        continue
                    if isinstance(gv, (int, float)) and isinstance(pv, (int, float)):
                        ok = abs(float(pv) - float(gv)) <= max(1e-3, abs(float(gv)) * 0.005)
                    else:
                        ok = str(pv or "").strip() == str(gv or "").strip()
                    if ok:
                        tp += 1
                    else:
                        if pv not in (None, ""):
                            fp += 1
                        if gv not in (None, ""):
                            fn += 1
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, tp + fn)
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        report["golden_micro_f1"] = round(f1, 4)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PowerCostingAI Tizi Agent Golden Evaluator")
    parser.add_argument("--folder", required=True, help="施工图目录路径")
    parser.add_argument("--golden", default="", help="可选人工真值 JSON 路径")
    parser.add_argument("--out", default="", help="评测结果 JSON 输出路径")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8")) if args.golden else None
    with tempfile.TemporaryDirectory(prefix="tizi_eval_") as tmp:
        job = TiziJob(Path(tmp) / "session")
        job.open_folder(args.folder)
        t0 = time.perf_counter()
        job.run_harness_graph(hitl_required=False, async_run=False)
        elapsed = time.perf_counter() - t0
        report = compute_metrics(job, golden=golden, elapsed_s=elapsed)

    txt = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
