"""导出·结果字段：决定匹配结果明细表的列及其顺序，并把评审结果行整理为导出格式。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。写文件由宿主完成。
"""
XLSX_COLUMNS = ["分组序号", "源_序号", "源_同类项名称", "源_名称", "源_型号", "源_数量", "源_单位",
                "匹配_同类项名称", "匹配_发现", "匹配_原因", "匹配_置信",
                "匹配_序号", "匹配_数量", "匹配_单位", "匹配_名称", "匹配_型号", "匹配_价格",
                "成套项_可研序号", "成套项_覆盖清册序号", "成套项_覆盖可研序号", "成套项_组内配对", "成套项_所属", "成套项_依据", "成套项_复核"]


def columns():
    return list(XLSX_COLUMNS)


def flatten(rows):
    flat = []
    for r in rows:
        d = dict(r)
        d.pop("Top3_备选", None)
        for key in ("成套项_覆盖清册序号", "成套项_覆盖可研序号"):
            if isinstance(d.get(key), list):
                d[key] = "、".join(str(x) for x in d[key])
        if isinstance(d.get("成套项_组内配对"), list):
            d["成套项_组内配对"] = "、".join("%s↔%s" % tuple(p) for p in d["成套项_组内配对"])
        flat.append({c: d.get(c, "") for c in XLSX_COLUMNS})
    return flat
