"""清洗·逐行提取：按列映射从原单元格逐行取出物资，保留文件、工作表、行号与原表层级；
识别原表成套关系，并按领域知识标记计量性质。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
"""
import math
import re
import unicodedata


def text(v):
    return '' if v is None else str(v).strip()


def number(v):
    try:
        value = float(str(v).replace(',', '').strip())
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def heading_level(label):
    """Only numbered headings establish nesting; unknown headings stay siblings."""
    label = unicodedata.normalize('NFKC', str(label)).strip()
    if re.fullmatch(r'[一二三四五六七八九十百]+[、.]?', label): return 1
    if re.fullmatch(r'\([一二三四五六七八九十百]+\)', label): return 2
    if re.fullmatch(r'\d+(?:\.\d+)+\.?', label): return 2 + label.rstrip('.').count('.')
    if re.fullmatch(r'\d+[、.]?', label): return 3
    if re.fullmatch(r'\(\d+\)', label): return 4
    return None


SET_ROW_MAX_LEVEL = 2   # 清册中“一、/（一）”级编号且带单位数量的行是成套行，其下物资为组成件
SUB_ITEM = r'^\s*[(（]?\d{1,2}[)）]\s*'   # “1)托盘式直通桥架”“（2）三通”式子项


def merged_continuations(table, column):
    """数量列被纵向合并时，合并区域首行以下的各行只是同一条物资的延续（读表时值被复制到每行）。"""
    rows = set()
    for r0, r1, c0, c1 in table.get('merges') or []:
        if r1 > r0 and c0 <= column <= c1:
            rows.update(range(r0 + 1, r1 + 1))
    return rows


def item_label(row, name, side):
    """物资编号：清册取序号列，造价取名称前的“2.1、”式编号。"""
    label = unicodedata.normalize('NFKC', text(row[0]) if side == 'targets' else '').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)*', label):
        match = re.match(r'^\s*(\d+(?:\.\d+)*)\s*[、\t．.]', unicodedata.normalize('NFKC', name))
        label = match[1] if match else ''
    return label


def extract_table(table, mapping, side, result, audit, skills):
    """按列映射逐行提取物资。除名称、单位、数量、规格、价格外，记录原表结构：
    层级路径（标题与成套行）、编号、上级物资/下级物资（按编号嵌套或成套行），清册成套行的组成件标记“成套项”。"""
    rows = table['rows']
    numbered, set_records = {}, {}
    end = mapping.get('header_end')
    cols = mapping.get('columns', {})
    detail = side == 'sources' and skills.call('folder_discovery', 'is_estimation_detail', table['sheet'])
    width = max((len(r) for r in rows), default=0)
    if type(end) is not int or not 1 <= end <= min(24, len(rows)):
        raise ValueError(f'{table["sheet"]}：表头行号越界')
    for key in ('name', 'unit', 'qty', 'spec', 'price', 'notes', 'supply', 'equipment_price', 'material_price'):
        col = cols.get(key)
        required = ('name', 'unit', 'qty') + (('supply','equipment_price','material_price') if detail else ())
        if col is None and key not in required:
            continue
        if type(col) is not int or not 1 <= col <= width:
            raise ValueError(f'{table["sheet"]}：{key} 列号无效')
        if not any(col <= len(r) and text(r[col - 1]) for r in rows[:end]):
            raise ValueError(f'{table["sheet"]}：{key} 列没有表头证据')
    if len({cols[k] for k in ('name', 'unit', 'qty')}) != 3 or not mapping.get('quantity_basis'):
        raise ValueError('名称、单位、数量列冲突或缺少数量口径依据')
    audit.update(status='已提取', quantity_column=cols['qty'], quantity_basis=mapping['quantity_basis'], columns=cols)
    group = table['sheet']
    hierarchy = []
    blocked_section = ''
    previous = None
    heading_source = None   # 紧接标题之下、名称留空的规格续行可承接的标题物资名称（如“B 1kV动力电缆”）
    item_header = None      # “1 透气型耐火电缆槽盒”式无数量的物资标题，其下“1)…”子项归入该名称
    merged = merged_continuations(table, cols['qty'])
    for index in range(end, len(rows)):
        row = rows[index]
        def value(key):
            col = cols.get(key)
            return text(row[col - 1]) if col and col <= len(row) else ''
        name, unit, qty = value('name'), value('unit'), value('qty')
        if index + 1 in merged:
            # 数量单元格纵向合并：同一物资，只按合并区域首行计一次；续行上分开书写的规格并入该物资
            audit['merged_rows'] = audit.get('merged_rows', 0) + 1
            spec = value('spec')
            owner = result[side][-1] if result[side] and result[side][-1]['_source'].get('file') == table['file'] else None
            if owner is not None and spec and spec not in owner['型号及规范']:
                owner['型号及规范'] = (owner['型号及规范'] + '\n' + spec).strip()
                owner['_source'].setdefault('merged_spec_rows', []).append(index + 1)
            continue
        if not any(text(v) for v in row) or any(word in name for word in ('小计', '合计')):
            previous = heading_source = item_header = None
            continue
        if name and (not unit and not qty or name == unit == qty):
            group = name
            original_label = text(row[0])
            level = heading_level(original_label)
            if level is None:
                # Do not infer nested parents from title wording alone.
                hierarchy = []
            else:
                while hierarchy and (hierarchy[-1]['level'] is None or hierarchy[-1]['level'] >= level):
                    hierarchy.pop()
            hierarchy.append(dict(name=name, row=index+1, label=original_label, level=level,
                                  basis='原表标题编号' if level is not None else '标题行已保留，父子深度未确认'))
            previous = None
            numbered = {}
            section_id = text(row[0]).strip('（）()、.．')
            if re.fullmatch('[一二三四五六七八九十百]+', section_id):
                blocked_section = name
            label = unicodedata.normalize('NFKC', section_id).strip()
            whole_row = len({text(v) for v in row if text(v)}) == 1   # 整行合并的章节标题
            heading_source = None if whole_row or re.fullmatch('[一二三四五六七八九十百]+', label) else dict(name=name, row=index + 1)
            item_header = dict(name=name, row=index + 1) if re.fullmatch(r'\d+', label) else None
            continue
        supply = value('supply').strip('（）() ')
        if detail and supply not in ('甲设','乙设','甲材','乙材'):
            audit['non_material_rows'] = audit.get('non_material_rows', 0) + 1
            continue
        label = unicodedata.normalize('NFKC', text(row[0])).strip()
        inherited, fill, sub_label, from_heading = None, '', '', False
        if side == 'targets' and not name and number(qty) is not None and value('spec') and (not label or re.fullmatch(r'\d+', label)):
            if heading_source and not label and (previous is None or previous.get('from_heading')):
                name, inherited, from_heading = heading_source['name'], heading_source['row'], True
                fill = '标题下名称留空的规格续行，承接标题物资名称'
            elif previous and unit == previous['unit']:
                name, inherited = previous['name'], previous['row']
                fill = '同组连续规格续行，承接上一物资名称'
        elif side == 'targets' and name and not label and item_header and re.match(SUB_ITEM, name):
            sub_label = re.sub(SUB_ITEM, '', name)
            name, inherited = item_header['name'], item_header['row']
            fill = f'子项“{sub_label}”归入上级物资名称'
        if unit.replace(' ', '') in ('单位', '计量单位') and number(qty) is None:
            continue
        if unit in ('元', '万元', '%', '台班', '工日'):
            result['rejected_rows'].append(dict(file=table['file'], sheet=table['sheet'], row=index+1,
                                                name=name, reason='费用/人工/机械计价行，非物资数量'))
            continue
        if not name or not unit or number(qty) is None or number(qty) < 0:
            previous = None
            if any((name, unit, qty, value('spec'))):
                result['rejected_rows'].append(dict(file=table['file'], sheet=table['sheet'], row=index+1,
                                                    name=name, unit=unit, quantity=qty, reason='名称、单位或非负数字数量不完整，未提取'))
            continue
        set_level = heading_level(text(row[0])) if side == 'targets' else None
        set_row = set_level is not None and set_level <= SET_ROW_MAX_LEVEL
        if set_row:
            # 成套行与同级标题并列：先退出同级及更深的章节，成套行自身挂在上一级章节下
            while hierarchy and (hierarchy[-1]['level'] is None or hierarchy[-1]['level'] >= set_level):
                hierarchy.pop()
            group = hierarchy[-1]['name'] if hierarchy else table['sheet']
            numbered = {}
        evidence = dict(file=table['file'], sheet=table['sheet'], row=index+1, original_id=text(row[0]),
                        quantity_column=cols['qty'], quantity_basis=mapping['quantity_basis'])
        if inherited:
            evidence.update(name_source_row=inherited, name_fill_reason=fill)
        previous = dict(name=name, unit=unit, row=inherited or index+1, from_heading=from_heading)
        evidence['hierarchy'] = [dict(node) for node in hierarchy]
        record = {'序号': len(result[side])+1, '型号及规范': value('spec'), '层级': group,
                  '层级路径': [node['name'] for node in hierarchy], '_source': evidence}
        keys = ('Estimation_Name', 'Estimation_Unit', 'Estimation_Quantity', 'Estimation_Price') if side == 'sources' else ('设备材料名称', '单位', '数量', '价格')
        record.update(zip(keys, (name, unit, qty, value('price'))))
        if detail:
            record['Supply_Type'] = supply
            price_key = 'equipment_price' if supply.endswith('设') else 'material_price'
            record['Estimation_Price'] = value(price_key)
            evidence.update(price_column=cols[price_key], supply_column=cols['supply'], quantity_basis='安装工程明细原始设计用量；未根据汇总量倒算损耗')
            if not value(price_key):
                reason = '设备子项缺少独立单价，待确认是否已包含在主项中' if supply.endswith('设') else '原表材料单价为空，保留空值待确认'
                result['rejected_rows'].append(dict(file=table['file'], sheet=table['sheet'], row=index+1, name=name, quantity=qty, unit=unit, reason=reason, retained=not supply.endswith('设')))
                if supply.endswith('设'):
                    continue
                record['price_warning'] = reason
        elif side=='sources':
            record['Supply_Type'] = next((tag for title,tag in [('甲供设备','甲设'),('乙供设备','乙设'),('甲供主材','甲材'),('乙供主材','乙材')] if title in table['sheet']), '')
        if side=='targets':
            record['备注'] = value('notes')
            if sub_label:
                record['子项名称'] = sub_label
            reason = skills.call('folder_discovery', 'inventory_exclusion', record, blocked_section + '/' + group)
            if reason:
                result['rejected_rows'].append(dict(file=table['file'], sheet=table['sheet'], row=index+1, name=name, quantity=qty, unit=unit, reason=reason, status='按规则排除'))
                continue
        transformed = skills.call('data_cleaning', 'transform_record', record, side == 'sources')
        if not isinstance(transformed, dict) or any(k not in transformed for k in keys[:3]) or number(transformed.get(keys[2])) is None:
            raise ValueError('清洗技能破坏了必要物资字段')
        transformed.update({'序号': record['序号'], '_source': evidence})
        knowledge = ctx.knowledge()
        kind = knowledge.kind(transformed) if knowledge else None
        if kind:
            transformed['计量性质'] = kind['name']
        if side == 'sources' and knowledge:
            # 成套项之后列出的无单价子项是其组成件，明细表按非物资行跳过，这里随成套项保留
            components = knowledge.declared_components(rows, index, cols)
            if components:
                transformed['成套组成'] = components
                audit['component_rows'] = audit.get('component_rows', 0) + len(components)
        link_structure(transformed, item_label(row, name, side), numbered, hierarchy, set_records)
        result[side].append(transformed)
        audit['rows'] += 1
        if set_row:
            transformed['成套行'] = True
            set_records[transformed['序号']] = transformed
            hierarchy.append(dict(name=name, row=index+1, label=text(row[0]), level=set_level,
                                  basis='原表成套行（带单位数量）', set_seq=transformed['序号']))
            group = name


def link_structure(record, label, numbered, hierarchy, set_records):
    """记录原表结构关系：按编号嵌套（2.1 → 2.1.1）的上级物资，以及清册成套行下的组成件。"""
    parent = None
    if label:
        record['编号'] = label
        if '.' in label:
            parent = numbered.get(label.rsplit('.', 1)[0])
        numbered[label] = record
    set_node = next((node for node in reversed(hierarchy) if node.get('set_seq')), None)
    if set_node:
        owner = set_records[set_node['set_seq']]
        record['成套项'] = {'序号': owner['序号'], '名称': set_node['name']}
        parent = parent or owner
    if parent is not None:
        record['上级物资'] = parent['序号']
        parent.setdefault('下级物资', []).append(record['序号'])
