"""土石方资料清洗 · 等高线定高程：图上没有逐条标注高程时，由高程点和等高线之间的相邻关系推定每条等高线的高程。

依据三条事实：
1. 等高线是测图软件由高程点构网内插出来的，在等高线上用高程点三角网内插，得到的值在真实高程附近（有几分米误差）；
2. 相邻两条等高线的高程相差一个等高距，或（跨山脊、山谷线时）相等；
3. 计曲线（加粗线）的高程是 5 倍等高距的整数倍，有高程注记的等高线高程已知。
先用 1 得到每条线的估计值，再用 2、3 在全图范围内做约束平差，最后取整到等高距的整数倍；
与估计值相差过大或得不到估计值的线标为“高程待定”，不强行赋值。"""
import math
import statistics

import earthwork_geom as geom

INTERVALS = (0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0)
SAMPLE_STEP = 4.0      # 沿等高线取样的间距（m）
RAY = 40.0             # 向两侧找相邻等高线的最大距离（m）


def estimate(lines, tin, max_samples=60):
    """每条等高线的三角网内插估计值（各取样点的中位数）和离散度；取样点不足 3 个在网内的返回 None。"""
    out = []
    for pts in lines:
        # 沿线等距取样（不只取顶点：抽稀后的直线段只有两个顶点）
        total = geom.length(pts)
        samples = geom.resample(pts, max(total / max_samples, 0.5)) if total > 0 else pts
        values = [v for v in (tin.z(x, y) for x, y in samples) if v is not None]
        if len(values) < 3:
            out.append(None)
            continue
        med = statistics.median(values)
        out.append((med, statistics.median(abs(v - med) for v in values)))
    return out


def guess_interval(estimates, pairs, label_values=()):
    """等高距：有高程注记时取注记高程最小公差的 1/5（注记标在计曲线上）；否则取相邻等高线估计值之差的中位数，
    归到最接近的常用等高距（相邻线的估计误差方向相近，差值比估计值本身可靠）。"""
    labels = sorted(set(round(v, 3) for v in label_values))
    if len(labels) >= 2:
        gap = min(b - a for a, b in zip(labels, labels[1:]))
        return min(INTERVALS, key=lambda d: abs(d - gap / 5))
    diffs = sorted(abs(estimates[i][0] - estimates[j][0]) for (i, j), count in pairs.items() if count >= 3 and estimates[i] and estimates[j])
    if not diffs:
        return 1.0
    typical = diffs[len(diffs) // 2]
    return min(INTERVALS, key=lambda d: abs(math.log(d / max(typical, 1e-6))))


def neighbours(lines, step=SAMPLE_STEP, reach=RAY):
    """相邻关系：从每条线的取样点向两侧作法线，最先碰到的另一条线即为该侧的相邻线。
    返回 {(i, j): 次数}（i < j）。"""
    cell = 8.0
    grid = {}
    for i, pts in enumerate(lines):
        for k in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[k], pts[k + 1]
            for cx in range(int(math.floor(min(x1, x2) / cell)), int(math.floor(max(x1, x2) / cell)) + 1):
                for cy in range(int(math.floor(min(y1, y2) / cell)), int(math.floor(max(y1, y2) / cell)) + 1):
                    grid.setdefault((cx, cy), []).append((i, x1, y1, x2, y2))
    pairs = {}
    for i, pts in enumerate(lines):
        if geom.length(pts) < step:
            samples = [(pts[len(pts) // 2], pts[0], pts[-1])]
        else:
            rs = geom.resample(pts, step)
            samples = [(rs[k], rs[k - 1], rs[k + 1]) for k in range(1, len(rs) - 1)]
        for (px, py), a, b in samples:
            tx, ty = b[0] - a[0], b[1] - a[1]
            norm = math.hypot(tx, ty)
            if norm < 1e-9:
                continue
            nx, ny = -ty / norm, tx / norm
            for sign in (1, -1):
                dx, dy = nx * sign, ny * sign
                best, hit = reach, None
                seen = set()
                # 沿射线逐格查找
                for s in range(int(reach / cell) + 2):
                    qx, qy = px + dx * s * cell, py + dy * s * cell
                    for cx in (int(math.floor(qx / cell)) - 1, int(math.floor(qx / cell)), int(math.floor(qx / cell)) + 1):
                        for cy in (int(math.floor(qy / cell)) - 1, int(math.floor(qy / cell)), int(math.floor(qy / cell)) + 1):
                            if (cx, cy) in seen:
                                continue
                            seen.add((cx, cy))
                            for j, x1, y1, x2, y2 in grid.get((cx, cy), ()):
                                if j == i:
                                    continue
                                ex, ey = x2 - x1, y2 - y1
                                den = dx * ey - dy * ex
                                if abs(den) < 1e-12:
                                    continue
                                t = ((x1 - px) * ey - (y1 - py) * ex) / den
                                u = ((x1 - px) * dy - (y1 - py) * dx) / den
                                if 0.05 < t < best and -1e-9 <= u <= 1 + 1e-9:
                                    best, hit = t, j
                    if hit is not None and best <= (s + 1) * cell:
                        break
                if hit is not None:
                    key = (i, hit) if i < hit else (hit, i)
                    pairs[key] = pairs.get(key, 0) + 1
    return pairs


def crossings(lines, spacing=3.0):
    """用四组平行的横断线（水平、竖直、两条对角方向）切割全部等高线，返回各横断线上按位置排序的等高线序列。
    同一条横断线上相邻两次穿越的等高线在地面上相邻。"""
    families = ((1.0, 0.0), (0.0, 1.0), (math.sqrt(0.5), math.sqrt(0.5)), (math.sqrt(0.5), -math.sqrt(0.5)))
    out = []
    for ux, uy in families:
        vx, vy = -uy, ux                      # 横断线沿 u 方向，按 v 方向的坐标分条
        rows = {}
        for i, pts in enumerate(lines):
            px, py = pts[0]
            pu, pv = px * ux + py * uy, px * vx + py * vy
            for qx, qy in pts[1:]:
                qu, qv = qx * ux + qy * uy, qx * vx + qy * vy
                lo, hi = (pv, qv) if pv <= qv else (qv, pv)
                if hi - lo > 1e-12:
                    for k in range(int(math.ceil(lo / spacing)), int(math.floor(hi / spacing)) + 1):
                        level = k * spacing
                        if lo <= level < hi:
                            f = (level - pv) / (qv - pv)
                            rows.setdefault(k, []).append((pu + (qu - pu) * f, i))
                pu, pv = qu, qv
        for row in rows.values():
            row.sort()
            out.append([i for _, i in row])
    return out


def _vote_between_anchors(sequences, level, votes):
    """横断线上两条已知高程的等高线之间，若中间的线数正好等于层号差减一，说明这一段是单调坡面，
    中间各线的层号按顺序唯一确定；每确定一次记一票。"""
    for seq in sequences:
        last = None
        for pos, i in enumerate(seq):
            if level[i] is None:
                continue
            if last is not None:
                a, b = last, pos
                between = b - a - 1
                diff = level[i] - level[seq[a]]
                if between and abs(diff) == between + 1:
                    sign = 1 if diff > 0 else -1
                    for k in range(1, between + 1):
                        j = seq[a + k]
                        votes.setdefault(j, {})
                        key = level[seq[a]] + sign * k
                        votes[j][key] = votes[j].get(key, 0) + 1
            last = pos


def assign(lines, tin, interval=None, index_flags=None, known=None, label_values=()):
    """给等高线定高程。
    lines：折线列表；index_flags：各线是否计曲线（未知传 None）；known：{线下标: 已知高程}（来自高程注记）。
    返回 (高程列表（待定为 None）, 采用的等高距, 统计)。"""
    n = len(lines)
    known = dict(known or {})
    est = estimate(lines, tin)
    pairs = neighbours(lines)
    d = interval or guess_interval(est, pairs, list(label_values) + list(known.values()))
    flags = index_flags or [False] * n
    level = [None] * n       # 层号：高程 / 等高距，整数
    source = [''] * n
    for i in range(n):
        if i in known:
            level[i], source[i] = round(known[i] / d), '注记'
        elif flags[i] and est[i]:
            # 计曲线的高程是 5 倍等高距的整数倍，估计值有几分米误差也能取对
            level[i], source[i] = round(est[i][0] / (5 * d)) * 5, '计曲线'
    sequences = crossings(lines)
    anchors = sum(1 for v in level if v is not None)
    if anchors:
        # 计曲线之间互相校核：横断线上两条计曲线之间正好隔 4 条首曲线，说明两者相差 5 个等高距；
        # 不足 4 条说明是同一高程（同一条线的两段，或山脊、山谷两侧）。据此给没有估计值的计曲线定高程，
        # 并纠正取整差了一档的计曲线。有高程注记的线不改。
        for _ in range(4):
            changed = 0
            for i in range(n):
                if not flags[i] or source[i] == '注记':
                    continue
                tally = {}
                for seq in sequences:
                    for pos, j in enumerate(seq):
                        if j != i:
                            continue
                        for step in (-1, 1):
                            q, between = pos + step, 0
                            while 0 <= q < len(seq) and not (flags[seq[q]] or source[seq[q]] == '注记'):
                                between += 1; q += step
                            if not 0 <= q < len(seq) or seq[q] == i or level[seq[q]] is None:
                                continue
                            other = level[seq[q]]
                            if between < 4:
                                picks = [other]
                            elif between == 4:
                                if est[i]:
                                    picks = [min((other + 5, other - 5), key=lambda k: abs(k * d - est[i][0]))]
                                else:
                                    # 没有估计值：看这条横断线上再往外一条计曲线，坡向延续
                                    r, more = q + step, 0
                                    while 0 <= r < len(seq) and not flags[seq[r]]:
                                        more += 1; r += step
                                    if 0 <= r < len(seq) and level[seq[r]] is not None and level[seq[r]] != other:
                                        picks = [other + (5 if other > level[seq[r]] else -5)]
                                    else:
                                        continue
                            else:
                                continue
                            for key in picks:
                                tally[key] = tally.get(key, 0) + 1
                if not tally:
                    continue
                key, top = max(tally.items(), key=lambda kv: kv[1])
                # 自身有估计值的计曲线，只在新高程与估计值相差不超过 0.7 档时才改（估计值可信时不被少数横断线带偏）
                if top / sum(tally.values()) >= 0.6 and key != level[i] and (not est[i] or abs(key * d - est[i][0]) <= 3.5 * d):
                    level[i], source[i] = key, '计曲线'
                    changed += 1
            if not changed:
                break
        # 逐轮投票：新定出的线在下一轮也作为已知线
        for _ in range(6):
            votes = {}
            _vote_between_anchors(sequences, level, votes)
            added = 0
            for j, tally in votes.items():
                if level[j] is not None:
                    continue
                total = sum(tally.values())
                key, top = max(tally.items(), key=lambda kv: kv[1])
                if top >= 3 and top / total >= 0.7:
                    level[j], source[j] = key, '相邻推定'
                    added += 1
            if not added:
                break
    if anchors:
        # 只有一侧有已知线的（最外一条计曲线以外、图幅边缘）：按顺序每隔一条线差一层，方向和结果用自身估计值校核——
        # 估计值须落在推定高程的 0.6 个等高距以内。没有估计值的不推。
        for _ in range(4):
            votes = {}
            for seq in sequences:
                known_at = [pos for pos, i in enumerate(seq) if level[i] is not None]
                if not known_at:
                    continue
                for edge, step in ((known_at[0], -1), (known_at[-1], 1)):
                    pos, k = edge + step, 1
                    while 0 <= pos < len(seq) and k <= 2:      # 只外推紧邻的两条，再远不可靠
                        j = seq[pos]
                        if est[j]:
                            key = min((level[seq[edge]] + k, level[seq[edge]] - k), key=lambda v: abs(v * d - est[j][0]))
                            if abs(key * d - est[j][0]) <= 0.6 * d:
                                votes.setdefault(j, {})
                                votes[j][key] = votes[j].get(key, 0) + 1
                        pos += step; k += 1
            added = 0
            for j, tally in votes.items():
                if level[j] is not None:
                    continue
                key, top = max(tally.items(), key=lambda kv: kv[1])
                if top >= 6 and top / sum(tally.values()) >= 0.9:
                    level[j], source[j] = key, '单侧推定'
                    added += 1
            if not added:
                break
            more = {}
            _vote_between_anchors(sequences, level, more)
            for j, tally in more.items():
                if level[j] is None:
                    key, top = max(tally.items(), key=lambda kv: kv[1])
                    if top >= 3 and top / sum(tally.values()) >= 0.7:
                        level[j], source[j] = key, '相邻推定'
    # 图上既没有计曲线也没有高程注记可作基准时，只能按估计值取整：估计值明确靠近某一层（偏差不超过等高距的 30%）
    # 且与已定出的相邻线相差不超过一层才采用。有基准时不用这一步——实测它的正确率只有一半左右，宁可留作待定。
    if not anchors:
        near = [[] for _ in range(n)]
        for (i, j), count in pairs.items():
            if count >= 2:
                near[i].append(j); near[j].append(i)
        for i in sorted(range(n), key=lambda k: abs(est[k][0] / d - round(est[k][0] / d)) if est[k] else 9):
            if not est[i]:
                continue
            guess = round(est[i][0] / d)
            if abs(est[i][0] / d - guess) <= 0.3 and all(abs(guess - level[j]) <= 1 for j in near[i] if level[j] is not None):
                level[i], source[i] = guess, '估计取整'
    out = [None if v is None else round(v * d, 3) for v in level]
    counts = {}
    for name in source:
        counts[name or '待定'] = counts.get(name or '待定', 0) + 1
    return out, d, {'estimated': sum(1 for e in est if e), 'pending': counts.get('待定', 0), 'by': counts, 'pairs': len(pairs), 'source': source}
