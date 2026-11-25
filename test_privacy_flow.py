"""数量评审前的脱敏与同意、清洗结果表格内修改、价格评审与结果导出。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import openpyxl

import desensitize
import server as app


def src(i, name, qty, unit, price='', group='主变部分'):
    return {'序号': i, 'Estimation_Name': name, 'Estimation_Quantity': qty, 'Estimation_Unit': unit, 'Estimation_Price': price,
            '型号及规范': '', '层级': group, '层级路径': [group], '_source': {'file': '可研估算书.xlsx', 'sheet': '表三甲', 'row': i + 3}}


def tgt(i, name, qty, unit, group='主变压器部分', notes=''):
    return {'序号': i, '设备材料名称': name, '数量': qty, '单位': unit, '型号及规范': '', '层级': group, '层级路径': [group], '备注': notes,
            '_source': {'file': '一次.xlsx', 'sheet': '电气一次', 'row': i + 2}}


class DesensitizeTests(unittest.TestCase):
    SOURCES = [src(1, '110kV线路保护屏', '1', '面', group='对侧220kV瑞丽变扩建110kV间隔工程')]
    TARGETS = [tgt(1, '110kV线路保护屏', '1', '面', group='对侧220kV瑞丽变扩建110kV间隔工程部分', notes='瑞丽线，北京四方继保自动化股份有限公司，联系人13812345678'),
               tgt(2, '站用变压器', '1', '台', group='10kV站用变部分'), tgt(3, '主变压器', '1', '台', group='110kV主变压器部分')]

    def test_station_names_get_one_alias_on_both_sides_and_come_back(self):
        mapping = desensitize.plan(self.SOURCES, self.TARGETS)
        self.assertEqual(mapping['stations'], {'瑞丽': '站点A'})   # 站用变、主变不是站名
        masked_s, masked_t = desensitize.apply(self.SOURCES, mapping), desensitize.apply(self.TARGETS, mapping)
        self.assertEqual(masked_s[0]['层级'], '对侧220kV站点A变扩建110kV间隔工程')
        self.assertEqual(masked_t[0]['备注'], '站点A线，某单位，联系人[手机号]')
        self.assertEqual(masked_t[1]['层级'], '10kV站用变部分')
        blob = json.dumps(masked_s + masked_t, ensure_ascii=False)
        for secret in ('瑞丽', '四方', '13812345678', '一次.xlsx', '电气一次'):
            self.assertNotIn(secret, blob)
        self.assertEqual(self.TARGETS[0]['层级'], '对侧220kV瑞丽变扩建110kV间隔工程部分')   # 原记录不变
        self.assertEqual(desensitize.restore({'匹配_发现': '一致：站点A变扩建间隔'}, mapping), {'匹配_发现': '一致：瑞丽变扩建间隔'})

    def test_every_model_prompt_is_masked_and_answers_restored(self):
        old = copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.STATE['dataset'] = {'sources': self.SOURCES, 'targets': self.TARGETS}
        app.CONFIG.update(api_key='k', base_url='https://example.test/v1', model='m')
        seen = []

        def fake(endpoint, model, key, messages, **kw):
            seen.append(messages[-1]['content'])
            return {'answer': '站点A的保护屏数量一致'}, {}
        try:
            with patch('llm_client.request_json', side_effect=fake):
                result = app.llm('瑞丽变的保护屏是否一致？厂家北京四方继保自动化股份有限公司')
        finally:
            app.STATE.clear(); app.STATE.update(old[0]); app.CONFIG.clear(); app.CONFIG.update(old[1])
        self.assertNotIn('瑞丽', seen[0])
        self.assertNotIn('四方', seen[0])
        self.assertEqual(result['answer'], '瑞丽的保护屏数量一致')


class ConsentAndEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.CONFIG['api_key'] = 'test-key'
        app.STATE.update(stage=1, scope='', rows=[], pairs={}, covers={}, messages=[], history=[], price_results=[], prices=[], consent={},
                         dataset={'label': '工程', 'files': [], 'warnings': [],
                                  'sources': [src(1, '110kV线路保护屏', '1', '面', '120000', group='对侧220kV瑞丽变扩建110kV间隔工程')],
                                  'targets': [tgt(1, '110kV线路保护屏', '1', '面', group='对侧220kV瑞丽变扩建110kV间隔工程部分')]})

    def tearDown(self):
        app.DATA = self.old[0]
        app.STATE.clear(); app.STATE.update(self.old[1])
        app.CONFIG.clear(); app.CONFIG.update(self.old[2])
        self.temp.cleanup()

    def test_engine_only_sees_masked_copies_and_consent_follows_data(self):
        with self.assertRaisesRegex(ValueError, '同意'):
            app.advance()
        app.record_consent('admin')
        model = Mock()
        model.match_pairs.return_value = ({'1': '1'}, {})
        model.build_rows.side_effect = lambda s, t, *a: [{'源_序号': '1', '匹配_序号': '1'}]
        model.review.return_value = [{'源_序号': '1', '匹配_序号': '1', '匹配_发现': '一致：站点A扩建间隔的保护屏名称、数量相同'}]
        with patch.object(app, 'engine', return_value=model):
            app.quantity_review()
        sent = json.dumps([model.match_pairs.call_args[0][:2], model.review.call_args[0][3:]], ensure_ascii=False)
        self.assertNotIn('瑞丽', sent)
        self.assertNotIn('可研估算书.xlsx', sent)
        self.assertEqual(app.STATE['stage'], 3)
        self.assertEqual(app.STATE['rows'][0]['reason'], '一致：瑞丽扩建间隔的保护屏名称、数量相同')
        self.assertTrue(any(h['label'] == '数量评审完成' for h in app.STATE['history']))
        # 修改清洗结果后，原同意不再适用于新数据
        app.apply_data_edits({'side': 'targets', 'changes': [{'id': 1, 'fields': {'quantity': '2', 'notes': '已核对'}}]})
        self.assertEqual((app.STATE['stage'], app.STATE['dataset']['targets'][0]['数量']), (1, '2'))
        self.assertIn('确认框中同意', app.consent_error())

    def test_batch_edit_validates_and_records_before_after(self):
        with self.assertRaisesRegex(ValueError, '非负数字'):
            app.apply_data_edits({'side': 'sources', 'changes': [{'id': 1, 'fields': {'quantity': '-1'}}]})
        with self.assertRaisesRegex(ValueError, '无效'):
            app.apply_data_edits({'side': 'sources', 'changes': [{'id': 1, 'fields': {'序号': '9'}}]})
        app.apply_data_edits({'side': 'sources', 'changes': [{'id': 1, 'fields': {'group': '间隔扩建 / 保护'}}], 'deletes': []})
        record = app.STATE['dataset']['sources'][0]
        self.assertEqual((record['层级'], record['层级路径']), ('间隔扩建 / 保护', ['间隔扩建', '保护']))
        self.assertEqual(record['_edits'][-1]['before'], {'group': '对侧220kV瑞丽变扩建110kV间隔工程'})
        app.apply_data_edits({'side': 'targets', 'deletes': [1]})
        self.assertEqual(app.STATE['dataset']['targets'], [])
        self.assertEqual(app.STATE['dataset']['deleted_rows'][0]['record']['设备材料名称'], '110kV线路保护屏')

    def test_price_review_and_excel_export(self):
        app.STATE['stage'] = 3
        app.price_review()
        self.assertEqual(app.STATE['price_results'][0]['reason'], '价格库尚未载入信息价')
        app.STATE['prices'] = [dict(code='P1', name='线路保护屏', spec='', unit='面', price=100000, period='2026-09', region='云南', tax='除税', basis='出厂', source='信息价', page=1)]
        app.price_review()
        result = app.STATE['price_results'][0]
        self.assertAlmostEqual(result['delta'], 20)
        self.assertIn('参考比较', result['reason'])
        self.assertTrue(any(h['label'] == '价格评审完成' for h in app.STATE['history']))
        app.STATE['rows'] = app.pair_rows(app.STATE['dataset']['sources'], app.STATE['dataset']['targets'], {'1': '1'})
        path = app.DATA / 'out.xlsx'
        app.write_results_xlsx(copy.deepcopy(app.STATE), path)
        book = openpyxl.load_workbook(path)
        self.assertEqual(book.sheetnames, ['结论汇总', '数量评审明细', '价格评审', '造价清洗数据', '清册清洗数据'])
        self.assertEqual(book['价格评审']['H2'].value, 20)


if __name__ == '__main__':
    unittest.main()
