"""土石方资料清洗：几何与三角网、等高线定高程、DXF 读写、钻孔柱状图解析、笔画注记识别、第 2 类流程和接口的文件校验。
第 1 类（PDF）全流程依赖真实图纸，样例资料在本机时才运行。"""
import copy
import json
import math
import re
import tempfile
import time
import unittest
from pathlib import Path

import earthwork_clean as clean
import earthwork_contours as contours
import earthwork_dxf as dxf
import earthwork_geom as geom
import earthwork_glyphs as glyphs
import earthwork_survey as survey
import earthwork_tin as tin

SAMPLE = Path(r'D:\场平标高三维比选\data')


def plane(x, y):
    """测试地形：向东北升高的斜面。"""
    return 100 + 0.05 * x + 0.03 * y


def plane_contour(level, x0=0.0, x1=200.0, step=5.0):
    return [(x, (level - 100 - 0.05 * x) / 0.03) for x in [x0 + i * step for i in range(int((x1 - x0) / step) + 1)]]


class GeometryTests(unittest.TestCase):
    def test_similarity_fit_recovers_scale_rotation_and_rejects_outliers(self):
        truth = (0.3 * math.cos(0.4), 0.3 * math.sin(0.4), 500000.0, 2600000.0)
        src = [(x * 37.0 % 900, x * 53.0 % 700) for x in range(1, 40)]
        dst = [geom.apply(truth, p) for p in src]
        dst[5] = (dst[5][0] + 30, dst[5][1] - 20)        # 一个错配的控制点
        fitted, keep, res = geom.robust_similarity(src, dst)
        self.assertNotIn(5, keep)
        self.assertAlmostEqual(geom.scale_of(fitted), 0.3, places=6)
        self.assertLess(max(res), 1e-6)
        back = geom.apply(geom.invert(fitted), geom.apply(fitted, (12.0, 34.0)))
        self.assertAlmostEqual(back[0], 12.0, places=6)
        self.assertEqual(tuple(round(v, 6) for v in geom.apply(geom.compose(fitted, geom.invert(fitted)), (7.0, 9.0))), (7.0, 9.0))

    def test_triangle_strip_gives_centreline(self):
        # 宽 2 的折线剖成三角形条带：顶点在两侧交替
        centre = [(0, 0), (10, 0), (20, 5), (30, 5)]
        left = [(x, y + 1) for x, y in centre]; right = [(x, y - 1) for x, y in centre]
        verts = [p for pair in zip(left, right) for p in pair]
        triangles = [[verts[i], verts[i + 1], verts[i + 2]] for i in range(len(verts) - 2)]
        line = geom.strip_centerline(triangles)
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y in line], [(float(x), float(y)) for x, y in centre])
        path = {'subs': [t + [t[0]] for t in triangles], 'closed': [True] * len(triangles)}
        self.assertEqual(len(geom.path_triangles(path)), len(triangles))
        self.assertIsNone(geom.path_triangles({'subs': [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]], 'closed': [True]}))

    def test_chain_joins_pieces_and_detects_rings(self):
        pieces = [[(0, 0), (1, 0)], [(2, 0.02), (1.02, 0)], [(2, 0), (2, 1)], [(10, 10), (11, 10)]]
        chains = sorted(geom.chain(pieces, 0.1), key=lambda c: -len(c[0]))
        self.assertEqual((len(chains), len(chains[0][0]), chains[0][1]), (2, 6, False))
        square = [[(0, 0), (4, 0)], [(4, 0), (4, 4)], [(4, 4), (0, 4)], [(0, 4), (0, 0)]]
        ring, closed = geom.chain(square, 0.01)[0]
        self.assertTrue(closed)
        self.assertAlmostEqual(abs(geom.area(ring[:-1])), 16)
        self.assertEqual(geom.simplify([(0, 0), (1, 0.001), (2, 0), (3, 2)], 0.01), [(0, 0), (2, 0), (3, 2)])
        self.assertTrue(geom.inside(ring[:-1], 2, 2)); self.assertFalse(geom.inside(ring[:-1], 5, 2))

    def test_tin_interpolates_linearly_and_reports_outside(self):
        pts = [(x * 10.0 + (y % 2) * 3, y * 10.0, plane(x * 10.0 + (y % 2) * 3, y * 10.0)) for x in range(8) for y in range(8)]
        net = tin.Tin([(x + 34571000, y + 2685000, z) for x, y, z in pts])      # 大坐标下仍准确
        self.assertAlmostEqual(net.z(34571033.3, 2685041.7), plane(33.3, 41.7), places=6)
        self.assertIsNone(net.z(34571500, 2685000))
        with self.assertRaises(ValueError):
            tin.Tin([(0, 0, 1), (1, 1, 2)])


class ContourTests(unittest.TestCase):
    def setUp(self):
        # 高程点带一点噪声：内插估计值离真值有几厘米到十几厘米，单靠取整会出错
        self.spots = [(x, y, plane(x, y) + (((x * 7 + y * 13) % 11) - 5) * 0.04) for x in range(-20, 221, 12) for y in range(-200, 201, 12)]
        self.net = tin.Tin(self.spots)
        self.levels = [100.5 + 0.5 * k for k in range(18)]
        self.lines = [plane_contour(z) for z in self.levels]
        self.index = [abs(z / 2.5 - round(z / 2.5)) < 1e-9 for z in self.levels]

    def test_levels_follow_from_index_contours_and_order(self):
        got, interval, stats = contours.assign(self.lines, self.net, index_flags=self.index)
        self.assertEqual(interval, 0.5)
        self.assertEqual(got, self.levels)
        self.assertEqual(stats['pending'], 0)

    def test_label_fixes_level_and_interval(self):
        got, interval, _ = contours.assign(self.lines, self.net, index_flags=self.index, known={self.index.index(True): 102.5}, label_values=[102.5, 105.0])
        self.assertEqual((interval, got), (0.5, self.levels))

    def test_line_outside_points_without_neighbours_stays_pending(self):
        far = [(5000 + i, 5000.0) for i in range(30)]
        got, _, stats = contours.assign(self.lines + [far], self.net, index_flags=self.index + [False])
        self.assertIsNone(got[-1])
        self.assertEqual(stats['pending'], 1)


class DxfTests(unittest.TestCase):
    def test_round_trip_keeps_layers_elevation_text_and_points(self):
        drawing = dxf.new_drawing()
        dxf.add_line(drawing, '等高线-计曲线', [(34571000.5, 2685000.25), (34571010.0, 2685003.0), (34571020.0, 2685001.0)], elev=1842.5)
        dxf.add_line(drawing, '场平范围', [(0, 0), (10, 0), (10, 5), (0, 5)], closed=True)
        dxf.add_point(drawing, '高程点', 34571005.0, 2685002.0, 1838.15)
        dxf.add_text(drawing, '高程注记', '1838.15 标高', 34571005.5, 2685002.0, 1.0, math.radians(30))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '成果.dxf'
            dxf.write(drawing, path)
            raw = path.read_bytes()
            self.assertNotRegex(raw.decode('utf-8'), r'[^\x00-\x7f]')        # 中文一律转义，老版本 CAD 也能读
            back = dxf.read(path)
        line = back['layers']['等高线-计曲线']['lines'][0]
        self.assertEqual((line['elev'], line['closed'], line['pts'][0]), (1842.5, False, (34571000.5, 2685000.25)))
        self.assertTrue(back['layers']['场平范围']['lines'][0]['closed'])
        self.assertEqual(back['layers']['高程点']['points'][0]['z'], 1838.15)
        text = back['layers']['高程注记']['texts'][0]
        self.assertEqual((text['text'], round(math.degrees(text['angle']))), ('1838.15 标高', 30))
        self.assertEqual(dxf.extent(back)[:2], (0, 0))

    def test_reads_old_polyline_hatch_boundary_and_rejects_binary(self):
        body = ('0\nSECTION\n2\nENTITIES\n'
                '0\nPOLYLINE\n8\nDGX\n66\n1\n70\n0\n0\nVERTEX\n8\nDGX\n10\n1\n20\n2\n30\n55.5\n0\nVERTEX\n8\nDGX\n10\n3\n20\n4\n30\n55.5\n0\nSEQEND\n'
                '0\nHATCH\n8\nYD\n91\n1\n92\n2\n72\n0\n73\n1\n93\n3\n10\n0\n20\n0\n10\n2\n20\n0\n10\n2\n20\n1\n97\n0\n'
                '0\nLINE\n8\nL\n10\n0\n20\n0\n11\n5\n21\n5\n0\nINSERT\n8\nGCD\n2\nGC200\n10\n7\n20\n8\n30\n12.3\n'
                '0\nMTEXT\n8\nT\n10\n1\n20\n1\n40\n2.5\n1\n{\\fSimSun;XZK12}\\P1845.20\n0\nENDSEC\n0\nEOF\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.dxf'
            path.write_text(body, encoding='gbk')
            d = dxf.read(path)
            (Path(folder) / 'b.dxf').write_bytes(b'AutoCAD Binary DXF\r\n\x1a\x00')
            with self.assertRaisesRegex(ValueError, '二进制'):
                dxf.read(Path(folder) / 'b.dxf')
        self.assertEqual((d['layers']['DGX']['lines'][0]['elev'], d['layers']['DGX']['lines'][0]['pts']), (55.5, [(1.0, 2.0), (3.0, 4.0)]))
        self.assertEqual((len(d['layers']['YD']['lines'][0]['pts']), d['layers']['YD']['lines'][0]['fill']), (3, True))
        self.assertEqual((d['layers']['GCD']['points'][0]['z'], d['layers']['GCD']['points'][0]['label']), (12.3, 'GC200'))
        self.assertEqual(d['layers']['T']['texts'][0]['text'], 'XZK12\n1845.20')


def column_block(dx, hole, collar, east, north, water, rows):
    """钻孔柱状图一栏的文字（位置按真实报告的版式）：表头在上，分层表各列竖排标题在中，数值在下。"""
    def t(text, x, y, w=20, h=8):
        return {'text': text, 'box': (dx + x, y, dx + x + w, y + h), 'x': dx + x, 'y': y, 'layer': '', 'angle': 0, 'size': 8}
    texts = [t('钻 孔 柱 状 图', 266, 790, 112), t('钻孔编号', 335, 725, 37), t(hole, 390, 727), t('孔口高程(m)', 84, 700, 51), t(f'{collar:.2f}', 153, 700, 33),
             t(f'X={east:.3f}', 248, 700, 58), t(f'Y={north:.3f}', 246, 675, 62), t('稳定水位深度(m)', 451, 699, 70), t(water, 539, 700)]
    for chars, x in (('地层名称', 113), ('层底高程', 173), ('层底深度', 200), ('分层厚度', 227)):
        for i, ch in enumerate(chars):
            texts.append(t(ch, x, 642 - i * 12, 8))
        if chars != '地层名称':
            texts.append(t('(m)', x, 602, 9))
    y = 583
    for name, depth in rows:
        texts += [t(name, 105, y - 6, 26), t(f'{collar - depth:.2f}', 167, y, 22), t(f'{depth:.2f}', 198, y, 12)]
        y -= 40
    return texts


class SurveyTests(unittest.TestCase):
    def test_column_page_with_two_holes_merges_sublayers(self):
        page = {'size': (1190, 842), 'texts': column_block(0, 'WZK1', 1844.33, 571158.026, 2685839.248, '10.90', [('耕植土', 0.4), ('粘土', 1.3), ('粘土', 4.1), ('全风化砂岩', 15.4)])
                + column_block(595, 'XZK2', 1842.4, 571206.527, 2685849.531, '8.60', [('耕植土', 0.4), ('粘土', 4.7), ('全风化砂岩', 12.6)])}
        holes = survey.parse_column_page(page)
        self.assertEqual([(h['id'], h['E'], h['N'], h['z'], h['water']) for h in holes],
                         [('WZK1', 571158.026, 2685839.248, 1844.33, '10.90'), ('XZK2', 571206.527, 2685849.531, 1842.4, '8.60')])
        self.assertEqual(holes[0]['layers'], [('耕植土', 0.0, 0.4, 1843.93), ('粘土', 0.4, 4.1, 1840.23), ('全风化砂岩', 4.1, 15.4, 1828.93)])
        self.assertEqual(survey.check_holes(holes), [])
        text = survey.holes_csv(holes, '34')
        self.assertIn('WZK1,34571158.026,2685839.248,1844.33,10.90,粘土,0.4,4.1,1840.23', text)
        back = survey.parse_holes_csv(text)
        self.assertEqual((back[1]['id'], back[1]['E'], back[1]['layers'][-1]), ('XZK2', 34571206.527, ('全风化砂岩', 4.7, 12.6, 1829.8)))

    def test_csv_validation_and_checks(self):
        with self.assertRaisesRegex(ValueError, '缺少列'):
            survey.parse_holes_csv('钻孔编号,东坐标E\nA1,1\n')
        with self.assertRaisesRegex(ValueError, '第 3 行'):
            survey.parse_holes_csv(','.join(survey.CSV_HEADER) + '\nZK1,1,2,100,3.0,粘土,0,2,98\nZK1,1,2,100,3.0,砂岩,2,x,90\n')
        holes = survey.parse_holes_csv(','.join(survey.CSV_HEADER) + '\nZK1,1,2,100,3.0,粘土,0,2,98\nZK1,1,2,100,3.0,砂岩,2.5,6,95\n')
        problems = survey.check_holes(holes)
        self.assertTrue(any('不衔接' in p for p in problems))
        self.assertTrue(any('相差 1.00 m' in p for p in problems))


class GeoreferenceTests(unittest.TestCase):
    def test_plan_is_georeferenced_from_hole_symbols_not_label_positions(self):
        truth = (0.3528, 0.0, 571000.0, 2685600.0)
        holes, texts, paths = [], [], []
        for i in range(12):
            px, py = 100 + (i % 4) * 150 + (i * 7 % 11), 120 + (i // 4) * 180 + (i * 5 % 13)
            east, north = geom.apply(truth, (px, py))
            holes.append({'id': f'XZK{i + 1}', 'E': east, 'N': north, 'z': 1840.0, 'layers': []})
            # 编号文字在符号左上方、斜着写；符号是一个小圆
            texts.append({'text': f'XZK{i + 1}', 'x': px - 14, 'y': py + 12, 'layer': '地质-钻孔', 'angle': -0.5, 'size': 8, 'box': (px - 14, py + 5, px + 3, py + 19)})
            ring = [(px + 5 * math.cos(a / 8 * math.pi), py + 5 * math.sin(a / 8 * math.pi)) for a in range(17)]
            paths.append({'layer': '地质-钻孔', 'subs': [ring], 'closed': [True], 'fill': True, 'stroke': True, 'width': 0, 'color': (0, 0, 0)})
        transform, info = clean.georeference_by_holes({'texts': texts, 'paths': paths, 'size': (800, 800)}, holes)
        self.assertEqual((info['by'], info['control_points']), ('symbol', 12))
        self.assertLess(info['rms'], 0.01)
        self.assertEqual(info['plot_scale'], 1000)
        with self.assertRaisesRegex(ValueError, '无法配准'):
            clean.georeference_by_holes({'texts': texts[:2], 'paths': paths, 'size': (800, 800)}, holes)


# 单线字体的数字笔画（示意）：每个字一条折线
STROKES = {'0': [(0, 0), (4, 0), (4, 8), (0, 8), (0, 0)], '1': [(1, 6), (2, 8), (2, 0)], '2': [(0, 8), (4, 8), (4, 4), (0, 4), (0, 0), (4, 0)],
           '3': [(0, 8), (4, 8), (4, 4), (1, 4), (4, 4), (4, 0), (0, 0)], '4': [(3, 0), (3, 8), (0, 3), (4, 3)], '5': [(4, 8), (0, 8), (0, 4), (4, 4), (4, 0), (0, 0)],
           '6': [(4, 8), (0, 8), (0, 0), (4, 0), (4, 4), (0, 4)], '7': [(0, 8), (4, 8), (1, 0)], '8': [(0, 4), (0, 8), (4, 8), (4, 0), (0, 0), (0, 4), (4, 4)],
           '9': [(4, 4), (0, 4), (0, 8), (4, 8), (4, 0), (0, 0)], '.': [(0.5, 0), (0.9, 0), (0.9, 0.4), (0.5, 0.4), (0.5, 0)]}


def stroke_label(text, x, y, jitter=0):
    lines = []
    for k, ch in enumerate(text):
        offset = ((k * 3 + jitter) % 3 - 1) * 0.03
        lines.append({'pts': [(x + k * 5.5 + px + offset, y + py + offset) for px, py in STROKES[ch]], 'fill': False, 'strip': False})
    return lines


class GlyphTests(unittest.TestCase):
    def test_reads_unknown_labels_after_learning_from_known_ones(self):
        values = [1800 + ((i * 37) % 6000) / 100 for i in range(60)]
        spots = [(100.0 + (i % 10) * 90, 50.0 + (i // 10) * 40) for i in range(60)]
        lines = [l for i, (v, (x, y)) in enumerate(zip(values, spots)) for l in stroke_label(f'{v:.2f}', x + 3, y - 4, i)]
        known = {i: values[i] for i in range(40)}
        got, stats = glyphs.read_spot_labels(lines, spots, known, clean.ELEVATION)
        self.assertEqual(stats['check_failed'], 0)
        self.assertEqual(stats['characters'], '.0123456789')
        self.assertEqual({i: round(v, 2) for i, v in got.items()}, {i: round(values[i], 2) for i in range(40, 60)})

    def test_too_few_samples_or_cropped_labels_are_not_read(self):
        spots = [(100.0 + i * 90, 50.0) for i in range(30)]
        lines = [l for i, (x, y) in enumerate(spots) for l in stroke_label('1843.21', x + 3, y - 4)]
        self.assertEqual(glyphs.read_spot_labels(lines, spots, {0: 1843.21}, clean.ELEVATION)[0], {})
        values = [1800 + ((i * 53) % 5000) / 100 for i in range(40)]
        spots = [(100.0 + (i % 10) * 90, 50.0 + (i // 10) * 40) for i in range(41)]
        lines = [l for v, (x, y) in zip(values, spots) for l in stroke_label(f'{v:.2f}', x + 3, y - 4)] + stroke_label('1801.4', spots[40][0] + 3, spots[40][1] - 4)
        got, _ = glyphs.read_spot_labels(lines, spots, {i: values[i] for i in range(40)}, clean.ELEVATION)
        self.assertEqual(got, {})          # 被图框裁掉一位小数的注记不采用


def write_base_dxf(path, with_elevation=True):
    """示意基础图：斜面地形的等高线和高程点、虚线征地线（小四边形）、场地边线和稍大一圈的坡顶线、图框。"""
    d = dxf.new_drawing()
    for k in range(14):
        z = 101.0 + 0.5 * k
        dxf.add_line(d, 'DGX', plane_contour(z, 0, 200), elev=z if with_elevation else None)
    for x in range(0, 201, 20):
        for y in range(-60, 181, 20):
            dxf.add_point(d, 'GCD', x, y, plane(x, y))
    ring = [(20, -40), (180, -40), (180, 160), (20, 160)]
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        n = int(math.hypot(x2 - x1, y2 - y1) / 4)
        ux, uy = (x2 - x1) / n, (y2 - y1) / n
        for i in range(n):
            ax, ay, bx, by = x1 + ux * i, y1 + uy * i, x1 + ux * (i + 0.7), y1 + uy * (i + 0.7)
            nx, ny = -uy / 4 * 0.1, ux / 4 * 0.1
            dxf.layer(d, '1')['lines'].append({'pts': [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)], 'closed': True, 'elev': None, 'fill': False})
    dxf.add_line(d, '0', [(60, 0), (140, 0), (140, 60), (110, 60), (110, 100), (60, 100)], closed=True)
    dxf.add_line(d, '坡角坡顶线', [(59.7, -0.3), (140.3, -0.3), (140.3, 60.3), (110.3, 60.3), (110.3, 80), (110.3, 100.3), (80, 100.3), (59.7, 100.3)], closed=True)
    dxf.add_line(d, 'TK', [(-50, -120), (300, -120), (300, 240), (-50, 240)], closed=True)
    dxf.write(d, path)


class DxfFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        write_base_dxf(self.folder / '布置图.dxf')
        plan = dxf.new_drawing()
        dxf.add_text(plan, '地质-钻孔', 'XZK1', 70, 20); dxf.add_text(plan, '地质-钻孔', 'XZK2', 120, 40); dxf.add_text(plan, '地质-钻孔', 'XZK3', 90, 80)
        dxf.write(plan, self.folder / '勘探点平面位置图.dxf')
        (self.folder / '钻孔分层数据.csv').write_text(','.join(survey.CSV_HEADER) + '\nXZK1,70,20,104.1,3.0,粘土,0,2,102.1\nXZK2,120,40,107.2,2.5,粘土,0,3,104.2\n', encoding='utf-8-sig')
        (self.folder / '说明.csv').write_text('a,b\n1,2\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_then_clean_writes_three_outputs(self):
        found = clean.scan(self.folder)
        self.assertEqual({f['name']: f['kind'] for f in found['files']},
                         {'勘探点平面位置图.dxf': 'plan_dxf', '布置图.dxf': 'base_dxf', '说明.csv': 'other_csv', '钻孔分层数据.csv': 'holes_csv'})
        # 有 DXF、CSV 时逐项默认直接用它们，不必转换
        self.assertEqual((found['sources'], found['ready']), ({'terrain': '布置图.dxf', 'holes': '钻孔分层数据.csv', 'plan': '勘探点平面位置图.dxf'}, True))
        self.assertEqual(found['candidates']['holes'], ['钻孔分层数据.csv'])
        log = []
        report = clean.clean_dxf(self.folder, '布置图.dxf', '勘探点平面位置图.dxf', '钻孔分层数据.csv', progress=log.append)
        out = self.folder / clean.OUTPUT_DIR
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([clean.BASE_NAME, clean.PLAN_NAME, clean.HOLES_NAME, clean.REPORT_NAME]))
        self.assertEqual(report['roles'], {'contour': ['DGX'], 'point': ['GCD'], 'land': ['1'], 'platform': ['0']})
        base = report['base']
        self.assertEqual((base['contours']['total'], base['contours']['pending'], base['contours']['interval']), (14, 0, 0.5))
        self.assertEqual((base['points']['total'], base['points']['with_elevation']), (143, 143))
        self.assertTrue(base['land']['closed'])
        self.assertAlmostEqual(base['land']['area'], 160 * 200, delta=400)
        # 场地边线与坡顶线面积相近：取顶点少的场地边线（6 个角点）
        self.assertEqual((base['platform']['vertices'], base['platform']['area']), (6, 6800.0))
        self.assertEqual(report['holes']['count'], 2)
        result = dxf.read(out / clean.BASE_NAME)
        self.assertEqual(len(result['layers']['等高线-计曲线']['lines']), 3)          # 102.5、105、107.5
        self.assertEqual(len(result['layers']['等高线-首曲线']['lines']), 11)
        self.assertEqual(clean.load_report(self.folder)['time'], report['time'])
        self.assertTrue(log)
        view = clean.preview(out / clean.BASE_NAME)
        self.assertEqual(sorted(l['name'] for l in view['layers']), ['场平范围', '征地线', '等高线-计曲线', '等高线-首曲线', '高程注记', '高程点'])
        self.assertLess(max(view['size']), 700)            # 预览坐标已减去原点
        self.assertEqual(next(l for l in view['layers'] if l['name'] == '等高线-计曲线')['lines'][0]['z'], 102.5)

    def test_contours_without_elevation_are_levelled_from_points(self):
        write_base_dxf(self.folder / '布置图.dxf', with_elevation=False)
        report = clean.clean_dxf(self.folder, '布置图.dxf', '勘探点平面位置图.dxf', '钻孔分层数据.csv')
        c = report['base']['contours']
        self.assertFalse(c['has_elevation'])
        result = dxf.read(self.folder / clean.OUTPUT_DIR / clean.BASE_NAME)
        got = sorted(l['elev'] for name in ('等高线-计曲线', '等高线-首曲线') for l in result['layers'].get(name, {'lines': []})['lines'])
        self.assertEqual(len(got) + c['pending'], 14)
        self.assertTrue(set(got) <= {101.0 + 0.5 * k for k in range(14)})
        self.assertGreaterEqual(len(got), 10)
        self.assertTrue(any('没有逐条标注高程' in w for w in report['warnings']))
        self.assertTrue(any('可靠性较低' in w for w in report['warnings']))      # 分不出计曲线也没有注记

    def test_sources_are_assigned_item_by_item_and_checked_against_what_modelling_needs(self):
        (self.folder / '钻孔分层数据.csv').write_text('\n'.join(
            [','.join(survey.CSV_HEADER)] + [f'XZK{i},{60 + i * 20},{20 + i * 15},{104 + i},3.0,粘土,0,2,{102 + i}' for i in range(1, 4)]) + '\n', encoding='utf-8-sig')
        # 地形图是 DXF 时可以不给勘探点平面位置图：成果里的平面图由钻孔坐标生成
        report = clean.clean_sources(self.folder, '布置图.dxf', '钻孔分层数据.csv')
        self.assertEqual((report['mode'], report['converted'], report['sources']['plan']), ('dxf', [], None))
        self.assertEqual((report['checklist']['ready'], report['checklist']['missing']), (True, []))
        self.assertEqual([(x['name'], x['ok']) for x in report['checklist']['items']],
                         [('等高线', True), ('高程点', True), ('征地线', True), ('场平范围', True), ('钻孔位置', True), ('钻孔分层', True)])
        plan = dxf.read(self.folder / clean.OUTPUT_DIR / clean.PLAN_NAME)
        self.assertEqual((len(plan['layers']['钻孔']['points']), sorted(t['text'] for t in plan['layers']['钻孔编号']['texts'])), (3, ['XZK1', 'XZK2', 'XZK3']))
        # 缺哪一项就指出哪一项（这里不给征地线图层）
        report = clean.clean_sources(self.folder, '布置图.dxf', '钻孔分层数据.csv', roles={'land': []})
        self.assertEqual((report['checklist']['ready'], report['checklist']['missing']), (False, ['征地线']))
        # PDF 地形图必须配勘察报告 PDF 才能取得坐标；格式不对的直接拒绝
        with self.assertRaisesRegex(ValueError, '需要同时指定勘察报告 PDF'):
            clean.clean_sources(self.folder, '布置图.pdf', '钻孔分层数据.csv', '勘探点平面位置图.dxf')
        with self.assertRaisesRegex(ValueError, '钻孔资料须为'):
            clean.clean_sources(self.folder, '布置图.dxf', '布置图.dxf')

    def test_holes_outside_drawing_and_role_override(self):
        (self.folder / '钻孔分层数据.csv').write_text(','.join(survey.CSV_HEADER) + '\nXZK1,34570070,20,104.1,3.0,粘土,0,2,102.1\n', encoding='utf-8-sig')
        report = clean.clean_dxf(self.folder, '布置图.dxf', '勘探点平面位置图.dxf', '钻孔分层数据.csv', roles={'platform': ['坡角坡顶线'], 'bogus': ['x']})
        self.assertTrue(any('不在地形图范围内' in w for w in report['warnings']))
        self.assertEqual((report['roles']['platform'], report['base']['platform']['vertices']), (['坡角坡顶线'], 6))


class ServerRouteTests(unittest.TestCase):
    def setUp(self):
        import server as app
        self.app = app
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / '资料'
        self.folder.mkdir()
        self.old = app.DATA, copy.deepcopy(app.STATE)
        app.DATA = Path(self.temp.name)
        app.STATE.update(app.session_defaults(), running=False, pause=False, workspace='')

    def tearDown(self):
        for _ in range(300):
            if not self.app.STATE['running']:
                break
            time.sleep(0.02)
        self.app.DATA = self.old[0]
        self.app.STATE.clear(); self.app.STATE.update(self.old[1])
        self.temp.cleanup()

    def test_only_plain_file_names_inside_the_folder_are_accepted(self):
        app = self.app
        (self.folder / 'a.dxf').write_text('x', encoding='utf-8')
        (Path(self.temp.name) / 'secret.dxf').write_text('x', encoding='utf-8')
        self.assertEqual(app.clean_file(self.folder, 'a.dxf', ('.dxf',)).name, 'a.dxf')
        for bad in ('../secret.dxf', '..\\secret.dxf', str(Path(self.temp.name) / 'secret.dxf'), 'a.pdf', '', 'missing.dxf'):
            with self.assertRaises(ValueError, msg=bad):
                app.clean_file(self.folder, bad, ('.dxf',))
        with self.assertRaisesRegex(ValueError, '请先选择资料文件夹'):
            app.clean_folder_path({})
        with self.assertRaisesRegex(ValueError, '不存在'):
            app.clean_folder_path({'folder': str(self.folder / 'none')})

    def test_run_stores_report_and_reports_without_calling_model(self):
        app = self.app
        write_base_dxf(self.folder / '布置图.dxf')
        dxf.write(dxf.new_drawing(), self.folder / '平面.dxf')
        (self.folder / '钻孔.csv').write_text(','.join(survey.CSV_HEADER) + '\nXZK1,70,20,104.1,3.0,粘土,0,2,102.1\n', encoding='utf-8-sig')
        old_llm, app.llm = app.llm, lambda *a, **k: self.fail('资料清洗不应调用模型')
        try:
            # 三项资料逐项给完整路径，可以不在同一个文件夹；成果写在地形与场地图所在文件夹下
            other = Path(self.temp.name) / '勘察'
            other.mkdir()
            (self.folder / '钻孔.csv').replace(other / '钻孔.csv')
            terrain, plan, holes = str(self.folder / '布置图.dxf'), str(self.folder / '平面.dxf'), str(other / '钻孔.csv')
            with self.assertRaisesRegex(ValueError, '请添加地形与场地图'):
                app.earthwork_clean_run({'sources': {'holes': holes}})
            with self.assertRaisesRegex(ValueError, '请添加钻孔资料'):
                app.earthwork_clean_run({'sources': {'terrain': terrain}})
            with self.assertRaisesRegex(ValueError, '等高距'):
                app.earthwork_clean_run({'sources': {'terrain': terrain, 'holes': holes}, 'interval': 99})
            with self.assertRaisesRegex(ValueError, '文件不存在'):
                app.earthwork_clean_run({'sources': {'terrain': terrain, 'holes': '钻孔.csv'}})          # 不是完整路径
            with self.assertRaisesRegex(ValueError, '钻孔资料须为 CSV 或 PDF'):
                app.earthwork_clean_run({'sources': {'terrain': terrain, 'holes': plan}})
            self.assertEqual(app.clean_source(f'"{terrain}"', 'terrain'), Path(terrain))               # 粘贴的路径带引号也认
            app.earthwork_clean_run({'sources': {'terrain': terrain, 'plan': plan, 'holes': holes}})
            for _ in range(500):
                if not app.STATE['running']:
                    break
                time.sleep(0.02)
        finally:
            app.llm = old_llm
        cleaning = app.STATE['earthwork']['cleaning']
        self.assertEqual(cleaning['sources'], {'terrain': terrain, 'holes': holes, 'plan': plan})
        self.assertTrue((self.folder / clean.OUTPUT_DIR / clean.HOLES_NAME).is_file())
        self.assertEqual((cleaning['folder'], cleaning['report']['mode'], cleaning['report']['holes']['count']), (str(self.folder), 'dxf', 1))
        self.assertIn('未调用模型', app.STATE['messages'][-1]['text'])
        self.assertIn('建模必需的数据还缺：钻孔位置、钻孔分层', app.STATE['messages'][-1]['text'])      # 只有 1 个钻孔
        self.assertEqual(app.STATE['error'], '')
        json.dumps(app.STATE['earthwork'])


class ModelTests(unittest.TestCase):
    """由清洗成果生成三维模型：斜面地形、三层地层的示意工程。"""
    def setUp(self):
        import earthwork_model
        self.model = earthwork_model
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        write_base_dxf(self.folder / '布置图.dxf')
        rows = [','.join(survey.CSV_HEADER)]
        self.holes = [(f'XZK{n}', 70.0 + (n % 4) * 20, 10.0 + (n // 4) * 25) for n in range(12)]
        for name, x, y in self.holes:
            z = round(plane(x, y), 2)
            rows += [f'{name},{x},{y},{z},3.0,耕植土,0,0.5,{z - 0.5}', f'{name},{x},{y},{z},3.0,粉质粘土,0.5,4.5,{z - 4.5}']
            if name == 'XZK5':
                rows += [f'{name},{x},{y},{z},3.0,全风化砂岩,4.5,8,{z - 8}', f'{name},{x},{y},{z},3.0,强风化砂岩,8,9,{z - 9}', f'{name},{x},{y},{z},3.0,全风化砂岩,9,15,{z - 15}']
            elif name == 'XZK11':
                rows += [f'{name},{x},{y},{z},3.0,黑色怪料,4.5,6,{z - 6}']          # 终孔在土层，且名称认不出
            else:
                rows += [f'{name},{x},{y},{z},3.0,全风化砂岩,4.5,12,{z - 12}']
        (self.folder / '钻孔.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8-sig')
        clean.clean_sources(self.folder, '布置图.dxf', '钻孔.csv')

    def tearDown(self):
        self.temp.cleanup()

    def test_unit_classification(self):
        got = {n: self.model.classify(n) for n in ('耕植土', '素填土', '粘土', '粉质粘土', '全风化砂岩', '强风化泥岩', '中风化灰岩', '微风化花岗岩', '残积土', '黑色怪料')}
        self.assertEqual(got, {'耕植土': 'soil', '素填土': 'soil', '粘土': 'clay', '粉质粘土': 'clay', '全风化砂岩': 'w4', '强风化泥岩': 'w3', '中风化灰岩': 'w2',
                               '微风化花岗岩': 'w2', '残积土': 'w4', '黑色怪料': None})
        segs, unknown = self.model.hole_segments({'layers': [('耕植土', 0, 0.5, 0), ('粘土', 0.5, 2, 0), ('粉质粘土', 2, 4, 0), ('耕植土', 4, 4.2, 0), ('全风化砂岩', 4.2, 9, 0), ('怪岩', 9, 10, 0)]})
        self.assertEqual((segs, unknown), ([('soil', 0, 0.5), ('clay', 0.5, 4.2), ('w4', 4.2, 10)], ['怪岩']))

    def test_model_reproduces_terrain_strata_and_platform(self):
        log = []
        stats = self.model.build(self.folder, progress=log.append)
        model = json.loads((self.folder / clean.OUTPUT_DIR / self.model.MODEL_NAME).read_text(encoding='utf-8'))
        nx, ny, dx, x0, y0 = model['nx'], model['ny'], model['dx'], model['x0'], model['y0']
        self.assertEqual((len(model['dem']), dx), (nx * ny, 2.0))
        # 地形是斜面：格网内部各点应与斜面一致
        worst = max(abs(model['dem'][j * nx + i] - plane(x0 + i * dx, y0 + j * dx)) for i in range(nx) for j in range(ny)
                    if 20 <= x0 + i * dx <= 180 and -40 <= y0 + j * dx <= 160)
        self.assertLess(worst, 0.02)
        self.assertLess(stats['collar_mean_abs'], 0.02)
        # 场平范围 6800 m²：inside 格点数 × 格网面积应接近
        self.assertAlmostEqual(sum(model['inside']) * dx * dx, 6800, delta=350)
        self.assertEqual(model['platform'][0], model['platform'][-1])
        k = next(k for k in range(nx * ny) if abs(x0 + (k % nx) * dx - 50) < 1e-6 and abs(y0 + (k // nx) * dx - 50) < 1e-6)
        self.assertEqual((model['inside'][k], model['dist'][k]), (0, 10.0))                  # 场平西边线在 x=60
        # 地层：耕植土处处 0.5 m，土岩界面在 4.5 m；强风化夹层只在 XZK5 周围
        self.assertEqual((min(model['tsoil']), max(model['tsoil']), min(model['tclay']), max(model['tclay'])), (0.5, 0.5, 4.0, 4.0))
        hx, hy = next((x, y) for n, x, y in self.holes if n == 'XZK5')
        at = lambda x, y: round((y - y0) / dx) * nx + round((x - x0) / dx)
        self.assertAlmostEqual(model['t3th'][at(hx, hy)], 1.0, delta=0.02)        # 钻孔不在格点上，差不到 1 m
        self.assertEqual((model['t3top'][at(hx, hy)], model['t3th'][at(hx + 20, hy)]), (3.5, 0.0))
        self.assertEqual((sum(model['i2']), model['w2top']), (0, 0.0))
        self.assertEqual(model['holes'][5]['segs'], [['soil', 0.0, 0.5], ['clay', 0.5, 4.5], ['w4', 4.5, 8.0], ['w3', 8.0, 9.0], ['w4', 9.0, 15.0]])
        self.assertEqual((stats['holes'], stats['rock_holes'], stats['lens_holes'], stats['cv']['耕植土厚']), (12, 11, 1, [0.0, 0.0]))
        self.assertTrue(any('黑色怪料' in w for w in stats['warnings']))
        self.assertTrue(any('终孔仍在土层' in w for w in stats['warnings']))
        self.assertEqual(len(model['land']), 1)
        self.assertEqual(len(model['contours']), 14)
        # 平台读取时优先用成果目录里的模型
        import earthwork
        self.assertEqual(earthwork.model_path(str(self.folder)), self.folder / clean.OUTPUT_DIR / self.model.MODEL_NAME)
        self.assertEqual(earthwork.load_model(str(self.folder))['nx'], nx)
        self.assertEqual(earthwork.quality(str(self.folder))['stats']['holes'], 12)
        self.assertEqual(len(earthwork.sources(str(self.folder))['points']), 143)
        self.assertTrue(log)
        earthwork.load_model('')

    @unittest.skipUnless(__import__('shutil').which('node'), '需要 node 运行计算引擎')
    def test_engine_computes_on_built_model(self):
        import subprocess
        self.model.build(self.folder)
        script = ("import {readFileSync} from 'node:fs';import {createEngine} from './static/earthwork/engine.js';"
                  "const e=createEngine(JSON.parse(readFileSync(process.argv[1],'utf-8')));const s=e.scan(0,0,'rule');const r=e.compute(s.balance.H,0,0,'free');"
                  "console.log(JSON.stringify({area:e.platformArea,cut:r.cut,fill:r.V.fill,soil:r.V.soil,H:s.balance.H,total:r.total}))")
        proc = subprocess.run(['node', '--input-type=module', '-e', script, str(self.folder / clean.OUTPUT_DIR / self.model.MODEL_NAME)],
                              capture_output=True, text=True, encoding='utf-8', timeout=120, cwd=str(Path(__file__).resolve().parent))
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        got = json.loads(proc.stdout)
        self.assertAlmostEqual(got['area'], 6800, delta=1)
        self.assertTrue(got['cut'] > 1000 and got['fill'] > 1000 and got['soil'] > 0 and got['total'] > 0)
        self.assertTrue(103 < got['H'] < 110)            # 斜面场地的挖填平衡标高在场地高程范围内

    def test_build_needs_cleaning_outputs(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(ValueError, '请先完成资料清洗'):
                self.model.build(empty)


class PreviewTests(unittest.TestCase):
    """PDF 预览在子进程里渲染并缓存：首次返回 pending，完成后返回 PNG；页码越界、尺寸不支持时报错。"""
    def setUp(self):
        import earthwork_preview
        self.preview = earthwork_preview
        self.temp = tempfile.TemporaryDirectory()
        self.old = earthwork_preview.CACHE
        earthwork_preview.CACHE = Path(self.temp.name) / 'cache'
        import pypdfium2 as pdfium
        self.pdf = Path(self.temp.name) / '图纸.pdf'
        doc = pdfium.PdfDocument.new()
        doc.new_page(400, 300); doc.new_page(300, 400)
        doc.save(str(self.pdf)); doc.close()

    def tearDown(self):
        self.preview.CACHE = self.old
        self.temp.cleanup()

    def wait(self, call):
        for _ in range(300):
            status, value = call()
            if status != 'pending':
                return status, value
            time.sleep(0.1)
        self.fail('预览渲染超时')

    def test_info_and_pages_render_in_subprocess_and_are_cached(self):
        status, info = self.wait(lambda: self.preview.info(self.pdf))
        self.assertEqual((status, info['pages'], info['default'], info['plan'], info['columns']), ('ready', 2, 1, [], []))
        self.assertEqual(self.preview.page(self.pdf, 2, 900)[0], 'pending')          # 首次：已在后台进程里渲染
        status, path = self.wait(lambda: self.preview.page(self.pdf, 2, 900))
        self.assertEqual(status, 'ready')
        from PIL import Image
        with Image.open(path) as image:
            self.assertEqual(image.size[1], 900)                                       # 长边 900 像素
        self.assertEqual(self.preview.page(self.pdf, 2, 900), ('ready', path))        # 再取直接命中缓存
        status, message = self.wait(lambda: self.preview.page(self.pdf, 9, 900))
        self.assertEqual(status, 'error')
        self.assertIn('页码超出范围', message)
        with self.assertRaises(ValueError):
            self.preview.page(self.pdf, 1, 5000)


@unittest.skipUnless((SAMPLE / '岩土工程勘察报告已脱敏.pdf').is_file(), '样例图纸不在本机')
class SamplePdfFlowTests(unittest.TestCase):
    def test_pdf_flow_matches_reference_results(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            for name in ('岩土工程勘察报告已脱敏.pdf', '变电站护坡挡墙布置图已脱敏.pdf'):
                shutil.copy(SAMPLE / name, folder)
            report = clean.clean_pdf(folder, '变电站护坡挡墙布置图已脱敏.pdf', '岩土工程勘察报告已脱敏.pdf', east_prefix='34')
            produced = (Path(folder) / clean.OUTPUT_DIR / clean.HOLES_NAME).read_text(encoding='utf-8-sig')
        self.assertEqual(produced.strip().splitlines(), (SAMPLE / '钻孔分层数据.csv').read_text(encoding='utf-8-sig').strip().splitlines())
        self.assertLess(report['georeference']['rms'], 0.05)
        base = report['base']
        self.assertGreaterEqual(base['points']['with_elevation'], 555)
        self.assertEqual(base['platform']['vertices'], 8)
        self.assertAlmostEqual(base['platform']['area'], 29971, delta=5)
        self.assertTrue(base['land']['closed'])
        self.assertLess(base['contours']['pending_length_share'], 8)
        self.assertEqual(base['stroke_labels']['characters'], '.0123456789')
        self.assertEqual((report['converted'], report['checklist']['ready']), (['变电站护坡挡墙布置图已脱敏.pdf', '岩土工程勘察报告已脱敏.pdf'], True))


if __name__ == '__main__':
    unittest.main()
