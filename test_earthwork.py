"""土石方数智化算量：数据与质检、动作校验、本机规则、智能体编排（模型调用以桩替代）、报告、离线资源。"""
import copy
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import earthwork
import server as app

ROOT = Path(__file__).resolve().parent
SNAPSHOT = {
    'design': {'H': 1843.07, 'iL': 2, 'iT': 0, 'mode': 'rule', 'zLo': 1841.64, 'zHi': 1844.86, 'synth': 2},
    'quantities': {'cut': 70387, 'fill': 34341, 'spoil': 38598, 'borrow': 0, 'inCut': 64865, 'inFill': 34341, 'outCut': 5523,
                   'wall': 6611, 'wFH': 9.1, 'wYH': 3.0, 'slopeA': 1160, 'raisedL': 0},
    'cost': {'total': 7090353, 'items': {'挡墙': 4098820, '护坡': 110200}},
    'prices': {'wall': 620, 'clay': 16}, 'rules': {'iMax': 2}, 'context': {'hRange': [1835, 1853]},
}


class EarthworkDataTests(unittest.TestCase):
    def test_demo_model_loads_and_quality_reports_known_facts(self):
        model = earthwork.load_model('')
        self.assertEqual((model['nx'], model['ny'], len(model['holes'])), (179, 145, 93))
        data = earthwork.sources('')
        self.assertTrue(data['demo'])
        self.assertEqual((len(data['points']), len(data['holes'])), (553, 93))
        self.assertEqual(data['holes'][0]['segs'][0][0], '耕植土')
        stats = data['quality']['stats']
        # 钻孔分层表：XZK20、XZK21 终孔在粘土中，中风化砂岩仅 XZK29 揭露
        self.assertEqual((stats['no_rock'], stats['medium']), (2, 1))
        text = json.dumps(data['quality'], ensure_ascii=False)
        self.assertIn('XZK29', text)
        self.assertNotIn('34571', text)   # 质检结果要发给模型，不能带坐标

    def test_workspace_model_overrides_demo_and_is_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(earthwork.model_path(folder), earthwork.DEMO / 'model.json')
            (Path(folder) / 'model.json').write_text('{"nx":1}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '缺少字段'):
                earthwork.load_model(folder)
        earthwork.load_model('')

    def test_snapshot_is_reduced_to_numbers_and_short_text(self):
        raw = copy.deepcopy(SNAPSHOT)
        raw['design']['note'] = 'x' * 500
        raw['evil'] = {'a': {'b': {'c': {'d': {'e': 1}}}}}
        cleaned = earthwork.clean_snapshot(raw)
        self.assertEqual(len(cleaned['design']['note']), 80)
        self.assertIsNone(cleaned['evil']['a']['b']['c'])
        with self.assertRaises(ValueError):
            earthwork.clean_snapshot({'design': {}})


class EarthworkActionTests(unittest.TestCase):
    def test_actions_are_whitelisted_and_range_checked(self):
        good = earthwork.validate_actions([
            {'type': 'set_design', 'dH': -0.5, 'iL': 1.5, 'mode': 'free', 'extra': 'ignored'},
            {'type': 'optimize', 'target': 'full'}, {'type': 'set_price', 'key': 'wall', 'value': '650'},
            {'type': 'set_rule', 'key': 'iMax', 'value': 1}, {'type': 'save_scheme', 'name': '甲\n方案'},
            {'type': 'section', 'dir': 'x', 'pos': 0.3}, {'type': 'set_view', 'view': 'top'}], SNAPSHOT)
        self.assertEqual(good[0], {'type': 'set_design', 'dH': -0.5, 'iL': 1.5, 'mode': 'free'})
        self.assertEqual((good[2]['value'], good[4]['name']), (650.0, '甲 方案'))
        for bad in ([{'type': 'run_code', 'code': 'x'}], [{'type': 'set_design', 'H': 2500}], [{'type': 'set_design', 'H': 1840, 'dH': 1}],
                    [{'type': 'set_design'}], [{'type': 'set_price', 'key': 'gold', 'value': 1}], [{'type': 'set_price', 'key': 'wall', 'value': -1}],
                    [{'type': 'set_design', 'iL': 30}], [{'type': 'optimize', 'target': 'all'}], [{'type': 'optimize', 'target': 'full'}] * 9, 'x'):
            with self.assertRaises(ValueError, msg=str(bad)):
                earthwork.validate_actions(bad, SNAPSHOT)

    def test_local_rules_parse_explicit_commands_only(self):
        plan = earthwork.local_plan
        self.assertEqual(plan('标高降低 0.5 m，纵坡改为 1.5%，横坡 0%'), [{'type': 'set_design', 'dH': -0.5, 'iL': 1.5, 'iT': 0.0}])
        self.assertEqual(plan('标高设为1842.5'), [{'type': 'set_design', 'H': 1842.5}])
        self.assertEqual(plan('把场地抬高1米'), [{'type': 'set_design', 'dH': 1.0}])
        self.assertEqual(plan('套用图纸方案，并保存方案 图纸方案'), [{'type': 'optimize', 'target': 'drawing'}, {'type': 'save_scheme', 'name': '图纸方案'}])
        self.assertEqual(plan('放坡寻优'), [{'type': 'optimize', 'target': 'full'}])
        self.assertEqual(plan('改成自由放坡'), [{'type': 'set_design', 'mode': 'free'}])
        self.assertEqual(plan('为什么挡墙这么贵？'), [])
        self.assertEqual(plan('挖填平衡是什么意思'), [])
        for text in ('标高降低 0.5 m', '套用图纸方案，并保存方案 A'):
            earthwork.validate_actions(plan(text), SNAPSHOT)

    def test_cost_delta_and_local_answer_use_engine_numbers(self):
        after = copy.deepcopy(SNAPSHOT)
        after['cost'] = {'total': 7065177, 'items': {'挡墙': 3747900, '护坡': 124355}}
        self.assertEqual(earthwork.cost_delta(SNAPSHOT, after), {'total': -25176, 'items': {'挡墙': -350920, '护坡': 14155}})
        answer = earthwork.local_answer([{'label': '放坡寻优', 'ok': True, 'note': ''}, {'label': '保存方案', 'ok': False, 'note': '已满'}], SNAPSHOT, after)
        self.assertIn('未调用模型', answer)
        self.assertIn('706.5 万元', answer)
        self.assertIn('未能执行：保存方案（已满）', answer)
        self.assertIn('减少 2.5 万元', answer)

    def test_price_suggestions_keep_only_known_keys_ranges_and_retrieved_sources(self):
        result = {'suggestions': [{'key': 'wall', 'value': 680, 'basis': '信息价', 'source': 'KB1'}, {'key': 'clay', 'value': 20, 'source': '某定额'},
                                  {'key': 'wall', 'value': 1e9}, {'key': 'unknown', 'value': 1}, 'junk']}
        out = earthwork.clean_suggestions(result, SNAPSHOT, {'KB1'})
        self.assertEqual([(s['key'], s['value'], s['source'], s['current']) for s in out], [('wall', 680.0, 'KB1', 620), ('clay', 20.0, '', 16)])

    def test_prompts_carry_summary_without_coordinates(self):
        prompt = earthwork.plan_prompt('标高降 0.5 米', SNAPSHOT, [{'name': '甲', 'snapshot': SNAPSHOT}])
        self.assertIn('set_design', prompt)
        self.assertIn('1843.07', prompt)
        self.assertNotRegex(prompt + earthwork.answer_prompt('x', [], SNAPSHOT, SNAPSHOT, []), r'3457\d{4}')


class EarthworkFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG), app.llm
        app.DATA = Path(self.temp.name)
        app.STATE.update(app.session_defaults(), running=False, pause=False, artifacts=[], workspace='')
        app.CONFIG['api_key'] = ''

    def tearDown(self):
        self.wait()
        app.DATA, app.llm = self.old[0], self.old[3]
        app.STATE.clear(); app.STATE.update(self.old[1])
        app.CONFIG.clear(); app.CONFIG.update(self.old[2])
        self.temp.cleanup()

    def wait(self):
        for _ in range(200):
            if not app.STATE['running']:
                return
            time.sleep(0.02)
        self.fail('后台任务未结束')

    def test_local_rules_plan_then_report_result_without_model(self):
        app.llm = lambda *a, **k: self.fail('未配置模型时不应调用模型')
        app.earthwork_chat('套用图纸方案', SNAPSHOT); self.wait()
        pending = app.STATE['earthwork']['pending']
        self.assertEqual([(a['type'], a['label']) for a in pending['actions']], [('optimize', '套用图纸方案')])
        with self.assertRaises(ValueError):
            app.earthwork_result({'id': 'other', 'steps': [], 'before': SNAPSHOT, 'after': SNAPSHOT})
        app.earthwork_result({'id': pending['id'], 'steps': [{'label': '套用图纸方案', 'ok': True}], 'before': SNAPSHOT, 'after': SNAPSHOT}); self.wait()
        self.assertIsNone(app.STATE['earthwork']['pending'])
        self.assertIn('未调用模型', app.STATE['messages'][-1]['text'])
        app.earthwork_chat('为什么挡墙这么贵', SNAPSHOT); self.wait()
        self.assertIsNone(app.STATE['earthwork']['pending'])
        self.assertIn('未配置在线模型', app.STATE['messages'][-1]['text'])

    def test_model_actions_are_validated_before_reaching_the_engine(self):
        app.CONFIG['api_key'] = 'test-key'
        prompts = []
        replies = [{'actions': [{'type': 'set_design', 'H': 99999}]}, {'actions': [], 'answer': '挡墙占造价的大部分。'},
                   {'actions': [{'type': 'set_design', 'dH': -0.5}, {'type': 'save_scheme', 'name': '降半米'}]}, {'answer': '已降低 0.5 m。'}]
        app.llm = lambda prompt, config=None: (prompts.append(prompt), replies.pop(0))[1]
        app.earthwork_chat('把标高改成 99999', SNAPSHOT); self.wait()
        self.assertIsNone(app.STATE['earthwork']['pending'])
        self.assertIn('未通过校验', app.STATE['messages'][-1]['text'])
        app.earthwork_chat('为什么挡墙这么贵', SNAPSHOT); self.wait()
        self.assertEqual(app.STATE['messages'][-1]['text'], '挡墙占造价的大部分。')
        app.earthwork_chat('标高降半米存一下', SNAPSHOT); self.wait()
        pending = app.STATE['earthwork']['pending']
        self.assertEqual([a['type'] for a in pending['actions']], ['set_design', 'save_scheme'])
        app.earthwork_result({'id': pending['id'], 'steps': [{'label': x['label'], 'ok': True} for x in pending['actions']], 'before': SNAPSHOT, 'after': SNAPSHOT}); self.wait()
        self.assertEqual((app.STATE['messages'][-1]['text'], app.STATE['messages'][-1]['mode']), ('已降低 0.5 m。', 'earthwork'))
        self.assertTrue(all('3457' not in p for p in prompts))

    def test_schemes_report_and_session_reset(self):
        self.assertEqual(app.earthwork_scheme({'op': 'save', 'snapshot': SNAPSHOT}), '方案1')
        self.assertEqual(app.earthwork_scheme({'op': 'save', 'name': '方案1', 'snapshot': SNAPSHOT}), '方案1（2）')
        scheme_id = app.STATE['earthwork']['schemes'][0]['id']
        app.earthwork_report(); self.wait()
        artifact = app.STATE['earthwork']['artifacts'][0]
        self.assertTrue((app.DATA / 'outputs' / artifact['id']).stat().st_size > 5000)
        self.assertIn(artifact, app.STATE['artifacts'])
        from docx import Document
        doc = Document(str(app.DATA / 'outputs' / artifact['id']))
        text = '\n'.join(p.text for p in doc.paragraphs) + '\n'.join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        self.assertIn('709.0', text)
        self.assertIn('方案1（2）', text)
        app.earthwork_scheme({'op': 'delete', 'id': scheme_id})
        self.assertEqual([s['name'] for s in app.STATE['earthwork']['schemes']], ['方案1（2）'])
        with self.assertRaises(ValueError):
            app.earthwork_qc(SNAPSHOT)   # 需要模型
        self.assertIsNone(app.session_defaults()['earthwork'])


class EarthworkOfflineTests(unittest.TestCase):
    def test_three_is_vendored_and_static_has_no_external_script_or_style(self):
        three = ROOT / 'static' / 'lib' / 'three'
        for name in ('three.module.js', 'addons/controls/OrbitControls.js', 'addons/controls/PointerLockControls.js', 'LICENSE'):
            self.assertTrue((three / name).is_file(), name)
        self.assertIn("REVISION = '160'", (three / 'three.module.js').read_text(encoding='utf-8'))
        index = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('"three":"/lib/three/three.module.js"', index)
        self.assertNotRegex(index, r'<(script|link)[^>]+(src|href)="https?://')
        for path in list((ROOT / 'static' / 'earthwork').glob('*')) + [ROOT / 'static' / 'earthwork-ui.js']:
            source = path.read_text(encoding='utf-8')
            self.assertNotRegex(source, r'https?://', path.name)
            for spec in re.findall(r'''(?:from|import)\s*\(?\s*['"]([^'"]+)['"]''', source):
                self.assertTrue(spec.startswith(('./', '/earthwork/', 'node:')) or spec == 'three' or spec.startswith('three/addons/'), f'{path.name}: {spec}')

    @unittest.skipUnless(shutil.which('node'), '需要 node 运行计算引擎回归')
    def test_engine_reproduces_drawing_scheme_quantities(self):
        proc = subprocess.run(['node', str(ROOT / 'static' / 'earthwork' / 'engine.test.mjs')], capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual((got['H'], got['wall'], got['slopeA']), (1843.07, 6611, 1160))


if __name__ == '__main__':
    unittest.main()
