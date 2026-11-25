"""土石方资料清洗 · 平面几何：相似变换拟合、条带还原中心线、线段串接、抽稀、多边形判断。纯 Python，不依赖 numpy。"""
import math


# ---------- 相似变换 world = s·R·p + t，表示为 (a, b, tx, ty)：X = a·x − b·y + tx，Y = b·x + a·y + ty
def fit_similarity(src, dst):
    n = len(src)
    if n < 2:
        raise ValueError('控制点不足，至少需要 2 个')
    mx, my = sum(p[0] for p in src) / n, sum(p[1] for p in src) / n
    ux, uy = sum(p[0] for p in dst) / n, sum(p[1] for p in dst) / n
    sxx = sxy = den = 0.0
    for (x, y), (u, v) in zip(src, dst):
        x -= mx; y -= my; u -= ux; v -= uy
        sxx += x * u + y * v
        sxy += x * v - y * u
        den += x * x + y * y
    if den < 1e-12:
        raise ValueError('控制点重合，无法配准')
    a, b = sxx / den, sxy / den
    return (a, b, ux - (a * mx - b * my), uy - (b * mx + a * my))


def apply(t, p):
    a, b, tx, ty = t
    return (a * p[0] - b * p[1] + tx, b * p[0] + a * p[1] + ty)


def invert(t):
    a, b, tx, ty = t
    d = a * a + b * b
    ia, ib = a / d, -b / d
    return (ia, ib, -(ia * tx - ib * ty), -(ib * tx + ia * ty))


def compose(first, second):
    """先 first 后 second。"""
    a1, b1, x1, y1 = first
    a2, b2, x2, y2 = second
    return (a2 * a1 - b2 * b1, b2 * a1 + a2 * b1, a2 * x1 - b2 * y1 + x2, b2 * x1 + a2 * y1 + y2)


def scale_of(t):
    return math.hypot(t[0], t[1])


def rotation_of(t):
    return math.degrees(math.atan2(t[1], t[0]))


def residuals(t, src, dst):
    return [math.hypot(u - X, v - Y) for (X, Y), (u, v) in zip((apply(t, p) for p in src), dst)]


def robust_similarity(src, dst, rounds=4, k=3.0):
    """最小二乘拟合后反复剔除残差超过 k 倍中位数的点（至少保留一半）。返回 (变换, 保留下标, 残差)。"""
    keep = list(range(len(src)))
    t = fit_similarity(src, dst)
    for _ in range(rounds):
        res = residuals(t, [src[i] for i in keep], [dst[i] for i in keep])
        med = sorted(res)[len(res) // 2]
        limit = max(k * med, 1e-9)
        kept = [i for i, r in zip(keep, res) if r <= limit]
        if len(kept) == len(keep) or len(kept) < max(2, len(src) // 2):
            break
        keep = kept
        t = fit_similarity([src[i] for i in keep], [dst[i] for i in keep])
    return t, keep, residuals(t, [src[i] for i in keep], [dst[i] for i in keep])


# ---------- 网格索引：近邻查找
class Grid:
    def __init__(self, cell):
        self.cell, self.map = cell, {}

    def _key(self, x, y):
        return (math.floor(x / self.cell), math.floor(y / self.cell))

    def add(self, x, y, item):
        self.map.setdefault(self._key(x, y), []).append((x, y, item))

    def near(self, x, y, radius):
        cx, cy = self._key(x, y)
        reach = int(math.ceil(radius / self.cell))
        r2 = radius * radius
        out = []
        for i in range(cx - reach, cx + reach + 1):
            for j in range(cy - reach, cy + reach + 1):
                for px, py, item in self.map.get((i, j), ()):
                    d = (px - x) ** 2 + (py - y) ** 2
                    if d <= r2:
                        out.append((d, item))
        out.sort(key=lambda v: v[0])
        return out

    def nearest(self, x, y, radius):
        found = self.near(x, y, radius)
        return found[0] if found else None


# ---------- 折线工具
def length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def bbox(points):
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def dedupe(points, eps=1e-9):
    out = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    return out


def simplify(points, tolerance):
    """Douglas-Peucker 抽稀（迭代实现，避免长折线递归过深）。"""
    n = len(points)
    if n < 3 or tolerance <= 0:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = points[i]; bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, index = -1.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            d = abs(dx * (py - ay) - dy * (px - ax)) / norm if norm > 1e-12 else math.hypot(px - ax, py - ay)
            if d > worst:
                worst, index = d, k
        if worst > tolerance:
            keep[index] = True
            stack.append((i, index)); stack.append((index, j))
    return [p for p, flag in zip(points, keep) if flag]


def area(points):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2


def inside(polygon, x, y):
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def resample(points, step):
    """沿折线按 step 等距取点（含首尾）。"""
    out = [points[0]]
    carry = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg < 1e-12:
            continue
        d = step - carry
        while d <= seg:
            out.append((x1 + (x2 - x1) * d / seg, y1 + (y2 - y1) * d / seg))
            d += step
        carry = seg - (d - step)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


# ---------- 带宽度的线在 PDF 中被剖分成三角形条带：还原中心线
def strip_centerline(triangles):
    """triangles：同一条带上按顺序排列的三角形（各 3 个顶点）。相邻三角形共用一条边，条带的两侧顶点交替出现；
    中心线取各“横向边”的中点。返回折线；无法识别为条带时返回 None。"""
    if not triangles:
        return None
    if len(triangles) == 1:
        a, b, c = triangles[0]
        # 单个三角形：最短边视为宽度方向，中心线从该边中点到对顶点
        edges = sorted(((math.hypot(p[0] - q[0], p[1] - q[1]), p, q, r) for p, q, r in ((a, b, c), (b, c, a), (c, a, b))), key=lambda e: e[0])
        _, p, q, r = edges[0]
        return [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), r]
    # 顶点序列 v0 v1 v2 …：第 i 个三角形为 (v_i, v_{i+1}, v_{i+2})
    def same(p, q):
        return abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6
    first, second = triangles[0], triangles[1]
    shared = [p for p in first if any(same(p, q) for q in second)]
    if len(shared) != 2:
        return None
    start = next(p for p in first if not any(same(p, q) for q in shared))
    # 第二个三角形里不在第一个三角形中的顶点是 v3，它与 v2 相对：v1、v2 为共用边，v2 是与 v3 同侧相邻的那个
    verts = [start]
    new = next((q for q in second if not any(same(q, p) for p in first)), None)
    if new is None:
        return None
    third = triangles[2] if len(triangles) > 2 else None
    if third is not None:
        # v2 同时属于第三个三角形，v1 不属于
        in_third = [p for p in shared if any(same(p, q) for q in third)]
        if len(in_third) == 1:
            v2 = in_third[0]
            v1 = next(p for p in shared if p is not v2)
        else:
            v1, v2 = shared
    else:
        v1, v2 = shared
    verts += [v1, v2, new]
    for k in range(2, len(triangles)):
        tri = triangles[k]
        nxt = next((q for q in tri if not any(same(q, p) for p in verts[-3:])), None)
        if nxt is None or sum(1 for q in tri if any(same(q, p) for p in verts[-2:])) < 2:
            return None
        verts.append(nxt)
    # 条带两侧交替：横向边为 (v0,v1)、(v2,v3)…；顶点数为奇数时末点单独成点
    line = []
    for i in range(0, len(verts) - 1, 2):
        line.append(((verts[i][0] + verts[i + 1][0]) / 2, (verts[i][1] + verts[i + 1][1]) / 2))
    if len(verts) % 2:
        line.append(verts[-1])
    return dedupe(line)


def path_triangles(path):
    """路径的各子路径若都是闭合三角形则返回三角形列表，否则返回 None。"""
    triangles = []
    for sub, closed in zip(path['subs'], path['closed']):
        pts = dedupe(sub)
        if len(pts) == 4 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
            pts = pts[:3]
        if len(pts) != 3:
            return None
        triangles.append(pts)
    return triangles or None


# ---------- 把首尾相接的线段串成长折线
def chain(lines, tolerance):
    """lines：折线列表。端点距离不超过 tolerance 的折线首尾相接成一条；每个端点只接一次，接最近的。
    返回 [(折线, 是否闭合)]。"""
    lines = [list(l) for l in lines if len(l) >= 2]
    grid = Grid(max(tolerance, 1e-6) * 2)
    for i, line in enumerate(lines):
        grid.add(line[0][0], line[0][1], (i, 0))
        grid.add(line[-1][0], line[-1][1], (i, 1))
    used = [False] * len(lines)
    taken = set()
    out = []
    # 先从长线出发，短碎线接在长线上
    for seed in sorted(range(len(lines)), key=lambda i: -len(lines[i])):
        if used[seed]:
            continue
        used[seed] = True
        taken.update(((seed, 0), (seed, 1)))
        current = list(lines[seed])
        closed = False
        for forward in (True, False):
            while True:
                x, y = current[-1] if forward else current[0]
                found = next((item for d, item in grid.near(x, y, tolerance) if item not in taken and not used[item[0]]), None)
                if found is None:
                    break
                i, end = found
                used[i] = True
                taken.update(((i, 0), (i, 1)))
                piece = lines[i] if end == 0 else lines[i][::-1]
                if forward:
                    current.extend(piece[1:] if math.hypot(piece[0][0] - x, piece[0][1] - y) < 1e-9 else piece)
                else:
                    piece = piece[::-1]
                    current[:0] = piece[:-1] if math.hypot(piece[-1][0] - x, piece[-1][1] - y) < 1e-9 else piece
        if len(current) > 3 and math.hypot(current[0][0] - current[-1][0], current[0][1] - current[-1][1]) <= tolerance:
            closed = True
        out.append((dedupe(current), closed))
    return out
