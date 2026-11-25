"""项目问答：程序统计的项目概况 + 模型选择、程序执行的受控查询。条数、数量合计一律由代码计算，模型只组织回答。"""
import re
from collections import Counter, defaultdict

from result_qa import parents

LIST_LIMIT = 60
GROUP_LIMIT = 40
OPS = ('包含', '不包含', '等于', '不等于', '大于', '小于', '不小于', '不大于', '为是', '为否', '为空', '非空')
NUMERIC = ('数量', '价格', '组成件数')


def _num(value):
    match = re.search(r'-?\d+(?:\.\d+)?', str(value or '').replace(',', ''))
    return float(match.group()) if match else None


def _cut(text, size):
    text = str(text or '').strip()
    return text if len(text) <= size else text[:size] + '…'


def _place(record, key):
    return str((record.get('_source') or {}).get(key) or '')


def _common(name, spec, qty, unit):
    return {'序号': lambda r: str(r.get('序号')), '名称': lambda r: str(r.get(name) or ''), '规格': lambda r: str(r.get(spec) or ''),
            '数量': lambda r: _num(r.get(qty)), '单位': lambda r: str(r.get(unit) or ''),
            '父级': lambda r: ' > '.join(parents(r)), '品类': lambda r: ' · '.join(r.get('品类') or []) or '未归类',
            '计量性质': lambda r: str(r.get('计量性质') or '实物'),
            '文件': lambda r: _place(r, 'file'), '工作表': lambda r: _place(r, 'sheet')}


FIELDS = {
    '清册': {**_common('设备材料名称', '型号及规范', '数量', '单位'),
             '成套行': lambda r: bool(r.get('成套行')), '组成件数': lambda r: len(r.get('下级物资') or []),
             '价格': lambda r: _num(r.get('价格')), '备注': lambda r: str(r.get('备注') or '')},
    '造价': {**_common('Estimation_Name', '型号及规范', 'Estimation_Quantity', 'Estimation_Unit'),
             '成套组成': lambda r: bool(r.get('成套组成')), '组成件数': lambda r: len(r.get('成套组成') or []),
             '价格': lambda r: _num(r.get('Estimation_Price')), '供货方式': lambda r: str(r.get('Supply_Type') or '')},
    '结果': {'清册编号': lambda r: str(r.get('target_id') or ''), '清册名称': lambda r: str(r.get('target') or '') if r.get('target_id') else '',
             '造价编号': lambda r: str(r.get('source_id') or ''), '造价名称': lambda r: str(r.get('name') or '') if r.get('source_id') else '',
             '结论': lambda r: str(r.get('band') or ''), '依据': lambda r: str(r.get('reason') or ''),
             '已确认': lambda r: bool(r.get('confirmed')), '成套项': lambda r: bool(r.get('covers')),
             '成套组成件': lambda r: bool(r.get('covered_by'))},
}
FIELD_NOTES = {'成套行': '清洗时识别为成套形式列出的清册条目', '成套组成': '原表声明了组成件的造价条目',
               '价格': '原表价格列，未注明单价或合价', '成套项': '整体覆盖清册组成件的造价成套项', '成套组成件': '与造价成套项多对多匹配、随成套项整体核对的组成件'}


def schema():
    return {side: [f + (f'（{FIELD_NOTES[f]}）' if f in FIELD_NOTES else '') for f in fields] for side, fields in FIELDS.items()}


def parent_names(dataset):
    return {side: sorted({' > '.join(parents(r)) for r in (dataset or {}).get(key) or []}) for side, key in (('清册', 'targets'), ('造价', 'sources'))}


def _side_profile(records):
    groups = Counter(' > '.join(parents(r)) for r in records)
    return {'条目': len(records), '父级数': len(groups), '各父级条目': dict(groups),
            '品类大类': dict(Counter((r.get('品类') or ['未归类'])[0] for r in records).most_common(15)),
            '计量性质': dict(Counter(str(r.get('计量性质') or '实物') for r in records)),
            '常用单位': dict(Counter(str(r.get('单位') or r.get('Estimation_Unit') or '') for r in records).most_common(12))}


def _brief(record, side):
    name = record.get('设备材料名称') if side == '清册' else record.get('Estimation_Name')
    qty, unit = (record.get('数量'), record.get('单位')) if side == '清册' else (record.get('Estimation_Quantity'), record.get('Estimation_Unit'))
    return f"{record.get('序号')} {_cut(name, 30)} {qty or ''}{unit or ''}".strip()


def profile(project, dataset, rows, covers):
    """程序统计的项目概况。成套物资按三种口径分别给出，避免模型把“以套计量”当成成套。"""
    dataset = dataset or {}
    targets, sources = dataset.get('targets') or [], dataset.get('sources') or []
    by_source = {str(s.get('序号')): s for s in sources}
    set_rows = [t for t in targets if t.get('成套行')]
    declared = [s for s in sources if s.get('成套组成')]
    result = {
        '工程': project, '来源文件': [f.get('name') for f in dataset.get('files') or []][:20],
        '清册': _side_profile(targets), '造价': _side_profile(sources),
        '成套物资': {
            '清册成套行': {'说明': FIELD_NOTES['成套行'], '条数': len(set_rows),
                           '条目': [_brief(t, '清册') + f"（组成件 {len(t.get('下级物资') or [])} 条）" for t in set_rows]},
            '造价声明组成件的成套项': {'说明': FIELD_NOTES['成套组成'], '条数': len(declared),
                                       '条目': [_brief(s, '造价') + f"（组成件 {len(s['成套组成'])} 条）" for s in declared]},
            '匹配中整体覆盖清册组成件的造价成套项': {'条数': len(covers or {}), '覆盖清册组成件': sum(len(v) for v in (covers or {}).values()),
                                                   '条目': [_brief(by_source[k], '造价') + f'（覆盖清册 {len(v)} 条）' for k, v in (covers or {}).items() if k in by_source]},
            '以套计量的条目（不等于成套物资）': {'清册': sum(1 for t in targets if str(t.get('单位')).strip() == '套'),
                                               '造价': sum(1 for s in sources if str(s.get('Estimation_Unit')).strip() == '套')}},
    }
    if rows:
        result['匹配结果'] = {'结果行': len(rows), '结论分布': dict(Counter(r.get('band') for r in rows)),
                              '两侧已配对': sum(1 for r in rows if r.get('source_id') and r.get('target_id')),
                              '清册单侧未匹配': sum(1 for r in rows if r.get('target_id') and not r.get('source_id') and not r.get('covered_by')),
                              '造价单侧未匹配': sum(1 for r in rows if r.get('source_id') and not r.get('target_id') and not r.get('covers') and not r.get('covered_by')),
                              '已人工确认': sum(1 for r in rows if r.get('confirmed'))}
    else:
        result['匹配结果'] = '尚未完成物资匹配'
    return result


def clean_queries(queries):
    """只保留合法查询：未知侧、字段或运算直接丢弃，不执行模型给出的任何其他内容。"""
    kept = []
    for q in queries if isinstance(queries, list) else []:
        if not isinstance(q, dict) or q.get('side') not in FIELDS:
            continue
        fields = FIELDS[q['side']]
        where = [w for w in q.get('where') or [] if isinstance(w, dict) and w.get('field') in fields and w.get('op') in OPS]
        group = q.get('group_by') if q.get('group_by') in fields else None
        total = q.get('sum') if q.get('sum') in NUMERIC and q.get('sum') in fields else None
        if not (where or group or total or q.get('list')):
            continue  # 无条件、无分组的查询只得到总条数，概况里已有
        kept.append({'side': q['side'], 'where': where, 'group_by': group, 'sum': total, 'list': bool(q.get('list'))})
    return kept[:4]


def _match(value, op, expected):
    text = '' if value is None else str(value)
    if op == '包含':
        return str(expected) in text
    if op == '不包含':
        return str(expected) not in text
    if op == '等于':
        return text == str(expected) or (_num(value) is not None and _num(value) == _num(expected))
    if op == '不等于':
        return not _match(value, '等于', expected)
    if op in ('为是', '为否'):
        return bool(value) == (op == '为是')
    if op in ('为空', '非空'):
        return (text.strip() in ('', '0', 'False')) == (op == '为空')
    a, b = _num(value), _num(expected)
    if a is None or b is None:
        return False
    return {'大于': a > b, '小于': a < b, '不小于': a >= b, '不大于': a <= b}[op]


def _item(record, side):
    if side == '结果':
        return {'清册': f"{record.get('target_id')} {record.get('target')}" if record.get('target_id') else '未匹配',
                '造价': f"{record.get('source_id')} {_cut(record.get('name'), 30)}" if record.get('source_id') else '未匹配',
                '结论': record.get('band'), '依据': _cut(record.get('reason'), 80)}
    get = FIELDS[side]
    item = {'序号': get['序号'](record), '名称': _cut(get['名称'](record), 40), '规格': _cut(get['规格'](record), 40),
            '数量': record.get('数量') if side == '清册' else record.get('Estimation_Quantity'), '单位': get['单位'](record), '父级': get['父级'](record)}
    # 成套标记随条目给出，避免模型按单位“套/组/面”自行猜测是否成套
    flag = '成套行' if side == '清册' else '成套组成'
    if get[flag](record):
        item[flag] = f"是（组成件 {get['组成件数'](record)} 条）"
    return item


def describe(q):
    parts = [q['side']] + [f"{w['field']}{w['op']}{w.get('value', '')}" for w in q['where']]
    if q['group_by']:
        parts.append('按' + q['group_by'] + '分组')
    if q['sum']:
        parts.append('合计' + q['sum'])
    return ' · '.join(parts)


def run_query(q, dataset, rows):
    records = rows if q['side'] == '结果' else (dataset or {}).get('targets' if q['side'] == '清册' else 'sources') or []
    fields = FIELDS[q['side']]
    hit = [r for r in records if all(_match(fields[w['field']](r), w['op'], w.get('value', '')) for w in q['where'])]
    result = {'查询': describe(q), '条数': len(hit)}
    if q['group_by']:
        groups = defaultdict(list)
        for r in hit:
            groups[str(fields[q['group_by']](r))].append(r)
        ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        result['分组'] = [{'值': k, '条数': len(v), **({'合计': _total(v, q, fields)} if q['sum'] else {})} for k, v in ordered[:GROUP_LIMIT]]
    if q['sum']:
        result['合计'] = _total(hit, q, fields)
    if q['list'] or (not q['group_by'] and not q['sum'] and len(hit) <= 20):
        result['列表'] = [_item(r, q['side']) for r in hit[:LIST_LIMIT]]
        if len(hit) > LIST_LIMIT:
            result['列表省略'] = len(hit) - LIST_LIMIT
    return result


def _total(records, q, fields):
    """数量按单位分开合计，不同单位不相加。"""
    if q['sum'] != '数量':
        return round(sum(fields[q['sum']](r) or 0 for r in records), 4)
    by_unit = defaultdict(float)
    for r in records:
        by_unit[fields['单位'](r) or '无单位'] += fields['数量'](r) or 0
    return {unit: round(v, 4) for unit, v in by_unit.items()}
