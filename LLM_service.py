# -*- coding: utf-8 -*-
"""LLM_service.py —— 电力工程物资匹配引擎宿主（无 GPU 依赖）。

匹配与评审的业务逻辑已全部拆为平台流程技能（skill_templates/pipeline，运行时副本在数据目录
pipeline_skills），可在“技能开发与管理”中查看、修改、校验和回退。本文件只负责宿主职责：
模型接口配置与调用、执行日志、领域知识加载、结果文件写出，并保留原有函数名供平台调用。

流水线（顺序见流程技能“流程·匹配与评审编排”）
  1. 候选配对    ：清册目录作固定前缀，估算项分批送入模型，逐条给候选与置信，全局一对一分配
  2. 规则校验    ：型号强特征、领域知识身份属性做确定性复核
  3. 成套项比对  ：造价整套计价的成套项与清册成套行、组成件整体比对，组成件不逐条匹配
  4. 核对单元分组：按原表成套项与直接上级章节分组，不调用模型
  5. 评审        ：逐行核对、成套项整体复核、核对单元定档计分，模型写复核意见
  6. 导出        ：match_details_*.json + match_results_*.xlsx

用法
----
  python LLM_service.py                 # 自动读同目录 .dskey，跑 gs_data0807 → qc_data0807
  python LLM_service.py <key文件路径>
  设置环境变量 DEEPSEEK_API_KEY 亦可
"""
import json
import os
import sys
import time
import uuid
import warnings

import requests  # 模型请求经 llm_client 发出；保留引用便于测试替换传输层

from pipeline_skills import EngineContext, PipelineStore

try:
    from domain_kg import DomainKnowledge
except ImportError:  # 没有领域知识库时，各技能退回原有算法
    DomainKnowledge = None

# Windows 控制台默认 GBK，批注里有 ✅ 等字符，不改编码会 UnicodeEncodeError 崩掉
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.environ.get("LLM_RESULTS_PATH", os.path.join(BASE_DIR, "results"))
os.makedirs(RESULTS_PATH, exist_ok=True)

DEEPSEEK_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")
MATCH_BATCH = 20              # 每批送入的估算条数（技能通过 ctx.setting 读取）
MAX_WORKERS = 4               # 配对并发数
MATCH_GUIDANCE = ""           # 平台规则技能“物资数量匹配”的专家提示，追加到配对提示词

MATCH_STATUS_CACHE = {}
_ACTIVE_API_KEY = ""
_DOMAIN_KNOWLEDGE = None


def _knowledge():
    """领域知识库（品类身份属性、计量参数、成套组成）。缺失时返回 None，各技能退回原逻辑。"""
    global _DOMAIN_KNOWLEDGE
    if _DOMAIN_KNOWLEDGE is None and DomainKnowledge is not None:
        try:
            _DOMAIN_KNOWLEDGE = DomainKnowledge()
        except (OSError, ValueError, KeyError) as exc:
            print(f"领域知识库加载失败，按原逻辑运行：{exc}")
            _DOMAIN_KNOWLEDGE = False
    return _DOMAIN_KNOWLEDGE or None


def _log(task_id, msg, progress=None):
    print("[LLM_service][%s] %s" % (task_id[:8], msg), flush=True)
    if progress is not None:
        MATCH_STATUS_CACHE[task_id] = {"status": "processing", "message": msg, "progress": progress}


WEB_SEARCH = True             # 匹配与评审中联网检索物资与换算参数（仅 DeepSeek 官方接口可用；平台按模型设置开关）
WEB_SEARCH_BUDGET = 120       # 每次运行最多检索次数，按次计费
_WEB_CACHE = {}
_WEB_USED = [0]


def _web_search(query, count=5):
    """技能通过 ctx.web_search 调用。同一搜索词只检索一次；超出次数上限、未开启或接口不支持时返回空列表。"""
    import web_search
    query = " ".join(str(query or "").split())[:128]
    if not (WEB_SEARCH and query and _ACTIVE_API_KEY and web_search.available(DEEPSEEK_URL)):
        return []
    if query in _WEB_CACHE:
        return _WEB_CACHE[query]
    if _WEB_USED[0] >= WEB_SEARCH_BUDGET:
        return []
    _WEB_USED[0] += 1
    try:
        results = web_search.search(DEEPSEEK_URL, _ACTIVE_API_KEY, query, count)
    except RuntimeError as exc:
        print(f"联网检索“{query}”失败：{exc}")
        results = []
    _WEB_CACHE[query] = results
    return results


def _ask_llm(prompt, api_key, max_tokens=3000, retries=3):
    from llm_client import request_json
    return request_json(DEEPSEEK_URL, MODEL, api_key,
                        [{"role": "system", "content": "只返回 JSON 对象。"},
                         {"role": "user", "content": prompt}],
                        max_tokens=max_tokens, retries=retries)


# 平台把数据目录写入环境变量后加载本模块，技能读用户修改过的版本；单独运行时读内置模板。
PIPELINE = PipelineStore(os.environ.get("POWER_COST_PIPELINE_DATA") or None)
SKILLS = PIPELINE.instantiate(EngineContext(globals()))


# ---------------------------------------------------------------- 平台调用入口（转交流程技能）
def llm_pair(sources, targets, api_key, task_id):
    return SKILLS.candidate_pairing.llm_pair(sources, targets, api_key, task_id)


def rule_refine(pairs, sources, targets, task_id):
    return SKILLS.rule_check.rule_refine(pairs, sources, targets, task_id)


def match_pairs(sources, targets, api_key, task_id):
    """返回 (一对一配对, 成套项覆盖)。"""
    return SKILLS.match_flow.pair(sources, targets, api_key, task_id)


def build_rows(sources, targets, pairs, task_id, api_key, covers=None):
    return SKILLS.check_units.build_rows(sources, targets, pairs, covers or {}, task_id, api_key)


def review(rows, api_key, task_id, sources=None, targets=None):
    return SKILLS.match_flow.review(rows, api_key, task_id, sources or [], targets or [])


def _mass_length_conversion_review(source_item, target_item, for_scoring=False):
    return SKILLS.quantity_rules._mass_length_conversion_review(source_item, target_item, for_scoring)


def _steel_pipe_kg_per_m(text):
    return SKILLS.quantity_rules._steel_pipe_kg_per_m(text)


def _resolve_density_via_llm(material_text):
    return SKILLS.quantity_rules._resolve_density_via_llm(material_text)


# ---------------------------------------------------------------- 导出与独立运行
def export(rows, task_id):
    import pandas as pd  # 仅导出 Excel 时加载；本地智能体的 CSV/JSON 导出无需 pandas。
    detail_path = os.path.join(RESULTS_PATH, "match_details_%s.json" % task_id)
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    columns = SKILLS.result_export.columns()
    xlsx_path = os.path.join(RESULTS_PATH, "match_results_%s.xlsx" % task_id)
    pd.DataFrame(SKILLS.result_export.flatten(rows), columns=columns).to_excel(xlsx_path, index=False)
    return detail_path, xlsx_path


def run_match(sources, targets, api_key, task_id=None):
    task_id = task_id or str(uuid.uuid4())
    started = time.time()
    _log(task_id, "开始：估算 %d 条 / 清册 %d 条（无 GPU 依赖）" % (len(sources), len(targets)), 0.02)
    pairs, covers = match_pairs(sources, targets, api_key, task_id)
    rows = build_rows(sources, targets, pairs, task_id, api_key, covers)
    rows = review(rows, api_key, task_id, sources, targets)
    detail, xlsx = export(rows, task_id)
    MATCH_STATUS_CACHE[task_id] = {
        "status": "success",
        "message": "完成：%d 行，配对 %d 对，成套项 %d 项，耗时 %.1f 秒" % (len(rows), len(pairs), len(covers), time.time() - started),
        "progress": 1.0, "filename": os.path.basename(detail), "xlsx": os.path.basename(xlsx)}
    _log(task_id, MATCH_STATUS_CACHE[task_id]["message"], 1.0)
    return {"task_id": task_id, "rows": rows, "detail_path": detail, "xlsx_path": xlsx, "pairs": len(pairs), "covers": covers}


def resolve_api_key(key_file=""):
    """按 命令行参数 → 同目录 .dskey → 环境变量 的顺序找 key。"""
    if key_file and os.path.exists(key_file):
        return open(key_file, encoding="utf-8").read().strip()
    default_key = os.path.join(BASE_DIR, ".dskey")
    if os.path.exists(default_key):
        return open(default_key, encoding="utf-8").read().strip()
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


if __name__ == "__main__":
    api_key = resolve_api_key(sys.argv[1] if len(sys.argv) > 1 else "")
    if not api_key:
        raise SystemExit(
            "未找到 DeepSeek api_key，三种方式任选其一：\n"
            "  1) 在本目录放一个 .dskey 文件，内容为 key（推荐，直接 python LLM_service.py 即可）\n"
            "  2) python LLM_service.py <key文件路径>\n"
            "  3) 设置环境变量 DEEPSEEK_API_KEY")
    src = json.load(open(os.path.join(BASE_DIR, "gs_data0807.json"), encoding="utf-8"))
    tgt = json.load(open(os.path.join(BASE_DIR, "qc_data0807.json"), encoding="utf-8"))
    result = run_match(src, tgt, api_key)
    print("明细:", result["detail_path"])
    print("表格:", result["xlsx_path"])
