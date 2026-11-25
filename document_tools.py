"""Local document skills; imported documents are data, never executable instructions."""
import base64
import io
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET


def extract_file(path):
    path=Path(path)
    ext=path.suffix.lower()
    chunks=[]
    def add(location,content):
        content=str(content).strip()
        if content:
            for i in range(0,len(content),3000):
                chunks.append({'location':location+(f' · 片段{i//3000+1}' if len(content)>3000 else ''),'content':content[i:i+3000]})
    if ext=='.docx':
        with zipfile.ZipFile(path) as z:
            if sum(x.file_size for x in z.infolist())>80_000_000: raise ValueError('文档解压体积过大')
            root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        buffer=[]; start=1
        for i,p in enumerate(root.findall('.//w:p',ns),1):
            text=''.join(p.itertext()) if False else ''.join(t.text or '' for t in p.findall('.//w:t',ns))
            if sum(map(len,buffer))+len(text)>2400:
                add(f'段落{start}～{i-1}','\n'.join(buffer));buffer=[];start=i
            buffer.append(text)
        add(f'段落{start}～{i if "i" in locals() else 0}','\n'.join(buffer))
    elif ext in ('.xlsx','.xls'):
        from cleaning import sheets
        for title, rows in sheets(path):
            for start in range(0,len(rows),20):
                lines=[' | '.join('' if c is None else str(c) for c in row) for row in rows[start:start+20]]
                add(f'工作表 {title} · 行{start+1}～{min(start+20,len(rows))}','\n'.join(lines))
    elif ext=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(path)
        if len(reader.pages)>500: raise ValueError('PDF超过500页，请拆分')
        for i,page in enumerate(reader.pages,1): add(f'第{i}页',page.extract_text() or '')
    elif ext in ('.txt','.md','.csv','.json'):
        try: text=path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError: text=path.read_text(encoding='gb18030')
        for i in range(0,len(text),2500): add(f'字符{i+1}～{min(i+2500,len(text))}',text[i:i+2500])
    else:
        raise ValueError('支持 DOCX、XLSX、XLS、PDF、TXT、CSV、JSON；旧版DOC请另存为DOCX，DWG需转换为PDF。')
    if len(chunks)>600: raise ValueError('提取结果过大，请分批导入')
    return chunks


def drawing_images(path):
    path=Path(path)
    if path.suffix.lower()=='.pdf':
        import pypdfium2 as pdfium
        document=pdfium.PdfDocument(str(path))
        pages=[]
        try:
            for i in range(min(3,len(document))):
                page=document[i]
                scale=min(2,1800/max(page.get_size()))
                bitmap=page.render(scale=scale)
                im=bitmap.to_pil().convert('RGB')
                data=io.BytesIO(); im.save(data,format='JPEG',quality=85)
                pages.append((i+1,base64.b64encode(data.getvalue()).decode(),'image/jpeg'))
                bitmap.close();page.close()
        finally: document.close()
        return pages
    if path.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
        from PIL import Image
        with Image.open(path) as im:
            im=im.convert('RGB'); im.thumbnail((2000,2000))
            data=io.BytesIO();im.save(data,format='JPEG',quality=85)
        return [(1,base64.b64encode(data.getvalue()).decode(),'image/jpeg')]
    raise ValueError('视觉识别支持PDF前3页或PNG/JPG/WebP图片；DWG请先转换为PDF。')


def write_xlsx(rows, path, title='核对明细'):
    import openpyxl
    from openpyxl.styles import Font,PatternFill,Alignment
    book=openpyxl.Workbook();sheet=book.active;sheet.title=title[:31]
    keys=list(dict.fromkeys(k for row in rows for k in row))
    sheet.append(keys)
    for row in rows:
        values=[]
        for key in keys:
            v=row.get(key,'')
            if isinstance(v,(dict,list)): v=str(v)
            if isinstance(v,str) and v.startswith(('=','+','-','@')):v="'"+v
            values.append(v)
        sheet.append(values)
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='347457')
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for column in sheet.columns:
        letter=column[0].column_letter
        sheet.column_dimensions[letter].width=min(60,max(12,max((len(str(c.value or '')) for c in column),default=10)*1.3))
        for c in column:c.alignment=Alignment(vertical='top',wrap_text=True)
    book.save(path)


def write_results_xlsx(state, path):
    """结果导出：数量评审明细、结论汇总、价格评审、清洗后的造价与清册数据，各占一张工作表。"""
    import io
    import openpyxl
    from collections import Counter
    from openpyxl.styles import Font, PatternFill, Alignment
    rows = state.get('rows') or []
    dataset = state.get('dataset') or {}
    anchors = {str(r['source_id']) for r in rows if r.get('source_id') and _set_anchor(r)}
    sheets = []
    detail = [DETAIL_HEAD] + [detail_row(r, anchors) for r in rows]
    if rows:
        counts = Counter(result_category(r, anchors) for r in rows)
        sheets.append(('结论汇总', [['匹配情况', '条数', '占比']] + [[k, counts[k], _percent(counts[k], len(rows))] for k in REPORT_CATEGORIES if counts[k]]
                       + [['合计', len(rows), '100%']]))
        sheets.append(('数量评审明细', detail))
    prices = state.get('price_results') or []
    if prices:
        sheets.append(('价格评审', [['造价序号', '物资名称', '型号规格', '单位', '造价单价', '信息价', '信息价期次', '偏差率(%)', '结论', '依据']]
                       + [[x['item'].get('source_id', ''), x['item'].get('name', ''), x['item'].get('spec', ''), x['item'].get('unit', ''),
                           x['item'].get('price', ''), (x.get('reference') or {}).get('price', ''), (x.get('reference') or {}).get('period', ''),
                           '' if x.get('delta') is None else round(x['delta'], 2), x.get('band', ''), x.get('reason', '')] for x in prices]))
    for side, title, keys in (('sources', '造价清洗数据', ('Estimation_Name', '型号及规范', 'Estimation_Unit', 'Estimation_Quantity', 'Estimation_Price', 'Supply_Type')),
                              ('targets', '清册清洗数据', ('设备材料名称', '型号及规范', '单位', '数量', '备注'))):
        items = dataset.get(side) or []
        if items:
            head = ['序号', '层级', '名称', '型号及规范', '单位', '数量'] + (['单价', '供货类型'] if side == 'sources' else ['备注']) + ['来源文件', '工作表', '行号']
            sheets.append((title, [head] + [[x.get('序号', ''), '/'.join(x.get('层级路径') or []) or x.get('层级', '')] + [x.get(k, '') for k in keys]
                                           + [(x.get('_source') or {}).get('file', ''), (x.get('_source') or {}).get('sheet', ''), (x.get('_source') or {}).get('row', '')]
                                           for x in items]))
    if not sheets:
        raise ValueError('当前没有可导出的结果，请先完成资料清洗或数量评审')
    save_sheets(sheets, path)


DETAIL_HEAD = ['匹配情况', '层级', '造价序号', '清册序号', '造价名称', '清册名称', '清册型号及规范', '造价数量', '造价单位',
               '清册数量', '清册单位', '结论', '依据', '人工确认', '复核意见']


def detail_row(r, anchors):
    return [result_category(r, anchors), r.get('group', ''), r.get('source_id', ''), r.get('target_id', ''), r.get('name', ''),
            r.get('target', ''), r.get('target_spec', ''), r.get('quantity', ''), r.get('unit', ''), r.get('target_quantity', ''),
            r.get('target_unit', ''), r.get('band', ''), r.get('reason', ''), '是' if r.get('confirmed') else '否', r.get('human_note', '')]


def write_selection_xlsx(rows, all_rows, path, info):
    """按条件导出的匹配结果：“导出明细”逐条列出，“导出说明”写明工程、时间、筛选条件、条数和结论分布。"""
    from collections import Counter
    anchors = {str(r['source_id']) for r in all_rows if r.get('source_id') and _set_anchor(r)}
    counts = Counter(result_category(r, anchors) for r in rows)
    notes = [['项目', '内容'], *[[k, v] for k, v in info.items()],
             ['导出条数', len(rows)], ['结论分布', '；'.join(f'{k} {counts[k]} 条' for k in REPORT_CATEGORIES if counts[k])]]
    save_sheets([('导出明细', [DETAIL_HEAD] + [detail_row(r, anchors) for r in rows]), ('导出说明', notes)], path)
    return dict(counts)


def docx_blocks(path, limit=3000):
    """Word 文档按正文顺序转为标题、段落和表格，供平台内只读预览；不含页眉页脚、图片和域代码。"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc, blocks = Document(str(path)), []
    for element in doc.element.body.iterchildren():
        if len(blocks) >= limit:
            blocks.append({'type': 'note', 'text': f'文档较长，预览只显示前 {limit} 段，完整内容请下载查看。'})
            break
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            para = Paragraph(element, doc)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name if para.style is not None else '') or ''
            level = next((int(c) for c in style if c.isdigit()), 1) if style.lower().startswith(('heading', 'title')) or style.startswith('标题') else 0
            blocks.append({'type': 'heading', 'level': min(level, 4), 'text': text} if level else {'type': 'para', 'text': text})
        elif tag == 'tbl':
            rows = [[cell.text.strip() for cell in row.cells] for row in Table(element, doc).rows]
            blocks.append({'type': 'table', 'rows': rows[:500]})
    return blocks


def save_sheets(sheets, path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, table in sheets:
        sheet = book.create_sheet(title)
        for row in table:
            sheet.append(["'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in row])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='347457')
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = max((len(str(c.value or '')) for c in column[:200]), default=8)
            sheet.column_dimensions[column[0].column_letter].width = min(48, max(8, width * 1.3))
            for c in column:
                c.alignment = Alignment(vertical='top', wrap_text=True)
    book.save(path)


# 成果报告的结论分类与平台结果表一致：先分出两侧未匹配，再按评审结论区分已匹配物资
REPORT_CATEGORIES=('一致','基本一致','部分一致','不一致','待复核','评审未完成','清册未匹配','造价未匹配','不参与核对')
MATCHED=('一致','基本一致','部分一致','不一致','待复核','评审未完成')


def _set_anchor(row):
    return bool(row.get('covers') or row.get('covers_sources') or row.get('set_pairs'))


def result_category(row,anchors):
    band=row.get('band') or '待复核'
    if band=='不参与核对':
        return band
    if row.get('covered_by'):
        # 成套项组成件与成套项多对多匹配；所属成套项不在结果中的按未匹配
        return band if str(row['covered_by']) in anchors else ('清册未匹配' if row.get('target_id') else '造价未匹配')
    missing=band in ('不一致','待复核')
    if row.get('target_id') and not row.get('source_id') and missing:
        return '清册未匹配'
    if row.get('source_id') and not row.get('target_id') and not _set_anchor(row) and missing:
        return '造价未匹配'
    return band if band in MATCHED else '待复核'


def _short(text,limit):
    text=re.sub(r'\s+',' ',str(text or '')).strip()
    return text if len(text)<=limit else text[:limit-1]+'…'


def _percent(part,whole):
    return f'{part/whole*100:.1f}%' if whole else '—'


def _finding(row):
    return re.sub(r'^[^。]*。','',str(row.get('reason') or ''),count=1)


def write_report(state,path):
    """成果报告：对匹配结果做总结性说明（范围与方法、总体情况、主要问题、结论与建议），不逐条罗列明细。"""
    import time
    from collections import Counter
    from docx import Document
    from docx.shared import Pt
    rows=state['rows']
    dataset=state.get('dataset') or {}
    anchors={str(r['source_id']) for r in rows if r.get('source_id') and _set_anchor(r)}
    cats=[result_category(r,anchors) for r in rows]
    counts=Counter(cats)
    total=len(rows)
    matched=sum(counts[k] for k in MATCHED)
    good=counts['一致']+counts['基本一致']
    issues=[r for r,c in zip(rows,cats) if c in ('不一致','部分一致')]
    top_parent=lambda x:((x.get('层级路径') or [x.get('层级')])[0] or '未分组')
    target_parent={str(x.get('序号')):top_parent(x) for x in dataset.get('targets',[])}
    source_parent={str(x.get('序号')):top_parent(x) for x in dataset.get('sources',[])}
    def parent(r):
        return target_parent.get(str(r.get('target_id')),'') if r.get('target_id') else source_parent.get(str(r.get('source_id')),'')
    def concentrated(items):
        return '、'.join(f'{name}（{n} 条）' for name,n in Counter(parent(r) or '未分组' for r in items).most_common(4))

    doc=Document()
    normal=doc.styles['Normal'];normal.font.name='Microsoft YaHei';normal.font.size=Pt(10.5)
    doc.add_heading(f'{state.get("project") or "工程"} 物资核对成果报告',0)
    doc.add_paragraph(f'生成时间：{time.strftime("%Y-%m-%d %H:%M")}。本报告由平台根据匹配结果自动汇总，结论为待专业复核的草稿，逐条明细与依据请在平台结果表中查看。')

    doc.add_heading('一、核对范围与方法',1)
    sets=[r for r in rows if _set_anchor(r)]
    members=sum(1 for r in rows if r.get('covered_by') and str(r['covered_by']) in anchors)
    doc.add_paragraph(f'以设计清册为基准（{len(dataset.get("targets",[]))} 条物资），与可研估算（{len(dataset.get("sources",[]))} 条物资）逐项匹配，共形成 {total} 条核对结果'
                      +(f'，核对范围限定为“{state["scope"]}”' if state.get('scope') else '')+'。')
    doc.add_paragraph('匹配由在线大模型结合规则校验完成：名称、型号规格相符的物资建立对应，数量按单位折算后比对，结论分为一致、基本一致、部分一致、不一致四档。'
                      +(f'造价中按整套计价的成套项与清册组成件按多对多整体核对，本次共 {len(sets)} 个成套对应组、涉及组成件 {members} 条，组成件与成套项取同一结论。' if sets else '本次未识别到按整套计价的成套对应组。'))
    files=sorted({src.get('file') for r in rows for src in (r.get('evidence'),r.get('source')) if isinstance(src,dict) and src.get('file')})
    if files:
        doc.add_paragraph('资料来源：'+'；'.join(files[:12])+(f' 等 {len(files)} 个文件' if len(files)>12 else '')+'。')

    doc.add_heading('二、总体结果',1)
    table=doc.add_table(rows=1,cols=3);table.style='Light Grid Accent 1'
    for cell,text in zip(table.rows[0].cells,('类别','条数','占比')):cell.text=text
    for key in REPORT_CATEGORIES:
        if counts[key]:
            cells=table.add_row().cells;cells[0].text=key;cells[1].text=str(counts[key]);cells[2].text=_percent(counts[key],total)
    cells=table.add_row().cells;cells[0].text='合计';cells[1].text=str(total);cells[2].text='100%'
    doc.add_paragraph()
    doc.add_paragraph(f'两侧已匹配 {matched} 条，其中一致与基本一致 {good} 条，占已匹配条目的 {_percent(good,matched)}；'
                      f'部分一致 {counts["部分一致"]} 条、不一致 {counts["不一致"]} 条、待复核 {counts["待复核"]} 条。'
                      f'清册有而造价未找到对应的 {counts["清册未匹配"]} 条，造价有而清册未找到对应的 {counts["造价未匹配"]} 条'
                      +(f'；另有 {counts["不参与核对"]} 条为服务费用、按项包干等不参与数量核对的条目' if counts['不参与核对'] else '')+'。')

    doc.add_heading('三、主要问题',1)
    if issues:
        doc.add_paragraph(f'1. 数量或规格存在差异的已匹配物资共 {len(issues)} 条（部分一致 {counts["部分一致"]} 条、不一致 {counts["不一致"]} 条），主要集中在：{concentrated(issues)}。典型条目：')
        order={'不一致':0,'部分一致':1}
        for r in sorted(issues,key=lambda r:order.get(r.get('band'),2))[:8]:
            doc.add_paragraph(f'清册“{_short(r.get("target"),24)}”{r.get("target_quantity","")}{r.get("target_unit","")} ↔ '
                              f'造价“{_short(r.get("name"),24)}”{r.get("quantity","")}{r.get("unit","")}：{_short(_finding(r),70)}',style='List Bullet')
        if len(issues)>8:
            doc.add_paragraph(f'其余 {len(issues)-8} 条见平台结果表“部分一致 / 不一致”筛选。')
    else:
        doc.add_paragraph('1. 已匹配物资中未发现数量或规格存在明显差异的条目。')
    for no,key,meaning in ((2,'清册未匹配','清册中列出但可研估算中未找到对应，需确认是否为造价漏项，或已含在造价其他计价项内'),
                           (3,'造价未匹配','可研估算中列出但清册中未找到对应，需确认是否为清册以外的费用项，或清册漏列')):
        items=[r for r,c in zip(rows,cats) if c==key]
        if not items:
            doc.add_paragraph(f'{no}. {key}：无。')
            continue
        names=list(dict.fromkeys(_short(r.get('target') if r.get('target_id') else r.get('name'),16) for r in items))
        doc.add_paragraph(f'{no}. {key} {len(items)} 条，{meaning}。主要集中在：{concentrated(items)}；例如'+'、'.join(names[:8])+('等' if len(names)>8 else '')+'。')
    if counts['待复核']:
        doc.add_paragraph(f'4. 另有 {counts["待复核"]} 条模型无法确定结论，标为待复核，需人工判定。')
    if counts['评审未完成']:
        doc.add_paragraph(f'{5 if counts["待复核"] else 4}. 另有 {counts["评审未完成"]} 条模型评审重试后仍未通过程序校验，标为评审未完成（处理状态，不是结论），需人工判定。')

    doc.add_heading('四、结论与建议',1)
    unmatched=counts['清册未匹配']+counts['造价未匹配']
    ratio=good/matched if matched else 0
    level='较好' if ratio>=0.85 else '一般' if ratio>=0.6 else '较差'
    doc.add_paragraph(f'本工程可研估算与设计清册的物资对应情况{level}：已匹配物资中一致与基本一致占 {_percent(good,matched)}，'
                      f'两侧未匹配合计 {unmatched} 条，占全部核对结果的 {_percent(unmatched,total)}。')
    advice=[]
    if issues:
        advice.append(f'优先复核 {len(issues)} 条数量或规格差异，确认差异来源（设计变更、计量口径或单位折算），必要时调整造价数量。')
    if counts['清册未匹配']:
        advice.append(f'逐项确认清册未匹配的 {counts["清册未匹配"]} 条物资是否在造价中漏列，或已含于其他计价项。')
    if counts['造价未匹配']:
        advice.append(f'核实造价未匹配的 {counts["造价未匹配"]} 条是否属于清册以外的费用项，或需在清册中补列。')
    if sets:
        advice.append('成套对应组已按整体核对，配置差异在结论中提示，建议结合成套项单价核查组成件是否齐全。')
    advice.append(f'已人工确认 {sum(1 for r in rows if r.get("confirmed"))} 条，其余为模型结论草稿，正式使用前须由专业人员复核。')
    for item in advice:
        doc.add_paragraph(item,style='List Number')
    doc.save(path)
