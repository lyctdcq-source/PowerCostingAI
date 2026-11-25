"""对话修改数量评审结果：写入工具、修改校验技能、批准卡片、修改记录与撤销。"""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_client
import pipeline_skills
import server as app
from test_result_qa import dataset, rows, reply, scripted, tool_results


class EditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=3, scope='', rows=rows(), dataset=dataset(), messages=[], history=[], logs=[], running=False, executions=[],
                         pairs={'1': '5', '193': '75', '197': '79'}, covers={'134': [{'target': '187'}]}, price_results=[],
                         result_edits=[], last_edit='', edit_mode='auto', approval=None, material_artifacts=[{'id': 'r.docx'}])
        app.CONFIG.update(api_key='test-key', base_url='https://llm.example.com/v1', web_search=False, thinking=False)
        self.immediate = patch.object(app.threading, 'Thread', lambda target, **kw: type('T', (), {'start': lambda self: target()})())

    def tearDown(self):
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def ask(self, text, *replies, refs=None, admin=True):
        fake, calls = scripted(*replies)
        with self.immediate, patch.object(llm_client, 'request_chat', side_effect=fake):
            app.chat(text, refs, admin=admin, user='tester')
        return calls

    def row(self, source, target):
        return next(r for r in app.STATE['rows'] if (r['source_id'], r['target_id']) == (source, target))

    def test_set_verdict_marks_row_records_edit_and_invalidates_report(self):
        refs = [{'kind': 'rows', 'keys': [['193', '75']], 'label': '清册75 支架'}]
        calls = self.ask('这一条改为一致', reply('', ('lookup_results', {'about': '这一条', 'use_refs': True})),
                         reply('', ('set_verdict', {'items': [{'source_id': '193', 'target_id': '75', 'band': '一致', 'reason': '按图纸支架单重折算数量一致'}]})),
                         reply('已把[R2]改为一致（E1）。'), refs=refs)
        self.assertIn('set_verdict', calls[0]['tools'])
        row = self.row('193', '75')
        self.assertEqual((row['band'], row['confirmed'], row['edited'], row['id']), ('一致', True, 'E1', 2))
        self.assertTrue(row['reason'].startswith('一致。按图纸支架单重折算数量一致'))
        edit = app.STATE['result_edits'][0]
        self.assertEqual((edit['label'], edit['changes'][0]['修改前'], edit['user']), ('改结论', '部分一致', 'tester'))
        self.assertIn('对话修改前 E1', [h['label'] for h in app.STATE['history']])
        self.assertEqual(app.STATE['material_artifacts'], [])
        result = tool_results(calls[2])[-1]
        self.assertEqual((result['修改编号'], result['已修改'][0]['行']), ('E1', 'R2'))
        msg = app.STATE['messages'][-1]
        self.assertEqual((msg['mode'], msg['edits'][0]['id'], [c['key'] for c in msg['citations']]), ('edit', 'E1', ['R2']))

    def test_model_sees_edit_history_beyond_recent_dialogue(self):
        self.ask('改', reply('', ('set_verdict', {'items': [{'source_id': '193', 'target_id': '75', 'band': '一致', 'reason': '按图纸核实一致'}]})), reply('好'))
        calls = self.ask('撤销刚才对支架的修改', reply('已撤销'))
        system = calls[0]['messages'][0]['content']
        self.assertIn('E1', system)
        self.assertIn('部分一致 → 一致', system)
        self.assertIn('依据：按图纸核实一致', system)
        self.assertIn('[R2]', system)
        self.assertIn('本次对话已允许自动修改', system)

    def test_edit_skill_refusals_go_back_to_model_without_changes(self):
        cases = [{'source_id': '', 'target_id': '187', 'band': '一致', 'reason': '与时钟同步系统一致'},   # 成套组成件
                 {'source_id': '', 'target_id': '83', 'band': '一致', 'reason': '托臂已含在支架中'},         # 单侧条目不能定为一致
                 {'source_id': '193', 'target_id': '75', 'band': '一致', 'reason': ''}]                    # 缺依据
        calls = self.ask('改结论', *[reply('', ('set_verdict', {'items': [c]})) for c in cases], reply('未修改'))
        errors = [tool_results(c)[-1]['error'] for c in calls[1:]]
        self.assertIn('成套项', errors[0])
        self.assertIn('只能定为不一致或不参与核对', errors[1])
        self.assertIn('须写明依据', errors[2])
        self.assertEqual(app.STATE['result_edits'], [])
        self.assertEqual([s['status'] for s in app.STATE['executions'][-1]['steps']], ['refused'] * 3)
        self.assertEqual(self.row('193', '75')['band'], '部分一致')

    def test_anchor_verdict_carries_set_members(self):
        self.ask('改', reply('', ('set_verdict', {'items': [{'source_id': '134', 'target_id': '', 'band': '一致', 'reason': '成套组成与清册一致'}]})),
                 reply('好'))
        self.assertEqual((self.row('134', '')['band'], self.row('', '187')['band']), ('一致', '一致'))
        self.assertEqual(len(app.STATE['result_edits'][0]['changes']), 2)

    def test_change_pair_keeps_other_rows_and_marks_new_pairs_pending(self):
        before = {(r['source_id'], r['target_id']): r['id'] for r in app.STATE['rows']}
        with patch.object(app, 'request_approval', return_value=None) as ask:   # 改配对一律须批准，这里直接批准
            self.ask('造价193应对应清册83', reply('', ('change_pair', {'source_id': '193', 'target_id': '83', 'reason': '角钢用于托臂制作'})), reply('好'))
        ask.assert_called_once()
        self.assertEqual(app.STATE['pairs']['193'], '83')
        keys = {(r['source_id'], r['target_id']): r for r in app.STATE['rows']}
        self.assertEqual(keys[('1', '5')]['id'], before[('1', '5')])        # 未受影响的行号与结论不变
        self.assertEqual(keys[('1', '5')]['band'], '一致')
        self.assertEqual((keys[('193', '83')]['band'], keys[('', '75')]['band']), ('待复核', '待复核'))
        self.assertNotIn(('', '83'), keys)
        self.assertEqual(len({r['id'] for r in app.STATE['rows']}), len(app.STATE['rows']))
        self.assertEqual(app.STATE['stage'], 3)

    def test_change_pair_refusals(self):
        calls = self.ask('改配对', reply('', ('change_pair', {'source_id': '193', 'target_id': '79', 'reason': '规格相同的支架'})), reply('未改'))
        self.assertIn('已与造价197配对', tool_results(calls[1])[0]['error'])
        calls = self.ask('改配对', reply('', ('change_pair', {'source_id': '193', 'target_id': '83', 'reason': '角钢用于托臂制作'})), reply('未改'),
                         admin=False)
        self.assertIn('管理员', tool_results(calls[1])[0]['error'])
        self.assertEqual(app.STATE['pairs']['193'], '75')

    def test_undo_restores_previous_result_and_blocks_after_manual_change(self):
        self.ask('改', reply('', ('set_verdict', {'items': [{'source_id': '193', 'target_id': '75', 'band': '一致', 'reason': '按图纸核实一致'}]})), reply('好'))
        calls = self.ask('撤销刚才的修改', reply('', ('undo_edit', {})), reply('已撤销'))
        self.assertEqual(tool_results(calls[1])[0]['已撤销'], 'E1')
        self.assertEqual(tool_results(calls[1])[0]['恢复'][0]['行'], 'R2')
        self.assertEqual([c['key'] for c in app.STATE['messages'][-1]['citations']], [])   # 回答“已撤销”未引用行号
        self.assertEqual(self.row('193', '75')['band'], '部分一致')
        self.assertNotIn('edited', self.row('193', '75'))
        self.assertTrue(app.STATE['result_edits'][0]['undone'])
        with self.assertRaisesRegex(ValueError, '没有可撤销'):
            app.undo_result_edit()
        self.ask('改', reply('', ('set_verdict', {'items': [{'source_id': '197', 'target_id': '79', 'band': '基本一致', 'reason': '宽度写法不同'}]})), reply('好'))
        app.checkpoint('人工复核条目 3')
        with self.assertRaisesRegex(ValueError, '又有改动'):
            app.undo_result_edit('E2')

    def test_ask_mode_waits_for_approval_and_deny_leaves_result(self):
        app.STATE['edit_mode'] = 'ask'
        for decision, band in (('deny', '部分一致'), ('allow', '一致')):
            fake, calls = scripted(reply('', ('set_verdict', {'items': [{'source_id': '193', 'target_id': '75', 'band': '一致', 'reason': '按图纸核实一致'}]})),
                                   reply('结束'))
            with patch.object(llm_client, 'request_chat', side_effect=fake):
                app.chat('造价193改为一致', admin=True)
                card = None
                for _ in range(200):
                    card = app.STATE.get('approval')
                    if card:
                        break
                    time.sleep(0.02)
                self.assertEqual(card['changes'][0]['修改前'], '部分一致')
                self.assertEqual(card['changes'][0]['修改后'], '一致')
                app.answer_approval(card['id'], decision)
                for _ in range(200):
                    if not app.STATE['running']:
                        break
                    time.sleep(0.02)
            self.assertFalse(app.STATE['running'])
            self.assertIsNone(app.STATE['approval'])
            self.assertEqual(self.row('193', '75')['band'], band)
            steps = [s['status'] for s in app.STATE['executions'][-1]['steps']]
            self.assertEqual(steps, ['waiting', 'refused' if decision == 'deny' else 'ok'])
            if decision == 'deny':
                self.assertIn('没有批准', tool_results(calls[1])[0]['error'])

    def test_allow_all_switches_to_auto_but_batches_still_need_approval(self):
        app.STATE['edit_mode'] = 'ask'
        items = [{'source_id': s, 'target_id': t, 'band': '一致', 'reason': '核实一致'} for s, t in (('1', '5'), ('193', '75'), ('197', '79'))]
        gate = app.edit_gate({'admin': True, 'notes': []})
        tool = app.agent.Tool('set_verdict', '', {}, lambda a: None, read_only=False, label='改结论')
        with patch.object(app, 'request_approval', return_value=None) as ask:
            self.assertIsNone(gate(tool, {'items': items}))
            ask.assert_called_once()
            app.STATE['edit_mode'] = 'auto'
            self.assertIsNone(gate(tool, {'items': items}))
            self.assertEqual(ask.call_count, 1)                     # 自动修改模式下少量修改不再询问
            with patch.object(app, 'run_edit_check', return_value={'allow': True, 'reason': '', 'confirm': True, 'notes': []}):
                gate(tool, {'items': items})
            self.assertEqual(ask.call_count, 2)                     # 校验技能要求批准时仍询问

    def test_routing_sends_result_edits_to_agent_and_rule_edits_to_skills(self):
        with self.immediate, patch.object(app, 'edit_skill') as edit, patch.object(app, 'agent_answer') as answer:
            app.chat('把造价193这条结论改为一致')
            app.chat('撤销刚才的修改')
            edit.assert_not_called()
            self.assertEqual(answer.call_count, 2)
            app.chat('忽略文件名包含临时施工用电的文件')
            edit.assert_called_once()

    def test_edit_check_skill_cannot_drop_baseline(self):
        code = app.PIPELINE.code('edit_check')
        weak = code.replace('    if owner:\n        return _refuse', '    if False:\n        return _refuse')
        self.assertNotEqual(weak, code)
        with self.assertRaisesRegex(ValueError, '已被占用'):
            pipeline_skills.edit_contract(weak)
        self.assertTrue(pipeline_skills.edit_contract(code)[0].startswith('修改校验底线测试通过'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
