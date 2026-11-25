import unittest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))

from llm_client import request_json


def response(content, finish='stop'):
    return Mock(status_code=200, json=Mock(return_value={
        'choices': [{'message': {'content': content}, 'finish_reason': finish}],
        'usage': {'completion_tokens': 12}}))


class StructuredRequestsTests(unittest.TestCase):
    def setUp(self):
        # 其他测试导入 server 时会按模型档案设置接口限速（Kimi 每分钟 3 次）；这里验证请求层本身，先清掉再还原
        import llm_client
        saved = dict(llm_client._LIMITS)
        llm_client._LIMITS.clear()
        self.addCleanup(llm_client._LIMITS.update, saved)

    def call(self, **kwargs):
        return request_json('https://api.deepseek.com/chat/completions',
                            'deepseek-flash', 'test-key', [], **kwargs)

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_empty_then_valid(self, post, sleep):
        post.side_effect = [response(''), response('{"tables": []}')]
        log = Mock()
        result, usage = self.call(log=log)
        self.assertEqual(result, {'tables': []})
        self.assertEqual(usage['completion_tokens'], 12)
        self.assertEqual(post.call_count, 2)
        self.assertIn('空结果', log.call_args.args[0])
        self.assertEqual(post.call_args.kwargs['json']['thinking'], {'type': 'disabled'})

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_truncated_valid_json_is_not_accepted(self, post, sleep):
        post.side_effect = [response('{"partial": true}', 'length'), response('{"complete": true}')]
        self.assertEqual(self.call()[0], {'complete': True})
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 16384)

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_invalid_formats_bounded(self, post, sleep):
        post.side_effect = [response('not json'), response('[]'), response('{}')]
        with self.assertRaisesRegex(RuntimeError, '已尝试 3 次'):
            self.call()
        self.assertEqual(post.call_count, 3)

    @patch('llm_client.requests.post')
    def test_auth_error_no_retry(self, post):
        post.return_value = Mock(status_code=401)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            self.call()
        self.assertEqual(post.call_count, 1)

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_non_json_http_body_retried(self, post, sleep):
        post.side_effect = [Mock(status_code=200, json=Mock(side_effect=ValueError('html'))),
                            response('```json\n{"ok": true}\n```')]
        self.assertEqual(self.call()[0], {'ok': True})

    @patch('llm_client.requests.post')
    def test_custom_endpoint_has_no_provider_options(self, post):
        post.return_value = response('{"ok": true}')
        request_json('https://example.com/chat/completions', 'local', 'key', [])
        self.assertNotIn('thinking', post.call_args.kwargs['json'])

    @patch('llm_client.requests.post')
    def test_kimi_k3_uses_fixed_temperature_and_reasoning_effort(self, post):
        # Kimi K3 始终思考，只允许 temperature=1：不传 temperature，按用途给推理强度，输出额度容得下思考内容
        post.return_value = response('{"ok": true}')
        request_json('https://api.moonshot.cn/v1/chat/completions', 'kimi-k3', 'key', [], max_tokens=200)
        sent = post.call_args.kwargs['json']
        self.assertNotIn('temperature', sent)
        self.assertEqual((sent['reasoning_effort'], sent['max_tokens']), ('low', 8192))
        request_json('https://api.moonshot.cn/v1/chat/completions', 'kimi-k3', 'key', [], thinking=True)
        self.assertEqual(post.call_args.kwargs['json']['reasoning_effort'], 'high')

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_rate_limit_waits_and_does_not_use_up_retries(self, post, sleep):
        # Kimi 低等级账户并发 1：HTTP 429 时按服务端建议或逐级加长的间隔等待后重发，不算一次失败
        limited = Mock(status_code=429, headers={'Retry-After': '1'}, text='{"error":{"message":"reached max organization concurrency: 1"}}')
        post.side_effect = [limited, limited, response('{"ok": true}')]
        log = Mock()
        result, _ = request_json('https://api.moonshot.cn/v1/chat/completions', 'kimi-k3', 'key', [], retries=1, log=log)
        self.assertEqual(result, {'ok': True})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 10])
        self.assertIn('HTTP 429', log.call_args_list[0].args[0])

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_persistent_rate_limit_reports_clearly(self, post, sleep):
        post.return_value = Mock(status_code=429, headers={}, text='engine is currently overloaded')
        with self.assertRaisesRegex(RuntimeError, '持续限速或繁忙'):
            request_json('https://api.moonshot.cn/v1/chat/completions', 'kimi-k3', 'key', [], retries=1, rate_wait=30)

    def test_gate_limits_concurrency_per_host(self):
        import threading
        import llm_client
        llm_client.set_limit('limited.example', 1, 0)
        active, peak, lock = [0], [0], threading.Lock()

        def slow(*a, **k):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            import time
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return response('{"ok": true}')
        with patch('llm_client.requests.post', side_effect=slow):
            threads = [threading.Thread(target=request_json, args=('https://limited.example/v1/chat/completions', 'm', 'k', [])) for _ in range(4)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(peak[0], 1)

    @patch('llm_client.requests.post')
    def test_temperature_rejection_retried_without_it(self, post):
        rejected = Mock(status_code=400, text='{"error":{"message":"invalid temperature: only 1 is allowed for this model"}}')
        post.side_effect = [rejected, response('{"ok": true}')]
        result, _ = request_json('https://example.com/v1/chat/completions', 'other-model', 'key', [], retries=1)
        self.assertEqual(result, {'ok': True})
        self.assertNotIn('temperature', post.call_args.kwargs['json'])

    @patch('llm_client.time.sleep')
    @patch('llm_client.requests.post')
    def test_service_failure_retried(self, post, sleep):
        post.side_effect = [Mock(status_code=503), response('{"ok": true}')]
        self.assertEqual(self.call()[0], {'ok': True})


if __name__ == '__main__':
    unittest.main()
