"""Validated, bounded structured model requests; never log response contents."""
import collections
import json
import re
import threading
import time
import uuid
from urllib.parse import urlparse

import requests

# 按接口主机限速：同时进行的请求数与每分钟请求数（0 为不限制）。Kimi 账户按累计充值分级限速，
# Tier0 只允许并发 1、每分钟 3 次，超出即返回 HTTP 429；平台在发出请求前排队等待，而不是让请求失败。
_LIMITS = {}
_LIMITS_LOCK = threading.Lock()
RATE_LIMIT_WAIT = 300          # 连续被限速（HTTP 429）最多等待的总秒数，超过才报错
RATE_LIMIT_BACKOFF = (5, 10, 20, 40, 60)


class _Gate:
    def __init__(self, concurrency, rpm):
        self.concurrency, self.rpm = concurrency, rpm
        self.slots = threading.BoundedSemaphore(concurrency) if concurrency else None
        self.starts, self.lock = collections.deque(), threading.Lock()

    def __enter__(self):
        if self.slots:
            self.slots.acquire()
        while self.rpm:
            with self.lock:
                now = time.monotonic()
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                if len(self.starts) < self.rpm:
                    self.starts.append(now)
                    break
                wait = 60 - (now - self.starts[0]) + 0.1
            time.sleep(wait)
        return self

    def __exit__(self, *exc):
        if self.slots:
            self.slots.release()


def set_limit(host, concurrency=0, rpm=0):
    """设置某个接口主机的限速；与现有设置相同时保留原排队状态。"""
    concurrency, rpm = max(0, int(concurrency or 0)), max(0, int(rpm or 0))
    with _LIMITS_LOCK:
        gate = _LIMITS.get(host)
        if gate is None or (gate.concurrency, gate.rpm) != (concurrency, rpm):
            _LIMITS[host] = _Gate(concurrency, rpm)


def _gate(endpoint):
    with _LIMITS_LOCK:
        return _LIMITS.get(urlparse(endpoint).hostname) or _Gate(0, 0)


def _post(endpoint, api_key, payload):
    with _gate(endpoint):
        return requests.post(endpoint, headers={'Authorization': 'Bearer ' + api_key}, json=payload, timeout=240)


def _retry_after(response, waited_count):
    try:
        value = float(response.headers.get('Retry-After') or response.headers.get('X-Retry-After') or 0)
    except (TypeError, ValueError, AttributeError):
        value = 0
    backoff = RATE_LIMIT_BACKOFF[min(waited_count, len(RATE_LIMIT_BACKOFF) - 1)]
    return max(value, backoff) if value > 2 else backoff


def always_thinking(model):
    """始终开启思考的模型（Kimi K3）：temperature 固定为 1，传其他值返回 400；思考内容也占输出 token。"""
    return str(model).lower().startswith('kimi-k3')


def build_payload(endpoint, model, messages, budget, thinking, temperature=True, tools=None, tool_choice=None):
    """tools 为 OpenAI 兼容的工具定义时按原生工具调用请求（不限定 JSON 输出），否则要求返回 JSON 对象。"""
    payload = {'model': model, 'messages': messages, 'max_tokens': budget}
    if tools:
        payload.update(tools=tools, tool_choice=tool_choice or 'auto')
    else:
        payload['response_format'] = {'type': 'json_object'}
    if temperature and not always_thinking(model):
        payload['temperature'] = 0
    if urlparse(endpoint).hostname == 'api.deepseek.com':
        payload['thinking'] = {'type': 'enabled' if thinking else 'disabled'}
    if always_thinking(model):
        # 批量步骤用低推理强度控制耗时，问答用高推理强度；输出额度至少 8192，避免思考占满后正文为空
        payload['reasoning_effort'] = 'high' if thinking else 'low'
        payload['max_tokens'] = max(budget, 8192)
    return payload


def _send(endpoint, api_key, build, flags, log, rate_wait):
    """发出一次请求。build(temperature) 生成请求体；模型只允许默认 temperature 时去掉该参数立即重发（flags 记住，
    后续重试不再带）；限速或繁忙（HTTP 429）按服务端建议或逐级加长的间隔等待后重发。两者都不消耗重试次数。"""
    payload = build(flags['temperature'])
    response = _post(endpoint, api_key, payload)
    if response.status_code == 400 and 'temperature' in payload and 'temperature' in response.text.lower():
        flags['temperature'] = False
        payload = build(False)
        response = _post(endpoint, api_key, payload)
    waited, count = 0, 0
    while response.status_code == 429 and waited < rate_wait:
        delay = _retry_after(response, count)
        if log:
            log(f'模型服务限速或繁忙（HTTP 429），{delay:g} 秒后重试。')
        time.sleep(delay)
        waited, count = waited + delay, count + 1
        response = _post(endpoint, api_key, payload)
    return response


def _http_failure(status, rate_wait):
    """HTTP 错误：客户端错误直接报错（不重试）；限速和服务端错误返回原因，由调用方重试。"""
    if status not in (408, 429) and status < 500:
        hint = '请检查 API Key 和账户权限' if status in (401, 403) else '请检查模型名称、接口地址和账户状态'
        raise RuntimeError(f'模型接口返回 HTTP {status}，{hint}。')
    return (f'模型服务持续限速或繁忙（HTTP 429，已等待约 {max(1, round(rate_wait / 60))} 分钟），请稍后再试，或在模型设置中降低并发与每分钟请求数'
            if status == 429 else f'模型服务暂不可用（HTTP {status}）')


def request_json(endpoint, model, api_key, messages, max_tokens=8192, retries=3, log=None, thinking=False, rate_wait=RATE_LIMIT_WAIT):
    """thinking=True 时请求深度思考（问答类使用）；接口不支持时自动退回普通模式。"""
    attempts = max(1, min(3, retries))
    budget = max(max_tokens, 16384) if thinking else max_tokens
    reason = '模型响应无效'
    flags = {'temperature': True}
    for attempt in range(attempts):
        try:
            response = _send(endpoint, api_key, lambda t: build_payload(endpoint, model, messages, budget, thinking, t), flags, log, rate_wait)
            status = response.status_code
            if status >= 400:
                if thinking and status == 400:
                    # 深度思考与结构化输出不能同时使用等情况：退回普通模式重试，不消耗重试次数
                    if log:
                        log('模型接口不支持深度思考模式，改用普通模式回答。')
                    return request_json(endpoint, model, api_key, messages, max_tokens, retries, log, False, rate_wait)
                reason = _http_failure(status, rate_wait)
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                choices = body.get('choices') if isinstance(body, dict) else None
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                message = choice.get('message')
                raw = message.get('content') if isinstance(message, dict) else None
                finish = choice.get('finish_reason')
                if finish == 'length':
                    reason = '模型输出被截断'
                    budget = min(budget * 2, 32768)
                elif finish not in (None, 'stop'):
                    reason = '模型未正常完成结构化输出'
                elif not choice:
                    reason = '模型接口未返回有效的对话响应'
                elif not isinstance(raw, str) or not raw.strip():
                    reason = '模型返回了空结果'
                else:
                    try:
                        result = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()))
                    except ValueError:
                        result = None
                    if isinstance(result, dict) and result:
                        return result, body.get('usage', {})
                    reason = '模型未返回有效的 JSON 对象'
        except (requests.Timeout, requests.ConnectionError):
            reason = '模型连接超时或网络中断'
        if log:
            log(f'{reason}（第 {attempt + 1}/{attempts} 次）' + ('，正在重试。' if attempt + 1 < attempts else '。'))
        if attempt + 1 < attempts:
            time.sleep(attempt + 1)
    raise RuntimeError(f'{reason}，已尝试 {attempts} 次。请稍后重新执行；本步骤结果未保存，已有检查点不受影响。')


class ToolsUnsupported(RuntimeError):
    """模型接口不接受原生工具调用（tools 参数）：调用方改用 JSON 动作协议。"""


def _plain(messages):
    """去掉历史消息中的思考内容：退回普通模式后，部分接口不接受输入里带 reasoning_content。"""
    return [{k: v for k, v in m.items() if k != 'reasoning_content'} for m in messages]


def request_chat(endpoint, model, api_key, messages, tools=None, tool_choice=None, max_tokens=8192, retries=3, log=None,
                 thinking=False, rate_wait=RATE_LIMIT_WAIT):
    """原生工具调用的一轮对话，返回 ({'content', 'tool_calls', 'reasoning_content'}, usage)。
    tool_calls 为 [{'id', 'name', 'arguments'}]，arguments 是模型给出的原始 JSON 文本，由调用方解析和校验；
    接口不接受 tools 参数时抛出 ToolsUnsupported。"""
    attempts = max(1, min(3, retries))
    budget = max(max_tokens, 16384) if thinking else max_tokens
    reason = '模型响应无效'
    flags = {'temperature': True}
    for attempt in range(attempts):
        try:
            response = _send(endpoint, api_key, lambda t: build_payload(endpoint, model, messages, budget, thinking, t, tools, tool_choice),
                             flags, log, rate_wait)
            status = response.status_code
            if status >= 400:
                if thinking and status == 400:
                    if log:
                        log('模型接口不支持在深度思考模式下调用工具，改用普通模式。')
                    return request_chat(endpoint, model, api_key, _plain(messages), tools, tool_choice, max_tokens, retries, log, False, rate_wait)
                if tools and status in (400, 422):
                    raise ToolsUnsupported(f'模型接口不接受原生工具调用（HTTP {status}）')
                reason = _http_failure(status, rate_wait)
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                choices = body.get('choices') if isinstance(body, dict) else None
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
                finish = choice.get('finish_reason')
                if finish == 'length':
                    reason = '模型输出被截断'
                    budget = min(budget * 2, 32768)
                elif finish not in (None, 'stop', 'tool_calls'):
                    reason = '模型未正常完成回答'
                elif not message:
                    reason = '模型接口未返回有效的对话响应'
                else:
                    calls = []
                    for call in message.get('tool_calls') or []:
                        function = call.get('function') if isinstance(call, dict) and isinstance(call.get('function'), dict) else {}
                        if function.get('name'):
                            arguments = function.get('arguments')
                            calls.append({'id': str(call.get('id') or 'call_' + uuid.uuid4().hex[:12]), 'name': str(function['name']),
                                          'arguments': arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)})
                    content = message.get('content') if isinstance(message.get('content'), str) else ''
                    if calls or content.strip():
                        thought = message.get('reasoning_content')
                        return {'content': content, 'tool_calls': calls,
                                'reasoning_content': thought if isinstance(thought, str) else None}, body.get('usage', {})
                    reason = '模型返回了空结果'
        except (requests.Timeout, requests.ConnectionError):
            reason = '模型连接超时或网络中断'
        if log:
            log(f'{reason}（第 {attempt + 1}/{attempts} 次）' + ('，正在重试。' if attempt + 1 < attempts else '。'))
        if attempt + 1 < attempts:
            time.sleep(attempt + 1)
    raise RuntimeError(f'{reason}，已尝试 {attempts} 次。请稍后重新执行；已有检查点不受影响。')
