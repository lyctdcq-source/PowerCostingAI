"""Local tabular cleaning; retain file/sheet/row evidence, never overwrite inputs."""
import json
import math
import re
from pathlib import Path


def text(v):
    return '' if v is None else str(v).strip()


def number(v):
    try:
        value = float(str(v).replace(',', '').strip())
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def sheets(path):
    for title, rows, _ in sheet_tables(path):
        yield title, rows


def sheet_tables(path):
    """逐表返回 (表名, 行, 合并区域)。合并单元格的值填入区域内每一格；合并区域按 1 起的
    (首行, 末行, 首列, 末列) 给出，供提取时区分“合并单元格续行”与真实重复行。"""
    if path.suffix.lower() == '.xls':
        import xlrd
        book = xlrd.open_workbook(str(path), formatting_info=True)
        for sheet in book.sheets():
            rows = [sheet.row_values(i) for i in range(sheet.nrows)]
            for r0, r1, c0, c1 in sheet.merged_cells:
                for r in range(r0, r1):
                    for c in range(c0, c1):
                        rows[r][c] = rows[r0][c0]
            yield sheet.name, rows, [(r0 + 1, r1, c0 + 1, c1) for r0, r1, c0, c1 in sheet.merged_cells]
    else:
        import openpyxl
        book = openpyxl.load_workbook(path, data_only=True)
        for sheet in book:
            rows = [list(r) for r in sheet.values]
            for cell in sheet.merged_cells.ranges:
                for r in range(cell.min_row - 1, cell.max_row):
                    for c in range(cell.min_col - 1, cell.max_col):
                        rows[r][c] = rows[cell.min_row - 1][cell.min_col - 1]
            yield sheet.title, rows, [(m.min_row, m.max_row, m.min_col, m.max_col) for m in sheet.merged_cells.ranges]
        book.close()


def clean_folder(folder, skills=None):
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('工程文件夹不存在')
    estimates, inventory, files, warnings, sheet_audit = [], [], [], [], []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name.startswith(('~', '.')):
            continue
        if path.suffix.lower() not in ('.xlsx', '.xls', '.pdf', '.dwg', '.dxf', '.txt', '.json'):
            continue
        relative = str(path.relative_to(root))
        if skills and not skills.call('data_cleaning', 'include_file', relative):
            warnings.append('按当前清洗技能排除文件：' + relative)
            continue
        files.append({'name': path.name, 'path': relative, 'size': path.stat().st_size, 'type': path.suffix.lower()})
        if path.suffix.lower() not in ('.xlsx', '.xls'):
            continue
        is_est = any(t in path.name for t in ('估算', '可研', '概算', '预算'))
        try:
            for title, rows in sheets(path):
                # Use summary sheets once, avoiding fee/price-adjustment duplicates.
                if skills:
                    if not skills.call('data_cleaning', 'include_sheet', path.name, title, is_est):
                        continue
                else:
                    if is_est and (not any(t in title for t in ('设备汇总', '材料汇总', '装置性材料')) or any(t in title for t in ('价差', '配合比', '单公里'))):
                        continue
                    if is_est and title == '乙供设备汇总表' and '临时施工' in path.name:
                        continue
                mapping = {}
                start = 0
                for ri, row in enumerate(rows[:20]):
                    normalized = [re.sub(r'\s+', '', text(c)) for c in row]
                    names = [i for i, c in enumerate(normalized) if c in ('名称', '设备名称', '材料名称', '设备材料名称', '项目名称')]
                    units = [i for i, c in enumerate(normalized) if c in ('单位', '计量单位')]
                    if names and units:
                        mapping = {'name': names[0], 'unit': units[0]}
                        for offset, header in enumerate(rows[ri:ri + 3]):
                            for ci, cell in enumerate(header):
                                val = re.sub(r'\s+', '', text(cell))
                                for key, choices in [('qty', ('数量', '工程量')), ('spec', ('规格', '型号', '型号及规范', '规格型号'))]:
                                    if val in choices and key not in mapping:
                                        mapping[key] = ci
                                if '单价' in val and 'price' not in mapping:
                                    mapping['price'] = ci
                        start = ri + 1
                        break
                if not {'name', 'unit', 'qty'}.issubset(mapping):
                    if is_est:
                        warnings.append(f'{path.name} / {title}：未识别完整表头，未纳入')
                    continue
                before = len(estimates) + len(inventory)
                hierarchy = title
                for ri in range(start, len(rows)):
                    row = rows[ri]
                    def val(key):
                        col = mapping.get(key)
                        return text(row[col]) if col is not None and col < len(row) else ''
                    name, unit, qty = val('name'), val('unit'), val('qty')
                    if name and not unit and number(qty) is None:
                        hierarchy = name
                    if not name or not unit or number(qty) is None or any(t in name for t in ('小计', '合计', '税率')):
                        continue
                    evidence = {'file': relative, 'sheet': title, 'row': ri + 1, 'original_id': text(row[0]), 'quantity_column': mapping['qty'] + 1}
                    if is_est:
                        estimates.append({'序号': len(estimates) + 1, 'Estimation_Name': name, '型号及规范': val('spec'), 'Estimation_Unit': unit, 'Estimation_Quantity': qty, 'Estimation_Price': val('price'), '层级': hierarchy, '_source': evidence})
                    else:
                        inventory.append({'序号': len(inventory) + 1, '设备材料名称': name, '型号及规范': val('spec'), '单位': unit, '数量': qty, '层级': hierarchy, '_source': evidence})
                    if skills:
                        dest = estimates if is_est else inventory
                        original = dest[-1]
                        transformed = skills.call('data_cleaning', 'transform_record', original, is_est)
                        required = ('Estimation_Name','Estimation_Unit','Estimation_Quantity') if is_est else ('设备材料名称','单位','数量')
                        if not isinstance(transformed,dict) or any(k not in transformed for k in required):
                            raise ValueError('清洗技能移除了必要字段')
                        transformed.update({'序号': original['序号'], '_source': evidence})
                        dest[-1] = transformed
                count = len(estimates) + len(inventory) - before
                sheet_audit.append({'file': relative, 'sheet': title, 'rows': count})
        except Exception as exc:
            warnings.append(f'{path.name}：解析失败（{type(exc).__name__}）')
    warnings.extend(['本次为表头规则清洗草稿；跨表重复、成套关系、金额守恒尚需人工确认。', '多工程数量列默认采用首个数量列，请核对通信等多列清册的工程范围。'])
    return {'sources': estimates, 'targets': inventory, 'files': files, 'warnings': warnings, 'sheet_audit': sheet_audit, 'origin': str(root), 'label': root.name}


def load_samples(root):
    sources = json.loads((root / '估算数据.txt').read_text(encoding='utf-8-sig'))
    targets = json.loads((root / '清册数据.txt').read_text(encoding='utf-8-sig'))
    for label, items in [('估算数据.txt', sources), ('清册数据.txt', targets)]:
        for i, item in enumerate(items, 1):
            item['_source'] = {'file': label, 'row': i, 'original_id': item.get('序号'), 'sheet': 'JSON条目'}
            item['序号'] = i
    return {'sources': sources, 'targets': targets, 'files': [{'name': n, 'type': '.txt', 'size': (root / n).stat().st_size} for n in ('估算数据.txt', '清册数据.txt')], 'warnings': ['现有 TXT 样例包含110kV设备，未认定为35kV鲁纳工程清洗成果。'], 'sheet_audit': [], 'origin': str(root), 'label': '现有 TXT 样例 · 项目待确认'}
