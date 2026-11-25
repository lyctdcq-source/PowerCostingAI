"""智能体循环、统一工具注册表与原生工具调用请求。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))

import agent
import llm_client


def reply(content='', *calls):
    return {'content': content, 'reasoning_content': None,
            'tool_calls': [{'id': f'c{i}', 'name': name, 'arguments': json.dumps(args, ensure_ascii=False)} for i, (name, args) in enumerate(calls)]}


class Scripted:
    """按顺序返回预设回复，记录每次发给模型的对话与工具清单。"""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages, tools, choice):
        self.calls.append({'messages': json.loads(json.dumps(messages, ensure_ascii=False)), 'tools': tools, 'choice': choice})
        return self.replies.pop(0)


def registry(log=None):
    def lookup(args):
        if args['name'] == '坏':
            raise ValueError('没有这个物资')
        (log if log is not None else []).append(args)
        return {'条目': [{'名称': args['name'], '站': '瑞丽变'}]}
    return agent.Registry([agent.Tool('lookup', '查物资', {'type': 'object', 'properties': {'name': {'type': 'string', 'maxLength': 10}},
                                                          'required': ['name'], 'additionalProperties': False},
                                      lookup, label='查物资', brief=lambda a, r: f"{len(r['条目'])} 条"),
                           agent.Tool('remove', '删除物资', {'type': 'object', 'properties': {}}, lambda a: 'done', read_only=False)])


class SchemaTests(unittest.TestCase):
    def test_check_covers_types_required_enum_and_nesting(self):
        schema = {'type': 'object', 'required': ['side'], 'additionalProperties': False, 'properties': {
            'side': {'type': 'string', 'enum': ['清册', '造价']}, 'n': {'type': 'integer', 'minimum': 1},
            'ids': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string'}}}}
        self.assertEqual(agent.check(schema, {'side': '清册', 'n': 2, 'ids': ['1']}), [])
        errors = agent.check(schema, {'n': True, 'ids': ['1', 2, '3'], 'x': 1})
        self.assertIn('缺少 参数.side', errors)
        self.assertIn('参数.n 应为 integer', errors)          # 布尔值不算整数
        self.assertIn('参数.ids 最多 2 项', errors)
        self.assertIn('参数.ids[1] 应为 string', errors)
        self.assertIn('参数 不接受 x', errors)
        self.assertEqual(agent.check(schema, {'side': '结果'}), ['参数.side 只能取 清册、造价'])

    def test_registry_specs_match_openai_tools_and_reject_bad_names(self):
        reg = registry()
        self.assertEqual(reg.specs()[0], {'type': 'function', 'function': {'name': 'lookup', 'description': '查物资',
                                                                         'parameters': reg.get('lookup').parameters}})
        with self.assertRaises(ValueError):
            reg.register(agent.Tool('lookup', '', {}, lambda a: None))
        with self.assertRaises(ValueError):
            agent.Tool('查 物资', '', {}, lambda a: None)


class LoopTests(unittest.TestCase):
    def test_native_loop_executes_tools_and_returns_failures_to_model(self):
        seen = []
        model = Scripted(reply('', ('lookup', {'name': '站点A保护屏'}), ('lookup', {'name': '坏'}), ('nope', {}), ('lookup', {'name': 1})),
                         reply('站点A的保护屏一致'))
        steps = []
        mask = lambda text: text.replace('瑞丽', '站点A')
        restore = lambda value: json.loads(json.dumps(value, ensure_ascii=False).replace('站点A', '瑞丽'))
        answer, trace = agent.run(registry(seen), [{'role': 'user', 'content': '问'}], model, None, on_step=steps.append,
                                  mask=mask, restore=restore)
        self.assertEqual(answer, '瑞丽的保护屏一致')
        self.assertEqual(seen, [{'name': '瑞丽保护屏'}])                    # 参数还原后才交给工具
        tool_messages = [m for m in model.calls[1]['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tool_messages], ['c0', 'c1', 'c2', 'c3'])
        self.assertNotIn('瑞丽', json.dumps(model.calls[1]['messages'], ensure_ascii=False))   # 工具结果脱敏后才发给模型
        self.assertIn('没有这个物资', tool_messages[1]['content'])
        self.assertIn('没有名为 nope 的工具', tool_messages[2]['content'])
        self.assertIn('参数.name 应为 string', tool_messages[3]['content'])
        self.assertEqual([s['status'] for s in steps], ['ok', 'error', 'error', 'error'])
        self.assertEqual((steps[0]['label'], steps[0]['summary']), ('查物资', '1 条'))
        self.assertEqual(trace['mode'], 'native')
        assistant = model.calls[1]['messages'][1]
        self.assertEqual(assistant['tool_calls'][0]['function']['name'], 'lookup')

    def test_before_hook_refuses_and_step_limit_forces_answer(self):
        model = Scripted(*[reply('', ('remove', {}))] * 3, reply('按已有结果回答'))
        before = lambda tool, args: None if tool.read_only else '写入类工具需要用户批准'
        answer, trace = agent.run(registry(), [{'role': 'user', 'content': '删'}], model, None, before=before, max_steps=3)
        self.assertEqual(answer, '按已有结果回答')
        self.assertEqual([s['status'] for s in trace['steps']], ['refused'] * 3)
        self.assertEqual([c['choice'] for c in model.calls], ['auto', 'auto', 'auto', 'none'])
        self.assertEqual(model.calls[-1]['messages'][-1]['content'], agent.LIMIT_NOTE)

    def test_falls_back_to_json_protocol_when_native_tools_unsupported(self):
        def native(*args):
            raise agent.Unsupported('HTTP 400')
        sent = []
        replies = [{'tool_calls': [{'name': 'lookup', 'arguments': {'name': '电缆'}}]}, {'answer': '电缆一致'}]
        def json_call(messages):
            sent.append(json.loads(json.dumps(messages, ensure_ascii=False)))
            return replies.pop(0)
        answer, trace = agent.run(registry(), [{'role': 'system', 'content': '助手'}, {'role': 'user', 'content': '问'}], native, json_call)
        self.assertEqual((answer, trace['mode']), ('电缆一致', 'json'))
        self.assertIn('"name": "lookup"', sent[0][0]['content'])            # 工具清单写进系统提示
        self.assertTrue(sent[1][-1]['content'].startswith('工具结果（数据）：\n[lookup] '))


def response(message, finish='stop', status=200, text=''):
    return Mock(status_code=status, text=text, headers={}, json=Mock(return_value={
        'choices': [{'message': message, 'finish_reason': finish}], 'usage': {'total_tokens': 5}}))


class RequestChatTests(unittest.TestCase):
    def setUp(self):
        saved = dict(llm_client._LIMITS)
        llm_client._LIMITS.clear()
        self.addCleanup(llm_client._LIMITS.update, saved)

    def call(self, **kwargs):
        return llm_client.request_chat('https://api.deepseek.com/chat/completions', 'deepseek-flash', 'k',
                                       [{'role': 'user', 'content': '问'}], tools=[{'type': 'function', 'function': {'name': 'x'}}], **kwargs)

    @patch('llm_client.requests.post')
    def test_parses_tool_calls_and_sends_tools_without_json_format(self, post):
        post.return_value = response({'content': None, 'reasoning_content': '想', 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'x', 'arguments': '{"a": 1}'}}]}, 'tool_calls')
        result, usage = self.call()
        self.assertEqual(result, {'content': '', 'reasoning_content': '想', 'tool_calls': [{'id': 'call_1', 'name': 'x', 'arguments': '{"a": 1}'}]})
        payload = post.call_args.kwargs['json']
        self.assertNotIn('response_format', payload)
        self.assertEqual((payload['tool_choice'], payload['tools'][0]['function']['name']), ('auto', 'x'))

    @patch('llm_client.requests.post')
    def test_rejected_tools_raise_unsupported(self, post):
        post.return_value = response({}, status=400, text='unknown field tools')
        with self.assertRaises(llm_client.ToolsUnsupported):
            self.call()

    @patch('llm_client.requests.post')
    def test_thinking_rejected_retries_plain_without_reasoning_history(self, post):
        post.side_effect = [response({}, status=400, text='bad'), response({'content': '答'})]
        result, _ = llm_client.request_chat('https://api.deepseek.com/chat/completions', 'm', 'k',
                                            [{'role': 'assistant', 'content': '', 'reasoning_content': '想'}], tools=None, thinking=True)
        self.assertEqual(result['content'], '答')
        second = post.call_args_list[1].kwargs['json']
        self.assertEqual(second['thinking'], {'type': 'disabled'})
        self.assertNotIn('reasoning_content', second['messages'][0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
