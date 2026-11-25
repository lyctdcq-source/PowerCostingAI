"""对话中的文档：按条件导出 Excel、生成 Word 报告、打开文档，以及 Word 只读预览。"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

import document_tools
import llm_client
import server as app
from test_result_qa import dataset, rows, reply, scripted, tool_results


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=3, scope='', rows=rows(), dataset=dict(dataset(), label='测试工程', files=[], warnings=[]), messages=[], history=[],
                         logs=[], running=False, executions=[], pairs={'1': '5', '193': '75', '197': '79'}, covers={'134': [{'target': '187'}]},
                         price_results=[], result_edits=[], documents=[], material_artifacts=[], artifacts=[], project='测试工程', edit_mode='ask')
        app.CONFIG.update(api_key='test-key', base_url='https://llm.example.com/v1', web_search=False, thinking=False)
        self.immediate = patch.object(app.threading, 'Thread', lambda target, **kw: type('T', (), {'start': lambda self: target()})())

    def tearDown(self):
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def ask(self, text, *replies):
        fake, calls = scripted(*replies)
        with self.immediate, patch.object(llm_client, 'request_chat', side_effect=fake):
            app.chat(text)
        return calls

    def test_export_by_category_writes_workbook_and_file_card(self):
        calls = self.ask('请将结论为“一致”的物资导出一个excel表格',
                         reply('', ('export_results', {'categories': ['一致'], 'title': '结论一致物资'})), reply('已导出 2 条。'))
        self.assertIn('export_results', calls[0]['tools'])
        result = tool_results(calls[1])[0]
        self.assertEqual((result['条数'], result['筛选条件']), (2, '匹配情况：一致'))
        doc = app.STATE['documents'][0]
        self.assertEqual(doc['name'], '测试工程_结论一致物资_2条.xlsx')
        book = openpyxl.load_workbook(app.DATA / 'outputs' / doc['id'])
        detail = list(book['导出明细'].values)
        self.assertEqual(detail[0][0], '匹配情况')
        self.assertEqual(sorted(r[2] for r in detail[1:]), ['1', '197'])
        notes = dict(list(book['导出说明'].values)[1:])
        self.assertEqual((notes['筛选条件'], notes['导出条数']), ('匹配情况：一致', 2))
        msg = app.STATE['messages'][-1]
        self.assertEqual((msg['mode'], msg['files'][0]['id'], msg['open_file']), ('document', doc['id'], doc['id']))
        self.assertIn(doc['id'], [a['id'] for a in app.STATE['artifacts']])         # 可下载
        self.assertIn(doc['id'], [d['id'] for d in app.all_documents()])            # 可用 Excel 查看接口预览

    def test_filters_combine_and_empty_selection_is_an_error(self):
        app.STATE['rows'][1].update(edited='E1')
        self.assertEqual([r['id'] for r in app.select_rows(app.STATE['rows'], {'edited_only': True})], [2])
        self.assertEqual([r['id'] for r in app.select_rows(app.STATE['rows'], {'parents': ['电缆辅助措施']})], [2, 3, 5])
        self.assertEqual([r['id'] for r in app.select_rows(app.STATE['rows'], {'parents': ['电缆辅助措施'], 'categories': ['清册未匹配']})], [5])
        self.assertEqual([r['id'] for r in app.select_rows(app.STATE['rows'], {'target_ids': ['187']})], [6])
        calls = self.ask('导出', reply('', ('export_results', {'categories': ['评审未完成']})), reply('没有符合条件的条目'))
        self.assertIn('没有符合条件', tool_results(calls[1])[0]['error'])
        self.assertEqual(app.STATE['documents'], [])

    def test_report_tool_and_word_preview(self):
        self.ask('生成成果报告', reply('', ('generate_report', {})), reply('报告已生成'))
        doc = app.STATE['material_artifacts'][0]
        self.assertEqual((doc['kind'], app.STATE['stage']), ('docx', 4))
        self.assertEqual(app.STATE['messages'][-1]['open_file'], doc['id'])
        blocks = document_tools.docx_blocks(app.DATA / 'outputs' / doc['id'])
        self.assertTrue(any(b['type'] == 'heading' for b in blocks))
        self.assertTrue(all(b['type'] in ('heading', 'para', 'table', 'note') for b in blocks))

    def test_list_and_open_document(self):
        self.ask('导出', reply('', ('export_results', {})), reply('好'))
        doc_id = app.STATE['documents'][0]['id']
        calls = self.ask('打开刚才的表格', reply('', ('list_documents', {})), reply('', ('open_document', {'file_id': doc_id})), reply('已打开'))
        self.assertEqual(tool_results(calls[1])[0][0]['文件编号'], doc_id)
        msg = app.STATE['messages'][-1]
        self.assertEqual((msg['open_file'], msg.get('files')), (doc_id, None))
        calls = self.ask('打开', reply('', ('open_document', {'file_id': 'nope.xlsx'})), reply('没有'))
        self.assertIn('没有这个文件编号', tool_results(calls[1])[0]['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
