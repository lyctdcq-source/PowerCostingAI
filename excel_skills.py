"""Application Excel skills: native-free reading, paged viewing, copy editing."""
import datetime
import json
import math
import shutil
import uuid
from pathlib import Path
import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill


def scalar(value):
    if value is None: return ''
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)): return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value): return str(value)
    return value if isinstance(value, (str, int, float, bool)) else str(value)


class ExcelSkills:
    def __init__(self, data, skills):
        self.output = Path(data) / 'outputs'
        self.output.mkdir(exist_ok=True, parents=True)
        self.skills = skills

    def path(self, item):
        path = (self.output / item['id']).resolve()
        if path.parent != self.output.resolve() or not path.is_file(): raise ValueError('表格文件不存在')
        return path

    def register(self, path, name, kind, **extra):
        return dict(id=path.name, name=name, kind=kind, **extra)

    def snapshot(self, source, relative):
        source = Path(source)
        if not self.skills.call('excel_read', 'allow_format', source.suffix.lower()): raise ValueError('不支持的表格格式')
        if source.stat().st_size > 100_000_000: raise ValueError('表格超过100MB，请拆分后读取')
        target=self.output/(uuid.uuid4().hex+source.suffix.lower())
        shutil.copyfile(source,target)
        return self.register(target, source.name, 'original', source=relative)

    def cleaned(self, rows, name):
        path=self.output/(uuid.uuid4().hex+'.xlsx')
        book=openpyxl.Workbook();sheet=book.active;sheet.title='清洗结果'
        headers=list(dict.fromkeys(k for row in rows for k in row if not k.startswith('_')))
        headers+=['来源文件','来源工作表','来源行号','数量列']
        sheet.append(headers)
        for row in rows:
            source=row.get('_source') or {}
            values=[row.get(k,'') for k in headers[:-4]]+[source.get('file',''),source.get('sheet',''),source.get('row',''),source.get('quantity_column','')]
            sheet.append([json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else scalar(v) for v in values])
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value,str): cell.data_type='s'
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]: cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='334155')
        for column in sheet.columns: sheet.column_dimensions[column[0].column_letter].width=25
        book.save(path);book.close()
        return self.register(path,name,'cleaned')

    def prepare(self, dataset):
        root=Path(dataset['origin']).resolve();items=[]
        # Preserve every input workbook, including sheets excluded by the cleaning plan.
        for file in dataset.get('files',[]):
            relative=file.get('path','');source=(root/relative).resolve()
            if not source.is_relative_to(root): raise ValueError('原表路径超出工程目录')
            if source.suffix.lower() in ('.xls','.xlsx') and source.is_file(): items.append(self.snapshot(source,relative))
        items += [self.cleaned(dataset['sources'],'可研估算_清洗后.xlsx'),self.cleaned(dataset['targets'],'设计清册_清洗后.xlsx')]
        return items

    def view(self, item, sheet_name=None, row=1, column=1):
        path=self.path(item);row=int(row);column=int(column)
        if row<1 or column<1: raise ValueError('行列号须从1开始')
        size=max(10,min(200,int(self.skills.call('excel_view','page_size'))));width=40
        if path.suffix=='.xls':
            import xlrd
            book=xlrd.open_workbook(str(path),formatting_info=True)
            try:
                sheets=[dict(name=s.name,rows=s.nrows,columns=s.ncols) for s in book.sheets()]
                sheet=book.sheet_by_name(sheet_name or sheets[0]['name']);merges=[[a+1,b,c+1,d] for a,b,c,d in sheet.merged_cells]
                total,cols=sheet.nrows,sheet.ncols;data=[]
                for r in range(row-1,min(total,row-1+size)):
                    values=[]
                    for c in range(column-1,min(cols,column-1+width)):
                        cell=sheet.cell(r,c);value=cell.value
                        if cell.ctype==xlrd.XL_CELL_DATE:
                            value=xlrd.xldate_as_datetime(value,book.datemode)
                        values.append(dict(value=scalar(value),formula=''))
                    data.append(values)
                title=sheet.name
            finally: book.release_resources()
        else:
            book=openpyxl.load_workbook(path,data_only=False);cached=openpyxl.load_workbook(path,data_only=True)
            try:
                sheets=[dict(name=s.title,rows=s.max_row,columns=s.max_column) for s in book]
                sheet=book[sheet_name or sheets[0]['name']];total,cols=sheet.max_row,sheet.max_column;title=sheet.title
                merges=[[m.min_row,m.max_row,m.min_col,m.max_col] for m in sheet.merged_cells.ranges];data=[]
                for cells in sheet.iter_rows(min_row=row,max_row=min(total,row+size-1),min_col=column,max_col=min(cols,column+width-1)) if row<=total and column<=cols else []:
                    data.append([dict(value=scalar(cached[title][c.coordinate].value if c.data_type=='f' else c.value),formula=c.value if c.data_type=='f' else '') for c in cells])
            finally: book.close();cached.close()
        return dict(book=item,sheets=sheets,sheet=title,rows=total,columns=cols,row=row,column=column,page_size=size,column_size=width,cells=data,merges=merges,
                    note='显示完整单元格数据，可翻页浏览所有行列。公式显示原公式及文件内缓存值，不在本机重算；图表、图片和宏暂不渲染。')

    def values(self, item, sheet_name):
        """整张工作表的单元格值（不含格式），供程序查找原表行。"""
        path=self.path(item)
        if path.suffix=='.xls':
            import xlrd
            book=xlrd.open_workbook(str(path))
            try:
                sheet=book.sheet_by_name(sheet_name)
                return [tuple(sheet.row_values(r)) for r in range(sheet.nrows)]
            finally: book.release_resources()
        book=openpyxl.load_workbook(path,read_only=True,data_only=True)
        try: return [tuple(row) for row in book[sheet_name].iter_rows(values_only=True)]
        finally: book.close()

    def sheet(self, item, sheet_name, max_rows=20000, max_columns=80):
        """整张工作表的显示值，供原表对照在界面中筛选、排序；去掉末尾空行空列。"""
        rows=self.values(item, sheet_name)
        truncated=len(rows)>max_rows
        cells=[[scalar(v) for v in row[:max_columns]] for row in rows[:max_rows]]
        while cells and not any(v!='' for v in cells[-1]): cells.pop()
        width=max((max((i+1 for i,v in enumerate(row) if v!=''),default=0) for row in cells),default=0)
        return dict(sheet=sheet_name,rows=len(rows),columns=width,truncated=truncated or any(len(r)>max_columns for r in rows),
                    cells=[row[:width]+['']*(width-len(row[:width])) for row in cells])

    def edit(self, item, sheet_name, address, value, value_type):
        if not self.skills.call('excel_edit','allow_operation','set_cell'): raise ValueError('当前技能不允许修改单元格')
        from openpyxl.utils.cell import coordinate_to_tuple
        try: row,col=coordinate_to_tuple(address.upper())
        except Exception: raise ValueError('请输入有效单元格地址，例如 B12')
        if not 1<=row<=1048576 or not 1<=col<=16384: raise ValueError('单元格地址超出Excel范围')
        if value_type=='number':
            value=float(value)
            if not math.isfinite(value): raise ValueError('数值必须有限')
        elif value_type=='blank': value=None
        elif value_type=='text':
            value=str(value)
            if len(value)>32767: raise ValueError('单元格文本过长')
        else: raise ValueError('仅支持文本、数值或清空')
        path=self.path(item)
        if path.suffix=='.xls': raise ValueError('XLS原表支持读取；请先另存为XLSX副本后编辑')
        book=openpyxl.load_workbook(path)
        try:
            cell=book[sheet_name].cell(row,col)
            if isinstance(cell,MergedCell): raise ValueError('请修改合并区域左上角单元格')
            before=scalar(cell.value);cell.value=value
            if value_type=='text': cell.data_type='s'
            target=self.output/(uuid.uuid4().hex+'.xlsx');book.save(target)
        finally: book.close()
        return self.register(target,Path(item['name']).stem+'_编辑副本.xlsx','edited',parent=item['id'],change=dict(sheet=sheet_name,cell=address,before=before,after=value))

    def convert(self,item):
        path=self.path(item);target=self.output/(uuid.uuid4().hex+'.xlsx')
        if path.suffix=='.xlsx': shutil.copyfile(path,target)
        else:
            import xlrd
            original=xlrd.open_workbook(str(path),formatting_info=True);book=openpyxl.Workbook();book.remove(book.active)
            try:
                for source in original.sheets():
                    sheet=book.create_sheet(source.name)
                    for r in range(source.nrows):
                        for c in range(source.ncols):
                            value=source.cell_value(r,c)
                            if source.cell_type(r,c)==xlrd.XL_CELL_DATE: value=xlrd.xldate_as_datetime(value,original.datemode)
                            cell=sheet.cell(r+1,c+1,value)
                            if isinstance(value,str):cell.data_type='s'
                    for a,b,c,d in source.merged_cells:sheet.merge_cells(start_row=a+1,end_row=b,start_column=c+1,end_column=d)
                book.save(target)
            finally:original.release_resources();book.close()
        return self.register(target,Path(item['name']).stem+'_副本.xlsx','edited',parent=item['id'],note='XLS转换只保留单元格值和合并区域，原公式与格式不保留。' if path.suffix=='.xls' else '')
