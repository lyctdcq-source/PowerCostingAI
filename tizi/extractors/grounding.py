# -*- coding: utf-8 -*-
"""《防雷接地装置图》模块参数表解析 + 三源关联推导器。

核心设计：
1. 自动扫描页面文字层中匹配 `[12][DS]-[ZN]\\d{2,3}-[FX]\\d` 的接地模块表，不硬编码页号；
2. 强类型单位换算：图纸中接地模块总重单位为 kg，经 UnitConverter 显式乘 0.001 转为模板所需的 t；
3. 生成 3 条可跳转证据（结构册塔位呼高 + 土壤电阻率报告 ρ + 防雷接地装置图参数表）与中文算式。
"""
import re

from .. import cache
from ..grid_parser import GridCellExtractor
from ..provenance import UnitConverter, make_evidence, make_prov

_MOD_CODE_RE = re.compile(r"\b([12][DS]-[ZN]\d{2,3})-[FX]\d\b")
_TOK_RE = re.compile(r"[-\d.]+|>3000")
_TENSION_RE = re.compile(r"(-J\d|-JG|-JD|GFJ|GJD)", re.IGNORECASE)


def _grab_nums(text: str, label: str, n: int = 7):
    idx = text.find(label)
    if idx < 0:
        # 兼容括号间距差异，如 "长度（m）" vs "长度 (m)"
        m = re.search(re.escape(label).replace(r"\ ", r"\s*"), text)
        if not m:
            return []
        seg = text[m.end():]
    else:
        seg = text[idx + len(label):]
    out = []
    for tok in _TOK_RE.findall(seg):
        if tok in ("-", ""):
            continue
        if tok == ">3000":
            break
        try:
            out.append(float(tok))
        except ValueError:
            break
        if len(out) >= n:
            break
    return out


def _table_bbox(words, mod_prefix: str):
    """估算该模块参数表在页面中的大致矩形框，用于点击证据胶囊高亮。"""
    hits = [w for w in words if mod_prefix in str(w[4]) or "总重" in str(w[4]) or "长度" in str(w[4])]
    if not hits:
        hits = words[:40]
    if not hits:
        return None
    x0 = min(w[0] for w in hits)
    y0 = min(w[1] for w in hits)
    x1 = max(w[2] for w in hits)
    y1 = max(w[3] for w in hits)
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]


def _extract_grounding_by_grid(path: str, page_idx: int, mod: str, cache_dir=None):
    """优先用 GridCellExtractor 按框线网格精确读取 7 档长度、总重与单元格坐标（修复 D-02 偶位切片）。"""
    try:
        gres = GridCellExtractor.extract_page(path, page_idx, cache_dir=cache_dir)
    except Exception:  # noqa: BLE001
        return None
    if gres.get("mode") != "grid":
        return None

    rows = gres.get("rows") or []
    lens, wts = [], []
    hit_boxes = []
    seen_mod = False
    for r in rows:
        if r.get("kind") == "banner":
            continue
        texts = [str(t or "").strip() for t in (r.get("texts") or [])]
        joined = "".join(texts)
        if mod in joined:
            seen_mod = True
        if "长度" in joined and ("m" in joined.lower() or "米" in joined or seen_mod):
            nums = []
            for t in texts[1:]:
                m = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", t)
                if m:
                    nums.append(float(m.group(1)))
            if len(nums) >= 3:
                lens = nums[:7]
                if r.get("bbox"):
                    hit_boxes.append(r["bbox"])
        elif "总重" in joined and "单重" not in joined:
            nums = []
            for t in texts[1:]:
                m = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", t)
                if m:
                    nums.append(float(m.group(1)))
            if len(nums) >= 3:
                wts = nums[:7]
                if r.get("bbox"):
                    hit_boxes.append(r["bbox"])

    if not wts or not lens:
        return None
    bbox = None
    if hit_boxes:
        bbox = [
            round(min(b[0] for b in hit_boxes), 1),
            round(min(b[1] for b in hit_boxes), 1),
            round(max(b[2] for b in hit_boxes), 1),
            round(max(b[3] for b in hit_boxes), 1),
        ]
    return lens, wts, bbox


def parse_grounding_pdf(path, cache_dir=None):
    """从防雷接地装置图提取标准接地模块库（优先框线网格，无框线页回退文本切片）。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    lib = {}
    for pg in doc["pages"]:
        txt = pg.get("text") or ""
        if not txt:
            continue
        prefixes = sorted(set(_MOD_CODE_RE.findall(txt)))
        if not prefixes:
            continue
        # 若单页只定义一个模块，优先尝试框线网格提取，失败回退文本切片
        for mod in prefixes:
            models = sorted(
                set(re.findall(rf"{re.escape(mod)}-[FX]\d", txt)),
                key=lambda s: int(s[-1]),
            )
            grid_hit = _extract_grounding_by_grid(path, pg["page"], mod, cache_dir=cache_dir)
            if grid_hit is not None:
                lens, wts, grid_bbox = grid_hit
            else:
                lens = _grab_nums(txt, "长度 （m）", 7) or _grab_nums(txt, "长度（m）", 7) or _grab_nums(txt, "长度(m)", 7)
                raw_wt = _grab_nums(txt, "总重", 14)
                wts = raw_wt[0::2][:7] if len(raw_wt) >= 14 else raw_wt[:7]
                grid_bbox = None
            if not models and not lens and not wts:
                continue
            prev = lib.get(mod)
            if prev and len(prev.get("len") or []) >= len(lens) and len(prev.get("wt_kg") or []) >= len(wts):
                continue
            lib[mod] = {
                "module": mod,
                "models": models,
                "len": lens[:7],
                "wt_kg": wts[:7],
                "page": pg["page"] + 1,
                "bbox": grid_bbox or _table_bbox(pg["words"], mod),
            }
    return lib


def rho_band(rho_val):
    if rho_val is None:
        return None
    v = float(rho_val)
    if v <= 100:
        return 1
    if v <= 500:
        return 2
    if v <= 1000:
        return 3
    if v <= 1500:
        return 4
    if v <= 2000:
        return 5
    if v <= 3000:
        return 6
    return 7


def is_tension_tower(ttype: str) -> bool:
    s = str(ttype or "")
    if not s or "GFZ" in s.upper():
        return False
    return bool(_TENSION_RE.search(s))


def pick_module(seg_code: str, ttype: str, call_height: float, available_mods=None) -> str:
    """优先依据南网/国网标准塔型编码（电压首码 + 单/双回码 + 耐张/直线）选择接地模块，去工程字母硬编码。"""
    tt = str(ttype or "").strip().upper()
    tt_clean = re.sub(r"^V\d+-", "", tt)

    # 1) 依据标准塔型编码判定电压等级（1xx=110kV, 2xx=220kV, 5xx=500kV），仅在非标准塔型时回落 seg_code
    if re.match(r"^1[A-Z]", tt_clean):
        kv220 = False
    elif re.match(r"^[235][A-Z]", tt_clean):
        kv220 = True
    else:
        seg = (seg_code or "").upper()
        kv220 = seg in ("A", "B", "C") or not seg

    # 2) 依据塔型编码判定单/双回（如 1B2/2E2/1S/2S 为双回，1B1/2E1/1D/2D 为单回）
    is_dbl = bool(re.match(r"^[12][A-Z]2|^[12]S", tt_clean)) or (not tt_clean and (seg_code or "").upper() == "D")
    ten = is_tension_tower(tt)
    h = float(call_height or 0.0)
    if kv220:
        cand = "2D-N36" if ten else ("2D-Z36" if h <= 36 else ("2D-Z51" if h <= 51 else "2D-Z72"))
    else:
        if ten:
            cand = "1S-N30" if is_dbl else "1D-N30"
        else:
            cand = "1S-Z54"
    if available_mods and cand not in available_mods:
        prefix = cand[:4]
        for m in sorted(available_mods):
            if m.startswith(prefix):
                return m
    return cand


def derive_grounding_for_tower(
    *,
    tower_id: str,
    seg_code: str,
    ttype: str,
    call_height,
    rho_rec: dict,
    ground_lib: dict,
    struct_ev: dict,
    rho_rel: str,
    ground_rel: str,
):
    """为单基杆塔推导接地装置 4 列 (型号, 长度m, 埋深m, 重量t) 及其 ProvenanceDAG。"""
    if not rho_rec or rho_rec.get("rho") is None:
        return None
    rv = float(rho_rec["rho"])
    band = rho_band(rv)
    if not band:
        return None
    h_num = float(call_height or 0.0)
    mod = pick_module(seg_code, ttype, h_num, available_mods=ground_lib)
    entry = ground_lib.get(mod)
    suffix = f"{'F' if band == 1 else 'X'}{band}"
    model_name = f"{mod}-{suffix}"

    evs = []
    if struct_ev:
        evs.append(struct_ev)
    evs.append(make_evidence(
        "推荐土壤电阻率", rho_rel,
        page=rho_rec.get("page"), bbox=rho_rec.get("bbox"),
        raw_text=rho_rec.get("raw_text") or f"{tower_id} ρ={rv:g}Ω·m",
    ))

    g_len = None
    g_wt_t = None
    wt_trace = ""
    if entry:
        raw_slice = f"{model_name}"
        if len(entry.get("len") or []) >= band:
            g_len = round(float(entry["len"][band - 1]), 1)
            raw_slice += f" | 长度 {g_len:g}m"
        if len(entry.get("wt_kg") or []) >= band:
            wt_kg = float(entry["wt_kg"][band - 1])
            g_wt_t, wt_trace = UnitConverter.convert(wt_kg, "kg", "t", decimals=3)
            raw_slice += f" | 总重 {wt_kg:g}kg"
        evs.append(make_evidence(
            "接地模块参数表", ground_rel,
            page=entry.get("page"), bbox=entry.get("bbox"),
            raw_text=raw_slice,
        ))

    base_trace = f"塔型={ttype}, 呼高={h_num:g}m → 模块 {mod}；ρ={rv:g}Ω·m(第{band}档) → {model_name}"
    st_model = "confirmed" if entry else "pending"
    high_rho_tip = "（高阻区 ρ>2000Ω·m，建议同步核对清册生态/降阻接地装置）" if rv > 2000 else ""
    note_model = (f"三源关联查表（结构册+电阻率+防雷接地图）{high_rho_tip}" if entry else "防雷接地图未匹配到该模块，待核")

    return {
        "接地装置.型号": (
            model_name,
            make_prov(
                "multi_join_lookup", conf=0.96 if entry else 0.6, status=st_model,
                note=note_model, machine_value=model_name,
                formula_trace=base_trace, evidences=evs,
            ),
        ),
        "接地装置.长度（m）": (
            g_len,
            make_prov(
                "multi_join_lookup", conf=0.96 if g_len is not None else 0.0,
                status="confirmed" if g_len is not None else "pending",
                note="来自防雷接地装置图模块表" if g_len is not None else "未查到模块长度",
                machine_value=g_len, unit="m",
                formula_trace=f"{base_trace} → 长度 {g_len} m" if g_len is not None else base_trace,
                evidences=evs,
            ),
        ),
        "接地装置.埋深（m）": (
            0.8,
            make_prov(
                "constant", conf=0.95, status="confirmed",
                note="防雷接地装置图常规埋深 0.8m",
                machine_value=0.8, unit="m",
                formula_trace="设计通用标准埋深 = 0.8 m",
                evidences=evs[-1:] if entry else evs,
            ),
        ),
        "接地装置.常规接地镀锌接地材料重量（t）": (
            g_wt_t,
            make_prov(
                "multi_join_lookup", conf=0.96 if g_wt_t is not None else 0.0,
                status="confirmed" if g_wt_t is not None else "pending",
                note="防雷接地图 kg 显式换算为 t" if g_wt_t is not None else "未查到模块重量",
                machine_value=g_wt_t, unit="t",
                formula_trace=f"{base_trace}；{wt_trace}" if wt_trace else base_trace,
                evidences=evs,
            ),
        ),
    }
