"""土石方资料清洗 · 岩土工程勘察报告：定位勘探点平面位置图和钻孔柱状图页，读出钻孔分层表（本机规则，不调用模型）。

钻孔柱状图每页并排若干个钻孔，每个钻孔一个表头（钻孔编号、孔口高程、X/Y 坐标、稳定水位深度）和一张分层表
（地层名称、层底高程、层底深度、分层厚度）。按“钻孔编号”表头把页面分栏，栏内按列标题的横向位置取数、按纵向位置配行。"""
import csv
import io
import re

COLUMN_TITLE = '钻孔柱状图'
PLAN_TITLE = '勘探点平面位置图'
CSV_HEADER = ['钻孔编号', '东坐标E', '北坐标N', '孔口高程', '稳定水位深度', '岩土层', '层顶深度', '层底深度', '层底高程']
NUMBER = re.compile(r'^-?\d+(?:\.\d+)?$')
HOLE_ID = re.compile(r'^[A-Za-z]{1,6}\d{1,4}[A-Za-z]?$')


def squeeze(text):
    return re.sub(r'\s+', '', text or '')


def classify_pages(pdf, texts=None):
    """按页面文字找出勘探点平面位置图页和钻孔柱状图页。texts 可传入已取好的各页文字。"""
    import earthwork_pdf
    plan, columns = [], []
    for index in range(len(pdf)):
        text = squeeze(texts[index] if texts else earthwork_pdf.page_text(pdf, index))
        if COLUMN_TITLE in text and '钻孔编号' in text:
            columns.append(index)
        elif PLAN_TITLE in text or (len(re.findall(r'[A-Z]{1,4}ZK\d+|ZK\d+', text)) >= 8 and '剖面图' not in text and COLUMN_TITLE not in text):
            plan.append(index)
    return {'plan': plan, 'columns': columns}


def _mid(t):
    left, bottom, right, top = t['box']
    return (left + right) / 2, (bottom + top) / 2


def _value_right_of(texts, label, pattern, dy=8, dx=220):
    """标签右侧同一行里最近的、符合 pattern 的文字。"""
    lx, ly = _mid(label)
    best = None
    for t in texts:
        x, y = _mid(t)
        if t is label or abs(y - ly) > dy or not 0 < x - lx < dx:
            continue
        m = pattern.search(squeeze(t['text']))
        if m and (best is None or x < best[0]):
            best = (x, m)
    return best[1] if best else None


def _column_x(texts, chars):
    """竖排列标题（如 层/底/深/度 各占一行）所在的横向位置：取这些字横向位置一致的一组。"""
    groups = {}
    for t in texts:
        text = squeeze(t['text'])
        if text in chars:
            x = _mid(t)[0]
            key = next((k for k in groups if abs(k - x) < 6), x)
            groups.setdefault(key, set()).add(text)
    full = [k for k, found in groups.items() if found >= set(chars)]
    return full


def parse_column_page(page):
    """一页钻孔柱状图 → [{'id', 'E', 'N', 'z', 'water', 'layers': [(名称, 层顶深度, 层底深度, 层底高程)], 'warnings': [...]}]。"""
    texts = page['texts']
    anchors = sorted((t for t in texts if squeeze(t['text']) == '钻孔编号'), key=lambda t: _mid(t)[0])
    if not anchors:
        return []
    width = page['size'][0]
    # 每个钻孔占页面的一栏：栏宽按相邻“钻孔编号”表头的间距定，单孔页取整页
    pitch = min((_mid(b)[0] - _mid(a)[0] for a, b in zip(anchors, anchors[1:])), default=width)
    titles = sorted((_mid(t)[0] for t in texts if squeeze(t['text']) == COLUMN_TITLE))
    holes = []
    for n, anchor in enumerate(anchors):
        ax = _mid(anchor)[0]
        centre = min(titles, key=lambda x: abs(x - ax)) if titles else ax
        left, right = centre - pitch / 2, centre + pitch / 2
        block = [t for t in texts if left <= _mid(t)[0] < right]
        warnings = []
        found = _value_right_of(block, anchor, re.compile(r'^[A-Za-z]{1,6}\d{1,4}[A-Za-z]?$'))
        if not found:
            continue
        hole = {'id': found.group(0), 'warnings': warnings}

        def labelled(name, pattern):
            label = next((t for t in block if squeeze(t['text']).startswith(name)), None)
            return _value_right_of(block, label, pattern) if label else None
        collar = labelled('孔口高程', re.compile(r'^\d{1,5}(?:\.\d+)?$'))
        water = labelled('稳定水位深度', re.compile(r'^\d{1,3}(?:\.\d+)?$'))
        # 表头“坐标”栏写作 X=…、Y=…：数值较大的是北坐标，另一个是东坐标（多数报告的 X 为北坐标，也有反过来标的）
        coords = sorted(float(m.group(1)) for t in block for m in [re.match(r'^[XYxy]=(\d+(?:\.\d+)?)$', squeeze(t['text']))] if m)
        if len(coords) != 2 or not collar:
            warnings.append('表头缺少坐标或孔口高程')
            continue
        hole.update(E=coords[0], N=coords[1], z=float(collar.group(0)), water=water.group(0) if water else '')
        # 分层表：列标题为竖排单字
        name_x = _column_x(block, '地层名称')
        depth_x = _column_x(block, '层底深度')
        level_x = _column_x(block, '层底高程')
        header_y = min((_mid(t)[1] for t in block if squeeze(t['text']) in ('(m)', '（m）')), default=None)
        if not name_x or not depth_x or header_y is None:
            warnings.append('未找到分层表的列标题')
            holes.append(dict(hole, layers=[]))
            continue

        def column(xs, pattern, tol=16):
            rows = []
            for t in block:
                x, y = _mid(t)
                text = squeeze(t['text'])
                if y < header_y and any(abs(x - cx) < tol for cx in xs) and pattern.match(text):
                    rows.append((y, text))
            return [text for _, text in sorted(rows, reverse=True)]
        names = column(name_x, re.compile(r'^[^\d\s().]{2,12}$'), 24)
        depths = [float(v) for v in column(depth_x, NUMBER)]
        levels = [float(v) for v in column(level_x, NUMBER)] if level_x else []
        if len(names) != len(depths):
            warnings.append(f'地层名称 {len(names)} 行与层底深度 {len(depths)} 行不一致')
        layers, top = [], 0.0
        for i, (name, depth) in enumerate(zip(names, depths)):
            # 层底高程统一按孔口高程减层底深度计算；报告所列数值只用于核对（个别相差 0.01 属取整）
            level = round(hole['z'] - depth, 2)
            if i < len(levels) and abs(levels[i] - level) > 0.06:
                warnings.append(f'{name} 报告所列层底高程 {levels[i]:g} 与孔口高程减层底深度 {level:g} 不符')
            if layers and layers[-1][0] == name:
                layers[-1] = (name, layers[-1][1], depth, level)   # 相邻同名亚层合并
            else:
                layers.append((name, top, depth, level))
            top = depth
        holes.append(dict(hole, layers=layers))
    return holes


def read_columns(pdf, pages, progress=None):
    import earthwork_pdf
    holes, seen = [], {}
    for n, index in enumerate(pages):
        page = earthwork_pdf.extract_page(pdf, index, layers=())   # 只要文字：不取任何图层的路径
        for hole in parse_column_page(page):
            hole['page'] = index + 1
            if hole['id'] in seen:
                # 深孔的柱状图分页续接：后页的分层接在前页之后，跨页的同名层合并
                first = seen[hole['id']]
                for name, top, bottom, level in hole['layers']:
                    last = first['layers'][-1] if first['layers'] else None
                    if last and bottom <= last[2] + 1e-6:
                        continue
                    if last and last[0] == name:
                        first['layers'][-1] = (name, last[1], bottom, level)
                    else:
                        first['layers'].append((name, last[2] if last else 0.0, bottom, level))
                continue
            seen[hole['id']] = hole
            holes.append(hole)
        if progress:
            progress(n + 1, len(pages))
    return holes


def _num(value):
    return '0' if not value else str(round(float(value), 3))


def holes_csv(holes, east_prefix=''):
    """钻孔分层数据.csv 的内容。east_prefix 为东坐标带号（如 34），与平面图坐标一致时加在东坐标前。"""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(CSV_HEADER)
    for hole in holes:
        for name, top, bottom, level in hole['layers']:
            writer.writerow([hole['id'], f'{east_prefix}{hole["E"]:.3f}', f'{hole["N"]:.3f}', _num(hole['z']), hole['water'], name,
                             _num(top), _num(bottom), _num(level)])
    return out.getvalue()


def parse_holes_csv(text):
    """读取钻孔分层数据.csv（列名同 CSV_HEADER），返回与 read_columns 相同结构的钻孔列表。"""
    rows = list(csv.reader(io.StringIO(text.lstrip('﻿'))))
    if not rows:
        raise ValueError('钻孔分层数据为空')
    header = [squeeze(c) for c in rows[0]]
    missing = [c for c in CSV_HEADER if c not in header]
    if missing:
        raise ValueError('钻孔分层数据缺少列：' + '、'.join(missing))
    col = {c: header.index(c) for c in CSV_HEADER}
    holes, by_id = [], {}
    for line, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        try:
            key = row[col['钻孔编号']].strip()
            hole = by_id.get(key)
            if hole is None:
                hole = by_id[key] = {'id': key, 'E': float(row[col['东坐标E']]), 'N': float(row[col['北坐标N']]), 'z': float(row[col['孔口高程']]),
                                      'water': row[col['稳定水位深度']].strip(), 'layers': [], 'warnings': []}
                holes.append(hole)
            hole['layers'].append((row[col['岩土层']].strip(), float(row[col['层顶深度']]), float(row[col['层底深度']]), float(row[col['层底高程']])))
        except (ValueError, IndexError):
            raise ValueError(f'钻孔分层数据第 {line} 行格式有误')
    return holes


def check_holes(holes):
    """钻孔分层的规则校验：层序连续、层底高程自洽。返回问题列表。"""
    problems = []
    for hole in holes:
        top = 0.0
        for name, start, bottom, level in hole['layers']:
            if abs(start - top) > 0.011:
                problems.append(f'{hole["id"]} {name} 层顶深度 {start:g} 与上一层层底 {top:g} 不衔接')
            if bottom <= start:
                problems.append(f'{hole["id"]} {name} 层底深度 {bottom:g} 不大于层顶深度 {start:g}')
            if abs(hole['z'] - bottom - level) > 0.06:
                problems.append(f'{hole["id"]} {name} 层底高程 {level:g} 与孔口高程减层底深度相差 {abs(hole["z"] - bottom - level):.2f} m')
            top = bottom
        if not hole['layers']:
            problems.append(f'{hole["id"]} 没有读到分层')
        problems.extend(f'{hole["id"]} {w}' for w in hole.get('warnings') or [])
    return problems
