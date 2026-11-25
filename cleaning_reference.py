"""Compare generated data to human fixtures without substituting fixture values."""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def normalized(value):
    return re.sub(r'[\s"“”]', '', unicodedata.normalize('NFKC', str(value or ''))).lower()


def comparable(value, numeric=False):
    if numeric and str(value).strip():
        try:
            number = Decimal(str(value).replace(',', ''))
            if number.is_finite(): return number
        except InvalidOperation:
            pass
    return normalized(value)


def compare_side(manual, actual, side):
    fields = (['Estimation_Name','Estimation_Unit','Estimation_Quantity','Estimation_Price','Supply_Type']
              if side=='sources' else ['设备材料名称','型号及规范','单位','数量','备注'])
    identity = ['Estimation_Name','Estimation_Unit','Supply_Type'] if side=='sources' else ['设备材料名称','型号及规范','单位']
    numeric = {'Estimation_Quantity','Estimation_Price','数量'}
    def key(row): return tuple(normalized(row.get(k,'')) for k in identity)
    def differences(left, right):
        return [{'field':k,'manual':left.get(k,''),'actual':right.get(k,'')}
                for k in fields if comparable(left.get(k,''),k in numeric)!=comparable(right.get(k,''),k in numeric)]
    pool = defaultdict(list)
    for row in actual: pool[key(row)].append(row)
    missing, changes, matched = [], [], 0
    for row in manual:
        candidates = pool[key(row)]
        if not candidates:
            missing.append(row); continue
        # Match quantity first so deferred price/notes cannot hide a quantity error.
        index = min(range(len(candidates)),key=lambda i:(
            sum(d['field'] not in {'Estimation_Price','备注'} for d in differences(row,candidates[i])),
            len(differences(row,candidates[i]))))
        candidate = candidates.pop(index)
        delta = differences(row,candidate)
        matched += 1
        if delta:
            changes.append({'manual_id':row.get('序号'),'actual_id':candidate.get('序号'),
                            'name':row.get(fields[0]),'source':candidate.get('_source'), 'fields':delta})
    extra = [row for candidates in pool.values() for row in candidates]
    core_changes = [c for c in changes if any(d['field'] not in {'Estimation_Price','备注'} for d in c['fields'])]
    return dict(manual_count=len(manual),actual_count=len(actual),matched_count=matched,
                core_equivalent_count=matched-len(core_changes),core_difference_count=len(core_changes),
                equivalent_count=matched-len(changes),missing=missing,extra=extra,differences=changes)


def compare_reference(dataset, sources_path, targets_path):
    reports, files = {}, []
    for side, filename in [('sources', sources_path), ('targets', targets_path)]:
        path = Path(filename)
        if path.stat().st_size > 10_000_000:
            raise ValueError('人工参考文件过大')
        raw = path.read_bytes()
        rows = json.loads(raw.decode('utf-8-sig'))
        required = ('Estimation_Name','Estimation_Unit','Estimation_Quantity') if side=='sources' else ('设备材料名称','单位','数量')
        if not isinstance(rows,list) or not rows or any(not isinstance(row,dict) or any(k not in row for k in required) for row in rows):
            raise ValueError('人工参考文件不是有效物资数组')
        files.append(dict(side=side,path=str(path),sha256=hashlib.sha256(raw).hexdigest(),count=len(rows)))
        reports[side] = compare_side(rows,dataset[side],side)
    return dict(files=files,results=reports,
                policy='逐条核查并保留重复项；核心验收物资名称、规格、单位、数量及供货类型。单价与备注差异单列保留。忽略序号及空白、引号、全半角格式差异，数值按数值比较。人工结果不覆盖原表提取值。')
