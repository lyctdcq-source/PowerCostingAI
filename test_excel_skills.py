import tempfile
import unittest
from pathlib import Path
import openpyxl
from excel_skills import ExcelSkills
from skill_runtime import SkillStore

class ExcelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.skills=SkillStore(self.root,Path(__file__).parent/'skill_templates');self.excel=ExcelSkills(self.root,self.skills)
  self.source=self.root/'original.xlsx';w=openpyxl.Workbook();s=w.active;s.title='原始表'
  s['A1']='设备';s['B1']='=1+1';s['A205']='最后一行';s['AO205']='最后一列';s.merge_cells('C2:D2');s['C2']='合并'
  w.create_sheet('另一张表')['A1']='另一张';w.save(self.source);w.close()
  self.item=self.excel.snapshot(self.source,'original.xlsx')
 def tearDown(self):self.tmp.cleanup()
 def test_all_sheets_rows_columns_and_formulas_accessible(self):
  first=self.excel.view(self.item)
  self.assertEqual(len(first['sheets']),2);self.assertEqual(first['cells'][0][1]['formula'],'=1+1')
  last=self.excel.view(self.item,'原始表',201,41)
  self.assertEqual(last['cells'][4][0]['value'],'最后一列')
  self.assertEqual(self.excel.view(self.item,'另一张表')['cells'][0][0]['value'],'另一张')
 def test_edit_copy_preserves_source_and_text_not_formula(self):
  before=self.source.read_bytes()
  edited=self.excel.edit(self.item,'原始表','A1','=HYPERLINK("x")','text')
  self.assertEqual(self.source.read_bytes(),before)
  result=self.excel.view(edited)['cells'][0][0]
  self.assertEqual(result['formula'],'');self.assertEqual(result['value'],'=HYPERLINK("x")')
  with self.assertRaises(ValueError):self.excel.edit(self.item,'原始表','D2','bad','text')
 def test_cleaned_export_preserves_provenance_and_all_records(self):
  rows=[{'名称':'物资','数量':i,'_source':{'file':'a.xlsx','sheet':'原始表','row':i+2}} for i in range(205)]
  item=self.excel.cleaned(rows,'清洗后.xlsx');result=self.excel.view(item,row=201)
  self.assertEqual(result['rows'],206);self.assertEqual(result['cells'][-1][1]['value'],204)
  self.assertEqual(result['cells'][-1][2]['value'],'a.xlsx')
 def test_whole_sheet_values_for_filter_and_sort(self):
  data=self.excel.sheet(self.item,'原始表')
  self.assertEqual((data['rows'],data['columns'],data['truncated']),(205,41,False))
  self.assertEqual((data['cells'][0][0],data['cells'][204][0],data['cells'][204][40]),('设备','最后一行','最后一列'))
  self.assertTrue(all(len(r)==41 for r in data['cells']))
  small=self.excel.sheet(self.item,'原始表',max_rows=10,max_columns=5)
  self.assertTrue(small['truncated']);self.assertEqual((len(small['cells']),small['columns']),(2,3))
 def test_registry_path_cannot_escape_outputs(self):
  with self.assertRaises(ValueError):self.excel.view({'id':'../original.xlsx'})
if __name__=='__main__':unittest.main()
