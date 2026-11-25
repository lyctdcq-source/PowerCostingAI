# -*- coding: utf-8 -*-
"""L2 标准化原子工具包：导入即向默认 ToolRegistry 注册全部 12 个工具。"""
from . import (  # noqa: F401
    cv_layout,
    excel_export,
    fs_io,
    ocr_region,
    pdf_catalog,
    pdf_grid,
    pdf_render,
    price_match,
    store_patch,
    validator,
    vlm_reader,
    vlm_schema,
)
from ..harness.registry import get_default_registry


def list_registered_tools():
    return get_default_registry().list_tools()
