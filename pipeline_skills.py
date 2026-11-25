"""平台流程技能：数据清洗、物资匹配、数量评审各步骤的可编辑 Python 模块。

规则技能（skill_runtime）是纯函数沙箱，表达不了正则、异常处理和模型调用；流程技能因此允许导入
白名单内的纯计算模块，文件、网络、系统能力一律由宿主通过 ctx 提供。这是代码审查约束，
不是操作系统级沙箱。每次保存先过语法与接口检查，再用内置样例把整条流程跑通，全部通过才生效；
历史版本可回退，也可恢复内置版本。
"""
import ast
import builtins
import concurrent.futures
import difflib
import hashlib
import json
import linecache
import time
import types
import uuid
from pathlib import Path

import protected

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / 'skill_templates' / 'pipeline'

# 顺序即流程顺序；functions 为宿主调用的接口函数及其必需的位置参数个数
SKILLS = [
    dict(id='table_selection', name='清洗·选表与列映射', stage='数据清洗',
         functions={'clean_tables': 7, 'preview': 2}, invalidates='cleaning'),
    dict(id='row_extraction', name='清洗·逐行提取', stage='数据清洗',
         functions={'extract_table': 6, 'heading_level': 1}, invalidates='cleaning'),
    dict(id='text_rules', name='公共·文本与型号规则', stage='公共规则',
         functions={'normalize_power_units': 1, 'get_smart_item_info': 1, '_seq': 1}, invalidates='pairing'),
    dict(id='candidate_pairing', name='匹配·候选配对', stage='物资匹配',
         functions={'llm_pair': 4}, invalidates='pairing'),
    dict(id='rule_check', name='匹配·规则校验', stage='物资匹配',
         functions={'rule_refine': 4}, invalidates='pairing'),
    dict(id='set_coverage', name='匹配·成套项整体比对', stage='物资匹配',
         functions={'cover_sets': 5, 'is_whole_priced': 2, 'mark_set_rows': 3, 'review_sets': 4}, invalidates='pairing'),
    dict(id='set_matching', name='匹配·成套对应（多对多）', stage='物资匹配',
         functions={'match_sets': 5, 'apply_groups': 4}, invalidates='pairing'),
    dict(id='check_units', name='匹配·核对单元分组', stage='物资匹配',
         functions={'build_rows': 6, 'unit_name': 4}, invalidates='review'),
    dict(id='quantity_rules', name='评审·计量与换算', stage='数量评审',
         functions={'_mass_length_conversion_review': 2, '_steel_pipe_kg_per_m': 1, '_resolve_density_via_llm': 1,
                    '_unit_base_factor': 1, '_item_quantity': 1}, invalidates='review'),
    dict(id='row_review', name='评审·逐行核对', stage='数量评审',
         functions={'normalize_final_match_results': 1, '_format_final_match_findings': 2, '_classify_row_detail': 1,
                    '_row_pair_consistency_label': 1}, invalidates='review'),
    dict(id='group_review', name='评审·组级定档', stage='数量评审',
         functions={'_apply_final_group_reasons': 2, '_apply_group_level_scores': 1,
                    '_ensure_conversion_process_in_finding': 1, '_clear_alternative_candidates': 1}, invalidates='review'),
    dict(id='knowledge_apply', name='评审·领域知识定档', stage='数量评审',
         functions={'_apply_domain_knowledge': 3, '_append_knowledge_notes': 1}, invalidates='review'),
    dict(id='match_flow', name='流程·匹配与评审编排', stage='流程编排',
         functions={'pair': 4, 'review': 5}, invalidates='pairing'),
    dict(id='result_export', name='导出·结果字段', stage='结果导出',
         functions={'columns': 0, 'flatten': 1}, invalidates='none'),
    dict(id='edit_check', name='评审·修改校验', stage='结果修改',
         functions={'check_edit': 2}, invalidates='none'),
]
SPECS = {s['id']: s for s in SKILLS}
ALLOWED_IMPORTS = {'re', 'math', 'json', 'itertools', 'collections', 'unicodedata', 'functools', 'copy', 'string',
                   'difflib', 'statistics', 'decimal', 'fractions', 'operator', 'datetime', 'heapq', 'bisect'}
BLOCKED_NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals', 'vars', 'input', 'breakpoint',
                 'help', 'exit', 'quit', 'getattr', 'setattr', 'delattr', 'memoryview', 'object'}
MAX_CODE = 240_000


def _guarded_import(name, globals_=None, locals_=None, fromlist=(), level=0):
    if level or name.split('.')[0] not in ALLOWED_IMPORTS:
        raise ImportError(f'流程技能不允许导入模块 {name}')
    return builtins.__import__(name, globals_, locals_, fromlist, level)


SAFE_BUILTINS = {k: v for k, v in vars(builtins).items() if k not in BLOCKED_NAMES}
SAFE_BUILTINS['__import__'] = _guarded_import


def validate_source(skill_id, code):
    """语法、导入白名单、禁用名称与接口函数检查。返回检查项列表，不通过抛 ValueError。"""
    spec = SPECS.get(skill_id)
    if not spec:
        raise ValueError('未知流程技能')
    if len(code) > MAX_CODE:
        raise ValueError('技能代码超过240KB')
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f'Python语法错误：第{exc.lineno}行 {exc.msg}') from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in ALLOWED_IMPORTS:
                    raise ValueError(f'第{node.lineno}行：不允许导入模块 {alias.name}，文件、网络、系统能力请通过 ctx 使用')
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or '').split('.')[0] not in ALLOWED_IMPORTS:
                raise ValueError(f'第{node.lineno}行：不允许导入模块 {node.module}')
        elif isinstance(node, ast.Name) and (node.id in BLOCKED_NAMES or node.id.startswith('__')):
            raise ValueError(f'第{node.lineno}行：不允许使用 {node.id}')
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            raise ValueError(f'第{node.lineno}行：不允许访问双下划线属性 {node.attr}')
    defined = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name, count in spec['functions'].items():
        node = defined.get(name)
        if node is None:
            raise ValueError(f'缺少接口函数 {name}，宿主依赖它运行')
        positional = len(node.args.posonlyargs) + len(node.args.args)
        required = positional - len(node.args.defaults)
        if not (required <= count <= positional or node.args.vararg):
            raise ValueError(f'接口函数 {name} 须接受 {count} 个位置参数')
    return ['Python语法通过', '导入白名单与禁用名称检查通过', '接口函数检查通过']


def _contract_view():
    rows = [{'source_id': '1', 'target_id': '11', 'band': '部分一致'}, {'source_id': '2', 'target_id': '12', 'band': '一致'},
            {'source_id': '', 'target_id': '13', 'band': '不一致'}, {'source_id': '3', 'target_id': '', 'band': '不一致'}]
    rows += [{'source_id': str(10 + i), 'target_id': str(20 + i), 'band': '部分一致'} for i in range(6)]
    pairs = {r['source_id']: r['target_id'] for r in rows if r['source_id'] and r['target_id']}
    return {'rows': rows, 'pairs': pairs, 'admin': True, 'edits': [],
            'source_ids': [r['source_id'] for r in rows if r['source_id']], 'target_ids': [r['target_id'] for r in rows if r['target_id']]}


# 修改校验的底线：技能可以调整规则和阈值，但这些情况必须拒绝或须批准，否则修改不生效
EDIT_CONTRACT = [
    ('改配对到已被占用的清册条目须拒绝', {'type': 'change_pair', 'items': [{'source_id': '3', 'target_id': '12', 'reason': '型号规格一致'}]}, False, None),
    ('改配对到不存在的清册编号须拒绝', {'type': 'change_pair', 'items': [{'source_id': '3', 'target_id': '999', 'reason': '型号规格一致'}]}, False, None),
    ('不存在的结果条目须拒绝', {'type': 'set_verdict', 'items': [{'source_id': '8', 'target_id': '88', 'band': '一致', 'reason': '型号规格一致'}]}, False, None),
    ('不在四档及“不参与核对”之内的结论须拒绝', {'type': 'set_verdict', 'items': [{'source_id': '1', 'target_id': '11', 'band': '完全一致', 'reason': '型号规格一致'}]}, False, None),
    ('没有依据的结论修改须拒绝', {'type': 'set_verdict', 'items': [{'source_id': '1', 'target_id': '11', 'band': '一致', 'reason': ''}]}, False, None),
    ('依据完整的单条结论修改应允许', {'type': 'set_verdict', 'items': [{'source_id': '1', 'target_id': '11', 'band': '一致', 'reason': '型号规格一致，数量相同'}]}, True, None),
    ('一次修改 6 条以上须由用户批准', {'type': 'set_verdict', 'items': [{'source_id': str(10 + i), 'target_id': str(20 + i), 'band': '一致', 'reason': '型号规格一致'} for i in range(6)]}, True, True),
    ('没有修改记录时撤销须拒绝', {'type': 'undo', 'items': []}, False, None),
]


def edit_contract(code):
    """修改校验技能的底线测试：用平台固定的样例逐条检查，任何一条不符即不允许保存。"""
    try:
        check = SkillSet({'edit_check': code}, PipelineContext()).module('edit_check').check_edit
    except Exception as exc:
        raise ValueError(f'修改校验底线测试未通过：技能加载出错（{type(exc).__name__}：{exc}）') from exc
    for name, op, allow, confirm in EDIT_CONTRACT:
        try:
            result = check(json.loads(json.dumps(op)), _contract_view())
        except Exception as exc:
            raise ValueError(f'修改校验底线测试未通过：{name}（运行出错 {type(exc).__name__}：{exc}）') from exc
        if not isinstance(result, dict) or not isinstance(result.get('allow'), bool):
            raise ValueError(f'修改校验底线测试未通过：{name}（check_edit 须返回含布尔值 allow 的字典）')
        if result['allow'] is not allow or (confirm is not None and bool(result.get('confirm')) is not confirm):
            raise ValueError(f'修改校验底线测试未通过：{name}')
    return [f'修改校验底线测试通过（{len(EDIT_CONTRACT)} 项）']


class PipelineContext:
    """宿主提供给流程技能的能力。子类可接入真实模型、日志和领域知识。"""

    def __init__(self, ask=None, log=None, knowledge=None, settings=None, search=None):
        self._ask, self._logger, self._knowledge, self._search = ask, log, knowledge, search
        self._settings = dict(settings or {})
        self._api_key = ''

    def web_search(self, query, count=5):
        """联网检索（DeepSeek Search）。返回结果列表：title、url、site_name、date、authority、rerank_score、summary。
        未接入或检索失败时返回空列表，技能应把空结果当作“网上没有查到”继续。"""
        if not self._search:
            return []
        try:
            return list(self._search(query, count) or [])
        except Exception:
            return []

    def ask_llm(self, prompt, api_key, max_tokens=3000, retries=3):
        """返回 (JSON对象, 用量)。未配置模型时抛错，技能应在 try 中调用并保留兜底结论。"""
        if not self._ask:
            raise RuntimeError('当前未接入模型')
        return self._ask(prompt, api_key, max_tokens=max_tokens, retries=retries)

    def llm_json(self, prompt, api_key, max_tokens=3000):
        # 连接超时或网络中断时重试：一批调用失败会让整批条目退回规则兜底，结论明显变差
        data, _usage = self.ask_llm(prompt, api_key, max_tokens=max_tokens, retries=3)
        return data

    def log(self, task_id, message, progress=None):
        if self._logger:
            self._logger(task_id, message, progress)

    def setting(self, name, default=None):
        return self._settings.get(name, default)

    def knowledge(self):
        return self._knowledge() if callable(self._knowledge) else self._knowledge

    def api_key(self):
        return self._api_key

    def set_api_key(self, value):
        self._api_key = value or ''

    def parallel(self, fn, items, workers=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            return list(pool.map(fn, items))


class SkillRef:
    """技能间引用：按需取另一技能模块的属性，允许互相引用。"""

    def __init__(self, owner, skill_id):
        self._owner, self._id = owner, skill_id

    def __getattr__(self, name):
        return getattr(self._owner.module(self._id), name)


class SkillSet:
    """一次运行使用的一整套技能实例；模块级缓存只在本次运行内有效。"""

    def __init__(self, codes, ctx):
        self.codes, self.ctx, self._modules, self._loading = dict(codes), ctx, {}, set()
        for skill_id in self.codes:
            self.module(skill_id)

    def module(self, skill_id):
        if skill_id in self._modules:
            return self._modules[skill_id]
        if skill_id not in self.codes:
            raise KeyError(f'未知流程技能 {skill_id}')
        if skill_id in self._loading:
            raise RuntimeError(f'流程技能 {skill_id} 在模块加载时循环引用，请改为在函数内调用')
        self._loading.add(skill_id)
        try:
            filename = f'<流程技能 {skill_id}>'
            code = self.codes[skill_id]
            linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
            mod = types.ModuleType('pipeline_skill_' + skill_id)
            mod.__dict__.update(__builtins__=SAFE_BUILTINS, skill=lambda name: SkillRef(self, name), ctx=self.ctx)
            exec(compile(code, filename, 'exec'), mod.__dict__)
            self._modules[skill_id] = mod
            return mod
        finally:
            self._loading.discard(skill_id)

    def __getattr__(self, skill_id):
        if skill_id.startswith('_'):
            raise AttributeError(skill_id)
        return self.module(skill_id)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _doc(code):
    try:
        return (ast.get_docstring(ast.parse(code)) or '').strip()
    except SyntaxError:
        return ''


class PipelineStore:
    """流程技能的存放、版本与校验。data_dir 为空时只读内置模板（测试与离线评估使用）。"""

    def __init__(self, data_dir=None, templates=TEMPLATES):
        self.templates = Path(templates)
        self.folder = Path(data_dir) / 'pipeline_skills' if data_dir else None
        self.versions, self.base = [], {}
        if self.folder:
            (self.folder / 'versions').mkdir(parents=True, exist_ok=True)
            self.index_path = self.folder / 'history.json'
            index = json.loads(protected.read_text(self.index_path)) if self.index_path.exists() else {}
            self.versions, self.base = index.get('versions', []), index.get('base', {})
            for skill in SKILLS:
                self._sync_template(skill['id'])

    def template(self, skill_id):
        return protected.source(self.templates / f'{skill_id}.py')

    def _sync_template(self, skill_id):
        """未修改过的技能随内置模板升级；已修改的保留用户版本并提示模板有更新。"""
        path, template = self.folder / f'{skill_id}.py', self.template(skill_id)
        current = protected.read_text(path) if path.exists() else None
        if current is None or _digest(current) == self.base.get(skill_id):
            # 内容和基线都没变时不写盘：每次匹配都会新建技能库，反复改写会与杀毒软件扫描冲突
            if current != template:
                protected.write_atomic(path, template)
            if self.base.get(skill_id) != _digest(template):
                self.base[skill_id] = _digest(template)
                self._save_index()

    def _save_index(self):
        if self.folder:
            protected.write_atomic(self.index_path, json.dumps({'versions': self.versions, 'base': self.base}, ensure_ascii=False, indent=1))

    def code(self, skill_id):
        if skill_id not in SPECS:
            raise ValueError('未知流程技能')
        if self.folder:
            return protected.read_text(self.folder / f'{skill_id}.py')
        return self.template(skill_id)

    def codes(self):
        return {s['id']: self.code(s['id']) for s in SKILLS}

    def instantiate(self, ctx):
        return SkillSet(self.codes(), ctx)

    def describe(self):
        items = []
        for skill in SKILLS:
            code, template = self.code(skill['id']), self.template(skill['id'])
            history = [v for v in self.versions if v['skill_id'] == skill['id']]
            items.append(dict(skill, description=_doc(code).split('\n')[0], code=code, modified=_digest(code) != _digest(template),
                              template_updated=bool(self.folder) and _digest(template) != self.base.get(skill['id']) and _digest(code) != _digest(template),
                              versions=[{k: v[k] for k in ('id', 'time', 'summary', 'instruction')} for v in history][::-1],
                              last_diff=history[-1]['diff'] if history else '', lines=code.count('\n') + 1))
        return items

    def check(self, skill_id, code):
        """完整校验：静态检查 + 用内置样例跑通整条流程。返回检查项说明。"""
        checks = validate_source(skill_id, code)
        codes = self.codes()
        codes[skill_id] = code
        from pipeline_fixture import run_fixture
        checks += run_fixture(codes)
        if skill_id == 'edit_check':
            checks += edit_contract(code)
        return checks

    def apply(self, skill_id, code, instruction, summary):
        if not self.folder:
            raise ValueError('只读技能库不能保存修改')
        before = self.code(skill_id)
        if before.strip() == code.strip():
            raise ValueError('代码没有变化')
        tests = self.check(skill_id, code)
        record = dict(id=uuid.uuid4().hex[:12], skill_id=skill_id, time=time.strftime('%Y-%m-%d %H:%M:%S'),
                      instruction=instruction, summary=summary, tests=tests, before=before, after=code,
                      diff='\n'.join(difflib.unified_diff(before.splitlines(), code.splitlines(), fromfile=f'{skill_id}.py 原版本',
                                                          tofile=f'{skill_id}.py 新版本', lineterm='')))
        protected.write_text(self.folder / 'versions' / f"{record['id']}.json", json.dumps(record, ensure_ascii=False, indent=1))
        protected.write_atomic(self.folder / f'{skill_id}.py', code)
        self.versions.append({k: record[k] for k in ('id', 'skill_id', 'time', 'instruction', 'summary', 'diff')})
        self._save_index()
        return record

    def rollback(self, version_id):
        meta = next((v for v in self.versions if v['id'] == version_id), None)
        if not meta:
            raise ValueError('未找到该版本')
        record = json.loads(protected.read_text(self.folder / 'versions' / f'{version_id}.json'))
        return self.apply(record['skill_id'], record['before'], '回退版本 ' + version_id, '恢复该次修改之前的代码')

    def reset(self, skill_id):
        record = self.apply(skill_id, self.template(skill_id), '恢复内置版本', '恢复平台内置的技能代码')
        self.base[skill_id] = _digest(self.template(skill_id))
        self._save_index()
        return record


class EngineContext(PipelineContext):
    """绑定到 LLM_service 模块的全局命名空间：模型调用、日志、配置和领域知识都按调用时取值，
    因此平台对模块的配置（接口地址、模型、批量、专家提示、日志）和测试替换都会生效。"""

    def __init__(self, namespace):
        super().__init__()
        self._ns = namespace

    def ask_llm(self, prompt, api_key, max_tokens=3000, retries=3):
        return self._ns['_ask_llm'](prompt, api_key, max_tokens=max_tokens, retries=retries)

    def log(self, task_id, message, progress=None):
        self._ns['_log'](task_id, message, progress)

    def setting(self, name, default=None):
        return self._ns.get(name, default)

    def knowledge(self):
        return self._ns['_knowledge']()

    def web_search(self, query, count=5):
        search = self._ns.get('_web_search')
        if not search:
            return []
        try:
            return list(search(query, count) or [])
        except Exception:
            return []

    def api_key(self):
        return self._ns.get('_ACTIVE_API_KEY', '')

    def set_api_key(self, value):
        self._ns['_ACTIVE_API_KEY'] = value or ''
