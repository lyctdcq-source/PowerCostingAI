# -*- coding: utf-8 -*-
"""《基础明细表》+《基础库》联动构建与毫秒级 DAG 增量重算器。

流程：
1. 从结构册 T2（已剥离构架的 214 基有效杆塔）自动提取 A/B/C/D 四腿基础型号与备注中的
   排水沟/护坡/挡土墙辅工提示，铺底生成《基础明细表》；
2. 对全线基础型号去重生成《基础库》清单，优先查内置标准基础图集库（std_foundation_lib.csv）
   填充砼量/HRB400/HPB300/尺寸，未命中的加高/非标型号按代号前缀识别基础类型并标 pending；
3. 当用户在《基础库》中修改或通过局部离线 OCR 确认任一型号参数后，调用 recalc_foundation_links(store)
   在 < 10ms 内联动刷新全线《基础明细表》引用该型号的所有塔位参考砼量与钢筋量（零 PDF 重读）。
"""
import csv
import os
import re

from ..provenance import make_evidence, make_prov

_FDN_CODE_RE = re.compile(r"^[A-Z]{1,4}\d{3,6}(?:\([0-9.]+\))?$", re.IGNORECASE)
_DITCH_RE = re.compile(r"排水沟[^。；;\n]{0,20}?(?:长|长度)[度为约]?\s*(\d+(?:\.\d+)?)\s*米?")
_SLOPE_RE = re.compile(r"(护坡[^。；;\n]{0,28})")
_WALL_RE = re.compile(r"([^。；;\n]{0,12}挡土墙[^。；;\n]{0,36})")


def load_std_foundation_lib(project_root=None, enterprise_memory_dir=None):
    """加载内置南网/国网通用设计基础标准库，并合并经 HITL 确认沉淀的企业长期基础库。"""
    candidates = []
    if project_root:
        candidates.append(os.path.join(project_root, "data", "std_foundation_lib.csv"))
    candidates.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "defaults", "data", "std_foundation_lib.csv",
    ))
    lib = {}
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    m = str(row.get("model") or "").strip().upper()
                    if m:
                        lib[m] = row
            break
        except OSError:
            pass

    # 合并企业长期记忆库（仅限 human_confirmed=True 条目）
    mem_dirs = []
    if enterprise_memory_dir:
        mem_dirs.append(enterprise_memory_dir)
    elif project_root:
        mem_dirs.append(os.path.join(os.path.dirname(os.path.abspath(project_root)), "enterprise_memory"))
    for md in mem_dirs:
        jf = os.path.join(md, "foundation_lib_custom.jsonl")
        if not os.path.isfile(jf):
            continue
        try:
            import json
            with open(jf, encoding="utf-8") as fh:
                for ln in fh:
                    s = ln.strip()
                    if not s:
                        continue
                    rec = json.loads(s)
                    if rec.get("human_confirmed") is True:
                        m = str(rec.get("model") or "").strip().upper()
                        if m:
                            lib[m] = rec
        except (OSError, ValueError):
            pass
    return lib


def _fnum(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else round(f, 3)
    except (TypeError, ValueError):
        return None


def _guess_fdn_type(code: str) -> str:
    """严格对齐院标母版《隐藏表（勿删）》D34:D42 唯一合法基础类型枚举。"""
    c = (code or "").upper()
    if c.startswith(("GZZ", "GZ", "Z")):
        return "挖孔桩基础"
    if c.startswith(("WK", "WZ", "TW")):
        return "掏挖基础"
    if c.startswith(("LH", "TJ")):
        return "阶梯式"
    if c.startswith(("L", "BF", "DB")):
        return "大板式"
    if c.startswith(("YS", "QG")):
        return "岩石嵌固基础"
    return "掏挖基础"


def _find_fdn_and_remark_cols(layout: dict, rows: list = None):
    """数据驱动 + 表头联合定位基础型号 A/B/C/D 四列与备注列，免疫合并表头居中偏列 1 位缺陷。"""
    cols = (layout or {}).get("columns") or []
    rem_keys = []
    named_fdn = []
    hdr_idx = None
    for i, c in enumerate(cols):
        lbl = re.sub(r"\s+", "", str(c.get("label") or c.get("name") or ""))
        key = c.get("key") or ""
        if key in ("fdn_a", "fdn_b", "fdn_c", "fdn_d"):
            named_fdn.append(key)
        if ("基础型号" in lbl or "基础型式" in lbl) and hdr_idx is None:
            hdr_idx = i
        if "备注" in lbl or key in ("remark", "note"):
            rem_keys.append(key)

    if len(named_fdn) == 4:
        return named_fdn, rem_keys

    # 数据驱动：扫描所有列中符合基础型号正则（如 WK1290 / GZZ24140 / L3232）的命中率，锁定连续 4 列
    if rows:
        col_scores = []
        sample = rows[: min(len(rows), 120)]
        for i, c in enumerate(cols):
            k = c.get("key") or ""
            non_empty = 0
            fdn_hits = 0
            for r in sample:
                v = re.sub(r"\s+", "", str((r.get("cells") or {}).get(k) or ""))
                if not v or v in ("-", "—"):
                    continue
                non_empty += 1
                if _FDN_CODE_RE.match(v) and not re.match(r"^(?:V\d+-)?[12][A-Z]", v, re.IGNORECASE):
                    fdn_hits += 1
            rate = fdn_hits / max(non_empty, 1)
            col_scores.append((i, k, fdn_hits, rate))
        best_win = None
        best_sum = 0
        for s in range(0, max(0, len(cols) - 3)):
            win = col_scores[s:s + 4]
            if all(w[2] >= 3 and w[3] >= 0.4 for w in win):
                tot = sum(w[2] for w in win)
                if tot > best_sum:
                    best_sum = tot
                    best_win = [w[1] for w in win]
        if best_win:
            return best_win, rem_keys

    if hdr_idx is not None:
        return [cols[j]["key"] for j in range(hdr_idx, min(hdr_idx + 4, len(cols)))], rem_keys
    return [], rem_keys


def _clean_fdn_code(raw: str):
    s = str(raw or "").replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s).upper()
    if not s or s in ("-", "—"):
        return None
    if _FDN_CODE_RE.match(s):
        return s
    return None


def build_foundation_sheets(*, tower_v2_rows: list, layout: dict, struct_rel: str, project_root=None):
    """从结构册解析行同步生成《基础明细表》与去重《基础库》。"""
    std_lib = load_std_foundation_lib(project_root)
    fdn_keys, rem_keys = _find_fdn_and_remark_cols(layout, tower_v2_rows)

    unique_models = {}  # model -> list of sample evidences
    detail_rows = []

    for r in tower_v2_rows or []:
        t_out = r.get("out") or {}
        cells = r.get("cells") or {}
        tid = t_out.get("杆塔号") or ""
        seg = t_out.get("线路段名称") or ""
        page1 = r.get("page")
        bbox = r.get("bbox")

        raw_legs = [_clean_fdn_code(cells.get(k)) for k in fdn_keys]
        while len(raw_legs) < 4:
            raw_legs.append(None)

        # 钢管杆等单腿基础型号往往只印在某一列（如 C 列），其余三腿留空
        ev = make_evidence(
            "结构册基础配置", struct_rel, page=page1, bbox=bbox,
            raw_text=f"{tid} | 基础: {'/'.join(x or '-' for x in raw_legs)}",
        )
        for m in raw_legs:
            if m:
                unique_models.setdefault(m, []).append(ev)

        rem_text = " ".join(str(cells.get(k) or "").strip() for k in rem_keys).strip()
        m_ditch = _DITCH_RE.search(rem_text)
        m_slope = _SLOPE_RE.search(rem_text)
        m_wall = _WALL_RE.search(rem_text)
        ditch_len = float(m_ditch.group(1)) if m_ditch else None
        slope_str = m_slope.group(1).strip() if m_slope else None
        wall_str = m_wall.group(1).strip() if m_wall else None

        out = {
            "线路段名称": seg,
            "杆塔号": tid,
            "基数": 1,
            "基础型号.A腿": raw_legs[0],
            "基础型号.B腿": raw_legs[1],
            "基础型号.C腿": raw_legs[2],
            "基础型号.D腿": raw_legs[3],
            "露头高度.A腿": None,
            "露头高度.B腿": None,
            "露头高度.C腿": None,
            "露头高度.D腿": None,
            "本体附加砼.A腿": None,
            "本体附加砼.B腿": None,
            "本体附加砼.C腿": None,
            "本体附加砼.D腿": None,
            "有筋护壁砼.A腿": None,
            "有筋护壁砼.B腿": None,
            "有筋护壁砼.C腿": None,
            "有筋护壁砼.D腿": None,
            "排水沟.长度（m）": ditch_len,
            "护坡.面积或方量": slope_str,
            "挡土墙.规格说明": wall_str,
            "基础总砼量(参考m³)": None,
            "基础总钢筋(参考kg)": None,
        }
        prov = {
            "线路段名称": make_prov("derive", conf=1.0, status="confirmed", machine_value=seg, evidences=[ev]),
            "杆塔号": make_prov("extract", conf=0.99, status="confirmed", machine_value=tid, evidences=[ev]),
            "基数": make_prov("constant", conf=1.0, status="confirmed", machine_value=1, evidences=[ev]),
        }
        for leg_name, leg_val in zip(("A腿", "B腿", "C腿", "D腿"), raw_legs):
            col_k = f"基础型号.{leg_name}"
            prov[col_k] = make_prov(
                "extract", conf=0.98 if leg_val else 0.0,
                status="confirmed" if leg_val else "empty",
                machine_value=leg_val, note="结构册直取" if leg_val else "",
                evidences=[ev],
            )
        if ditch_len is not None:
            prov["排水沟.长度（m）"] = make_prov(
                "extract", conf=0.92, status="pending", unit="m",
                machine_value=ditch_len, note=f"由结构册备注提取（{rem_text[:45]}），请复核",
                formula_trace=rem_text[:80], evidences=[ev],
            )
        if slope_str:
            prov["护坡.面积或方量"] = make_prov(
                "extract", conf=0.9, status="pending", machine_value=slope_str,
                note="由结构册备注提取，请核定方量", formula_trace=rem_text[:80], evidences=[ev],
            )
        if wall_str:
            prov["挡土墙.规格说明"] = make_prov(
                "extract", conf=0.9, status="pending", machine_value=wall_str,
                note="由结构册备注提取，请核定方量", formula_trace=rem_text[:80], evidences=[ev],
            )

        detail_rows.append({
            "row_id": tid or f"F{len(detail_rows) + 1}",
            "page": page1,
            "bbox": bbox,
            "out": out,
            "prov": prov,
        })

    # 生成去重《基础库》清单并查询标准基础图集库
    lib_rows = []
    std_hits = 0
    for model in sorted(unique_models.keys()):
        evs = unique_models[model][:4]
        base_code = model.split("(")[0].strip().upper()
        hit = std_lib.get(model) or std_lib.get(base_code)
        is_exact = model in std_lib
        ftype = hit["fdn_type"] if hit else _guess_fdn_type(model)

        conc = _fnum(hit.get("concrete_m3")) if is_exact else None
        hrb = _fnum(hit.get("hrb400_kg")) if is_exact else None
        hpb = _fnum(hit.get("hpb300_kg")) if is_exact else None
        if is_exact:
            std_hits += 1

        # 对带括号加高/非标型号解析尺寸提示但不猜砼量
        orig_h = _fnum(hit.get("orig_h")) if is_exact else None
        orig_d = _fnum(hit.get("orig_d")) if is_exact else None
        orig_hh = _fnum(hit.get("orig_head_h")) if is_exact else None
        pile_d = _fnum(hit.get("pile_d")) if is_exact else None
        pile_l = _fnum(hit.get("pile_l")) if is_exact else None
        m_paren = re.search(r"\((\d+(?:\.\d+)?)\)", model)
        if not is_exact and m_paren:
            if ftype == "挖孔桩基础":
                pile_l = float(m_paren.group(1))
                m_pd = re.match(r"GZZ(\d{2})", model)
                if m_pd:
                    pile_d = round(int(m_pd.group(1)) / 10.0, 2)
            elif ftype == "掏挖基础":
                orig_h = float(m_paren.group(1))

        out_l = {
            "基础型号": model,
            "基础类型": ftype,
            "基础.砼量(m³)": conc,
            "基础钢筋量（kg）.HRB400": hrb,
            "基础钢筋量（kg）.HPB300": hpb,
            "原状土.基础全高(m)": orig_h,
            "原状土.基础直径(m)": orig_d,
            "原状土.扩大头高度(m)": orig_hh,
            "重力式.底板长(m)": _fnum(hit.get("grav_l")) if is_exact else None,
            "重力式.底板宽(m)": _fnum(hit.get("grav_w")) if is_exact else None,
            "重力式.基础深度(m)": _fnum(hit.get("grav_d")) if is_exact else None,
            "重力式.立柱宽(m)": _fnum(hit.get("grav_col_w")) if is_exact else None,
            "灌注桩.桩直径(m)": pile_d,
            "灌注桩.桩长(m)": pile_l,
            "灌注桩.挤扩支盘桩": (hit.get("pile_branch") or "否") if (is_exact and ftype == "挖孔桩基础") else None,
            "混凝土标号": (hit.get("concrete_grade") if hit else ("C30" if ftype == "挖孔桩基础" else "C25")),
        }

        prov_l = {
            "基础型号": make_prov(
                "extract", conf=0.99, status="confirmed", machine_value=model,
                note=f"结构册去重（共 {len(unique_models[model])} 腿引用）", evidences=evs,
            ),
            "基础类型": make_prov(
                "std_lib_lookup" if is_exact else "derive",
                conf=0.98 if is_exact else 0.85,
                status="confirmed" if is_exact else "pending",
                machine_value=ftype,
                note="标准基础图集库命中" if is_exact else "按型号前缀初判，待对照基础详图确认",
                evidences=evs[:1],
            ),
        }
        for ck in ("基础.砼量(m³)", "基础钢筋量（kg）.HRB400", "基础钢筋量（kg）.HPB300"):
            v = out_l[ck]
            prov_l[ck] = make_prov(
                "std_lib_lookup" if is_exact else "extract",
                conf=0.95 if is_exact else 0.0,
                status="confirmed" if is_exact else "pending",
                machine_value=v,
                note="通用设计标准基础库查表" if is_exact else "非标/加高型号：请在基础详图框选或调用本机离线 OCR 录入",
                formula_trace=f"std_foundation_lib[{model}] → {v}" if is_exact else f"待录入 {model} 单腿参数（录入后全线 {len(unique_models[model])} 腿自动联动）",
                evidences=evs[:1],
            )
        prov_l["混凝土标号"] = make_prov("constant", conf=0.95, status="confirmed", machine_value=out_l["混凝土标号"], note="模板默认值")

        lib_rows.append({
            "row_id": model,
            "out": out_l,
            "prov": prov_l,
        })

    return {
        "detail_rows": detail_rows,
        "lib_rows": lib_rows,
        "meta": {
            "towers": len(detail_rows),
            "unique_models": len(lib_rows),
            "std_lib_hits": std_hits,
        },
        "source_files": [struct_rel] if struct_rel else [],
    }


def recalc_foundation_links(store):
    """DAG 增量重算：将《基础库》单腿砼量/钢筋量毫秒级同步到《基础明细表》各塔四腿参考汇总。"""
    lib_rows = (store.get_sheet("基础库") or {}).get("rows") or []
    det_rows = (store.get_sheet("基础明细表") or {}).get("rows") or []
    if not lib_rows or not det_rows:
        return {"updated_towers": 0}

    lib_idx = {}
    for lr in lib_rows:
        o = lr.get("out") or {}
        m = str(o.get("基础型号") or "").strip().upper()
        if m:
            lib_idx[m] = {
                "conc": _fnum(o.get("基础.砼量(m³)")),
                "hrb": _fnum(o.get("基础钢筋量（kg）.HRB400")) or 0.0,
                "hpb": _fnum(o.get("基础钢筋量（kg）.HPB300")) or 0.0,
                "has_rebar": (o.get("基础钢筋量（kg）.HRB400") is not None or o.get("基础钢筋量（kg）.HPB300") is not None),
            }

    updated = 0
    for dr in det_rows:
        o = dr.get("out") or {}
        prov = dr.setdefault("prov", {})
        legs = [str(o.get(f"基础型号.{k}腿") or "").strip().upper() for k in ("A", "B", "C", "D")]
        c_parts = []
        r_parts = []
        c_sum = 0.0
        r_sum = 0.0
        for leg_lbl, m in zip(("A", "B", "C", "D"), legs):
            if not m or m not in lib_idx:
                continue
            info = lib_idx[m]
            if info["conc"] is not None:
                c_sum += info["conc"]
                c_parts.append(f"{leg_lbl}:{m}({info['conc']:g})")
            if info["has_rebar"]:
                rs = info["hrb"] + info["hpb"]
                r_sum += rs
                r_parts.append(f"{leg_lbl}:{m}({rs:g})")

        if c_parts:
            c_val = round(c_sum, 3)
            o["基础总砼量(参考m³)"] = c_val
            prov["基础总砼量(参考m³)"] = make_prov(
                "rollup", conf=0.96, status="confirmed", unit="m³",
                machine_value=c_val,
                note="由《基础库》四腿型号实时联动汇总",
                formula_trace=" + ".join(c_parts) + f" = {c_val:g} m³",
            )
            updated += 1
        if r_parts:
            r_val = round(r_sum, 1)
            o["基础总钢筋(参考kg)"] = r_val
            prov["基础总钢筋(参考kg)"] = make_prov(
                "rollup", conf=0.96, status="confirmed", unit="kg",
                machine_value=r_val,
                note="由《基础库》四腿 HRB400+HPB300 实时联动汇总",
                formula_trace=" + ".join(r_parts) + f" = {r_val:g} kg",
            )
    return {"updated_towers": updated}
