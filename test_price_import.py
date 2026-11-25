"""价格库接口：信息价 Excel 导入、分表块打标签、按块审核、期次版本，以及七千条不进 state.json。

价格库是跨工程的全局资料：条目单独存盘，/api/state 只下发第一页与统计，其余按需检索。
"""
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import openpyxl

import server
import price_library as PL

ROWS = [
    ['序号', '名称', '规格型号', '单位', '含税单价（元）', '备注'],
    ['一', '变压器', None, None, None, None],
    ['1', '110kV 三相三绕组油浸式电力变压器', 'SSZ-63000/110', '台', 3530000, '三相三绕组，有载'],
    ['2', '铜铝连接管', 'GTL-120', '条', 8, None],
    ['一', '配网材料', None, None, None, None],
    ['1', '铝包钢芯铝绞线', 'JL/LB1A-70/10', 't', 19045, None],
    ['2', '铜铝连接管', 'GTL-120', '条', 3, '配网用'],
    ['3', '标示牌', 'PVC', '只', None, '原表无价'],
]
META = dict(period='2026Q4', region='XX公司', tax='含税', tax_rate=13, composition=1.0, basis='到现场仓库')


class PriceLibraryHttpTests(unittest.TestCase):
    """接口层：导入、标签、检索、审核、发布与回滚。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.LocalServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / '信息价1031.xlsx'
        book = openpyxl.Workbook()
        for row in ROWS:
            book.active.append(row)
        book.save(self.path)
        server.STATE.update(prices=[], versions=[], price_blocks=[], price_duplicates=[], price_rejected=[],
                            price_results=[], messages=[])
        server.AUTH.failures.clear()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.call('/api/auth/login', {'username': 'admin', 'password': 'admin123!@#'})

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, data):
        message = urllib.request.Request(self.base + path, data=data,
                                         headers={'Content-Type': 'application/json', 'X-Agent-Demo': '1'})
        try:
            with self.opener.open(message) as response:
                return response.status, json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b'{}')

    def call(self, path, body=None):
        return self.request(path, json.dumps(body or {}).encode())

    def get(self, path):
        return self.request(path, None)

    def import_prices(self, **overrides):
        body = dict(META, path=str(self.path))
        body.update(overrides)
        return self.call('/api/prices/import_excel', body)

    def test_import_requires_declared_caliber(self):
        """期次、税口径、税率与构成系数表里都没有，必须声明，不能推测。"""
        self.assertIn('期次', self.import_prices(period='')[1]['error'])
        self.assertIn('含税', self.import_prices(tax='不含税')[1]['error'])
        self.assertIn('税率', self.import_prices(tax_rate=99)[1]['error'])
        self.assertIn('构成系数', self.import_prices(composition=5)[1]['error'])
        self.assertIn('不存在', self.import_prices(path=str(self.path) + '.missing')[1]['error'])
        self.assertEqual(server.STATE['prices'], [])

    def test_import_splits_blocks_and_keeps_caliber(self):
        self.assertEqual(self.import_prices()[0], 200)
        self.assertEqual(len(server.STATE['prices']), 4)
        self.assertEqual([b['count'] for b in server.STATE['price_blocks']], [2, 2])
        item = server.STATE['prices'][0]
        self.assertEqual((item['period'], item['tax'], item['tax_rate'], item['composition']), ('2026Q4', '含税', 13.0, 1.0))
        self.assertEqual([r['reason'] for r in server.STATE['price_rejected']], ['原表没有有效单价，未导入'])
        self.assertEqual(len(server.STATE['price_duplicates']), 1)                      # 两块里的铜铝连接管
        text = server.STATE['messages'][-1]['text']
        self.assertIn('标签已按一级类目暂填，2 块待确认', text)
        self.assertIn('最大相差 2.67 倍', text)

    def test_state_sends_one_page_and_counts(self):
        self.import_prices()
        original = server.PRICE_PAGE
        server.PRICE_PAGE = 2
        try:
            state = self.get('/api/state')[1]
        finally:
            server.PRICE_PAGE = original
        self.assertEqual((len(state['prices']), state['price_total']), (2, 4))
        self.assertEqual((state['price_duplicate_total'], state['price_rejected_total']), (1, 1))
        self.assertEqual(len(state['price_blocks']), 2)

    def test_prices_stay_out_of_state_json(self):
        """七千多条信息价不进 state.json：单独存盘，重启时读回。"""
        self.import_prices()
        saved = json.loads((server.DATA / 'state.json').read_text(encoding='utf-8'))
        self.assertNotIn('prices', saved)
        self.assertNotIn('price_blocks', saved)
        library = json.loads((server.DATA / 'price_library.json').read_text(encoding='utf-8'))
        self.assertEqual(len(library['prices']), 4)
        server.STATE.update(prices=[], price_blocks=[])
        server.load_price_library()
        self.assertEqual(len(server.STATE['prices']), 4)

    def test_label_blocks_then_approve_and_publish(self):
        self.import_prices()
        blocks = [b['id'] for b in server.STATE['price_blocks']]
        self.assertEqual([b['label'] for b in server.STATE['price_blocks']], ['变压器', '配网材料'])
        self.assertIn('先确认该分表块的标签', self.call('/api/prices/approve_block', {'block': blocks[0]})[1]['error'])
        self.assertIn('至少为一个', self.call('/api/prices/label', {'labels': {}})[1]['error'])
        self.call('/api/prices/label', {'labels': {blocks[0]: '主网设备·35kV及以上'}})
        self.assertEqual({p['block_label'] for p in server.STATE['prices'] if p['block'] == blocks[0]},
                         {'主网设备·35kV及以上'})
        self.assertIn('仍有 1 块待确认', server.STATE['messages'][-1]['text'])
        self.assertIn('还没有人工确认', self.call('/api/prices/publish')[1]['error'])
        self.call('/api/prices/label', {'labels': {blocks[1]: '配网材料·20kV及以下'}})
        self.assertIn('须全部审核', self.call('/api/prices/publish')[1]['error'])
        for block in blocks:
            self.call('/api/prices/approve_block', {'block': block, 'note': '已核对出处与口径'})
        self.assertEqual(self.call('/api/prices/publish')[0], 200)
        self.assertEqual([(v['period'], len(v['rows'])) for v in server.STATE['versions']], [('2026Q4', 4)])

    def test_reimport_fills_previous_price_from_published_version(self):
        """环比价取自上一个已发布期次：按名称型号单位的键对上才填。"""
        self.import_prices()
        for block in [b['id'] for b in server.STATE['price_blocks']]:
            self.call('/api/prices/label', {'labels': {block: f'块{block}'}})
            self.call('/api/prices/approve_block', {'block': block})
        self.call('/api/prices/publish')
        book = openpyxl.load_workbook(self.path)
        book.active.cell(row=3, column=5).value = 3700000
        book.save(self.path)
        self.import_prices(period='2027Q1')
        item = next(p for p in server.STATE['prices'] if p['spec'] == 'SSZ-63000/110')
        self.assertEqual((item['price'], item['previous'], item['period']), (3700000, 3530000.0, '2027Q1'))
        self.assertFalse(any(p.get('approved') for p in server.STATE['prices']))   # 新期次重新审核

    def test_search_finds_entries_beyond_the_first_page(self):
        self.import_prices()
        status, body = self.call('/api/prices/search', {'q': 'GTL-120'})
        self.assertEqual((status, body['total']), (200, 2))
        self.assertEqual({x['name'] for x in body['items']}, {'铜铝连接管'})
        self.assertEqual(self.call('/api/prices/search', {'q': '没有这个东西'})[1]['total'], 0)
        block = server.STATE['price_blocks'][1]['id']
        self.assertEqual(self.call('/api/prices/search', {'block': block})[1]['total'], 2)

    def test_rollback_restores_blocks_and_duplicates(self):
        self.import_prices()
        for block in [b['id'] for b in server.STATE['price_blocks']]:
            self.call('/api/prices/label', {'labels': {block: f'块{block}'}})
            self.call('/api/prices/approve_block', {'block': block})
        self.call('/api/prices/publish')
        server.STATE.update(prices=[], price_blocks=[], price_duplicates=[])
        self.assertEqual(self.call('/api/prices/rollback', {'id': 1})[0], 200)
        self.assertEqual(len(server.STATE['prices']), 4)
        self.assertEqual([b['label'] for b in server.STATE['price_blocks']], ['块1', '块2'])
        self.assertEqual(len(server.STATE['price_duplicates']), 1)

    def test_json_import_still_works_and_gets_keys(self):
        item = dict(code='P1', name='钢芯铝绞线', spec='JL/G1A-300/40', unit='t', price=21800, period='2026Q4',
                    region='XX公司', tax='除税', basis='出厂', source='信息价.pdf', page=1)
        self.assertEqual(self.call('/api/prices/import', {'items': [item]})[0], 200)
        self.assertEqual(server.STATE['prices'][0]['key'], PL.item_key(item))
        self.assertIn('吨', server.STATE['prices'][0]['key'])                      # 单位归一进键
        self.assertEqual(self.call('/api/prices/import', {'items': [dict(item, price=0)]})[0], 400)


class PriceReviewWiringTests(unittest.TestCase):
    """价格评审这一步：清洗完成即可执行，成套结论来自数量评审，造价侧税口径可声明。"""

    def setUp(self):
        server.STATE.update(
            prices=[dict(code='P1', name='110kV三相三绕组油浸式电力变压器', spec='SSZ-63000/110', unit='台',
                         price=3530000, tax='含税', tax_rate=13.0, composition=1.0, period='20261031',
                         region='XX公司', block=1, block_label='主网设备', source='信息价1031.xlsx', row=3,
                         key=PL.item_key(dict(name='110kV三相三绕组油浸式电力变压器', spec='SSZ-63000/110', unit='台')))],
            price_results=[], rows=[], messages=[], threshold=10, cost_tax='', stage=1, scope='',
            dataset=dict(label='样例', origin='', sources=[
                dict({'序号': 1, 'Estimation_Name': '110kV三相三绕组油浸式电力变压器', '型号及规范': 'SSZ-63000/110',
                      'Estimation_Unit': '台', 'Estimation_Quantity': '1', 'Estimation_Price': '3800000',
                      'Supply_Type': '甲设', '层级': '主变压器系统'}),
                dict({'序号': 2, 'Estimation_Name': '主变保护屏', '型号及规范': '', 'Estimation_Unit': '面',
                      'Estimation_Quantity': '1', 'Estimation_Price': '120000', 'Supply_Type': '甲设', '层级': '继电保护'}),
                dict({'序号': 3, 'Estimation_Name': '铝包钢芯铝绞线', '型号及规范': 'JL/LB20A 300/40',
                      'Estimation_Unit': 't', 'Estimation_Quantity': '0.017', 'Estimation_Price': '13034',
                      'Supply_Type': '乙材', '层级': '主变压器系统'}),
            ], targets=[], warnings=[]))

    def test_price_step_runs_right_after_cleaning(self):
        self.assertEqual(server.step_error('price'), '')                     # 只审价不审量也能用
        server.STATE['stage'] = 0
        self.assertIn('资料清洗', server.step_error('price'))

    def test_supplier_equipment_price_is_tax_inclusive_so_no_conversion(self):
        """甲供设备的表三甲单价是含税市场价：不再换算成除税价，直接与含税信息价比。"""
        server.price_review()
        results = server.STATE['price_results']
        self.assertEqual([r['status'] for r in results], ['已比较', '无对应条目', '无对应条目'])
        self.assertEqual(results[0]['cost_tax'], '含税')
        self.assertAlmostEqual(results[0]['delta'], (3800000 - 3530000) / 3530000 * 100, places=6)
        self.assertNotIn('除税价', results[0]['conversion'])
        text = server.STATE['messages'][-1]['text']
        self.assertIn('按期次 20261031 的信息价完成比较 1 条', text)
        self.assertIn('偏高 1 条', text)
        self.assertIn('未使用数量评审的成套结论', text)
        self.assertIn('无对应条目 2 条', text)

    def add_material_price(self):
        server.STATE['prices'].append(dict(
            code='P2', name='铝包钢芯铝绞线', spec='JL/LB20A 300/40', unit='吨', price=19045, tax='含税',
            tax_rate=13.0, composition=1.0, period='20261031', block=2, source='信息价1031.xlsx', row=1457,
            key=PL.item_key(dict(name='铝包钢芯铝绞线', spec='JL/LB20A 300/40', unit='吨'))))
        return 19045 / 1.13                                   # 装材按除税比

    def test_material_uses_market_price_from_the_difference_sheet(self):
        """清洗回填了价差表的市场价：用市场价比，表三甲单价（基准期预算价）只作说明。"""
        reference = self.add_material_price()
        server.STATE['dataset']['sources'][2]['市场价'] = 18995.58
        server.STATE['dataset']['sources'][2]['比价基准'] = '主材价差表市场价（不含税）'
        server.price_review()
        material = server.STATE['price_results'][2]
        self.assertEqual((material['status'], material['cost_price']), ('已比较', 18995.58))
        self.assertAlmostEqual(material['delta'], (18995.58 - reference) / reference * 100, places=6)
        self.assertIn('造价侧取主材价差表的市场价（不含税），表三甲单价 13,034 为基准期预算价', material['reason'])

    def test_material_without_a_difference_row_is_compared_but_flagged(self):
        """不在价差表里的装材：仍然比，但标注单价基准待确认，不装作已经对齐。"""
        reference = self.add_material_price()
        server.price_review()
        material = server.STATE['price_results'][2]
        self.assertEqual((material['status'], material['cost_price']), ('已比较', 13034.0))
        self.assertAlmostEqual(material['delta'], (13034 - reference) / reference * 100, places=6)
        self.assertEqual(material['basis_note'], '该条不在主材价差表中，表三甲单价是否已是市场价待确认')
        self.assertIn('待确认', material['reason'])

    def test_set_members_from_quantity_review_are_not_priced(self):
        server.STATE['rows'] = [dict(source_id='1', covered_by='2', band='一致'),
                                dict(source_id='2', covers=['3'], band='一致')]
        self.assertEqual(server.set_exclusions()['1'], '含于造价成套项（序号 2），价格随成套项整体核对，不单独比价')
        server.price_review()
        results = server.STATE['price_results']
        self.assertEqual(results[0]['status'], '不参与比价')
        self.assertIn('须拆分后与各组成件信息价合计比较', results[1]['reason'])
        self.assertIn('2 条按数量评审的成套结论不单独比价', server.STATE['messages'][-1]['text'])

    def test_cost_side_tax_caliber_can_be_forced_and_reset(self):
        server.price_review()
        inclusive = server.STATE['price_results'][0]['delta']                 # 甲设默认按含税比
        server.chat('造价税口径 除税', admin=True)
        self.assertEqual(server.STATE['cost_tax'], '除税')
        exclusive = server.STATE['price_results'][0]['delta']
        self.assertAlmostEqual(exclusive, (3800000 - 3530000 / 1.13) / (3530000 / 1.13) * 100, places=6)
        self.assertGreater(exclusive, inclusive)                              # 统一除税会把偏差推高约 13 个百分点
        self.assertIn('统一设为除税价', server.STATE['messages'][-1]['text'])
        server.chat('造价税口径 按供货方式', admin=True)
        self.assertEqual(server.STATE['cost_tax'], '')
        self.assertAlmostEqual(server.STATE['price_results'][0]['delta'], inclusive, places=6)
        self.assertIn('按供货方式', server.STATE['messages'][-1]['text'])


if __name__ == '__main__':
    unittest.main()
