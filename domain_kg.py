"""电力物资领域知识：品类本体、身份属性、计量参数、成套组成与计量性质。

知识数据保存在 domain_knowledge/*.json，与代码分离，逐条带出处、证据级别和审核状态。
本模块只读知识并给出判断依据：结果中的 decisive 仅在所用知识全部“已审核”时为 True；
“待审核”知识只生成提示和算式，不应改变评审结论。
"""
import itertools
import json
import re
from collections import defaultdict
import unicodedata
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent / 'domain_knowledge'
STATUSES = ('待审核', '已审核', '停用')
EVIDENCE = ('标准原文', '标准公式', '工程原表', '专业经验', '人工确认')
MASS_UNITS = {'T': 1000.0, '吨': 1000.0, 'KG': 1.0, '千克': 1.0, '公斤': 1.0}
LENGTH_UNITS = {'M': 1.0, '米': 1.0, 'KM': 1000.0, '千米': 1000.0, '公里': 1000.0}
COUNT_UNITS = set('台套面块只个件组副根支条盘对基串付')
CN_DIGITS = {'单': 1, '一': 1, '双': 2, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5}
TOLERANCE = 0.01  # 与评审层一致：换算后相对差不超过1%判一致
LABEL = re.compile(r'^\s*(?:[(（][甲乙][设材][)）])?\s*\d+(?:\.\d+)*\s*[、\t．]+\s*')
ATTR_LABELS = {
    'cores_section': '芯数×截面', 'voltage_system': '电压等级', 'cable_armour': '铠装代号', 'fire_class': '阻燃耐火等级',
    'busbar_size': '截面尺寸', 'conductor_size': '导线规格', 'conductor_al': '适配导线截面', 'round_section': '标称截面',
    'dn': '公称口径', 'angle_size': '角钢规格', 'support_type': '支架型号', 'post_insulator': '电压等级/抗弯强度',
    'category': '品类',
}
PARAM_ATTRS = ('voltage_system', 'post_insulator')  # 需要读取参数表的属性


def norm(text):
    """NFKC、大写、乘号统一为 X、平方毫米统一为 MM2，便于跨表比较。"""
    text = unicodedata.normalize('NFKC', str(text or '')).upper()
    text = re.sub(r'[×*]', 'X', text)
    text = re.sub(r'MM\^?2|平方毫米', 'MM2', text)
    return re.sub(r'\s+', ' ', text).strip()


def number(value):
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def fmt(value):
    return f'{value:.6g}'


def item_name(item):
    return str(item.get('Estimation_Name') or item.get('设备材料名称') or item.get('name') or '')


def item_spec(item):
    return str(item.get('型号及规范') or item.get('Spec') or item.get('spec') or '')


def item_unit(item):
    return str(item.get('Estimation_Unit') or item.get('单位') or item.get('unit') or '').strip()


def item_qty(item):
    for key in ('Estimation_Quantity', '数量', 'quantity'):
        if key in item:
            return number(item[key])
    return None


def item_path(item):
    return [str(p) for p in (item.get('层级路径') or [item.get('层级') or '']) if p]


def head(name):
    """去掉章节编号、冒号后说明、规格和末尾括注，得到物资主名。"""
    raw = LABEL.sub('', str(name or ''))
    raw = norm(re.split(r'[　：:“"]', raw)[0])
    raw = re.sub(r'\s*\([^()]*\)$', '', raw)
    return raw.strip(' ,、')


def full_text(item):
    return norm(f'{item_name(item)} {item_spec(item)}')


def brief(item):
    return f"{head(item_name(item))[:24]} {item.get('Estimation_Quantity', item.get('数量', ''))}{item_unit(item)}".strip()


def unit_kind(unit):
    u = norm(unit)
    if u in MASS_UNITS:
        return 'mass', MASS_UNITS[u]
    if u in LENGTH_UNITS:
        return 'length', LENGTH_UNITS[u]
    if unit and unit in COUNT_UNITS:
        return 'count', 1.0
    return '', 1.0


def similarity(a, b):
    """物资主名相似度：一方包含另一方时按长度比给分，否则取二元字 Jaccard。"""
    a, b = re.sub(r'[\s()（）,，、]', '', head(a)), re.sub(r'[\s()（）,，、]', '', head(b))
    if not a or not b:
        return 0.0
    short, long_ = sorted((a, b), key=len)
    if short in long_:
        return 0.6 + 0.4 * len(short) / len(long_)
    grams = lambda s: {s[i:i + 2] for i in range(max(1, len(s) - 1))}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb)


# ------------------------------------------------------------------ 属性抽取（输入为 norm 后文本）
def _num(text):
    return f'{float(text):g}'


def cores_section(t):
    m = re.search(r'(?<![\dX.])([1-9]\d?)\s*X\s*(\d+(?:\.\d+)?)(?:\s*\+\s*([1-9])\s*X\s*(\d+(?:\.\d+)?))?(?!\s*X\s*\d)', t)
    if m:
        return f'{int(m[1])}X{_num(m[2])}' + (f'+{m[3]}X{_num(m[4])}' if m[3] else '')
    m = re.search(r'([单一双二两三四五])芯\s*(\d+(?:\.\d+)?)', t)
    return f'{CN_DIGITS[m[1]]}X{_num(m[2])}' if m else ''


def cable_armour(t):
    m = re.search(r'(YJLV|YJV|YJY|VLV|VV|KYJVP?|KVVP|KVV)(?:(\d{2})|2[/-](\d{2})|2)?', t)
    if not m:
        return ''
    return m[2] or m[3] or '无铠装'


def fire_class(t):
    m = re.search(r'(WDZ[A-D]?N?|ZR[A-C]?N?|Z[A-D]N?|NH)-?(?=YJ|VV|KV)', t)
    return m[1] if m else ''


def busbar_size(t):
    m = re.search(r'(?<![\dX.])(\d{2,3}(?:\.\d+)?)\s*(?:MM)?\s*X\s*(\d{1,2}(?:\.\d+)?)(?:\s*MM2?)?(?!\s*X\s*\d)(?!\d)', t)
    return f'{_num(m[1])}X{_num(m[2])}' if m else ''


def dn(t):
    m = re.search(r'(?:DN|Φ|∅)\s*(\d{2,3})(?!\s*X|\d)', t)
    return m[1] if m else ''


def angle_size(t):
    m = re.search(r'(?:∠|L)\s*(\d{2,3})\s*X\s*(\d{1,2})(?!\d)', t) or re.search(r'(?<!\d)(\d{2,3})X\1X(\d{1,2})(?!\d)', t)
    return f'{m[1]}X{m[2]}' if m else ''


def conductor_size(t):
    m = re.search(r'(?:JL/[A-Z0-9]+|LGJ[A-Z]*|JKLG?YJ)\s*-?\s*(\d{2,4})(?:\s*/\s*(\d{1,3}))?', t)
    return (m[1] + (f'/{m[2]}' if m[2] else '')) if m else ''


def conductor_al(t):
    m = re.search(r'(?<![\d.])(\d{2,3})\s*/\s*\d{1,3}', t)
    return m[1] if m else ''


def round_section(t):
    m = re.search(r'(?<![\dX.])(\d+(?:\.\d+)?)\s*MM2', t)
    return _num(m[1]) if m else ''


def support_type(t):
    m = re.search(r'E-(\d)-(\d{3})', t)
    return f'E-{m[1]}-{m[2]}' if m else ''


EXTRACTORS = dict(cores_section=cores_section, cable_armour=cable_armour, fire_class=fire_class,
                  busbar_size=busbar_size, dn=dn, angle_size=angle_size, conductor_size=conductor_size,
                  conductor_al=conductor_al, round_section=round_section, support_type=support_type)


class DomainKnowledge:
    def __init__(self, folder=DEFAULT_DIR):
        folder = Path(folder)
        self.data = {name: json.loads((folder / f'{name}.json').read_text(encoding='utf-8'))
                     for name in ('sources', 'categories', 'parameters', 'compositions')}
        self.sources = {s['id']: s for s in self.data['sources']['sources']}
        cats = self.data['categories']
        self.taxonomy = {k: cats.get(k) for k in ('id', 'source', 'evidence', 'status', 'review')}
        self.categories = {c['id']: c for c in cats['categories']}
        self.kinds = cats['kinds']
        self.params = self.data['parameters']
        self.comp = self.data['compositions']
        self._patterns = [(c, [re.compile(p) for p in c['match']]) for c in cats['categories'] if c.get('match')]

    # -------------------------------------------------------------- 校验
    def validate(self):
        """返回知识库的结构问题列表；空列表表示通过。"""
        problems, seen = [], set()

        def entry(e, where):
            for key in ('id', 'source', 'evidence', 'status'):
                if not e.get(key):
                    problems.append(f'{where}：缺少 {key}')
            if e.get('id') in seen:
                problems.append(f'{where}：编号重复 {e.get("id")}')
            seen.add(e.get('id'))
            if e.get('source') and e['source'] not in self.sources:
                problems.append(f'{where}：来源 {e["source"]} 未登记')
            if e.get('status') and e['status'] not in STATUSES:
                problems.append(f'{where}：状态 {e["status"]} 无效')
            if e.get('evidence') and e['evidence'] not in EVIDENCE:
                problems.append(f'{where}：证据级别 {e["evidence"]} 无效')
            review = e.get('review') or {}
            if e.get('status') == '已审核' and not all(review.get(k) for k in ('by', 'date', 'basis')):
                problems.append(f'{where}：已审核条目须记录审核人、日期和依据')

        entry(self.taxonomy, '品类本体')
        for c in self.categories.values():
            if c.get('parent') and c['parent'] not in self.categories:
                problems.append(f'品类 {c["id"]}：上级 {c["parent"]} 不存在')
            for attr in c.get('identity', []) + c.get('hints', []):
                if attr not in EXTRACTORS and attr not in PARAM_ATTRS:
                    problems.append(f'品类 {c["id"]}：属性 {attr} 没有抽取器')
            for eq in c.get('equivalents', []):
                entry(eq, f'品类 {c["id"]} 等价规则')
            for p in c.get('match', []):
                try:
                    re.compile(p)
                except re.error as exc:
                    problems.append(f'品类 {c["id"]}：正则 {p} 无效（{exc}）')
        for k in self.kinds:
            entry(k, f'计量性质 {k.get("id")}')
        p = self.params
        for d in p['densities']:
            entry(d, f'密度 {d.get("id")}')
            if d['range'][0] > d['range'][1]:
                problems.append(f'密度 {d["id"]}：区间下限大于上限')
        for key in ('steel_pipe', 'pipe_size_rule', 'angle_steel', 'cable_support', 'system_voltages', 'cable_voltage_systems', 'overhead_conductors'):
            entry(p[key], f'参数 {key}')
        for size, (_od, wall, thick) in p['steel_pipe']['dimensions'].items():
            for w in (wall, thick):
                for coat in ('300', '500'):
                    if _wall_key(w) not in p['steel_pipe']['galvanize_c'][coat]:
                        problems.append(f'钢管 DN{size}：壁厚 {w} 缺少镀锌系数（{coat}g/m²）')
        for size, (r, kg) in p['angle_steel']['sizes'].items():
            calc = angle_formula(size, r)
            if abs(calc - kg) > 0.005:
                problems.append(f'角钢 {size}：表值 {kg} 与公式 {calc:.3f} 不符')
        for key in ('declared', 'accessories'):
            entry(self.comp[key], f'成套规则 {key}')
        for t in self.comp['templates']:
            entry(t, f'成套模板 {t.get("id")}')
        return problems

    # -------------------------------------------------------------- 品类与计量性质
    def classify(self, item):
        """按顺序匹配品类：先用物资主名，未命中再用名称+规格。"""
        for text in (head(item_name(item)), full_text(item)):
            for node, patterns in self._patterns:
                if any(p.search(text) for p in patterns):
                    return node
        return None

    def category_path(self, item):
        node = self.classify(item)
        return self.lineage(node) if node else []

    def lineage(self, node):
        names = []
        while node:
            names.append(node['name'])
            node = self.categories.get(node.get('parent'))
        return list(reversed(names))

    def kind(self, item):
        """非实物或非新购条目返回计量性质知识；普通实物返回 None。"""
        name, path, unit = head(item_name(item)), ' '.join(item_path(item)), item_unit(item)
        for k in self.kinds:
            if k.get('match_name') and re.search(k['match_name'], name):
                return k
            if k.get('match_path') and re.search(k['match_path'], path):
                return k
            if unit and unit in k.get('units', []):
                return k
        return None

    # -------------------------------------------------------------- 身份属性
    def attribute(self, attr, item):
        t = full_text(item)
        if attr == 'voltage_system':
            return self.voltage_system(t)
        if attr == 'post_insulator':
            return self.post_insulator(t)
        return EXTRACTORS[attr](t)

    def system_voltage(self, value):
        """按 GB/T 156 把标称电压或设备最高电压归到系统标称电压。"""
        for nominal, um in self.params['system_voltages']['pairs']:
            if abs(value - nominal) < 1e-6 or abs(value - um) < 1e-6:
                return nominal
        return None

    def post_insulator(self, t):
        """支柱绝缘子“ZSW-40.5/8”：电压归到系统电压等级，ZSW-35/8 与 ZSW-40.5/8 同为 35kV 级。"""
        m = re.search(r'Z[A-Z]{1,3}-?(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)', t)
        if not m:
            return ''
        system = self.system_voltage(float(m[1]))
        return f'{system:g}kV级/{_num(m[2])}kN' if system else f'{float(m[1]):g}kV/{_num(m[2])}kN'

    def voltage_system(self, t):
        m = re.search(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(?=KV|-|\s|$)', t)
        value = number(m[2]) if m and number(m[2]) <= 40.5 else None
        if value is None:
            m = re.search(r'(?<![\d./])(\d+(?:\.\d+)?)\s*KV', t)
            value = number(m[1]) if m else None
        if value is None:
            return ''
        for system in self.params['cable_voltage_systems']['systems']:
            if any(abs(value - u) < 1e-6 for u in system['U']):
                return system['name']
        return f'{value:g}kV'

    def compare(self, a, b):
        """比较两条物资的身份属性。severity：冲突=不是同一物资；提示=写法或口径差异。"""
        ca, cb = self.classify(a), self.classify(b)
        findings = []
        if ca and cb and ca['id'] != cb['id']:
            findings.append(self._finding('category', ca['name'], cb['name'], '提示', '两侧识别为不同品类，请核对是否同一物资', self.taxonomy))
        node = ca or cb
        if not node:
            return findings
        identity = node.get('identity', [])
        for attr in identity + node.get('hints', []):
            va, vb = self.attribute(attr, a), self.attribute(attr, b)
            if not va or not vb or va == vb:
                continue
            eq = self._equivalent(node, attr, va, vb, a, b)
            if eq:
                findings.append(self._finding(attr, va, vb, '提示', eq['note'], eq))
            elif attr in identity:
                findings.append(self._finding(attr, va, vb, '冲突', f'{node["name"]}的{ATTR_LABELS[attr]}不同，不是同一物资', self.taxonomy))
            else:
                findings.append(self._finding(attr, va, vb, '提示', f'{ATTR_LABELS[attr]}写法不同', self.taxonomy))
        return findings

    def _equivalent(self, node, attr, va, vb, a, b):
        for eq in node.get('equivalents', []):
            if eq['attr'] != attr or {va, vb} != set(eq['values']):
                continue
            cores = {int(v.split('X')[0]) for v in (self.attribute('cores_section', a), self.attribute('cores_section', b)) if v}
            if 'when_cores' not in eq or cores == {eq['when_cores']}:
                return eq
        return None

    @staticmethod
    def _finding(attr, va, vb, severity, note, entry):
        return dict(attr=attr, label=ATTR_LABELS.get(attr, attr), a=va, b=vb, severity=severity, note=note,
                    knowledge=[_ref(entry)], decisive=entry.get('status') == '已审核')

    # -------------------------------------------------------------- 计量参数
    def linear_mass(self, item):
        """线材单位长度重量区间(kg/m)，附逐步算式和所用知识；无适用知识返回 None。"""
        node = self.classify(item)
        cid = node['id'] if node else ''
        t = full_text(item)
        if cid == 'steel.pipe':
            return self._pipe_mass(t)
        if cid in ('cond.busbar', 'steel.flat'):
            size = busbar_size(t)
            if not size:
                return None
            w, th = (float(x) for x in size.split('X'))
            dens = self._density('钢' if cid == 'steel.flat' else t)
            if not dens:
                return _gap('未识别母排材质，无法确定密度', [])
            area = w * th
            lo, hi = (area * d / 1e6 for d in dens['range'])
            return _mass((lo, hi), [f'截面{size}mm，面积{fmt(area)}mm²',
                                    f'{dens["material"]}密度{_span(dens["range"])}kg/m³（{dens["source"]} {dens["location"]}）',
                                    f'单位重量={fmt(area)}×密度÷1000000={_span((lo, hi))}kg/m'], [dens], area=area, material=dens['material'])
        if cid == 'steel.angle':
            size = angle_size(t)
            table = self.params['angle_steel']
            if size not in table['sizes']:
                return _gap(f'角钢{size or "规格"}不在GB/T 706-2016参数表中', [table])
            kg = table['sizes'][size][1]
            return _mass((kg, kg), [f'∠{size.replace("X", "×")}理论重量{kg}kg/m（GB/T 706-2016 附录A）'], [table])
        if cid == 'cond.overhead':
            return self._conductor_mass(t)
        if cid == 'cond.flex':
            area, dens = number(round_section(t)), self._density(t)
            if not area or not dens:
                return None
            lo, hi = (area * d / 1e6 for d in dens['range'])
            return _mass((lo, hi), [f'标称截面{fmt(area)}mm²，{dens["material"]}密度{_span(dens["range"])}kg/m³（{dens["source"]} {dens["location"]}）',
                                    f'单位重量={fmt(area)}×密度÷1000000={_span((lo, hi))}kg/m（按标称截面计，未计绞制增量）'], [dens], area=area, material=dens['material'])
        return None

    def _density(self, text):
        for d in self.params['densities']:
            if re.search(d['match'], norm(text)):
                return d
        return None

    def _pipe_mass(self, t):
        table, rule = self.params['steel_pipe'], self.params['pipe_size_rule']
        size = dn(t)
        if not size:
            return None
        if size not in table['dimensions']:
            return _gap(f'GB/T 3091-2015 附录A未列 DN{size}', [table, rule])
        od, wall, thick = table['dimensions'][size]
        wall = thick if '加厚' in t else wall
        black = 0.0246615 * (od - wall) * wall
        steps = [f'φ{size}按公称口径DN{size}理解（{rule["id"]}）',
                 f'GB/T 3091-2015 表A.1：外径{od}mm，{"加厚" if "加厚" in t else "普通"}钢管壁厚{wall}mm',
                 f'未镀锌理论重量 W=0.0246615×({od}−{wall})×{wall}={black:.4f}kg/m（式1）']
        if '镀锌' not in t:
            return _mass((black, black), steps, [table, rule], black=black)
        coats = ['300'] if '300G' in t else ['500'] if '500G' in t else ['300', '500']
        cs = [table['galvanize_c'][c][_wall_key(wall)] for c in coats]
        lo, hi = black * min(cs), black * max(cs)
        steps.append(f'镀锌层重量系数 c={_span(cs)}（{"、".join("表3" if c == "300" else "表4" for c in coats)}，'
                     f'{"/".join(coats)}g/m²），W′=cW={_span((lo, hi))}kg/m（式2）')
        return _mass((lo, hi), steps, [table, rule], black=black)

    def _conductor_mass(self, t):
        table = self.params['overhead_conductors']
        for model, spec in table['models'].items():
            if norm(model) in t:
                core = table['core_densities'][spec['core']]
                al_inc, core_inc = table['aluminium_lay_increment'], table['core_lay_increment']
                al = [spec['aluminium_area'] * table['aluminium_density'] / 1e6 * (1 + i) for i in al_inc]
                cr = [spec['core_area'] * d / 1e6 * (1 + i) for d, i in zip(core['range'], core_inc)]
                lo, hi = al[0] + cr[0], al[1] + cr[1]
                return _mass((lo, hi), [f'{model} 结构：{spec["structure"]}，铝{spec["aluminium_area"]}mm²、{spec["core"]}{spec["core_area"]}mm²',
                                        f'铝部分={spec["aluminium_area"]}×{table["aluminium_density"]}÷1000000×(1+绞制增量{_span(al_inc)})={_span(al)}kg/m',
                                        f'芯部分={spec["core_area"]}×{_span(core["range"])}÷1000000×(1+绞制增量{_span(core_inc)})={_span(cr)}kg/m',
                                        f'单位重量={_span((lo, hi))}kg/m（公式推算区间，待以GB/T 1179-2017原表替换）'], [table])
        return _gap('导线型号不在参数表中，需补充GB/T 1179-2017原表数据', [table])

    def piece_mass(self, item):
        """电缆沟支架等按规格写明的型钢构成计算单套重量(kg)。"""
        t = full_text(item)
        rule, angles = self.params['cable_support'], self.params['angle_steel']
        m = re.search(rule['pattern'], t)
        if not m:
            return None
        layers, length_mm = int(m[1]), int(m[2])
        parts = {kind: (f'{b}X{th}', float(length)) for b, th, kind, length in
                 re.findall(r'(\d{2,3})X(\d{1,2})\s*(支架|格架)\s*=\s*(\d+(?:\.\d+)?)\s*M', t)}
        if '格架' not in parts:
            grid = re.search(r'格架\s*=\s*(\d+(?:\.\d+)?)\s*M', t)
            return dict(_gap(f'E-{layers}-{length_mm} 规格未写明格架型钢，无法计算单套重量', [rule]),
                        grid=(layers, float(grid[1])) if grid else None)
        steps, total = [f'E-{layers}-{length_mm} 按“{rule["meaning"]}”理解：{layers}层格架', ], 0.0
        for kind, count in (('支架', 1), ('格架', layers)):
            if kind not in parts:
                continue
            size, length = parts[kind]
            if size not in angles['sizes']:
                return _gap(f'{kind}角钢∠{size}不在GB/T 706-2016参数表中', [rule, angles])
            kg = angles['sizes'][size][1]
            total += count * length * kg
            steps.append(f'{kind} ∠{size.replace("X", "×")} {kg}kg/m×{length:g}m×{count}={count * length * kg:.4f}kg')
        steps.append(f'单套重量={total:.4f}kg')
        return _mass((total, total), steps, [rule, angles], parts=parts, layers=layers)

    def quantity_check(self, a, b):
        """一侧按重量、另一侧按长度或件数计量时，按知识参数换算后核对数量；无适用知识返回 None。"""
        (ka, fa), (kb, fb) = unit_kind(item_unit(a)), unit_kind(item_unit(b))
        if 'mass' not in (ka, kb) or {ka, kb} not in ({'mass', 'length'}, {'mass', 'count'}):
            return None
        mass_item, other, mf, of = (a, b, fa, fb) if ka == 'mass' else (b, a, fb, fa)
        per_length = {ka, kb} == {'mass', 'length'}
        lookup = self.linear_mass if per_length else self.piece_mass
        basis = lookup(other) or lookup(mass_item)
        if basis is None:
            return None
        qm, qo = item_qty(mass_item), item_qty(other)
        if basis.get('gap'):
            diagnostic = ''
            if basis.get('grid') and qm and qo:
                layers, length = basis['grid']
                per_m = qm * mf / qo / (layers * length)
                close = [k.replace('X', '×') for k, (_r, kg) in self.params['angle_steel']['sizes'].items() if abs(kg - per_m) / kg <= TOLERANCE]
                diagnostic = (f'重量侧折合{qm * mf / qo:.4f}kg/{item_unit(other)}，按{layers}层×{length:g}m格架反推单重{per_m:.3f}kg/m'
                              + (f'，相当于∠{"、∠".join(close)}，请按设计图集核实' if close else ''))
            return dict(verdict='无法核实', gap=basis['gap'], steps=[basis['gap']], diagnostic=diagnostic,
                        knowledge=basis['knowledge'], decisive=all(k['status'] == '已审核' for k in basis['knowledge']))
        if not qm or not qo:
            return None
        amount = qo * of
        observed = round(qm * mf / 1000.0, 3)
        lo, hi = (round(amount * v / 1000.0, 3) for v in basis['range'])
        unit_word = 'm' if per_length else item_unit(other)
        steps = list(basis['steps'])
        steps.append(f'换算重量={fmt(amount)}{unit_word}×{_span(basis["range"])}kg/{unit_word}={_span((lo, hi))}t（吨保留3位）')
        if lo * (1 - TOLERANCE) <= observed <= hi * (1 + TOLERANCE):
            ratio, verdict = 0.0, '一致'
            steps.append(f'对比重量侧{observed:.3f}t，在换算区间允许误差{TOLERANCE * 100:g}%以内，数量一致')
        else:
            nearest = lo if observed < lo else hi
            ratio = abs(observed - nearest) / max(observed, nearest)
            verdict = '不一致'
            steps.append(f'对比重量侧{observed:.3f}t，与换算区间最近端{nearest:.3f}t相差{ratio * 100:.2f}%，超过允许误差{TOLERANCE * 100:g}%')
        implied = qm * mf / amount
        diagnostic = f'重量侧折合{implied:.4f}kg/{unit_word}'
        if basis.get('black'):
            diagnostic += f'，为未镀锌理论重量的{implied / basis["black"]:.3f}倍（标准镀锌系数见上）'
        if not per_length and verdict == '不一致':
            diagnostic += self._support_diagnostic(implied, basis)
        if basis.get('area') and verdict == '不一致':
            diagnostic += self._density_diagnostic(implied, basis)
        return dict(verdict=verdict, ratio=ratio, observed_t=observed, expected_t=(lo, hi), steps=steps, diagnostic=diagnostic,
                    knowledge=basis['knowledge'], decisive=all(k['status'] == '已审核' for k in basis['knowledge']))

    def _density_diagnostic(self, implied, basis):
        """反推重量侧隐含的材料密度，若与另一种材料吻合则提示可能错用了材料（只作核查线索）。"""
        density = implied / basis['area'] * 1e6
        for d in self.params['densities']:
            lo, hi = d['range']
            if d['material'] != basis['material'] and lo * (1 - TOLERANCE) <= density <= hi * (1 + TOLERANCE):
                return f'；按截面反推密度{density:.0f}kg/m³，与{d["material"]}的密度吻合，疑似重量侧按{d["material"]}计算，请核对'
        return f'；按截面反推密度{density:.0f}kg/m³'

    def _support_diagnostic(self, implied, basis):
        """反推重量侧隐含的格架单重，并列出与之吻合的标准角钢（只作核查线索）。"""
        parts, layers = basis['parts'], basis['layers']
        size, length = parts['格架']
        fixed = 0.0
        if '支架' in parts:
            s, l = parts['支架']
            fixed = l * self.params['angle_steel']['sizes'][s][1]
        per_m = (implied - fixed) / (layers * length)
        close = [k.replace('X', '×') for k, (_r, kg) in self.params['angle_steel']['sizes'].items() if abs(kg - per_m) / kg <= TOLERANCE]
        text = f'；按规格的支架重量反推，格架隐含单重{per_m:.3f}kg/m（规格写∠{size.replace("X", "×")}）'
        return text + (f'，与∠{"、∠".join(close)}吻合，疑似重量侧按该规格计列，请核对' if close else '')

    # -------------------------------------------------------------- 成套组成
    def declared_components(self, rows, parent_index, columns):
        """原表中成套项之后列出的组成件。rows 为原表行列表，columns 为 1 起的列号映射（name/unit/qty/supply）。"""
        rule = self.comp['declared']
        cell = lambda row, key: str(row[columns[key] - 1] or '').strip() if columns.get(key) and columns[key] <= len(row) else ''
        parent = rows[parent_index]
        if not re.search(rule['parent_marker'], cell(parent, 'name')):
            return []
        found = []
        for index in range(parent_index + 1, len(rows)):
            row = rows[index]
            name, unit, qty, supply = (cell(row, k) for k in ('name', 'unit', 'qty', 'supply'))
            if not any(str(v or '').strip() for v in row) or supply or re.search(rule['quota_code'], name):
                break
            if re.search(rule['stop_marker'], name) or not unit or number(qty) is None:
                break
            if any(cell(row, k) for k in ('price', 'equipment_price', 'material_price')):
                break  # 有单价的行是独立计价物资，不是组成件
            label = re.match(r'^\s*(\d+(?:\.\d+)*)', name)
            found.append(dict(name=LABEL.sub('', name).strip(), unit=unit, quantity=qty, row=index + 1,
                              label=label[1] if label else ''))
        return found

    def match_component(self, item, components, threshold=0.5):
        """在组成件中找与该物资同名、单位相容的一项；返回 (组成件, 相似度, 数量是否相同)。"""
        best = None
        for comp in components:
            score = similarity(item_name(item), comp['name'])
            if score < threshold or not _units_compatible(item_unit(item), comp['unit']):
                continue
            if not best or score > best[1]:
                best = (comp, score, number(comp['quantity']) == item_qty(item))
        return best

    def template_for(self, item):
        text = head(item_name(item))
        return next((t for t in self.comp['templates'] if re.search(t['match'], text)), None)

    def template_component(self, item, template):
        text = head(item_name(item))
        return next((c for c in template['components'] if re.search(c['match'], text)), None)

    def declared_accessories(self, item):
        """规格中“配/含/带/附”后写明的附件名称。"""
        marker = self.comp['accessories']['marker']
        found = []
        for phrase in re.findall(rf'(?:{marker})([^,，;；。]+)', norm(f'{item_name(item)} {item_spec(item)}')):
            found.extend(p.strip() for p in re.split(r'及|和|、|与', phrase) if len(p.strip()) >= 2)
        return found

    def accessory_of(self, item, holder):
        """item 是否为 holder 规格中声明已包含的附件。"""
        name = re.sub(r'\s', '', head(item_name(item)))
        for phrase in self.declared_accessories(holder):
            phrase = re.sub(r'\s', '', phrase)
            if len(phrase) >= 3 and (phrase in name or name in phrase):
                return phrase
        return ''

    # -------------------------------------------------------------- 配对与单侧解释（供清洗、匹配、评审共用）
    def conflicts(self, a, b):
        """已审核知识判定的身份冲突：存在即不是同一物资。"""
        return [f for f in self.compare(a, b) if f['severity'] == '冲突' and f['decisive']]

    def resolve_conflicts(self, pairs, src_by, tgt_by, pool_limit=8, group_limit=6):
        """对存在身份冲突的配对，在同品类的冲突配对及空闲清册项之间重新分配，使冲突全部消除。

        pairs 为 {造价序号: 清册序号}，原地修改；返回改动列表 [(造价序号, 原清册序号, 新清册序号)]。
        找不到无冲突的分配时保持原配对，由评审标为不一致。
        """
        conflicted = [s for s, t in pairs.items() if s in src_by and t in tgt_by and self.conflicts(src_by[s], tgt_by[t])]
        groups = defaultdict(list)
        for s in conflicted:
            node = self.classify(src_by[s])
            groups[node['id'] if node else ''].append(s)
        changes = []
        for cid, srcs in groups.items():
            if not cid or len(srcs) > group_limit:
                continue
            used = set(pairs.values())
            current = [pairs[s] for s in srcs]
            free = [t for t, item in tgt_by.items() if t not in used and (self.classify(item) or {}).get('id') == cid]
            pool = current + free[:max(0, pool_limit - len(current))]
            bad = {(s, t): bool(self.conflicts(src_by[s], tgt_by[t])) for s in srcs for t in pool}
            best = None
            for perm in itertools.permutations(pool, len(srcs)):
                if any(bad[(s, t)] for s, t in zip(srcs, perm)):
                    continue
                score = (sum(same_quantity(src_by[s], tgt_by[t]) for s, t in zip(srcs, perm)), sum(t == c for t, c in zip(perm, current)))
                if best is None or score > best[0]:
                    best = (score, perm)
            if best:
                for s, old, new in zip(srcs, current, best[1]):
                    if old != new:
                        pairs[s] = new
                        changes.append((s, old, new))
        return changes

    def explain_unpaired(self, item, side, sources, targets):
        """解释单侧条目：计量性质 > 原表成套组成 > 规格声明附件 > 成套模板。

        side 为 'source'（造价单侧）或 'target'（清册单侧）。造价条目的成套组成件在
        “成套组成”字段中（清洗时从原表抽取）。返回 dict(type, band, finding, knowledge, decisive, related) 或 None。
        """
        kind = self.kind(item)
        if kind:
            return _explain('计量性质', '不参与核对', f'{kind["name"]}：{kind["note"]}', [kind])
        if side == 'target':
            best = None
            for src in sources:
                hit = self.match_component(item, src.get('成套组成') or [])
                if hit and (not best or hit[1] > best[1][1]):
                    best = (src, hit)
            if best:
                src, (comp, _score, qty_ok) = best
                listed = f'造价“{brief(src)}”原表第{comp["row"]}行列有组成件“{comp["name"]} {comp["quantity"]}{comp["unit"]}”'
                return _explain('成套组成（原表）', '基本一致' if qty_ok else '部分一致',
                                f'属{listed}，数量一致，按一对多包含关系核对' if qty_ok else
                                f'数量不一致：{listed}，与清册数量不同', [self.comp['declared']], src.get('序号'))
        holders = [(h, p) for h in (sources if side == 'target' else targets) if (p := self.accessory_of(item, h))]
        if holders:
            # 多条物资都写明同一附件时，优先数量相同、所在章节最接近的一条
            section = lambda x: re.sub(r'\d+(?:\.\d+)?\s*KV', '', norm(' '.join(item_path(x))))  # 电压等级不区分章节
            holder, phrase = max(holders, key=lambda x: (same_quantity(item, x[0]), similarity(section(item), section(x[0]))))
            return _explain('规格声明附件', '基本一致', f'{"造价" if side == "target" else "清册"}“{brief(holder)}”规格已写明“{phrase}”，'
                            f'该附件包含在该条物资中', [self.comp['accessories']], holder.get('序号'))
        if side == 'target':
            candidates = [(src, tp, c) for src in sources if (tp := self.template_for(src)) and (c := self.template_component(item, tp))]
            if candidates:
                src, tp, comp = max(candidates, key=lambda x: similarity(item_name(item), item_name(x[0])))
                return _explain('成套组成（模板）', '待复核', f'按“{tp["name"]}”成套组成，疑似为造价“{brief(src)}”中的{comp["name"]}，请确认是否包含',
                                [tp], src.get('序号'))
        return None

    # -------------------------------------------------------------- 图谱导出
    def graph(self):
        """导出节点与边，供可视化或审核界面使用。"""
        nodes, edges = [], []
        for s in self.sources.values():
            nodes.append(dict(id='source:' + s['id'], label=s['id'], type='来源'))
        for c in self.categories.values():
            nodes.append(dict(id=c['id'], label=c['name'], type='品类'))
            if c.get('parent'):
                edges.append(dict(source=c['id'], target=c['parent'], type='属于'))
            for attr in c.get('identity', []):
                edges.append(dict(source=c['id'], target='attr:' + attr, type='身份属性'))
        for attr, label in ATTR_LABELS.items():
            nodes.append(dict(id='attr:' + attr, label=label, type='属性'))
        for k in self.kinds:
            nodes.append(dict(id=k['id'], label=k['name'], type='计量性质', status=k['status']))
            edges.append(dict(source=k['id'], target='source:' + k['source'], type='出处'))
        for t in self.comp['templates']:
            nodes.append(dict(id=t['id'], label=t['name'], type='成套模板', status=t['status']))
            edges.append(dict(source=t['id'], target='source:' + t['source'], type='出处'))
            for i, comp in enumerate(t['components']):
                cid = f'{t["id"]}.{i}'
                nodes.append(dict(id=cid, label=comp['name'], type='组成件'))
                edges.append(dict(source=cid, target=t['id'], type='组成'))
        p = self.params
        for e in p['densities'] + [p[k] for k in ('steel_pipe', 'angle_steel', 'cable_support', 'overhead_conductors', 'cable_voltage_systems', 'pipe_size_rule')]:
            nodes.append(dict(id=e['id'], label=e.get('material') or e.get('location') or e.get('note', '')[:20], type='计量参数', status=e['status']))
            edges.append(dict(source=e['id'], target='source:' + e['source'], type='出处'))
        return dict(nodes=nodes, edges=edges)


# ------------------------------------------------------------------ 辅助
def angle_formula(size, r):
    b, t = (float(x) for x in size.split('X'))
    r1 = t / 3
    return (t * (2 * b - t) + 0.2146 * (r * r - 2 * r1 * r1)) * 7.85 / 1000


def _wall_key(wall):
    return f'{wall:.1f}' if wall < 10 else f'{wall:g}'


def _span(values):
    lo, hi = min(values), max(values)
    return fmt(lo) if abs(hi - lo) < 1e-9 else f'{fmt(lo)}～{fmt(hi)}'


def _ref(entry):
    return dict(id=entry['id'], status=entry['status'], evidence=entry['evidence'], source=entry['source'],
                reviewer=(entry.get('review') or {}).get('by', ''))


def _explain(kind, band, finding, entries, related=None):
    refs = [_ref(e) for e in entries]
    return dict(type=kind, band=band, finding=finding, knowledge=refs, related=related,
                decisive=all(r['status'] == '已审核' for r in refs))


def same_quantity(a, b):
    """同量纲（或均为计数单位）时数量是否相差不超过1%。"""
    (ka, fa), (kb, fb) = unit_kind(item_unit(a)), unit_kind(item_unit(b))
    qa, qb = item_qty(a), item_qty(b)
    if qa is None or qb is None or not (item_unit(a) == item_unit(b) or (ka and ka == kb)):
        return False
    qa, qb = qa * fa, qb * fb
    return abs(qa - qb) <= TOLERANCE * max(abs(qa), abs(qb), 1e-9)


def _mass(rng, steps, entries, **extra):
    return dict(range=rng, steps=steps, knowledge=[_ref(e) for e in entries], **extra)


def _gap(message, entries):
    return dict(gap=message, knowledge=[_ref(e) for e in entries])


def _units_compatible(a, b):
    return a == b or (a in COUNT_UNITS and b in COUNT_UNITS)
