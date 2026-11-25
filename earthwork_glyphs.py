"""土石方资料清洗 · 笔画文字识别：CAD 用单线字体出图时，高程注记在 PDF 里不是文字而是一段段笔画折线。

不内置字形库，也不调用模型：先用“位置已知高程”的注记（同一点在勘探点平面图上有文字注记）作样本，学出本图所用
字体里每个数字的笔画形状，再去读其余注记。同一字体的同一个字笔画完全相同（只差出图取整），所以按形状最近归类即可；
读出的结果须符合高程注记的写法（位数、小数位）且落在样本高程范围附近，否则不采用。"""
import math

import earthwork_geom as geom

SAMPLES = 16          # 每个字形沿笔画等距取的点数


def _glyphs(lines, max_size):
    """图层里的短笔画 → 字形：[{'pts', 'box'}]。"""
    out = []
    for line in lines:
        if line.get('fill') or line.get('strip') or len(line['pts']) < 2:
            continue
        box = geom.bbox(line['pts'])
        if max(box[2] - box[0], box[3] - box[1]) <= max_size:
            out.append({'pts': line['pts'], 'box': box})
    return out


def text_height(lines):
    """图层里笔画的典型字高：各笔画高度的 80 分位（小数点、“1”等窄矮笔画不影响）。"""
    heights = sorted(geom.bbox(l['pts'])[3] - geom.bbox(l['pts'])[1] for l in lines if not l.get('fill') and not l.get('strip') and len(l['pts']) >= 2)
    heights = [h for h in heights if h > 0]
    return heights[int(len(heights) * 0.8)] if heights else 0.0


def clusters(lines):
    """把同一行上相邻的笔画并成一条注记，行内按从左到右排好；在水平方向重叠的笔画属于同一个字。
    返回 [{'chars': [[笔画点列, ...], ...], 'box': (左, 下, 右, 上)}]。"""
    h = text_height(lines)
    if h <= 0:
        return [], 0.0
    glyphs = _glyphs(lines, h * 2.2)
    parent = list(range(len(glyphs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    grid = geom.Grid(h * 2)
    for i, g in enumerate(glyphs):
        x0, y0, x1, y1 = g['box']
        for _, j in grid.near((x0 + x1) / 2, (y0 + y1) / 2, h * 2.2):
            a = glyphs[j]['box']
            gap = max(x0, a[0]) - min(x1, a[2])            # 水平间隙（负值为重叠）
            if gap < h * 0.75 and abs(y0 - a[1]) < h * 0.45 or gap < h * 0.75 and min(y1, a[3]) - max(y0, a[1]) > h * 0.3:
                parent[find(i)] = find(j)
        grid.add((x0 + x1) / 2, (y0 + y1) / 2, i)
    groups = {}
    for i in range(len(glyphs)):
        groups.setdefault(find(i), []).append(glyphs[i])
    out = []
    for members in groups.values():
        members.sort(key=lambda g: g['box'][0])
        chars = []
        for g in members:
            if chars:
                last = chars[-1]
                overlap = min(last['box'][2], g['box'][2]) - max(last['box'][0], g['box'][0])
                narrow = min(last['box'][2] - last['box'][0], g['box'][2] - g['box'][0])
                if overlap > max(narrow, h * 0.05) * 0.5:
                    last['strokes'].append(g['pts'])
                    last['box'] = (min(last['box'][0], g['box'][0]), min(last['box'][1], g['box'][1]), max(last['box'][2], g['box'][2]), max(last['box'][3], g['box'][3]))
                    continue
            chars.append({'strokes': [g['pts']], 'box': g['box']})
        box = (min(c['box'][0] for c in chars), min(c['box'][1] for c in chars), max(c['box'][2] for c in chars), max(c['box'][3] for c in chars))
        out.append({'chars': chars, 'box': box})
    return out, h


def describe(char, baseline, height):
    """字形特征：笔画连成一条折线后等距取点，坐标相对本字左边界和整条注记的基线、按字高归一。"""
    pts = [p for stroke in char['strokes'] for p in stroke]
    left = char['box'][0]
    total = geom.length(pts)
    if total < 1e-9:
        sampled = [pts[0]] * SAMPLES
    else:
        sampled = geom.resample(pts, total / (SAMPLES - 1))[:SAMPLES]
        sampled += [pts[-1]] * (SAMPLES - len(sampled))
    vec = []
    for x, y in sampled:
        vec.extend(((x - left) / height, (y - baseline) / height))
    vec.append((char['box'][2] - char['box'][0]) / height)
    vec.append((char['box'][3] - char['box'][1]) / height)
    return vec


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def learn(samples, height):
    """samples：[(注记, 文本)]，字数与笔画组数相同的才用。返回 {字符: [特征均值, ...]}（同一字符形状差别大时分成多个样板）。"""
    by_char = {}
    for cluster, text in samples:
        if len(cluster['chars']) != len(text):
            continue
        baseline = cluster['box'][1]
        for char, letter in zip(cluster['chars'], text):
            by_char.setdefault(letter, []).append(describe(char, baseline, height))
    templates = {}
    for letter, vectors in by_char.items():
        groups = []          # [(均值, 个数)]
        for v in vectors:
            for k, (mean, count) in enumerate(groups):
                if _distance(v, mean) < 0.12:
                    groups[k] = ([(m * count + x) / (count + 1) for m, x in zip(mean, v)], count + 1)
                    break
            else:
                groups.append((v, 1))
        # 只出现一两次的形状多半来自错配的样本，不作样板
        floor = max(2, max(c for _, c in groups) // 20)
        templates[letter] = [mean for mean, count in groups if count >= floor]
    return {k: v for k, v in templates.items() if v}


def read(cluster, templates, height, limit=0.10):
    """读一条注记。返回 (文本, 最差的字形距离)；有字形认不出时返回 (None, 距离)。"""
    baseline = cluster['box'][1]
    text, worst = [], 0.0
    for char in cluster['chars']:
        v = describe(char, baseline, height)
        best = min(((_distance(v, mean), letter) for letter, means in templates.items() for mean in means), default=(9.0, '?'))
        worst = max(worst, best[0])
        if best[0] > limit:
            return None, worst
        text.append(best[1])
    return ''.join(text), worst


def read_spot_labels(lines, spots, known, pattern):
    """lines：注记图层的笔画（图面坐标）；spots：[(x, y)] 高程点位置；known：{点下标: 高程}。
    返回 ({点下标: 高程}（新读出的）, 统计)。"""
    found, height = clusters(lines)
    stats = {'labels': len(found), 'text_height': round(height, 3), 'trained_on': 0, 'read': 0, 'checked': 0, 'check_failed': 0}
    if not found or not spots:
        return {}, stats
    grid = geom.Grid(height * 4)
    for i, (x, y) in enumerate(spots):
        grid.add(x, y, i)
    # 注记左下角相对点位的偏移全图一致
    votes = {}
    unit = height * 0.25
    for c in found:
        for _, i in grid.near(c['box'][0], c['box'][1], height * 4)[:3]:
            key = (round((spots[i][0] - c['box'][0]) / unit), round((spots[i][1] - c['box'][1]) / unit))
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return {}, stats
    key = max(votes, key=votes.get)
    ox, oy = key[0] * unit, key[1] * unit
    owner = {}
    for n, c in enumerate(found):
        hit = grid.nearest(c['box'][0] + ox, c['box'][1] + oy, height * 0.8)
        if hit and hit[1] not in owner:
            owner[hit[1]] = n
    decimals = {}
    samples = []
    for i, z in known.items():
        if i in owner:
            text = f'{z:.2f}'
            samples.append((found[owner[i]], text))
            decimals[len(text.split('.')[1])] = decimals.get(len(text.split('.')[1]), 0) + 1
    stats['trained_on'] = len(samples)
    if len(samples) < 20:
        return {}, stats
    templates = learn(samples, height)
    stats['characters'] = ''.join(sorted(templates))
    # 用样本自检：读回的文本应与已知高程一致
    for cluster, text in samples:
        got, _ = read(cluster, templates, height)
        if len(cluster['chars']) == len(text):
            stats['checked'] += 1
            stats['check_failed'] += got != text
    if stats['checked'] and stats['check_failed'] > stats['checked'] * 0.02:
        return {}, stats           # 字形学得不可靠：不用
    low, high = min(known.values()) - 40, max(known.values()) + 40
    out = {}
    for i, n in owner.items():
        if i in known:
            continue
        text, _ = read(found[n], templates, height)
        if not text or not pattern.match(text) or len(text.split('.')[1]) != 2:
            continue
        value = float(text)
        if low <= value <= high:
            out[i] = value
    stats['read'] = len(out)
    return out, stats
