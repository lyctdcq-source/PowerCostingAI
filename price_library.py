"""信息价导入：把一个季度的信息价工作簿解析成价格库条目。

一份文件常把多张分表拼进同一个工作表：序号与“一/二”层级多次重启，块与块之间没有标题行，
而同名同型号在不同块里价格可能差几倍，所以解析时按层级重启切块，每块的标签由人工补齐。
单位统一到规范名称，备注里的折算系数单独取出，名称型号单位相同而价格不同的整理成重复清单交人工定。
税口径、税率与构成系数不在表里，由导入时逐份声明，随条目一并保存，供价格评审换算时引用。
"""
import math
import re
import unicodedata
from pathlib import Path

import openpyxl

_DIGITS = '零一二三四五六七八九'
# 只写需要改写的单位；表里的中文计量单位（台、套、面…）原样保留
UNIT_ALIASES = {'t': '吨', '公吨': '吨', 'km': '千米', '公里': '千米', 'm': '米', '延米': '米', '米/根': '米',
                'm2': '平方米', 'm²': '平方米', '平米': '平方米', 'm3': '立方米', 'm³': '立方米', '立方': '立方米',
                'kg': '公斤', '千克': '公斤', '个/只': '只', 'pcs': '只'}
UNIT_MAX = 6                      # 超过这个字数的“单位”按备注错列处理
# 归一后仍不在这里的单位，多半是备注或名称串进了单位列，标出来让人工核对；不拦截导入
KNOWN_UNITS = {'吨', '千米', '米', '平方米', '立方米', '公斤', '台', '套', '件', '个', '只', '根', '面', '块',
               '支', '组', '座', '付', '副', '条', '片', '段', '间隔', '串', '粒', '三相米', '项', '部', '台架'}
_FACTOR_HEAD = re.compile(r'折算系数[：:]?\s*(.+)')
_FACTOR_ITEM = re.compile(r'([一-龥A-Za-z]+?)\s*(\d+(?:\.\d+)?)')
_PERIOD_HINT = re.compile(r'(\d{4,8})\D*$')


def text(value):
    return '' if value is None else str(value).strip()


def number(value):
    """表里的价格常带千分位或单位后缀，取不到数值返回 None。"""
    raw = text(value).replace(',', '').replace('，', '')
    try:
        return float(raw)
    except ValueError:
        return None


def money(value):
    """金额文本：千分位，不用科学计数法，整数不带小数。"""
    amount = number(value)
    if amount is None:
        return ''
    return f'{int(amount):,}' if float(amount).is_integer() else f'{amount:,.2f}'


def normalize(value):
    """全角转半角、去掉空白、乘号统一，用于显示与比较前的归一。"""
    cleaned = unicodedata.normalize('NFKC', text(value))
    cleaned = re.sub(r'\s+', '', cleaned)
    return re.sub(r'[xX*×]', '×', cleaned)


def match_key(value):
    """匹配用的键：归一后不区分大小写。"""
    return normalize(value).upper()


def item_key(item):
    """比价与去重用的键：名称、规格型号归一后加规范单位。"""
    unit = item.get('unit') if isinstance(item, dict) else ''
    return f'{match_key((item or {}).get("name"))}|{match_key((item or {}).get("spec"))}|{canonical_unit(unit)[0]}'


def numeral(value):
    """“一”“十七”“（三）”转成序号；不是中文序号返回 None。"""
    stripped = re.sub(r'[（）()\s、.，,]', '', text(value))
    if not stripped or any(char not in '十' + _DIGITS for char in stripped):
        return None
    if stripped == '十':
        return 10
    if '十' in stripped:
        tens, _, ones = stripped.partition('十')
        return (_DIGITS.index(tens) if tens else 1) * 10 + (_DIGITS.index(ones) if ones else 0)
    return _DIGITS.index(stripped)


def canonical_unit(value):
    """单位归一：t→吨、km→千米、m²→平方米；备注错列到单位列的返回空单位和原因。"""
    raw = normalize(value)
    if not raw:
        return '', '原表单位为空'
    if len(raw) > UNIT_MAX or re.search(r'[，。；、:：]', raw):
        return '', '单位列内容像备注，未作为单位'
    return UNIT_ALIASES.get(raw, UNIT_ALIASES.get(raw.lower(), raw)), ''


def parse_factors(notes):
    """备注里的折算系数：“折算系数： 普通1 ZRC1.01 ZRB1.03”→ 标号与系数。"""
    head = _FACTOR_HEAD.search(text(notes))
    if not head:
        return []
    out = []
    for label, factor in _FACTOR_ITEM.findall(head.group(1)):
        value = float(factor)
        if 0 < value < 100:
            out.append(dict(label=label, factor=value))
    return out


def period_hint(filename):
    """文件名尾部的数字当作期次提示（如“…信息价1031”→“1031”）；期次仍以导入时填写的为准。"""
    found = _PERIOD_HINT.search(Path(filename).stem)
    return found.group(1) if found else ''


def _header(rows):
    """表头行：同时出现“名称”和“单价”的那一行；返回行号与列序。"""
    for index, row in enumerate(rows[:10]):
        cells = [normalize(cell) for cell in row]
        if not any('名称' in cell for cell in cells) or not any('单价' in cell or '价格' in cell for cell in cells):
            continue
        columns = {}
        for position, cell in enumerate(cells):
            for key, words in (('seq', ('序号',)), ('name', ('名称',)), ('spec', ('规格', '型号')),
                               ('unit', ('单位',)), ('price', ('单价', '价格')), ('notes', ('备注', '说明'))):
                if key not in columns and any(word in cell for word in words):
                    columns[key] = position
        if {'name', 'price'} <= set(columns):
            return dict(row=index, columns=columns)
    return None


def _block(result, sheet, row):
    block = dict(id=len(result['blocks']) + 1, sheet=sheet, first_row=row, last_row=row,
                 label='', label_auto=True, categories=[], count=0)
    result['blocks'].append(block)
    return block


def auto_label(block):
    """按一级类目给分表块先填一个标签。类目名常常分不出主配网或电压等级，所以只是草稿，待人工确认。"""
    names = [name for name in block['categories'] if name]
    if not names:
        return f'第{block["first_row"]}–{block["last_row"]}行'
    head = '·'.join(names[:2])
    return f'{head}等{len(names)}类' if len(names) > 2 else head


def _parse_sheet(sheet, source, meta, result):
    rows = list(sheet.iter_rows(values_only=True))
    head = _header(rows)
    if not head:
        result['warnings'].append(f'{sheet.title}：未找到同时含“名称”和“单价”的表头，整表未导入')
        return
    columns = head['columns']
    block, category, subcategory, previous, titled = None, '', '', None, False
    for index in range(head['row'] + 1, len(rows)):
        cells = [text(cell) for cell in rows[index]]
        if not any(cells):
            continue
        def column(key):
            position = columns.get(key)
            return cells[position] if position is not None and position < len(cells) else ''
        seq, name, spec = column('seq'), column('name'), column('spec')
        unit, notes, price = column('unit'), column('notes'), number(column('price'))
        level_no, row_no = numeral(seq), index + 1
        if price is None and not unit:
            if name and level_no is not None:                     # 分类标题行
                level = 2 if re.match(r'^[（(]', seq) else 1
                if level == 1:
                    if level_no == 1 and block and block['count']:
                        block = None                              # 层级从“一”重启：这里是下一张分表
                    block = block or _block(result, sheet.title, row_no)
                    category, subcategory = name, ''
                    block['categories'].append(name)
                else:
                    block = block or _block(result, sheet.title, row_no)
                    subcategory = name
                titled = True
                continue
            if name and number(seq) is not None and previous is not None:
                previous['notes'] = (previous['notes'] + ' ' + name).strip()   # 上一条的备注折行到了下一行
                result['warnings'].append(f'{sheet.title} 第{row_no}行：内容并入上一条的备注（原表折行）')
                continue
            if name and not seq:
                block = block or _block(result, sheet.title, row_no)
                subcategory, titled = name, True                  # 没编号的小类标题
                continue
        if not name:
            result['rejected'].append(dict(sheet=sheet.title, row=row_no, name=name, unit=unit,
                                           price=column('price'), reason='没有名称，未导入'))
            continue
        if price is None or price <= 0:
            result['rejected'].append(dict(sheet=sheet.title, row=row_no, name=name, unit=unit,
                                           price=column('price'), reason='原表没有有效单价，未导入'))
            continue
        block = block or _block(result, sheet.title, row_no)
        canonical, unit_note = canonical_unit(unit)
        if unit_note and unit:
            result['warnings'].append(f'{sheet.title} 第{row_no}行：{unit_note}（“{unit[:20]}”）')
        item = dict(code=f'B{block["id"]:02d}R{row_no:05d}', name=name, spec=spec,
                    unit=canonical, unit_raw=unit, price=price, notes=notes, factors=parse_factors(notes),
                    block=block['id'], block_label=block['label'], category=category, subcategory=subcategory,
                    period=result['period'], region=meta.get('region', ''), tax=meta.get('tax', '含税'),
                    tax_rate=meta.get('tax_rate', 13.0), composition=meta.get('composition', 1.0),
                    basis=meta.get('basis', ''), source=source.name, sheet=sheet.title, row=row_no, page=row_no,
                    approved=False, inferred=False)
        item['key'] = item_key(item)
        if not canonical:
            item['price_warning'] = '单位缺失或无法识别，比价前需人工补单位'
        elif canonical not in KNOWN_UNITS:
            item['price_warning'] = f'单位“{canonical}”不是常用计量单位，需人工核对'
            result['warnings'].append(f'{sheet.title} 第{row_no}行：单位“{canonical}”不是常用计量单位，需人工核对')
        result['items'].append(item)
        block.update(last_row=row_no, count=block['count'] + 1)
        # 分类标题后序号从 1 开始是正常的；没有标题却重新从 1 开始，说明这里可能是丢了标题的分表边界
        if number(seq) == 1 and not titled and block['count'] > 4:
            result['warnings'].append(f'{sheet.title} 第{row_no}行：序号重新从 1 开始但前面没有分类标题，可能是分表边界，请在打标签时核对')
        previous, titled = item, False


def duplicates(items):
    """名称、型号、单位相同而价格不同的条目：比价时不能自动取第一条，必须人工指定。"""
    groups = {}
    for item in items:
        groups.setdefault(item['key'], []).append(item)
    out = []
    for key, group in groups.items():
        prices = sorted({round(float(x['price']), 2) for x in group})
        if len(group) < 2 or len(prices) < 2:
            continue
        out.append(dict(key=key, name=group[0]['name'], spec=group[0]['spec'], unit=group[0]['unit'],
                        count=len(group), ratio=round(prices[-1] / prices[0], 2) if prices[0] > 0 else None,
                        entries=[dict(code=x['code'], block=x['block'], row=x['row'], price=x['price'],
                                      notes=x['notes'][:60]) for x in group]))
    out.sort(key=lambda record: -(record['ratio'] or 0))
    return out


def parse(path, meta=None):
    """解析信息价工作簿：返回条目、分块、重复键清单和未导入的行。

    meta 为导入时声明的口径：period 期次、region 区域、tax 含税/除税、tax_rate 税率、
    composition 构成系数（运杂与采购保管）、basis 交货口径。表里没有这些信息，不能推测。
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError(f'信息价文件不存在：{path}')
    meta = dict(meta or {})
    result = dict(file=source.name, path=str(source), period=meta.get('period') or period_hint(source.name),
                  tax=meta.get('tax', '含税'), tax_rate=meta.get('tax_rate', 13.0),
                  composition=meta.get('composition', 1.0), region=meta.get('region', ''),
                  basis=meta.get('basis', ''), blocks=[], items=[], duplicates=[], rejected=[], warnings=[])
    book = openpyxl.load_workbook(source, data_only=True, read_only=True)
    try:
        for sheet in book.worksheets:
            _parse_sheet(sheet, source, meta, result)
    finally:
        book.close()
    for block in result['blocks']:
        block['suggestion'] = ' / '.join(block['categories'][:3])
        block['label'] = auto_label(block)
    for item in result['items']:
        item['block_label'] = next((b['label'] for b in result['blocks'] if b['id'] == item['block']), '')
    result['duplicates'] = duplicates(result['items'])
    if not result['items']:
        raise ValueError('未从该工作簿解析出任何带单价的信息价条目，请确认选的是信息价表')
    return result


def label_blocks(items, blocks, labels):
    """人工确认分表块标签：labels 为 {块号: 标签}，写回块与块内条目，并去掉“按类目暂填”的标记。

    返回仍未确认的块。导入时按类目填的是草稿，不算确认：类目名分不出主配网与电压等级，
    而同名同型号正是靠块来区分的，这一步不能跳过。
    """
    mapping = {int(key): str(value).strip() for key, value in dict(labels or {}).items() if str(value).strip()}
    for block in blocks:
        if block['id'] in mapping:
            block.update(label=mapping[block['id']], label_auto=False)
    for item in items:
        if item.get('block') in mapping:
            item['block_label'] = mapping[item['block']]
    return [block for block in blocks if not block['label'] or block.get('label_auto')]


# ── 召回：词面打分，供模型或人工在候选里判定 ──────────────────────────────
# 七千多条信息价不能整本塞进提示词，先由程序召回少量候选。型号是强标识符（SSZ-63000/110、
# JL/LB1A-70/10），所以字母数字词加权；太常见的词（“电缆”“户外”）不参与召回。
_WORD = re.compile(r'[A-Za-z0-9]+(?:[/×\-\.][A-Za-z0-9]+)*')
RECALL_MAX_DF = 0.08          # 出现在 8% 以上条目里的词太泛，跳过
RECALL_MIN_DF = 30            # 但库很小时按比例会把所有词都滤掉，至少留到这个出现次数
RECALL_LIMIT = 8              # 每条造价物资交给模型的候选数
MODEL_WORD_WEIGHT = 2.5       # 型号类词相对中文二元词的权重


def tokens(value):
    """型号类词及其片段 + 中文二元词。

    这里不删空格：造价常写成“JL/LB20A 300/40”，删了空格就切不出“300/40”这个截面词。
    型号再按 / - × . 拆成片段，让“SSZ20-50000/110”和“SSZ-50000/110”能靠“50000”“110”互相召回。
    没有分词器，中文按相邻两字切，够用来召回。
    """
    flat = re.sub(r'[xX*×]', '×', unicodedata.normalize('NFKC', text(value))).upper()
    out = []
    for word in _WORD.findall(flat):
        if len(word) >= 2:
            out.append(word)
        for part in re.split(r'[/×\-\.]', word):
            if len(part) >= 2 and part != word:
                out.append(part)
    chinese = re.sub(r'[^\u4e00-\u9fa5]', '', flat)
    out += [chinese[i:i + 2] for i in range(len(chinese) - 1)]
    return out


def item_tokens(item):
    return set(tokens(item.get('name')) + tokens(item.get('spec')))


def build_recall_index(prices):
    postings = {}
    for position, entry in enumerate(prices):
        for word in item_tokens(entry):
            postings.setdefault(word, []).append(position)
    return postings


def recall(item, prices, postings, limit=RECALL_LIMIT):
    """按词面打分召回候选；单位可换算的排前面。返回 [(条目, 分数)]，分数已归一到 0~100。"""
    if not prices:
        return []
    scores, total = {}, len(prices)
    for word in item_tokens(item):
        hits = postings.get(word)
        if not hits or len(hits) > max(total * RECALL_MAX_DF, RECALL_MIN_DF):
            continue
        weight = (MODEL_WORD_WEIGHT if _WORD.fullmatch(word) else 1.0) / math.log(2 + len(hits))
        for position in hits:
            scores[position] = scores.get(position, 0.0) + weight
    if not scores:
        return []
    best = max(scores.values())
    ranked = []
    for position, score in scores.items():
        entry = prices[position]
        value = score / best * 100
        if unit_factor(entry.get('unit'), item.get('unit')) is None:
            value *= 0.6                      # 单位不可换算的留着让人看，但排后面
        ranked.append((entry, round(value, 1)))
    ranked.sort(key=lambda pair: -pair[1])
    return ranked[:limit]


def candidate_brief(entry, score=None):
    """交给模型或界面的候选摘要：只给判定需要的字段。"""
    brief = dict(code=entry.get('code'), name=entry.get('name'), spec=entry.get('spec'),
                 unit=entry.get('unit'), price=entry.get('price'), block=entry.get('block'),
                 block_label=entry.get('block_label'), notes=(entry.get('notes') or '')[:80],
                 category=entry.get('category'), row=entry.get('row'))
    if score is not None:
        brief['score'] = score
    return brief


# ── 比价：口径换算与本机匹配 ──────────────────────────────────────────────
# 量纲相同才自动换算单位。吨与米（导线按线密度）、吨与根（按单重）这类换算要查参数，
# 数量评审里有联网与模型的换算能力，价格侧不自动做，列为不可比交人工。
LENGTH, MASS, AREA, VOLUME = '长度', '质量', '面积', '体积'
UNIT_SCALE = {'米': (LENGTH, 1.0), '千米': (LENGTH, 1000.0), '三相米': (LENGTH, 1.0),
              '公斤': (MASS, 1.0), '吨': (MASS, 1000.0),
              '平方米': (AREA, 1.0), '立方米': (VOLUME, 1.0)}
MIN_LOOSE_NAME = 4            # 信息价没有型号时，名称至少这么长才允许按“名称出现在造价文本里”对应
# 表三甲的单价按供货方式分口径（在两本估算书上逐条核对过）：
# 甲设＝甲供设备汇总表的“市场价”，设备购置费明细表记为“甲供设备费含税”，不含运杂费（运杂费单列）；
# 乙设＝“乙供设备费不含税”，税金单列；甲材乙材＝主材价差表的“预算价（不含税）”，与市场价的差额走价差表。
SUPPLY_TAX = {'甲设': '含税', '乙设': '除税', '甲材': '除税', '乙材': '除税'}
MATERIAL_SUPPLIES = ('甲材', '乙材')
MATERIAL_PENDING = '该条不在主材价差表中，表三甲单价是否已是市场价待确认'
CALIBER_FIELDS = ('period', 'region', 'tax', 'basis')
BANDS_OK = ('一致', '基本一致')


def unit_factor(info_unit, cost_unit):
    """一个信息价单位等于几个造价单位；量纲不同或单位缺失返回 None。"""
    left, right = canonical_unit(info_unit)[0], canonical_unit(cost_unit)[0]
    if not left or not right:
        return None
    if left == right:
        return 1.0
    kind_l, scale_l = UNIT_SCALE.get(left, (left, 1.0))
    kind_r, scale_r = UNIT_SCALE.get(right, (right, 1.0))
    return scale_l / scale_r if kind_l == kind_r else None


def comparable_price(entry, cost_unit, cost_tax='除税'):
    """信息价换算到与造价同口径：含税转除税、乘构成系数、按单位折算。返回（价格, 换算过程, 不可比原因）。"""
    price = number(entry.get('price'))
    if price is None or price <= 0:
        return None, '', '信息价条目没有有效单价'
    factor = unit_factor(entry.get('unit'), cost_unit)
    if factor is None:
        return None, '', ('单位无法换算（信息价%s → 造价%s），需人工确认换算参数'
                          % (canonical_unit(entry.get('unit'))[0] or '空', canonical_unit(cost_unit)[0] or '空'))
    steps = [f'信息价 {money(price)} 元/{entry.get("unit")}']
    rate = number(entry.get('tax_rate')) or 0
    if str(entry.get('tax') or '') == '含税' and rate > 0 and cost_tax == '除税':
        price /= 1 + rate / 100
        steps.append(f'÷(1+{rate:g}%) 得除税价 {money(round(price, 2))}')
    composition = number(entry.get('composition')) or 1
    if composition != 1:
        price *= composition
        steps.append(f'×构成系数 {composition:g} = {money(round(price, 2))}')
    if factor != 1:
        price /= factor
        steps.append(f'÷{factor:g}（{canonical_unit(entry.get("unit"))[0]}→{canonical_unit(cost_unit)[0]}）'
                     f'= {money(round(price, 2))}')
    return price, '；'.join(steps), ''


def _cost_text(item):
    return match_key(f'{item.get("name") or ""}{item.get("spec") or ""}')


def candidates(item, prices, index=None):
    """为一条造价物资找信息价候选。

    先按名称型号单位的键精确对应，再按名称与型号出现在造价文本里对应。
    第三个返回值是“名称对上但型号对不上”的条目：不能当作同一物资，但正是人工或模型要判定的地方。
    """
    index = index if index is not None else {}
    exact = index.get(item_key(item)) or []
    if exact:
        return list(exact), '名称、型号与单位完全一致', []
    text = _cost_text(item)
    loose, near = [], []
    for entry in prices:
        name, spec = match_key(entry.get('name')), match_key(entry.get('spec'))
        if not name or name not in text:
            continue
        if spec:
            loose.append((len(spec), entry)) if spec in text else near.append(entry)
        elif len(name) >= MIN_LOOSE_NAME:
            loose.append((0, entry))
    if not loose:
        return [], '', near
    best = max(score for score, _ in loose)
    return [entry for score, entry in loose if score == best], (
        '信息价名称与型号都出现在造价名称规格中' if best else '信息价名称出现在造价名称规格中，该条无型号'), near


def build_index(prices):
    index = {}
    for entry in prices:
        index.setdefault(entry.get('key') or item_key(entry), []).append(entry)
    return index


CALIBER_NAMES = {'period': '期次', 'region': '区域', 'tax': '税口径', 'basis': '交货口径'}


def _caliber_conflict(item, entry):
    """单独导入的待核对价格要逐项对齐口径；造价清洗结果不带这些字段（loose），按信息价口径参考比较。"""
    stated = [k for k in CALIBER_FIELDS if item.get(k)] if item.get('loose') else list(CALIBER_FIELDS)
    for key in stated:
        if not item.get(key) or not entry.get(key) or item[key] != entry[key]:
            return (f'{CALIBER_NAMES[key]}不齐，禁止直接比较'
                    f'（造价 {item.get(key) or "未注明"} / 信息价 {entry.get(key) or "未注明"}）')
    return ''


def _settle(record, item, entry, cost_price, cost_tax, basis, threshold, classify, basis_note):
    """对着一条信息价算偏差率并定档，证据链写明换算过程、口径与出处。"""
    conflict = _caliber_conflict(item, entry)
    if conflict:
        record.update(status='口径不可比', reason=conflict)
        return record
    if entry.get('inferred'):
        record.update(status='口径不可比', reason='推算价仅供参考，不参与正式比较')
        return record
    value, steps, why = comparable_price(entry, item.get('unit'), cost_tax)
    if value is None:
        record.update(status='口径不可比', reason=why)
        return record
    delta = (cost_price - value) / value * 100
    band = classify(delta, threshold)
    source = f'{entry.get("source") or "信息价"} 第{entry.get("row") or entry.get("page")}行'
    if entry.get('block'):
        source += f'（块{entry["block"]}{entry.get("block_label") or ""}）'
    caliber = (f'造价未注明期次、区域等口径，按信息价 {entry.get("period") or ""} '
               f'{entry.get("region") or ""} 参考比较' if item.get('loose') else
               f'口径同为 {entry.get("period") or ""} {entry.get("region") or ""} '
               f'{entry.get("tax") or ""} {entry.get("basis") or ""}')
    record.update(reference=entry, delta=delta, band=band, conversion=steps,
                  direction='' if band in BANDS_OK else ('偏高' if delta > 0 else '偏低'),
                  status='已比较',
                  reason=f'{basis}；{steps}；偏差率 ({money(cost_price)} − {money(round(value, 2))}) '
                         f'÷ {money(round(value, 2))} × 100% '
                         f'= {delta:.2f}%；造价单价按{cost_tax}价'
                         + (f'；{basis_note}' if basis_note else '')
                         + f'；{caliber}；出处 {source}')
    return record


def compare(items, prices, options=None, classify=None):
    """逐条比价：换算口径后算偏差率并定档；不能比的给出具体原因，不猜、不取第一条。"""
    options = dict(options or {})
    threshold = number(options.get('threshold')) or 10
    forced_tax = options.get('cost_tax') or ''      # 空＝按供货方式分口径，非空＝用户统一指定
    exclusions = {str(k): v for k, v in (options.get('exclusions') or {}).items()}
    picks = {str(k): v for k, v in (options.get('picks') or {}).items()}
    by_code = {e.get('code'): e for e in prices if e.get('code')}
    classify = classify or (lambda delta, limit: ('一致' if abs(delta) <= 2 else '基本一致' if abs(delta) <= 5
                                                  else '偏差' if abs(delta) <= limit else '显著偏差'))
    index = build_index(prices)
    out = []
    for item in items:
        record = dict(item=item, reference=None, candidates=[], delta=None, band='不可比',
                      direction='', conversion='', reason='', status='')
        excluded = exclusions.get(str(item.get('source_id') or ''))
        supply = str(item.get('supply') or '')
        cost_tax = forced_tax or SUPPLY_TAX.get(supply, '除税')
        market = number(item.get('market_price'))
        material = supply in MATERIAL_SUPPLIES
        listed = number(item.get('price'))
        if material and market is not None:
            # 表三甲装材单价是基准期预算价，价差单列；比价要用价差表的市场价
            cost_price, basis_note = market, ('造价侧取主材价差表的市场价（不含税），'
                                              f'表三甲单价 {money(listed)} 为基准期预算价')
        else:
            cost_price, basis_note = listed, (MATERIAL_PENDING if material else '')
        record.update(cost_tax=cost_tax, cost_price=cost_price, basis_note=basis_note)
        if excluded:
            record.update(status='不参与比价', reason=excluded)
        elif not prices:
            record.update(status='价格库为空', reason='价格库尚未载入信息价')
        elif cost_price is None or cost_price <= 0:
            record.update(status='造价无单价', reason='造价该条没有单价，不参与比价')
        elif picks.get(str(item.get('source_id') or '')):
            pick = picks[str(item.get('source_id'))]
            record['pick'] = pick
            entry = by_code.get(pick.get('code'))
            if entry is None:
                record.update(status='指定条目已失效',
                              reason=f'原先指定的参考条目 {pick.get("code")} 不在当前价格库版本里，请重新指定')
            else:
                who = pick.get('by') or '人工'
                basis = f'{who}指定参考条目' + (f'（置信度 {pick["confidence"]:g}）' if pick.get('confidence') else '')
                if pick.get('reason'):
                    basis += f'：{pick["reason"]}'
                _settle(record, item, entry, cost_price, cost_tax, basis, threshold, classify, basis_note)
        else:
            found, basis, near = candidates(item, prices, index)
            priced = []
            for entry in found:
                conflict = _caliber_conflict(item, entry)
                if conflict:
                    priced.append((None, '', conflict, entry))
                    continue
                if entry.get('inferred'):
                    priced.append((None, '', '推算价仅供参考，不参与正式比较', entry))
                    continue
                value, steps, why = comparable_price(entry, item.get('unit'), cost_tax)
                priced.append((value, steps, why, entry))
            usable = [(v, s, e) for v, s, _, e in priced if v is not None]
            record['candidates'] = [dict(code=e.get('code'), name=e.get('name'), spec=e.get('spec'),
                                         unit=e.get('unit'), price=e.get('price'), block=e.get('block'),
                                         block_label=e.get('block_label'), notes=(e.get('notes') or '')[:60],
                                         comparable=v, reason=w) for v, _, w, e in priced]
            distinct = {round(v, 2) for v, _, _ in usable}
            if not found and near:
                shown = '、'.join(f'{e.get("spec")}（{money(e.get("price"))} 元/{e.get("unit")}）' for e in near[:3])
                record.update(status='型号不同', candidates=[dict(code=e.get('code'), name=e.get('name'),
                                                               spec=e.get('spec'), unit=e.get('unit'),
                                                               price=e.get('price'), block=e.get('block'),
                                                               block_label=e.get('block_label'),
                                                               notes=(e.get('notes') or '')[:60]) for e in near[:8]],
                              reason=f'价格库有同名条目 {len(near)} 条但型号对不上（{shown}'
                                     f'{" 等" if len(near) > 3 else ""}），是否同一物资需人工或模型判定')
            elif not found:
                record.update(status='无对应条目', reason='价格库没有名称型号对应的信息价条目，需人工指定参考条目或补录')
            elif not usable:
                record.update(status='口径不可比', reason='；'.join(sorted({w for v, _, w, _ in priced if w})))
            elif len(distinct) > 1:
                blocks = '、'.join(sorted({f'块{e.get("block")}{e.get("block_label") or ""}' for _, _, e in usable}))
                record.update(status='候选多价', reason=f'有 {len(usable)} 条可能对应且换算后价格不同（{blocks}），'
                                                       '不自动取值，需人工指定参考条目')
            else:
                _settle(record, item, usable[0][2], cost_price, cost_tax, basis, threshold, classify, basis_note)
        out.append(record)
    return out
