# -*- coding: utf-8 -*-
"""Tool: vlm_learn_layout_schema — 跨设计院表头一次性视觉学习 + 5 行抽样物理值域自检门。"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from ..grid_parser import GridCellExtractor
from ..harness.gateway import VlmGateway
from ..harness.memory import EnterpriseMemory
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..prompts import load_prompt
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult
from ..schemas.layout import LayoutProfile, SampleSanityReport
from .pdf_grid import compute_header_fingerprint
from .pdf_render import pdf_crop_render

# 核心标准字段的确定性物理值域守卫（防 VLM 表头映射错 1 列导致整表污染）
_PHYSICAL_RANGES: dict[str, tuple[float, float]] = {
    "呼称高": (6.0, 120.0),
    "杆塔.全高（m）": (8.0, 160.0),
    "杆塔.全重(t)": (0.3, 220.0),
    "全重": (0.3, 220.0),
    "基础.砼量(m³)": (0.2, 500.0),
    "基础钢筋量（kg）.HRB400": (0.0, 200000.0),
    "推荐": (1.0, 50000.0),
    "推荐电阻率": (1.0, 50000.0),
}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

_CANDIDATE_FIELDS_BY_SHEET: dict[str, list[str]] = {
    "杆塔明细表": ["杆塔号", "杆塔型号", "呼称高", "全重", "线路转角", "基础型号.A腿", "基础型号.B腿", "基础型号.C腿", "基础型号.D腿", "备注"],
    "土壤电阻率": ["塔号", "桩号", "推荐电阻率", "岩土名称", "备注"],
    "设备材料表": ["序号", "材料名称", "规格型号", "单位", "数量", "单重", "备注"],
}

_LOCAL_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "杆塔明细表": {
        "杆塔号": ("杆塔号", "塔号", "杆号", "桩号"),
        "杆塔型号": ("杆塔型号", "塔型", "杆塔型式", "杆型"),
        "呼称高": ("呼称高", "呼高", "标称高"),
        "全重": ("全重", "单基重", "塔重", "铁塔重", "杆重"),
        "线路转角": ("转角", "线路转角", "角度"),
        "档距": ("档距", "水平档距", "代表档距"),
        "基础型号.A腿": ("基础型号", "基础型式", "a腿", "基础a", "A腿"),
        "基础型号.B腿": ("b腿", "基础b", "B腿"),
        "基础型号.C腿": ("c腿", "基础c", "C腿"),
        "基础型号.D腿": ("d腿", "基础d", "D腿"),
        "备注": ("备注", "附注", "说明"),
    },
    "土壤电阻率": {
        "塔号": ("塔号", "杆塔号", "杆号"),
        "桩号": ("桩号", "里程"),
        "推荐电阻率": ("推荐电阻率", "推荐", "选用值", "设计值", "土壤电阻率"),
        "岩土名称": ("岩土名称", "岩土", "土质", "地质名称", "地质描述"),
        "备注": ("备注", "说明"),
    },
    "设备材料表": {
        "序号": ("序号", "编号"),
        "材料名称": ("材料名称", "设备名称", "器材名称", "物资名称", "名称"),
        "规格型号": ("材料规格或型号", "规格或图号", "规格型号", "型号规格", "规格"),
        "单位": ("单位", "计量单位"),
        "数量": ("设计用量", "数量", "净量", "计价数量", "最终数量"),
        "单重": ("单位重", "单重"),
        "备注": ("备注", "物资类别", "供货"),
    },
}

_LOCAL_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "杆塔明细表": ("杆塔号", "杆塔型号", "呼称高"),
    "土壤电阻率": ("塔号", "推荐电阻率"),
    "设备材料表": ("材料名称", "单位", "数量"),
}


def compute_local_header_match_score(target_sheet: str, header_texts: list[str]) -> tuple[float, dict[int, str]]:
    """计算本地标准表头同义词匹配分 (0.0~1.0) 与 col_idx -> canonical_field 映射。"""
    syn_map = _LOCAL_SYNONYMS.get(target_sheet) or {}
    if not syn_map or not header_texts:
        return 0.0, {}

    col_map: dict[int, str] = {}
    used_fields: set[str] = set()
    for idx, raw in enumerate(header_texts):
        norm = re.sub(r"[\s（）()\[\]:：/]+", "", str(raw or "")).lower()
        if not norm:
            continue
        best_field = ""
        best_len = 0
        for field, aliases in syn_map.items():
            if field in used_fields:
                continue
            for al in aliases:
                al_low = al.lower()
                if al_low in norm and len(al_low) > best_len:
                    best_field = field
                    best_len = len(al_low)
        if best_field:
            col_map[idx] = best_field
            used_fields.add(best_field)

    non_empty = [h for h in header_texts if str(h or "").strip()]
    req = _LOCAL_REQUIRED_KEYS.get(target_sheet) or ()
    if req:
        req_hit = sum(1 for r in req if r in used_fields)
        req_ratio = req_hit / float(len(req))
    else:
        req_ratio = min(1.0, len(col_map) / 3.0)
    # 综合核心必填命中率与映射列覆盖度：紧凑表格若仍有未识别列（如“单基耗钢(kg)”）须交由 VLM 学习
    col_cov = len(col_map) / float(max(1, len(non_empty)))
    if len(non_empty) <= 6:
        score = round(min(1.0, min(req_ratio, col_cov) * 0.7 + req_ratio * 0.3), 3)
    else:
        score = round(min(1.0, req_ratio * 0.75 + min(1.0, len(col_map) / max(4, len(req) + 1)) * 0.25), 3)
    return score, col_map


def _to_float_or_none(val: Any) -> Optional[float]:
    if val in (None, "", "-", "—"):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    m = _NUM_RE.search(str(val))
    return float(m.group()) if m else None


def run_sample_sanity_check(
    profile: LayoutProfile,
    sample_rows: list[list[str]],
    *,
    min_non_empty_rate: float = 0.6,
) -> SampleSanityReport:
    """5 行抽样值域自检门：验证 VLM 产出的列映射在真实数据行上满足物理范围与格式约束。"""
    checked = sample_rows[:5]
    if not checked or not profile.column_mapping:
        return SampleSanityReport(
            passed=False,
            checked_rows=len(checked),
            non_empty_rate=0.0,
            violations=["抽样数据行为空或 column_mapping 为空"],
        )

    violations: list[str] = []
    preview: list[dict[str, Any]] = []
    total_cells = 0
    filled_cells = 0
    tower_re = None
    if profile.tower_id_regex:
        try:
            tower_re = re.compile(profile.tower_id_regex, re.IGNORECASE)
        except re.error as exc:
            violations.append(f"tower_id_regex 非法：{exc}")

    unit_overrides = profile.unit_overrides or {}

    for r_idx, row_texts in enumerate(checked):
        rec: dict[str, Any] = {}
        for col_idx, field_name in profile.column_mapping.items():
            total_cells += 1
            raw_v = str(row_texts[col_idx]).strip() if 0 <= col_idx < len(row_texts) else ""
            rec[field_name] = raw_v
            if raw_v and raw_v not in ("-", "—"):
                filled_cells += 1
            else:
                continue

            # 1) 物理值域校验（考虑 unit_overrides 如 kg->t 换算后的合理区间）
            if field_name in _PHYSICAL_RANGES:
                num = _to_float_or_none(raw_v)
                if num is None:
                    violations.append(f"第{r_idx + 1}行 {field_name}='{raw_v}' 非数值，疑似列错位")
                else:
                    lo, hi = _PHYSICAL_RANGES[field_name]
                    u = str(unit_overrides.get(field_name) or "").lower()
                    if field_name in ("全重", "杆塔.全重(t)") and u in ("kg", "公斤", "千克"):
                        num = num * 0.001
                    if not (lo <= num <= hi):
                        violations.append(f"第{r_idx + 1}行 {field_name}={num:g} 超出合理物理区间 [{lo}, {hi}]")

            # 2) 塔号正则校验
            if field_name in ("杆塔号", "塔号") and tower_re is not None:
                if not tower_re.match(raw_v.upper()):
                    violations.append(f"第{r_idx + 1}行 {field_name}='{raw_v}' 不匹配塔号正则 {profile.tower_id_regex}")
        preview.append(rec)

    rate = round(filled_cells / max(1, total_cells), 3)
    if rate < min_non_empty_rate:
        violations.append(f"映射列非空率过低：{rate:.1%} < {min_non_empty_rate:.0%}")

    # 允许极个别行有空缺/噪声，但致命错列（>=2 条物理违规或过半行违规）必须拦截
    passed = (len(violations) == 0)
    return SampleSanityReport(
        passed=passed,
        checked_rows=len(checked),
        non_empty_rate=rate,
        violations=violations,
        sample_preview=preview,
    )


@register_tool(
    "vlm_learn_layout_schema",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="01_catalog",
    description="新设计院表头一次性视觉学习 -> LayoutProfile，强制过 5 行抽样值域自检门",
)
def vlm_learn_layout_schema(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    doc_id: str,
    target_sheet: str,
    page: int = 0,
    gateway: Optional[VlmGateway] = None,
    memory: Optional[EnterpriseMemory] = None,
    cache_dir: Optional[str] = None,
    force_vlm: bool = False,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)

    # 1) 提取首表格页的表头行与前 5 行数据样例，计算稳定指纹
    grid = GridCellExtractor.extract_page(pdf_path, int(page), cache_dir=cache_dir)
    data_rows = [r for r in (grid.get("rows") or []) if r.get("kind") != "banner" and (r.get("texts") or [])]
    # 跳过纯标题合并行，优先定位含至少 3 个非空词的首个表头行
    hdr_idx = 0
    for i, r in enumerate(data_rows[:4]):
        if len([x for x in (r.get("texts") or []) if str(x or "").strip()]) >= 3:
            hdr_idx = i
            break
    header_texts = data_rows[hdr_idx]["texts"] if data_rows else []
    sample_rows = [r["texts"] for r in data_rows[hdr_idx + 1 : hdr_idx + 6]]
    fp = compute_header_fingerprint(doc_id, target_sheet, header_texts)

    # 2) 先查跨工程企业长期记忆（同院二跑零 Token 秒级命中）
    if memory is not None:
        cached_prof = memory.lookup_layout_profile(fp.fingerprint, target_sheet=target_sheet)
        if cached_prof is not None:
            return ToolResult(
                ok=True,
                code=ErrorCode.OK,
                data=cached_prof.model_dump(),
                summary=f"命中企业跨院版式缓存：{target_sheet} ({fp.fingerprint})，零 VLM 调用",
            )

    # 2.5) 方案 §A.2/§B.4：标准同义词匹配分 >= 0.85 且通过 5 行自检时直接走本地快车道（零 VLM Token）
    if not force_vlm and header_texts:
        local_score, local_map = compute_local_header_match_score(target_sheet, header_texts)
        if local_score >= 0.85 and local_map:
            local_prof = LayoutProfile(
                doc_id=doc_id,
                target_sheet=target_sheet,
                header_fingerprint=fp.fingerprint,
                institute_hint="local_synonym_match",
                source="enterprise_cache",
                column_mapping=local_map,
                confidence=local_score,
            )
            local_sanity = run_sample_sanity_check(local_prof, sample_rows)
            if local_sanity.passed:
                local_prof = local_prof.model_copy(update={"sanity_passed": True, "sanity_report": local_sanity})
                return ToolResult(
                    ok=True,
                    code=ErrorCode.OK,
                    data=local_prof.model_dump(),
                    summary=f"本地高分同义词匹配通过 5 行自检：{target_sheet} (score={local_score:.2f})，零 VLM 调用",
                )

    if gateway is None or not gateway.configured:
        return ToolResult(
            ok=False,
            code=ErrorCode.VLM_UNAVAILABLE,
            summary="未配置视觉模型，无法对陌生设计院表头执行 VLM 学习（将继续使用本地默认规则）",
            recoverable=True,
        )

    # 3) 裁剪“表头 + 前 3 行样例”局部高清图发往 VLM（仅 1 张图）
    crop_bbox = None
    if data_rows:
        top_rows = data_rows[: min(4, len(data_rows))]
        bboxes = [r["bbox"] for r in top_rows if r.get("bbox")]
        if bboxes:
            crop_bbox = [
                min(b[0] for b in bboxes),
                min(b[1] for b in bboxes),
                max(b[2] for b in bboxes),
                max(b[3] for b in bboxes),
            ]
    crop_res = pdf_crop_render(
        workspace=workspace,
        pdf_path=pdf_path,
        doc_id=f"{doc_id}_hdr",
        page=int(page),
        bbox=crop_bbox,
        dpi=200,
    )
    if not crop_res.ok or not crop_res.data:
        return ToolResult(ok=False, code=crop_res.code, summary=f"表头裁图失败：{crop_res.summary}", recoverable=True)

    crop_abs = crop_res.data["abs_path"]
    crop_uri = crop_res.data["png_uri"]
    prompt_ver = "vlm_layout_schema_v1"
    sys_prompt, user_tmpl = load_prompt(prompt_ver)
    cands = _CANDIDATE_FIELDS_BY_SHEET.get(target_sheet, ["字段1", "字段2", "备注"])
    user_prompt = user_tmpl.format(
        target_sheet=target_sheet,
        candidate_fields=", ".join(cands),
        col_count=fp.col_count,
        header_tokens=fp.header_tokens[:25],
    )

    # 最多尝试 2 次（首次 + 抽样自检失败后附带违规样例复核 1 次）
    last_profile: Optional[LayoutProfile] = None
    for attempt in range(2):
        call_prompt = user_prompt
        if attempt == 1 and last_profile and last_profile.sanity_report:
            call_prompt += f"\n\n上轮列映射未通过 5 行抽样物理值域自检，违规项：{last_profile.sanity_report.violations}，请修正列序号映射。"
        raw, usage = gateway.call_vision_json(
            system_prompt=sys_prompt,
            user_prompt=call_prompt,
            image_paths=[crop_abs],
            prompt_version=prompt_ver,
        )
        col_map: dict[int, str] = {}
        for k, v in (raw.get("column_mapping") or {}).items():
            try:
                col_map[int(k)] = str(v).strip()
            except (TypeError, ValueError):
                continue

        prof = LayoutProfile(
            doc_id=doc_id,
            target_sheet=target_sheet,
            header_fingerprint=fp.fingerprint,
            institute_hint=str(raw.get("institute_hint") or ""),
            source="vlm_learned",
            column_mapping=col_map,
            unit_overrides={str(k): str(v) for k, v in (raw.get("unit_overrides") or {}).items()},
            tower_id_regex=raw.get("tower_id_regex") or None,
            confidence=max(0.0, min(1.0, float(raw.get("confidence") or 0.8))),
            sample_crop_uri=crop_uri,
            prompt_version=prompt_ver,
        )
        sanity = run_sample_sanity_check(prof, sample_rows)
        prof = prof.model_copy(update={"sanity_passed": sanity.passed, "sanity_report": sanity})
        last_profile = prof
        if sanity.passed:
            return ToolResult(
                ok=True,
                code=ErrorCode.OK,
                data=prof.model_dump(),
                summary=f"VLM 跨院表头学习通过 5 行自检：{target_sheet} 映射 {len(col_map)} 列（{prof.institute_hint or '新院版式'}）",
                meta={"token_usage": usage, "attempts": attempt + 1},
            )

    return ToolResult(
        ok=False,
        code=ErrorCode.SANITY_CHECK_FAILED,
        data=last_profile.model_dump() if last_profile else None,
        summary=f"VLM 表头学习 2 次均未通过 5 行抽样值域自检，已拦截防错列污染（转交 N7 人工指定）",
        recoverable=True,
    )
