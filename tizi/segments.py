# -*- coding: utf-8 -*-
"""全局线路段主数据注册表（LineSegmentRegistry，修复 D-04 硬编码 A/D/E/F 前缀）。

支持任意设计院杆塔编号前缀（N[A-Z]、J/Z/T 等）与 LayoutProfile 显式映射，
通过“塔型电压线索 + 塔型单/双回特征 + 基本信息分段”动态绑定，满足 9 表外键 100% 一致。
"""
import re

_PREFIX_RE = re.compile(r"^(?:N([A-Z])|([A-Z]{1,2}))\d+", re.IGNORECASE)


def extract_tower_prefix(tower_id: str, tower_id_regex: str = "") -> str:
    tid = str(tower_id or "").strip().upper()
    if not tid:
        return ""
    if tower_id_regex:
        try:
            m = re.match(tower_id_regex, tid)
            if m and m.groups():
                return str(m.group(1) or "").upper()
        except re.error:
            pass
    m = _PREFIX_RE.match(tid)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").upper()


def _infer_circuit_from_tower_type(ttype: str) -> str:
    t = str(ttype or "").upper()
    if re.search(r"(?:^|-)2[A-Z]|双回|SZ2|SJ2", t):
        return "双回路"
    if re.search(r"(?:^|-)1[A-Z]|单回|SZ1|SJ1", t):
        return "单回路"
    return ""


def _infer_kv_from_tower_type(ttype: str) -> str:
    t = str(ttype or "").upper()
    if t.startswith(("1B", "2B", "V3-1B", "1D", "2D", "1S", "2S")):
        return "110kV"
    if t.startswith(("5", "500")):
        return "500kV"
    if t.startswith(("1E", "2E", "1G", "2G", "V3-2E", "V3-1E")):
        return "220kV"
    return ""


class LineSegmentRegistry:
    """统一管理全工程线路段主数据，并提供塔号前缀 / 材料清册分册 → 标准线路段名称的映射。"""

    def __init__(self, segments=None, tower_id_regex: str = ""):
        self.segments = []
        self.tower_id_regex = tower_id_regex or ""
        for seg in segments or []:
            self.segments.append(dict(seg))

    @classmethod
    def from_basic_and_towers(
        cls,
        basic_rows: list,
        raw_tower_rows: list,
        *,
        layout_profile: dict = None,
    ):
        """联合《基本信息》分段、结构册塔号前缀与可选 LayoutProfile，构建全书统一的线路段注册表。"""
        lp = layout_profile if isinstance(layout_profile, dict) else {}
        tower_id_regex = str(lp.get("tower_id_regex") or "")
        explicit_prefix_map = {
            str(k).upper(): str(v) for k, v in (lp.get("segment_prefix_map") or {}).items()
        }

        prefix_info = {}
        for r in raw_tower_rows or []:
            out = r.get("out") or {}
            cells = r.get("cells") or {}
            tid = str(out.get("杆塔号") or cells.get("tower_id") or cells.get("c1") or "").strip().upper()
            ttype = str(out.get("杆塔型号") or cells.get("tower_type") or cells.get("c2") or "").strip().upper()
            if re.match(r"^(AN\d+|GJ)", tid) or "GJ-" in ttype:
                continue
            p = extract_tower_prefix(tid, tower_id_regex=tower_id_regex)
            if not p:
                continue
            info = prefix_info.setdefault(p, {"count": 0, "kv_votes": {}, "circ_votes": {}})
            info["count"] += 1
            kv_hint = _infer_kv_from_tower_type(ttype) or "220kV"
            info["kv_votes"][kv_hint] = info["kv_votes"].get(kv_hint, 0) + 1
            circ_hint = _infer_circuit_from_tower_type(ttype)
            if circ_hint:
                info["circ_votes"][circ_hint] = info["circ_votes"].get(circ_hint, 0) + 1

        for info in prefix_info.values():
            info["kv"] = max(info["kv_votes"].items(), key=lambda kv: kv[1])[0] if info["kv_votes"] else "220kV"
            info["circuit"] = (
                max(info["circ_votes"].items(), key=lambda kv: kv[1])[0] if info["circ_votes"] else "单回路"
            )

        segments = []
        if basic_rows:
            for idx, br in enumerate(basic_rows, 1):
                o = br.get("out") or {}
                name = str(o.get("线路段名称") or f"线路段{idx}").strip()
                kv = str(o.get("电压等级") or "220kV").strip()
                circuit = str(o.get("回路类型") or "单回路").strip()
                length_km = o.get("线路亘长（km）")
                segments.append({
                    "index": idx,
                    "name": name,
                    "kv": kv,
                    "circuit": circuit,
                    "length_km": length_km,
                    "prefixes": [],
                })

            seg_by_name = {s["name"]: s for s in segments}
            # 1) 优先应用 LayoutProfile / Model 语义对齐给出的显式前缀映射
            unassigned = []
            for p in sorted(prefix_info.keys()):
                mapped_name = explicit_prefix_map.get(p)
                if mapped_name and mapped_name in seg_by_name:
                    seg_by_name[mapped_name]["prefixes"].append(p)
                else:
                    unassigned.append(p)

            # 2) 按电压等级 × 回路特征 × 塔基规模动态归属，彻底消除 A/D/E/F 硬编码
            by_kv = {}
            for s in segments:
                kv_num = re.search(r"\d+", s["kv"])
                key = f"{kv_num.group()}kV" if kv_num else s["kv"]
                by_kv.setdefault(key, []).append(s)

            # 按塔基数降序优先匹配主干段（同电压多段时，规模最大前缀优先落主段，双回优先落双回段）
            kv_groups_assigned = {}
            for p in sorted(unassigned, key=lambda code: (-prefix_info[code]["count"], code)):
                pkv = prefix_info[p]["kv"]
                pcirc = prefix_info[p]["circuit"]
                cands = by_kv.get(pkv) or segments
                if not cands:
                    continue
                if len(cands) == 1:
                    cands[0]["prefixes"].append(p)
                    continue
                # 优先按回路类型（双回路 vs 单回路）过滤匹配
                is_double = ("双" in pcirc)
                circ_matches = [s for s in cands if ("双" in s["circuit"]) == is_double]
                pool = circ_matches or cands
                if len(pool) == 1:
                    pool[0]["prefixes"].append(p)
                else:
                    # 同电压同回路多段：最大塔基前缀归首段（主干），其余前缀按序归后续 π 接/分支段
                    slot = min(kv_groups_assigned.get((pkv, is_double), 0), len(pool) - 1)
                    pool[slot]["prefixes"].append(p)
                    kv_groups_assigned[(pkv, is_double)] = slot + 1
        else:
            for idx, p in enumerate(sorted(prefix_info.keys()), 1):
                pkv = prefix_info[p]["kv"]
                pcirc = prefix_info[p]["circuit"]
                segments.append({
                    "index": idx,
                    "name": f"{p}回（{pkv}线路段）",
                    "kv": pkv,
                    "circuit": pcirc,
                    "length_km": None,
                    "prefixes": [p],
                })
            if not segments:
                segments.append({
                    "index": 1, "name": "线路段1", "kv": "220kV",
                    "circuit": "单回路", "length_km": None, "prefixes": [],
                })
        return cls(segments, tower_id_regex=tower_id_regex)

    def all_names(self):
        return [s["name"] for s in self.segments]

    def for_tower_prefix(self, seg_code: str, default: str = "") -> str:
        code = (seg_code or "").strip().upper()
        for s in self.segments:
            if code and code in s.get("prefixes", []):
                return s["name"]
        return self.segments[0]["name"] if self.segments else (default or "线路段1")

    def for_subproject_index(self, idx_1based: int, hint_text: str = "") -> str:
        """将材料清册中的第 idx_1based 个单项工程（或标题线索）映射到标准线路段名称。"""
        h = str(hint_text or "")
        if 1 <= idx_1based <= len(self.segments):
            cand = self.segments[idx_1based - 1]
            if ("110kV" in h and "110" not in cand["kv"]) or ("220kV" in h and "220" not in cand["kv"]):
                for s in self.segments:
                    if ("110kV" in h and "110" in s["kv"]) or ("220kV" in h and "220" in s["kv"]):
                        return s["name"]
            return cand["name"]
        if self.segments:
            return self.segments[(idx_1based - 1) % len(self.segments)]["name"]
        return f"线路段{idx_1based}"

    def to_dict(self):
        return {"segments": self.segments, "tower_id_regex": self.tower_id_regex}
