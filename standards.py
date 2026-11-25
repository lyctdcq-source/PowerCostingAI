"""规范文件按条款切分：识别标准号与名称，按条款号（含附录、表）切分，去掉目次、页眉页脚和页码。"""
import hashlib
import re
from pathlib import Path

FIND = re.compile(r'(?<![A-Za-z])((?:T/)?(?:GB|DL|NB|JB|YB|SL|JGJ|CJJ|CECS|CEC|CSEE|Q/GDW|Q/CSG)(?:\s*[/_]\s*[TZ])?\s*\d+(?:\.\d+)*\s*[-—–]\s*\d{4})', re.I)
NUMBERED = re.compile(r'^([A-Z]\.\d{1,2}(?:\.\d{1,2}){0,3}|\d{1,2}(?:\.\d{1,2}){0,4})[\s　]+(?=[一-鿿A-Za-z（(“"])(.*)$')
APPENDIX = re.compile(r'^附\s*录\s*([A-Z])(?:[\s　（(]|$)(.*)$')
TABLE = re.compile(r'^表\s*([A-Z]\.?\d+(?:\.\d+)*|\d+(?:\.\d+)*)(?:[\s　]+(.*))?$')
# 多级条款号紧跟正文、中间没有空格（扫描件识别常丢空格）：“1.0.2本规范适用于…”
TIGHT = re.compile(r'^([A-Z]\.\d{1,2}(?:\.\d{1,2}){1,3}|\d{1,2}(?:\.\d{1,2}){1,4})(?=[一-鿿])(.*)$')
# 扫描件识别常把条款号里的 0、1 认成 O、I/l，并在点号两侧加空格：“I.O. 2本规范…”
OCR_NUMBER = re.compile(r'^([0-9OIl]{1,2}(?:\s*[.．]\s*[0-9OIl]{1,2}){1,4})(?=\s*[一-鿿])')
EXPLANATION = re.compile(r'^条\s*文\s*说\s*明$')
PREFACE = re.compile(r'^(前\s*言|引\s*言)$')
CONTENTS = re.compile(r'^(目\s*次|目\s*录)$')
ROMAN = 'IVXLCivxlcⅠ-ⅿ'  # 含全角罗马数字 Ⅰ～Ⅻ（前言、目次常用作页码）
# 目次引导线，页码可带括号、引导点之间可有空格：“1 总则 …… 1”“附录C …. . ( 56)”
LEADER = re.compile(rf'[.…·](?:\s*[.…·]){{2,}}\s*(?:[(（]\s*)?[{ROMAN}\d\s]{{0,8}}(?:[)）])?\s*$')
TOC_LINE = re.compile(rf'\s[{ROMAN}\d]{{1,4}}$')
PAGE_NUMBER = re.compile(rf'^[-—\s]*[{ROMAN}\d]{{1,4}}[-—\s]*$')
PART_LIMIT = 6000


def normalize_standard(text):
    """“GB_T 706—2016”“gb/t706-2016” 统一为 “GB/T 706-2016”。"""
    text = re.sub(r'\s*[/_]\s*', '/', str(text or '').upper())
    match = re.match(r'\s*((?:T/)?[A-Z]+(?:/[A-Z]+)?)\s*(\d+(?:\.\d+)*)\s*[-—–]\s*(\d{4})', text)
    return f'{match.group(1)} {match.group(2)}-{match.group(3)}' if match else str(text or '').strip()


def detect(file_name, chunks):
    """从文件名与封面文字识别标准号、名称和版本（年份）；识别不到时留空，由用户填写。"""
    head = '\n'.join(c['content'] for c in chunks[:3])[:4000]
    stem = Path(file_name).stem
    found = FIND.search(stem) or FIND.search(head)
    standard = normalize_standard(found.group(1)) if found else ''
    name = ''
    if found and FIND.search(stem):
        name = re.sub(r'^\d{4}[_\-.]\d{1,2}[_\-.]\d{1,2}[_\s\-]*', '', FIND.sub('', stem))  # 下载时加的日期前缀
        name = re.sub(r'[（(]\s*\d*\s*[)）]', '', name)  # “(1)”副本编号与空括号
        name = re.sub(r'^[\s_\-—–·.、《》]+|[\s_\-—–·.、《》]+$', '', name)
        opened, closed = name.count('(') + name.count('（'), name.count(')') + name.count('）')
        name = name.rstrip(')）') if closed > opened else name.lstrip('(（') if opened > closed else name  # 只去掉不成对的括号
    if not name:
        skip = ('中华人民共和国', '标准', '发布', '实施', '代替', 'ICS', '前言', '目次', '目录')
        name = next((s for s in (line.strip() for line in head.split('\n'))
                     if 2 <= len(s) <= 40 and len(re.findall(r'[一-鿿]', s)) >= 2 and not any(w in s for w in skip)
                     and not FIND.search(s)), '')
    return {'standard': standard, 'name': name, 'edition': standard[-4:] if re.search(r'-\d{4}$', standard) else ''}


def _key(clause):
    return tuple(int(p) if p.isdigit() else p for p in clause.split('.'))


def _rank(part):
    return part if isinstance(part, int) else ord(part) - ord('A') + 1


def _fresh(tail):
    """新开的下级编号：中间级为 0 或 1（GB 50xxx 的“1.0.1”以 0 占位），末级为 1 或 2。"""
    return not tail or (all(x in (0, 1) for x in tail[:-1]) and tail[-1] in (1, 2))


def _follows(prev, cur):
    """条款号是否可接在上一条之后：下级重新编号，或同级、上级递增后下级重新编号（允许漏识别最多 2 条）。"""
    if prev is None:
        return True
    if len(cur) > len(prev) and cur[:len(prev)] == prev and _fresh(cur[len(prev):]):
        return True
    return any(cur[:k - 1] == prev[:k - 1] and type(cur[k - 1]) is type(prev[k - 1])
               and 0 < _rank(cur[k - 1]) - _rank(prev[k - 1]) <= 3 and _fresh(cur[k:])
               for k in range(1, min(len(prev), len(cur)) + 1))


def _clean(line):
    """去掉汉字之间的空格，并还原扫描件识别错的条款号（O→0，I/l→1）。"""
    line = re.sub(r'(?<=[一-鿿])[ \t　]+(?=[一-鿿])', '', line.strip())
    prefix = re.match(r'表\s*', line)  # 表号同样会被识别错：“表5 . O. 5”
    head, rest = (line[:prefix.end()].strip(), line[prefix.end():]) if prefix else ('', line)
    match = OCR_NUMBER.match(rest)
    if match:
        rest = re.sub(r'\s', '', match.group(1)).replace('．', '.').translate(str.maketrans('OIl', '011')) + rest[match.end():]
    return head + rest


def _lines(chunks):
    for chunk in chunks:
        location = re.sub(r' · 片段\d+$', '', str(chunk.get('location', '')))
        paragraph = re.match(r'段落(\d+)～', location)
        for i, line in enumerate(str(chunk.get('content', '')).split('\n')):
            yield (f'段落{int(paragraph.group(1)) + i}' if paragraph else location), line.strip()


def _poor_text(chunks):
    """文字层质量差（扫描件识别）：汉字间大量空格，或大量只有一两个字的行。"""
    lines = [l.strip() for c in chunks for l in str(c.get('content', '')).split('\n') if l.strip()]
    if len(lines) < 30:
        return False  # 样本太少不判断
    text = '\n'.join(lines)
    cjk = len(re.findall(r'[一-鿿]', text)) or 1
    spaced = len(re.findall(r'[一-鿿][ \t　]+(?=[一-鿿])', text))
    tiny = sum(1 for l in lines if len(l) <= 2)
    return spaced / cjk > 0.2 or tiny / (len(lines) or 1) > 0.2


def split(chunks, standard=''):
    """返回 (条款列表, 提示)。条款含 clause、heading、content、location、path；无正文的章标题只作为下级条款的上下文。"""
    core = re.sub(r'\D', '', standard.split(' ')[-1]) if standard else ''
    entries, path, prev, contents = [], {}, None, False
    explain, section = '', 0  # 条文说明部分条款号从 1 重新开始，单独标注
    current = None

    def start(clause, heading, location, kind):
        nonlocal current
        key = re.sub('^(附录|条文说明 )', '', clause)
        current = {'clause': clause, 'heading': heading.strip(), 'location': location, 'lines': [], 'kind': kind, 'absorbed': 0,
                   'path': ' › '.join(v for k, v in sorted(path.items(), key=lambda kv: len(kv[0])) if key.startswith(k + '.'))}
        entries.append(current)

    for location, raw in _lines(chunks):
        line = _clean(raw)
        if not line or PAGE_NUMBER.match(line) or line.startswith('续表') or LEADER.search(line):
            continue  # 页码、续表标题、目次引导线
        if core and len(line) <= 30 and re.sub(r'\D', '', line) == core:
            continue  # 页眉中的标准号
        if CONTENTS.match(line):
            contents = True
            continue
        if contents:
            if TOC_LINE.search(line):
                continue
            contents = False
        if EXPLANATION.match(line):
            explain, section, prev, path = '条文说明 ', len(entries), None, {}
            continue
        if PREFACE.match(line):
            start(re.sub(r'\s', '', line), '', location, 'preface')
            continue
        appendix, numbered, table = APPENDIX.match(line), NUMBERED.match(line) or TIGHT.match(line), TABLE.match(line)
        # 附录在正文条款之后；正文还没出现时的“附录”多是目次或封面文字
        if appendix and prev is not None and (isinstance(prev[0], int) or _follows(prev, (appendix.group(1),))):
            prev = (appendix.group(1),)
            path = {appendix.group(1): f"附录{appendix.group(1)} {appendix.group(2).strip()[:30]}".strip()}
            start('附录' + appendix.group(1), appendix.group(2), location, 'appendix')
            continue
        if numbered:
            key = _key(numbered.group(1))
            # 章标题是短标题；“5 角钢 50 5 3.77”这类表格行有多个数字，不当作章
            # 符号表里的“5 —— 导线与地线间的距离 =”也不是章
            title = numbered.group(2)
            chapter_like = len(key) > 1 or (len(title) <= 30 and len(re.findall(r'\d+(?:\.\d+)?', title)) <= 1
                                            and not re.match(r'[—–\-一]{2}|[—–\-]', title.strip()) and '=' not in title)
            # 目次条目、封面上“1 kV”之类的行不是条款：遇到第 1 章而此前识别的条款几乎没有正文时，丢弃重来
            if chapter_like and key == (1,) and all(len(e['lines']) <= 2 for e in entries[section:] if e['kind'] == 'clause'):
                entries[section:] = [e for e in entries[section:] if e['kind'] != 'clause']
                prev, path = None, {}
            # 第一条可以是章（扫描件的章标题常识别失败，也可以直接从“1.0.1”开始）
            if chapter_like and _follows(prev, key) and (prev is not None or len(key) == 1 or _fresh(key)):
                prev = key
                clause = numbered.group(1)
                path = {k: v for k, v in path.items() if clause.startswith(k + '.')}
                path[clause] = f'{clause} {numbered.group(2).strip()[:30]}'
                start(explain + clause, numbered.group(2), location, 'clause')
                continue
        if table:
            start('表' + table.group(1).replace(' ', ''), table.group(2) or '', location, 'table')
            continue
        if current is not None and current['kind'] == 'appendix' and not current['lines'] and current['absorbed'] < 2 and len(line) <= 30:
            # “附录 A”后面的“（资料性附录）”“型钢截面特性”是附录标题，不是正文
            current['heading'] = (current['heading'] + ' ' + line).strip()
            current['absorbed'] += 1
            path[current['clause'][2:]] = f"{current['clause']} {current['heading'][:30]}"
            continue
        if current is not None:
            current['lines'].append(line)
    clauses = []
    for e in entries:
        body = '\n'.join(e['lines']).strip()
        sentence = e['kind'] == 'clause' and (re.search(r'[。；;：:]$', e['heading']) or len(e['heading']) > 25)
        if not body and not sentence:
            continue  # 只有标题的章节或附录：作为下级条款的上下文
        text = ((e['clause'] + ' ' + e['heading']).strip() + '\n' + body).strip() if e['kind'] != 'preface' else body
        parts = [text[i:i + PART_LIMIT] for i in range(0, len(text), PART_LIMIT)]
        for n, part in enumerate(parts):
            clauses.append({'clause': e['clause'], 'heading': e['heading'][:40], 'content': part, 'part': n,
                            'location': e['location'] + (f" · {e['path']}" if e['path'] else ''),
                            'title': f"{e['clause']} {e['heading'][:30]}".strip() + (f'（续{n}）' if n else '')})
    warnings = ['文字层质量差（疑似扫描件识别出的文字：字间大量空格、单字成行或错字），条款识别和检索效果会受影响。建议使用文字版 PDF，或重新做文字识别后再导入。'] if _poor_text(chunks) else []
    if len({c['clause'] for c in clauses if re.match(r'\d|[A-Z]\.', c['clause'])}) < 3:
        warnings.append('识别到的条款很少：可能是扫描件，或 Word 使用了自动编号（条款号不在文字里）。可改用 PDF，或按段落导入为自定义规则。')
    return clauses, warnings


def entry_id(standard, clause, part):
    return 'std-' + hashlib.sha256(f'{standard}|{clause}|{part}'.encode()).hexdigest()[:12]
