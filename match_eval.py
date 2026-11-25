"""匹配结果对照人工基准（gold）评估：配对是否正确、结论档位是否在可接受范围内。

基准按原表位置（文件、工作表、行号）标识物资，重新清洗后序号变化也能对照。
对应关系按“对应组”比较：一对一、一对多、多对一和成套对应（多对多）都看作一组，
两侧组成员完全相同才算配对正确；内容完全相同的并列物资（如多面同规格机柜）可互换。

用法：python match_eval.py <基准.json> <结果 state.json 或会话 json> [--detail]
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CONSISTENT = {'一致', '基本一致'}


def loc_key(side, item):
    src = item.get('_source') or {}
    return f"{side}|{Path(str(src.get('file', ''))).name}|{src.get('sheet', '')}|{src.get('row', '')}"


def content_key(side, item):
    n = lambda v: re.sub(r'[\s"“”　]', '', str(v or '')).lower()
    if side == '造价':
        return (side, n(item.get('Estimation_Name')), n(item.get('Estimation_Quantity')), n(item.get('Estimation_Unit')))
    return (side, n(item.get('设备材料名称')), n(item.get('型号及规范')), n(item.get('数量')), n(item.get('单位')))


def is_anchor(row):
    return bool(row.get('covers') or row.get('covers_sources') or row.get('set_pairs'))


def result_groups(state):
    """结果中的对应组：两侧配对、成套项与其组成件（组内逐项配对单独成组）。返回 {物资键: 组成员集合}。"""
    ds, rows = state['dataset'], state['rows']
    src = {str(x['序号']): loc_key('造价', x) for x in ds['sources']}
    tgt = {str(x['序号']): loc_key('清册', x) for x in ds['targets']}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for key in [*src.values(), *tgt.values()]:
        find(key)
    for r in rows:
        s, t = str(r.get('source_id') or ''), str(r.get('target_id') or '')
        if s in src and t in tgt:
            union(src[s], tgt[t])
        owner = str(r.get('covered_by') or '')
        if owner in src:
            member = src.get(s) if s and not t else tgt.get(t)
            if member:
                union(member, src[owner])
    members = defaultdict(set)
    for key in parent:
        members[find(key)].add(key)
    return {key: frozenset(members[find(key)]) for key in parent}


def result_bands(state):
    """每条物资在结果表中的归类（与平台筛选口径一致）：两侧未匹配、四档结论、待复核、不参与核对。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from document_tools import result_category
    ds, rows = state['dataset'], state['rows']
    anchors = {str(r['source_id']) for r in rows if r.get('source_id') and is_anchor(r)}
    out = {}
    src = {str(x['序号']): loc_key('造价', x) for x in ds['sources']}
    tgt = {str(x['序号']): loc_key('清册', x) for x in ds['targets']}
    for r in rows:
        cat = result_category(r, anchors)
        if r.get('source_id') and str(r['source_id']) in src:
            out[src[str(r['source_id'])]] = cat
        if r.get('target_id') and str(r['target_id']) in tgt:
            out[tgt[str(r['target_id'])]] = cat
    return out


def evaluate(gold, state):
    ds = state['dataset']
    items = {loc_key('造价', x): content_key('造价', x) for x in ds['sources']}
    items.update({loc_key('清册', x): content_key('清册', x) for x in ds['targets']})
    pred_groups, pred_bands = result_groups(state), result_bands(state)
    gold_items = gold['items']
    canon = lambda keys: Counter(items.get(k, k) for k in keys)
    stats = Counter()
    errors = []

    def gold_of(key):
        g = gold_items[key]
        return frozenset(gold['groups'][g['group']]['members']) if g.get('group') else frozenset({key})

    def other_side(key, members):
        # 只比较另一侧的对应物资：成套组里某个组成件归属有误，只算该组成件与成套项错，不连累其余成员
        side = key.split('|', 1)[0]
        return tuple(sorted(canon(k for k in members if not k.startswith(side + '|')).items()))

    classes = defaultdict(list)
    for key in gold_items:
        if key in items:
            classes[items[key]].append(key)
    class_ok = {ck: Counter(other_side(k, gold_of(k)) for k in keys)
                == Counter(other_side(k, pred_groups.get(k, frozenset({k}))) for k in keys)
                for ck, keys in classes.items() if len(keys) > 1}
    for key, g in gold_items.items():
        if key not in items:
            stats['基准物资在结果中缺失'] += 1
            errors.append(('缺失', key, g, None, None))
            continue
        side = key.split('|', 1)[0]
        stats[side + '物资'] += 1
        gold_members, pred_members = gold_of(key), pred_groups.get(key, frozenset({key}))
        # 内容完全相同的并列物资（如同一清册里多面同规格机柜）作为一类整体比较：整类对应的物资一致即可
        pair_ok = other_side(key, gold_members) == other_side(key, pred_members) or class_ok.get(items[key], False)
        band = pred_bands.get(key, '')
        same_class = classes.get(items[key], [key]) if items[key] in class_ok else [key]
        band_ok = band in set().union(*(gold_items[k]['bands'] for k in same_class))
        stats[side + '配对正确'] += pair_ok
        stats[side + '结论正确'] += band_ok
        stats[side + '全对'] += pair_ok and band_ok
        if not (pair_ok and band_ok):
            errors.append(('配对' if not pair_ok else '结论', key, g, sorted(pred_members - {key}), band))
    extra = [k for k in items if k not in gold_items]
    stats['结果中有、基准未覆盖'] = len(extra)
    return stats, errors


def describe(key, state_items):
    x = state_items.get(key)
    if not x:
        return key
    side = key.split('|', 1)[0]
    if side == '造价':
        return f"造价{x['序号']} {str(x.get('Estimation_Name'))[:28]} {x.get('Estimation_Quantity')}{x.get('Estimation_Unit')}"
    return f"清册{x['序号']} {str(x.get('设备材料名称'))[:20]} {x.get('数量')}{x.get('单位')}"


def report(gold, state, detail=False, out=sys.stdout):
    stats, errors = evaluate(gold, state)
    ds = state['dataset']
    lookup = {loc_key('造价', x): x for x in ds['sources']}
    lookup.update({loc_key('清册', x): x for x in ds['targets']})
    for side in ('清册', '造价'):
        n = stats[side + '物资'] or 1
        print(f"{side} {stats[side + '物资']} 条：配对正确 {stats[side + '配对正确']}（{stats[side + '配对正确'] / n:.1%}），"
              f"结论在可接受范围 {stats[side + '结论正确']}（{stats[side + '结论正确'] / n:.1%}），全对 {stats[side + '全对']}（{stats[side + '全对'] / n:.1%}）", file=out)
    if stats['基准物资在结果中缺失'] or stats['结果中有、基准未覆盖']:
        print(f"基准物资在结果中缺失 {stats['基准物资在结果中缺失']} 条；结果中有、基准未覆盖 {stats['结果中有、基准未覆盖']} 条", file=out)
    if detail:
        for kind, key, g, pred, band in errors:
            gold_members = [describe(k, lookup) for k in sorted(gold['groups'][g['group']]['members']) if k != key] if g.get('group') else []
            print(f"[{kind}] {describe(key, lookup)} | 基准：{'、'.join(gold_members) or '无对应'} {'/'.join(g['bands'])}"
                  f" | 结果：{'、'.join(describe(k, lookup) for k in pred or []) or '无对应'} {band}"
                  + (f" | 说明：{g['note']}" if g.get('note') else ''), file=out)
    return stats, errors


if __name__ == '__main__':
    gold = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    state = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    report(gold, state, '--detail' in sys.argv)
