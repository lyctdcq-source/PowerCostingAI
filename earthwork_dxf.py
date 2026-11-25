"""土石方资料清洗 · DXF 读写：读取 ASCII DXF 中建模用得到的实体，写出带图层的简化 DXF。纯 Python。

读取：LINE、LWPOLYLINE、POLYLINE（含顶点）、POINT、CIRCLE、TEXT、MTEXT、INSERT（取插入点）、HATCH（取边界）。
写出：AutoCAD 2000 格式（AC1015）的 LWPOLYLINE、POINT、CIRCLE、TEXT，文字按 UTF-8 写并声明编码。"""
import math
import re

READ_TYPES = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'POINT', 'CIRCLE', 'TEXT', 'MTEXT', 'INSERT', 'HATCH'}


def new_drawing(units='m'):
    return {'units': units, 'layers': {}}


def layer(drawing, name):
    return drawing['layers'].setdefault(name, {'lines': [], 'points': [], 'texts': []})


def add_line(drawing, name, pts, closed=False, elev=None, fill=False):
    layer(drawing, name)['lines'].append({'pts': pts, 'closed': closed, 'elev': elev, 'fill': fill})


def add_point(drawing, name, x, y, z=None, label=''):
    layer(drawing, name)['points'].append({'x': x, 'y': y, 'z': z, 'label': label})


def add_text(drawing, name, text, x, y, height=1.0, angle=0.0):
    layer(drawing, name)['texts'].append({'text': text, 'x': x, 'y': y, 'h': height, 'angle': angle})


def stats(drawing):
    return {name: {'lines': len(l['lines']), 'points': len(l['points']), 'texts': len(l['texts']),
                   'vertices': sum(len(x['pts']) for x in l['lines'])} for name, l in drawing['layers'].items()}


def extent(drawing):
    x0 = y0 = math.inf; x1 = y1 = -math.inf
    for l in drawing['layers'].values():
        for line in l['lines']:
            for x, y in line['pts']:
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
        for p in l['points'] + l['texts']:
            x0 = min(x0, p['x']); x1 = max(x1, p['x']); y0 = min(y0, p['y']); y1 = max(y1, p['y'])
    return None if x0 == math.inf else (x0, y0, x1, y1)


# ---------- 读取
def _decode(data):
    """DXF 2007 及以后为 UTF-8；更早的版本按 $DWGCODEPAGE（中文图纸一般为 ANSI_936）。"""
    head = data[:4000].decode('latin1')
    version = re.search(r'\$ACADVER\s+1\s+(AC\d+)', head)
    if version and version.group(1) >= 'AC1021':
        return data.decode('utf-8', 'replace')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', 'replace')


_UNICODE_ESCAPE = re.compile(r'\\U\+([0-9A-Fa-f]{4})')
_MTEXT_CODES = re.compile(r'\\[AaCcFfHhQqTtWwLlOoKk][^;]*;|\\[PpNn~]|[{}]')


def _clean_text(text, mtext=False):
    text = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    if mtext:
        text = _MTEXT_CODES.sub(lambda m: '\n' if m.group(0) in ('\\P', '\\p', '\\N', '\\n') else '', text)
    return text.replace('%%d', '°').replace('%%D', '°').replace('%%p', '±').replace('%%P', '±').replace('%%c', 'φ').replace('%%C', 'φ').strip()


def read(path):
    """读取 DXF 模型空间。返回 drawing；不支持二进制 DXF 和 DWG。"""
    with open(path, 'rb') as handle:
        data = handle.read()
    if data[:22].startswith(b'AutoCAD Binary DXF'):
        raise ValueError('不支持二进制 DXF，请在 CAD 中另存为 ASCII 格式的 DXF')
    if data[:6] in (b'AC1012', b'AC1014', b'AC1015', b'AC1018', b'AC1021', b'AC1024', b'AC1027', b'AC1032'):
        raise ValueError('这是 DWG 文件，请在 CAD 中另存为 DXF')
    lines = _decode(data).splitlines()
    n = len(lines) - 1
    drawing = new_drawing('m')
    i = 0
    # 定位 ENTITIES 段
    start = None
    while i < n:
        if lines[i].strip() == '2' and lines[i + 1].strip() == 'ENTITIES' and i >= 2 and lines[i - 1].strip() == 'SECTION':
            start = i + 2
            break
        i += 2 if lines[i].strip() != '0' else 1
        if lines[i - 1].strip() == '0':
            continue
    if start is None:
        # 逐对扫描兜底
        for k in range(0, n, 2):
            if lines[k].strip() == '2' and lines[k + 1].strip() == 'ENTITIES':
                start = k + 2
                break
    if start is None:
        raise ValueError('DXF 中没有 ENTITIES 段')
    i = start
    polyline = None     # 旧式 POLYLINE：后续 VERTEX 归它，SEQEND 结束
    while i < n:
        code = lines[i].strip(); value = lines[i + 1].strip()
        i += 2
        if code != '0':
            continue
        if value == 'ENDSEC':
            break
        if value == 'VERTEX' and polyline is not None:
            x = y = z = 0.0
            while i < n and lines[i].strip() != '0':
                c = lines[i].strip(); v = lines[i + 1]
                if c == '10': x = float(v)
                elif c == '20': y = float(v)
                elif c == '30': z = float(v)
                i += 2
            polyline['pts'].append((x, y)); polyline['zs'].append(z)
            continue
        if value == 'SEQEND' and polyline is not None:
            zs = polyline.pop('zs')
            if polyline['elev'] is None and zs and max(zs) - min(zs) < 1e-6 and abs(zs[0]) > 1e-9:
                polyline['elev'] = zs[0]
            if len(polyline['pts']) >= 2:
                layer(drawing, polyline.pop('layer'))['lines'].append(polyline)
            polyline = None
            continue
        if value not in READ_TYPES:
            continue
        # 收集本实体的组码
        pairs = []
        while i < n and lines[i].strip() != '0':
            pairs.append((lines[i].strip(), lines[i + 1]))
            i += 2
        name = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), next((v.strip() for c, v in pairs if c == '8'), '0'))
        if value == 'LWPOLYLINE':
            pts, elev, flags, x = [], None, 0, None
            for c, v in pairs:
                if c == '10': x = float(v)
                elif c == '20' and x is not None: pts.append((x, float(v))); x = None
                elif c == '38': elev = float(v)
                elif c == '70': flags = int(float(v))
            if len(pts) >= 2:
                layer(drawing, name)['lines'].append({'pts': pts, 'closed': bool(flags & 1), 'elev': elev if elev else None, 'fill': False})
        elif value == 'LINE':
            g = {c: float(v) for c, v in pairs if c in ('10', '20', '30', '11', '21', '31')}
            z = g.get('30', 0.0)
            layer(drawing, name)['lines'].append({'pts': [(g.get('10', 0.0), g.get('20', 0.0)), (g.get('11', 0.0), g.get('21', 0.0))], 'closed': False,
                                                  'elev': z if z and abs(z - g.get('31', 0.0)) < 1e-6 else None, 'fill': False})
        elif value == 'POLYLINE':
            flags = next((int(float(v)) for c, v in pairs if c == '70'), 0)
            z = next((float(v) for c, v in pairs if c == '30'), 0.0)
            polyline = {'pts': [], 'zs': [], 'closed': bool(flags & 1), 'elev': z if z else None, 'fill': False, 'layer': name}
        elif value in ('POINT', 'CIRCLE', 'INSERT'):
            g = {c: v for c, v in pairs if c in ('10', '20', '30', '40', '2')}
            z = float(g.get('30', 0) or 0)
            entry = {'x': float(g.get('10', 0)), 'y': float(g.get('20', 0)), 'z': z if z else None, 'label': g.get('2', '').strip() if value == 'INSERT' else ''}
            if value == 'CIRCLE':
                entry['r'] = float(g.get('40', 0) or 0)
            entry['kind'] = value
            layer(drawing, name)['points'].append(entry)
        elif value in ('TEXT', 'MTEXT'):
            g = {}
            chunks = []
            for c, v in pairs:
                if c in ('1', '3'): chunks.append((c, v))
                elif c in ('10', '20', '40', '50', '11', '21', '72', '73') and c not in g: g[c] = v
            text = ''.join(v for c, v in chunks if c == '3') + ''.join(v for c, v in chunks if c == '1')
            text = _clean_text(text, value == 'MTEXT')
            if not text:
                continue
            x, y = float(g.get('10', 0)), float(g.get('20', 0))
            # 非左对齐的 TEXT 以第二对齐点为准
            if value == 'TEXT' and (int(float(g.get('72', 0) or 0)) or int(float(g.get('73', 0) or 0))) and '11' in g:
                x, y = float(g['11']), float(g.get('21', 0))
            angle = math.radians(float(g.get('50', 0) or 0)) if value == 'TEXT' else (math.atan2(float(g.get('21', 0) or 0), float(g['11'])) if '11' in g else 0.0)
            layer(drawing, name)['texts'].append({'text': text, 'x': x, 'y': y, 'h': float(g.get('40', 1) or 1), 'angle': angle})
        elif value == 'HATCH':
            # 边界环：多段线型（92 的第 2 位）或由直线段组成的边
            k, total = 0, len(pairs)
            while k < total:
                if pairs[k][0] != '92':
                    k += 1
                    continue
                flag = int(float(pairs[k][1])); k += 1
                pts = []
                if flag & 2:
                    x = None
                    while k < total and pairs[k][0] in ('72', '73', '93', '10', '20', '42'):
                        c, v = pairs[k]
                        if c == '10': x = float(v)
                        elif c == '20' and x is not None: pts.append((x, float(v))); x = None
                        k += 1
                else:
                    x = None
                    while k < total and pairs[k][0] not in ('92', '75', '97'):
                        c, v = pairs[k]
                        if c == '10': x = float(v)
                        elif c == '20' and x is not None: pts.append((x, float(v))); x = None
                        k += 1
                if len(pts) >= 3:
                    layer(drawing, name)['lines'].append({'pts': pts, 'closed': True, 'elev': None, 'fill': True})
    return drawing


# ---------- 写出
def _fmt(v):
    return f'{v:.4f}'.rstrip('0').rstrip('.') if abs(v) < 1e7 else f'{v:.3f}'


LAYER_COLORS = {'等高线-计曲线': 30, '等高线-首曲线': 8, '等高线-高程待定': 6, '高程点': 3, '高程注记': 3, '高程点-高程待定': 6,
                '征地线': 1, '场平范围': 5, '钻孔': 4, '钻孔编号': 4}


def write(drawing, path, colors=None):
    """写出 AC1015 的 ASCII DXF（UTF-8，文字中的非 ASCII 字符用 \\U+XXXX 转义，各版本 CAD 都能正确显示）。"""
    colors = dict(LAYER_COLORS, **(colors or {}))
    out = []
    add = out.append

    def esc(text):
        return ''.join(ch if ord(ch) < 128 else f'\\U+{ord(ch):04X}' for ch in str(text).replace('\n', ' '))
    handle = [0x100]

    def nxt():
        handle[0] += 1
        return f'{handle[0]:X}'
    box = extent(drawing) or (0, 0, 1, 1)
    add('0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n9\n$DWGCODEPAGE\n3\nANSI_936\n9\n$INSUNITS\n70\n6\n'
        f'9\n$EXTMIN\n10\n{_fmt(box[0])}\n20\n{_fmt(box[1])}\n30\n0\n9\n$EXTMAX\n10\n{_fmt(box[2])}\n20\n{_fmt(box[3])}\n30\n0\n'
        f'9\n$HANDSEED\n5\nFFFFF\n0\nENDSEC\n')
    add('0\nSECTION\n2\nTABLES\n')
    add('0\nTABLE\n2\nVPORT\n5\n8\n100\nAcDbSymbolTable\n70\n0\n0\nENDTAB\n')
    add('0\nTABLE\n2\nLTYPE\n5\n5\n100\nAcDbSymbolTable\n70\n1\n0\nLTYPE\n5\n14\n100\nAcDbSymbolTableRecord\n100\nAcDbLinetypeTableRecord\n2\nBYBLOCK\n70\n0\n3\n\n72\n65\n73\n0\n40\n0\n'
        '0\nLTYPE\n5\n15\n100\nAcDbSymbolTableRecord\n100\nAcDbLinetypeTableRecord\n2\nBYLAYER\n70\n0\n3\n\n72\n65\n73\n0\n40\n0\n'
        '0\nLTYPE\n5\n16\n100\nAcDbSymbolTableRecord\n100\nAcDbLinetypeTableRecord\n2\nCONTINUOUS\n70\n0\n3\nSolid line\n72\n65\n73\n0\n40\n0\n0\nENDTAB\n')
    names = ['0'] + [n for n in drawing['layers'] if n != '0']
    add(f'0\nTABLE\n2\nLAYER\n5\n2\n100\nAcDbSymbolTable\n70\n{len(names)}\n')
    for n in names:
        add(f'0\nLAYER\n5\n{nxt()}\n100\nAcDbSymbolTableRecord\n100\nAcDbLayerTableRecord\n2\n{esc(n)}\n70\n0\n62\n{colors.get(n, 7)}\n6\nCONTINUOUS\n')
    add('0\nENDTAB\n')
    add('0\nTABLE\n2\nSTYLE\n5\n3\n100\nAcDbSymbolTable\n70\n1\n0\nSTYLE\n5\n11\n100\nAcDbSymbolTableRecord\n100\nAcDbTextStyleTableRecord\n2\nSTANDARD\n70\n0\n40\n0\n41\n1\n50\n0\n71\n0\n42\n2.5\n3\ntxt\n4\n\n0\nENDTAB\n')
    for table, h in (('VIEW', '6'), ('UCS', '7')):
        add(f'0\nTABLE\n2\n{table}\n5\n{h}\n100\nAcDbSymbolTable\n70\n0\n0\nENDTAB\n')
    add('0\nTABLE\n2\nAPPID\n5\n9\n100\nAcDbSymbolTable\n70\n1\n0\nAPPID\n5\n12\n100\nAcDbSymbolTableRecord\n100\nAcDbRegAppTableRecord\n2\nACAD\n70\n0\n0\nENDTAB\n')
    add('0\nTABLE\n2\nDIMSTYLE\n5\nA\n100\nAcDbSymbolTable\n70\n0\n100\nAcDbDimStyleTable\n0\nENDTAB\n')
    add('0\nTABLE\n2\nBLOCK_RECORD\n5\n1\n100\nAcDbSymbolTable\n70\n2\n'
        '0\nBLOCK_RECORD\n5\n1F\n100\nAcDbSymbolTableRecord\n100\nAcDbBlockTableRecord\n2\n*MODEL_SPACE\n'
        '0\nBLOCK_RECORD\n5\n1B\n100\nAcDbSymbolTableRecord\n100\nAcDbBlockTableRecord\n2\n*PAPER_SPACE\n0\nENDTAB\n0\nENDSEC\n')
    add('0\nSECTION\n2\nBLOCKS\n'
        '0\nBLOCK\n5\n20\n100\nAcDbEntity\n8\n0\n100\nAcDbBlockBegin\n2\n*MODEL_SPACE\n70\n0\n10\n0\n20\n0\n30\n0\n3\n*MODEL_SPACE\n1\n\n0\nENDBLK\n5\n21\n100\nAcDbEntity\n8\n0\n100\nAcDbBlockEnd\n'
        '0\nBLOCK\n5\n1C\n100\nAcDbEntity\n67\n1\n8\n0\n100\nAcDbBlockBegin\n2\n*PAPER_SPACE\n70\n0\n10\n0\n20\n0\n30\n0\n3\n*PAPER_SPACE\n1\n\n0\nENDBLK\n5\n1D\n100\nAcDbEntity\n67\n1\n8\n0\n100\nAcDbBlockEnd\n0\nENDSEC\n')
    add('0\nSECTION\n2\nENTITIES\n')
    for name, content in drawing['layers'].items():
        lname = esc(name)
        for line in content['lines']:
            pts = line['pts']
            if len(pts) < 2:
                continue
            parts = [f'0\nLWPOLYLINE\n5\n{nxt()}\n100\nAcDbEntity\n8\n{lname}\n100\nAcDbPolyline\n90\n{len(pts)}\n70\n{1 if line.get("closed") else 0}\n']
            if line.get('elev') is not None:
                parts.append(f'38\n{_fmt(line["elev"])}\n')
            parts.extend(f'10\n{_fmt(x)}\n20\n{_fmt(y)}\n' for x, y in pts)
            add(''.join(parts))
        for p in content['points']:
            z = _fmt(p['z']) if p.get('z') is not None else '0'
            if p.get('r'):
                add(f'0\nCIRCLE\n5\n{nxt()}\n100\nAcDbEntity\n8\n{lname}\n100\nAcDbCircle\n10\n{_fmt(p["x"])}\n20\n{_fmt(p["y"])}\n30\n{z}\n40\n{_fmt(p["r"])}\n')
            else:
                add(f'0\nPOINT\n5\n{nxt()}\n100\nAcDbEntity\n8\n{lname}\n100\nAcDbPoint\n10\n{_fmt(p["x"])}\n20\n{_fmt(p["y"])}\n30\n{z}\n')
        for t in content['texts']:
            add(f'0\nTEXT\n5\n{nxt()}\n100\nAcDbEntity\n8\n{lname}\n100\nAcDbText\n10\n{_fmt(t["x"])}\n20\n{_fmt(t["y"])}\n30\n0\n40\n{_fmt(max(t.get("h") or 1, 0.01))}\n'
                f'1\n{esc(t["text"])}\n50\n{_fmt(math.degrees(t.get("angle") or 0))}\n100\nAcDbText\n')
    add('0\nENDSEC\n0\nSECTION\n2\nOBJECTS\n0\nDICTIONARY\n5\nC\n100\nAcDbDictionary\n281\n1\n0\nENDSEC\n0\nEOF\n')
    with open(path, 'w', encoding='utf-8', newline='\r\n') as handle_out:
        handle_out.write(''.join(out))
