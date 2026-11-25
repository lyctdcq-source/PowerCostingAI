"""DeepSeek 网页搜索与抓取客户端：请求格式、结果整理、按状态码重试。全部离线运行。"""
import unittest
from unittest.mock import patch

import web_search

BASE = 'https://api.deepseek.com'


class Response:
    def __init__(self, status, data=None):
        self.status_code, self.data = status, data

    def json(self):
        return self.data


class WebSearchTests(unittest.TestCase):
    def test_search_posts_documented_request_and_sorts_by_score(self):
        data = {'results': [{'title': 'B', 'url': 'https://b', 'rerank_score': 0.8, 'summary': '短', 'date': '2025-03-01T00:00:00+00:00', 'authority': 1},
                            {'title': 'A', 'url': 'https://a', 'rerank_score': 2.2, 'snippet': '摘要', 'authority': 3},
                            {'title': '无链接'}]}
        with patch.object(web_search.requests, 'post', return_value=Response(200, data)) as post:
            results = web_search.search(BASE + '/v1', 'key', 'TMY-50×5 铜排 每米重量', 5, '铜排换算准确吗？')
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer key'})
        self.assertEqual(post.call_args.kwargs['json'], {'query': 'TMY-50×5 铜排 每米重量', 'count': 5, 'ori_question': '铜排换算准确吗？'})
        self.assertEqual([r['title'] for r in results], ['A', 'B'])
        self.assertEqual(results[0]['summary'], '摘要')
        self.assertEqual(results[1]['date'], '2025-03-01')

    def test_titles_and_summaries_drop_html(self):
        data = {'results': [{'title': '<table><tr><td>公称直径 DN</td><td>焊接钢管</...', 'url': 'https://a', 'summary': '<b>DN25</b>\n 2.41kg/m'}]}
        with patch.object(web_search.requests, 'post', return_value=Response(200, data)):
            result = web_search.search(BASE, 'key', 'q')[0]
        self.assertEqual((result['title'], result['summary']), ('公称直径 DN 焊接钢管', 'DN25 2.41kg/m'))

    def test_fetch_truncates_markdown(self):
        with patch.object(web_search.requests, 'post', return_value=Response(200, {'title': 'T', 'content': 'x' * 50})) as post:
            page = web_search.fetch(BASE, 'key', 'https://example.com/p', limit=10)
        self.assertEqual(post.call_args.args[0], 'https://api.deepseek.com/v1/tools/fetch')
        self.assertEqual(post.call_args.kwargs['json'], {'url': 'https://example.com/p'})
        self.assertEqual(page, {'title': 'T', 'content': 'x' * 10 + '…'})

    def test_retries_only_rate_limit_and_server_errors(self):
        with patch.object(web_search.requests, 'post', side_effect=[Response(429), Response(200, {'results': []})]) as post, \
                patch.object(web_search.time, 'sleep'):
            self.assertEqual(web_search.search(BASE, 'key', 'q'), [])
        self.assertEqual(post.call_count, 2)
        with patch.object(web_search.requests, 'post', return_value=Response(402)) as post:
            with self.assertRaisesRegex(RuntimeError, '余额不足'):
                web_search.search(BASE, 'key', 'q')
        self.assertEqual(post.call_count, 1)

    def test_only_deepseek_endpoint_is_supported(self):
        self.assertTrue(web_search.available('https://api.deepseek.com'))
        self.assertTrue(web_search.available('https://api.deepseek.com/v1/chat/completions'))
        self.assertFalse(web_search.available('https://dashscope.aliyuncs.com/compatible-mode/v1'))


if __name__ == '__main__':
    unittest.main()
