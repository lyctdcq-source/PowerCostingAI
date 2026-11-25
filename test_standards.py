import copy
import tempfile
import unittest
from pathlib import Path

import server as app
import standards
from knowledge import CUSTOM_RULES, STANDARD, KnowledgeStore, citation, knowledge_context

COVER = """ICS 77.140.70
中华人民共和国国家标准
GB/T 706—2016
代替 GB/T 706—2008
热轧型钢
Hot rolled section steel
1 kV 及以下用途说明"""
CONTENTS = """目次
前言 ……………… Ⅲ
1 范围 ……………… 1
2 规范性引用文件 ……………… 1
4 订货内容 1
4.2 尺寸、外形 2
附录 A （资料性附录） 型钢截面特性 9"""
BODY = """前言
本标准按照 GB/T 1.1—2009 给出的规则起草。
GB/T 706—2016
1 范围
本标准规定了热轧型钢的尺寸、外形、重量及允许偏差。
2 规范性引用文件
下列文件对于本文件的应用是必不可少的。
3
3 术语和定义
本标准没有需要界定的术语。
4 尺寸、外形、重量及允许偏差
4.1 截面尺寸
型钢的截面尺寸应符合表 1 的规定。
表 1 热轧等边角钢截面尺寸
型号 边宽 边厚 理论重量
5 角钢 50 5 3.77
6 角钢 60 6 5.427
4.2 重量
4.2.1 型钢按理论重量交货。
理论重量按密度 7.85g/cm³ 计算。
4.2.2 根据双方协议，也可按实际重量交货。
5 试验方法
5.1 尺寸用量具测量。
附录 A
（资料性附录）
型钢截面特性
A.1 截面面积按公式计算。
A.2 惯性矩按公式计算。"""


class SplitTests(unittest.TestCase):
    def test_normalize_and_detect_standard(self):
        self.assertEqual(standards.normalize_standard('gb_t706—2016'), 'GB/T 706-2016')
        self.assertEqual(standards.normalize_standard('DL/T 5222-2021'), 'DL/T 5222-2021')
        self.assertEqual(standards.normalize_standard('Q/GDW 1166-2013'), 'Q/GDW 1166-2013')
        meta = standards.detect('GB_T 706-2016 热轧型钢.pdf', [{'content': 'x'}])
        self.assertEqual(meta, {'standard': 'GB/T 706-2016', 'name': '热轧型钢', 'edition': '2016'})
        self.assertEqual(standards.detect('GB_T 706-2016 热轧型钢（测试摘录）.txt', [{'content': ''}])['name'], '热轧型钢（测试摘录）')
        self.assertEqual(standards.detect('(GB 50545-2010)架空输电线路设计规范).pdf', [{'content': ''}])['name'], '架空输电线路设计规范')
        meta = standards.detect('型钢标准.pdf', [{'content': COVER}])
        self.assertEqual(meta, {'standard': 'GB/T 706-2016', 'name': '热轧型钢', 'edition': '2016'})

    def test_split_skips_contents_headers_and_table_rows(self):
        chunks = [{'location': '第1页', 'content': COVER}, {'location': '第2页', 'content': CONTENTS}, {'location': '第3页', 'content': BODY}]
        clauses, warnings = standards.split(chunks, 'GB/T 706-2016')
        self.assertEqual(warnings, [])
        self.assertEqual([c['clause'] for c in clauses],
                         ['前言', '1', '2', '3', '4.1', '表1', '4.2.1', '4.2.2', '5.1', 'A.1', 'A.2'])
        by = {c['clause']: c for c in clauses}
        self.assertNotIn('GB/T 706—2016', by['前言']['content'])
        self.assertIn('5 角钢 50 5 3.77', by['表1']['content'])  # 表格行留在表内，不被当成第 5 章
        self.assertEqual(by['4.2.1']['title'], '4.2.1 型钢按理论重量交货。')
        self.assertIn('理论重量按密度', by['4.2.1']['content'])
        self.assertEqual(by['4.2.1']['location'], '第3页 · 4 尺寸、外形、重量及允许偏差 › 4.2 重量')
        self.assertEqual(by['A.1']['location'], '第3页 · 附录A （资料性附录） 型钢截面特性')
        self.assertNotIn('2', [c['clause'] for c in clauses if '……' in c['content']])

    def test_scanned_text_numbers_explanation_section_and_quality_warning(self):
        scanned = '\n'.join(['1 总 则', 'I.O.I为了在交 流架空输电线路的设计中贯彻国家方针。', 'I. O. 2本 规范适用于交流线路的设计。',
                             '条文说明', '1 总则', '1.0.1 本条说明制定规范的目的。'] + ['设', '计'] * 20)
        clauses, warnings = standards.split([{'location': '第11页', 'content': scanned}])
        self.assertEqual([c['clause'] for c in clauses], ['1.0.1', '1.0.2', '条文说明 1.0.1'])
        self.assertEqual(clauses[1]['content'], '1.0.2 本规范适用于交流线路的设计。')
        self.assertTrue(any('文字层质量差' in w for w in warnings))
        self.assertEqual(citation({'kind': STANDARD, 'standard': 'GB 50545-2010', 'source': '架空输电线路设计规范', 'clause': '条文说明 1.0.1'}),
                         'GB 50545-2010《架空输电线路设计规范》条文说明第1.0.1条')
        meta = standards.detect('2023_12_27_110kV-750kV架空输电线路设计规范(GB50545-2010)(1).pdf', [{'content': ''}])
        self.assertEqual(meta, {'standard': 'GB 50545-2010', 'name': '110kV-750kV架空输电线路设计规范', 'edition': '2010'})

    def test_word_paragraph_locations_long_clauses_and_warnings(self):
        long_body = '\n'.join(['1 范围', '本标准适用于电缆。' * 900, '2 术语', '术语说明。'])
        clauses, warnings = standards.split([{'location': '段落10～40', 'content': long_body}])
        self.assertEqual([(c['clause'], c['part']) for c in clauses], [('1', 0), ('1', 1), ('2', 0)])
        self.assertEqual(clauses[0]['location'], '段落10')
        self.assertEqual(clauses[2]['location'], '段落12')
        self.assertTrue(clauses[1]['title'].endswith('（续1）'))
        self.assertTrue(warnings)  # 只有 2 章，提示可能未识别编号
        _, warnings = standards.split([{'location': '段落1～3', 'content': '范围\n本标准规定…\n术语'}])
        self.assertIn('自动编号', warnings[0])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_kinds_validation_search_and_citation(self):
        store = KnowledgeStore(self.temp.name)
        store.save({'id': 'r1', 'title': '数量核对四档口径', 'content': '角钢数量差异不超过1%', 'source': CUSTOM_RULES})
        store.save({'id': 's1', 'kind': STANDARD, 'title': '4.2.1 理论重量', 'content': '角钢按理论重量交货', 'source': '热轧型钢',
                    'standard': 'GB/T 706-2016', 'clause': '4.2.1', 'edition': '2016', 'location': '第3页'})
        with self.assertRaisesRegex(ValueError, '标准号和条款号'):
            store.save({'kind': STANDARD, 'title': 't', 'content': 'c', 'source': 's'})
        with self.assertRaisesRegex(ValueError, '知识类型'):
            store.save({'kind': '其他', 'title': 't', 'content': 'c', 'source': 's'})
        self.assertEqual({e['id'] for e in store.search('角钢')}, {'r1', 's1'})
        self.assertEqual([e['id'] for e in store.search('角钢', kind=STANDARD)], ['s1'])
        self.assertEqual([e['id'] for e in store.search('GB/T 706 4.2.1')], ['s1'])
        self.assertEqual(citation(store.get('s1')), 'GB/T 706-2016《热轧型钢》第4.2.1条（2016版）')
        self.assertEqual(citation(dict(store.get('s1'), clause='表1', edition='')), 'GB/T 706-2016《热轧型钢》表1')
        self.assertIn('（规范文件·待专家审核）', knowledge_context([store.get('s1')]))
        self.assertIn('（自定义规则·待专家审核）', knowledge_context([store.get('r1')]))
        self.assertEqual(store.get('r1')['standard'], '')

    def test_old_database_gains_kind_columns(self):
        import sqlite3
        db = sqlite3.connect(Path(self.temp.name) / 'knowledge.sqlite')
        db.execute('CREATE TABLE entries (id TEXT PRIMARY KEY,title TEXT,content TEXT,source TEXT,location TEXT,category TEXT,approved INTEGER,active INTEGER,version INTEGER,updated TEXT)')
        db.execute("INSERT INTO entries VALUES ('old','旧条目','正文','导入资料.docx','段落1','导入资料',0,1,1,'2026-01-01')")
        db.commit()
        db.close()
        store = KnowledgeStore(self.temp.name)
        self.assertEqual({k: store.get('old')[k] for k in ('kind', 'standard', 'clause', 'edition')},
                         {'kind': CUSTOM_RULES, 'standard': '', 'clause': '', 'edition': ''})


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_kb, self.old_state, self.old_data = app.KB, copy.deepcopy(app.STATE), app.DATA
        app.KB = KnowledgeStore(self.temp.name)
        app.DATA = Path(self.temp.name)  # message() 会落盘，不能写到正式数据目录
        app.STATE.update(messages=[], history=[], logs=[])
        self.doc = {'id': 'd1', 'name': 'GB_T 706-2016 热轧型钢.pdf', 'chunks': [{'location': '第3页', 'content': BODY}]}

    def tearDown(self):
        app.KB, app.DATA = self.old_kb, self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        self.temp.cleanup()

    def test_import_by_clause_then_reimport_updates_only_changes(self):
        body = {'standard': 'gb_t 706—2016', 'name': '热轧型钢', 'edition': '2016'}
        first = app.import_standard(self.doc, body)
        self.assertEqual((first['count'], first['新增'], first['更新'], first['未变']), (11, 11, 0, 0))
        entry = next(e for e in app.KB.list() if e['clause'] == '4.2.1')
        self.assertEqual((entry['kind'], entry['standard'], entry['source'], entry['edition'], entry['approved']),
                         (STANDARD, 'GB/T 706-2016', '热轧型钢', '2016', 0))
        app.KB.save(dict(entry, approved=True))
        again = app.import_standard(self.doc, body)
        self.assertEqual((again['新增'], again['更新'], again['未变']), (0, 0, 11))
        changed = dict(self.doc, chunks=[{'location': '第3页', 'content': BODY.replace('7.85g/cm³', '7.85 g/cm³（修订）')}])
        third = app.import_standard(changed, body)
        self.assertEqual((third['更新'], third['未变']), (1, 10))
        updated = app.KB.get(entry['id'])
        self.assertEqual((updated['approved'], updated['version']), (0, 3))  # 正文变了：重新待审核
        self.assertIn('已按条款导入 GB/T 706-2016《热轧型钢》11 条', app.STATE['messages'][0]['text'])
        with self.assertRaisesRegex(ValueError, '标准号和标准名称'):
            app.import_standard(self.doc, {'standard': '', 'name': '热轧型钢'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
