"""匹配结果追问：定位相关条目 → 组装证据包 → 校验模型引用。只读，不改动匹配结果、配对或技能。"""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

SEVERITY = ('不一致', '部分一致', '评审未完成', '待复核', '基本一致', '一致', '不参与核对')
CHECKED = ('一致', '基本一致', '部分一致', '不一致')  # 父级汇总取子项最严重差异，与可视化连线口径一致
ROW_LIMIT = 60
QUESTION_WORDS = ('为什么', '为何', '原因', '怎么', '如何', '是否', '是不是', '有没有', '哪些', '哪个', '哪几',
                  '多少', '情况', '什么', '依据', '吗', '呢')
RESULT_WORDS = ('匹配', '配对', '一致', '核对', '结论', '清册', '造价', '估算', '父级', '成套', '差异', '漏项', '单侧')
SIDES = (('清册', 'targets', 'target_id'), ('造价', 'sources', 'source_id'))
FOLLOW_UP = r'这|那|其中|上述|上面|前面|刚才|它|该|此'
BAND_SCOPE = '结论为'


def is_question(text):
    text = str(text).strip()
    return text.endswith(('？', '?')) or any(w in text for w in QUESTION_WORDS)


def about_results(text):
    return any(w in text for w in RESULT_WORDS)


def parents(record):
    return [str(p) for p in (record.get('层级路径') or [record.get('层级') or '未记录父级'])]


def row_key(row):
    return str(row.get('source_id') or ''), str(row.get('target_id') or '')


def _norm(text):
    return re.sub(r'[\s　“”"‘’\'「」《》]', '', str(text or '')).lower()


def user_terms(text):
    """匹配引擎的评审文字用“源/匹配”指造价/清册，交给模型前换成用户用语；“电源、来源、未匹配、匹配结果”等保持原样。"""
    text = re.sub(r'(?<![来电资光能水气热根])源(?![头自于])', '造价', str(text or ''))
    text = re.sub(r'可研(?!估算书)', '造价', text)   # 旧版结果与用户提问中的“可研”统一称造价
    text = re.sub(r'(?<![未已不相互配])匹配(?![结得度关率上到])', '清册', text)
    return text.replace('造价表', '造价').replace('清册表', '清册')


def _specific(key):
    """“110kV”“SF6”这类电压、型号片段在大量物资名称中都会出现，至少含两个汉字才作为物资名称命中。"""
    return len(re.findall(r'[一-鿿]', key)) >= 2


def _cut(text, size):
    text = str(text or '').strip()
    return text if len(text) <= size else text[:size] + '…'


def _head(name):
    return re.split(r'[\s　（(，,：:]', str(name or '').strip())[0]


def _unique(rows):
    seen, result = set(), []
    for row in rows:
        if row_key(row) not in seen:
            seen.add(row_key(row))
            result.append(row)
    return result


def _records(dataset):
    return {side: {str(x.get('序号')): x for x in (dataset or {}).get(field) or []} for side, field, _ in SIDES}


def resolve_refs(refs, rows):
    """前端引用（勾选/筛选/单条的行键，或父级下的物资序号）转为当前结果行；失效引用直接报错，不静默忽略。"""
    by_key = {row_key(r): r for r in rows}
    picked, labels = [], []
    for ref in refs[:20]:
        label = _cut(ref.get('label') or '结果引用', 60)
        if ref.get('kind') == 'rows':
            keys = [tuple(str(x) for x in k) for k in (ref.get('keys') or [])[:2000] if isinstance(k, (list, tuple)) and len(k) == 2]
            hit = [by_key[k] for k in keys if k in by_key]
        elif ref.get('kind') == 'group' and ref.get('side') in ('清册', '造价'):
            field = 'target_id' if ref['side'] == '清册' else 'source_id'
            ids = {str(x) for x in (ref.get('ids') or [])[:2000]}
            hit = [r for r in rows if r.get(field) and str(r[field]) in ids]
        else:
            raise ValueError('无法识别的结果引用：' + label)
        if not hit:
            raise ValueError(f'引用“{label}”已不在当前匹配结果中，请移除后重新选择')
        picked += hit
        labels.append(label)
    return _unique(picked), labels


def locate(question, dataset, rows):
    """无引用时按问题文本定位：明确编号 → 原表父级 → 物资名称 → 结论档位。返回 (行, 范围说明, 补充说明)。"""
    q = _norm(question)
    quoted = {_norm(x) for x in re.findall(r'[“"「『]([^”"」』]{1,40})[”"」』]', question)}
    numbered = []
    for side, number in re.findall(r'(清册|造价|估算)\s*(?:编号|序号)?\s*#?\s*(\d+)', question):
        field = 'target_id' if side == '清册' else 'source_id'
        numbered += [r for r in rows if str(r.get(field) or '') == number]
    if numbered:
        return _unique(numbered), '问题中指明的编号', []

    groups = {}
    for side, field, _ in SIDES:
        for record in (dataset or {}).get(field) or []:
            for name in parents(record):
                groups.setdefault((side, name), set()).add(str(record.get('序号')))
    hits = [k for k in groups if _norm(k[1]) in quoted or (_specific(_norm(k[1])) and _norm(k[1]) in q)]
    # 同时命中“静止无功补偿装置”和“10kV静止无功补偿装置”时只保留更具体的名称
    hits = [k for k in hits if not any(_norm(k[1]) != _norm(o[1]) and _norm(k[1]) in _norm(o[1]) for o in hits)]
    if hits:
        picked, scopes, notes = [], [], []
        for side, name in hits:
            field = 'target_id' if side == '清册' else 'source_id'
            found = [r for r in rows if str(r.get(field) or '') in groups[(side, name)]]
            picked += found
            scopes.append(f'{side}父级「{name}」{len(found)} 条结果')
        records = _records(dataset)
        for name in dict.fromkeys(name for _, name in hits):
            present = {side for side, n in hits if n == name}
            for missing in {'清册', '造价'} - present:
                field = 'target_id' if missing == '清册' else 'source_id'
                counts = Counter(' > '.join(parents(records[missing][str(r[field])])) for r in picked
                                 if r.get(field) and str(r[field]) in records[missing])
                where = '、'.join(f'「{k}」{v} 条' for k, v in counts.most_common(5)) if counts else f'本范围内没有与之配对的{missing}物资'
                notes.append(f'{missing}表中没有名为「{name}」的父级；与之配对的{missing}物资位于父级：{where}')
        return _unique(picked), '、'.join(scopes), notes

    named = []
    for row in rows:
        for field, name_field in (('target_id', 'target'), ('source_id', 'name')):
            if not row.get(field):
                continue
            # 名称首段（如“铝合金水平电缆桥架”）或去空格的完整名称（如“126kVSF6瓷柱式断路器…”）出现在问题中才算提到
            for key in dict.fromkeys((_norm(_head(row.get(name_field))), _norm(row.get(name_field)))):
                if _specific(key) and key in q:
                    named.append((key, row))
    longest = {h for h, _ in named if not any(h != o and h in o for o, _ in named)}
    picked = [row for h, row in named if h in longest]
    if picked:
        return _unique(picked), '问题中提到的物资：' + '、'.join(sorted(longest)), []

    bands = [b for b in ('部分一致', '基本一致', '不一致', '待复核', '评审未完成', '不参与核对') if b in question]
    if bands:
        return [r for r in rows if r.get('band') in bands], BAND_SCOPE + '、'.join(f'「{b}」' for b in bands) + '的条目', []
    return [], '', []


def _plain(text):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(text or ''))).lower()


def _cell(row, column):
    value = row[column - 1] if column and 0 < column <= len(row) else None
    return '' if value is None else str(value).strip()


def raw_mentions(question, dataset, read_rows):
    """问题提到的原表行若未入库（定额、安装费等非物资行），查原表快照说明原因，并给出原表中紧邻其上的已入库物资。
    read_rows(file, sheet) 返回原表该工作表各行的单元格值。返回 {'notes': [说明字段], 'ids': {'造价': set, '清册': set}}。"""
    q = _plain(question)
    quoted = [x for x in (_plain(t) for t in re.findall(r'[“"「『”]([^“”"」』]{2,60})[”"」』“]', question)) if len(x) >= 4]
    loaded = {}
    for side, field, _ in SIDES:
        for record in (dataset or {}).get(field) or []:
            src = record.get('_source') or {}
            loaded[(src.get('file'), src.get('sheet'), src.get('row'))] = (side, record)
    rejected = {(r.get('file'), r.get('sheet'), r.get('row')): r.get('reason') for r in (dataset or {}).get('rejected_rows') or []}
    notes, ids = [], {'造价': set(), '清册': set()}
    for audit in (dataset or {}).get('sheet_audit') or []:
        columns = audit.get('columns') or {}
        if audit.get('side') not in ('sources', 'targets') or audit.get('status') != '已提取' or not columns.get('name'):
            continue
        side = '造价' if audit['side'] == 'sources' else '清册'
        try:
            sheet_rows = read_rows(audit['file'], audit['sheet'])
        except (OSError, ValueError, KeyError):
            continue
        for index, row in enumerate(sheet_rows):
            name = _cell(row, columns['name'])
            plain = _plain(name)
            if len(plain) < 4 or (audit['file'], audit['sheet'], index + 1) in loaded:
                continue
            if not (any(SequenceMatcher(None, plain, x).ratio() >= 0.85 for x in quoted) or (len(plain) >= 6 and plain in q)):
                continue
            supply = _cell(row, columns.get('supply')).strip('（）() ')
            unit, qty = _cell(row, columns.get('unit')), _cell(row, columns.get('qty'))
            reason = rejected.get((audit['file'], audit['sheet'], index + 1))
            if not reason and columns.get('supply'):
                reason = (f'编制依据为“{supply}”' if supply else '编制依据为空') + '，不是甲设、乙设、甲材、乙材，属定额、安装费等非物资行，清洗时不作为物资提取'
            # 分字段给模型，避免整段说明被逐字照抄进回答
            note = {'原表行': f'{side}「{audit["sheet"]}」第{index + 1}行“{name}”', '单位数量': f'{qty}{unit}',
                    '未入库原因': reason or '清洗时未提取为物资，原因未记录'}
            neighbor = next((loaded[(audit['file'], audit['sheet'], j)] for j in range(index, max(0, index - 5), -1)
                             if (audit['file'], audit['sheet'], j) in loaded), None)
            if neighbor:
                record = neighbor[1]
                label = record.get('Estimation_Name') or record.get('设备材料名称')
                note['对应物资'] = f'原表第{record["_source"]["row"]}行“{label}”（{neighbor[0]}编号{record.get("序号")}），紧邻其上，该行是它的安装费用'
                note['link'] = (neighbor[0], str(record.get('序号')))
                ids[neighbor[0]].add(str(record.get('序号')))
            else:
                note['对应物资'] = '原表附近没有已入库的物资行'
            notes.append(note)
            if len(notes) >= 5:
                return {'notes': notes, 'ids': ids}
    return {'notes': notes, 'ids': ids}


CODE = re.compile(r'[A-Za-z]{1,6}[-_]?\d[\w\-/×.]*')


def _codes(text):
    return {c.upper() for c in CODE.findall(unicodedata.normalize('NFKC', str(text or ''))) if len(c) >= 3}


def _similar(a_name, a_spec, b_name, b_spec):
    """名称相似度；两侧型号编码相同（如 TJ-50、GYFTZY63）直接视为高度相近。"""
    a, b = _plain(a_name), _plain(b_name)
    if not a or not b:
        return 0.0
    score = SequenceMatcher(None, a, b).ratio()
    if len(re.findall(r'[一-鿿]', min(a, b, key=len))) >= 2 and (a in b or b in a):
        score = max(score, 0.75)
    if _codes(f'{a_name} {a_spec}') & _codes(f'{b_name} {b_spec}'):
        score = max(score, 0.9)
    return score


def counterpart_search(picked, dataset, rows, read_rows=None, limit=12):
    """单侧条目（只有造价或只有清册）到另一侧检索相近物资：清洗后的物资附当前配对状态，以及另一侧原表中未作为物资入库的相近行。
    让模型回答“清册原表中确实没有吗”这类问题时有据可查，而不是只看到对侧为空。"""
    names = {'造价': ('Estimation_Name', 'Estimation_Quantity', 'Estimation_Unit'), '清册': ('设备材料名称', '数量', '单位')}
    records = _records(dataset)
    state = {}
    for r in rows:
        s, t = str(r.get('source_id') or ''), str(r.get('target_id') or '')
        if s and t:
            state[('造价', s)] = f'已与清册{t}“{_cut(r.get("target"), 20)}”配对，结论{r.get("band")}'
            state[('清册', t)] = f'已与造价{s}“{_cut(r.get("name"), 20)}”配对，结论{r.get("band")}'
        elif r.get('covered_by'):
            state[('清册' if t else '造价', t or s)] = f'成套对应（多对多），随造价成套项{r["covered_by"]}整体核对，结论{r.get("band")}'
    out = []
    for r in [r for r in picked if bool(r.get('source_id')) != bool(r.get('target_id')) and not r.get('covered_by') and not is_set_anchor(r)][:limit]:
        side = '造价' if r.get('source_id') else '清册'
        other = '清册' if side == '造价' else '造价'
        name, spec = (r.get('name'), r.get('spec')) if side == '造价' else (r.get('target'), r.get('target_spec'))
        qty, unit = (r.get('quantity'), r.get('unit')) if side == '造价' else (r.get('target_quantity'), r.get('target_unit'))
        key_name, key_qty, key_unit = names[other]
        scored = sorted(((_similar(name, spec, x.get(key_name), x.get('型号及规范')), x) for x in records[other].values()),
                        key=lambda p: -p[0])
        found = [{f'{other}编号': str(x.get('序号')), '名称规格': _cut(f"{x.get(key_name) or ''} {x.get('型号及规范') or ''}", 60),
                  '数量': f"{x.get(key_qty, '')}{x.get(key_unit, '')}", '父级': ' > '.join(parents(x)),
                  '原表位置': "%s 第%s行" % ((x.get('_source') or {}).get('sheet', ''), (x.get('_source') or {}).get('row', '')),
                  '相似度': round(score, 2), '当前状态': state.get((other, str(x.get('序号'))), '未配对')}
                 for score, x in scored[:3] if score >= 0.5]
        item = {'条目': 'R%s %s“%s %s” %s%s' % (r['id'], side, _cut(name, 30), _cut(spec, 30), qty or '', unit or ''),
                f'在{other}中检索': found or f'{other}清洗后的物资中没有名称或型号相近的条目'}
        if read_rows:
            raw = _raw_similar(name, spec, other, dataset, read_rows)
            if raw:
                item[f'{other}原表中未作为物资入库的相近行'] = raw
        out.append(item)
    return out


def _raw_similar(name, spec, side, dataset, read_rows):
    loaded = {((x.get('_source') or {}).get('file'), (x.get('_source') or {}).get('sheet'), (x.get('_source') or {}).get('row'))
              for x in (dataset or {}).get('sources' if side == '造价' else 'targets') or []}
    hits = []
    for audit in (dataset or {}).get('sheet_audit') or []:
        columns = audit.get('columns') or {}
        if audit.get('side') != ('sources' if side == '造价' else 'targets') or audit.get('status') != '已提取' or not columns.get('name'):
            continue
        try:
            sheet_rows = read_rows(audit['file'], audit['sheet'])
        except (OSError, ValueError, KeyError):
            continue
        for index, row in enumerate(sheet_rows):
            cell = _cell(row, columns['name'])
            if len(_plain(cell)) < 2 or (audit['file'], audit['sheet'], index + 1) in loaded:
                continue
            score = _similar(name, spec, cell, _cell(row, columns.get('spec')))
            if score >= 0.6:
                hits.append((score, f'{audit["sheet"]} 第{index + 1}行“{_cut(cell, 30)}”（{_cell(row, columns.get("qty"))}{_cell(row, columns.get("unit"))}）'))
    return [text for _score, text in sorted(hits, key=lambda h: -h[0])[:2]]


SET_QUESTION = r'成套|多对多'


def is_set_anchor(row):
    return bool(row.get('covers') or row.get('covers_sources') or row.get('set_pairs'))


def set_owner(row):
    """成套对应组的主项（造价成套项）编号；不属于任何组时为空。"""
    return str(row['source_id']) if is_set_anchor(row) else str(row.get('covered_by') or row.get('in_set') or '')


def _brief(row, side, records):
    info = _side(row, side, records)
    if not isinstance(info, dict):
        return info
    return {'编号': info['编号'], '名称': _cut(info['名称'], 40), '规格': _cut(info['规格'], 50),
            '数量': f"{info['数量'] or ''}{info['单位'] or ''}", '原表位置': info['原表位置']}


def set_groups(rows, records):
    """成套对应组明细（多对多匹配）：造价成套项、清册对应成套行、两侧组成件、组内逐项配对和整组结论。"""
    groups = []
    for anchor in [r for r in rows if is_set_anchor(r)]:
        aid = str(anchor['source_id'])
        members = [r for r in rows if str(r.get('covered_by') or '') == aid]
        inner = [r for r in rows if str(r.get('in_set') or '') == aid]
        group = {'成套项行': 'R%s' % anchor['id'], '造价成套项': _brief(anchor, '造价', records),
                 '清册对应成套行': _brief(anchor, '清册', records) if anchor.get('target_id') else '无（清册以组成件形式列出）',
                 '整组结论': anchor.get('band'), '整组依据': _cut(user_terms(anchor.get('reason')), 260),
                 '清册组成件数': sum(1 for m in members if m.get('target_id')), '造价组成件数': sum(1 for m in members if not m.get('target_id')),
                 '组内逐项配对数': len(inner),
                 '复核方式': anchor.get('set_review') or '规则复核',
                 '清册组成件': [dict(_brief(m, '清册', records), 行='R%s' % m['id'], 归属依据=_cut(m.get('cover_basis'), 50))
                           for m in members if m.get('target_id')],
                 '造价组成件': [dict(_brief(m, '造价', records), 行='R%s' % m['id']) for m in members if not m.get('target_id')],
                 '组内逐项配对': [{'行': 'R%s' % r['id'], '造价': f"{_cut(r.get('name'), 30)} {r.get('quantity') or ''}{r.get('unit') or ''}",
                             '清册': f"{_cut(r.get('target'), 30)} {r.get('target_quantity') or ''}{r.get('target_unit') or ''}",
                             '结论': r.get('band')} for r in inner]}
        groups.append({k: v for k, v in group.items() if v not in ([], '', 0)})
    return groups


def expand(picked, rows):
    """成套项与其组成件整体核对：引用任一方时把另一方一并带入。"""
    sets = ({str(r['source_id']) for r in picked if r.get('covers') or r.get('covers_sources') or r.get('set_pairs')}
            | {str(r.get('covered_by') or r.get('in_set')) for r in picked if r.get('covered_by') or r.get('in_set')})
    related = [r for r in rows if sets and (str(r.get('covered_by') or r.get('in_set') or '') in sets
                                            or ((r.get('covers') or r.get('covers_sources') or r.get('set_pairs')) and str(r.get('source_id')) in sets))]
    return _unique(picked + related)


def worst(bands):
    checked = [b for b in bands if b in CHECKED]
    if checked:
        return max(checked, key=CHECKED.index)
    return '待复核' if bands else ''


def _side(row, side, records):
    target = side == '清册'
    rid = str(row.get('target_id' if target else 'source_id') or '')
    if not rid:
        return '未匹配'
    place = row.get('evidence' if target else 'source') or {}
    record = records[side].get(rid)
    return {'编号': rid, '名称': row.get('target' if target else 'name'), '规格': _cut(row.get('target_spec' if target else 'spec'), 120),
            '数量': row.get('target_quantity' if target else 'quantity'), '单位': row.get('target_unit' if target else 'unit'),
            '父级': ' > '.join(parents(record)) if record else '',
            '原表位置': f"{place.get('sheet', '')} 第{place.get('row', '')}行" if place else ''}


def entry(row, records):
    item = {'行': 'R%s' % row['id'], '清册': _side(row, '清册', records), '造价': _side(row, '造价', records),
            '结论': row.get('band'), '依据': _cut(user_terms(row.get('reason')), 360)}
    advice = (row.get('raw') or {}).get('匹配_置信')
    if advice:
        item['复核建议'] = _cut(user_terms(advice), 120)
    if row.get('covers') or row.get('covers_sources') or row.get('set_pairs'):
        item['成套项'] = {'整体覆盖清册编号': row.get('covers') or [], '造价组成件编号': row.get('covers_sources') or [],
                       '组内逐项配对': row.get('set_pairs') or [], '复核方式': row.get('set_review') or '规则复核'}
    if row.get('in_set'):
        item['成套组内配对'] = {'所属造价成套项': row['in_set']}
    if row.get('covered_by'):
        item['成套对应'] = {'所属造价成套项编号': row['covered_by'], '方式': '多对多，随成套项整体核对，结论与成套项相同', '依据': row.get('cover_basis', '')}
    if row.get('domain'):
        item['领域知识'] = [f"{d.get('type')}·{d.get('id')}·{d.get('status')}" + (f"（{d['reviewer']}）" if d.get('reviewer') else '')
                         for d in row['domain'][:4]]
    if row.get('confirmed') or row.get('human_note'):
        item['人工复核'] = {'已确认': bool(row.get('confirmed')), '意见': _cut(row.get('human_note'), 200)}
    return item


def summary(picked, records, verdicts=True):
    bands = [r.get('band') for r in picked]
    involved = {}
    for side, _, field in SIDES:
        counts = Counter(' > '.join(parents(records[side][str(r[field])])) for r in picked
                         if r.get(field) and str(r[field]) in records[side])
        involved[side] = dict(counts.most_common(12))
    group_reasons = {}
    for row in picked:
        reason = (row.get('raw') or {}).get('匹配_原因')
        if reason and row.get('group'):
            group_reasons.setdefault(row['group'], _cut(user_terms(reason), 300))
    counts = {'两侧已配对': sum(1 for r in picked if r.get('source_id') and r.get('target_id')),
              '清册单侧未匹配': sum(1 for r in picked if r.get('target_id') and not r.get('source_id') and not r.get('covered_by')),
              '造价单侧未匹配': sum(1 for r in picked if r.get('source_id') and not r.get('target_id') and not r.get('covers') and not r.get('covered_by')),
              '成套对应组': sum(1 for r in picked if is_set_anchor(r)),
              '成套对应组成件': sum(1 for r in picked if r.get('covered_by')),
              '已人工确认': sum(1 for r in picked if r.get('confirmed'))}
    # 计数为零的项不给模型，避免回答罗列“0 项”
    result = {'结果条目': len(picked), '结论分布': dict(Counter(bands)), **{k: v for k, v in counts.items() if v}, '涉及父级': involved}
    if verdicts and len(picked) > 1:  # 单条或按结论筛出的跨组条目，整组评价无意义且会让回答重复
        result['按最严重子项汇总'] = worst(bands)
        if group_reasons:
            result['流程分组评价'] = [{'分组': k, '评价': v} for k, v in list(group_reasons.items())[:12]]
    return result


def parent_overview(rows, records):
    groups = {}
    for row in rows:
        side, field = ('清册', 'target_id') if row.get('target_id') else ('造价', 'source_id')
        record = records[side].get(str(row.get(field)))
        groups.setdefault(side + '：' + (' > '.join(parents(record)) if record else '未记录父级'), []).append(row.get('band'))
    return [{'父级': k, '条目': len(v), '汇总': worst(v), '分布': dict(Counter(v))} for k, v in list(groups.items())[:60]]


def build(question, dataset, rows, refs=None, previous=None, raw=None, follow=False):
    """组装追问证据包。keys 记录本次范围，供“那第三条呢”这类后续追问沿用；raw 为 raw_mentions 查到的未入库原表行。"""
    notes = []
    raw = raw or {}
    related = [r for side, field, id_field in SIDES for r in rows
               if r.get(id_field) and str(r[id_field]) in (raw.get('ids') or {}).get(side, ())]
    if refs:
        picked, labels = resolve_refs(refs, rows)
        scope = '用户引用：' + '、'.join(labels)
    elif follow and previous and previous.get('keys') and not re.search(r'R\d+', question):
        # 接着上一轮结果追问、没点名其他条目：沿用上一轮条目，不按问题文字重新定位
        by_key = {row_key(r): r for r in rows}
        picked = [by_key[tuple(k)] for k in previous['keys'] if tuple(k) in by_key]
        scope = '沿用上一问的范围：' + str(previous.get('scope', ''))
    else:
        picked, scope, notes = locate(question, dataset, rows)
        if related:
            picked, scope = _unique(related + picked), '、'.join(x for x in ('原表行对应的物资', scope) if x)
        # 只有问题明确指代前文（那/其中/这些…）时才沿用上一问范围
        if not picked and previous and (follow or re.search(FOLLOW_UP, question)):
            by_key = {row_key(r): r for r in rows}
            picked = [by_key[tuple(k)] for k in previous.get('keys') or [] if tuple(k) in by_key]
            scope = '沿用上一问的范围：' + str(previous.get('scope', ''))
    overall = not picked
    if overall:
        picked, scope = rows, '全部匹配结果（问题未指明具体物资或父级）'
    picked = expand(picked, rows)
    records = _records(dataset)
    # 问成套项（成套物资的匹配情况、明细）：只看范围内的成套对应组，按组给出全部组成件明细
    groups = None
    if re.search(SET_QUESTION, question):
        owners = {set_owner(r) for r in picked} - {''}
        if owners:
            picked = [r for r in rows if set_owner(r) in owners]
            scope += '中的成套对应组（多对多匹配）'
            overall = False
            groups = set_groups(picked, records)
    shown = sorted(sorted(picked, key=lambda r: SEVERITY.index(r.get('band')) if r.get('band') in SEVERITY else 2)[:ROW_LIMIT],
                   key=lambda r: r['id'])
    # 整组汇总只对父级、整体这类成组的问题有意义；逐条勾选的条目不给，避免回答里出现与问题无关的“按最严重子项汇总”
    grouped = (not scope.startswith(BAND_SCOPE) and not (refs and all(r.get('kind') == 'rows' for r in refs))
               and groups is None)  # 成套组问题各组自带整组结论，不再给整体汇总档位
    if groups is not None:
        # 组明细已列出每个成员；条目只放成套项本身，组成件用组明细中的行号引用
        shown = [r for r in picked if is_set_anchor(r) or r.get('in_set')][:ROW_LIMIT]
    evidence = {'范围': scope, '汇总': summary(picked, records, grouped), '条目': [entry(r, records) for r in shown]}
    if groups is not None:
        evidence['汇总']['成套对应组结论分布'] = dict(Counter(g['整组结论'] for g in groups))
        evidence['成套对应组'] = groups
    if notes:
        evidence['范围说明'] = notes
    if raw.get('notes'):
        evidence['原表行说明'] = []
        for note in raw['notes']:
            item = {k: v for k, v in note.items() if k != 'link'}
            if note.get('link'):
                side, rid = note['link']
                id_field = 'source_id' if side == '造价' else 'target_id'
                item['对应结果'] = ['R%s' % r['id'] for r in shown if str(r.get(id_field) or '') == rid] or '未列入本次条目'
            evidence['原表行说明'].append(item)
    if groups is None and len(shown) < len(picked):
        evidence['省略'] = f'另有 {len(picked) - len(shown)} 条差异较轻的条目未逐条列出，已计入汇总'
    if overall:
        evidence['父级概况'] = parent_overview(rows, records)
    # 可引用的行：成套组问题中组成件也可按行号引用
    citable = picked if groups is not None else shown
    return {'scope': scope, 'keys': [] if overall else [list(row_key(r)) for r in picked], 'rows': citable, 'evidence': evidence}


def recent_dialogue(messages, pairs=2):
    """最近几轮问答（结果追问、项目问答、知识问答），让后续提问能接上前文。"""
    turns = []
    for i, m in enumerate(messages):
        if m.get('role') == 'assistant' and (m.get('qa') or m.get('mode')):
            question = next((p['text'] for p in reversed(messages[:i]) if p.get('role') == 'user'), '')
            turns.append(f"问：{_cut(question, 200)}\n答：{_cut(m.get('text'), 500)}")
    return '\n\n'.join(turns[-pairs:]) or '（无）'


def drop_scope_tail(answer):
    """去掉模型在结尾补的“本次核对范围仅涉及……”之类的范围说明段。"""
    parts = str(answer).rstrip().split('\n')
    # “本次范围内共32条…”这类带计数的统计结论保留
    while len(parts) > 1 and re.match(r'^(?!.*\d+\s*[条项个处台组套])\s*(?:本次|以上|本回答|此次)[^。\n]{0,40}范围[^。\n]{0,6}(?:为|是|仅|只|限于|针对)[^\n]{0,80}$', parts[-1]):
        parts.pop()
        while parts and not parts[-1].strip():
            parts.pop()
    text = '\n'.join(parts) or str(answer)
    # 句末附带的范围说明（“…；本次核对范围为所选9条。”）同样去掉，保留前面的正文
    text = re.sub(r'[；;，,]\s*(?:本次|此次)[^。；;\n\d]{0,10}范围(?:为|是|仅|只)[^。\n]{0,40}。?\s*$', '。', text.rstrip())
    return drop_scope_clauses(text)


# 句中只交代回答范围的分句：“都落在这707条范围内”“本次看的是全部匹配结果里的……”“没有限定具体父级”
SCOPE_CLAUSE = re.compile(r'^\s*(?:本次|这次|此次)?看的是|没有限定|^\s*(?:都|均|全都)(?:落)?在(?:这|你所选的?|所选的?)?\s*\d*\s*条?(?:结果)?的?范围内\s*$')


def drop_scope_clauses(text):
    """去掉句中只说明回答范围的分句，保留同一句里的其他内容；整句都是范围说明时删去整句。"""
    out = []
    for sentence in re.split(r'(?<=[。！？\n])', str(text)):
        end = re.search(r'[。！？\n]*$', sentence).group()
        parts = re.split(r'([，,；;])', sentence[:len(sentence) - len(end)])
        clauses, seps = parts[0::2], [''] + parts[1::2]
        kept = [(sep, c) for sep, c in zip(seps, clauses) if not SCOPE_CLAUSE.search(c)]
        if len(kept) == len(clauses):
            out.append(sentence)
        elif kept:
            out.append(kept[0][1] + ''.join(sep + c for sep, c in kept[1:]) + end)
        elif '\n' in end:
            out.append('\n' * end.count('\n'))
    return ''.join(out)


def cite(answer, cited, rows):
    """只保留证据包内的行引用；越界引用从回答中移除并计数，避免模型编造条目。"""
    known = {'R%s' % r['id']: r for r in rows}
    dropped = 0

    def fix(match):
        nonlocal dropped
        key = 'R' + match.group(1)
        if key in known:
            return f'[{key}]'
        dropped += 1
        return ''
    text = re.sub(r'[\[【]((?:\s*R\d+\s*[,，、;；]\s*)+R\d+\s*)[\]】]',
                  lambda m: ''.join(f'[{k}]' for k in re.findall(r'R\d+', m.group(1))), str(answer))
    text = re.sub(r'[\[【]\s*R(\d+)\s*[\]】]', fix, text)
    # 模型有时漏写方括号：证据包内的裸行号补成标注，其他形似文字（如型号中的 R 字样）保持原样
    text = re.sub(r'(?<![\[A-Za-z0-9])R(\d+)(?![\]\d])', lambda m: f'[R{m.group(1)}]' if 'R' + m.group(1) in known else m.group(0), text)
    # 标注在界面上显示为物资名称；去掉模型紧贴标注重复写出的名称，保留括号里名称之外的规格等信息
    heads = {key: _head(row.get('target') if row.get('target_id') else row.get('name')) for key, row in known.items()}

    def tidy(match):
        key, inner = match.group(1), match.group(2)
        head = heads.get(key)
        if not head or not inner.startswith(head):
            return match.group(0)
        rest = inner[len(head):].lstrip('，,、 　')
        if rest[:1] in '（(' and rest[-1:] in '）)':
            rest = rest[1:-1]
        return f'[{key}]' + (f'（{rest}）' if rest else '')
    text = re.sub(r'\[(R\d+)\][（(]([^（）()]{1,80}(?:[（(][^（）()]{0,40}[）)][^（）()]{0,40})?)[）)]', tidy, text)
    for key, head in heads.items():
        if head:
            text = text.replace(f'{head}[{key}]', f'[{key}]').replace(f'[{key}]{head}', f'[{key}]')
    used = list(dict.fromkeys(re.findall(r'\[(R\d+)\]', text) + [str(c) for c in cited or [] if str(c) in known]))
    citations = []
    for key in used:
        row = known[key]
        source_id, target_id = row_key(row)
        label = heads[key]
        title = ' ↔ '.join(x for x in (target_id and f"清册{target_id} {row.get('target')}", source_id and f"造价{source_id} {row.get('name')}") if x)
        citations.append({'key': key, 'source_id': source_id, 'target_id': target_id, 'label': _cut(label, 16) or key,
                          'title': f"{row.get('band')} · {_cut(title, 120)}"})
    return text, citations, dropped
