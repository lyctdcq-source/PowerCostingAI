import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_client
import project_qa
import server as app
from test_result_qa import dataset, rows, reply, scripted, tool_results


def project_dataset():
    ds = dataset()
    ds['targets'].append({'序号': 186, '设备材料名称': '时钟同步系统', '单位': '套', '数量': '1', '层级': '电气二次', '层级路径': ['电气二次'],
                          '成套行': True, '下级物资': [187], '品类': ['二次及自动化设备', '时间同步']})
    ds['sources'][2]['成套组成'] = [{'name': 'GPS天线'}, {'name': '扩展装置'}]
    ds['files'] = [{'name': '清册.xlsx'}, {'name': '可研.xlsx'}]
    return ds


class ProjectQueryTests(unittest.TestCase):
    def test_profile_counts_sets_by_each_definition(self):
        overview = project_qa.profile('测试工程', project_dataset(), rows(), {'134': [{'target': '187'}]})
        sets = overview['成套物资']
        self.assertEqual(sets['清册成套行']['条数'], 1)
        self.assertIn('时钟同步系统', sets['清册成套行']['条目'][0])
        self.assertEqual(sets['造价声明组成件的成套项']['条数'], 1)
        self.assertEqual(sets['匹配中整体覆盖清册组成件的造价成套项']['覆盖清册组成件'], 1)
        self.assertEqual(sets['以套计量的条目（不等于成套物资）']['清册'], 3)
        self.assertEqual(overview['清册']['条目'], 6)
        self.assertEqual(overview['清册']['各父级条目']['电缆辅助措施'], 3)
        self.assertEqual(overview['匹配结果']['结论分布']['不一致'], 1)
        self.assertEqual(project_qa.profile('测试工程', project_dataset(), [], {})['匹配结果'], '尚未完成物资匹配')

    def test_only_valid_queries_run(self):
        queries = project_qa.clean_queries([
            {'side': '清册', 'where': [{'field': '成套行', 'op': '为是'}, {'field': '不存在', 'op': '等于', 'value': 1},
                                       {'field': '名称', 'op': 'exec', 'value': 'x'}], 'group_by': '父级', 'sum': '名称', 'list': 1},
            {'side': '数据库'}, 'drop table', *[{'side': '造价'}] * 5])
        self.assertEqual(len(queries), 1)  # 无条件的造价查询只会得到总条数，被丢弃
        self.assertEqual(len(project_qa.clean_queries([{'side': '造价', 'list': True}] * 6)), 4)
        self.assertEqual(queries[0], {'side': '清册', 'where': [{'field': '成套行', 'op': '为是'}], 'group_by': '父级', 'sum': None, 'list': True})

    def test_filters_groups_and_unit_aware_sums(self):
        ds = project_dataset()
        q = project_qa.clean_queries([{'side': '清册', 'where': [{'field': '父级', 'op': '等于', 'value': '电缆辅助措施'}],
                                       'group_by': '单位', 'sum': '数量'}])[0]
        result = project_qa.run_query(q, ds, rows())
        self.assertEqual(result['条数'], 3)
        self.assertEqual(result['合计'], {'套': 300.0, 'm': 80.0, '件': 90.0})
        self.assertEqual({g['值']: g['条数'] for g in result['分组']}, {'套': 1, 'm': 1, '件': 1})
        sets = project_qa.run_query(project_qa.clean_queries([{'side': '清册', 'where': [{'field': '成套行', 'op': '为是'}]}])[0], ds, rows())
        self.assertEqual([(i['名称'], i['成套行']) for i in sets['列表']], [('时钟同步系统', '是（组成件 1 条）')])
        plain = project_qa.run_query(dict(q, group_by=None, sum=None, list=True), ds, rows())['列表']
        self.assertFalse(any('成套行' in i for i in plain))
        bad = project_qa.run_query(project_qa.clean_queries([{'side': '结果', 'where': [{'field': '结论', 'op': '等于', 'value': '不一致'}]}])[0], ds, rows())
        self.assertEqual((bad['条数'], bad['列表'][0]['清册']), (1, '83 热镀锌钢托臂'))
        big = project_qa.run_query(project_qa.clean_queries([{'side': '清册', 'where': [{'field': '数量', 'op': '大于', 'value': '50'}], 'list': True}])[0], ds, rows())
        self.assertEqual(sorted(i['序号'] for i in big['列表']), ['75', '79', '83'])


class ProjectRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=3, scope='', rows=rows(), dataset=project_dataset(), covers={'134': [{'target': '187'}]},
                         messages=[], history=[], logs=[], running=False, executions=[], project='测试工程')
        app.CONFIG.update(api_key='test-key')
        self.immediate = patch.object(app.threading, 'Thread', lambda target, **kw: type('T', (), {'start': lambda self: target()})())
        self.immediate.start()

    def tearDown(self):
        self.immediate.stop()
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def ask(self, text, *replies):
        fake, calls = scripted(*replies)
        with patch.object(llm_client, 'request_chat', side_effect=fake):
            app.chat(text)
        return calls

    def test_project_question_answers_from_computed_tools(self):
        app.STATE['messages'] = [{'role': 'user', 'text': '热镀锌钢托臂需要调整配对吗？'},
                                 {'role': 'assistant', 'text': '不需要', 'qa': {'scope': '热镀锌钢托臂', 'keys': [['', '83']]}, 'mode': 'result'}]
        calls = self.ask('清册表中一共有多少成套物资？',
                         reply('', ('project_overview', {}),
                               ('query_project', {'queries': [{'side': '清册', 'where': [{'field': '成套行', 'op': '为是'}], 'list': True}]})),
                         reply('回答'))
        self.assertIn('热镀锌钢托臂需要调整配对吗', calls[0]['messages'][1]['content'])   # 模型能看到上一轮
        overview, results = tool_results(calls[1])
        self.assertEqual(overview['成套物资']['清册成套行']['条数'], 1)
        self.assertEqual(results[0]['条数'], 1)
        reply_msg = app.STATE['messages'][-1]
        self.assertEqual((reply_msg['mode'], reply_msg['text']), ('project', '回答'))
        self.assertNotIn('qa', reply_msg)
        self.assertIn('项目查询：清册 · 成套行为是 → 1 条', app.STATE['executions'][-1]['steps'][1]['text'])

    def test_invalid_query_and_missing_results_are_reported_to_model(self):
        calls = self.ask('清册有多少条？', reply('', ('query_project', {'queries': [{'side': '清册', 'where': [{'field': '不存在', 'op': '等于'}]}]})),
                         reply('回答'))
        self.assertIn('查询无效', tool_results(calls[1])[0]['error'])
        self.assertEqual(app.STATE['executions'][-1]['steps'][0]['status'], 'error')
        app.STATE['rows'] = []
        calls = self.ask('电缆辅助措施为什么不一致？', reply('', ('lookup_results', {'about': '电缆辅助措施'})), reply('还没有评审结果'))
        self.assertIn('还没有数量评审的匹配结果', tool_results(calls[1])[0]['error'])
        self.assertEqual(app.STATE['messages'][-1]['mode'], 'agent')


if __name__ == '__main__':
    unittest.main(verbosity=2)
