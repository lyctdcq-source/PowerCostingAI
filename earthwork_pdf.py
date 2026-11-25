"""土石方资料清洗 · PDF 矢量提取：用 pdfium 按原 CAD 图层取出页面上的路径和文字（本机处理，不调用模型）。

CAD 出图的 PDF 把图层保存为可选内容组，每个页面对象带有所属图层名；路径取控制点（贝塞尔曲线取样为折线），
文字取文本对象的内容、位置和方向。坐标为 PDF 页面坐标（点，原点在左下角）。"""
import ctypes
import math

BEZIER_STEPS = 6


def _raw():
    import pypdfium2.raw as raw
    return raw


def open_pdf(path):
    import pypdfium2 as pdfium
    return pdfium.PdfDocument(str(path))


def _layer(R, obj, cache):
    """页面对象所属图层：OC 标记属性字典中的 Name（UTF-16BE 带 BOM 或 PDFDocEncoding）。"""
    for i in range(R.FPDFPageObj_CountMarks(obj)):
        mark = R.FPDFPageObj_GetMark(obj, i)
        buf, size = cache
        length = ctypes.c_ulong()
        if R.FPDFPageObjMark_GetParamBlobValue(mark, b'Name', buf, size, length):
            data = bytes(buf[:length.value])
            return data[2:].decode('utf-16-be', 'replace') if data[:2] == b'\xfe\xff' else data.decode('latin1')
    return ''


def _mul(m, n):
    """先按 m 再按 n 变换的复合矩阵，矩阵为 (a, b, c, d, e, f)。"""
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
            m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def page_text(pdf, index):
    page = pdf[index]
    textpage = page.get_textpage()
    try:
        return textpage.get_text_bounded()
    finally:
        textpage.close(); page.close()


def extract_page(pdf, index, layers=None, progress=None):
    """返回 {'size': (宽, 高), 'paths': [...], 'texts': [...], 'layers': {图层: 对象数}}。

    path：{'layer', 'subs': [[(x, y), ...], ...], 'closed': [bool, ...], 'fill': bool, 'stroke': bool, 'width': 线宽, 'color': (r, g, b)}
    text：{'layer', 'text', 'x', 'y', 'angle': 弧度, 'size': 字高, 'box': (左, 下, 右, 上)}
    layers 给定时只取这些图层的路径（大图可省时间），文字总是全取。"""
    R = _raw()
    page = pdf[index]
    textpage = page.get_textpage()
    width, height = page.get_size()
    paths, texts, counts = [], [], {}
    cache = ((ctypes.c_ubyte * 512)(), 512)
    matrix = R.FS_MATRIX()
    fx, fy = ctypes.c_float(), ctypes.c_float()
    wide = (ctypes.c_ushort * 2048)()

    def walk(count, get, outer, depth):
        for i in range(count):
            obj = get(i)
            kind = R.FPDFPageObj_GetType(obj)
            if kind == R.FPDF_PAGEOBJ_FORM:
                if depth < 4:
                    R.FPDFPageObj_GetMatrix(obj, matrix)
                    inner = _mul((matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f), outer)
                    walk(R.FPDFFormObj_CountObjects(obj), lambda j, o=obj: R.FPDFFormObj_GetObject(o, j), inner, depth + 1)
                continue
            if kind not in (R.FPDF_PAGEOBJ_PATH, R.FPDF_PAGEOBJ_TEXT):
                continue
            layer = _layer(R, obj, cache)
            counts[layer] = counts.get(layer, 0) + 1
            if layers is not None and kind == R.FPDF_PAGEOBJ_PATH and layer not in layers:
                continue
            R.FPDFPageObj_GetMatrix(obj, matrix)
            a, b, c, d, e, f = _mul((matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f), outer)
            if kind == R.FPDF_PAGEOBJ_TEXT:
                n = R.FPDFTextObj_GetText(obj, textpage.raw, wide, 4096)
                text = bytes(wide)[:max(0, n - 2)].decode('utf-16-le', 'replace').strip() if n else ''
                if not text:
                    continue
                size = ctypes.c_float()
                R.FPDFTextObj_GetFontSize(obj, size)
                l, bt, r, t = (ctypes.c_float() for _ in range(4))
                R.FPDFPageObj_GetBounds(obj, l, bt, r, t)
                texts.append({'layer': layer, 'text': text, 'x': e, 'y': f, 'angle': math.atan2(b, a),
                              'size': size.value * math.hypot(a, b), 'box': (l.value, bt.value, r.value, t.value)})
                continue
            fill, stroke = ctypes.c_int(), ctypes.c_int()
            R.FPDFPath_GetDrawMode(obj, fill, stroke)
            subs, closed, current, pending = [], [], None, []
            for j in range(R.FPDFPath_CountSegments(obj)):
                seg = R.FPDFPath_GetPathSegment(obj, j)
                R.FPDFPathSegment_GetPoint(seg, fx, fy)
                x, y = a * fx.value + c * fy.value + e, b * fx.value + d * fy.value + f
                kind_seg = R.FPDFPathSegment_GetType(seg)
                if kind_seg == R.FPDF_SEGMENT_MOVETO:
                    current = [(x, y)]
                    subs.append(current); closed.append(False); pending = []
                elif current is None:
                    continue
                elif kind_seg == R.FPDF_SEGMENT_BEZIERTO:
                    pending.append((x, y))
                    if len(pending) == 3:
                        p0, (p1, p2, p3) = current[-1], pending
                        for k in range(1, BEZIER_STEPS + 1):
                            t = k / BEZIER_STEPS; u = 1 - t
                            current.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                                            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
                        pending = []
                else:
                    current.append((x, y))
                if R.FPDFPathSegment_GetClose(seg) and current is not None:
                    closed[-1] = True
            if not subs:
                continue
            stroke_width = ctypes.c_float()
            R.FPDFPageObj_GetStrokeWidth(obj, stroke_width)
            r, g, bl, al = (ctypes.c_uint() for _ in range(4))
            (R.FPDFPageObj_GetStrokeColor if stroke.value else R.FPDFPageObj_GetFillColor)(obj, r, g, bl, al)
            paths.append({'layer': layer, 'subs': subs, 'closed': closed, 'fill': bool(fill.value), 'stroke': bool(stroke.value),
                          'width': stroke_width.value * math.sqrt(abs(a * d - b * c)), 'color': (r.value, g.value, bl.value)})
            if progress and len(paths) % 5000 == 0:
                progress(len(paths))

    try:
        walk(R.FPDFPage_CountObjects(page.raw), lambda j: R.FPDFPage_GetObject(page.raw, j), IDENTITY, 0)
    finally:
        textpage.close(); page.close()
    return {'size': (width, height), 'paths': paths, 'texts': texts, 'layers': counts}


def render_page(pdf, index, max_side=2200):
    """页面渲染为 PNG 字节，供原始资料预览。"""
    import io
    page = pdf[index]
    try:
        width, height = page.get_size()
        scale = min(4.0, max_side / max(width, height))
        image = page.render(scale=scale).to_pil()
        out = io.BytesIO()
        image.save(out, format='PNG', optimize=True)
        return out.getvalue(), (round(width * scale), round(height * scale))
    finally:
        page.close()
