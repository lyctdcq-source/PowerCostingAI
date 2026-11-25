import tempfile
import unittest
from pathlib import Path

import openpyxl

import server as app
from cleaning import sheet_tables
from folder_cleaning import clean_folder_local, extract_table
from skill_runtime import SkillStore

MAPPING = dict(header_end=1, columns=dict(name=2, spec=3, unit=4, qty=5, notes=6), quantity_basis='数量')


class CleaningStructureTests(unittest.TestCase):
    """110kV 工程暴露的清册结构：合并单元格、标题下续行、“1)…”子项，以及造价表被放到清册侧。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skills = SkillStore(self.root / 'runtime', app.HERE / 'skill_templates')

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, rows, merges=()):
        table = dict(file='一次.xlsx', sheet='电气一次设备材料清册', rows=rows, merges=list(merges))
        result, audit = dict(sources=[], targets=[], rejected_rows=[]), dict(rows=0)
        extract_table(table, MAPPING, 'targets', result, audit, self.skills)
        return result, audit

    def test_reader_reports_merged_ranges_and_fills_values(self):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(['序号', '名称', '规格', '单位', '数量'])
        sheet.append([1, '主变压器', 'SSZ20', '台', 1])
        sheet.append([None, None, '附件', None, None])
        sheet.merge_cells('E2:E3')
        sheet.merge_cells('B2:B3')
        book.save(self.root / '一次.xlsx')
        title, rows, merges = next(sheet_tables(self.root / '一次.xlsx'))
        self.assertEqual(sorted(merges), [(2, 3, 2, 2), (2, 3, 5, 5)])
        self.assertEqual(rows[2][1], '主变压器')
        self.assertEqual(rows[2][4], 1)

    def test_merged_quantity_counts_once_and_joins_split_spec(self):
        rows = [['序号', '名称', '规格', '单位', '数量', '备注'],
                [1, '主变压器', 'SSZ20', '台', 1, ''], [1, '主变压器', 'SSZ20', '台', 1, ''], [1, '主变压器', 'SSZ20', '台', 1, ''],
                [2, '电缆防火槽盒', '宽200，', '米', 300, ''], [2, '电缆防火槽盒', '采用防火板', '米', 300, ''],
                [3, '电缆埋管', 'Ф200', '米', 24, ''], [3, '电缆埋管', 'Ф150', '米', 37, '']]
        merges = [(2, 4, 5, 5), (2, 4, 3, 3), (5, 6, 5, 5), (5, 6, 2, 2), (7, 8, 2, 2), (7, 8, 1, 1)]
        result, audit = self.extract(rows, merges)
        self.assertEqual([(r['设备材料名称'], r['型号及规范'], r['数量']) for r in result['targets']],
                         [('主变压器', 'SSZ20', '1'), ('电缆防火槽盒', '宽200，\n采用防火板', '300'),
                          ('电缆埋管', 'Ф200', '24'), ('电缆埋管', 'Ф150', '37')])  # 只合并名称、数量各自独立的行照常保留
        self.assertEqual(audit['merged_rows'], 3)
        self.assertEqual(result['targets'][1]['_source']['merged_spec_rows'], [6])

    def test_blank_names_take_heading_or_previous_item_name(self):
        rows = [['序号', '名称', '规格', '单位', '数量', '备注'],
                ['B', '1kV动力电缆', '', '', '', ''],
                ['', '', 'YJV22 4×240', '米', 180, ''], ['', '', 'YJV22 4×70', '米', 382, ''],
                [37, '不锈钢电缆槽盒', '100×100', '米', 12, ''], [38, '', '80×80', '米', 12, ''],
                ['一、', '照明及动力配电箱', '', '', '', ''], ['', '', '不锈钢', '台', 1, '']]
        result, _ = self.extract(rows)
        self.assertEqual([r['设备材料名称'] for r in result['targets']], ['1kV动力电缆', '1kV动力电缆', '不锈钢电缆槽盒', '不锈钢电缆槽盒'])
        self.assertEqual(result['targets'][0]['_source']['name_fill_reason'], '标题下名称留空的规格续行，承接标题物资名称')
        self.assertEqual(result['targets'][3]['_source']['name_source_row'], 5)
        self.assertEqual(result['rejected_rows'][-1]['row'], 8)  # 章节标题下的无名称行不承接章节名

    def test_numbered_sub_items_take_item_header_name(self):
        rows = [['序号', '名称', '规格', '单位', '数量', '备注'],
                [1, '透气型耐火电缆槽盒', '', '', '', '不锈钢'],
                ['', '1)托盘式直通桥架', 'XQJ-P-01', 'm', 400, ''], ['', '2）托盘式水平三通', 'XQJ-P-03', '套', 30, ''],
                [2, '软铜绞线', 'TJR-120', '米', 30, '']]
        result, _ = self.extract(rows)
        self.assertEqual([(r['设备材料名称'], r.get('子项名称', '')) for r in result['targets']],
                         [('透气型耐火电缆槽盒', '托盘式直通桥架'), ('透气型耐火电缆槽盒', '托盘式水平三通'), ('软铜绞线', '')])

    def project(self):
        folder = self.root / '工程'
        folder.mkdir(exist_ok=True)
        book = openpyxl.Workbook()
        detail = book.active
        detail.title = '安装单位工程汇总表（表三甲）'
        detail.append(['序号', '编制依据', '项目名称及规格', '单位', '数量', '设备费单价', '装置性材料单价'])
        detail.append(['', '（甲设）', '主变压器', '台', 1, 100, None])
        summary = book.create_sheet('安装甲供设备汇总表')
        summary.append(['名称', '单位', '数量'])
        summary.append(['主变压器', '台', 1])
        book.save(folder / '110kV变电站工程.xlsx')
        inventory = openpyxl.Workbook()
        inventory.active.title = '电气一次设备材料清册'
        inventory.active.append(['序号', '名称', '规格', '单位', '数量'])
        inventory.active.append([1, '主变压器', 'SSZ20', '台', 1])
        inventory.save(folder / '一次.xlsx')
        return folder

    def test_estimate_workbook_never_feeds_inventory_side(self):
        """估算书中的表三甲、四类汇总表只进造价侧；设计清册工作簿进清册侧；全程不调用模型。"""
        logs = []
        result = clean_folder_local(self.project(), self.skills, logs.append)
        self.assertEqual([(r['_source']['sheet'], r['Supply_Type']) for r in result['sources']], [('安装单位工程汇总表（表三甲）', '甲设')])
        self.assertEqual(len(result['summary_sources']), 1)  # 汇总表只作参考，不重复入库
        self.assertEqual([r['_source']['sheet'] for r in result['targets']], ['电气一次设备材料清册'])
        self.assertTrue(any('不调用模型' in line for line in logs))
        mapping = next(m for m in result['cleaning_mappings'] if m['columns'].get('supply'))
        self.assertEqual((mapping['columns']['equipment_price'], mapping['columns']['material_price']), (6, 7))


class RuleSkillSyncTests(unittest.TestCase):
    def test_unmodified_rule_skills_follow_templates_and_edits_are_kept(self):
        import hashlib
        import shutil
        from unittest.mock import patch
        import skill_runtime
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        templates = root / 'templates'
        shutil.copytree(app.HERE / 'skill_templates', templates, ignore=shutil.ignore_patterns('pipeline'))
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        skills = root / 'data' / 'skills'
        try:
            skill_runtime.SkillStore(root / 'data', templates)  # 首次：写入模板并记录基线
            (templates / 'folder_discovery.py').write_text(
                (templates / 'folder_discovery.py').read_text(encoding='utf-8') + '\n# 新版模板\n', encoding='utf-8')
            skill_runtime.SkillStore(root / 'data', templates)  # 未改过：随模板升级
            self.assertIn('# 新版模板', (skills / 'folder_discovery.py').read_text(encoding='utf-8'))
            (skills / 'data_cleaning.py').write_text('# 用户在平台中修改的版本\n', encoding='utf-8')
            (templates / 'data_cleaning.py').write_text(
                (templates / 'data_cleaning.py').read_text(encoding='utf-8') + '\n# 新版模板\n', encoding='utf-8')
            skill_runtime.SkillStore(root / 'data', templates)  # 改过：保留用户版本
            self.assertEqual((skills / 'data_cleaning.py').read_text(encoding='utf-8'), '# 用户在平台中修改的版本\n')
            (skills / 'base.json').unlink()  # 旧安装没有基线：与已发布旧模板相同的副本也升级
            (skills / 'excel_read.py').write_text('# 旧版内置模板\n', encoding='utf-8')
            with patch.dict(skill_runtime.SUPERSEDED_TEMPLATES, {'excel_read': {digest('# 旧版内置模板\n')}}):
                skill_runtime.SkillStore(root / 'data', templates)
            self.assertEqual((skills / 'excel_read.py').read_text(encoding='utf-8'), (templates / 'excel_read.py').read_text(encoding='utf-8'))
            self.assertEqual((skills / 'data_cleaning.py').read_text(encoding='utf-8'), '# 用户在平台中修改的版本\n')
        finally:
            temp.cleanup()


class ReferenceCheckTests(unittest.TestCase):
    def test_workspace_manual_files_are_checked_not_imported(self):
        import copy
        import json
        from unittest.mock import patch
        temp = tempfile.TemporaryDirectory()
        folder = Path(temp.name)
        source = {'序号': 1, 'Estimation_Name': '主变压器', 'Estimation_Unit': '台', 'Estimation_Quantity': '1', 'Supply_Type': '甲设', '_source': {}}
        target = {'序号': 1, '设备材料名称': '主变压器', '型号及规范': 'SSZ20', '单位': '台', '数量': '1', '备注': '', '_source': {}}
        (folder / '估算数据.txt').write_text(json.dumps([source], ensure_ascii=False), encoding='utf-8')
        (folder / '清册数据.txt').write_text(json.dumps([target, dict(target, 设备材料名称='电缆')], ensure_ascii=False), encoding='utf-8')
        cleaned = dict(sources=[dict(source)], targets=[dict(target)], files=[], warnings=[], sheet_audit=[], origin=str(folder), label='工程')
        old_state, old_config, old_data = copy.deepcopy(app.STATE), dict(app.CONFIG), app.DATA
        app.DATA = folder / 'runtime'
        app.DATA.mkdir()
        app.CONFIG['api_key'] = 'test-key'
        app.STATE.update(messages=[], history=[], cleaning_reference=None, dataset=None)
        try:
            with patch.object(app, 'clean_folder_local', return_value=cleaned):
                app.load('folder', str(folder))
            report = app.STATE['dataset']['reference_report']['results']
            self.assertEqual((report['sources']['core_equivalent_count'], len(report['targets']['missing'])), (1, 1))
            self.assertEqual(len(app.STATE['dataset']['targets']), 1)  # 人工文件不作为数据导入
            self.assertIn('清册 人工 2 条、本次 1 条，核心字段一致 1 条，人工有而本次无 1 条', app.STATE['messages'][-1]['text'])
        finally:
            app.STATE.clear(); app.STATE.update(old_state)
            app.CONFIG.clear(); app.CONFIG.update(old_config)
            app.DATA = old_data
            temp.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
