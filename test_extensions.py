import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from knowledge import KnowledgeStore
from skill_runtime import SkillStore, compile_skill
from platform_tools import vision_extract
from document_tools import extract_file, write_xlsx


class ExtensionTests(unittest.TestCase):
    def test_knowledge_retrieval_versions_and_disable(self):
        with tempfile.TemporaryDirectory() as folder:
            store=KnowledgeStore(folder)
            e=store.save(dict(title='铜母排换算',content='铜母排50×5密度8900，换算结果2.225kg/m',source='测试规范',location='第2页'))
            self.assertEqual(store.search('铜母排重量')[0]['id'],e['id'])
            updated=store.save(dict(e,active=False))
            self.assertEqual(updated['version'],2)
            self.assertEqual(store.search('铜母排'),[])
            self.assertEqual(store.history(e['id'])[0]['entry']['location'],'第2页')

    def test_skill_apply_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SkillStore(folder,Path(__file__).parent/'skill_templates')
            old=next(d['code'] for d in store.describe() if d['id']=='data_cleaning')
            # Business function changes must produce a real file revision and can be reverted.
            code=old.replace('return True','return "临时施工用电" not in relative_path',1)
            record=store.apply('data_cleaning',code,'忽略临时施工用电','测试修改')
            self.assertIn('临时施工用电',record['diff'])
            store.rollback(record['id'])
            self.assertEqual(next(d['code'] for d in store.describe() if d['id']=='data_cleaning'),old)
            with self.assertRaises(ValueError):compile_skill('data_cleaning','import os\n'+old)

    def test_document_provenance(self):
        from docx import Document
        import openpyxl
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'原文.docx'
            doc=Document();doc.add_paragraph('线路长度15km');doc.save(source)
            self.assertIn('线路长度',extract_file(source)[0]['content'])
            sheet=root/'明细.xlsx';write_xlsx([{'名称':'=HYPERLINK("bad")','数量':2}],sheet)
            self.assertEqual(openpyxl.load_workbook(sheet).active['A2'].data_type,'s')
            self.assertIn('工作表',extract_file(sheet)[0]['location'])

    def test_vision_adapter_and_invalid_evidence(self):
        observed=[]
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'choices':[{'message':{'content':'{"fields":[{"field":"塔数","value":"48","page":1,"unit":"基","evidence":"明细表底部 合计48","confidence":0.9}]}'}}]}
        def post(*args,**kwargs):observed.append(kwargs['json']);return Response()
        with patch('platform_tools.drawing_images',return_value=[(1,'AAA','image/jpeg')]),patch('requests.post',side_effect=post):
            rows,pages=vision_extract('dummy','图纸.pdf',{'api_key':'test','model':'visual-test','base_url':'https://example.test/v1'})
            self.assertEqual(rows[0]['demo'],False)
            self.assertIn('第1页',rows[0]['source'])
            self.assertEqual(observed[0]['model'],'visual-test')
            self.assertEqual(observed[0]['messages'][0]['content'][1]['type'],'image_url')
            vision_extract('dummy','图纸.pdf',{'api_key':'test','model':'qwen3.8-max','base_url':'https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'})
            self.assertFalse(observed[-1]['enable_thinking'])


if __name__=='__main__':unittest.main()
