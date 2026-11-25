"""用领域知识回放一次已完成的物资匹配，对比“无知识 / 有知识”两次评审的结论差异。

读取运行数据中的清洗结果与候选配对，离线执行配对校验和评审两次（不调用模型、逐行批注取规则文案，
两次口径相同），只统计领域知识带来的变化。不修改运行数据。
造价条目缺少“成套组成”时（旧版清洗结果），按清洗规则从原工程表格补抽。
用法：python knowledge_eval.py [state.json] [输出.json]
"""
import copy
import importlib.util
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from cleaning import sheets
from domain_kg import DomainKnowledge, brief

HERE = Path(__file__).resolve().parent
OPEN_QUESTIONS = [
    'E-1-400 电缆沟支架规格未写格架型钢，需按设计图集核实（造价重量相当于∠50×5）。',
    'GB/T 1179-2017、GB/T 706-2016、GB 50217-2018 原文未取得，相关条目为 AI 核验，正式使用前建议专业人员对照原文复核。',
    '铝包钢线 LB20A 密度 6.53 与 6.59 未定，当前以区间参与换算，不影响本工程结论。',
]


def load_engine(enabled):
    os.environ.setdefault('LLM_RESULTS_PATH', tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location(f'kg_eval_{enabled}', HERE / 'LLM_service.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._log = lambda *args, **kwargs: None
    if not enabled:
        mod._DOMAIN_KNOWLEDGE = False
    return mod


def backfill_components(kg, dataset):
    """旧版清洗结果没有“成套组成”字段时，按清洗规则从原表补抽。"""
    columns = {(a['file'], a['sheet']): a.get('columns') for a in dataset.get('sheet_audit', []) if a.get('columns')}
    cache, count = {}, 0
    for item in dataset['sources']:
        src = item.get('_source') or {}
        key = (src.get('file'), src.get('sheet'))
        if '成套组成' in item or key not in columns or not src.get('row'):
            continue
        if key[0] not in cache:
            path = Path(dataset.get('origin', '')) / key[0]
            cache[key[0]] = dict(sheets(path)) if path.is_file() else {}
        rows = cache[key[0]].get(key[1])
        comps = kg.declared_components(rows, src['row'] - 1, columns[key]) if rows else []
        if comps:
            item['成套组成'] = comps
            count += len(comps)
    return count


def group_of(groups, s, t):
    return groups.get((s or '', t or '')) or groups.get((s or '', '')) or groups.get(('', t or '')) or '未分组'


def output_rows(pairs, sources, targets, groups):
    """按 build_rows 的字段组装评审输入；同类项组沿用原结果，避免模型分组差异干扰对比。"""
    src_by, tgt_by = {str(s['序号']): s for s in sources}, {str(t['序号']): t for t in targets}
    used = set(pairs.values())
    units = [(s, pairs.get(s)) for s in src_by] + [(None, t) for t in tgt_by if t not in used]
    order, seen, rows = {}, Counter(), []
    for s, t in units:
        name = group_of(groups, s, t)
        gid = order.setdefault(name, len(order) + 1)
        seen[gid] += 1
        si, ti = src_by.get(s or ''), tgt_by.get(t or '')
        rows.append({'分组序号': f'{gid}_{seen[gid]}', '源_序号': int(s) if s else '', '源_同类项名称': name,
                     '源_名称': si.get('Estimation_Name', '') if si else '', '源_型号': si.get('型号及规范', '') if si else '',
                     '源_数量': si.get('Estimation_Quantity', '') if si else '', '源_单位': si.get('Estimation_Unit', '') if si else '',
                     '匹配_同类项名称': name, '匹配_发现': bool(ti), '匹配_原因': '', '匹配_置信': '', '匹配_得分(%)': 99.0 if si and ti else 0.0,
                     '细项_得分(%)': 0.0, '匹配_序号': int(t) if t else '', '匹配_数量': ti.get('数量', '') if ti else '',
                     '匹配_单位': ti.get('单位', '') if ti else '', '匹配_名称': ti.get('设备材料名称', '') if ti else '',
                     '匹配_型号': ti.get('型号及规范', '') if ti else '', '匹配_价格': '', 'Top3_备选': [], '语义分组标签': str(gid)})
    return rows


def run(enabled, pairs, sources, targets, groups):
    mod = load_engine(enabled)
    refined = mod.rule_refine(dict(pairs), sources, targets, 'eval')
    rows = mod.review(output_rows(refined, sources, targets, groups), '', 'eval', sources, targets)
    return refined, rows


def band(row):
    return str(row['匹配_发现']).split('。')[0]


def by_item(rows):
    """以造价条目（或清册单侧条目）为单位索引评审行。"""
    return {(('S', str(r['源_序号'])) if r['源_序号'] != '' else ('T', str(r['匹配_序号']))): r for r in rows}


def evaluate(state_path, out_path):
    kg = DomainKnowledge()
    state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    dataset = copy.deepcopy(state['dataset'])
    backfilled = backfill_components(kg, dataset)
    sources, targets, pairs = dataset['sources'], dataset['targets'], state['pairs']
    groups = {(r['source_id'], r['target_id']): r.get('group') for r in state['rows']}
    S, T = {str(s['序号']): s for s in sources}, {str(t['序号']): t for t in targets}
    base_pairs, base = run(False, pairs, sources, targets, groups)
    kg_pairs, after = run(True, pairs, sources, targets, groups)
    reassigned = [dict(source=brief(S[s]), before=brief(T[base_pairs[s]]) if base_pairs.get(s) else '', after=brief(T[t]))
                  for s, t in kg_pairs.items() if base_pairs.get(s) != t]
    before_items, after_items = by_item(base), by_item(after)
    changes = []
    for key, r in after_items.items():
        old = before_items.get(key)
        if old is None or band(old) != band(r) or str(old['匹配_序号']) != str(r['匹配_序号']):
            item = S.get(key[1]) if key[0] == 'S' else T.get(key[1])
            changes.append(dict(item=brief(item), side='造价' if key[0] == 'S' else '清册单侧',
                                before=band(old) if old else '', after=band(r), finding=r['匹配_发现'],
                                knowledge=[f"{d['type']}:{d['id']}（{d['status']}）" for d in r.get('领域知识', [])]))
    report = dict(
        state=str(state_path), knowledge_problems=kg.validate(), backfilled_components=backfilled,
        statuses=dict(Counter(e['status'] for e in _entries(kg))),
        bands_without_knowledge=dict(Counter(band(r) for r in base)),
        bands_with_knowledge=dict(Counter(band(r) for r in after)),
        reassigned_pairs=reassigned, changes=changes,
        knowledge_rows=sum(1 for r in after if r.get('领域知识')),
        open_questions=OPEN_QUESTIONS,
        note='两次评审均离线运行（逐行批注取规则文案、不调用模型），差异只来自领域知识。')
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return report


def _entries(kg):
    p, c = kg.params, kg.comp
    cats = [kg.taxonomy] + kg.kinds + [e for node in kg.categories.values() for e in node.get('equivalents', [])]
    params = p['densities'] + [p[k] for k in ('steel_pipe', 'pipe_size_rule', 'angle_steel', 'cable_support', 'system_voltages', 'cable_voltage_systems', 'overhead_conductors')]
    return cats + params + [c['declared'], c['accessories']] + c['templates']


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    state = sys.argv[1] if len(sys.argv) > 1 else HERE / 'runtime' / 'state.json'
    out = sys.argv[2] if len(sys.argv) > 2 else HERE / 'qa' / 'knowledge-eval.json'
    rep = evaluate(state, out)
    print('知识库校验：', rep['knowledge_problems'] or '通过', '· 条目状态：', rep['statuses'])
    print('补抽成套组成件：', rep['backfilled_components'])
    print('无知识：', rep['bands_without_knowledge'])
    print('有知识：', rep['bands_with_knowledge'])
    print('知识改配：')
    for c in rep['reassigned_pairs']:
        print(f"  {c['source']}：{c['before']} → {c['after']}")
    print(f"结论变化 {len(rep['changes'])} 条：")
    for c in rep['changes']:
        print(f"  [{c['before']}→{c['after']}] {c['side']} {c['item']} | {c['finding'][:150]}")
    print('输出：', out)
