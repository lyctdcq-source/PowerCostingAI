"""价格评审第二遍：词面召回、模型判定与人工指定参考条目。

模型只在程序召回的候选里选，不接受候选以外的编号，也不接受漏答；置信度不足的不作为参考条目。
判定结果与人工指定都存成“指定”，阈值或口径改变后重算仍然沿用。
"""
import unittest
from unittest.mock import patch

import price_library as PL
import server


def entry(code, name, spec, unit, price, **extra):
    record = dict(code=code, name=name, spec=spec, unit=unit, price=price, tax='含税', tax_rate=13.0,
                  composition=1.0, period='20261031', region='XX公司', block=1, block_label='主网设备',
                  source='信息价1031.xlsx', row=int(code[-3:]), notes='', **extra)
    record['key'] = PL.item_key(record)
    return record


LIBRARY = [
    entry('B01R003', '110kV三相三绕组油浸式电力变压器', 'SSZ-50000/110', '台', 3380000),
    entry('B01R004', '110kV三相双绕组油浸式电力变压器', 'SZ-50000/110', '台', 2700000),
    entry('B01R005', '110kV三相三绕组油浸式电力变压器', 'SSZ-63000/110', '台', 3530000),
    entry('B02R010', '铝包钢芯铝绞线', 'JL/LB20A-300/40', '吨', 21011),
]
SOURCES = [
    {'序号': 1, 'Estimation_Name': '110kV 三相三绕组油浸式电力变压器　SSZ20-50000/110', '型号及规范': '',
     'Estimation_Unit': '台', 'Estimation_Quantity': '1', 'Estimation_Price': '3280000', 'Supply_Type': '甲设',
     '层级': '主变压器系统'},
    {'序号': 2, 'Estimation_Name': '铝包钢芯铝绞线　JL/LB20A 300/40', '型号及规范': '', 'Estimation_Unit': 't',
     'Estimation_Quantity': '0.017', 'Estimation_Price': '13034', 'Supply_Type': '乙材', '层级': '主变压器系统',
     '市场价': 18995.58, '比价基准': '主材价差表市场价（不含税）'},
]


class RecallTests(unittest.TestCase):

    def test_model_fragments_recall_a_different_spelling(self):
        """SSZ20-50000/110 要能召回 SSZ-50000/110：靠型号拆出的“50000”“110”。"""
        index = PL.build_recall_index(LIBRARY)
        got = PL.recall(dict(name='110kV 三相三绕组油浸式电力变压器　SSZ20-50000/110', spec='', unit='台'),
                        LIBRARY, index)
        self.assertEqual(got[0][0]['code'], 'B01R003')
        self.assertEqual(got[0][1], 100.0)                       # 分数归一到最高分
        self.assertGreater(len(got), 1)

    def test_unit_that_cannot_convert_is_ranked_lower(self):
        index = PL.build_recall_index(LIBRARY)
        ranked = PL.recall(dict(name='铝包钢芯铝绞线　JL/LB20A 300/40', spec='', unit='吨'), LIBRARY, index)
        self.assertEqual(ranked[0][0]['code'], 'B02R010')
        wrong_unit = PL.recall(dict(name='铝包钢芯铝绞线　JL/LB20A 300/40', spec='', unit='米'), LIBRARY, index)
        self.assertLess(wrong_unit[0][1], ranked[0][1])

    def test_nothing_recalled_without_shared_words(self):
        index = PL.build_recall_index(LIBRARY)
        self.assertEqual(PL.recall(dict(name='完全无关的东西', spec='', unit='台'), LIBRARY, index), [])


class JudgeTests(unittest.TestCase):

    def setUp(self):
        server.STATE.update(prices=[dict(x) for x in LIBRARY], price_results=[], price_picks={}, rows=[],
                            messages=[], logs=[], threshold=10, cost_tax='', stage=1, scope='', pause=False,
                            dataset=dict(label='样例', origin='', warnings=[], targets=[],
                                         sources=[dict(x) for x in SOURCES]))
        self.key = server.CONFIG['api_key']
        server.CONFIG['api_key'] = 'test-key'
        server.price_review()

    def tearDown(self):
        # STATE 是全局的：指定记录与价格库必须还原，否则后面的用例会读到本用例的残留
        server.STATE.update(price_picks={}, prices=[], price_results=[], rows=[], dataset=None)
        server.CONFIG['api_key'] = self.key

    def judge(self, reply):
        with patch.object(server, 'llm', return_value=reply) as call:
            server.price_judge()
        return call

    def test_pending_items_get_candidates_and_a_pick(self):
        self.assertEqual([r['status'] for r in server.STATE['price_results']], ['型号不同', '型号不同'])
        call = self.judge({'results': [
            {'id': '1', 'code': 'B01R003', 'confidence': 90, 'reason': '同为110kV三绕组50000kVA，型号写法不同'},
            {'id': '2', 'code': 'B02R010', 'confidence': 88, 'reason': '同为铝包钢芯铝绞线300/40'}]})
        prompt = call.call_args[0][0]
        self.assertIn('候选 B01R003', prompt)
        self.assertIn('不要勉强凑', prompt)
        self.assertNotIn('相关度', prompt)                        # 召回分不给模型，避免被分数带偏
        first = server.STATE['price_results'][0]
        self.assertEqual((first['status'], first['pick']['by'], first['pick']['confidence']), ('已比较', '模型', 90))
        self.assertAlmostEqual(first['delta'], (3280000 - 3380000) / 3380000 * 100, places=6)
        self.assertIn('模型指定参考条目（置信度 90）：同为110kV三绕组50000kVA', first['reason'])
        self.assertIn('认定同一物资 2 条', server.STATE['messages'][-1]['text'])

    def test_low_confidence_and_none_are_not_adopted(self):
        self.judge({'results': [{'id': '1', 'code': 'B01R003', 'confidence': 40, 'reason': '不确定'},
                                {'id': '2', 'code': 'none', 'confidence': 95, 'reason': '候选里没有同一物资'}]})
        self.assertEqual(server.STATE['price_picks'], {})
        self.assertEqual([r['status'] for r in server.STATE['price_results']], ['型号不同', '型号不同'])
        self.assertIn('置信度不足 2 条', server.STATE['messages'][-1]['text'])

    def test_invented_code_or_missing_row_fails_the_batch(self):
        self.judge({'results': [{'id': '1', 'code': 'B09R999', 'confidence': 99, 'reason': '编造的编号'},
                                {'id': '2', 'code': 'B02R010', 'confidence': 90, 'reason': '对的'}]})
        self.assertEqual(server.STATE['price_picks'], {})        # 整批作废，不采信其中任何一条
        self.assertIn('请求失败', server.STATE['messages'][-1]['text'])
        self.assertTrue(any('漏答' in x['text'] for x in server.STATE['logs']))

    def test_manual_pick_overrides_and_survives_recompute(self):
        self.judge({'results': [{'id': '1', 'code': 'B01R003', 'confidence': 90, 'reason': '同一物资'},
                                {'id': '2', 'code': 'B02R010', 'confidence': 88, 'reason': '同一物资'}]})
        server.STATE['price_picks']['1'] = dict(code='B01R005', by='人工', reason='按容量复核应取63000', confidence=100)
        server.price_compare(server.price_items(server.STATE['dataset']['sources']), server.set_exclusions())
        first = server.STATE['price_results'][0]
        self.assertEqual(first['reference']['code'], 'B01R005')
        self.assertIn('人工指定参考条目', first['reason'])
        server.chat('价格阈值 30', admin=True)                     # 改阈值重算，指定仍然生效
        self.assertEqual(server.STATE['price_results'][0]['reference']['code'], 'B01R005')

    def test_pick_pointing_at_a_removed_entry_is_reported(self):
        server.STATE['price_picks']['1'] = dict(code='B09R999', by='人工', reason='', confidence=100)
        server.price_compare(server.price_items(server.STATE['dataset']['sources']), server.set_exclusions())
        first = server.STATE['price_results'][0]
        self.assertEqual(first['status'], '指定条目已失效')
        self.assertIn('不在当前价格库版本里', first['reason'])

    def test_judge_needs_results_and_a_key(self):
        server.STATE['price_results'] = []
        with self.assertRaisesRegex(ValueError, '请先执行价格评审'):
            server.price_judge()
        server.price_review()
        server.CONFIG['api_key'] = ''
        with self.assertRaisesRegex(ValueError, 'API Key'):
            server.price_judge()


if __name__ == '__main__':
    unittest.main()
