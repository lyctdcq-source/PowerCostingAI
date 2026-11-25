"""土石方资料清洗 · 不规则三角网：由离散高程点构网（Bowyer-Watson），在任意位置按三角面线性内插高程。纯 Python。

地形图的等高线本身就是测图软件由这些高程点构网内插出来的，所以用同样的方法在等高线上内插，得到的高程应当
落在等高距的整数倍附近；据此给没有标注高程的等高线定高程。"""
import math


class Tin:
    def __init__(self, points):
        """points：[(x, y, z)]。重合点（平面距离小于 1 mm）只留第一个。"""
        seen, pts = set(), []
        for x, y, z in points:
            key = (round(x, 3), round(y, 3))
            if key not in seen:
                seen.add(key)
                pts.append((float(x), float(y), float(z)))
        if len(pts) < 3:
            raise ValueError('高程点不足 3 个，无法构网')
        self.x0 = sum(p[0] for p in pts) / len(pts)
        self.y0 = sum(p[1] for p in pts) / len(pts)
        # 以形心为原点计算，避免大坐标的舍入误差
        self.pts = [(x - self.x0, y - self.y0, z) for x, y, z in pts]
        self.triangles = self._triangulate()
        self._index()

    def _triangulate(self):
        pts = self.pts
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 20
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        n = len(pts)
        verts = pts + [(cx - span, cy - span, 0), (cx + span, cy - span, 0), (cx, cy + span, 0)]

        def circle(a, b, c):
            ax, ay = verts[a][0], verts[a][1]; bx, by = verts[b][0], verts[b][1]; qx, qy = verts[c][0], verts[c][1]
            d = 2 * (ax * (by - qy) + bx * (qy - ay) + qx * (ay - by))
            if abs(d) < 1e-12:
                return None
            ux = ((ax * ax + ay * ay) * (by - qy) + (bx * bx + by * by) * (qy - ay) + (qx * qx + qy * qy) * (ay - by)) / d
            uy = ((ax * ax + ay * ay) * (qx - bx) + (bx * bx + by * by) * (ax - qx) + (qx * qx + qy * qy) * (bx - ax)) / d
            return ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2
        tris = {(n, n + 1, n + 2): circle(n, n + 1, n + 2)}
        # 按 x 排序插入，外接圆已完全落在左侧的三角形不再参与判断
        done = []
        for i in sorted(range(n), key=lambda k: pts[k][0]):
            px, py = pts[i][0], pts[i][1]
            bad, edges = [], {}
            for tri, cc in list(tris.items()):
                if cc is None:
                    continue
                ux, uy, r2 = cc
                if ux + math.sqrt(r2) < px - 1e-9:
                    done.append(tri); del tris[tri]
                    continue
                if (px - ux) ** 2 + (py - uy) ** 2 <= r2 * (1 + 1e-12):
                    bad.append(tri)
            for tri in bad:
                del tris[tri]
                for e in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                    key = (min(e), max(e))
                    edges[key] = edges.get(key, 0) + 1
            for (a, b), count in edges.items():
                if count == 1:
                    tri = (a, b, i)
                    cc = circle(a, b, i)
                    if cc is not None:
                        tris[tri] = cc
        return [t for t in done + list(tris) if max(t) < n]

    def _index(self):
        pts = self.pts
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        self.minx, self.miny = min(xs), min(ys)
        area = max((max(xs) - self.minx) * (max(ys) - self.miny), 1e-9)
        self.cell = max(math.sqrt(area / max(len(self.triangles), 1)) * 1.5, 1e-6)
        self.bins = {}
        for tri in self.triangles:
            tx = [pts[v][0] for v in tri]; ty = [pts[v][1] for v in tri]
            for i in range(int((min(tx) - self.minx) // self.cell), int((max(tx) - self.minx) // self.cell) + 1):
                for j in range(int((min(ty) - self.miny) // self.cell), int((max(ty) - self.miny) // self.cell) + 1):
                    self.bins.setdefault((i, j), []).append(tri)

    def z(self, x, y):
        """(x, y) 处的内插高程；在三角网范围之外返回 None。"""
        x -= self.x0; y -= self.y0
        pts = self.pts
        for a, b, c in self.bins.get((int((x - self.minx) // self.cell), int((y - self.miny) // self.cell)), ()):
            ax, ay, az = pts[a]; bx, by, bz = pts[b]; qx, qy, qz = pts[c]
            det = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
            if abs(det) < 1e-12:
                continue
            l1 = ((by - qy) * (x - qx) + (qx - bx) * (y - qy)) / det
            l2 = ((qy - ay) * (x - qx) + (ax - qx) * (y - qy)) / det
            l3 = 1 - l1 - l2
            if l1 >= -1e-9 and l2 >= -1e-9 and l3 >= -1e-9:
                return l1 * az + l2 * bz + l3 * qz
        return None
