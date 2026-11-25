"""流程技能保存前的回放校验：用内置小样例把清洗、匹配、评审、导出整条流程跑一遍。

结构检查（每条物资恰好出现一次、结论档位合法、导出列齐全等）不通过则拒绝保存；
行为检查（身份属性改配、成套项覆盖、计量性质、标准换算）与内置规则结果不同时只作提示，
因为用户可能有意修改了这些规则。模型调用由桩函数代替，不产生网络请求。
"""
from pathlib import Path

import protected
from domain_kg import DomainKnowledge
from pipeline_skills import PipelineContext, SkillSet
from skill_runtime import compile_skill, invoke

HERE = Path(__file__).resolve().parent
BANDS = ('一致', '基本一致', '部分一致', '不一致', '不参与核对', '含于成套项', '待复核', '评审未完成')

ESTIMATE = dict(id=0, file='样例可研.xlsx', sheet='安装单位工程汇总表（表三甲）', rows=[
    ['序号', '编制依据', '项目名称及规格', '单位', '数量', '设备费单价', '装置性材料单价'],
    ['4.2', '', '继电保护', '', '', '', ''],
    ['', '（甲设）', '1号主变保护屏：费用包含1.1~1.2', '面', 1, 120000, None],
    ['', '', '1.1\t、1号主变压器保护1', '台', 1, None, None],
    ['', '', '1.2\t、打印机', '台', 1, None, None],
    ['', 'GD5-13', '变电站保护盘台柜安装', '面', 1, 2061.59, None],
    ['', '（乙材）', '电力电缆　ZR-YJV22 1kV 双芯 10', 'km', 0.3, None, 52000],
    ['', '（乙材）', '电力电缆　ZR-YJV22 1kV 四芯 4', 'km', 0.2, None, 26000],
    ['', '（乙材）', '镀锌钢管　φ40', 't', 4.92, None, 6800],
    ['', '（甲设）', '时钟同步系统 ：', '套', 1, 68200, None],
    ['', 'YD5-52', '数字同步设备安装调测 主站时钟屏', '台', 1, 2148.98, None]])
INVENTORY = dict(id=1, file='样例清册.xlsx', sheet='电气二次', rows=[
    ['序号', '名称', '型号及规范', '单位', '数量', '备注'],
    ['（一）', '继电保护', '', '', '', ''],
    ['1', '1号主变保护屏', '2260×800×600mm', '面', 1, ''],
    ['1.1', '1号主变压器保护1', '主后一体', '台', 1, ''],
    ['1.2', '打印机', '', '台', 1, ''],
    ['（二）', '时钟同步系统', '', '套', 1, ''],
    ['1', '主时钟', '具备GPS+北斗对时功能', '台', 1, ''],
    ['2', '从时钟', '', '台', 1, ''],
    ['（三）', '电缆', '', '', '', ''],
    ['1', '电力电缆', 'ZR-YJV22-0.6/1kV-2×10', '米', 300, ''],
    ['2', '电力电缆', 'ZR-YJV22-0.6/1kV-4×4', '米', 200, ''],
    ['3', '热镀锌钢管', 'φ40', '米', 1200, ''],
    ['4', '等级保护测评', '', '项', 1, '']])
class _Rules:
    """规则技能（内置模板）的最小调用接口，供清洗样例使用。"""

    def __init__(self):
        self.ns = {key: compile_skill(key, protected.source(HERE / 'skill_templates' / f'{key}.py'))
                   for key in ('folder_discovery', 'data_cleaning')}

    def call(self, key, function, *args):
        return invoke(self.ns[key], function, *args)


def _ask_matching(sources, targets):
    """配对桩：给电缆一个错误的首选候选，检验身份属性冲突能否被排除。"""
    def find(items, key, word):
        return next((str(x['序号']) for x in items if word in str(x.get(key, '')) + str(x.get('型号及规范', ''))), None)
    s = {w: find(sources, 'Estimation_Name', w) for w in ('保护屏', '双芯 10', '四芯 4', '钢管', '时钟同步')}
    t = {w: find(targets, '设备材料名称', w) for w in ('保护屏', '2×10', '4×4', '钢管', '时钟同步')}
    answer = [{'src': s['保护屏'], 'candidates': [{'qc': t['保护屏'], 'conf': 95}]},
              {'src': s['双芯 10'], 'candidates': [{'qc': t['4×4'], 'conf': 93}, {'qc': t['2×10'], 'conf': 90}]},
              {'src': s['四芯 4'], 'candidates': [{'qc': t['2×10'], 'conf': 92}]},
              {'src': s['钢管'], 'candidates': [{'qc': t['钢管'], 'conf': 95}]},
              {'src': s['时钟同步'], 'candidates': [{'qc': t['时钟同步'], 'conf': 95}]}]

    def ask(prompt, api_key, max_tokens=3000, retries=3):
        if '待匹配的估算物资' in prompt:
            return {'rows': [{'src': int(a['src']), 'candidates': [{'qc': int(c['qc']), 'conf': c['conf']} for c in a['candidates'] if c['qc']]}
                             for a in answer if a['src']]}, {}
        return {}, {}
    return ask, s, t


def run_fixture(codes):
    """用给定的整套技能代码跑样例。结构问题抛 ValueError；返回检查说明（含行为差异提示）。"""
    kg = DomainKnowledge()
    hints, checks = [], []
    try:
        clean_set = SkillSet(codes, PipelineContext(knowledge=kg))
        tables = [dict(ESTIMATE, rows=[list(r) for r in ESTIMATE['rows']]), dict(INVENTORY, rows=[list(r) for r in INVENTORY['rows']])]
        ds = clean_set.table_selection.clean_tables('样例工程', '样例工程', tables, [], [], _Rules(), lambda text: None)
    except Exception as exc:
        raise ValueError(f'回放校验未通过：样例清洗出错（{type(exc).__name__}：{exc}）') from exc
    sources, targets = ds.get('sources') or [], ds.get('targets') or []
    if not sources or not targets or any('序号' not in x or '_source' not in x for x in sources + targets):
        raise ValueError('回放校验未通过：样例清洗没有得到带序号和来源的双侧物资')
    checks.append(f'样例清洗通过（造价{len(sources)}条，清册{len(targets)}条）')
    if not any(x.get('成套组成') for x in sources) or not any(x.get('成套行') and x.get('下级物资') for x in targets):
        hints.append('样例中未识别出原表成套结构（与内置规则不同）')

    ask, s, t = _ask_matching(sources, targets)
    try:
        match_set = SkillSet(codes, PipelineContext(ask=ask, knowledge=kg, settings={'MATCH_BATCH': 20, 'MAX_WORKERS': 1}))
        pairs, covers = match_set.match_flow.pair(sources, targets, 'fixture-key', 'fixture')
        rows = match_set.check_units.build_rows(sources, targets, pairs, covers, 'fixture', '')
        rows = match_set.match_flow.review(rows, '', 'fixture', sources, targets)
        flat = match_set.result_export.flatten(rows)
        columns = match_set.result_export.columns()
    except Exception as exc:
        raise ValueError(f'回放校验未通过：样例匹配评审出错（{type(exc).__name__}：{exc}）') from exc

    src_ids, tgt_ids = {str(x['序号']) for x in sources}, {str(x['序号']) for x in targets}
    if any(str(k) not in src_ids or str(v) not in tgt_ids for k, v in pairs.items()) or len(set(pairs.values())) != len(pairs):
        raise ValueError('回放校验未通过：配对包含不存在或重复占用的序号')
    seen_s = [str(r['源_序号']) for r in rows if str(r.get('源_序号') or '')]
    seen_t = [str(r['匹配_序号']) for r in rows if str(r.get('匹配_序号') or '')]
    if sorted(seen_s) != sorted(src_ids) or sorted(seen_t) != sorted(tgt_ids):
        raise ValueError('回放校验未通过：评审结果行没有让每条物资恰好出现一次')
    bad = [r.get('匹配_发现') for r in rows if not str(r.get('匹配_发现') or '').startswith(BANDS)]
    if bad:
        raise ValueError(f'回放校验未通过：结论没有以合法档位开头，如“{str(bad[0])[:30]}”')
    if any(str(k).startswith('_') for r in rows for k in r):
        raise ValueError('回放校验未通过：结果行残留内部字段')
    if not columns or any(set(columns) - set(f) for f in flat):
        raise ValueError('回放校验未通过：导出字段不完整')
    checks.append(f'样例匹配、评审与导出通过（{len(rows)}行）')

    band = {(str(r.get('源_序号') or ''), str(r.get('匹配_序号') or '')): str(r['匹配_发现']).split('。')[0] for r in rows}
    if pairs.get(s['双芯 10']) != t['2×10'] or pairs.get(s['四芯 4']) != t['4×4']:
        hints.append('电缆芯数×截面冲突没有被排除或改配（与内置规则不同）')
    covered = {c['target'] for v in covers.values() for c in v if c.get('target') and not c.get('source')}
    if len(covered) < 4:
        hints.append(f'成套项只覆盖了{len(covered)}条组成件（内置规则为4条）')
    if band.get(('', next((str(x['序号']) for x in targets if '测评' in x['设备材料名称']), ''))) != '不参与核对':
        hints.append('服务费用条目没有标为“不参与核对”（与内置规则不同）')
    pipe = next((r for r in rows if '钢管' in str(r.get('源_名称'))), None)
    if not pipe or 'GB/T 3091-2015' not in str(pipe.get('匹配_发现')):
        hints.append('镀锌钢管换算没有引用 GB/T 3091-2015（与内置规则不同）')
    checks.append('行为检查：' + ('；'.join(hints) if hints else '与内置规则一致（身份属性改配、成套项覆盖、计量性质、标准换算）'))
    return checks
