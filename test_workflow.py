import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import server as app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=1, scope='', rows=[], pairs={}, messages=[], history=[], logs=[], price_results=[], prices=[], versions=[])
        app.CONFIG.update(mode='online', api_key='')
        self.sources = [{'序号': 1, 'Estimation_Name': '导线', '型号及规范': 'A', 'Estimation_Unit': 'km', 'Estimation_Quantity': '1', '_source': {'file': 'test'}}]
        self.targets = [{'序号': 1, '设备材料名称': '导线', '型号及规范': 'A', '单位': 'm', '数量': '1000', '_source': {'file': 'test'}}, {'序号': 2, '设备材料名称': '独有物资', '型号及规范': '', '单位': '套', '数量': '1'}]
        app.STATE['dataset'] = {'sources': self.sources, 'targets': self.targets}

    def tearDown(self):
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def test_execution_records_group_progress_and_keep_failure_visible(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        app.STATE.update(running=False, executions=[])
        def work():
            app.message('正在读取资料')
            app.execution_log('已读取工作表')
            app.message('清洗完成')
        with patch.object(app.threading, 'Thread', ImmediateThread):
            app.background(work)
            record = app.STATE['executions'][-1]
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(record['steps'][0]['text'], '已读取工作表')
            self.assertTrue(all(m['run_id'] == record['id'] for m in app.STATE['messages']))
            self.assertGreaterEqual(record['elapsed_seconds'], 0)
            def fail(): raise ValueError('测试失败')
            app.background(fail)
        self.assertEqual(app.STATE['executions'][-1]['status'], 'failed')
        self.assertIn('测试失败', app.STATE['messages'][-1]['text'])
        self.assertFalse(app.STATE['running'])
        app.message('独立通知')
        self.assertNotIn('run_id', app.STATE['messages'][-1])

    def test_checkpoint_pipeline_and_both_sides(self):
        app.CONFIG['api_key'] = 'test-key'
        model = Mock()
        model.match_pairs.return_value = ({'1':'1'}, {})
        model.review.return_value = [
            {'源_序号': '1', '匹配_序号': '1', '匹配_发现': '基本一致：单位换算后相等'},
            {'源_序号': '', '匹配_序号': '2', '匹配_发现': '不一致：单侧条目'}]
        with self.assertRaisesRegex(ValueError, '确认框中同意'):
            app.advance()   # 未经同意不发送在线模型
        app.record_consent()
        with patch.object(app, 'engine', return_value=model):
            app.advance()
            self.assertEqual(app.STATE['stage'], 2)
            self.assertEqual(len(app.STATE['rows']), 2)
            app.advance()
        self.assertEqual(app.STATE['rows'][0]['band'], '基本一致')
        self.assertEqual(app.STATE['rows'][1]['band'], '不一致')
        app.advance()
        self.assertEqual(app.STATE['stage'], 4)
        self.assertEqual(len(app.STATE['history']), 3)
        app.chat('只处理 导线')
        self.assertEqual(app.STATE['stage'], 1)
        self.assertEqual(app.STATE['rows'], [])

    def test_invalid_target_and_reuse_are_rejected(self):
        with self.assertRaises(ValueError):
            app.pair_rows(self.sources, self.targets, {'1': '999'})
        with self.assertRaises(ValueError):
            app.pair_rows(self.sources * 2, self.targets, {'1': '1', '2': '1'})

    def test_missing_key_never_generates_demo_results(self):
        with self.assertRaisesRegex(ValueError, 'API Key'):
            app.advance()
        self.assertEqual(app.STATE['stage'], 1)
        self.assertEqual(app.STATE['rows'], [])
        with self.assertRaisesRegex(ValueError, '样例入口已移除'):
            app.load('sample')
        with self.assertRaisesRegex(ValueError, '工程文件夹不存在'):
            app.load('folder', 'unused')   # 资料清洗在本机按规则完成，不需要 API Key

    def test_price_basis_and_inferred_guard(self):
        app.price_seed()
        p = dict(app.STATE['prices'][0], price=23980)
        app.price_compare([p])
        self.assertAlmostEqual(app.STATE['price_results'][0]['delta'], 10)
        p['tax'] = '含税'
        app.price_compare([p])
        self.assertIsNone(app.STATE['price_results'][0]['delta'])
        p['tax'] = '除税'
        app.STATE['prices'][0]['inferred'] = True
        app.price_compare([p])
        self.assertIsNone(app.STATE['price_results'][0]['delta'])

    def test_model_adapter_honors_all_call_sites(self):
        mod = app.engine(dict(app.CONFIG, base_url='https://example.test/v1', model='custom-model', batch=2))
        observed = []
        class Response:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':'{"density":8900,"rows":[]}'}}]}
        def post(url, **kwargs):
            observed.append((url, kwargs['json']['model']))
            return Response()
        with patch.object(mod.requests, 'post', side_effect=post):
            mod._ask_llm('test', 'dummy')
            mod._ACTIVE_API_KEY = 'dummy'
            mod._resolve_density_via_llm('测试金属')
        self.assertEqual(observed, [('https://example.test/v1/chat/completions','custom-model')]*2)

    def test_secret_not_persisted(self):
        app.CONFIG['api_key'] = 'test-secret-do-not-save'
        app.persist()
        self.assertNotIn('test-secret-do-not-save', (app.DATA/'state.json').read_text(encoding='utf-8'))

    def test_scoped_sample_stable_ids(self):
        app.STATE['scope'] = '独有物资'
        s, t = app.scoped()
        self.assertEqual(s, [])
        self.assertEqual(t[0]['序号'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
