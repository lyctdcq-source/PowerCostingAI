"""成套项多对多匹配的结论归并与 Word 成果报告（总结性说明）。"""
import copy
import tempfile
import unittest
from pathlib import Path

from docx import Document

import document_tools
import server as app
from test_result_qa import dataset, rows


class SetFoldTests(unittest.TestCase):
    def test_components_take_set_verdict_or_become_unmatched(self):
        folded = app.fold_set_members(copy.deepcopy(rows()))
        gps = folded[5]
        self.assertEqual(gps['band'], '基本一致')
        self.assertTrue(gps['reason'].startswith('基本一致。成套对应（多对多）：与造价成套项“时钟同步系统”'))
        orphan = app.fold_set_members([dict(rows()[5], covered_by='999')])[0]
        self.assertEqual(orphan['band'], '不一致')
        anchors = {'134'}
        self.assertEqual(document_tools.result_category(gps, anchors), '基本一致')
        self.assertEqual(document_tools.result_category(orphan, anchors), '清册未匹配')
        self.assertEqual(document_tools.result_category(folded[4], anchors), '清册未匹配')
        self.assertEqual(document_tools.result_category(folded[3], anchors), '基本一致')


class ReportTests(unittest.TestCase):
    def test_report_is_a_summary_docx(self):
        state = {'project': '测试工程', 'scope': '', 'dataset': dataset(), 'rows': app.fold_set_members(copy.deepcopy(rows()))}
        path = Path(tempfile.mkdtemp()) / 'report.docx'
        document_tools.write_report(state, path)
        doc = Document(str(path))
        text = '\n'.join(p.text for p in doc.paragraphs)
        cells = [[c.text for c in r.cells] for r in doc.tables[0].rows]
        for heading in ('一、核对范围与方法', '二、总体结果', '三、主要问题', '四、结论与建议'):
            self.assertIn(heading, text)
        self.assertIn(['清册未匹配', '1', '16.7%'], cells)
        self.assertIn(['基本一致', '2', '33.3%'], cells)
        self.assertEqual(cells[-1], ['合计', '6', '100%'])
        self.assertIn('1 个成套对应组、涉及组成件 1 条', text)
        self.assertIn('清册“400×400电缆沟支架”', text)
        self.assertNotIn('含于成套项', text)
        self.assertLess(len(doc.paragraphs), 40)


if __name__ == '__main__':
    unittest.main()
