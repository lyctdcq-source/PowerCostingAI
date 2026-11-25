# -*- coding: utf-8 -*-
"""Prompt 版本化加载器（tizi/prompts/<name>.md）。"""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(prompt_version: str) -> tuple[str, str]:
    """读取独立 Markdown Prompt，按 `---USER---` 分隔为 (system_prompt, user_template)。"""
    p = _PROMPTS_DIR / f"{prompt_version}.md"
    if not p.is_file():
        raise FileNotFoundError(f"Prompt 模板不存在：{prompt_version}")
    raw = p.read_text(encoding="utf-8")
    # 剥离顶部 YAML frontmatter
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            raw = parts[2].lstrip()
    if "<!-- USER -->" in raw:
        sys_txt, usr_txt = raw.split("<!-- USER -->", 1)
        return sys_txt.strip(), usr_txt.strip()
    return raw.strip(), ""
