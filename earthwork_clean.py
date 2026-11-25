"""土石方资料清洗：把施工图布置图和岩土工程勘察报告整理成三维建模用的三份成果（本机规则，不调用在线模型）。

两类输入：
第 1 类  布置图 PDF + 勘察报告 PDF。勘察报告里找出钻孔柱状图页（读出钻孔分层）和勘探点平面位置图页
        （用钻孔坐标作控制点配准到工程坐标）；布置图与勘探点平面图按相同的底图图层套合，得到工程坐标。
第 2 类  直接提供含等高线、高程点、征地线、场平范围的 DXF，以及勘探点平面位置图 DXF 和钻孔分层数据 CSV。

成果：三维建模基础数据.dxf（等高线、高程点、征地线、场平范围）、勘探点平面位置图.dxf、钻孔分层数据.csv。"""
import json
import math
import re
import shutil
import time
from pathlib import Path

import earthwork_contours as contours
import earthwork_dxf as dxf
import earthwork_geom as geom
import earthwork_glyphs as glyphs
import earthwork_survey as survey
import earthwork_tin as tin

OUTPUT_DIR = '土石方清洗成果'
BASE_NAME, PLAN_NAME, HOLES_NAME, REPORT_NAME = '三维建模基础数据.dxf', '勘探点平面位置图.dxf', '钻孔分层数据.csv', '清洗报告.json'
ROLES = {'contour': '等高线', 'point': '高程点', 'land': '征地线', 'platform': '场平范围'}
# 图层名规则：CASS 等测图软件的标准图层代码与常见中文名
ROLE_PATTERNS = {
    'contour': re.compile(r'等高线|首曲线|计曲线|DGX|CONTOUR', re.I),
    'point': re.compile(r'高程点|离散点|GCD|ELEV', re.I),
    'land': re.compile(r'征地|用地|红线|YDHX|界线', re.I),
    'platform': re.compile(r'场平|坡顶|坡角|坡脚|围墙|PLATFORM', re.I),
}
EXCLUDE = re.compile(r'注记|文字|TEXT|图例|图框|待定|裁切', re.I)
ELEVATION = re.compile(r'^\d{2,4}\.\d{1,3}$')
CONTOUR_LABEL = re.compile(r'^\d{2,4}(?:\.\d{1,2})?$')
TOLERANCE = 0.01          # 成果折线抽稀容差（m）


def _log(progress, text):
    if progress:
        progress(text)


# ---------- PDF 页 → 图形（页面坐标或工程坐标）
def page_drawing(page, transform=None, tolerance=0.0):
    """把 extract_page 的结果整理成按图层的线、点、文字。带宽度的线（三角形条带）还原为中心线。
    每条线附带 color、width、strip（是否由条带还原）。"""
    drawing = dxf.new_drawing('m' if transform else 'pt')
    scale = geom.scale_of(transform) if transform else 1.0
    rotation = math.atan2(transform[1], transform[0]) if transform else 0.0
    conv = (lambda p: geom.apply(transform, p)) if transform else (lambda p: p)

    def put(name, pts, closed, fill, color, width, strip=False):
        pts = geom.dedupe([conv(p) for p in pts])
        if len(pts) == 1 or (len(pts) == 2 and math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]) < 1e-6):
            dxf.add_point(drawing, name, pts[0][0], pts[0][1])
            return
        if len(pts) < 2:
            return
        if tolerance and len(pts) > 2:
            pts = geom.simplify(pts, tolerance)
        entry = {'pts': pts, 'closed': closed, 'elev': None, 'fill': fill, 'color': color, 'width': width * scale, 'strip': strip}
        dxf.layer(drawing, name)['lines'].append(entry)

    for path in page['paths']:
        name = path['layer'] or '0'
        triangles = geom.path_triangles(path) if path['fill'] else None
        if triangles:
            line = geom.strip_centerline(triangles)
            if line and len(line) >= 2:
                put(name, line, False, False, path['color'], path['width'], True)
                continue
        for sub, closed in zip(path['subs'], path['closed']):
            put(name, sub, closed, path['fill'], path['color'], path['width'])
    for t in page['texts']:
        x, y = conv((t['x'], t['y']))
        dxf.add_text(drawing, t['layer'] or '0', t['text'], x, y, max(t['size'] * scale * 0.72, 0.01), t['angle'] + rotation)
    return drawing


# ---------- 配准
def georeference_by_holes(page, holes):
    """勘探点平面图 → 工程坐标：图上的钻孔编号文字对应钻孔柱状图表头的坐标。
    先按编号文字的位置粗配准，再找到钻孔符号（编号旁的小圆等），用符号中心精配准。"""
    by_id = {h['id']: h for h in holes}
    labels = [t for t in page['texts'] if t['text'] in by_id]
    seen, unique = set(), []
    for t in labels:
        if t['text'] not in seen:
            seen.add(t['text']); unique.append(t)
    if len(unique) < 3:
        raise ValueError(f'勘探点平面图上只找到 {len(unique)} 个与钻孔柱状图对应的钻孔编号，无法配准（至少需要 3 个）')
    target = [(by_id[t['text']]['E'], by_id[t['text']]['N']) for t in unique]
    rough, _, _ = geom.robust_similarity([(t['x'], t['y']) for t in unique], target)
    counts = {}
    for t in unique:
        counts[t['layer']] = counts.get(t['layer'], 0) + 1
    hole_layer = max(counts, key=counts.get)
    # 钻孔符号：同图层里的小图形，相互接触的部件并成一个符号
    parts = []
    for path in page['paths']:
        if path['layer'] != hole_layer:
            continue
        x0, y0, x1, y1 = geom.bbox([p for sub in path['subs'] for p in sub])
        if math.hypot(x1 - x0, y1 - y0) < 18:
            parts.append([x0, y0, x1, y1])
    parent = list(range(len(parts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    grid = geom.Grid(20)
    for i, (x0, y0, x1, y1) in enumerate(parts):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for _, j in grid.near(cx, cy, 20):
            a = parts[j]
            if x0 - 0.5 <= a[2] and a[0] - 0.5 <= x1 and y0 - 0.5 <= a[3] and a[1] - 0.5 <= y1:
                parent[find(i)] = find(j)
        grid.add(cx, cy, i)
    boxes = {}
    for i, (x0, y0, x1, y1) in enumerate(parts):
        b = boxes.setdefault(find(i), [x0, y0, x1, y1])
        b[0] = min(b[0], x0); b[1] = min(b[1], y0); b[2] = max(b[2], x1); b[3] = max(b[3], y1)
    symbols = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes.values() if math.hypot(b[2] - b[0], b[3] - b[1]) < 24]
    sgrid = geom.Grid(15)
    for i, c in enumerate(symbols):
        sgrid.add(c[0], c[1], i)
    inverse = geom.invert(rough)
    # 编号文字与符号之间的偏移在全图一致：投票取出现最多的偏移
    votes, predicted = {}, []
    for t in unique:
        px, py = geom.apply(inverse, (by_id[t['text']]['E'], by_id[t['text']]['N']))
        predicted.append((px, py))
        for _, i in sgrid.near(px, py, 45):
            key = (round((symbols[i][0] - px) / 1.5), round((symbols[i][1] - py) / 1.5))
            votes[key] = votes.get(key, 0) + 1
    src, dst, used = [(t['x'], t['y']) for t in unique], target, 'label'
    if votes:
        key, top = max(votes.items(), key=lambda kv: kv[1])
        if top >= max(3, len(unique) * 0.5):
            near = [k for k in votes if abs(k[0] - key[0]) <= 1 and abs(k[1] - key[1]) <= 1]
            weight = sum(votes[k] for k in near)
            ox = sum(k[0] * votes[k] for k in near) / weight * 1.5
            oy = sum(k[1] * votes[k] for k in near) / weight * 1.5
            pairs = []
            for (px, py), goal in zip(predicted, target):
                hit = sgrid.nearest(px + ox, py + oy, 4)
                if hit:
                    pairs.append((symbols[hit[1]], goal))
            if len(pairs) >= max(3, len(unique) * 0.5):
                src, dst, used = [p for p, _ in pairs], [g for _, g in pairs], 'symbol'
    transform, keep, res = geom.robust_similarity(src, dst)
    rms = math.sqrt(sum(r * r for r in res) / len(res))
    return transform, {'control_points': len(keep), 'labels_found': len(unique), 'holes': len(holes), 'by': used, 'hole_layer': hole_layer,
                       'rms': round(rms, 3), 'max': round(max(res), 3), 'scale': round(geom.scale_of(transform), 6),
                       'rotation': round(geom.rotation_of(transform), 4), 'plot_scale': round(geom.scale_of(transform) / 0.0254 * 72)}


def register_pages(source, target):
    """两张图用同一份底图时，同名图层里的图形逐一对应：按对象顺序配对顶点，拟合 source → target 的相似变换。"""
    def by_layer(page):
        out = {}
        for path in page['paths']:
            if not path['fill']:
                out.setdefault(path['layer'], []).append(path)
        return out
    a, b = by_layer(source), by_layer(target)
    src, dst, used = [], [], []
    for name in sorted(set(a) & set(b), key=lambda n: -len(a[n])):
        pa, pb = a[name], b[name]
        if len(pa) != len(pb) or len(pa) < 4 or any(len(x['subs'][0]) != len(y['subs'][0]) for x, y in zip(pa, pb)):
            continue
        for x, y in zip(pa, pb):
            src.extend(x['subs'][0]); dst.extend(y['subs'][0])
        used.append(name)
        if len(src) > 4000:
            break
    if len(src) < 12:
        raise ValueError('布置图与勘探点平面图没有可逐一对应的同名图层，无法套合；请确认两张图用的是同一份地形底图')
    transform, keep, res = geom.robust_similarity(src, dst)
    rms = math.sqrt(sum(r * r for r in res) / len(res))
    if len(keep) < len(src) * 0.6 or rms > 1.0:
        raise ValueError(f'布置图与勘探点平面图套合残差过大（{rms:.2f} 点），无法确认是同一份底图')
    return transform, {'layers': used, 'points': len(keep), 'rms_pt': round(rms, 3), 'scale': round(geom.scale_of(transform), 5),
                       'rotation': round(geom.rotation_of(transform), 3)}


# ---------- 图层角色
def layer_summary(drawing):
    out = []
    for name, content in drawing['layers'].items():
        lines = content['lines']
        out.append({'name': name, 'lines': len(lines), 'points': len(content['points']), 'texts': len(content['texts']),
                    'with_elev': sum(1 for l in lines if l.get('elev') is not None),
                    'with_z': sum(1 for p in content['points'] if p.get('z') is not None)})
    return sorted(out, key=lambda x: -(x['lines'] + x['points'] + x['texts']))


def _dash_segments(lines):
    """虚线的每一小段在 PDF/DXF 中是细长的小多边形或短线：取其长轴作为线段。"""
    segments, seen = [], set()
    for line in lines:
        pts = line['pts']
        if line.get('closed') and len(pts) >= 3 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if (line.get('closed') or line.get('fill')) and 3 <= len(pts) <= 6:
            edges = sorted(((math.hypot(a[0] - b[0], a[1] - b[1]), a, b) for a, b in zip(pts, pts[1:] + pts[:1])), key=lambda e: e[0])
            if len(pts) == 3:
                _, a, b = edges[0]
                c = next(p for p in pts if p is not a and p is not b)
                seg = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), c]
            else:
                (_, a, b), (_, c, d) = edges[0], edges[1]
                seg = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), ((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)]
        else:
            seg = list(pts)
        key = tuple(sorted((round(seg[0][0], 2), round(seg[0][1], 2), round(seg[-1][0], 2), round(seg[-1][1], 2))))
        if key in seen or geom.length(seg) < 1e-6:
            continue
        seen.add(key)
        segments.append(seg)
    return segments


def _ring_from_dashes(lines):
    """把虚线各段串成环。返回 (折线, 是否闭合, 各段)；串不起来返回 None。"""
    segments = _dash_segments(lines)
    if len(segments) < 3:
        return None
    lengths = sorted(geom.length(s) for s in segments)
    typical = lengths[len(lengths) // 2]
    chains = geom.chain(segments, max(typical * 1.6, 0.3))
    pts, closed = max(chains, key=lambda c: geom.length(c[0]))
    if not closed and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= max(typical * 4, 2.0):
        closed = True
    return pts, closed, segments


def guess_roles(drawing):
    """按图层名猜各角色用哪些图层；征地线图层名不规范时，找“由短虚线段围成闭合环”的图层。"""
    roles = {k: [] for k in ROLES}
    for name, content in drawing['layers'].items():
        if EXCLUDE.search(name):
            continue
        for role, pattern in ROLE_PATTERNS.items():
            if pattern.search(name):
                if role == 'contour' and not content['lines']:
                    continue
                if role == 'point' and not content['points']:
                    continue
                roles[role].append(name)
                break
    box = dxf.extent(drawing)
    extent_area = (box[2] - box[0]) * (box[3] - box[1]) if box else 0
    ring = None
    if roles['land']:
        found = _ring_from_dashes([l for name in roles['land'] for l in drawing['layers'][name]['lines']])
        ring = found[0] if found and found[1] else None
    if ring is None:
        # 图层名看不出征地线时：找“大部分是短线段、能串成闭合环”的图层，取围出面积最大的（整幅图框除外）
        best = None
        for name, content in drawing['layers'].items():
            lines = content['lines']
            if len(lines) < 40 or name in roles['contour']:
                continue
            if sum(1 for l in lines if len(l['pts']) <= 6) < len(lines) * 0.9:
                continue
            found = _ring_from_dashes(lines)
            if not found or not found[1] or len(found[0]) < 8:
                continue
            size = abs(geom.area(found[0]))
            if size < extent_area * 0.7 and (best is None or size > best[0]):
                best = (size, name, found[0])
        if best:
            roles['land'], ring = [best[1]], best[2]
    # 场平范围：征地线以内最大的闭合多边形所在图层；找不到再按图层名
    found = platform_candidates(drawing, ring)
    if found:
        roles['platform'] = [found[0][0]]
    return roles


# ---------- 基础数据提取
def _spots(drawing, layers, reference=None, radius=0.6, recognised=None):
    """高程点：自带高程的直接用；否则找旁边的高程注记文字；再否则到参考高程点（勘探点平面图上的）里按位置取。"""
    labels = geom.Grid(4.0)
    for name, content in drawing['layers'].items():
        for t in content['texts']:
            if ELEVATION.match(t['text'].strip()):
                labels.add(t['x'], t['y'], float(t['text']))
    ref = None
    if reference:
        ref = geom.Grid(2.0)
        for x, y, z in reference:
            ref.add(x, y, z)
    found, seen = [], set()
    for name in layers:
        for p in drawing['layers'].get(name, {}).get('points', []):
            key = (round(p['x'], 2), round(p['y'], 2))
            if key in seen:
                continue
            seen.add(key)
            found.append([p['x'], p['y'], p.get('z'), '图面' if p.get('z') is not None else ''])
    pending = [s for s in found if s[2] is None]
    if pending and labels.map:
        # 注记相对点位的偏移全图一致：投票取最常见的偏移，再按预测位置取注记
        votes = {}
        for s in pending:
            for d, z in labels.near(s[0], s[1], 4.0)[:3]:
                pass
        offsets = {}
        for s in pending[:400]:
            for cx, cy in ((s[0], s[1]),):
                for i in range(-1, 2):
                    for j in range(-1, 2):
                        for px, py, z in labels.map.get((math.floor(cx / 4.0) + i, math.floor(cy / 4.0) + j), ()):
                            if math.hypot(px - cx, py - cy) <= 4.0:
                                key = (round((px - cx) / 0.25), round((py - cy) / 0.25))
                                offsets[key] = offsets.get(key, 0) + 1
        if offsets:
            key = max(offsets, key=offsets.get)
            ox, oy = key[0] * 0.25, key[1] * 0.25
            for s in pending:
                hit = labels.nearest(s[0] + ox, s[1] + oy, 0.6)
                if hit:
                    s[2], s[3] = hit[1], '注记'
    if ref:
        for s in found:
            if s[2] is None:
                hit = ref.nearest(s[0], s[1], radius)
                if hit:
                    s[2], s[3] = hit[1], '勘探点平面图'
    if recognised:
        grid = geom.Grid(2.0)
        for x, y, z in recognised:
            grid.add(x, y, z)
        for s in found:
            if s[2] is None:
                hit = grid.nearest(s[0], s[1], 0.05)
                if hit:
                    s[2], s[3] = hit[1], '图面注记识别'
    return found


def _contour_lines(drawing, layers, min_open=3.0):
    """等高线图层 → [(折线, 是否计曲线, 已有高程)]。成果里已有高程的线原样保留；没有高程的碎线按类别首尾串接。"""
    raw = [l for name in layers for l in drawing['layers'].get(name, {}).get('lines', [])
           if not (l.get('fill') and l.get('closed')) and len(l['pts']) >= 2]
    with_elev = [l for l in raw if l.get('elev') is not None]
    if len(with_elev) >= len(raw) * 0.5 and with_elev:
        return [(l['pts'], None, l['elev']) for l in with_elev], {'pieces': len(raw), 'has_elevation': True}
    # 标注文字的笔画与等高线同层：线宽不同于主体线宽且很短的是笔画，去掉
    total = {}
    for l in raw:
        key = None if l.get('strip') else round(l.get('width') or 0, 2)
        total[key] = total.get(key, 0) + geom.length(l['pts'])
    main = max(total, key=total.get) if total else None
    kept = [l for l in raw if l.get('strip') or round(l.get('width') or 0, 2) == main or main is None or geom.length(l['pts']) >= 8]
    # 计曲线与首曲线颜色不同时，总长较短的一类是计曲线
    by_color = {}
    for l in kept:
        by_color.setdefault(tuple(l.get('color') or ()), []).append(l)
    classes = sorted(by_color.items(), key=lambda kv: -sum(geom.length(l['pts']) for l in kv[1]))
    index_color = classes[1][0] if len(classes) == 2 and classes[0][0] != classes[1][0] else None
    out = []
    for color, members in classes:
        for pts, closed in geom.chain([l['pts'] for l in members], 0.15):
            if closed and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            if geom.length(pts) < (2.0 if closed else min_open):
                continue
            out.append((geom.simplify(pts, TOLERANCE), (color == index_color) if index_color is not None else None, None))
    return out, {'pieces': len(raw), 'has_elevation': False, 'index_by_colour': index_color is not None}


def platform_candidates(drawing, ring=None, layers=None):
    """场平范围的候选：闭合（或首尾相距不到 1 m）、顶点不多、面积够大、落在征地线以内的多边形。
    排序：图上明确闭合的在前，其次面积大的在前。返回 [(图层, 顶点, 面积, 是否明确闭合)]。"""
    found = []
    ring_area = abs(geom.area(ring)) if ring else None
    for name, content in drawing['layers'].items():
        if layers is not None and name not in layers:
            continue
        for line in content['lines']:
            if line.get('fill') or not 4 <= len(line['pts']) <= 80:
                continue
            pts = geom.dedupe(line['pts'])
            gap = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
            explicit = bool(line.get('closed')) or gap < 1e-6
            if not explicit and gap > 1.0:
                continue
            if gap < 1e-6:
                pts = pts[:-1]
            if len(pts) < 4:
                continue
            size = abs(geom.area(pts))
            if size < 500 or (ring_area and not 0.05 * ring_area <= size <= ring_area):
                continue
            if ring and not all(geom.inside(ring, x, y) for x, y in pts[::max(1, len(pts) // 8)]):
                continue
            found.append((name, pts, size, explicit))
    found.sort(key=lambda c: (not c[3], -c[2]))
    if found:
        # 同一范围常有几条线（坡顶线、场地边线）：面积相近（不小于最大者的 95%）的里面取顶点最少、最规整的一条排在最前
        top = max(c[2] for c in found if c[3] == found[0][3])
        close = [c for c in found if c[3] == found[0][3] and c[2] >= top * 0.95]
        first = min(close, key=lambda c: (len(c[1]), -c[2]))
        found.remove(first); found.insert(0, first)
    return found


def _platform(drawing, layers, ring=None):
    found = platform_candidates(drawing, ring, layers or None)
    return (found[0][2], found[0][1], found[0][0]) if found else None


def build_base(drawing, roles, reference_spots=None, contour_labels=None, interval=None, progress=None, recognised=None):
    """由图形和图层角色整理出三维建模基础数据。返回 (成果图形, 统计, 提示)。"""
    warnings, info = [], {}
    out = dxf.new_drawing('m')
    for name in ('等高线-计曲线', '等高线-首曲线', '等高线-高程待定', '高程点', '高程注记', '高程点-高程待定', '征地线', '场平范围'):
        dxf.layer(out, name)
    # 高程点
    spots = _spots(drawing, roles.get('point') or [], reference_spots, recognised=recognised)
    known = [(x, y, z) for x, y, z, _ in spots if z is not None]
    sources = {}
    for x, y, z, source in spots:
        sources[source or '待定'] = sources.get(source or '待定', 0) + 1
        if z is None:
            dxf.add_point(out, '高程点-高程待定', x, y)
        else:
            dxf.add_point(out, '高程点', x, y, z)
            dxf.add_text(out, '高程注记', f'{z:.2f}', x + 0.5, y, 1.0)
    info['points'] = {'total': len(spots), 'with_elevation': len(known), 'by': sources}
    if not spots:
        warnings.append('没有找到高程点图层，等高线高程无法校核。')
    elif len(known) < len(spots):
        warnings.append(f'{len(spots) - len(known)} 个高程点没有取到高程（注记被图框裁切或不在勘探点平面图范围内），已放在“高程点-高程待定”图层，不参与建模。')
    # 等高线
    _log(progress, '整理等高线…')
    lines, cinfo = _contour_lines(drawing, roles.get('contour') or [])
    info['contours'] = cinfo
    if not lines:
        warnings.append('没有找到等高线图层。')
    elif cinfo['has_elevation']:
        levels = sorted({round(e, 3) for _, _, e in lines})
        gaps = sorted(round(b - a, 3) for a, b in zip(levels, levels[1:]))
        d = gaps[len(gaps) // 2] if gaps else 1.0
        for pts, _, elev in lines:
            is_index = abs(elev / (5 * d) - round(elev / (5 * d))) < 1e-6
            dxf.add_line(out, '等高线-计曲线' if is_index else '等高线-首曲线', geom.simplify(pts, TOLERANCE), elev=elev)
        cinfo.update(total=len(lines), pending=0, interval=d, by={'图面': len(lines)})
    else:
        net_points = known + [p for p in (reference_spots or [])]
        if len(net_points) < 3:
            warnings.append('高程点不足，等高线无法定高程，全部放在“等高线-高程待定”图层。')
            for pts, _, _ in lines:
                dxf.add_line(out, '等高线-高程待定', pts)
            cinfo.update(total=len(lines), pending=len(lines))
        else:
            _log(progress, f'由 {len(net_points)} 个高程点构网，为 {len(lines)} 条等高线定高程…')
            net = tin.Tin(net_points)
            flags = [bool(f) for _, f, _ in lines] if any(f is not None for _, f, _ in lines) else None
            known_levels = {}
            if contour_labels:
                # 高程注记写在计曲线的断口处：取注记两侧最近的线
                grid = geom.Grid(6.0)
                for i, (pts, _, _) in enumerate(lines):
                    for p in (pts[0], pts[-1]):
                        grid.add(p[0], p[1], i)
                estimates = contours.estimate([pts for pts, _, _ in lines], net)
                for x, y, z in contour_labels:
                    for _, i in grid.near(x, y, 4.0)[:2]:
                        # 注记须与该线的内插估计值相符（相差不超过 1.5 m），防止把注记配到旁边的线上
                        if (flags is None or flags[i]) and estimates[i] and abs(estimates[i][0] - z) <= 1.5:
                            known_levels.setdefault(i, z)
            elevations, d, stats = contours.assign([pts for pts, _, _ in lines], net, interval=interval, index_flags=flags,
                                                   known=known_levels, label_values=[z for _, _, z in (contour_labels or [])])
            for (pts, _, _), elev in zip(lines, elevations):
                if elev is None:
                    dxf.add_line(out, '等高线-高程待定', pts)
                else:
                    is_index = abs(elev / (5 * d) - round(elev / (5 * d))) < 1e-6
                    dxf.add_line(out, '等高线-计曲线' if is_index else '等高线-首曲线', pts, elev=elev)
            stats.pop('source', None)
            cinfo.update(total=len(lines), pending=stats['pending'], interval=d, by=stats['by'], labelled=len(known_levels))
            pending_length = sum(geom.length(pts) for (pts, _, _), e in zip(lines, elevations) if e is None)
            total_length = sum(geom.length(pts) for pts, _, _ in lines) or 1
            cinfo['pending_length_share'] = round(pending_length / total_length * 100, 1)
            warnings.append(f'图上的等高线没有逐条标注高程：{len(lines) - stats["pending"]} 条由计曲线、高程注记和相邻关系推定（等高距 {d:g} m），'
                            f'{stats["pending"]} 条定不出（多为短线段，占等高线总长 {cinfo["pending_length_share"]}%），放在“等高线-高程待定”图层，不参与建模。推定结果请在预览中抽查。')
            if flags is None and not known_levels:
                warnings.append('等高线图层里分不出计曲线，也没有高程注记作基准，推定结果的可靠性较低。')
    # 征地线
    land_layers = roles.get('land') or []
    land_lines = [l for name in land_layers for l in drawing['layers'].get(name, {}).get('lines', [])]
    ring = _ring_from_dashes(land_lines) if land_lines else None
    if ring:
        pts, closed, segments = ring
        pts = geom.simplify(pts, 0.05)
        if closed and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        dxf.add_line(out, '征地线', pts, closed=False)
        info['land'] = {'segments': len(segments), 'closed': closed, 'vertices': len(pts), 'length': round(geom.length(pts), 1),
                        'area': round(abs(geom.area(pts[:-1] if closed else pts)), 1)}
        if not closed:
            warnings.append('征地线没有围成闭合环，建模时边坡外缘约束可能不完整。')
    else:
        info['land'] = None
        warnings.append('没有找到征地线：请在图层角色里指定征地线所在的图层。')
    # 场平范围
    ring_pts = ring[0] if ring and ring[1] else None
    platform = _platform(drawing, roles.get('platform') or [], ring_pts)
    info['platform_candidates'] = [{'layer': n, 'vertices': len(p), 'area': round(a, 1), 'closed': e} for n, p, a, e in platform_candidates(drawing, ring_pts)[:6]]
    if platform:
        size, pts, _ = platform
        pts = geom.simplify(pts + [pts[0]], 0.02)
        dxf.add_line(out, '场平范围', pts, closed=False)
        info['platform'] = {'vertices': len(pts) - 1, 'area': round(size, 1), 'perimeter': round(geom.length(pts), 1)}
    else:
        info['platform'] = None
        warnings.append('没有找到场平范围：请在图层角色里指定场平范围（坡顶/坡脚线、围墙线）所在的图层。')
    return out, info, warnings


# ---------- 输入识别
_SCAN_CACHE = {}


def _pdf_kind(path):
    import earthwork_pdf
    pdf = earthwork_pdf.open_pdf(path)
    try:
        pages = survey.classify_pages(pdf)
        if pages['columns']:
            return {'kind': 'survey_pdf', 'pages': len(pdf), 'plan_pages': [p + 1 for p in pages['plan']], 'column_pages': len(pages['columns'])}
        best = None
        for index in range(min(len(pdf), 3)):
            layers = earthwork_pdf.extract_page(pdf, index, layers=())['layers']
            score = sum(count for name, count in layers.items() if ROLE_PATTERNS['contour'].search(name) or ROLE_PATTERNS['point'].search(name))
            if score and (best is None or score > best[1]):
                best = (index, score)
        if best:
            return {'kind': 'layout_pdf', 'pages': len(pdf), 'page': best[0] + 1, 'score': best[1]}
        return {'kind': 'other_pdf', 'pages': len(pdf)}
    finally:
        pdf.close()


def _dxf_kind(path):
    drawing = dxf.read(path)
    roles = guess_roles(drawing)
    hole_ids = sum(1 for c in drawing['layers'].values() for t in c['texts'] if survey.HOLE_ID.match(t['text'].strip()) and 'ZK' in t['text'].upper())
    hole_layer = any(re.search(r'钻孔|勘探点|ZK', name, re.I) for name in drawing['layers'])
    elev = sum(1 for name in roles['contour'] for l in drawing['layers'][name]['lines'] if l.get('elev') is not None)
    points = sum(len(drawing['layers'][name]['points']) for name in roles['point'])
    return {'kind': 'plan_dxf' if (hole_ids >= 3 or hole_layer) else 'base_dxf' if (roles['contour'] or roles['point']) else 'other_dxf',
            'layers': len(drawing['layers']), 'contours_with_elevation': elev, 'points': points, 'hole_labels': hole_ids,
            'roles': roles}


def scan(folder):
    """列出资料文件夹里的 PDF、DXF、CSV，判断各自的用途，并给出两类流程的默认选择。"""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError('资料文件夹不存在')
    files = []
    for path in sorted(folder.iterdir()):
        suffix = path.suffix.lower()
        if not path.is_file() or suffix not in ('.pdf', '.dxf', '.csv', '.dwg'):
            continue
        stamp = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        info = _SCAN_CACHE.get(stamp)
        if info is None:
            try:
                if suffix == '.pdf':
                    info = _pdf_kind(path)
                elif suffix == '.dxf':
                    info = _dxf_kind(path)
                elif suffix == '.dwg':
                    info = {'kind': 'dwg', 'note': 'DWG 需在 CAD 中另存为 DXF'}
                else:
                    text = path.read_text(encoding='utf-8-sig', errors='replace')
                    header = [survey.squeeze(c) for c in text.splitlines()[0].split(',')] if text.strip() else []
                    info = {'kind': 'holes_csv', 'holes': len(survey.parse_holes_csv(text))} if all(c in header for c in survey.CSV_HEADER) else {'kind': 'other_csv'}
            except Exception as exc:   # 单个文件读不了不影响其他文件
                info = {'kind': 'unreadable', 'note': str(exc)[:120]}
            _SCAN_CACHE[stamp] = info
        files.append(dict(info, name=path.name, size=path.stat().st_size))

    def pick(kind, key=None):
        found = [f for f in files if f['kind'] == kind]
        return max(found, key=key)['name'] if found and key else found[0]['name'] if found else ''
    defaults = {'layout_pdf': pick('layout_pdf', lambda f: f['score']), 'survey_pdf': pick('survey_pdf', lambda f: f['column_pages']),
                'base_dxf': pick('base_dxf', lambda f: (f['contours_with_elevation'], f['points'])), 'plan_dxf': pick('plan_dxf', lambda f: f['hole_labels']),
                'holes_csv': pick('holes_csv', lambda f: f['holes'])}
    # 逐项默认：有 DXF、CSV 就直接用（不必转换）；地形图只有 PDF 时，平面图须取自勘察报告 PDF
    terrain = defaults['base_dxf'] or (defaults['layout_pdf'] if defaults['survey_pdf'] else '')
    sources = {'terrain': terrain, 'holes': defaults['holes_csv'] or defaults['survey_pdf'],
               'plan': defaults['survey_pdf'] if terrain.lower().endswith('.pdf') else (defaults['plan_dxf'] or '')}
    candidates = {'terrain': [f['name'] for f in files if f['kind'] in ('base_dxf', 'layout_pdf', 'plan_dxf', 'other_dxf', 'other_pdf')],
                  'holes': [f['name'] for f in files if f['kind'] in ('holes_csv', 'survey_pdf')],
                  'plan': [f['name'] for f in files if f['kind'] in ('plan_dxf', 'survey_pdf', 'base_dxf', 'other_dxf')]}
    return {'folder': str(folder), 'files': files, 'defaults': defaults, 'sources': sources, 'candidates': candidates,
            'ready': bool(sources['terrain'] and sources['holes']), 'output': str(folder / OUTPUT_DIR), 'result': load_report(folder)}


def preview_page(path):
    """预览 PDF 时默认看哪一页（从 1 起）：勘察报告看勘探点平面位置图，图纸看含地形的那一页。"""
    path = Path(path)
    stamp = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    info = _SCAN_CACHE.get(stamp)
    if info is None:
        info = _SCAN_CACHE[stamp] = _pdf_kind(path)
    return (info.get('plan_pages') or [info.get('page') or 1])[0]


def load_report(folder):
    path = Path(folder) / OUTPUT_DIR / REPORT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None


def _read_stroke_labels(page, roles, to_world, reference, progress=None):
    """布置图上的高程注记是笔画而不是文字时：用在勘探点平面图上有注记的点作样本学字形，读出其余注记。
    返回 ([(x, y, z)]（工程坐标）, 统计)。"""
    flat = page_drawing(page)
    spots, seen = [], set()
    for name in roles.get('point') or []:
        for p in flat['layers'].get(name, {}).get('points', []):
            key = (round(p['x'], 3), round(p['y'], 3))
            if key not in seen:
                seen.add(key); spots.append((p['x'], p['y']))
    strokes = [l for name, content in flat['layers'].items() if ROLE_PATTERNS['point'].search(name) or '注记' in name
               for l in content['lines'] if not l.get('fill') and not l.get('strip')]
    if not spots or len(strokes) < 50 or not reference:
        return [], None
    grid = geom.Grid(2.0)
    for x, y, z in reference:
        grid.add(x, y, z)
    known = {}
    for i, p in enumerate(spots):
        hit = grid.nearest(*geom.apply(to_world, p), 0.6)
        if hit:
            known[i] = hit[1]
    _log(progress, f'布置图的高程注记是笔画：用 {len(known)} 个已知高程的注记学习字形，识别其余注记…')
    values, stats = glyphs.read_spot_labels(strokes, spots, known, ELEVATION)
    return [(*geom.apply(to_world, spots[i]), z) for i, z in values.items()], stats


# ---------- 两类清洗流程
def _finish(folder, mode, inputs, base, base_info, warnings, plan_writer, holes, extra, progress):
    out_dir = Path(folder) / OUTPUT_DIR
    out_dir.mkdir(exist_ok=True)
    _log(progress, '写出清洗成果…')
    dxf.write(base, out_dir / BASE_NAME)
    plan_writer(out_dir / PLAN_NAME)
    prefix = extra.pop('east_prefix', '')
    # 带号已在配准时加进钻孔东坐标，这里不再重复加
    (out_dir / HOLES_NAME).write_text(survey.holes_csv(holes), encoding='utf-8-sig')
    problems = survey.check_holes(holes)
    names = {}
    for hole in holes:
        for name, *_ in hole['layers']:
            names[name] = names.get(name, 0) + 1
    report = {'mode': mode, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'inputs': inputs, 'base': base_info, 'warnings': warnings,
              'holes': {'count': len(holes), 'layers': sum(len(h['layers']) for h in holes), 'problems': problems[:40], 'problem_count': len(problems),
                        'strata': names, 'east_prefix': prefix},
              'base_layers': dxf.stats(base), 'outputs': [BASE_NAME, PLAN_NAME, HOLES_NAME], **extra}
    (out_dir / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return report


# 建模必需的数据：每项由哪份资料提供、建模时作什么用
NEEDS = (('contour', '等高线', '地形与场地图', '生成现状地形面'), ('point', '高程点', '地形与场地图', '生成现状地形面、校核等高线'),
         ('land', '征地线', '地形与场地图', '限定边坡和挡墙的外缘'), ('platform', '场平范围', '地形与场地图', '确定场平设计面的范围'),
         ('hole', '钻孔位置', '钻孔资料', '确定各钻孔在平面上的位置'), ('strata', '钻孔分层', '钻孔资料', '插值各土层、岩层的界面'))


def checklist(base_info, holes):
    """逐项核对建模必需的数据是否齐备。"""
    c, pts, land, platform = base_info.get('contours') or {}, base_info.get('points') or {}, base_info.get('land'), base_info.get('platform')
    levelled = (c.get('total') or 0) - (c.get('pending') or 0)
    layered = sum(1 for h in holes if h['layers'])
    state = {
        'contour': (levelled > 0, f'{levelled} 条有高程' + (f'，{c["pending"]} 条待定' if c.get('pending') else '') if c.get('total') else '没有找到'),
        'point': ((pts.get('with_elevation') or 0) >= 3, f'{pts.get("with_elevation") or 0} 个有高程' if pts.get('total') else '没有找到'),
        'land': (bool(land and land['closed']), '已闭合' if land and land['closed'] else '未闭合' if land else '没有找到'),
        'platform': (bool(platform), f'{platform["vertices"]} 个角点，{platform["area"]:,.0f} m²' if platform else '没有找到'),
        'hole': (len(holes) >= 3, f'{len(holes)} 个钻孔'),
        'strata': (layered >= 3, f'{layered} 个钻孔有分层，共 {sum(len(h["layers"]) for h in holes)} 层'),
    }
    # 地形面由等高线和高程点共同生成：两者有其一即可建模，都没有才算缺
    terrain_ok = state['contour'][0] or state['point'][0]
    items = [{'key': key, 'name': name, 'source': source, 'use': use, 'ok': state[key][0], 'detail': state[key][1],
              'required': key not in ('contour', 'point') or not terrain_ok} for key, name, source, use in NEEDS]
    missing = [x['name'] for x in items if not x['ok'] and x['required']]
    return {'items': items, 'missing': missing, 'ready': not missing}


def clean_sources(folder, terrain, holes, plan='', terrain_page=None, east_prefix='', roles=None, interval=None, progress=None):
    """按逐项指定的资料清洗。
    terrain：地形与场地图（含等高线、高程点、征地线、场平范围），DXF 或 PDF；
    holes：钻孔资料，钻孔分层 CSV 或含钻孔柱状图的勘察报告 PDF；
    plan：勘探点平面位置图，DXF 或勘察报告 PDF（取其中的平面图页）。地形图是 PDF 时必需，用来取得工程坐标；地形图是 DXF 时可不给。"""
    folder = Path(folder)
    kind = {name: Path(name).suffix.lower() for name in (terrain, holes, plan) if name}
    if kind.get(terrain) not in ('.dxf', '.pdf'):
        raise ValueError('地形与场地图须为 DXF 或 PDF')
    if kind.get(holes) not in ('.csv', '.pdf'):
        raise ValueError('钻孔资料须为钻孔分层 CSV 或勘察报告 PDF')
    if plan and kind[plan] not in ('.dxf', '.pdf'):
        raise ValueError('勘探点平面位置图须为 DXF 或勘察报告 PDF')
    if kind[terrain] == '.pdf' and kind.get(plan) != '.pdf':
        raise ValueError('地形与场地图是 PDF 时，需要同时指定勘察报告 PDF 作为勘探点平面位置图：PDF 图纸靠平面图上的钻孔坐标取得工程坐标。'
                         '若已有配准好的 DXF，请把地形与场地图改为 DXF。')
    east_prefix = str(east_prefix or '').strip()
    if east_prefix and not re.fullmatch(r'\d{1,3}', east_prefix):
        raise ValueError('东坐标带号应为 1～3 位数字，例如 34')
    if kind[holes] != '.pdf':
        east_prefix = ''          # 带号只用于从钻孔柱状图读出的坐标
    warnings, extra, inputs = [], {}, {'terrain': terrain, 'holes': holes, 'plan': plan}
    opened = {}

    def report_pdf(name):
        import earthwork_pdf
        if name not in opened:
            pdf = earthwork_pdf.open_pdf(folder / name)
            opened[name] = (pdf, survey.classify_pages(pdf))
        return opened[name]
    try:
        # 1. 钻孔资料
        if kind[holes] == '.csv':
            _log(progress, f'读取钻孔分层数据 {holes}…')
            hole_list = survey.parse_holes_csv((folder / holes).read_text(encoding='utf-8-sig', errors='replace'))
            _log(progress, f'读到 {len(hole_list)} 个钻孔。')
        else:
            _log(progress, f'读取勘察报告 {holes}，查找钻孔柱状图…')
            pdf, pages = report_pdf(holes)
            if not pages['columns']:
                raise ValueError('勘察报告中没有找到钻孔柱状图页；如是扫描件（页面是图片，没有可提取的文字），请改为提供钻孔分层数据 CSV')
            hole_list = survey.read_columns(pdf, pages['columns'])
            if not hole_list:
                raise ValueError('钻孔柱状图中没有读到钻孔')
            inputs['column_pages'] = len(pages['columns'])
            _log(progress, f'钻孔柱状图 {len(pages["columns"])} 页，读到 {len(hole_list)} 个钻孔。')
        # 2. 勘探点平面位置图
        plan_page = plan_drawing = None
        transform = None
        if kind.get(plan) == '.pdf':
            import earthwork_pdf
            pdf, pages = report_pdf(plan)
            if not pages['plan']:
                raise ValueError('勘察报告中没有找到勘探点平面位置图页')
            _log(progress, f'提取勘探点平面位置图（第 {pages["plan"][0] + 1} 页）…')
            plan_page = earthwork_pdf.extract_page(pdf, pages['plan'][0])
            inputs['plan_page'] = pages['plan'][0] + 1
            transform, georef = georeference_by_holes(plan_page, hole_list)
            extra['georeference'] = georef
            _log(progress, f'平面图配准：{georef["control_points"]} 个钻孔作控制点，中误差 {georef["rms"]} m，出图比例约 1:{georef["plot_scale"]}。')
            if east_prefix:
                # 带号加在东坐标前：相当于整体东移
                shift = float(east_prefix) * 10 ** (len(str(int(max(h['E'] for h in hole_list)))))
                transform = (transform[0], transform[1], transform[2] + shift, transform[3])
                for hole in hole_list:
                    hole['E'] += shift
            plan_drawing = page_drawing(plan_page, transform, TOLERANCE)
        elif kind.get(plan) == '.dxf':
            _log(progress, f'读取 {plan}…')
            plan_drawing = dxf.read(folder / plan)
        elif east_prefix:
            shift = float(east_prefix) * 10 ** (len(str(int(max(h['E'] for h in hole_list)))))
            for hole in hole_list:
                hole['E'] += shift
        reference, labels = [], []
        if plan_drawing:
            plan_roles = guess_roles(plan_drawing)
            reference = [(x, y, z) for x, y, z, _ in _spots(plan_drawing, plan_roles['point']) if z is not None]
            labels = [(t['x'], t['y'], float(t['text'])) for name in plan_roles['contour'] for t in plan_drawing['layers'][name]['texts'] if CONTOUR_LABEL.match(t['text'].strip())]
            if plan_page is not None:
                _log(progress, f'勘探点平面图上读到 {len(reference)} 个带注记的高程点、{len(labels)} 处等高线高程注记。')
        # 3. 地形与场地图
        recognised = glyph_stats = None
        if kind[terrain] == '.pdf':
            import earthwork_pdf
            _log(progress, f'提取 {terrain}…')
            layout = earthwork_pdf.open_pdf(folder / terrain)
            try:
                index = (terrain_page or _pdf_kind(folder / terrain).get('page', 1)) - 1
                layout_page = earthwork_pdf.extract_page(layout, index)
            finally:
                layout.close()
            if not layout_page['paths']:
                raise ValueError(f'{terrain} 里没有可提取的矢量图形（可能是扫描件）。请提供 CAD 导出的 PDF 或 DXF')
            inputs['terrain_page'] = index + 1
            to_plan, registration = register_pages(layout_page, plan_page)
            extra['registration'] = registration
            _log(progress, f'地形图与勘探点平面图套合：{registration["points"]} 个对应点，残差 {registration["rms_pt"]} 点。')
            to_world = geom.compose(to_plan, transform)
            drawing = page_drawing(layout_page, to_world, 0.0)
        else:
            _log(progress, f'读取 {terrain}…')
            drawing = dxf.read(folder / terrain)
        used_roles = guess_roles(drawing)
        for role, names in (roles or {}).items():
            if role in ROLES and isinstance(names, list):
                used_roles[role] = [n for n in names if n in drawing['layers']]
        if kind[terrain] == '.pdf':
            recognised, glyph_stats = _read_stroke_labels(layout_page, used_roles, to_world, reference, progress)
        base, info, base_warnings = build_base(drawing, used_roles, reference, labels, interval, progress, recognised)
        warnings += base_warnings
        if glyph_stats:
            info['stroke_labels'] = glyph_stats
    finally:
        for pdf, _ in opened.values():
            pdf.close()
    if kind[holes] == '.pdf' and not east_prefix:
        warnings.append('钻孔坐标取自钻孔柱状图表头，东坐标不带带号；如设计图坐标带带号（如 34），请填写“东坐标带号”后重新清洗。')
    # 钻孔应落在地形图范围内：坐标系或带号不一致时提示
    box = dxf.extent(base)
    if box:
        outside = [h['id'] for h in hole_list if not (box[0] - 50 <= h['E'] <= box[2] + 50 and box[1] - 50 <= h['N'] <= box[3] + 50)]
        if outside:
            warnings.append(f'{len(outside)} 个钻孔的坐标不在地形图范围内（如 {"、".join(outside[:5])}），请核对坐标系和东坐标带号是否一致。')
    source = folder / plan if kind.get(plan) == '.dxf' else None

    def write_plan(path):
        if source is not None:
            if source.resolve() != Path(path).resolve():
                shutil.copyfile(source, path)
            return
        # 平面图来自 PDF 时写出配准后的图形；没有平面图时由钻孔坐标生成一张只有钻孔的图
        out = plan_drawing if plan_drawing is not None else dxf.new_drawing('m')
        for hole in hole_list:
            dxf.add_point(out, '钻孔', hole['E'], hole['N'], hole['z'])
            dxf.add_text(out, '钻孔编号', hole['id'], hole['E'] + 1.0, hole['N'] + 1.0, 2.0)
        dxf.write(out, path)
    converted = [name for name in (terrain, plan, holes) if name and kind[name] == '.pdf']
    extra.update(east_prefix=east_prefix, roles=used_roles, layers=layer_summary(drawing), plan_layers=len(plan_drawing['layers']) if plan_drawing else 0,
                 sources={'terrain': {'file': terrain, 'format': kind[terrain][1:]}, 'holes': {'file': holes, 'format': kind[holes][1:]},
                          'plan': {'file': plan, 'format': kind[plan][1:]} if plan else None},
                 converted=sorted(set(converted)), checklist=checklist(info, hole_list))
    return _finish(folder, 'pdf' if converted else 'dxf', inputs, base, info, warnings, write_plan, hole_list, extra, progress)


def clean_pdf(folder, layout_pdf, survey_pdf, layout_page=None, east_prefix='', roles=None, interval=None, progress=None):
    """布置图 PDF + 岩土工程勘察报告 PDF（平面图和钻孔柱状图都取自勘察报告）。"""
    return clean_sources(folder, layout_pdf, survey_pdf, survey_pdf, layout_page, east_prefix, roles, interval, progress)


def clean_dxf(folder, base_dxf, plan_dxf, holes_csv, roles=None, interval=None, progress=None):
    """地形与场地 DXF + 勘探点平面位置图 DXF + 钻孔分层数据 CSV。"""
    return clean_sources(folder, base_dxf, holes_csv, plan_dxf, roles=roles, interval=interval, progress=progress)


# ---------- 预览数据
def preview(path, max_vertices=400000):
    """DXF → 浏览器画布用的精简数据：坐标减去原点、保留 2 位小数；顶点过多时加大抽稀容差。"""
    drawing = dxf.read(path)
    box = dxf.extent(drawing)
    if not box:
        raise ValueError('图形为空')
    ox, oy = box[0], box[1]
    total = sum(len(l['pts']) for c in drawing['layers'].values() for l in c['lines'])
    tolerance = 0.0 if total <= max_vertices else max(box[2] - box[0], box[3] - box[1]) / 4000 * min(8.0, total / max_vertices)
    layers = []
    for name, content in drawing['layers'].items():
        lines = []
        for line in content['lines']:
            pts = geom.simplify(line['pts'], tolerance) if tolerance else line['pts']
            flat = [round(v, 2) for x, y in pts for v in (x - ox, y - oy)]
            lines.append({'p': flat, 'c': 1 if line.get('closed') else 0, **({'z': line['elev']} if line.get('elev') is not None else {})})
        points = [[round(p['x'] - ox, 2), round(p['y'] - oy, 2), p.get('z'), p.get('label') or ''] for p in content['points'] if not p.get('r')]
        texts = [[round(t['x'] - ox, 2), round(t['y'] - oy, 2), t['text'][:40], round(t['h'], 2), round(t.get('angle') or 0, 3)] for t in content['texts'][:6000]]
        if lines or points or texts:
            layers.append({'name': name, 'lines': lines, 'points': points, 'texts': texts})
    return {'origin': [ox, oy], 'size': [round(box[2] - ox, 2), round(box[3] - oy, 2)], 'layers': layers, 'simplified': bool(tolerance)}
