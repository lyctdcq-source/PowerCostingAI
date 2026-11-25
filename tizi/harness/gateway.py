# -*- coding: utf-8 -*-
"""L1 信息边界层：双向脱敏网关（DesensitizeGateway）与受限 VLM 视觉调用网关（VlmGateway）。"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image

_LOG = logging.getLogger("tizi.harness.gateway")

MAX_CROPS_PER_CALL = 3
MAX_IMAGE_EDGE_PX = 1568
DEFAULT_CONCURRENCY = 4

_WIN_ABS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+")
_UNIX_ABS_PATH_RE = re.compile(r"/(?:Users|home|var|tmp|opt|mnt)/[^\s\"'<>|]+")


class DesensitizeGateway:
    """发往外部模型前做路径/工程名双向脱敏，返回后无感还原。"""

    def __init__(self, doc_aliases: Optional[dict[str, str]] = None, project_names: Optional[list[str]] = None):
        self._fwd: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        for idx, (orig, alias) in enumerate((doc_aliases or {}).items(), 1):
            if orig:
                tag = alias or f"DOC_{idx:02d}"
                self._register(orig, tag)
        for idx, pname in enumerate(project_names or [], 1):
            if pname and len(pname) >= 3:
                self._register(pname, f"PROJECT_{idx}")

    def _register(self, raw: str, alias: str) -> None:
        key = str(raw).strip()
        if not key:
            return
        self._fwd[key] = alias
        self._rev[alias] = key

    def mask_text(self, text: str) -> str:
        s = str(text or "")
        for orig, alias in sorted(self._fwd.items(), key=lambda kv: len(kv[0]), reverse=True):
            s = s.replace(orig, alias)
        s = _WIN_ABS_PATH_RE.sub("<LOCAL_PATH>", s)
        s = _UNIX_ABS_PATH_RE.sub("<LOCAL_PATH>", s)
        return s

    def unmask_text(self, text: str) -> str:
        s = str(text or "")
        for alias, orig in sorted(self._rev.items(), key=lambda kv: len(kv[0]), reverse=True):
            s = s.replace(alias, orig)
        return s

    def mask_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.mask_text(obj)
        if isinstance(obj, list):
            return [self.mask_obj(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.mask_obj(v) for k, v in obj.items()}
        return obj

    def unmask_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.unmask_text(obj)
        if isinstance(obj, list):
            return [self.unmask_obj(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v) for k, v in obj.items()}
        return obj


def encode_bounded_image_data_url(image_path: str | Path, max_edge: int = MAX_IMAGE_EDGE_PX) -> tuple[str, tuple[int, int]]:
    """强制将裁切图长边压至 <=1568px 并编码为 PNG data URL，控制视觉 Token 预算。"""
    p = Path(image_path)
    with Image.open(p) as im:
        im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / float(longest)
            im = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.Resampling.LANCZOS)
            w, h = im.size
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}", (w, h)


def _load_local_env_file() -> dict[str, str]:
    """从仓库根目录读取已加入 .gitignore 的 .env.local / .env（绝不写入日志）。"""
    repo_root = Path(__file__).resolve().parents[2]
    loaded: dict[str, str] = {}
    for fname in (".env", ".env.local"):
        p = repo_root / fname
        if not p.is_file():
            continue
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                s = ln.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    loaded[k] = v
        except OSError:
            continue
    return loaded


class VlmGateway:
    """统一的多模态模型调用网关（脱敏 + 图片预算 + 并发限流 + Pydantic 自修复重试 + 可注入 mock）。"""

    def __init__(
        self,
        *,
        endpoint: str = "",
        model: str = "",
        api_key: str = "",
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: int = 30,
        max_retries: int = 2,
        desensitizer: Optional[DesensitizeGateway] = None,
        mock_handler: Optional[Callable[[str, list[str], str], dict[str, Any]]] = None,
    ):
        local_env = _load_local_env_file()
        self.endpoint = (endpoint or os.environ.get("TIZI_VLM_ENDPOINT") or local_env.get("TIZI_VLM_ENDPOINT") or "").strip()
        self.model = (model or os.environ.get("TIZI_VLM_MODEL") or local_env.get("TIZI_VLM_MODEL") or "").strip()
        self.api_key = (api_key or os.environ.get("TIZI_VLM_API_KEY") or local_env.get("TIZI_VLM_API_KEY") or "").strip()
        self.timeout = max(5, int(timeout))
        self.max_retries = max(0, int(max_retries))
        self.desensitizer = desensitizer or DesensitizeGateway()
        self.mock_handler = mock_handler
        self._sem = threading.BoundedSemaphore(max(1, int(concurrency)))

    @property
    def configured(self) -> bool:
        return bool(self.mock_handler or (self.endpoint and self.model and self.api_key))

    def call_vision_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str | Path],
        prompt_version: str = "",
        max_tokens: int = 2048,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        if not image_paths:
            raise ValueError("image_paths 不能为空")
        if len(image_paths) > MAX_CROPS_PER_CALL:
            raise ValueError(f"单次视觉调用最多允许 {MAX_CROPS_PER_CALL} 张裁切图，当前传入 {len(image_paths)}")

        masked_sys = self.desensitizer.mask_text(system_prompt)
        masked_user = self.desensitizer.mask_text(user_prompt)

        if self.mock_handler is not None:
            raw = self.mock_handler(prompt_version, [str(p) for p in image_paths], masked_user)
            return self.desensitizer.unmask_obj(raw), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if not self.configured:
            raise RuntimeError("VLM 未配置（请设置 TIZI_VLM_ENDPOINT / TIZI_VLM_MODEL / TIZI_VLM_API_KEY 或在设置中配置视觉模型）")

        content: list[dict[str, Any]] = [{"type": "text", "text": masked_user}]
        for ip in image_paths:
            data_url, _ = encode_bounded_image_data_url(ip, max_edge=MAX_IMAGE_EDGE_PX)
            content.append({"type": "image_url", "image_url": {"url": data_url}})

        messages = [
            {"role": "system", "content": masked_sys},
            {"role": "user", "content": content},
        ]

        import llm_client  # 复用平台统一结构化请求与限速逻辑

        ep = self.endpoint.rstrip("/")
        if ep and not ep.endswith("/chat/completions"):
            ep = ep + "/chat/completions"

        last_err: Optional[Exception] = None
        with self._sem:
            for attempt in range(self.max_retries + 1):
                try:
                    res, usage = llm_client.request_json(
                        ep,
                        self.model,
                        self.api_key,
                        messages,
                        max_tokens=max_tokens,
                        retries=1,
                        thinking=False,
                    )
                    norm_usage = {
                        "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
                        "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
                        "total_tokens": int((usage or {}).get("total_tokens") or 0),
                    }
                    return self.desensitizer.unmask_obj(res), norm_usage
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    msg_low = str(exc).lower()
                    if any(k in msg_low for k in ("image_url", "multimodal", "vision", "not supported")):
                        _LOG.warning("VlmGateway multimodal unsupported by %s (%s): %s", self.model, ep, exc)
                        break
                    _LOG.warning("VlmGateway call failed (attempt %d/%d): %s", attempt + 1, self.max_retries + 1, exc)
                    if attempt < self.max_retries:
                        time.sleep(min(4.0, 0.8 * (2 ** attempt)))
        raise RuntimeError(f"VLM 调用失败（模型={self.model}）：{last_err}")
