"""发送在线模型前的本地脱敏：清洗后的物资数据只有经用户同意、脱敏后才离开本机。

- 不发送原表位置：文件名、工作表、行号（_source）及汇总表追溯证据。
- 公司、设计院等单位名称替换为“某单位”；电话、手机号、邮箱、身份证号替换为占位符。
- 站名（如“220kV瑞丽变”，连同同名线路“瑞丽线”）替换为一致的代号“站点A”，同一名称两侧同一代号，
  配对与对侧判断不受影响；评审结果回到本机后再换回原名。
全部是规则代码，不调用模型。
"""
import copy
import re

ORG = re.compile(r'[一-龥]{2,30}?(?:股份有限公司|有限责任公司|有限公司|设计研究院|研究院|设计院|供电局|供电公司|电力局)')
CONTACTS = [
    (re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'), '[手机号]'),
    (re.compile(r'(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)'), '[电话]'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), '[邮箱]'),
    (re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'), '[证件号]'),
]
# 带电压等级的站名：“220kV瑞丽变”“35kV鲁纳变电站”；设备词（主变、站用变等）不是站名
STATION = re.compile(r'(\d+(?:\.\d+)?kV)\s*([一-龥]{2,4}?)(变电站|开关站|换流站|变|站)(?![压电流频])', re.I)
NOT_PLACE = ('变', '站用', '所用', '主', '接地', '箱式', '消弧', '联络', '降压', '升压', '配电', '隔离', '电力', '整流', '调压',
             '电压', '电流', '互感', '厂用', '备用', '本', '对侧', '扩建', '新建', '一次', '二次', '通信', '光缆', '电缆',
             '导', '地', '母', '引下', '接', '出', '进', '馈', '中性', '避雷', '屏蔽', '控制', '信号', '电源', '网', '双绞',
             '同轴', '跳', '连', '引', '架空', '低压', '高压', '中压', '照明', '动力', '火', '零', '相', '专', '直')
TEXT_KEYS = ('Estimation_Name', '设备材料名称', '型号及规范', '层级', '备注', '子项名称', '名称')
DROP_KEYS = ('summary_evidence', 'price_warning')


def _letters(n):
    out = ''
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _texts(record):
    for key in TEXT_KEYS:
        if isinstance(record.get(key), str):
            yield record[key]
    for item in record.get('层级路径') or []:
        if isinstance(item, str):
            yield item
    for item in record.get('成套组成') or []:
        if isinstance(item, dict):
            yield from _texts(item)


def plan(sources, targets):
    """扫描两侧文本，建立站名、线路名代号表并统计各类敏感信息。"""
    stations, orgs, contacts = {}, set(), 0
    for record in list(sources) + list(targets):
        for text in _texts(record):
            for m in STATION.finditer(text):
                core = m.group(2)
                if '变' not in core and not any(core.startswith(w) for w in NOT_PLACE):
                    stations.setdefault(core, '站点' + _letters(len(stations)))
            orgs.update(ORG.findall(text))
            contacts += sum(len(p.findall(text)) for p, _ in CONTACTS)
    return dict(stations=stations, orgs=sorted(orgs), contacts=contacts)


def mask_text(text, mapping):
    for pattern, label in CONTACTS:
        text = pattern.sub(label, text)
    text = ORG.sub('某单位', text)
    names = mapping['stations']
    for core in sorted(names, key=len, reverse=True):
        text = text.replace(core, names[core])
    return text


def apply(records, mapping):
    """返回脱敏副本：去掉原表位置与追溯证据，文本字段按代号表替换。原记录不变。"""
    out = []
    for record in records:
        item = {k: copy.deepcopy(v) for k, v in record.items() if not k.startswith('_') and k not in DROP_KEYS}
        for key in TEXT_KEYS:
            if isinstance(item.get(key), str):
                item[key] = mask_text(item[key], mapping)
        if isinstance(item.get('层级路径'), list):
            item['层级路径'] = [mask_text(x, mapping) if isinstance(x, str) else x for x in item['层级路径']]
        if isinstance(item.get('成套组成'), list):
            item['成套组成'] = apply(item['成套组成'], mapping) if all(isinstance(x, dict) for x in item['成套组成']) else item['成套组成']
        out.append(item)
    return out


def mask_value(value, mapping):
    """嵌套结构中的文本逐项脱敏（如配对依据）。"""
    if isinstance(value, str):
        return mask_text(value, mapping)
    if isinstance(value, list):
        return [mask_value(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: mask_value(v, mapping) for k, v in value.items()}
    return value


def restore(value, mapping):
    """把模型返回内容中的代号换回原名（只处理本次代号表中的代号）。"""
    if isinstance(value, str):
        back = {v: k for k, v in mapping['stations'].items()}
        for alias in sorted(back, key=len, reverse=True):
            value = value.replace(alias, back[alias])
        return value
    if isinstance(value, list):
        return [restore(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: restore(v, mapping) for k, v in value.items()}
    return value


def summary(sources, targets, mapping):
    """给用户确认的脱敏摘要。"""
    return dict(sources=len(sources), targets=len(targets),
                stations=[dict(name=k, alias=v) for k, v in mapping['stations'].items()],
                orgs=len(mapping['orgs']), contacts=mapping['contacts'],
                fields=['名称', '型号及规范', '单位', '数量', '单价', '层级', '备注', '供货类型'],
                withheld=['原始文件名', '工作表名', '行号', '汇总表追溯证据'])
