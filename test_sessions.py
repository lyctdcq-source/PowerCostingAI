import copy
import tempfile
import unittest
from pathlib import Path

import server as app


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DATA, app.SESSIONS, copy.deepcopy(app.STATE)
        app.DATA = Path(self.temp.name)
        app.SESSIONS = app.DATA / 'sessions'
        app.STATE.update(app.session_defaults(), running=False, session_id='first', session_created='2026-10-16 09:00', sessions=[])

    def tearDown(self):
        app.DATA, app.SESSIONS = self.old[0], self.old[1]
        app.STATE.clear(); app.STATE.update(self.old[2])
        self.temp.cleanup()

    def test_switching_workspace_archives_and_reopens_review(self):
        app.STATE.update(workspace=r'D:\工程\甲', project='甲', stage=3, rows=[{'id': 1}], messages=[{'role': 'user', 'text': '问甲'}])
        app.start_session(r'D:\工程\乙')
        self.assertEqual((app.STATE['messages'], app.STATE['rows'], app.STATE['project']), ([], [], '乙'))
        self.assertNotEqual(app.STATE['session_id'], 'first')
        listed = app.session_list()
        self.assertEqual([(s['project'], s['stage'], s['rows']) for s in listed], [('甲', '数量评审', 1)])  # 空的新会话不列出
        app.STATE['messages'].append({'role': 'user', 'text': '问乙'})
        second = app.STATE['session_id']
        app.open_session('first')
        self.assertEqual((app.STATE['project'], app.STATE['messages'][0]['text'], app.STATE['stage']), ('甲', '问甲', 3))
        self.assertEqual([s['project'] for s in app.session_list()], ['乙', '甲'])  # 按创建时间，当前会话不置顶
        self.assertTrue(app.session_list()[1]['current'])
        app.open_session(second)
        self.assertEqual(app.STATE['messages'][0]['text'], '问乙')

    def test_empty_session_is_reused_and_running_blocks_switch(self):
        app.start_session(r'D:\工程\甲')
        self.assertEqual((app.STATE['session_id'], app.STATE['workspace']), ('first', r'D:\工程\甲'))
        self.assertEqual(app.session_list(), [])
        app.STATE['running'] = True
        with self.assertRaisesRegex(ValueError, '正在执行'):
            app.start_session(r'D:\工程\乙')

    def test_history_order_stays_fixed_when_switching(self):
        app.STATE.update(workspace=r'D:\工程\甲', project='甲', messages=[{'role': 'user', 'text': '甲'}])
        app.start_session(r'D:\工程\乙')
        app.STATE['session_created'] = '2026-10-16 09:01:00'
        app.STATE['messages'].append({'role': 'user', 'text': '乙'})
        app.start_session(r'D:\工程\丙')
        app.STATE['session_created'] = '2026-10-16 09:02:00'
        app.STATE['messages'].append({'role': 'user', 'text': '丙'})
        order = lambda: [s['project'] for s in app.session_list()]
        self.assertEqual(order(), ['丙', '乙', '甲'])
        updated = {s['project']: s['updated'] for s in app.session_list()}
        app.open_session('first')
        self.assertEqual(order(), ['丙', '乙', '甲'])
        app.open_session(next(s['id'] for s in app.session_list() if s['project'] == '乙'))
        self.assertEqual(order(), ['丙', '乙', '甲'])
        self.assertEqual(next(s for s in app.session_list() if s['project'] == '甲')['updated'], updated['甲'])  # 只是打开查看，不算更新

    def test_task_without_workspace_is_named_by_first_question(self):
        app.STATE.update(messages=[{'role': 'user', 'text': '铜排密度怎么取？'}])
        self.assertEqual(app.session_summary()['project'], '铜排密度怎么取？')
        app.STATE.update(workspace=r'D:\工程\甲', project='甲')
        self.assertEqual(app.session_summary()['project'], '甲')

    def test_delete_removes_archive(self):
        app.STATE.update(project='甲', messages=[{'role': 'user', 'text': '问甲'}])
        app.start_session(r'D:\工程\乙')
        self.assertTrue((app.SESSIONS / 'first.json').is_file())
        app.delete_session('first')
        self.assertFalse((app.SESSIONS / 'first.json').exists())
        self.assertEqual(app.session_list(), [])
        with self.assertRaisesRegex(ValueError, '已不存在'):
            app.open_session('first')

    def test_drawing_sessions_categorized_separately_and_legacy_migrated(self):
        # 1) 物资评审会话归类为 quantity
        app.STATE.update(workspace=r'D:\工程\甲', project='甲', stage=3, rows=[{'id': 1}], messages=[{'role': 'user', 'text': '问甲'}])
        self.assertEqual(app.session_summary()['kind'], 'quantity')

        # 2) 新建图纸提资会话归类为 drawing，并以工程目录命名
        app.start_session(workspace='', kind='drawing')
        app.STATE['session_created'] = '2026-10-16 09:05:00'
        app.STATE['tizi'] = {
            'folder': r'E:\提资项目\提资前期技术预演',
            'project_name': '提资前期技术预演',
            'workbook_summary': {'towers': 214},
            'summary': {'rows': 214},
        }
        sm_draw = app.session_summary()
        self.assertEqual(sm_draw['kind'], 'drawing')
        self.assertEqual(sm_draw['project'], '提资前期技术预演')
        self.assertEqual(sm_draw['stage'], '杆塔提资 · 214基')

        # 3) 历史列表中缺失 kind 的旧提资记录自动迁移归类为 drawing
        app.STATE['sessions'].append({
            'id': 'legacy-tizi-1',
            'project': '提资前期技术预演',
            'workspace': r'E:\提资项目\提资前期技术预演',
            'created': '2026-10-16 08:00:00',
            'updated': '2026-10-16 08:10',
            'stage': '杆塔提资 · 214基',
            'rows': 0,
            'messages': 4,
        })
        listed = {s['id']: s for s in app.session_list()}
        self.assertEqual(listed['first']['kind'], 'quantity')
        self.assertEqual(listed[app.STATE['session_id']]['kind'], 'drawing')
        self.assertEqual(listed['legacy-tizi-1']['kind'], 'drawing')


class TaskStepTests(unittest.TestCase):
    setUp, tearDown = SessionTests.setUp, SessionTests.tearDown

    def test_steps_check_workspace_and_stage_before_running(self):
        from unittest.mock import patch
        with patch.object(app, 'background') as run:
            with self.assertRaisesRegex(ValueError, '选择工作空间'):
                app.chat('资料清洗：……', step='clean')
            self.assertEqual(app.STATE['messages'], [])  # 不能执行时不记录用户消息
            app.STATE['workspace'] = r'D:\工程\甲'
            app.chat('资料清洗：……', step='clean')
            self.assertEqual(app.STATE['messages'][0]['text'], '资料清洗：……')
            with self.assertRaisesRegex(ValueError, '请先完成“资料清洗”'):
                app.chat('数量评审：……', step='quantity', consent=True)
            app.STATE['stage'] = 1
            app.STATE['dataset'] = {'sources': [], 'targets': []}
            app.CONFIG['api_key'] = 'test-key'
            with self.assertRaisesRegex(ValueError, '同意'):
                app.chat('数量评审：……', step='quantity')   # 未同意发送在线模型不能开始
            app.chat('数量评审：……', step='quantity', consent=True, user='admin')
            self.assertEqual((app.STATE['consent']['agreed'], app.STATE['consent']['user']), (True, 'admin'))
            app.chat('价格评审：……', step='price')   # 只审价不审量：清洗完成即可执行
            app.STATE['stage'] = 3
            with self.assertRaisesRegex(ValueError, '“数量评审”已完成'):
                app.chat('数量评审：……', step='quantity', consent=True)
            app.chat('价格评审：……', step='price')
            with self.assertRaisesRegex(ValueError, '未知'):
                app.chat('x', step='bogus')
        self.assertEqual(run.call_count, 4)
        app.STATE['stage'] = 0
        self.assertIn('资料清洗', app.step_error('price'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
