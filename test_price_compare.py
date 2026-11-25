"""价格比价：口径换算、单位折算、匹配与定档，以及不能比时给出的具体原因。

造价侧单价按除税价，信息价按导入时声明的税口径换算；同名不同型号、一名多价、
不同量纲的单位都不自动取值，列原因交人工，不猜。
"""
import unittest

import price_library as PL

LIBRARY = [
    dict(code='B01R0003', name='110kV三相三绕组油浸式电力变压器', spec='SSZ-63000/110', unit='台', price=3530000,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', region='XX公司', block=1,
         block_label='主网设备', source='信息价1031.xlsx', row=3, notes='三相三绕组，有载', key=''),
    dict(code='B01R0004', name='110kV三相三绕组油浸式电力变压器', spec='SSZ-50000/110', unit='台', price=3380000,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', block=1, source='信息价1031.xlsx', row=4, key=''),
    dict(code='B02R0520', name='35kV交联聚乙烯绝缘电力电缆', spec='ZRA-YJV62-26/35 1×500', unit='km', price=441420,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', block=2, source='信息价1031.xlsx', row=520, key=''),
    dict(code='B02R0463', name='钢芯铝绞线', spec='JL/G1A-300/40', unit='吨', price=21404,
         tax='除税', tax_rate=13.0, composition=1.0, period='20261031', block=2, source='信息价1031.xlsx', row=463, key=''),
    dict(code='B04R2184', name='耐受类槽式电缆桥架', spec='槽式终端封头 300×150', unit='套', price=24,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', block=4, source='信息价1031.xlsx', row=2184, key=''),
    dict(code='B05R3203', name='耐受类槽式电缆桥架', spec='槽式终端封头 300×150', unit='套', price=5.5,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', block=5, source='信息价1031.xlsx', row=3203, key=''),
    dict(code='B02R0629', name='光纤复合架空地线(OPGW)', spec='OPGW-80-48-1-1', unit='千米', price=12400,
         tax='含税', tax_rate=13.0, composition=1.0, period='20261031', block=2, source='信息价1031.xlsx', row=629, key=''),
]
for entry in LIBRARY:
    entry['key'] = PL.item_key(entry)


def cost(name, spec='', unit='台', price=0, **extra):
    return dict(name=name, spec=spec, unit=unit, price=price, loose=True, **extra)


class ConversionTests(unittest.TestCase):

    def test_unit_factor_only_within_one_dimension(self):
        self.assertEqual(PL.unit_factor('千米', '米'), 1000)
        self.assertEqual(PL.unit_factor('km', 'm'), 1000)
        self.assertEqual(PL.unit_factor('吨', '公斤'), 1000)
        self.assertEqual(PL.unit_factor('t', 't'), 1)
        self.assertEqual(PL.unit_factor('台', '台'), 1)
        self.assertIsNone(PL.unit_factor('吨', '米'))        # 要按线密度换算，价格侧不自动做
        self.assertIsNone(PL.unit_factor('套', '只'))        # 计数单位不互换
        self.assertIsNone(PL.unit_factor('台', ''))

    def test_tax_composition_and_unit_conversion_chain(self):
        entry = dict(LIBRARY[2], composition=1.05)           # 441420 元/km 含税13%，构成系数1.05
        value, steps, why = PL.comparable_price(entry, 'm')
        self.assertEqual(why, '')
        self.assertAlmostEqual(value, 441420 / 1.13 * 1.05 / 1000, places=4)
        self.assertIn('÷(1+13%) 得除税价', steps)
        self.assertIn('×构成系数 1.05', steps)
        self.assertIn('÷1000（千米→米）', steps)

    def test_cost_side_declared_as_tax_inclusive_skips_conversion(self):
        value, steps, _ = PL.comparable_price(LIBRARY[0], '台', cost_tax='含税')
        self.assertEqual(value, 3530000)
        self.assertNotIn('除税', steps)

    def test_no_conversion_for_entries_already_tax_exclusive(self):
        value, _, _ = PL.comparable_price(LIBRARY[3], '吨')
        self.assertEqual(value, 21404)


class CompareTests(unittest.TestCase):

    def compare(self, items, **options):
        return PL.compare(items, LIBRARY, dict(threshold=10, **options))

    def test_exact_match_converts_and_classifies_with_direction(self):
        result = self.compare([cost('110kV三相三绕组油浸式电力变压器', 'SSZ-63000/110', '台', 3800000)])[0]
        self.assertEqual(result['status'], '已比较')
        self.assertEqual(result['reference']['code'], 'B01R0003')
        self.assertAlmostEqual(result['delta'], (3800000 - 3530000 / 1.13) / (3530000 / 1.13) * 100, places=6)
        self.assertEqual((result['band'], result['direction']), ('显著偏差', '偏高'))
        self.assertIn('÷(1+13%) 得除税价', result['reason'])
        self.assertIn('出处 信息价1031.xlsx 第3行（块1主网设备）', result['reason'])

    def test_name_and_model_inside_one_cost_cell(self):
        """造价把名称与型号写在一格：名称与型号都出现在里面就算对上。"""
        result = self.compare([cost('35kV交联聚乙烯绝缘电力电缆　ZRA-YJV62-26/35 1×500', '', 'm', 400)])[0]
        self.assertEqual(result['reference']['code'], 'B02R0520')
        self.assertIn('÷1000（千米→米）', result['conversion'])
        self.assertAlmostEqual(result['delta'], (400 - 441420 / 1.13 / 1000) / (441420 / 1.13 / 1000) * 100, places=6)

    def test_same_name_different_model_is_not_a_match(self):
        result = self.compare([cost('110kV三相三绕组油浸式电力变压器', 'SSZ20-40000/110', '台', 3000000)])[0]
        self.assertEqual((result['status'], result['band'], result['delta']), ('型号不同', '不可比', None))
        self.assertIn('价格库有同名条目 2 条但型号对不上', result['reason'])
        self.assertIn('SSZ-63000/110', result['reason'])
        self.assertEqual(len(result['candidates']), 2)

    def test_two_blocks_with_different_prices_need_a_person(self):
        result = self.compare([cost('耐受类槽式电缆桥架', '槽式终端封头 300×150', '套', 20)])[0]
        self.assertEqual((result['status'], result['delta']), ('候选多价', None))
        self.assertIn('2 条可能对应且换算后价格不同', result['reason'])
        self.assertIn('块4', result['reason'])
        self.assertEqual({c['price'] for c in result['candidates']}, {24, 5.5})

    def test_unit_across_dimensions_is_reported_not_guessed(self):
        result = self.compare([cost('钢芯铝绞线', 'JL/G1A-300/40', 'm', 60)])[0]
        self.assertEqual(result['status'], '口径不可比')
        self.assertIn('单位无法换算（信息价吨 → 造价米）', result['reason'])

    def test_missing_entry_and_missing_cost_price(self):
        results = self.compare([cost('某种没有的设备', 'XYZ-1', '台', 1000), cost('钢芯铝绞线', 'JL/G1A-300/40', '吨', 0)])
        self.assertEqual(results[0]['status'], '无对应条目')
        self.assertIn('需人工指定参考条目或补录', results[0]['reason'])
        self.assertEqual(results[1]['status'], '造价无单价')

    def test_set_members_are_excluded_by_quantity_review(self):
        item = cost('110kV三相三绕组油浸式电力变压器', 'SSZ-63000/110', '台', 3200000, source_id='7')
        result = PL.compare([item], LIBRARY, dict(exclusions={'7': '含于造价成套项（序号 5），不单独比价'}))[0]
        self.assertEqual((result['status'], result['band']), ('不参与比价', '不可比'))
        self.assertIn('含于造价成套项', result['reason'])

    def test_empty_library_says_so(self):
        result = PL.compare([cost('任意', '', '台', 1)], [], {})[0]
        self.assertEqual(result['status'], '价格库为空')

    def test_declared_caliber_must_line_up_when_not_loose(self):
        """单独导入的待核对价格自带口径：有一项对不上就不比。"""
        item = dict(name='钢芯铝绞线', spec='JL/G1A-300/40', unit='吨', price=22000,
                    period='20261031', region='XX公司', tax='除税', basis='出厂')
        self.assertEqual(self.compare([item])[0]['status'], '口径不可比')   # 信息价这条没有 region/basis
        rich = dict(LIBRARY[3], region='XX公司', basis='出厂')
        result = PL.compare([item], [rich], dict(threshold=10))[0]
        self.assertEqual(result['status'], '已比较')
        self.assertIn('口径同为 20261031 XX公司 除税 出厂', result['reason'])

    def test_bands_follow_the_threshold(self):
        base = 12400 / 1.13 / 1000                                    # 元/米，含税转除税后
        cases = [(base * 1.01, '一致'), (base * 1.04, '基本一致'), (base * 1.08, '偏差'), (base * 1.5, '显著偏差')]
        for price, band in cases:
            result = self.compare([cost('光纤复合架空地线(OPGW)', 'OPGW-80-48-1-1', 'm', round(price, 4))])[0]
            self.assertEqual(result['band'], band, f'{price} 应为 {band}')
        low = self.compare([cost('光纤复合架空地线(OPGW)', 'OPGW-80-48-1-1', 'm', round(base * 0.5, 4))])[0]
        self.assertEqual((low['band'], low['direction']), ('显著偏差', '偏低'))


if __name__ == '__main__':
    unittest.main()
