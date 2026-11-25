"""公共·文本与型号规则：物资名称、规格的文本规范化，型号强特征抽取，物资主体类别识别与相似度。其他技能共用。

本文件是平台流程技能，可在“技能开发与管理”中查看和修改。
跨技能调用写作“别名.函数名”，别名由 skill("技能ID") 取得；模型调用、日志、领域知识等由宿主通过 ctx 提供。
"""
import re

QTY = skill("quantity_rules")


def normalize_power_units(text: str) -> str:
    if not isinstance(text, str) or not text: return ""
    text = text.replace('（', '(').replace('）', ')')
    cable_sections = r'(1\.5|2\.5|4|6|10|16|25|35|50|70|95|120|150|185|240|300|400|500|630|800)'
    text = re.sub(rf'(?<![\d\.])({cable_sections})\s*(m2|M2|mm2|MM2|mm\^2|平方毫米)\b', r'\1mm²', text)
    text = re.sub(r'(?<=\d)\s*(m2|M2|平方米)\b', 'm²', text)
    text = re.sub(r'(?<=\d)\s*(mm2|MM2|mm\^2|平方毫米)\b', 'mm²', text)
    text = re.sub(r'(?<=\d)\s*(m3|M3|立方米)\b', 'm³', text)
    text = re.sub(r'(?<=\d)\s*(mpa|MPa|兆帕)\b', 'MPa', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(kv|KV|千伏)\b', 'kV', text)
    text = re.sub(r'(?<=\d)\s*(v|V|伏|伏特)\b', 'V', text)
    text = re.sub(r'(?<=\d)\s*(ka|KA|千安)\b', 'kA', text)
    text = re.sub(r'(?<=\d)\s*(a|A|安|安培)\b', 'A', text)
    text = re.sub(r'(?<=\d)\s*(ohm|ohms|欧|欧姆)\b', 'Ω', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(hz|HZ|赫兹)\b', 'Hz', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(mva|MVA|兆伏安)\b', 'MVA', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(kva|KVA|千伏安)\b', 'kVA', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(va|VA|伏安)\b', 'VA', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(kvar|KVAR|kVar|千乏)\b', 'kVAR', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(kw|KW|千瓦)\b', 'kW', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(w|W|瓦|瓦特)\b', 'W', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(ah|AH|安时)\b', 'Ah', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(s|S|秒)\b', 's', text)
    text = re.sub(r'(?<=\d)\s*(h|H|小时)\b', 'h', text)
    text = re.sub(r'(?<=\d)\s*(t|T|吨)\b', 't', text)
    text = re.sub(r'(?<=\d)\s*(kg|KG|公斤)\b', 'kg', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(芯)\b', '芯', text)
    text = re.sub(r'(?<=\d)\s*(m/bit|M/bit|M/BIT|mbps|Mbps)\b', 'M/bit', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(km|千米|公里)\b', 'km', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*(mm|毫米)\b', 'mm', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d)\s*m\b', 'm', text, flags=re.IGNORECASE) 
    all_units = r'mm²|m²|m³|kV|V|kA|A|kVA|VA|kVAR|kW|W|Ω|Hz|Ah|km|mm|m|kg|t|s|h|MPa|芯'
    text = re.sub(rf'(?<=\d)\s+(?={all_units})', '', text)
    return text

def preprocess_power_text_expert(text: str) -> str:
    if not text or not isinstance(text, str): return ""
    text = text.upper().strip().replace('（', '(').replace('）', ')')
    noise_patterns = [r'\(?甲供\)?', r'\(?乙供\)?', r'\(?利旧\)?', r'安装', r'拆除', r'调试', r'试验', r'运输', r'主材', r'辅材', r'本期', r'新建', r'扩建', r'单价', r'总价', r'含税', r'预算', r'估算', r'元', r'万元']
    for p in noise_patterns: text = re.sub(p, '', text)
    measurement_units = r'(\d+(\.\d+)?)\s*(个|只|台|套|组|米|M|吨|T|KG|KM|根|块|箱|捆|卷|副|双)(?![A-Z²23])'
    text = re.sub(measurement_units, '', text, flags=re.IGNORECASE)
    synonym_dict = {
        r'MOA': '氧化锌避雷器', r'氧化锌': '氧化锌避雷器', r'避雷器': '氧化锌避雷器',
        r'变压器本体': '变压器', r'主变': '变压器', r'GIS': '组合电器', r'HGIS': '组合电器',
        r'封闭式组合电器': '组合电器', r'端子箱': '端子箱', r'检修电源箱': '检修箱',
        r'XLPE': '交联聚乙烯', r'PVC': '聚氯乙烯', r'钢芯铝绞线': '导线', r'铝包钢': '铝包钢',
        r'CT': '电流互感器', r'PT': '电压互感器', r'CB': '断路器', r'DS': '隔离开关',
        r'空芯': '空心', r'热镀锌': '镀锌'
    }
    for pattern, replacement in synonym_dict.items(): text = re.sub(pattern, replacement, text)
    return re.sub(r'\s+', ' ', text).strip()

def get_smart_item_info(item: dict):
    name = item.get('设备材料名称') or item.get('Estimation_Name') or item.get('物资名称') or item.get('Name') or ""
    spec = item.get('型号及规范') or item.get('规格型号') or item.get('Spec') or ""
    unit = item.get('单位') or item.get('Estimation_Unit') or item.get('计量单位') or ""
    price = item.get('含税价') or item.get('Estimation_Price') or item.get('单价') or item.get('Price') or 0
    name_str = normalize_power_units(str(name).strip())
    spec_str = normalize_power_units(str(spec).strip())
    processed_name = preprocess_power_text_expert(name_str)
    if spec_str:
        processed_spec = spec_str.replace('（', '(').replace('）', ')')
        combined_text = f"{processed_name} {processed_spec}" if processed_spec not in processed_name else processed_name
    else:
        combined_text = processed_name
    return name_str, spec_str, str(unit).strip(), price, combined_text

def _cluster_clean_text(text: str) -> str:
    text = normalize_power_units(str(text or "")).upper()
    text = text.replace("×", "X").replace("*", "X")
    return re.sub(r"\s+", "", text)

def _dynamic_family_from_text(name: str, spec: str = "") -> tuple:
    """Infer the real material subject; project/service modifiers such as 主变/线路/35kV are not categories."""
    raw_name = normalize_power_units(str(name or "")).strip()
    raw_spec = normalize_power_units(str(spec or "")).strip()
    name_text = _cluster_clean_text(raw_name)
    spec_text = _cluster_clean_text(raw_spec)
    full_text = _cluster_clean_text(f"{raw_name} {raw_spec}")
    if not full_text:
        return "", 0.0

    if re.fullmatch(r"(?:开关柜)?电动底盘", primary_name_text := _cluster_clean_text(re.split(r"　+", raw_name, maxsplit=1)[0])):
        return "电动底盘", 0.99

    priority_patterns = [
        ("电能采集屏", r"电能采集屏|电能表.*电压监测仪.*电能采集屏|站用电能表.*电能采集屏"),
        ("电能表屏", r"电能表屏|计量屏|电度表屏"),
        ("电能表", r"电能表|电度表"),
        ("计算机监控系统服务", r"计算机监控系统.*(?:调试费|费)|监控系统调试"),
        ("微机五防系统服务", r"微机五防系统.*(?:更名|调试|费)"),
        ("安全防护评估", r"安全防护评估|测评|评估"),
        ("地调二次系统扩容", r"地调二次系统扩容"),
        ("安装辅材", r"^安装辅材"),
        ("标识牌", r"标识牌"),
        ("监控主站兼操作员站", r"监控主站兼操作员站|操作员站"),
        ("光传输设备", r"光传输设备|光电端机|ADM型|主控.*交叉板|交叉矩阵"),
        ("光接口板", r"光接口板|光模块|L4\.1"),
        ("以太网板", r"以太网板|\d+路10M/100M"),
        ("2M支路板", r"2M支路|G\.703"),
        ("综合配线柜", r"综合配线柜"),
        ("光纤配线架", r"光纤配线架|ODF"),
        ("路由器", r"路由器"),
        ("交换机", r"网络交换机|交换机|千兆交换机|百兆交换机|站控层.*交换机"),
        ("纵向加密装置", r"纵向加密(?:认证)?装置"),
        ("远动通信屏", r"远动通信屏|数据通信网屏|调度数据网屏"),
        ("二次安防屏", r"二次安防屏"),
        ("防火墙", r"防火墙"),
        ("时钟同步系统", r"时钟同步系统|主时钟|从时钟|GPS对时|北斗对时"),
        ("保护测控屏", r"保护测控屏|保护屏|测控屏"),
        ("阴极保护装置", r"阴极保护装置"),
        ("保护装置", r"(?:保护测控|保护)(?:装置|柜|箱)?|(?:主变压器|主变|站用变|线路|母线|电容器|分段|进线|出线).{0,12}保护\d*"),
        ("测控装置", r"测控(?:装置|柜|箱)?|(?:主变压器|主变|站用变|线路|母线|电容器|分段|进线|出线).{0,12}测控"),
        ("智能终端", r"智能终端"),
        ("智能网关", r"智能网关"),
        ("网关", r"[IVX]+区网关|网关"),
        ("图像采集系统", r"图像采集系统|视频接入存储模块|视频/图像|图像数据采集"),
        ("电子围栏", r"电子围栏"),
        ("火灾自动报警系统", r"火灾自动报警系统"),
        ("直流一体化电源系统", r"直流一体化电源系统|直流电源系统|直流充电屏|直流馈线屏"),
        ("直流充电屏", r"直流充电屏|充电屏"),
        ("交流不间断电源系统", r"交流不间断电源系统|交流不间断电源屏|UPS|不间断电源(?:系统|屏)?"),
        ("蓄电池组", r"蓄电池组"),
        ("设备线夹", r"设备线夹|铜铝过渡设备线夹|压缩型[ABC]?型|(?<![A-Z0-9])SYG?[-/]\d|(?<![A-Z0-9])SY[-/]\d"),
        ("耐张线夹", r"耐张线夹|(?<![A-Z0-9])NY[-/]\d"),
        ("T型线夹", r"[TＴ][型形]线夹|(?<![A-Z0-9])TY[-/]\d"),
        ("母线绝缘护套", r"母线绝缘护套"),
        ("软母线固定金具", r"软母线固定金具|软导线固定金具|单双软母线固定金具|(?<![A-Z0-9])MDG[-/]\d"),
        ("矩形母线固定金具", r"矩形母线固定金具|(?<![A-Z0-9])MWP[-/]\d"),
        ("端子箱", r"端子箱"),
        ("电源箱", r"检修电源箱|试验电源箱|电源箱|动力箱|配电箱|照明箱"),
        ("智能控制柜", r"智能控制柜|本体智能控制柜|控制柜"),
        ("接地手车", r"接地手车|接地小车"),
        ("中性点接地成套装置", r"中性点接地成套装置|接地变容量.*小电阻"),
        ("电压监测仪", r"电压监测仪"),
        ("PT并列装置", r"PT并列(?:屏|装置)|电压并列(?:屏|装置)"),
        ("开关柜", r"站用变柜|接地变柜|分段隔离柜|进线柜|馈线柜|电容器开关柜|母线设备柜|开关柜|配电柜|中置柜|环网柜|KYN|XGN|GGD|MNS|CFI智能屏"),
        ("电动底盘", r"电动底盘"),
        ("控制箱", r"控制箱"),
        ("铜母线伸缩节", r"铜母线伸缩节|母线伸缩节|(?<![A-Z0-9])MST[-/]\d"),
        ("热镀锌扁钢", r"热镀锌扁钢|镀锌扁钢|扁钢"),
        ("电缆桥架弯通", r"电缆桥架弯通|水平弯通"),
        ("电缆桥架三通", r"电缆桥架三通|水平三通"),
        ("电缆桥架", r"电缆桥架"),
        ("电缆防火槽盒", r"电缆防火槽盒|防火槽盒"),
        ("铜母排", r"铜母排|铜母线|铜排|(?<![A-Z0-9])TMY[-/]?\d|(?<![A-Z0-9])LMY[-/]?\d"),
        ("铜母排", r"紫铜(?:排)?|(?<![A-Z0-9])[—-]\d+(?:\.\d+)?[X×]\d"),
        ("封闭母线桥", r"封闭母线桥"),
        ("钢板", r"花纹钢板|冷轧普通钢板|钢板|δ\s*=\s*\d"),
        ("垂直接地极", r"垂直接地极"),
        ("放热焊接模具", r"放热焊接模具"),
        ("防火隔板", r"耐火隔板|防火板|复合防火板"),
        ("阻火模块", r"阻火模块|阻火膨胀模块"),
        ("防火堵料", r"防火堵料|有机防火堵料"),
        ("U型抱箍", r"U型抱箍|U形抱箍"),
        ("并沟线夹", r"并沟线夹"),
        ("铜鼻子", r"铜鼻子"),
        ("电缆沟支架", r"电缆沟支架|电缆支架|角钢.*格架|格架|(?<![A-Z0-9])E[-/]\d"),
        ("电缆终端", r"电缆终端|冷缩式.*电缆终端"),
        ("ADSS光缆", r"ADSS|自承式光缆|导引光缆"),
        ("铠装光缆", r"铠装光缆"),
        ("多模光缆", r"多模光缆"),
        ("光缆", r"光缆|OPGW"),
        ("塑料绝缘电线", r"塑料绝缘电线|聚氯乙烯绝缘电线|(?<![A-Z0-9])BV(?:R)?[-/ ]?\d|(?<![A-Z0-9])BV(?:R)?[-/ ]?500"),
        ("通信线缆", r"屏蔽双绞线|RVVSP|网线|尾缆|跳纤"),
        ("安装电缆", r"安装电缆"),
        ("控制电缆", r"控制电缆|KVVP|KYJV|KVV|KYJVP"),
        ("电力电缆", r"电力电缆|YJV|YJLV|VV|VLV"),
        ("膨胀螺栓", r"膨胀螺栓"),
        ("螺栓", r"螺栓|螺母|垫片"),
        ("变压器", r"主变压器|电力变压器|站用变(?!柜)|变压器"),
        ("氧化锌避雷器", r"氧化锌避雷器|(?<![A-Z])避雷器|(?<![A-Z0-9])YH\d+(?:\.\d+)?W[A-Z]*[-/]|(?<![A-Z0-9])HY\d+(?:\.\d+)?W[A-Z]*[-/]|(?<![A-Z0-9])Y\(H\)\d+(?:\.\d+)?W[A-Z]*[-/]"),
        ("断路器", r"断路器|SF6断路器|真空断路器"),
        ("电流互感器", r"电流互感器|CT|LZZ"),
        ("电压互感器", r"电压互感器|PT|JDZ"),
        ("隔离开关", r"隔离开关|接地开关"),
        ("熔断器", r"熔断器|跌落式熔断器"),
        ("绝缘子", r"绝缘子"),
        ("电容器", r"电容器"),
        ("电抗器", r"电抗器"),
        ("钢管", r"钢管|热镀锌钢管"),
        ("网线", r"网线"),
    ]

    def infer_by_priority(text: str) -> str:
        for family, pattern in priority_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return family
        return ""

    primary_name = re.split(r"　+", raw_name, maxsplit=1)[0]
    primary_name_text = _cluster_clean_text(primary_name)
    primary_name_family = infer_by_priority(primary_name_text)
    if primary_name_family:
        return primary_name_family, 0.98

    # 名称决定物资本体；型号/规格中“附本体端子箱、带控制柜”等配置词不能覆盖主体。
    name_family = infer_by_priority(name_text)
    if name_family:
        return name_family, 0.97

    # 名称过于泛化时，允许型号代码把“电缆”等泛类细化到控制电缆/电力电缆。
    if name_text in {"电缆", "线缆", "电线电缆"} or re.fullmatch(r"[\u4e00-\u9fff]{0,4}电缆", name_text or ""):
        spec_family = infer_by_priority(spec_text)
        if spec_family in {"控制电缆", "电力电缆", "安装电缆"}:
            return spec_family, 0.91

    # 名称无法判断时，再用完整描述兜底；置信度降低，避免配置词形成强 must-link。
    full_family = infer_by_priority(full_text)
    if full_family:
        return full_family, 0.82

    # 估算表把“名称　规格”写在同一列，用带规格的整串会让后缀锚定只在清册侧命中、
    # 两表系统性走不同分支，因此统一取名称段作为主体。
    normalized_name = re.sub(r"^\d+(?:\.\d+)?KV", "", primary_name_text or name_text, flags=re.IGNORECASE)
    normalized_name = re.sub(r"^(?:主变|站用|线路|变电站|户外|户内|智能|数字化|专用|成套)", "", normalized_name)
    normalized_name = re.sub(r"(?:本体|附属|配套|附件|系统|装置|设备|材料)$", "", normalized_name)
    suffix_patterns = [
        r"([\u4e00-\u9fff]{0,8}电缆)", r"([\u4e00-\u9fff]{0,8}螺栓)", r"([\u4e00-\u9fff]{0,8}变压器)",
        r"([\u4e00-\u9fff]{0,8}避雷器)", r"([\u4e00-\u9fff]{0,8}断路器)", r"([\u4e00-\u9fff]{0,8}互感器)",
        r"([\u4e00-\u9fff]{0,8}隔离开关)", r"([\u4e00-\u9fff]{0,8}熔断器)", r"([\u4e00-\u9fff]{0,8}绝缘子)",
        r"([\u4e00-\u9fff]{0,8}电容器)", r"([\u4e00-\u9fff]{0,8}电抗器)", r"([\u4e00-\u9fff]{0,8}控制柜)",
        r"([\u4e00-\u9fff]{0,8}端子箱)", r"([\u4e00-\u9fff]{0,8}电源箱)", r"([\u4e00-\u9fff]{0,8}屏)",
        r"([\u4e00-\u9fff]{0,8}柜)", r"([\u4e00-\u9fff]{0,8}箱)", r"([\u4e00-\u9fff]{0,8}钢管)",
        r"([\u4e00-\u9fff]{0,8}管)", r"([\u4e00-\u9fff]{0,8}金具)", r"([\u4e00-\u9fff]{0,8}网线)",
    ]
    for pattern in suffix_patterns:
        # 后缀类目必须落在名称末尾。否则 {0,8}管 会把“录波管理单元”截成“录波管”、
        # {0,8}电缆 会把“电缆防火槽盒”截成“电缆”，使槽盒/桥架被误判进电缆类。
        match = re.search(pattern + r"$", normalized_name)
        if match:
            family = re.sub(r"^(?:主变|站用|线路|变电站|户外|户内|智能|数字化|安装)", "", match.group(1))
            return family or match.group(1), 0.84

    # 兜底取名称段本体（全角空格前为名称、后为规格），剥掉编号与引号包装。
    # 不再按 10 字硬截断、也不再只取最后一段中文，避免 “I区生产数据单向采集装置”→“区生产数据单向采集装”、
    # “调度录音IP电话”→“电话” 这类被罗马数字/英文切碎的残缺 family。
    fallback = re.split(r"\u3000+", raw_name, maxsplit=1)[0]
    fallback = re.sub(r"^[\s\t\"']+", "", fallback)
    fallback = re.sub(r"^(?:\d+(?:\.\d+)*\s*[\u3001.\uff0e]\s*)+", "", fallback)   # 估算表的 “1.2、” 章节号
    fallback = re.sub(r"^[IVXivx]+\s*[\u3001.\uff0e]\s*", "", fallback)
    fallback = re.sub(r"^(?:变电|线路|电站电瓷|线路电瓷)\s+", "", fallback)  # 估算表的专业分类前缀
    fallback = re.sub(r"^\d+(?:\.\d+)?[kK][vV]", "", fallback)             # 与 normalized_name 同口径剥电压
    fallback = re.sub(r"[\s\t\"'\uff1a:\uff0c,\u3002\u3001]+$", "", fallback)
    fallback = re.sub(r"\s+", "", fallback)
    if re.search(r"[\u4e00-\u9fff]", fallback):
        return fallback[:32], 0.62

    cn_tokens = re.findall(r"[\u4e00-\u9fff]{2,10}", raw_name)
    if cn_tokens:
        return cn_tokens[-1], 0.62
    model_family = _extract_model_family(full_text)
    return (model_family, 0.60) if model_family else ("", 0.0)

def _classify_power_family(name: str, spec: str):
    return _dynamic_family_from_text(name, spec)

def _canonical_open_subject(subject: str) -> str:
    subject = re.sub(r"\s+", "", str(subject or "").strip())
    if not subject or _non_subject_category_name(subject):
        return ""
    corrected_family, corrected_confidence = _classify_power_family(subject, "")
    if corrected_family and corrected_confidence >= 0.86:
        return corrected_family
    subject = re.sub(r"^(?:35kV|10kV|110kV|220kV|500kV|变电站|线路|本期|新建|扩建|改造)+", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"(?:设备材料|物资|材料)$", "", subject)
    return subject[:32]

def _family_with_open_subject_hint(item: dict, name: str = "", spec: str = "") -> tuple:
    if not name and not spec:
        name = item.get("设备材料名称") or item.get("Estimation_Name") or item.get("物资名称") or item.get("名称") or item.get("Name") or ""
        spec = item.get("型号及规范") or item.get("规格型号") or item.get("型号") or item.get("Spec") or ""
    family, confidence = _classify_power_family(name, spec)
    hint = _canonical_open_subject(item.get("_open_subject_hint", ""))
    hint_confidence = QTY._to_float_or_zero(item.get("_open_subject_confidence"))
    if hint and (not family or confidence < 0.86 or _families_match_compatible(family, hint)):
        return hint, max(confidence, hint_confidence or 0.90)
    return family, confidence

def _family_for_match(item: dict):
    name = item.get("设备材料名称") or item.get("Estimation_Name") or item.get("物资名称") or item.get("名称") or item.get("Name") or ""
    spec = item.get("型号及规范") or item.get("规格型号") or item.get("型号") or item.get("Spec") or ""
    return _family_with_open_subject_hint(item, name, spec)

def _families_match_compatible(source_family: str, candidate_family: str) -> bool:
    if not source_family or not candidate_family or source_family == candidate_family:
        return True
    s_key, c_key = _cluster_clean_text(source_family), _cluster_clean_text(candidate_family)
    if ("电缆终端" in s_key) != ("电缆终端" in c_key):
        return False
    allowed_precise_aliases = [
        ("电能采集屏", "电能表屏"),
        ("PT并列装置", "PT并列屏"),
        ("ADSS光缆", "导引光缆"),
        ("ADSS光缆", "自承式光缆"),
        ("母线绝缘护套", "铜母排"),
        ("母线绝缘护套", "铜母线"),
        ("母线绝缘护套", "矩形母线"),
    ]
    if any((a in s_key and b in c_key) or (b in s_key and a in c_key) for a, b in allowed_precise_aliases):
        return True
    strict_family_tokens = [
        "母线绝缘护套", "铜母线伸缩节", "矩形母线固定金具", "软母线固定金具",
        "封闭母线桥", "铜母排", "电缆沟支架", "电流互感器", "电压互感器",
        "设备线夹", "耐张线夹", "T型线夹", "PT并列装置", "阴极保护装置",
        "电能采集屏", "电能表屏", "电能表", "ADSS光缆", "铠装光缆", "多模光缆",
        "光传输设备", "光接口板", "以太网板", "2M支路板", "综合配线柜", "光纤配线架",
        "安装电缆", "电力电缆", "控制电缆", "电缆终端",
    ]
    for token in strict_family_tokens:
        if (token in s_key) != (token in c_key) and any(other in s_key or other in c_key for other in strict_family_tokens):
            return False
    alias_pairs = [
        ("接地手车", "接地小车"),
        ("塑料绝缘电线", "聚氯乙烯绝缘电线"),
        ("导引光缆", "自承式光缆"),
        ("导引光缆", "ADSS光缆"),
        ("芯铠装光缆", "多模光缆"),
        ("软导线固定金具", "软母线固定金具"),
        ("电单双软母线固定金具", "软母线固定金具"),
        ("耐火隔板", "防火隔板"),
        ("复合防火板", "防火隔板"),
        ("防火堵料", "有机防火堵料"),
        ("阻火模块", "阻火膨胀模块"),
        ("主时钟", "时钟同步系统"),
        ("从时钟", "时钟同步系统"),
        ("交流不间断电源系统", "交流不间断电源屏"),
        ("纵向加密装置", "纵向加密认证装置"),
    ]
    if any((a in s_key and b in c_key) or (b in s_key and a in c_key) for a, b in alias_pairs):
        return True
    if s_key and c_key and (s_key in c_key or c_key in s_key):
        return True
    shared_suffixes = ["电缆", "电线", "螺栓", "变压器", "线缆", "开关", "避雷器", "断路器", "母排", "光缆", "堵料", "模块"]
    return any(suffix in s_key and suffix in c_key for suffix in shared_suffixes)

def _non_subject_category_name(category: str) -> bool:
    text = _cluster_clean_text(category)
    if not text:
        return True
    if text in {"安装辅材"}:
        return False
    if re.search(r"^绝缘护套$|配绝缘护套|相色标识|附件|附属|辅材|安装材料|其他|含外壳|厚度|外径|厂家配套|提供焊粉|配套提供|用于|内含|组屏|^主机$|含.*主机|红外对射|本期不上屏|^箱$", text):
        return True
    if re.match(r"^(配|带|附|含)", text) and not re.match(r"^(配电|配线|配管)", text):
        return True
    return False

def _normalize_numeric_token(value) -> str:
    token = str(value or "").strip()
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token

def _extract_strong_model_tokens(text: str) -> set:
    """Extract model tokens specific enough to override semantic drift."""
    text = _cluster_clean_text(text)
    token_patterns = [
        r"(?<![A-Z0-9])(?:TMY|LMY)[-/]?\d+(?:\.\d+)?[X×]\d+(?:\.\d+)?",
        r"(?<![A-Z0-9])(?:MST|MWP|JWP|MDG|NY|TY|SYG|SY|SSY|MG)[-/]\d+(?:\.\d+)?(?:[A-Z]?[-/]\d+(?:\.\d+)?)?",
        r"(?<![A-Z0-9])XQJ[-/][A-Z0-9-]+",
        r"(?<![A-Z0-9])[A-Z][-/]\d+(?:[-/]\d+)+",
        r"(?:[Φφ]\s*\d+(?:\.\d+)?|(?<![A-Z0-9])(?:DN|PC|PVC|PE)\s*\d+(?:\.\d+)?)\s*(?:MM)?",
        r"(?<![A-Z0-9])(?:YH|HY)\d+(?:\.\d+)?[A-Z]+[-/]\d+(?:\.\d+)?/\d+(?:\.\d+)?[A-Z]*",
        r"(?<![A-Z0-9])(?:ZSW|FXBW|XP)[A-Z0-9-]*[-/]\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?",
        r"(?<![A-Z0-9])(?:JL/LB|JL/G|JL|JKLYJ)[A-Z0-9/]*[-/]\d+(?:/\d+)?",
        r"(?<![A-Z0-9])(?:ZAN|ZRA|ZR|ZA|NH)?-?(?:YJV|YJLV|KVVP|KYJV|KVV|BV|BVR|VV|VLV)[A-Z0-9/.-]*(?:\d+\s*[X×]\s*\d+(?:\.\d+)?)?",
        r"\bS[A-Z]*\d+[-/]\d+(?:/\d+)?",
    ]
    tokens = set()
    for pattern in token_patterns:
        for token in re.findall(pattern, text, flags=re.IGNORECASE):
            token = re.sub(r"\s+", "", token.upper()).replace("X", "×")
            token = token.replace("Φ", "φ")
            token = token.rstrip("MM²").rstrip("MM2")
            is_pipe_diameter = bool(re.match(r"^(?:φ|DN|PC|PVC|PE)\d", token))
            if (len(token) >= 5 or is_pipe_diameter) and "□" not in token and "口" not in token:
                tokens.add(token)
    return tokens

def _extract_model_family(text: str) -> str:
    cable_match = re.search(r"(?<![A-Z0-9])(KYJVP|KYJV|KVVP|KVV|YJLV|YJV|VLV|VV)", text)
    if cable_match:
        return cable_match.group(1)
    for pattern in [r"\b(KYN|XGN|ZW|LW|GW|LZZ|JDZ|ZSW|FXBW|TMY|LMY|JL)\b", r"\b([A-Z]{2,6})[-/]"]:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""

def _char_ngrams(text: str, n=2):
    compact = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    if not compact:
        return set()
    if len(compact) <= n:
        return {compact}
    return {compact[i:i+n] for i in range(len(compact) - n + 1)}

def _jaccard_similarity(text_a: str, text_b: str) -> float:
    grams_a, grams_b = _char_ngrams(text_a), _char_ngrams(text_b)
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)

def _split_match_sequences(value) -> list:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,，、;/\s]+", text) if part.strip()]

def _brief_text(value, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit] + "..."

def _clean_compare_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", normalize_power_units(str(text or "")).upper())

def _clean(text, limit):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]

# ---------------------------------------------------------------- 字段读取
def _src_name(item):
    return str(item.get("Estimation_Name") or item.get("设备材料名称") or item.get("物资名称") or item.get("名称") or "")

def _src_spec(item):
    return str(item.get("型号及规范") or item.get("规格型号") or item.get("型号") or "")

def _src_qty(item):
    return item.get("Estimation_Quantity") or item.get("数量") or ""

def _src_unit(item):
    return item.get("Estimation_Unit") or item.get("单位") or ""

def _tgt_name(item):
    return str(item.get("设备材料名称") or item.get("Estimation_Name") or item.get("物资名称") or item.get("名称") or "")

def _tgt_spec(item):
    return str(item.get("型号及规范") or item.get("规格型号") or item.get("型号") or "")

def _seq(item):
    return str(item.get("序号") or item.get("seq") or "").strip()


# ---------------------------------------------------------------- 站点归属
# 两表父级只是造价与清册人员各自的归类，名称写法不同（造价“对侧间隔 / 35kV弄岛变”，清册“35kV弄岛变”），
# 不能只凭父级里有没有“对侧”二字判断是否同一站点
SITE_RE = re.compile(r"\d+(?:\.\d+)?kV\s*([\u4e00-\u9fa5]{2,4}?)(?:变电站|开关站|换流站|变|站)(?![压电流频器])", re.I)
NOT_SITE = ("站用", "所用", "主", "接地", "箱式", "消弧", "配电", "降压", "升压", "本", "对侧", "扩建", "新建")


def site_of(item):
    """物资所属站点：(父级是否写“对侧”, 父级中写明的站名集合)。"""
    path = " ".join(str(x) for x in (item.get("层级路径") or [item.get("层级") or ""]))
    names = {m.group(1) for m in SITE_RE.finditer(path) if "变" not in m.group(1) and not m.group(1).startswith(NOT_SITE)}
    return "对侧" in path, names


def same_site(a, b):
    """两条物资是否属于同一站点：两侧父级都写了站名时按站名判断，不论是否写“对侧”；
    有一侧没写站名时，才按“对侧/本站”区分（本站物资不与对侧间隔的物资配对）。"""
    remote_a, names_a = site_of(a)
    remote_b, names_b = site_of(b)
    if names_a and names_b:
        return bool(names_a & names_b)
    return remote_a == remote_b

