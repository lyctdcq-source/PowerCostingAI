"""土石方数智化算量：模型数据与来源、规则质检、智能体动作校验与提示词、比选报告。

工程量、造价与寻优由前端计算引擎（static/earthwork/engine.js）完成；模型只负责理解指令、给出动作和解释结果，
不产出任何工程量数字。发给模型的只有工程量汇总和统计量，不含坐标。"""
import csv
import json
import math
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE / 'earthwork_data' / 'demo'
DEMO_NAME = '变电站场平示例工程'
MODEL_KEYS = ('origin', 'dx', 'x0', 'y0', 'nx', 'ny', 'dem', 'tsoil', 'tclay', 'inside', 'dist', 'platform', 'land', 'holes')
UNIT_NAMES = {'soil': '耕植土', 'clay': '粘土', 'w4': '全风化砂岩', 'w3': '强风化砂岩', 'w2': '中风化砂岩'}

# 单价、规则参数：键 → (名称, 单位, 下限, 上限)；与 engine.js 的 defaultPrices / defaultRules 对应
PRICES = {
    'soil': ('耕植土挖除/清表', '元/m³', 0, 1000), 'clay': ('粘土挖方', '元/m³', 0, 1000), 'w4': ('全风化砂岩挖方', '元/m³', 0, 1000),
    'w3': ('强风化砂岩挖方', '元/m³', 0, 2000), 'w2': ('中风化砂岩挖方', '元/m³', 0, 2000), 'fill': ('填方分层碾压', '元/m³', 0, 1000),
    'spoil': ('余方外运弃置', '元/m³', 0, 2000), 'borrow': ('借方外购运入', '元/m³', 0, 2000),
    'wall': ('C25毛石混凝土挡墙（含基础）', '元/m³', 0, 10000), 'slope': ('方格骨架植物护坡', '元/m²', 0, 5000),
    'kc': ('可用挖方压实折算系数', '', 0.5, 1.2),
}
RULES = {
    'iMin': ('合成坡度下限（寻优）', '%', 0, 5), 'iMax': ('合成坡度上限（寻优）', '%', 0, 8),
    'thr': ('挖填平衡区阈值', 'm', 0, 3), 'hw': ('挖方挡墙外露高度', 'm', 0, 8), 'embC': ('挖方挡墙埋深', 'm', 0, 5),
    'bench': ('墙顶平台宽', 'm', 0, 10), 'mcut': ('挖方边坡坡率 1:', '', 0.3, 4), 'hmin': ('骨架护坡起设坡高', 'm', 0, 10),
    'embF': ('填方挡墙埋深', 'm', 0, 6), 'mfill': ('填方边坡坡率 1:', '', 0.5, 4),
}
OPTIMIZE_TARGETS = {'full': '放坡寻优（标高 + 纵横坡）', 'height': '当前坡度下最优标高', 'balance': '挖填平衡标高', 'drawing': '套用图纸方案'}
VIEWS = {'iso': '东南鸟瞰', 'top': '俯视', 'under': '地下仰视'}
MAX_ACTIONS = 8
_CACHE = {}


def model_path(workspace=''):
    """工作空间里有已清洗的 model.json 时用它，否则用随平台发布的示例工程。"""
    if workspace:
        # 资料清洗后在成果目录里建的模型优先
        for candidate in (Path(workspace) / '土石方清洗成果' / 'model.json', Path(workspace) / 'model.json', Path(workspace) / 'data' / 'model.json'):
            if candidate.is_file():
                return candidate
    return DEMO / 'model.json'


def load_model(workspace=''):
    path = model_path(workspace)
    stamp = (str(path), path.stat().st_mtime_ns)
    if _CACHE.get('stamp') != stamp:
        raw = path.read_bytes()
        model = json.loads(raw)
        missing = [k for k in MODEL_KEYS if k not in model]
        if missing:
            raise ValueError('模型数据缺少字段：' + '、'.join(missing))
        if len(model['dem']) != model['nx'] * model['ny']:
            raise ValueError('模型数据的地形格网与行列数不一致')
        _CACHE.update(stamp=stamp, raw=raw, model=model, quality=None)
    return _CACHE['model']


def model_bytes(workspace=''):
    load_model(workspace)
    return _CACHE['raw']


def project_name(workspace=''):
    path = model_path(workspace)
    return DEMO_NAME if path.parent == DEMO else Path(workspace).name or DEMO_NAME


def _dem_at(model, east, north):
    nx, ny, dx, dem = model['nx'], model['ny'], model['dx'], model['dem']
    fi, fj = (east - model['x0']) / dx, (north - model['y0']) / dx
    i, j = max(0, min(nx - 2, int(fi))), max(0, min(ny - 2, int(fj)))
    u, v = min(1, max(0, fi - i)), min(1, max(0, fj - j))
    k = j * nx + i
    return dem[k] * (1 - u) * (1 - v) + dem[k + 1] * u * (1 - v) + dem[k + nx] * (1 - u) * v + dem[k + nx + 1] * u * v


def _points(folder):
    """清洗得到的高程点表（序号、东坐标、北坐标、高程）。"""
    path = next((p for p in sorted(folder.glob('*高程点*.csv'))), None)
    if not path:
        # 清洗成果目录：高程点在三维建模基础数据.dxf 里
        base = folder / '三维建模基础数据.dxf'
        if base.is_file():
            import earthwork_dxf
            points = earthwork_dxf.read(base)['layers'].get('高程点', {}).get('points', [])
            return [[i + 1, p['x'], p['y'], p['z']] for i, p in enumerate(points) if p.get('z') is not None]
        return []
    rows = []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        for row in list(csv.reader(handle))[1:]:
            try:
                rows.append([int(row[0]), float(row[1]), float(row[2]), float(row[3])])
            except (ValueError, IndexError):
                continue
    return rows


def sources(workspace=''):
    """资料清洗成果：高程点、钻孔分层，以及规则质检结果。"""
    model = load_model(workspace)
    holes = [{'id': h['id'], 'E': h['E'], 'N': h['N'], 'z': h['z'], 'water': h.get('water') or '',
              'depth': max((s[2] for s in h['segs']), default=0),
              'segs': [[UNIT_NAMES.get(s[0], s[0]), s[1], s[2], round(h['z'] - s[2], 2)] for s in h['segs']]} for h in model['holes']]
    return {'project': project_name(workspace), 'demo': model_path(workspace).parent == DEMO, 'points': _points(model_path(workspace).parent),
            'holes': holes, 'quality': quality(workspace)}


def quality(workspace=''):
    """规则质检：钻孔与地形吻合度、地层揭露情况、插值误差、钻孔覆盖和地下水。只算统计量，不调用模型。"""
    model = load_model(workspace)
    if _CACHE.get('quality'):
        return _CACHE['quality']
    holes, findings = model['holes'], []
    diffs = [(h['id'], round(h['z'] - _dem_at(model, h['E'], h['N']), 2)) for h in holes]
    off = [d for d in diffs if abs(d[1]) > 0.5]
    mean_abs = round(statistics.fmean(abs(d[1]) for d in diffs), 3) if diffs else 0
    findings.append({'level': 'warn' if off else 'ok', 'title': '钻孔孔口高程与地形',
                     'text': f'{len(holes)} 个钻孔孔口高程与 DEM 平均差 {mean_abs} m，最大差 {max((abs(d[1]) for d in diffs), default=0)} m'
                             + (f'；{len(off)} 个钻孔相差超过 0.5 m：' + '、'.join(f'{i}（{d:+.2f}）' for i, d in off[:8]) if off else '，吻合良好') + '。'})
    no_rock = [h['id'] for h in holes if not any(s[0] in ('w4', 'w3', 'w2') for s in h['segs'])]
    lens = [h['id'] for h in holes if any(s[0] == 'w3' for s in h['segs'])]
    medium = [h['id'] for h in holes if any(s[0] == 'w2' for s in h['segs'])]
    findings.append({'level': 'warn' if no_rock else 'ok', 'title': '土岩界面揭露',
                     'text': (f'{len(no_rock)} 个钻孔终孔仍在粘土中（{"、".join(no_rock[:8])}），该处岩面深度为插值推测，实际更深。' if no_rock
                              else '全部钻孔均揭穿粘土层进入风化岩。')})
    findings.append({'level': 'info', 'title': '强风化与中风化砂岩',
                     'text': f'强风化砂岩在 {len(lens)} 个钻孔中以夹层出现，按透镜体处理；中风化砂岩仅 {len(medium)} 个钻孔揭露'
                             + (f'（{"、".join(medium[:5])}）' if medium else '') + '，其顶面为推测，深挖时石方量不确定性较大。'})
    for name, item in (model.get('cv') or {}).items():
        try:
            method, table = item
            rmse, mae = table[method]
        except (TypeError, ValueError, KeyError):
            continue
        level = 'warn' if rmse >= 1 else 'ok'
        findings.append({'level': level, 'title': f'插值交叉验证 · {name}',
                         'text': f'{method} 留一交叉验证 RMSE {rmse} m、平均绝对误差 {mae} m' + ('，界面起伏大于钻孔间距可分辨的尺度，分层挖方量应留有余地。' if level == 'warn' else '。')})
    nx, dx, inside = model['nx'], model['dx'], model['inside']
    cells = [k for k, flag in enumerate(inside) if flag]
    far, worst = 0, 0.0
    for k in cells[::3]:   # 每三格取一格，足以估计覆盖
        east, north = model['x0'] + (k % nx) * dx, model['y0'] + (k // nx) * dx
        nearest = math.sqrt(min((h['E'] - east) ** 2 + (h['N'] - north) ** 2 for h in holes)) if holes else 0
        worst = max(worst, nearest)
        far += nearest > 30
    share = round(far / max(1, len(cells[::3])) * 100, 1)
    findings.append({'level': 'warn' if share > 5 else 'ok', 'title': '场平范围内钻孔覆盖',
                     'text': f'场平范围 {round(len(cells) * dx * dx):,} m²，距最近钻孔最远 {worst:.0f} m；{share}% 的面积距钻孔超过 30 m。'})
    water = sorted(float(h['water']) for h in holes if re.fullmatch(r'\d+(\.\d+)?', str(h.get('water') or '')))
    if water:
        shallow = [h['id'] for h in holes if re.fullmatch(r'\d+(\.\d+)?', str(h.get('water') or '')) and float(h['water']) < 3]
        findings.append({'level': 'warn' if shallow else 'info', 'title': '稳定水位',
                         'text': f'稳定水位埋深 {water[0]}～{water[-1]} m，中位数 {statistics.median(water)} m'
                                 + (f'；{len(shallow)} 个钻孔埋深不足 3 m（{"、".join(shallow[:8])}），该处挖方可能见水。' if shallow else '。')})
    result = {'findings': findings,
              'stats': {'holes': len(holes), 'grid': f'{model["nx"]}×{model["ny"]}，间距 {dx} m', 'collar_mean_abs': mean_abs,
                        'no_rock': len(no_rock), 'lens': len(lens), 'medium': len(medium), 'far_share': share, 'far_max': round(worst, 1),
                        'water_min': water[0] if water else None, 'water_median': statistics.median(water) if water else None}}
    _CACHE['quality'] = result
    return result


# ---------- 状态与快照
def new_state():
    return {'schemes': [], 'pending': None, 'suggestions': [], 'artifacts': [], 'qc_note': ''}


def clean_value(value, depth=0):
    """前端送来的快照只保留数字、布尔和短文本，限制层数与条数。"""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value, 4) if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:80]
    if depth >= 4:
        return None
    if isinstance(value, dict):
        return {str(k)[:40]: clean_value(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [clean_value(v, depth + 1) for v in value[:40]]
    return None


def clean_snapshot(raw):
    snapshot = clean_value(raw if isinstance(raw, dict) else {})
    design = snapshot.get('design') if isinstance(snapshot.get('design'), dict) else {}
    if not isinstance(design.get('H'), (int, float)):
        raise ValueError('还没有三维模型：请先完成资料清洗并生成三维模型，再进行标高寻优。')
    return snapshot


def _number(value, low, high, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}不是数字')
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{label} {value} 超出允许范围 {low}～{high}')
    return number


def validate_actions(actions, snapshot=None):
    """智能体动作只能来自白名单，取值须在范围内；任何一项不合规即整体拒绝，不执行部分动作。"""
    if not isinstance(actions, list):
        raise ValueError('动作须为列表')
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f'一次最多执行 {MAX_ACTIONS} 个动作')
    h_range = ((snapshot or {}).get('context') or {}).get('hRange') or [0, 9000]
    low, high = float(h_range[0]) - 5, float(h_range[1]) + 5
    cleaned = []
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError('动作格式无效')
        kind = action.get('type')
        if kind == 'set_design':
            item = {'type': kind}
            if action.get('H') is not None:
                item['H'] = round(_number(action['H'], low, high, '中心标高'), 2)
            if action.get('dH') is not None:
                item['dH'] = round(_number(action['dH'], -15, 15, '标高调整量'), 2)
            if 'H' in item and 'dH' in item:
                raise ValueError('标高与标高调整量不能同时给出')
            for key, label in (('iL', '纵坡'), ('iT', '横坡')):
                if action.get(key) is not None:
                    item[key] = round(_number(action[key], -8, 8, label + '（%）'), 3)
            if action.get('mode') is not None:
                if action['mode'] not in ('rule', 'free'):
                    raise ValueError('边坡处理方式须为 rule 或 free')
                item['mode'] = action['mode']
            if len(item) == 1:
                raise ValueError('set_design 未给出任何参数')
        elif kind == 'optimize':
            if action.get('target') not in OPTIMIZE_TARGETS:
                raise ValueError('未知的寻优目标')
            item = {'type': kind, 'target': action['target']}
        elif kind in ('set_price', 'set_rule'):
            table = PRICES if kind == 'set_price' else RULES
            key = action.get('key')
            if key not in table:
                raise ValueError('未知的' + ('单价' if kind == 'set_price' else '规则参数') + f'：{key}')
            item = {'type': kind, 'key': key, 'value': _number(action.get('value'), table[key][2], table[key][3], table[key][0])}
        elif kind == 'save_scheme':
            item = {'type': kind, 'name': re.sub(r'[\r\n\t]', ' ', str(action.get('name') or '')).strip()[:30]}
        elif kind == 'set_view':
            if action.get('view') not in VIEWS:
                raise ValueError('未知的视角')
            item = {'type': kind, 'view': action['view']}
        elif kind == 'section':
            if action.get('dir') not in ('none', 'x', 'y'):
                raise ValueError('剖切方向须为 none、x 或 y')
            item = {'type': kind, 'dir': action['dir']}
            if action.get('pos') is not None:
                item['pos'] = round(_number(action['pos'], 0, 1, '剖切位置'), 3)
        else:
            raise ValueError(f'未知的动作类型：{kind}')
        cleaned.append(item)
    return cleaned


def describe_action(action):
    kind = action['type']
    if kind == 'set_design':
        parts = []
        if 'H' in action: parts.append(f'中心标高 {action["H"]:.2f} m')
        if 'dH' in action: parts.append(f'标高{"抬高" if action["dH"] >= 0 else "降低"} {abs(action["dH"]):g} m')
        if 'iL' in action: parts.append(f'纵坡 {action["iL"]:g}%')
        if 'iT' in action: parts.append(f'横坡 {action["iT"]:g}%')
        if 'mode' in action: parts.append('按设计规则设挡墙护坡' if action['mode'] == 'rule' else '自由放坡')
        return '调整设计面：' + '，'.join(parts)
    if kind == 'optimize':
        return OPTIMIZE_TARGETS[action['target']]
    if kind == 'set_price':
        return f'单价“{PRICES[action["key"]][0]}”改为 {action["value"]:g} {PRICES[action["key"]][1]}'.rstrip()
    if kind == 'set_rule':
        return f'规则“{RULES[action["key"]][0]}”改为 {action["value"]:g} {RULES[action["key"]][1]}'.rstrip()
    if kind == 'save_scheme':
        return '保存方案' + (f'“{action["name"]}”' if action['name'] else '')
    if kind == 'set_view':
        return '视角切到' + VIEWS[action['view']]
    return '取消剖切' if action['dir'] == 'none' else ('东西向' if action['dir'] == 'x' else '南北向') + '剖切'


# ---------- 未配置模型时的本机规则：只认写法明确的指令
_NUM = r'(-?\d+(?:\.\d+)?)'


def local_plan(text):
    actions, design = [], {'type': 'set_design'}
    if re.search(r'自由放坡', text): design['mode'] = 'free'
    elif re.search(r'按设计规则|设挡墙', text): design['mode'] = 'rule'
    match = re.search(r'标高\s*(?:改为|改成|设为|调到|调为|取|为|=|到)?\s*(\d{3,4}(?:\.\d+)?)', text)
    if match: design['H'] = float(match.group(1))
    else:
        match = re.search(r'(降低|下调|下降|降|抬高|提高|上调|升高|抬)\s*(\d+(?:\.\d+)?)\s*(?:m|米)', text)
        if match: design['dH'] = float(match.group(2)) * (-1 if match.group(1)[0] in '降下' else 1)
    for key, word in (('iL', '纵坡'), ('iT', '横坡')):
        match = re.search(word + r'[^\d\-%]{0,6}' + _NUM + r'\s*[%％]', text)
        if match: design[key] = float(match.group(1))
    if len(design) > 1: actions.append(design)
    if re.search(r'套用图纸|图纸方案', text): actions.append({'type': 'optimize', 'target': 'drawing'})
    elif re.search(r'放坡寻优|纵横坡.*寻优|寻优.*坡|最省|最优方案', text): actions.append({'type': 'optimize', 'target': 'full'})
    elif re.search(r'最优标高|标高寻优', text): actions.append({'type': 'optimize', 'target': 'height'})
    elif re.search(r'挖填平衡|土方平衡', text) and re.search(r'标高|方案|切到|采用|设为', text): actions.append({'type': 'optimize', 'target': 'balance'})
    match = re.search(r'保存(?:为|成)?(?:方案)?\s*[“"「:：]?\s*([^”"」，。,\s]{0,20})', text) if re.search(r'保存(?:为|成)?方案|存为方案|保存当前', text) else None
    if match is not None: actions.append({'type': 'save_scheme', 'name': re.sub(r'^方案', '', match.group(1)) if match.group(1) not in ('方案', '当前方案') else ''})
    for view, word in (('top', '俯视'), ('under', '地下'), ('iso', '鸟瞰')):
        if word in text and re.search(r'视角|视图|看|切', text):
            actions.append({'type': 'set_view', 'view': view}); break
    return actions


def _wan(value):
    return f'{(value or 0) / 1e4:.1f} 万元'


def summary_line(snapshot):
    design, q, cost = snapshot.get('design') or {}, snapshot.get('quantities') or {}, snapshot.get('cost') or {}
    return (f'中心标高 {design.get("H", 0):.2f} m，纵坡 {design.get("iL", 0):g}%，横坡 {design.get("iT", 0):g}%，'
            f'{"按设计规则设挡墙护坡" if design.get("mode") != "free" else "自由放坡"}；挖方 {q.get("cut", 0):,.0f} m³，填方 {q.get("fill", 0):,.0f} m³，'
            f'余方外运 {q.get("spoil", 0):,.0f} m³，借方 {q.get("borrow", 0):,.0f} m³，挡墙 {q.get("wall", 0):,.0f} m³，'
            f'护坡 {q.get("slopeA", 0):,.0f} m²；土石方及挡墙护坡造价 {_wan(cost.get("total"))}')


def local_answer(steps, before, after):
    """未调用模型时的结果说明：只陈述执行了什么和结果数字。"""
    done = [s for s in steps if s.get('ok')]
    failed = [s for s in steps if not s.get('ok')]
    lines = ['（本机规则，未调用模型）已执行：' + '；'.join(s.get('label', '') + (f'（{s["note"]}）' if s.get('note') else '') for s in done) + '。' if done else '未执行任何动作。']
    if failed:
        lines.append('未能执行：' + '；'.join(f'{s.get("label", "")}（{s.get("note") or "失败"}）' for s in failed) + '。')
    lines.append('当前方案：' + summary_line(after) + '。')
    delta = ((after.get('cost') or {}).get('total') or 0) - ((before.get('cost') or {}).get('total') or 0)
    if done and abs(delta) >= 50:
        lines.append(f'与调整前相比造价{"增加" if delta > 0 else "减少"} {_wan(abs(delta))}。')
    return '\n'.join(lines)


# ---------- 提示词
ACTION_SPEC = '''可用动作（JSON 对象，字段名与取值必须照此书写）：
- {"type":"set_design","H":中心标高m,"dH":相对当前标高的调整量m,"iL":纵坡百分数,"iT":横坡百分数,"mode":"rule"或"free"}：
  只写要改的字段；H 与 dH 只能二选一；纵坡正值为南高北低，横坡正值为东高西低；rule=按设计规则设挡墙护坡，free=自由放坡不设挡墙。
- {"type":"optimize","target":"full"|"height"|"balance"|"drawing"}：full=在合成坡度限值内同时寻优标高和纵横坡；height=当前坡度下造价最低的标高；
  balance=当前坡度下挖填平衡标高；drawing=套用图纸方案。
- {"type":"set_price","key":单价键,"value":数值}；{"type":"set_rule","key":规则键,"value":数值}（坡度限值的单位是 %）。
- {"type":"save_scheme","name":"方案名"}：把当前方案存入方案对比。
- {"type":"set_view","view":"iso"|"top"|"under"}；{"type":"section","dir":"none"|"x"|"y","pos":0到1}：三维视角与剖切。'''

PLAN_PROMPT = '''你是变电站场平土石方方案比选助手。平台的计算引擎负责全部工程量、造价和寻优计算，你不计算、不估算任何工程量或造价数字。
根据用户的话决定：需要改动方案或运行计算时给出动作序列；只是提问时直接依据下面的数据作答。

{spec}
单价键：{prices}
规则键：{rules}

要求：
1. 用户要改方案、寻优、改单价或规则、保存方案、切换视图时，返回 {{"actions":[...],"answer":""}}，动作按执行顺序排列，最多 {limit} 个。
   用户要“比较”几种做法时，逐个设置并各自 save_scheme，方案名写清差别。
2. 用户只是询问（为什么、多少、哪个更省、风险在哪）且现有数据足以回答时，返回 {{"actions":[],"answer":"…"}}。回答中的数字必须逐字取自下面的数据，
   不得自行加减乘除推出新数字；数据不足以回答时如实说明还需要执行什么计算。
3. 用户的要求超出动作能力（如修改场平范围、重新建模、导入图纸）时，返回空动作并在 answer 中说明当前做不到。
4. answer 用中文，简明，不用 Markdown 标题。

当前方案与上下文（JSON）：
{snapshot}

已保存方案（JSON）：
{schemes}

用户：{text}'''

ANSWER_PROMPT = '''你是变电站场平土石方方案比选助手。下面是用户的要求、平台计算引擎刚执行的动作及执行前后的结果。请向用户说明结果。

要求：
1. 先一句话给出结论（执行后方案的标高、坡度和造价），再说明造价变化主要来自哪几项（对比执行前后的造价分项与工程量）。
2. 所有数字必须逐字取自下面的数据，不得自行计算新数字；需要差值时只引用数据里已有的 delta 字段。
3. 结合数据指出值得注意的约束或风险，仅限数据能支持的：raisedL>0 表示因征地线限制加高了挡墙；最大墙高超过 8 m 时圬工量为外推值；
   borrow>0 表示需要借方；合成坡度超出 iMin～iMax 限值；与图纸工程量（drawing）的偏差。数据不支持的风险不要写。
4. 有动作未能执行时如实说明。单价为示例值，结论用于方案间相对比较，提醒一次即可。
5. 中文，不超过 300 字，不用 Markdown 标题。返回 {{"answer":"…"}}。

用户要求：{text}
执行的动作：{steps}
执行前：{before}
执行后：{after}
造价变化（执行后 − 执行前，元）：{delta}
已保存方案：{schemes}'''

QC_PROMPT = '''你是岩土与场平算量的数据质检助手。下面是平台用规则算出的三维地质模型质检结果（统计量，不含坐标）。
请说明这些结果对分层土石方工程量和方案比选的影响，并给出处理建议（补勘、复核、留余量等）。
要求：只依据给出的质检结果，数字逐字引用，不编造未给出的数据；按影响大小排序，最多 5 条；中文，不用 Markdown 标题。
返回 {{"answer":"…"}}。

质检结果：{quality}
当前方案摘要：{summary}'''

PRICE_PROMPT = '''你是电力工程造价专家。下面是场平土石方比选用到的单价项和当前取值，以及从平台知识库检索到的资料。
请为各单价项建议取值，并说明对应的土石方类别或定额口径。

要求：
1. 只对有依据的单价项给建议；依据来自下面的知识库资料时，source 写该资料的 ID；没有资料依据、只是行业经验时 source 留空，
   并在 basis 中写明“经验值，须按当地定额与信息价核实”。不得编造文件名、定额编号或出处。
2. value 为数字，单位与该项当前单位一致。category 写土石方分类（如普通土、坚土、松砂石、次坚石）或工程做法，不确定则留空。
3. 返回 {{"suggestions":[{{"key":"单价键","value":数值,"category":"…","basis":"…","source":"知识ID或空"}}],"note":"总体说明"}}。

单价项（键：名称，单位，当前值）：
{prices}

地层与工程量（当前方案）：{summary}

知识库资料：
{knowledge}'''


def plan_prompt(text, snapshot, schemes):
    return PLAN_PROMPT.format(spec=ACTION_SPEC, limit=MAX_ACTIONS,
                              prices='、'.join(f'{k}={v[0]}' for k, v in PRICES.items()), rules='、'.join(f'{k}={v[0]}' for k, v in RULES.items()),
                              snapshot=json.dumps(snapshot, ensure_ascii=False), schemes=json.dumps(scheme_briefs(schemes), ensure_ascii=False), text=text[:2000])


def answer_prompt(text, steps, before, after, schemes):
    delta = cost_delta(before, after)
    return ANSWER_PROMPT.format(text=text[:2000], steps=json.dumps(steps, ensure_ascii=False), before=json.dumps(before, ensure_ascii=False),
                                after=json.dumps(after, ensure_ascii=False), delta=json.dumps(delta, ensure_ascii=False),
                                schemes=json.dumps(scheme_briefs(schemes), ensure_ascii=False))


def cost_delta(before, after):
    """执行前后造价分项差值由程序算好交给模型，模型不做算术。"""
    a, b = (after.get('cost') or {}), (before.get('cost') or {})
    items = {k: round((v or 0) - ((b.get('items') or {}).get(k) or 0)) for k, v in (a.get('items') or {}).items()}
    return {'total': round((a.get('total') or 0) - (b.get('total') or 0)), 'items': {k: v for k, v in items.items() if v}}


def scheme_briefs(schemes):
    return [{'name': s['name'], 'design': s['snapshot'].get('design'), 'quantities': s['snapshot'].get('quantities'), 'cost': s['snapshot'].get('cost')}
            for s in (schemes or [])[-8:]]


def clean_suggestions(result, snapshot, knowledge_ids):
    """模型的单价建议：键与取值范围校验，出处只认本次检索到的知识条目。"""
    current = snapshot.get('prices') or {}
    out = []
    for item in result.get('suggestions') or []:
        if not isinstance(item, dict) or item.get('key') not in PRICES:
            continue
        name, unit, low, high = PRICES[item['key']]
        try:
            value = _number(item.get('value'), low, high, name)
        except ValueError:
            continue
        source = str(item.get('source') or '')
        out.append({'key': item['key'], 'name': name, 'unit': unit, 'value': round(value, 2), 'current': current.get(item['key']),
                    'category': str(item.get('category') or '')[:40], 'basis': str(item.get('basis') or '')[:300],
                    'source': source if source in knowledge_ids else ''})
    return out


# ---------- 方案比选报告
QUANTITY_ROWS = (('挖方合计', 'cut', 'm³'), ('　耕植土', 'soil', 'm³'), ('　粘土', 'clay', 'm³'), ('　全风化砂岩', 'w4', 'm³'), ('　强风化砂岩', 'w3', 'm³'),
                 ('　中风化砂岩', 'w2', 'm³'), ('填方（压实方）', 'fill', 'm³'), ('填方区清表', 'strip', 'm³'), ('余方外运', 'spoil', 'm³'), ('借方', 'borrow', 'm³'),
                 ('场外边坡挖方', 'outCut', 'm³'), ('挡墙圬工', 'wall', 'm³'), ('填方挡墙长度', 'wFL', 'm'), ('挖方挡墙长度', 'wYL', 'm'),
                 ('最大墙高（填方）', 'wFH', 'm'), ('最大墙高（挖方）', 'wYH', 'm'), ('骨架护坡坡面', 'slopeA', 'm²'), ('因征地线加高挡墙段', 'raisedL', 'm'))


def write_report(path, project, schemes, narrative='', quality_result=None):
    from docx import Document
    from docx.shared import Pt
    if not schemes:
        raise ValueError('请先保存至少一个方案，再生成比选报告。')
    doc = Document()
    normal = doc.styles['Normal']; normal.font.name = 'Microsoft YaHei'; normal.font.size = Pt(10.5)
    doc.add_heading(f'{project} 场平标高方案比选报告', 0)
    doc.add_paragraph(f'生成时间：{time.strftime("%Y-%m-%d %H:%M")}。工程量与造价由平台计算引擎按三维地质模型计算；单价为比选用取值，结论用于方案间相对比较，需专业复核。')

    doc.add_heading('一、方案与造价', 1)
    best = min(schemes, key=lambda s: (s['snapshot'].get('cost') or {}).get('total') or 0)
    table = doc.add_table(rows=1, cols=7); table.style = 'Light Grid Accent 1'
    for cell, text in zip(table.rows[0].cells, ('方案', '中心标高 m', '纵坡 %', '横坡 %', '边坡处理', '挖方 / 填方 m³', '造价 万元')):
        cell.text = text
    for scheme in schemes:
        s = scheme['snapshot']; d, q = s.get('design') or {}, s.get('quantities') or {}
        cells = table.add_row().cells
        for cell, text in zip(cells, (scheme['name'] + ('（造价最低）' if scheme is best and len(schemes) > 1 else ''), f'{d.get("H", 0):.2f}', f'{d.get("iL", 0):g}', f'{d.get("iT", 0):g}',
                                      '自由放坡' if d.get('mode') == 'free' else '挡墙 + 护坡', f'{q.get("cut", 0):,.0f} / {q.get("fill", 0):,.0f}',
                                      f'{((s.get("cost") or {}).get("total") or 0) / 1e4:.1f}')):
            cell.text = text
    doc.add_paragraph()

    doc.add_heading('二、工程量对比', 1)
    table = doc.add_table(rows=1, cols=len(schemes) + 2); table.style = 'Light Grid Accent 1'
    for cell, text in zip(table.rows[0].cells, ['项目', '单位'] + [s['name'] for s in schemes]):
        cell.text = text
    for label, key, unit in QUANTITY_ROWS:
        cells = table.add_row().cells; cells[0].text = label; cells[1].text = unit
        for cell, scheme in zip(cells[2:], schemes):
            value = (scheme['snapshot'].get('quantities') or {}).get(key) or 0
            cell.text = f'{value:,.1f}' if unit == 'm' and 'H' in key else f'{value:,.0f}'
    doc.add_paragraph()

    doc.add_heading('三、造价分项（万元）', 1)
    names = list(dict.fromkeys(k for s in schemes for k in ((s['snapshot'].get('cost') or {}).get('items') or {})))
    table = doc.add_table(rows=1, cols=len(schemes) + 1); table.style = 'Light Grid Accent 1'
    for cell, text in zip(table.rows[0].cells, ['分项'] + [s['name'] for s in schemes]):
        cell.text = text
    for name in names + ['合计']:
        cells = table.add_row().cells; cells[0].text = name
        for cell, scheme in zip(cells[1:], schemes):
            cost = scheme['snapshot'].get('cost') or {}
            cell.text = f'{((cost.get("total") if name == "合计" else (cost.get("items") or {}).get(name)) or 0) / 1e4:.1f}'
    doc.add_paragraph()

    doc.add_heading('四、比选说明', 1)
    for line in (narrative or f'各方案中“{best["name"]}”造价最低，为 {_wan((best["snapshot"].get("cost") or {}).get("total"))}。本节未调用模型生成解读，请结合上表分析。').split('\n'):
        if line.strip():
            doc.add_paragraph(line.strip())

    prices = best['snapshot'].get('prices') or {}
    doc.add_heading('五、单价取值', 1)
    table = doc.add_table(rows=1, cols=3); table.style = 'Light Grid Accent 1'
    for cell, text in zip(table.rows[0].cells, ('项目', '取值', '单位')):
        cell.text = text
    for key, (name, unit, _, _) in PRICES.items():
        if key in prices:
            cells = table.add_row().cells; cells[0].text = name; cells[1].text = f'{prices[key]:g}'; cells[2].text = unit
    doc.add_paragraph('各方案单价取值不同时，以各方案保存时的取值计算；上表为造价最低方案的取值。')

    if quality_result:
        doc.add_heading('六、模型数据质检', 1)
        for item in quality_result['findings']:
            doc.add_paragraph(f'{item["title"]}：{item["text"]}')
    doc.add_heading('七、计算方法' if quality_result else '六、计算方法', 1)
    doc.add_paragraph('地形由等高线与高程点重建为格网 DEM；地层界面由钻孔分层插值；场平设计面为平面 z = H + 纵坡·s + 横坡·t。挖方按地层分别计量，可用挖方按折算系数回填，'
                      '耕植土及清表土外弃。按设计规则时沿场平边线逐米取断面布设挡墙与骨架护坡，边坡外缘不超出征地线。')
    doc.save(str(path))


REPORT_PROMPT = '''你是变电站场平土石方方案比选助手。下面是用户保存的各方案（设计参数、工程量、造价分项，均由平台计算引擎算出）。
请写比选说明：推荐方案及理由、各方案造价差异主要来自哪几项、需要注意的约束与风险（仅限数据能支持的：征地线加高挡墙 raisedL、
最大墙高超过 8 m、需要借方 borrow、合成坡度超限）。数字逐字取自数据，不得自行计算；单价为比选取值，提醒需按实际复核。
中文，分 3～5 段，每段不超过 120 字，不用 Markdown。返回 {{"answer":"…"}}。

方案数据：{schemes}'''


def report_prompt(schemes):
    return REPORT_PROMPT.format(schemes=json.dumps(scheme_briefs(schemes), ensure_ascii=False))
