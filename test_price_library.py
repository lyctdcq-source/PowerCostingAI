"""信息价导入：分表拼接的切块、单位归一、折行备注、重复键清单与口径字段。

样例按真实信息价表的结构造：同一个工作表里拼了两张分表（层级都从“一”开始、序号各自重启），
名称型号单位相同而价格不同的条目分散在两块里，备注里带折算系数，还有单位列串进备注、
整行只有折行内容、以及原表没有价格的行。
"""
import tempfile
import unittest
from pathlib import Path

import openpyxl

import price_library as PL

ROWS = [
    ['序号', '名称', '规格型号', '单位', '含税单价（元）', '备注'],
    ['一', '变压器', None, None, None, None],
    ['1', '110kV 三相三绕组油浸式电力变压器', 'SSZ-63000/110', '台', 3530000, '三相三绕组，有载'],
    ['2', '110kV 双绕组油浸式电力变压器', 'SZ-63000/110', '台', 3180000, None],
    ['二', '电缆及附件', None, None, None, None],
    ['1', '35kV交联聚乙烯绝缘电力电缆', 'ZRA-YJV62-26/35 1×500', 'km', 441420, '折算系数： 普通1 ZRC1.01 ZRB1.03 ZRA1.05'],
    ['2', '110kV 交联聚乙烯绝缘电力电缆', 'YJLW03-64/110 1×630', 'km', None, '原表空缺'],
    ['3', '铜铝连接管', 'GTL-120', '条', 8, None],
    [None, None, None, None, None, None],
    ['一', '配网材料', None, None, None, None],
    ['（一）', '导线', None, None, None, None],
    ['1', '铝包钢芯铝绞线', 'JL/LB1A-70/10', 't', 19045, None],
    ['2', '铜铝连接管', 'GTL-120', '条', 3, '配网用'],
    ['（二）', '其他材料', None, None, None, None],
    ['1', '电缆沟盖板', '800×400×60', 'm²', 120, None],
    ['2', '智能配电终端', 'DTU', '含电源模块DC 12V、24V、200W）等配件', 4600, None],
    ['3', '接地极', None, '只', 55, '备注第一行'],
    ['4', '续上一条的备注', None, None, None, None],
    ['5', '标示牌', 'PVC', '只', 0, '原表单价为 0'],
]


class PriceLibraryTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'XX公司电网工程主要设备材料信息价1031.xlsx'
        book = openpyxl.Workbook()
        for row in ROWS:
            book.active.append(row)
        book.save(self.path)
        self.result = PL.parse(self.path, dict(region='XX公司', tax='含税', tax_rate=13.0, composition=1.05, basis='到现场仓库'))

    def tearDown(self):
        self.temp.cleanup()

    def test_numeral_and_unit_normalization(self):
        self.assertEqual([PL.numeral(x) for x in ('一', '十', '十七', '（三）', '(四)', '二十一')], [1, 10, 17, 3, 4, 21])
        self.assertIsNone(PL.numeral('1'))
        self.assertIsNone(PL.numeral('铁塔'))
        self.assertEqual(PL.canonical_unit('t')[0], '吨')
        self.assertEqual(PL.canonical_unit('km')[0], '千米')
        self.assertEqual(PL.canonical_unit('m²')[0], '平方米')
        self.assertEqual(PL.canonical_unit('台')[0], '台')
        self.assertEqual(PL.canonical_unit('含电源模块DC 12V、24V、200W）等配件'), ('', '单位列内容像备注，未作为单位'))
        self.assertEqual(PL.match_key('ZRA-YJV62 1x500'), 'ZRA-YJV62 1×500'.replace(' ', ''))

    def test_blocks_split_where_hierarchy_restarts(self):
        """层级从“一”重启就是下一张分表：两块的行范围与一级类目各自独立。"""
        blocks = self.result['blocks']
        self.assertEqual(len(blocks), 2)
        self.assertEqual([b['categories'] for b in blocks], [['变压器', '电缆及附件'], ['配网材料']])
        self.assertEqual(blocks[0]['suggestion'], '变压器 / 电缆及附件')
        self.assertTrue(blocks[0]['last_row'] < blocks[1]['first_row'])
        # 标签按一级类目先填上，但标成草稿：类目名分不出主配网与电压等级，仍要人工确认
        self.assertEqual([b['label'] for b in blocks], ['变压器·电缆及附件', '配网材料'])
        self.assertEqual([b['label_auto'] for b in blocks], [True, True])
        self.assertEqual([x['block_label'] for x in self.result['items'] if x['block'] == 1],
                         ['变压器·电缆及附件'] * 4)
        self.assertEqual(PL.auto_label(dict(categories=['甲', '乙', '丙'], first_row=1, last_row=9)), '甲·乙等3类')
        self.assertEqual(PL.auto_label(dict(categories=[], first_row=1, last_row=9)), '第1–9行')

    def test_items_carry_caliber_and_hierarchy(self):
        item = next(x for x in self.result['items'] if x['spec'] == 'SSZ-63000/110')
        self.assertEqual((item['block'], item['category'], item['unit'], item['price']), (1, '变压器', '台', 3530000))
        self.assertEqual((item['tax'], item['tax_rate'], item['composition'], item['basis']),
                         ('含税', 13.0, 1.05, '到现场仓库'))
        self.assertEqual((item['period'], item['region'], item['source']), ('1031', 'XX公司', self.path.name))
        self.assertEqual(item['row'], 3)
        sub = next(x for x in self.result['items'] if x['name'] == '电缆沟盖板')
        self.assertEqual((sub['category'], sub['subcategory'], sub['unit']), ('配网材料', '其他材料', '平方米'))

    def test_rows_without_name_or_price_are_reported_not_imported(self):
        reasons = {(x['row'], x['reason']) for x in self.result['rejected']}
        self.assertIn((7, '原表没有有效单价，未导入'), reasons)      # 原表价格空缺
        self.assertIn((19, '原表没有有效单价，未导入'), reasons)     # 原表单价为 0
        self.assertFalse(any(x['price'] in (None, 0) for x in self.result['items']))

    def test_wrapped_notes_merge_into_previous_item(self):
        item = next(x for x in self.result['items'] if x['name'] == '接地极')
        self.assertEqual(item['notes'], '备注第一行 续上一条的备注')
        self.assertFalse(any(x['name'] == '续上一条的备注' for x in self.result['items']))
        self.assertTrue(any('并入上一条的备注' in w for w in self.result['warnings']))

    def test_notes_in_unit_column_warns_and_leaves_unit_empty(self):
        item = next(x for x in self.result['items'] if x['name'] == '智能配电终端')
        self.assertEqual(item['unit'], '')
        self.assertIn('人工补单位', item['price_warning'])
        self.assertTrue(any('第16行' in w and '像备注' in w for w in self.result['warnings']))

    def test_duplicate_key_lists_every_block_and_price(self):
        """同名同型号同单位、价格不同：列出全部候选与倍差，不能自动取第一条。"""
        self.assertEqual(len(self.result['duplicates']), 1)
        record = self.result['duplicates'][0]
        self.assertEqual((record['name'], record['unit'], record['count']), ('铜铝连接管', '条', 2))
        self.assertAlmostEqual(record['ratio'], 2.67, places=2)
        self.assertEqual(sorted(e['block'] for e in record['entries']), [1, 2])
        self.assertEqual(sorted(e['price'] for e in record['entries']), [3, 8])

    def test_conversion_factors_parsed_from_notes(self):
        item = next(x for x in self.result['items'] if x['unit'] == '千米')
        self.assertEqual(item['factors'], [dict(label='普通', factor=1.0), dict(label='ZRC', factor=1.01),
                                           dict(label='ZRB', factor=1.03), dict(label='ZRA', factor=1.05)])
        self.assertEqual(PL.parse_factors('无折算说明'), [])

    def test_label_blocks_writes_back_to_items(self):
        pending = PL.label_blocks(self.result['items'], self.result['blocks'], {1: '主网设备（35kV及以上）'})
        self.assertEqual([b['id'] for b in pending], [2])                 # 第 2 块的标签还没人工确认
        self.assertEqual(self.result['blocks'][0]['label'], '主网设备（35kV及以上）')
        self.assertFalse(self.result['blocks'][0]['label_auto'])          # 确认过的不再是草稿
        labels = {x['block_label'] for x in self.result['items'] if x['block'] == 1}
        self.assertEqual(labels, {'主网设备（35kV及以上）'})

    def test_period_hint_and_missing_file(self):
        self.assertEqual(PL.period_hint('XX公司电网工程主要设备材料信息价1031.xlsx'), '1031')
        self.assertEqual(PL.period_hint('信息价.xlsx'), '')
        with self.assertRaisesRegex(ValueError, '信息价文件不存在'):
            PL.parse(Path(self.temp.name) / '没有这个文件.xlsx')

    def test_workbook_without_price_header_is_refused(self):
        other = Path(self.temp.name) / '清册.xlsx'
        book = openpyxl.Workbook()
        book.active.append(['序号', '设备材料名称', '型号及规范', '单位', '数量'])
        book.active.append(['1', '隔离开关', 'GW13-72.5', '台', 2])
        book.save(other)
        with self.assertRaisesRegex(ValueError, '未从该工作簿解析出任何带单价'):
            PL.parse(other)


if __name__ == '__main__':
    unittest.main()
