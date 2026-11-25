import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_client
import result_qa
import server as app


def dataset():
    targets = [
        {'序号': 75, '设备材料名称': '400×400电缆沟支架', '单位': '套', '数量': '300', '层级': '电缆辅助措施', '层级路径': ['电缆辅助措施']},
        {'序号': 79, '设备材料名称': '铝合金水平电缆桥架', '单位': 'm', '数量': '80', '层级': '电缆辅助措施', '层级路径': ['电缆辅助措施']},
        {'序号': 83, '设备材料名称': '热镀锌钢托臂', '单位': '件', '数量': '90', '层级': '电缆辅助措施', '层级路径': ['电缆辅助措施']},
        {'序号': 187, '设备材料名称': 'GPS天线', '单位': '套', '数量': '1', '层级': '时钟同步系统', '层级路径': ['时钟同步系统']},
        {'序号': 5, '设备材料名称': '主变压器', '单位': '台', '数量': '1', '层级': '主变部分', '层级路径': ['主变部分']},
    ]
    sources = [
        {'序号': 193, 'Estimation_Name': '角钢（E-1-400）', 'Estimation_Unit': 't', 'Estimation_Quantity': '0.452', '层级': '1次', '层级路径': ['1次']},
        {'序号': 197, 'Estimation_Name': '铝合金水平电缆桥架　宽550mm', 'Estimation_Unit': 'm', 'Estimation_Quantity': '80', '层级': '1次', '层级路径': ['1次']},
        {'序号': 134, 'Estimation_Name': '时钟同步系统 ：', 'Estimation_Unit': '套', 'Estimation_Quantity': '1', '层级': '同步时钟', '层级路径': ['同步时钟']},
        {'序号': 1, 'Estimation_Name': '主变压器 SZ11-8000/35', 'Estimation_Unit': '台', 'Estimation_Quantity': '1', '层级': '35kV主变压器', '层级路径': ['主要生产工程', '35kV主变压器']},
    ]
    return {'targets': targets, 'sources': sources}


def rows():
    def row(i, s, t, band, reason, **extra):
        r = {'id': i, 'source_id': s, 'target_id': t, 'band': band, 'reason': reason, 'group': '电缆辅助措施' if t in ('75', '79', '83') else '',
             'name': '', 'target': '', 'spec': '', 'target_spec': '', 'quantity': '', 'unit': '', 'target_quantity': '', 'target_unit': '',
             'source': {'sheet': '造价', 'row': 10} if s else None, 'evidence': {'sheet': '清册', 'row': 20} if t else None,
             'raw': {'匹配_原因': '部分一致：电缆辅助措施组整体对应，支架重量偏差'}}
        r.update(extra)
        return r
    return [
        row(1, '1', '5', '一致', '一致。主变一致', name='主变压器 SZ11-8000/35', target='主变压器'),
        row(2, '193', '75', '部分一致', '部分一致。缺单套重量', name='角钢（E-1-400）', target='400×400电缆沟支架'),
        row(3, '197', '79', '一致', '一致。数量80吻合', name='铝合金水平电缆桥架　宽550mm', target='铝合金水平电缆桥架'),
        row(4, '134', '', '基本一致', '基本一致。成套项整体核对', name='时钟同步系统 ：', covers=['187'], set_review='模型整体复核'),
        row(5, '', '83', '不一致', '不一致。清册独有物资', target='热镀锌钢托臂'),
        row(6, '', '187', '含于成套项', '含于造价成套项（序号 134）', target='GPS天线', covered_by='134', cover_basis='列在时钟同步系统之下'),
    ]


class LocateTests(unittest.TestCase):
    def test_parent_present_on_one_side_explains_counterpart_parent(self):
        picked, scope, notes = result_qa.locate('清册表中“电缆辅助措施”与造价表中的“电缆辅助措施”的总体匹配情况？', dataset(), rows())
        self.assertEqual([r['id'] for r in picked], [2, 3, 5])
        self.assertIn('清册父级「电缆辅助措施」3 条结果', scope)
        self.assertEqual(notes, ['造价表中没有名为「电缆辅助措施」的父级；与之配对的造价物资位于父级：「1次」2 条'])
        evidence = result_qa.build('电缆辅助措施的匹配情况？', dataset(), rows())['evidence']
        self.assertEqual(evidence['汇总']['按最严重子项汇总'], '不一致')
        self.assertEqual(evidence['汇总']['涉及父级']['造价'], {'1次': 2})
        self.assertEqual(evidence['汇总']['清册单侧未匹配'], 1)
        self.assertNotIn('已人工确认', evidence['汇总'])  # 零计数不交给模型
        self.assertIn('支架重量偏差', evidence['汇总']['流程分组评价'][0]['评价'])

    def test_numbers_names_and_bands(self):
        self.assertEqual([r['id'] for r in result_qa.locate('清册83为什么不一致', dataset(), rows())[0]], [5])
        self.assertEqual([r['id'] for r in result_qa.locate('铝合金水平电缆桥架对上了吗', dataset(), rows())[0]], [3])
        self.assertEqual([r['id'] for r in result_qa.locate('部分一致和不一致的有哪些', dataset(), rows())[0]], [2, 5])
        self.assertEqual(result_qa.locate('铜排密度怎么取', dataset(), rows())[0], [])

    def test_generic_voltage_heads_do_not_match(self):
        many = rows() + [dict(rows()[0], id=9, source_id='9', target_id='', name='110kV GIS出线间隔', target='')]
        self.assertEqual(result_qa.locate('造价表中“SF6断路器安装户外电压110kV”清册没有吗', dataset(), many)[0], [])
        self.assertEqual([r['id'] for r in result_qa.locate('110kVGIS出线间隔对上了吗', dataset(), many)[0]], [9])

    def test_unloaded_raw_row_points_to_neighbor_material(self):
        data = dataset()
        data['sources'][3]['_source'] = {'file': '估算.xlsx', 'sheet': '表三甲', 'row': 3}
        data['sheet_audit'] = [{'file': '估算.xlsx', 'sheet': '表三甲', 'side': 'sources', 'status': '已提取',
                                'columns': {'name': 3, 'unit': 4, 'qty': 5, 'supply': 2}}]
        sheet = [('序号', '编制依据', '名称', '单位', '数量'), ('', '', '主变压器系统', '', ''),
                 ('', '（甲设）', '主变压器 SZ11-8000/35', '台', 1), ('', 'GD1-5', '三相双绕组变压器安装 35kV', '台', 1)]
        read = lambda file, name: sheet if (file, name) == ('估算.xlsx', '表三甲') else []
        raw = result_qa.raw_mentions('造价的”三相双绕组变压器安装35kV“清册里怎么没有？', data, read)
        self.assertEqual(raw['ids'], {'造价': {'1'}, '清册': set()})
        note = raw['notes'][0]
        self.assertEqual(note['原表行'], '造价「表三甲」第4行“三相双绕组变压器安装 35kV”')
        self.assertIn('编制依据为“GD1-5”', note['未入库原因'])
        self.assertIn('原表第3行“主变压器 SZ11-8000/35”（造价编号1）', note['对应物资'])
        self.assertEqual(result_qa.raw_mentions('主变压器 SZ11-8000/35 对上了吗', data, read)['notes'], [])  # 已入库的行交给结果定位
        context = result_qa.build('造价的”三相双绕组变压器安装35kV“清册里怎么没有？', data, rows(), raw=raw)
        self.assertEqual([r['id'] for r in context['rows']], [1])
        self.assertEqual(context['scope'], '原表行对应的物资')
        explained = context['evidence']['原表行说明'][0]
        self.assertEqual(explained['对应结果'], ['R1'])
        self.assertNotIn('link', explained)

    def test_counterpart_search_finds_similar_items_and_their_status(self):
        data = dataset()
        data['targets'].append({'序号': 90, '设备材料名称': '加密认证装置', '单位': '台', '数量': '1', '层级': '调度数据网', '层级路径': ['调度数据网']})
        data['sources'].append({'序号': 300, 'Estimation_Name': '百兆加密装置', 'Estimation_Unit': '台', 'Estimation_Quantity': '2', '层级': '二次', '层级路径': ['二次']})
        lone = dict(rows()[0], id=9, source_id='300', target_id='', name='百兆加密装置', quantity='2', unit='台')
        found = result_qa.counterpart_search([lone, rows()[0]], data, rows() + [lone])
        self.assertEqual(len(found), 1)                     # 只检索单侧条目
        hit = found[0]['在清册中检索'][0]
        self.assertEqual((hit['清册编号'], hit['当前状态']), ('90', '未配对'))
        none = result_qa.counterpart_search([dict(lone, name='镀锌费')], data, rows())
        self.assertIn('没有名称或型号相近', none[0]['在清册中检索'])
        evidence = result_qa.build('这几条清册里确实没有吗', data, rows(), [{'kind': 'rows', 'keys': [['193', '75'], ['197', '79']], 'label': '所选2条'}])['evidence']
        self.assertNotIn('按最严重子项汇总', evidence['汇总'])   # 逐条勾选的追问不给整组汇总

    def test_engine_wording_becomes_user_terms(self):
        self.assertEqual(result_qa.user_terms('源126/2000-40与匹配3150A不同，匹配表多出6条，源表有1条未找到对应匹配项'),
                         '造价126/2000-40与清册3150A不同，清册多出6条，造价有1条未找到对应清册项')
        self.assertEqual(result_qa.user_terms('试验电源屏来源不明，未匹配，匹配结果待复核，匹配侧列出'), '试验电源屏来源不明，未匹配，匹配结果待复核，清册侧列出')
        item = result_qa.entry(dict(rows()[0], reason='基本一致。源ZR与匹配ZRA不同'), result_qa._records(dataset()))
        self.assertEqual(item['依据'], '基本一致。造价ZR与清册ZRA不同')

    def test_trailing_scope_remark_is_dropped(self):
        self.assertEqual(result_qa.drop_scope_tail('结论为基本一致[R40]。\n1. 参数写法不同。\n\n本次核对范围仅涉及问题中提到的瓷柱式SF6断路器。'),
                         '结论为基本一致[R40]。\n1. 参数写法不同。')
        self.assertEqual(result_qa.drop_scope_tail('本次范围内两侧已配对7条。'), '本次范围内两侧已配对7条。')
        self.assertEqual(result_qa.drop_scope_tail('基本一致[R40]。\n\n本次核对范围为原表行对应的物资。'), '基本一致[R40]。')
        self.assertEqual(result_qa.drop_scope_tail('建议逐条填写复核意见；本次核对范围为所选9条。'), '建议逐条填写复核意见。')
        self.assertEqual(result_qa.drop_scope_tail('成套匹配项一共12组，涉及组成件195条，都落在这707条范围内；组结论是一致3组。'),
                         '成套匹配项一共12组，涉及组成件195条；组结论是一致3组。')
        self.assertEqual(result_qa.drop_scope_tail('共12组，其中部分一致2组；本次看的是全部匹配结果里的成套对应组，没有限定具体父级。逐组如下：\n\n1. [R51]一致。'),
                         '共12组，其中部分一致2组。逐组如下：\n\n1. [R51]一致。')
        self.assertEqual(result_qa.drop_scope_tail('片数对得上，6串×10片，在允许范围内。'), '片数对得上，6串×10片，在允许范围内。')
        kept = '结论为不一致[R5]。\n本次范围内共32条，两侧已配对7条。'
        self.assertEqual(result_qa.drop_scope_tail(kept), kept)

    def test_set_question_lists_every_group_with_members(self):
        all_rows = rows()
        refs = [{'kind': 'rows', 'keys': [list(result_qa.row_key(r)) for r in all_rows], 'label': '所选 6 条'}]
        context = result_qa.build('成套匹配项是哪些？', dataset(), all_rows, refs)
        evidence = context['evidence']
        self.assertTrue(context['scope'].endswith('中的成套对应组（多对多匹配）'))
        self.assertEqual({r['id'] for r in context['rows']}, {4, 6})
        self.assertEqual((evidence['汇总']['成套对应组'], evidence['汇总']['成套对应组成件']), (1, 1))
        group = evidence['成套对应组'][0]
        self.assertEqual((group['成套项行'], group['造价成套项']['名称'], group['整组结论']), ('R4', '时钟同步系统 ：', '基本一致'))
        self.assertEqual([(m['行'], m['名称']) for m in group['清册组成件']], [('R6', 'GPS天线')])
        # 问其他结论时不带组明细
        self.assertNotIn('成套对应组', result_qa.build('哪些不一致？', dataset(), all_rows, refs)['evidence'])

    def test_set_and_components_travel_together(self):
        context = result_qa.build('', dataset(), rows(), [{'kind': 'rows', 'keys': [['', '187']], 'label': 'GPS天线'}])
        self.assertEqual({r['id'] for r in context['rows']}, {4, 6})
        item = next(e for e in context['evidence']['条目'] if e['行'] == 'R4')
        self.assertEqual(item['成套项']['整体覆盖清册编号'], ['187'])

    def test_refs_resolve_and_stale_refs_fail(self):
        picked, labels = result_qa.resolve_refs([{'kind': 'group', 'side': '造价', 'ids': ['193', '197'], 'label': '造价 · 1次'}], rows())
        self.assertEqual([r['id'] for r in picked], [2, 3])
        self.assertEqual(labels, ['造价 · 1次'])
        with self.assertRaisesRegex(ValueError, '已不在当前匹配结果'):
            result_qa.resolve_refs([{'kind': 'rows', 'keys': [['999', '1']], 'label': '旧条目'}], rows())

    def test_overall_question_limits_rows_by_severity(self):
        many = rows() + [dict(rows()[0], id=i, source_id=str(i), target_id=str(1000 + i)) for i in range(10, 120)]
        context = result_qa.build('总体匹配情况如何？', dataset(), many)
        self.assertEqual(len(context['rows']), result_qa.ROW_LIMIT)
        self.assertIn(5, [r['id'] for r in context['rows']])
        self.assertIn('省略', context['evidence'])
        self.assertTrue(context['evidence']['父级概况'])
        self.assertEqual(context['keys'], [])

    def test_follow_up_reuses_previous_scope_only_when_referring_back(self):
        previous = {'scope': '清册父级', 'keys': [['193', '75']]}
        context = result_qa.build('那第二条呢？', dataset(), rows(), previous=previous)
        self.assertEqual([r['id'] for r in context['rows']], [2])
        self.assertTrue(context['scope'].startswith('沿用上一问的范围'))
        self.assertTrue(result_qa.build('不一致的原因有哪些？', dataset(), rows(), previous=previous)['scope'].startswith('结论为'))
        self.assertTrue(result_qa.build('总体情况如何？', dataset(), rows(), previous=previous)['scope'].startswith('全部匹配结果'))

    def test_citations_only_keep_evidence_rows(self):
        text, cites, dropped = result_qa.cite('支架偏差[R2]，托臂漏项【R5】，另见[R2, R5]与[R99]。', ['R3', 'R77'], [rows()[1], rows()[2], rows()[4]])
        self.assertEqual(text, '支架偏差[R2]，托臂漏项[R5]，另见[R2][R5]与。')
        self.assertEqual(dropped, 1)
        self.assertEqual([c['key'] for c in cites], ['R2', 'R5', 'R3'])
        self.assertEqual(cites[0]['target_id'], '75')
        self.assertEqual(cites[0]['label'], '400×400电缆沟支架')
        text, cites, dropped = result_qa.cite('R2（支架）与R5均需复核，R77、ZR3 不是行号。', [], [rows()[1], rows()[4]])
        self.assertEqual(text, '[R2]（支架）与[R5]均需复核，R77、ZR3 不是行号。')
        self.assertEqual(([c['key'] for c in cites], dropped), (['R2', 'R5'], 0))
        board = dict(rows()[2], id=7, target='622M光接口板（含光模块）')
        text, _, _ = result_qa.cite('[R2]（400×400电缆沟支架）偏差，热镀锌钢托臂[R5]漏项，[R3]（铝合金水平电缆桥架，宽550mm），'
                                    '[R7]（622M光接口板（含光模块）），造价侧[R2]（角钢），[R7]622M光接口板（2块）', [], [rows()[1], rows()[2], rows()[4], board])
        self.assertEqual(text, '[R2]偏差，[R5]漏项，[R3]（宽550mm），[R7]（含光模块），造价侧[R2]（角钢），[R7]（2块）')

    def test_band_filter_omits_group_verdicts(self):
        summary = result_qa.build('部分一致和不一致的有哪些', dataset(), rows())['evidence']['汇总']
        self.assertNotIn('按最严重子项汇总', summary)
        self.assertNotIn('流程分组评价', summary)

    def test_single_row_omits_group_verdicts(self):
        summary = result_qa.build('', dataset(), rows(), [{'kind': 'rows', 'keys': [['193', '75']], 'label': '支架'}])['evidence']['汇总']
        self.assertNotIn('按最严重子项汇总', summary)
        self.assertNotIn('流程分组评价', summary)


def reply(content='', *calls):
    """模型的一轮回复：正文，或若干 (工具名, 参数) 工具调用。"""
    return {'content': content, 'reasoning_content': None,
            'tool_calls': [{'id': f'c{i}', 'name': n, 'arguments': json.dumps(a, ensure_ascii=False)} for i, (n, a) in enumerate(calls)]}


def scripted(*replies):
    """替换原生工具调用请求：按顺序返回预设回复，记录每次发给模型的对话与工具名。"""
    queue, calls = list(replies), []

    def fake(endpoint, model, key, messages, tools=None, choice=None, **kw):
        calls.append({'messages': copy.deepcopy(messages), 'tools': [t['function']['name'] for t in tools or []]})
        return queue.pop(0), {}
    return fake, calls


def tool_results(call):
    return [json.loads(m['content']) if m['content'].startswith(('{', '[')) else m['content']
            for m in call['messages'] if m['role'] == 'tool']


class ChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=3, scope='', rows=rows(), dataset=dataset(), messages=[], history=[], logs=[], running=False, executions=[])
        app.CONFIG.update(api_key='test-key', base_url='https://api.deepseek.com', web_search=True, thinking=False)
        self.immediate = patch.object(app.threading, 'Thread', lambda target, **kw: type('T', (), {'start': lambda self: target()})())
        self.immediate.start()

    def tearDown(self):
        self.immediate.stop()
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def ask(self, text, *replies, refs=None):
        fake, calls = scripted(*replies)
        with patch.object(llm_client, 'request_chat', side_effect=fake):
            app.chat(text, refs)
        return calls

    def test_questions_with_edit_words_never_edit_skills(self):
        with patch.object(app, 'edit_skill') as edit, patch.object(app, 'agent_answer') as answer:
            app.chat('热镀锌钢托臂为什么被排除？')
            app.chat('这条配对需要调整吗')
            edit.assert_not_called()
            self.assertEqual(answer.call_count, 2)
            app.chat('忽略文件名包含临时施工用电的文件')
            edit.assert_called_once()

    def test_refs_always_route_to_agent(self):
        with patch.object(app, 'edit_skill') as edit, patch.object(app, 'agent_answer') as answer:
            app.chat('修改成一致', [{'kind': 'rows', 'keys': [['193', '75']], 'label': '清册75 支架'}])
        edit.assert_not_called()
        answer.assert_called_once_with('修改成一致', [{'kind': 'rows', 'keys': [['193', '75']], 'label': '清册75 支架'}], admin=True, user='')
        self.assertEqual(app.STATE['messages'][0]['refs'], ['清册75 支架'])

    def test_refs_are_looked_up_through_tool(self):
        refs = [{'kind': 'rows', 'keys': [['193', '75']], 'label': '清册75 支架'}]
        calls = self.ask('差异原因是什么？', reply('', ('lookup_results', {'about': '差异原因', 'use_refs': True})),
                         reply('缺单套重量[R2]。'), refs=refs)
        self.assertIn('用户在本条消息中引用了结果：清册75 支架', calls[0]['messages'][1]['content'])
        evidence = tool_results(calls[1])[0]
        self.assertTrue(evidence['范围'].startswith('用户引用：清册75 支架'))
        reply_msg = app.STATE['messages'][-1]
        self.assertEqual((reply_msg['mode'], [c['key'] for c in reply_msg['citations']]), ('result', ['R2']))

    def test_follow_up_hint_and_previous_scope(self):
        app.STATE['messages'] = [{'role': 'user', 'text': '差异原因是什么？', 'refs': ['清册75 支架']},
                                 {'role': 'assistant', 'text': '……', 'qa': {'scope': '用户引用：清册75 支架', 'keys': [['193', '75']]}, 'mode': 'result'}]
        calls = self.ask('你要从互联网上搜索，给我工程量',
                         reply('', ('lookup_results', {'about': '工程量', 'follow_previous': True})), reply('角钢约0.45t[R2]。'))
        self.assertIn('上一轮结果追问的范围：用户引用：清册75 支架', calls[0]['messages'][1]['content'])
        self.assertEqual(tool_results(calls[1])[0]['范围'], '沿用上一问的范围：用户引用：清册75 支架')
        self.assertEqual(app.STATE['messages'][-1]['qa']['keys'], [['193', '75']])

    def test_follow_scope_reuses_previous_keys(self):
        context = result_qa.build('给我工程量', dataset(), rows(), [], {'scope': '用户引用：支架', 'keys': [['193', '75']]}, None, True)
        self.assertEqual(context['scope'], '沿用上一问的范围：用户引用：支架')
        self.assertIn(('193', '75'), [result_qa.row_key(r) for r in context['rows']])

    def test_answer_uses_tool_evidence_and_validates_citations(self):
        question = '清册表中“电缆辅助措施”与造价表中的“电缆辅助措施”的总体匹配情况？'
        calls = self.ask(question, reply('', ('lookup_results', {'about': question})),
                         reply('总体不一致：托臂漏项[R5]，支架缺单重[R2]，另有[R42]。'))
        self.assertIn('lookup_results', calls[0]['tools'])
        evidence = tool_results(calls[1])[0]
        self.assertEqual([e['行'] for e in evidence['条目']], ['R2', 'R3', 'R5'])
        reply_msg = app.STATE['messages'][1]
        self.assertNotIn('[R42]', reply_msg['text'])
        self.assertIn('已移除 1 处', reply_msg['text'])
        self.assertEqual([c['key'] for c in reply_msg['citations']], ['R5', 'R2'])
        self.assertEqual(reply_msg['mode'], 'result')
        self.assertEqual(reply_msg['qa']['keys'], [['193', '75'], ['197', '79'], ['', '83']])
        self.assertEqual(reply_msg['tools'][0]['label'], '查匹配结果')
        step = app.STATE['executions'][-1]['steps'][0]
        self.assertEqual((step['tool'], step['status']), ('lookup_results', 'ok'))
        self.assertTrue(step['text'].startswith('查匹配结果：清册父级「电缆辅助措施」3 条结果'))
        calls = self.ask('那托臂呢？', reply('托臂是清册独有物资[R5]。'))
        self.assertIn('问：清册表中', calls[0]['messages'][1]['content'])
        self.assertIn('上一轮结果追问的范围', calls[0]['messages'][1]['content'])

    def test_web_search_tool_numbers_sources_and_keeps_known_citations(self):
        searched = []
        results = [{'title': '电缆沟支架重量表', 'url': 'https://example.com/a', 'site_name': '示例标准网', 'date': '2024-01-01',
                    'authority': 3, 'rerank_score': 2.1, 'summary': '400×400支架单套约12kg'},
                   {'title': '论坛帖子', 'url': 'https://example.com/b', 'site_name': '', 'date': '', 'authority': 0,
                    'rerank_score': 0.4, 'summary': '无关'}]

        def search(base, key, query, count, question):
            searched.append(query)
            return results
        fake, calls = scripted(reply('', ('lookup_results', {'about': '清册75支架'}),
                                     ('web_search', {'query': '400×400电缆沟支架 角钢 单套重量', 'purpose': '核实R2支架重量'})),
                               reply('造价角钢按网上资料单套约12kg[W1]，另见[W9]。支架见[R2]。'))
        with patch.object(llm_client, 'request_chat', side_effect=fake), patch.object(app.web_search, 'search', side_effect=search), \
                patch.object(app.web_search, 'fetch', return_value={'title': 'x', 'content': '表1 E-1-400 支架 12.1kg/套'}) as fetch:
            app.chat('清册75支架和造价角钢的匹配原因准确吗？')
        self.assertEqual(searched, ['400×400电缆沟支架 角钢 单套重量'])
        fetch.assert_called_once_with(app.CONFIG['base_url'], 'test-key', 'https://example.com/a')
        web = tool_results(calls[1])[1]
        self.assertEqual([w['编号'] for w in web], ['W1', 'W2'])
        self.assertEqual(web[0]['正文节选'], '表1 E-1-400 支架 12.1kg/套')
        self.assertNotIn('正文节选', web[1])
        text = app.STATE['messages'][-1]['text']
        self.assertIn('[W1]', text)
        self.assertNotIn('[W9]', text)
        self.assertIn('联网检索来源：\n[W1] 电缆沟支架重量表（示例标准网，2024-01-01）https://example.com/a', text)
        self.assertNotIn('example.com/b', text)

    def test_web_search_tool_absent_for_other_providers_or_when_disabled(self):
        for config in ({'base_url': 'https://llm.example.com/v1'}, {'web_search': False}):
            app.CONFIG.update(config)
            calls = self.ask('清册75支架的匹配原因准确吗？', reply('支架缺单重。'))
            self.assertNotIn('web_search', calls[0]['tools'])
            self.assertIn('lookup_results', calls[0]['tools'])
            app.CONFIG.update(base_url='https://api.deepseek.com', web_search=True)

    def test_knowledge_question_cites_only_retrieved_entries(self):
        entry = {'id': 'k1', 'version': 1, 'title': '铜母排长度重量换算', 'kind': '', 'approved': 1, 'content': '按密度8900换算',
                 'source': '自定义规则', 'location': ''}
        with patch.object(app.KB, 'search', return_value=[entry]):
            self.ask('铜排密度怎么取？', reply('', ('search_knowledge', {'query': '铜排 密度'})), reply('按密度8900kg/m³换算[KB:k1]，见[KB:k9]。'))
        msg = app.STATE['messages'][-1]
        self.assertEqual(msg['mode'], 'knowledge')
        self.assertNotIn('[KB:', msg['text'].split('可核查引用')[0])
        self.assertIn('可核查引用：\n[KB:k1@v1] 铜母排长度重量换算', msg['text'])

    def test_falls_back_to_json_protocol_and_remembers(self):
        app.NATIVE_OFF.clear()
        self.addCleanup(app.NATIVE_OFF.clear)
        answers = [{'tool_calls': [{'name': 'lookup_results', 'arguments': {'about': '清册75支架'}}]}, {'answer': '支架缺单重[R2]。'}]
        with patch.object(llm_client, 'request_chat', side_effect=llm_client.ToolsUnsupported('HTTP 400')) as native, \
                patch.object(llm_client, 'request_json', side_effect=lambda *a, **k: (answers.pop(0), {})):
            app.chat('清册75支架为什么部分一致？')
        self.assertEqual(native.call_count, 1)
        msg = app.STATE['messages'][-1]
        self.assertEqual((msg['tool_mode'], msg['mode'], [c['key'] for c in msg['citations']]), ('json', 'result', ['R2']))
        self.assertIn(('https://api.deepseek.com/chat/completions', app.CONFIG['model']), app.NATIVE_OFF)


if __name__ == '__main__':
    unittest.main(verbosity=2)
