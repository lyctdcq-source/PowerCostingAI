"""清洗·选表与列映射：以设计清册为基准，用本地规则选出对应的造价物资表并识别列映射，
随后调用“清洗·逐行提取”从原单元格取数，并汇总选表台账与异常。

清洗在本机完成，不调用任何模型：原始资料可能含敏感信息，只有清洗后的物资数据才在用户同意后发送给在线模型。
选表依据工作簿结构（估算书的表一～表七、四类汇总表）和表头；列映射依据表头原文（含多行合并表头）；
多工程数量列按工程标题中出现的站名选择本工程列，对侧站列不选，无法确认时标记待确认、不猜测。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。读取工作簿、文件哈希等由宿主完成。
"""
import re
import unicodedata
from collections import Counter

ROWX = skill("row_extraction")

NAME_WORDS = ('名称',)
UNIT_HEADS = ('单位', '计量单位')
QTY_HEADS = ('数量', '设计用量', '设计数量', '工程量', '本工程数量', '用量')
NOT_QTY = ('单重', '总重', '合重', '损耗', '重量', '指标', '合价', '单价')
ESTIMATE_SHEET = r'表[一二三四五六七][甲乙丙丁戊]?|总算表|总概算|总估算|取费表|其他费用表'
INVENTORY_TITLE = ('清册', '材料表', '材料一览', '设备材料', '材料清单')


def preview(rows, limit):
    """表格预览：前 limit 行、每行前 40 列的非空单元格，供台账展示表头。"""
    return [{'row': i + 1, 'cells': {str(c + 1): ROWX.text(v)[:160] for c, v in enumerate(row[:40]) if ROWX.text(v)}}
            for i, row in enumerate(rows[:limit])]


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', ROWX.text(value)))


def find_header(rows):
    """表头起止行与每列的表头路径。表头行同时有“名称”列和“单位”列；纵向合并的多行表头
    （单位格逐行重复）一并纳入。返回 None 表示没有物资表头。"""
    for start, row in enumerate(rows[:30]):
        cells = [norm(c) for c in row]
        unit = next((i for i, c in enumerate(cells) if c in UNIT_HEADS), None)
        name = next((i for i, c in enumerate(cells) if '名称' in c and '费用名称' not in c and len(c) <= 12), None)
        if unit is None or name is None:
            continue
        end = start
        while end + 1 < len(rows) and unit < len(rows[end + 1]) and norm(rows[end + 1][unit]) == cells[unit]:
            end += 1
        width = max(len(r) for r in rows[start:end + 1])
        paths = []
        for col in range(width):
            path = []
            for r in rows[start:end + 1]:
                cell = norm(r[col]) if col < len(r) else ''
                if cell and cell not in path:
                    path.append(cell)
            paths.append(path)
        return dict(start=start + 1, end=end + 1, name=name + 1, unit=unit + 1, paths=paths)
    return None


def project_evidence(tables, estimate_files):
    """工程标题：各表前 3 行的标题文字。带“对侧”的标题单独计，用来识别对侧站的数量列。"""
    own, other = [], []
    for t in tables:
        for row in t['rows'][:3]:
            for cell in row:
                value = norm(cell)
                if value:
                    (other if '对侧' in value else own).append(value)
    for t in tables:
        if t['file'] in estimate_files:
            for row in t['rows']:
                for cell in row[:4]:
                    value = norm(cell)
                    if '对侧' in value:
                        other.append(value)
    return ' '.join(sorted(set(own))), ' '.join(sorted(set(other)))


def station_core(label):
    core = re.sub(r'^\d+(?:\.\d+)?kV', '', label, flags=re.I)
    return re.sub(r'(变电站|变|站|侧)$', '', core)


def map_columns(table, header, evidence, detail):
    """按表头原文识别列映射；多工程数量列按工程标题选本工程列。"""
    paths = header['paths']
    def cols(test):
        return [i + 1 for i, path in enumerate(paths) if path and test(path)]
    def quote(col):
        return '/'.join(paths[col - 1])
    span = f"表头第{header['start']}" + (f"-{header['end']}" if header['end'] > header['start'] else '') + '行'
    columns = dict(name=header['name'], unit=header['unit'])
    qty = cols(lambda p: any(x in QTY_HEADS for x in p) and not any(w in x for x in p for w in NOT_QTY))
    design = [c for c in qty if '设计用量' in paths[c - 1]]
    qty = design or qty
    review, basis = False, ''
    own_column = [c for c in qty if paths[c - 1][-1] in ('本工程', '本期', '本期工程', '本站')]
    if len(qty) > 1 and len(own_column) == 1:
        basis = f"{span}有多工程数量列，取“{quote(own_column[0])}”（第{own_column[0]}列）为本工程数量"
        qty = own_column
    if len(qty) > 1:
        labels = {c: paths[c - 1][-1] for c in qty}
        own, other = evidence
        scored = []
        for c, label in labels.items():
            core = station_core(label)
            if label == '合计' or len(core) < 2:
                continue
            scored.append((own.count(core) - 3 * other.count(core), c, label))
        scored.sort(reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            qty = [scored[0][1]]
            basis = f"{span}有多工程数量列（{'、'.join(labels.values())}），工程标题含“{station_core(scored[0][2])}”，取“{quote(qty[0])}”（第{qty[0]}列）为本工程数量"
        else:
            review = True
            basis = f"{span}有多工程数量列（{'、'.join(labels.values())}），工程标题无法确认本工程列，需人工指定"
    if not qty:
        return dict(id=table['id'], header_end=header['end'], columns=columns, needs_review=True,
                    quantity_basis=f'{span}未找到数量或设计用量列')
    columns['qty'] = qty[0]
    basis = basis or f"{span}“{quote(qty[0])}”（第{qty[0]}列）为本工程数量"
    spec = cols(lambda p: any('型号' in x or '规格' in x for x in p))
    columns['spec'] = next((c for c in spec if c != columns['name']), None)
    notes = cols(lambda p: any('备注' in x for x in p))
    columns['notes'] = notes[0] if notes else None
    priced = cols(lambda p: any('单价' in x for x in p) and not any('运杂' in x or '合价' in x for x in p))
    columns['price'] = priced[0] if priced else None
    if detail:
        supply = cols(lambda p: any('编制依据' in x for x in p))
        equipment = [c for c in priced if any('设备' in x for x in paths[c - 1])]
        material = [c for c in priced if any('材料' in x for x in paths[c - 1])]
        columns.update(supply=supply[0] if supply else None, equipment_price=equipment[0] if equipment else None,
                       material_price=material[0] if material else None, price=None)
        if None in (columns['supply'], columns['equipment_price'], columns['material_price']):
            review = True
            basis += '；安装明细缺少编制依据、设备费单价或装置性材料单价列，需人工确认'
        else:
            basis += f"；设备单价取“{quote(columns['equipment_price'])}”，材料单价取“{quote(columns['material_price'])}”"
    elif columns['price']:
        basis += f"；单价取“{quote(columns['price'])}”（第{columns['price']}列）"
    return dict(id=table['id'], header_end=header['end'], columns=columns, quantity_basis=basis, needs_review=review)


def estimate_books(tables, skills):
    """估算/概算书：同一工作簿里有两张以上表一～表七、总算表、取费表等造价表，
    或有四类造价物资表、安装单位工程明细（设计清册不会用这些表名）。"""
    count = Counter(t['file'] for t in tables if re.search(ESTIMATE_SHEET, t['sheet']))
    books = {f for f, n in count.items() if n >= 2}
    books.update(t['file'] for t in tables if skills.call('folder_discovery', 'include_estimation_table', t['file'], t['sheet']))
    return books


def select_tables(tables, skills, headers, estimates):
    """逐表决定去向：造价侧取四类汇总表与安装单位工程明细，清册侧取设计清册主表。"""
    decisions = {}
    inventory_books = {}
    for t in tables:
        if t['file'] not in estimates and headers.get(t['id']):
            title = t['sheet'] + ' ' + ' '.join(norm(c) for r in t['rows'][:2] for c in r[:2])
            inventory_books.setdefault(t['file'], []).append((t, any(w in title for w in INVENTORY_TITLE)))
    for t in tables:
        label = t['file'] + ' / ' + t['sheet']
        if t['file'] in estimates:
            if '临时施工' in t['file']:
                decisions[t['id']] = dict(id=t['id'], side='excluded', reason='临时施工用电与永久工程分开，默认不纳入永久工程物资核对')
            elif '余物清理' in t['sheet']:
                decisions[t['id']] = dict(id=t['id'], side='excluded', reason='余物清理表为拆除回收工程量，不是本工程新增物资')
            elif not skills.call('folder_discovery', 'include_estimation_table', t['file'], t['sheet']):
                decisions[t['id']] = dict(id=t['id'], side='excluded', reason='估算书中的费用、取费、价差、人工机械、建筑或指标表，不是四类物资表')
            elif not headers.get(t['id']):
                decisions[t['id']] = dict(id=t['id'], side='excluded', status='待确认', reason='属于四类物资表，但未识别到名称、单位、数量表头，需人工确认')
            else:
                kind = '安装单位工程明细（按编制依据甲设/乙设/甲材/乙材取物资行）' if skills.call('folder_discovery', 'is_estimation_detail', t['sheet']) else '四类造价物资汇总表'
                decisions[t['id']] = dict(id=t['id'], side='sources', reason=f'估算书工作表“{t["sheet"]}”属于{kind}')
            continue
        if not headers.get(t['id']):
            decisions[t['id']] = dict(id=t['id'], side='excluded', reason='未识别到名称、单位、数量表头（封面、说明或空表）')
        elif not skills.call('folder_discovery', 'include_inventory_table', t['file'], t['sheet']):
            decisions[t['id']] = dict(id=t['id'], side='excluded', reason='按清洗规则排除土建清册及其全部分表')
        else:
            book = inventory_books.get(t['file'], [])
            main = [x for x, titled in book if titled]
            if main and t not in main:
                decisions[t['id']] = dict(id=t['id'], side='excluded', reason=f'同一工作簿的主清册为“{main[0]["sheet"]}”，本表是分项统计或计算附表，与主清册重复计算')
            else:
                decisions[t['id']] = dict(id=t['id'], side='targets', reason=f'设计清册：表头含名称、单位、数量列（{label}）')
    return decisions



# ── 装置性材料的比价基准：主材价差表 ─────────────────────────────────────
# 表三甲的装材单价取的是基准期预算价，与市场价的差额在“××主材（材料）价差汇总表”里单列。
# 价差表是表三甲的真子集（实测两本估算书：价差表里的物资全部出现在表三甲，反之不然），
# 所以只回填对得上的那些；对不上的保留原单价并标注基准待确认，由价格评审提示人工核对。
PRICE_DIFF_TITLE = ('价差汇总表', '价差表')


def price_diff_columns(rows):
    """价差表表头：名称、单位，以及“单价”分组下的预算价与市场价两列（合价下也有同名两列，要排除）。"""
    for index, row in enumerate(rows[:12]):
        cells = [ROWX.text(c) for c in row]
        if not any('名称' in c for c in cells):
            continue
        name = next(i for i, c in enumerate(cells) if '名称' in c)
        unit = next((i for i, c in enumerate(cells) if c in ('单位', '计量单位')), None)
        group = next((i for i, c in enumerate(cells) if '单价' in c), None)
        total = next((i for i, c in enumerate(cells) if '合价' in c), None)
        if group is None or index + 1 >= len(rows):
            continue
        sub = [ROWX.text(c) for c in rows[index + 1]]
        limit = total if total is not None else len(sub)
        budget = next((i for i, c in enumerate(sub) if '预算价' in c and group <= i < limit), None)
        market = next((i for i, c in enumerate(sub) if '市场价' in c and group <= i < limit), None)
        if budget is None or market is None:
            continue
        return dict(header=index + 1, name=name, unit=unit, budget=budget, market=market)
    return None


def price_diff_records(tables, files):
    """把属于估算书的价差表读成 {归一名称: 记录}；同名以安装专业的为准。"""
    out = {}
    for table in tables:
        title = table['sheet']
        if not any(word in title for word in PRICE_DIFF_TITLE) or table['file'] not in files:
            continue
        cols = price_diff_columns(table['rows'])
        if not cols:
            continue
        for row in table['rows'][cols['header'] + 1:]:
            def value(key):
                position = cols[key]
                return row[position] if position is not None and position < len(row) else None
            name, market = ROWX.text(value('name')), ROWX.number(value('market'))
            if not name or market is None or market <= 0:
                continue
            key = norm(name)
            if key in out and '安装' not in title:
                continue
            out[key] = dict(name=name, unit=ROWX.text(value('unit')), market=market,
                            budget=ROWX.number(value('budget')), sheet=title, file=table['file'])
    return out


def apply_price_basis(result, records, warnings):
    """装置性材料行回填价差表市场价：原单价保留不动，另记市场价与出处。"""
    filled, pending = 0, 0
    for row in result['sources']:
        if not str(row.get('Supply_Type', '')).endswith('材'):
            continue
        record = records.get(norm(row.get('Estimation_Name', '')))
        if record:
            row.update({'市场价': record['market'], '预算价': record['budget'],
                        '比价基准': '主材价差表市场价（不含税）'})
            row['_source'] = dict(row.get('_source') or {}, price_basis_sheet=record['sheet'])
            filled += 1
        else:
            row['比价基准'] = '表三甲单价（不在价差表中，是否已为市场价待确认）'
            pending += 1
    result['price_basis'] = dict(records=len(records), filled=filled, pending=pending)
    if filled or pending:
        warnings.append(f'装置性材料比价基准：{filled} 条取自主材价差表的市场价（不含税），'
                        f'{pending} 条不在价差表中，暂用表三甲单价，价格评审会标注基准待确认。')
    return result['price_basis']

def clean_tables(root_name, origin, tables, files, warnings, skills, log):
    """tables 为宿主读出的全部工作表 [{id, file, sheet, rows}]；skills 为规则技能库。全程本地规则，不调用模型。"""
    rules = skills.call('folder_discovery', 'discovery_rules')
    if not tables:
        raise ValueError('目录中未找到可读取的 Excel 工作簿')
    log(f'已发现 {len(tables)} 张工作表，正在用本地规则以清册为基准选择对应的四类造价物资表（不调用模型）。')
    by_id = {t['id']: t for t in tables}
    headers = {t['id']: find_header(t['rows']) for t in tables}
    estimates = estimate_books(tables, skills)
    decisions = select_tables(tables, skills, headers, estimates)
    selected = [d for d in decisions.values() if d['side'] != 'excluded']
    excluded = [d for d in decisions.values() if d['side'] == 'excluded']
    plan = dict(selected=selected, excluded=[dict(ids=[d['id']], reason=d['reason']) for d in excluded])
    if {i['side'] for i in selected} != {'sources', 'targets'}:
        raise ValueError('未找到同时包含造价物资和设计清册的方案：' + ('未识别到估算书' if not estimates else '未识别到设计清册'))
    evidence = project_evidence(tables, estimates)
    mappings = {}
    for item in selected:
        table = by_id[item['id']]
        detail = item['side'] == 'sources' and skills.call('folder_discovery', 'is_estimation_detail', table['sheet'])
        mappings[item['id']] = map_columns(table, headers[item['id']], evidence, detail)
        log(f"识别表头：{table['file']} / {table['sheet']} — {mappings[item['id']]['quantity_basis']}")
    result = dict(sources=[], targets=[], files=files, warnings=warnings, sheet_audit=[],
                  origin=origin, label=root_name, cleaning_method='本地规则选表 + 原单元格提取（未调用模型）',
                  cleaning_rules=rules,
                  cleaning_plan=plan, cleaning_mappings=list(mappings.values()), rejected_rows=[], summary_sources=[])
    detail_files = {by_id[i['id']]['file'] for i in selected if i['side']=='sources' and skills.call('folder_discovery', 'is_estimation_detail', by_id[i['id']]['sheet'])}
    for table in tables:
        decision = decisions[table['id']]
        audit = dict(file=table['file'], sheet=table['sheet'], side=decision['side'],
                     reason=decision['reason'], rows=0, status=decision.get('status', '已排除'))
        result['sheet_audit'].append(audit)
        if decision['side'] == 'excluded':
            continue
        mapping = mappings[table['id']]
        if mapping.get('needs_review') is not False:
            audit.update(status='待确认', reason=str(mapping.get('quantity_basis', '未确认数量列')))
            warnings.append(f'{table["file"]} / {table["sheet"]}：数量列待确认，未提取')
            continue
        if decision['side']=='sources' and table['file'] in detail_files and not skills.call('folder_discovery', 'is_estimation_detail', table['sheet']):
            reference = dict(sources=[], targets=[], rejected_rows=[])
            ROWX.extract_table(table, mapping, 'sources', reference, audit, skills)
            result['summary_sources'].extend(reference['sources'])
            audit.update(status='汇总参考', reason=audit['reason']+'；数量与单价回溯安装明细，本表不重复入库')
        else:
            ROWX.extract_table(table, mapping, decision['side'], result, audit, skills)
    def normalize(value):
        return re.sub(r'[\s"“”]', '', unicodedata.normalize('NFKC', value)).lower()
    for row in result['sources']:
        related = [s for s in result['summary_sources'] if normalize(s['Estimation_Name'])==normalize(row['Estimation_Name']) and s['Estimation_Unit']==row['Estimation_Unit']]
        if related:
            row['summary_evidence'] = [dict(source=s['_source'], quantity=s['Estimation_Quantity'], price=s['Estimation_Price']) for s in related]
    basis = apply_price_basis(result, price_diff_records(tables, detail_files), warnings)
    if basis['records']:
        log(f"主材价差表：读到 {basis['records']} 个物资的市场价，回填装置性材料 {basis['filled']} 条，"
            f"{basis['pending']} 条不在价差表中。")
    if not result['sources'] or not result['targets']:
        raise ValueError('清洗未取得完整双侧物资数据，原工程未替换')
    sets = [r for r in result['sources'] if r.get('成套组成')]
    if sets:
        warnings.append(f'造价 {len(sets)} 个成套项在原表列出了 {sum(len(r["成套组成"]) for r in sets)} 个组成件，已随成套项保留，用于一对多核对。')
    kinds = Counter(r['计量性质'] for side in ('sources', 'targets') for r in result[side] if r.get('计量性质'))
    if kinds:
        warnings.append('按领域知识标记为不参与数量核对：' + '、'.join(f'{k} {v} 条' for k, v in kinds.items()) + '；条目保留，可在结果中复核。')
    warnings.append('以设计清册为基准选择造价表；请确认选表台账、数量口径和异常行后继续匹配。')
    log(f'清洗完成：造价 {len(result["sources"])} 条，清册 {len(result["targets"])} 条，清洗处理记录 {len(result["rejected_rows"])} 条。')
    return result
