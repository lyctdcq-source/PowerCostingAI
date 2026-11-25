# -*- coding: utf-8 -*-
"""图纸识别与提资对话驱动中枢（中间栏选择工作空间 + 对话指令驱动 + 过程实况反馈）。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from .routes import list_registered_tools

_WIN_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\n\"'<>|]+)")


def handle_tizi_chat(
    job: Any,
    text: str,
    *,
    message_cb: Callable[..., None],
    persist_cb: Callable[[], None],
    sync_state_cb: Callable[[Any], None],
) -> dict[str, Any]:
    """解析用户在中间栏发送的自然语言提资指令并驱动 TiziJob / LangGraph Harness 执行。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("请输入指令或问题")

    # 记录用户消息到中间栏对话流
    message_cb(raw, "user")

    # 1) 绑定/切换施工图工作空间目录
    m_path = _WIN_PATH_RE.search(raw)
    if m_path and any(k in raw for k in ("打开", "绑定", "切换", "目录", "文件夹", "工作空间", "读取")):
        folder = m_path.group(1).strip().strip("”\"'")
        summary = job.open_folder(folder)
        sync_state_cb(summary)
        pdf_cnt = sum(1 for _ in Path(folder).rglob("*.pdf"))
        message_cb(
            f"已绑定施工图工作空间：{Path(folder).name}（目录内共发现 {pdf_cnt} 份 PDF）。\n"
            f"请直接发送「开始提资」或点击输入框下方的「开始智能体提资 →」启动分析；过程实况、9表就绪矩阵与待审冲突将在此实时输出。"
        )
        persist_cb()
        return {"ok": True, "intent": "bind_folder", "tizi": summary}

    # 2) 启动智能体图编排提资（N1 ~ N7）
    if any(k in raw for k in ("开始提资", "智能体提资", "开始智能体", "开始解析", "重新解析", "智能体图编排", "启动提资")):
        if not job.folder or not os.path.isdir(job.folder):
            msg = "当前尚未绑定有效施工图目录，请先在输入框下方点击「选择工作空间」选择图纸文件夹。"
            message_cb(msg)
            persist_cb()
            return {"ok": False, "intent": "need_folder", "message": msg}

        def _on_done(summary, err_msg):
            sync_state_cb(summary)
            if err_msg:
                message_cb(f"提资任务执行异常：{err_msg}")
                persist_cb()
                return
            wb = summary.get("workbook_summary") or {}
            rev = job.get_review_queue()
            rq = rev.get("review_queue") or {}
            p0 = int(rq.get("p0_count") or 0)
            p1 = int(rq.get("p1_count") or 0)
            p2 = int(rq.get("p2_count") or 0)
            lines = [
                f"已完成智能体全工作簿分析：点亮 {wb.get('sheets_ready', 0)}/9 张提资表（有效杆塔 {wb.get('towers', 0)} 基），交叉校验通过 {wb.get('checks_ok', 0)}/{wb.get('checks_total', 0)} 项。"
            ]
            if p0 or p1:
                lines.append(
                    f"当前停留在审核确认环节：P0 冲突 {p0} 项、P1 视觉补全 {p1} 项（P2 提示 {p2} 项）。"
                    f"请直接在下方审核卡片中裁决确认，或点击「去右侧对照原图调整」查看 PDF 红框原图，确认后即可继续生成双轨交付包。"
                )
            else:
                lines.append("无 P0/P1 阻断项，请点击下方审核卡片中的「确认审核并继续提资输出」（或发送「审批放行」）生成院标双轨 Excel 交付包。")
            message_cb("\n".join(lines))
            persist_cb()

        res = job.run_harness_graph(hitl_required=True, async_run=True, on_complete=_on_done)
        sync_state_cb(job.summary_state())
        message_cb("已启动图纸识别与提资智能体（LangGraph StateGraph），正在下方实时流式播报各节点进度与九表点亮状态…")
        persist_cb()
        return {"ok": True, "intent": "start_harness", "stream": True, **res}

    # 3) HITL 审批确认与放行（支持：全部确认P1并放行 / 跳过P1导出草稿 / 审批放行 / 导出）
    if any(k in raw for k in ("审批放行", "放行", "确认全部", "确认p1", "确认P1", "导出草稿", "跳过p1", "跳过P1", "导出提资")):
        if not job.pipe or not (job.pipe.store.get_sheet("杆塔明细表") or {}).get("rows"):
            msg = "当前还没有可导出的解析结果，请先发送「开始提资」。"
            message_cb(msg)
            persist_cb()
            return {"ok": False, "intent": "no_data", "message": msg}

        rev = job.get_review_queue()
        rq = rev.get("review_queue") or {}
        items = list(rq.get("items") or [])
        decisions = []
        if any(k in raw for k in ("确认全部", "确认p1", "确认P1", "一键确认")):
            for it in items:
                if it.get("priority") == "P1" and it.get("row_idx") is not None:
                    decisions.append({
                        "sheet": it["sheet"],
                        "row_idx": it["row_idx"],
                        "col_key": it["col_key"],
                        "action": "confirm",
                    })
        allow_draft = any(k in raw for k in ("草稿", "跳过p1", "跳过P1"))
        promote = any(k in raw for k in ("沉淀", "入库", "企业库"))

        if job.status == "paused_hitl":
            r = job.resume_hitl(
                decisions=decisions,
                approve_export=True,
                promote_memory=promote,
                allow_draft_skip=allow_draft,
            )
            sync_state_cb(r.get("state"))
            g_st = (r.get("graph") or {}).get("status")
            if g_st == "paused_hitl":
                gate = (job.get_review_queue().get("export_gate") or {})
                msg = (
                    f"HITL 门禁已拦截导出：{gate.get('message') or '仍存在未裁决项'}。\n"
                    f"请先在右侧裁决 P0 冲突，或发送「确认全部P1并审批放行」/「跳过P1导出草稿」。"
                )
            else:
                names = ", ".join(x.get("name", "") for x in (r.get("exports") or []))
                msg = f"HITL 审批已放行并生成院标双轨交付包：{names or 'tizi_standard.xlsx, tizi_audit.xlsx'}，可在右侧「校验与导出」下载。"
            message_cb(msg)
            persist_cb()
            return {"ok": True, "intent": "resume_hitl", **r}

        res_exp = job.export_files()
        sync_state_cb(job.summary_state())
        names = ", ".join(x.get("name", "") for x in (res_exp.get("exports") or []))
        message_cb(f"已重新导出双轨提资交付包：{names}，请在右侧「校验与导出」查看与下载。")
        persist_cb()
        return {"ok": True, "intent": "export", **res_exp}

    # 4) 查询待复核 / 校验 / 缺口 / 检查点 / 工具清单
    if any(k in raw for k in ("待复核", "冲突", "校验", "缺", "偏差", "CHK", "检查点", "进度", "概况", "状态")):
        st = job.status_payload()
        chks = st.get("workbook_checks") or []
        rev = job.get_review_queue()
        rq = rev.get("review_queue") or {}
        lines = [
            f"工作空间：{Path(job.folder).name if job.folder else '未绑定'} · 状态：{job.status} · 已注册工具 {len(list_registered_tools())} 个",
            f"待审队列：P0 阻断 {rq.get('p0_count', 0)} 项 / P1 视觉补全 {rq.get('p1_count', 0)} 项 / P2 缺源提示 {rq.get('p2_count', 0)} 项",
        ]
        if chks:
            lines.append("\n全工程交叉校验明细：")
            for c in chks:
                badge = "通过" if c.get("ok") else ("部分覆盖" if c.get("kind") == "incomplete" else "超差")
                lines.append(f"• {c.get('code')} {c.get('name')}：{c.get('actual')} / 基准 {c.get('expect')} {c.get('unit', '')}（{badge}）")
        p0_p1 = [it for it in (rq.get("items") or []) if it.get("priority") in ("P0", "P1")][:6]
        if p0_p1:
            lines.append("\n重点待裁决条目（Top 6）：")
            for it in p0_p1:
                lines.append(f"• [{it['priority']}] 《{it['sheet']}》{it['entity_id']} · {it['col_key']} = {it.get('current_value')}（{it.get('reason', '')[:60]}）")
        message_cb("\n".join(lines))
        persist_cb()
        return {"ok": True, "intent": "query_status"}

    # 5) 通用引导回复
    message_cb(
        "我是图纸识别与提资智能体，你可以直接用对话驱动全流程：\n"
        "• 选择下方「选择工作空间」后发送「开始提资」：自动运行 T2 本地快车道 + 跨院版式识别 + T3 冲突视觉重读\n"
        "• 发送「有哪些待复核」/「校验偏差」：查看 CHK-01~08、P0 冲突与 P1 视觉补全项\n"
        "• 发送「确认全部P1并审批放行」/「跳过P1导出草稿」：完成 HITL 审批并生成院标双轨 Excel（结果与同屏原图对照请在右侧查看）"
    )
    persist_cb()
    return {"ok": True, "intent": "help"}
