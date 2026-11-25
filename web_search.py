"""DeepSeek 网页搜索与抓取（https://api-docs.deepseek.com/zh-cn/guides/search/）：供结果追问联网核实匹配依据。

Search 按 query 返回结构化结果，Fetch 抓取单个网页的 Markdown 正文；两者与对话接口共用 Bearer 鉴权，
按成功请求次数计费。只读请求：HTTP 408、429、5xx 等待后重试，其他 4xx 不重试。
"""
import re
import time
from urllib.parse import urlparse

import requests

SEARCH_PATH = '/v1/tools/search'
FETCH_PATH = '/v1/tools/fetch'
GOOD_SCORE = 1.5        # rerank_score ≥1.5 为优质结果
AUTHORITATIVE = 3       # authority ≥3 为极权威结果
ERRORS = {400: '请求不合法', 401: 'API Key 缺失或无效', 402: '账号余额不足，请充值后再使用联网搜索',
          403: '抓取请求被安全策略拒绝', 404: '未能获取可用的网页内容', 413: '请求体超过大小限制'}


def _clean(text, limit):
    """去掉网页标题、摘要里夹带的 HTML 标签和多余空白，截到 limit 字。"""
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]*(?:>|$)', ' ', str(text or ''))).strip()
    return text if len(text) <= limit else text[:limit] + '…'


def available(base_url):
    """只有 DeepSeek 官方接口提供 Search/Fetch；其他 OpenAI 兼容接口不支持联网检索。"""
    return urlparse(str(base_url or '')).hostname == 'api.deepseek.com'


def _root(base_url):
    parsed = urlparse(base_url)
    return f'{parsed.scheme}://{parsed.netloc}'


def _post(base_url, path, api_key, body, timeout, retries=2):
    reason = '联网检索失败'
    for attempt in range(retries + 1):
        try:
            response = requests.post(_root(base_url) + path, headers={'Authorization': 'Bearer ' + api_key},
                                     json=body, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError):
            reason = '联网检索连接超时或网络中断'
        else:
            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError:
                    raise RuntimeError('联网检索返回了无效数据')
            if status not in (408, 429) and status < 500:
                raise RuntimeError(f'联网检索失败（HTTP {status}，{ERRORS.get(status, "请求被拒绝")}）')
            reason = f'联网检索服务暂不可用（HTTP {status}）'
        if attempt < retries:
            time.sleep(attempt + 1)
    raise RuntimeError(reason)


def search(base_url, api_key, query, count=5, question=None):
    """返回按排序分数降序的结果：title、url、site_name、date、authority、rerank_score、summary。"""
    body = {'query': str(query)[:128], 'count': max(1, min(50, int(count)))}
    if question:
        body['ori_question'] = str(question)[:128]
    data = _post(base_url, SEARCH_PATH, api_key, body, timeout=30)
    results = data.get('results') if isinstance(data, dict) else None
    out = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or not item.get('url'):
            continue
        out.append({'title': _clean(item.get('title'), 60), 'url': str(item['url']),
                    'site_name': item.get('site_name') or '', 'date': (item.get('date') or '')[:10],
                    'authority': item.get('authority', -1), 'rerank_score': float(item.get('rerank_score') or 0),
                    'summary': _clean(item.get('summary') or item.get('snippet'), 1000)})
    return sorted(out, key=lambda r: -r['rerank_score'])


def fetch(base_url, api_key, url, limit=3000):
    """抓取网页正文（Markdown），截取前 limit 个字符。"""
    # 抓取失败时检索结果仍有摘要可用，不重试，避免拖慢追问
    data = _post(base_url, FETCH_PATH, api_key, {'url': str(url)[:8192]}, timeout=30, retries=0)
    content = str((data or {}).get('content') or '').strip()
    return {'title': str((data or {}).get('title') or ''), 'content': content[:limit] + ('…' if len(content) > limit else '')}
