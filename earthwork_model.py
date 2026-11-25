"""土石方三维建模：由资料清洗成果（三维建模基础数据.dxf、钻孔分层数据.csv）生成计算引擎和三维场景用的格网模型。纯 Python，本机计算。

地形：等高线（沿线加密取点）和高程点一起构不规则三角网，重采样为规则格网 DEM；三角网以外的格点取最近的已知格点。
地层：每个钻孔取耕植土厚度、土岩界面深度，用反距离加权（幂 2）插值到格网，并做留一交叉验证；
强风化岩按夹层（透镜体）处理，只在揭露它的钻孔周围一定半径内存在；中风化岩只在揭露它的钻孔附近给出顶面。
场平范围内的格点标记为 inside，其余格点记录到场平边线的距离。"""
import json
import math
import re
import time
from pathlib import Path

import earthwork_clean as clean
import earthwork_dxf as dxf
import earthwork_geom as geom
import earthwork_survey as survey
import earthwork_tin as tin

MODEL_NAME = 'model.json'
MAX_CELLS = 60000
CONTOUR_STEP = 3.0        # 等高线上取点的间距（m）
STEPS = ('读取清洗成果', '构建地形三角网', '重采样格网地形', '整理钻孔分层', '插值地层界面', '场平范围与校核', '写出模型')
LENS_RADIUS = 12.0        # 强风化夹层在揭露它的钻孔周围的延伸半径（m）
DEEP_RADIUS = 19.0        # 中风化岩顶面在揭露它的钻孔周围的影响半径（m）
# 岩土名称 → 计算引擎的地层单元。先匹配风化程度，再匹配土类
UNIT_RULES = (('w2', re.compile(r'中等?风化|微风化|未风化|新鲜')), ('w3', re.compile(r'强风化')), ('w4', re.compile(r'全风化|残积')),
              ('soil', re.compile(r'耕植?土|耕土|种植土|表土|植物层|素填土|杂填土|填土')), ('clay', re.compile(r'粘土|黏土|粉土|粉质|砂土|粉砂|细砂|中砂|粗砂|砾|卵石|碎石土|淤泥|红土|黄土|土$')))
UNIT_NAMES = {'soil': '耕植土', 'clay': '粘土', 'w4': '全风化岩', 'w3': '强风化岩', 'w2': '中风化岩'}
ROCK = ('w4', 'w3', 'w2')


def classify(name):
    for unit, pattern in UNIT_RULES:
        if pattern.search(name):
            return unit
    return None


def hole_segments(hole):
    """钻孔分层 → [(单元, 层顶深度, 层底深度)]，相邻同单元合并。认不出的岩土名称：在岩层之上按粘土、之下按全风化岩，并记入提示。"""
    segs, unknown, in_rock = [], [], False
    for name, top, bottom, _ in hole['layers']:
        unit = classify(name)
        if unit is None:
            unknown.append(name)
            unit = 'w4' if in_rock else 'clay'
        # 表层以下再出现的“耕植土/填土”类按一般土层处理
        if unit == 'soil' and segs and segs[-1][0] != 'soil':
            unit = 'clay'
        in_rock = in_rock or unit in ROCK
        if segs and segs[-1][0] == unit:
            segs[-1] = (unit, segs[-1][1], bottom)
        else:
            segs.append((unit, top, bottom))
    return segs, unknown


def idw(samples, x, y, power=2):
    total = weight = 0.0
    for sx, sy, value in samples:
        d2 = (sx - x) ** 2 + (sy - y) ** 2
        if d2 < 1e-6:
            return value
        w = 1.0 / d2 if power == 2 else d2 ** (-power / 2)
        total += w * value; weight += w
    return total / weight


def cross_validate(samples):
    """留一交叉验证：返回 (RMSE, 平均绝对误差)。"""
    if len(samples) < 3:
        return None
    errors = [idw(samples[:i] + samples[i + 1:], s[0], s[1]) - s[2] for i, s in enumerate(samples)]
    return round(math.sqrt(sum(e * e for e in errors) / len(errors)), 2), round(sum(abs(e) for e in errors) / len(errors), 2)


def _distance_to_ring(ring, x, y):
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        vx, vy = x2 - x1, y2 - y1
        length = vx * vx + vy * vy
        u = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / length)) if length else 0.0
        d = math.hypot(x1 + u * vx - x, y1 + u * vy - y)
        if d < best:
            best = d
    return best


def build(folder, dx=2.0, progress=None, on_step=None):
    """读取资料文件夹下的清洗成果，生成 model.json。返回统计信息。on_step(序号) 在进入 STEPS 的每一步时调用。"""
    out_dir = Path(folder) / clean.OUTPUT_DIR
    base_path, holes_path = out_dir / clean.BASE_NAME, out_dir / clean.HOLES_NAME
    if not base_path.is_file() or not holes_path.is_file():
        raise ValueError('没有找到清洗成果，请先完成资料清洗')
    log = progress or (lambda text: None)
    step = on_step or (lambda index: None)
    step(0)
    log('读取三维建模基础数据和钻孔分层…')
    base = dxf.read(base_path)
    holes = survey.parse_holes_csv(holes_path.read_text(encoding='utf-8-sig'))
    layers = base['layers']
    contour_lines = [l for name in ('等高线-计曲线', '等高线-首曲线') for l in layers.get(name, {}).get('lines', []) if l.get('elev') is not None]
    spots = [(p['x'], p['y'], p['z']) for p in layers.get('高程点', {}).get('points', []) if p.get('z') is not None]
    platform_lines = layers.get('场平范围', {}).get('lines', [])
    land_lines = layers.get('征地线', {}).get('lines', [])
    if not platform_lines:
        raise ValueError('清洗成果里没有场平范围，无法建模')
    if not land_lines:
        raise ValueError('清洗成果里没有征地线，无法建模')
    if len(spots) + len(contour_lines) < 3:
        raise ValueError('清洗成果里没有可用的等高线和高程点，无法建模')
    if len(holes) < 3:
        raise ValueError('钻孔不足 3 个，无法插值地层')
    platform = geom.dedupe(platform_lines[0]['pts'])
    if math.hypot(platform[0][0] - platform[-1][0], platform[0][1] - platform[-1][1]) < 1e-6:
        platform = platform[:-1]
    land = geom.dedupe(land_lines[0]['pts'])

    # ---------- 地形三角网与格网范围
    terrain = list(spots)
    for line in contour_lines:
        pts = line['pts']
        for x, y in (geom.resample(pts, CONTOUR_STEP) if geom.length(pts) > CONTOUR_STEP else pts):
            terrain.append((x, y, line['elev']))
    xs = [p[0] for p in terrain] + [p[0] for p in land]; ys = [p[1] for p in terrain] + [p[1] for p in land]
    while True:
        x0, y0 = math.floor(min(xs) / dx) * dx, math.floor(min(ys) / dx) * dx
        nx, ny = int(math.ceil((max(xs) - x0) / dx)) + 1, int(math.ceil((max(ys) - y0) / dx)) + 1
        if nx * ny <= MAX_CELLS:
            break
        dx *= 1.25
    dx = round(dx, 3)
    count = nx * ny
    log(f'由 {len(spots)} 个高程点和 {len(contour_lines)} 条等高线（取 {len(terrain) - len(spots)} 个点）构三角网，重采样为 {nx}×{ny}、间距 {dx:g} m 的格网…')
    step(1)
    net = tin.Tin(terrain)
    step(2)
    dem = [None] * count
    for j in range(ny):
        y = y0 + j * dx
        for i in range(nx):
            dem[j * nx + i] = net.z(x0 + i * dx, y)
    outside = sum(1 for v in dem if v is None)
    # 三角网以外的格点：由已知格点逐圈向外取相邻已知值的平均
    frontier = [k for k in range(count) if dem[k] is None]
    while frontier:
        updates, rest = {}, []
        for k in frontier:
            i, j = k % nx, k // nx
            near = [dem[jj * nx + ii] for ii, jj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1), (i - 1, j - 1), (i + 1, j + 1), (i - 1, j + 1), (i + 1, j - 1))
                    if 0 <= ii < nx and 0 <= jj < ny and dem[jj * nx + ii] is not None]
            if near:
                updates[k] = sum(near) / len(near)
            else:
                rest.append(k)
        if not updates:
            raise ValueError('地形数据范围过小，无法生成格网')
        for k, v in updates.items():
            dem[k] = v
        frontier = rest

    def dem_at(east, north):
        fi, fj = (east - x0) / dx, (north - y0) / dx
        i, j = max(0, min(nx - 2, int(fi))), max(0, min(ny - 2, int(fj)))
        u, v = min(1, max(0, fi - i)), min(1, max(0, fj - j))
        k = j * nx + i
        return dem[k] * (1 - u) * (1 - v) + dem[k + 1] * u * (1 - v) + dem[k + nx] * (1 - u) * v + dem[k + nx + 1] * u * v

    # ---------- 钻孔分层
    step(3)
    log(f'整理 {len(holes)} 个钻孔的分层，插值地层界面…')
    model_holes, soil_samples, rock_samples, lens_holes, deep_holes, unknown_names, warnings = [], [], [], [], [], {}, []
    for hole in holes:
        segs, unknown = hole_segments(hole)
        for name in unknown:
            unknown_names[name] = unknown_names.get(name, 0) + 1
        if not segs:
            continue
        model_holes.append({'id': hole['id'], 'E': hole['E'], 'N': hole['N'], 'z': hole['z'], 'segs': [[u, round(a, 3), round(b, 3)] for u, a, b in segs], 'water': hole['water']})
        soil = segs[0][2] - segs[0][1] if segs[0][0] == 'soil' else 0.0
        soil_samples.append((hole['E'], hole['N'], soil))
        rock = next((a for u, a, b in segs if u in ROCK), None)
        if rock is None:
            continue           # 终孔仍在土层：土岩界面深度未知，不参与界面插值
        rock_samples.append((hole['E'], hole['N'], rock - soil))
        lens = [(a, b) for u, a, b in segs if u == 'w3']
        if lens:
            lens_holes.append((hole['E'], hole['N'], lens[0][0] - rock, sum(b - a for a, b in lens)))
        deep = next((a for u, a, b in segs if u == 'w2'), None)
        if deep is not None:
            deep_holes.append((hole['E'], hole['N'], deep - rock))
    if len(rock_samples) < 3:
        raise ValueError('揭露到风化岩的钻孔不足 3 个，无法插值土岩界面')
    if unknown_names:
        warnings.append('认不出的岩土名称：' + '、'.join(f'{n}（{c} 层）' for n, c in unknown_names.items()) + '；在岩层之上的按粘土、之下的按全风化岩计入，请核对。')
    no_rock = len(model_holes) - len(rock_samples)
    if no_rock:
        warnings.append(f'{no_rock} 个钻孔终孔仍在土层，未参与土岩界面插值，该处岩面深度为周围钻孔推测。')
    step(4)
    tsoil, tclay, t3top, t3th, i2 = [0.0] * count, [0.0] * count, [0.0] * count, [0.0] * count, [0.0] * count
    lens_top = [(x, y, top) for x, y, top, _ in lens_holes]
    for j in range(ny):
        y = y0 + j * dx
        for i in range(nx):
            x, k = x0 + i * dx, j * nx + i
            tsoil[k] = round(idw(soil_samples, x, y), 2)
            tclay[k] = round(max(idw(rock_samples, x, y), 0.0), 2)
            if lens_holes:
                t3top[k] = round(max(idw(lens_top, x, y), 0.0), 2)
                best = 0.0
                for hx, hy, _, thick in lens_holes:
                    d = math.hypot(hx - x, hy - y)
                    if d < LENS_RADIUS:
                        best = max(best, thick * (1 - (d / LENS_RADIUS) ** 2))
                t3th[k] = round(best, 2)
            for hx, hy, _ in deep_holes:
                d = math.hypot(hx - x, hy - y)
                if d < DEEP_RADIUS:
                    i2[k] = max(i2[k], round(1 - d / DEEP_RADIUS, 2))
    w2top = round(sum(d for _, _, d in deep_holes) / len(deep_holes), 2) if deep_holes else 0.0

    # ---------- 场平范围
    step(5)
    inside, dist = [0] * count, [0.0] * count
    box = geom.bbox(platform)
    for j in range(ny):
        y = y0 + j * dx
        for i in range(nx):
            x, k = x0 + i * dx, j * nx + i
            if box[0] <= x <= box[2] and box[1] <= y <= box[3] and geom.inside(platform, x, y):
                inside[k] = 1
            else:
                dist[k] = round(_distance_to_ring(platform, x, y), 2)
    cells_inside = sum(inside)
    if cells_inside < 10:
        raise ValueError('场平范围内的格点太少，请检查场平范围与地形数据是否在同一坐标系')

    # ---------- 校核：钻孔孔口高程与地形面
    diffs = [(h['id'], round(h['z'] - dem_at(h['E'], h['N']), 2)) for h in model_holes if x0 <= h['E'] <= x0 + (nx - 1) * dx and y0 <= h['N'] <= y0 + (ny - 1) * dx]
    mean_abs = round(sum(abs(d) for _, d in diffs) / len(diffs), 3) if diffs else None
    off = [d for d in diffs if abs(d[1]) > 0.5]
    if len(diffs) < len(model_holes):
        warnings.append(f'{len(model_holes) - len(diffs)} 个钻孔不在地形格网范围内。')
    if off:
        warnings.append(f'{len(off)} 个钻孔孔口高程与地形面相差超过 0.5 m：' + '、'.join(f'{i}（{d:+.2f}）' for i, d in off[:8]) + '。')
    cv = {}
    for name, samples in (('耕植土厚', soil_samples), ('粘土厚(到岩面孔)', rock_samples)):
        result = cross_validate(samples)
        if result:
            cv[name] = ['IDW p2', {'IDW p2': list(result)}]
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    r2 = lambda v: round(v, 2)
    model = {
        'origin': [round(centre[0]), round(centre[1])], 'dx': dx, 'x0': x0, 'y0': y0, 'nx': nx, 'ny': ny,
        'dem': [r2(v) for v in dem], 'tsoil': tsoil, 'tclay': tclay, 't3top': t3top, 't3th': t3th, 'i2': i2, 'w2top': w2top, 'inside': inside, 'dist': dist,
        'platform': [[r2(x), r2(y)] for x, y in platform + [platform[0]]],
        'land': [[[r2(x), r2(y)] for x, y in land]],
        'contours': [{'z': l['elev'], 'p': [[r2(x), r2(y)] for x, y in geom.simplify(l['pts'], 0.1)]} for l in contour_lines],
        'holes': model_holes, 'cv': cv,
    }
    stats = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'grid': f'{nx}×{ny}', 'dx': dx, 'cells': count, 'outside_tin': outside,
             'terrain_points': len(terrain), 'spots': len(spots), 'contours': len(contour_lines),
             'dem_range': [r2(min(dem)), r2(max(dem))], 'holes': len(model_holes), 'rock_holes': len(rock_samples), 'lens_holes': len(lens_holes), 'deep_holes': len(deep_holes),
             'collar_mean_abs': mean_abs, 'collar_max': max((abs(d) for _, d in diffs), default=None),
             'cv': {name: item[1]['IDW p2'] for name, item in cv.items()}, 'platform_area': round(abs(geom.area(platform)), 1), 'cells_inside': cells_inside,
             'units': {UNIT_NAMES[u]: sum(1 for h in model_holes if any(s[0] == u for s in h['segs'])) for u in UNIT_NAMES}, 'warnings': warnings}
    step(6)
    model['build'] = stats
    (out_dir / MODEL_NAME).write_text(json.dumps(model, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    log('三维模型已生成。')
    return stats
