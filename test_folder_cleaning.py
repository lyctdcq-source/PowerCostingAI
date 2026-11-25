import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import openpyxl
import server as app
from folder_cleaning import clean_folder_local, extract_table
from skill_runtime import SkillStore


class FolderCleaningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skills = SkillStore(self.root/'runtime', app.HERE/'skill_templates')
        self.inputs = self.root/'工程'; self.inputs.mkdir()
        book = openpyxl.Workbook()
        source = book.active; source.title='安装甲供设备汇总表'
        source.append(['名称','单位','数量'])
        source.append(['导线','km',1])
        book.create_sheet('封面')
        book.save(self.inputs/'可研估算书.xlsx'); book.close()
        book = openpyxl.Workbook()
        target = book.active; target.title='通信清册'
        target.append(['名称','单位','数量','数量'])
        target.append(['名称','单位','本工程','其他站'])
        target.append(['导线','m',1000,9999])
        target.append(['缺数量','台',None,None])
        book.save(self.inputs/'通信材料清册.xlsx'); book.close()

    def tearDown(self): self.temp.cleanup()

    def test_hierarchy_paths_preserve_heading_rows_and_siblings(self):
        table={'file':'工程.xlsx','sheet':'清册','rows':[
            ['编号','名称','单位','数量'],['一','电气部分','',''],
            ['（一）','主变系统','',''],['1','主变压器','台',1],
            ['（二）','配电系统','',''],['2','开关柜','台',2]]}
        result={'sources':[],'targets':[],'rejected_rows':[]}
        extract_table(table,{'header_end':1,'columns':{'name':2,'unit':3,'qty':4},
                            'quantity_basis':'原表数量'},'targets',result,{'rows':0},self.skills)
        first,second=result['targets']
        self.assertEqual(first['层级路径'],['电气部分','主变系统'])
        self.assertEqual(second['层级路径'],['电气部分','配电系统'])
        self.assertEqual([n['row'] for n in first['_source']['hierarchy']],[2,3])
        self.assertEqual(second['_source']['row'],6)

    def test_folder_plan_extracts_original_cells_and_audit(self):
        result=clean_folder_local(self.inputs,self.skills)
        self.assertEqual(result['targets'][0]['数量'],'1000')   # 多工程数量列取“本工程”
        self.assertEqual(result['targets'][0]['_source']['row'],3)
        self.assertEqual(len(result['sheet_audit']),3)
        self.assertEqual({a['sheet']:a['side'] for a in result['sheet_audit']},{'安装甲供设备汇总表':'sources','封面':'excluded','通信清册':'targets'})
        self.assertEqual(result['rejected_rows'][0]['name'],'缺数量')
        self.assertIn('乙供设备汇总表',result['cleaning_rules'])
        self.assertIn('未调用模型',result['cleaning_method'])

    def test_unknown_station_column_needs_review_not_guess(self):
        book=openpyxl.load_workbook(self.inputs/'通信材料清册.xlsx');sheet=book.active
        sheet['C2']='甲站';sheet['D2']='乙站';book.save(self.inputs/'通信材料清册.xlsx')
        with self.assertRaisesRegex(ValueError,'清洗未取得完整双侧'):
            clean_folder_local(self.inputs,self.skills)

    def test_own_station_column_chosen_from_project_titles(self):
        book=openpyxl.load_workbook(self.inputs/'通信材料清册.xlsx');sheet=book.active
        sheet.insert_rows(1);sheet['A1']='35kV鲁纳变电站工程 通信设备材料清册'
        sheet['C3']='35kV鲁纳变';sheet['D3']='对侧35kV待补变';book.save(self.inputs/'通信材料清册.xlsx')
        result=clean_folder_local(self.inputs,self.skills)
        self.assertEqual(result['targets'][0]['数量'],'1000')
        self.assertIn('鲁纳',next(a for a in result['sheet_audit'] if a['sheet']=='通信清册')['quantity_basis'])

    def test_auxiliary_sheets_beside_main_inventory_are_excluded(self):
        book=openpyxl.load_workbook(self.inputs/'通信材料清册.xlsx')
        extra=book.create_sheet('地脚螺栓');extra.append(['序号','名称','单位','数量']);extra.append([1,'地脚螺栓','组',4])
        book.save(self.inputs/'通信材料清册.xlsx')
        result=clean_folder_local(self.inputs,self.skills)
        audit={a['sheet']:a for a in result['sheet_audit']}
        self.assertEqual(audit['地脚螺栓']['side'],'excluded')
        self.assertIn('重复计算',audit['地脚螺栓']['reason'])
        self.assertEqual(len(result['targets']),1)

    def test_four_estimate_families_enforced(self):
        for title in ('安装甲供设备汇总表','安装甲供主材汇总表','安装乙供主材汇总表','安装乙供设备汇总表'):
            self.assertTrue(self.skills.call('folder_discovery','include_estimation_table','可研.xlsx',title))
        self.assertFalse(self.skills.call('folder_discovery','include_estimation_table','可研.xlsx','建筑乙供主材汇总表'))
        self.assertFalse(self.skills.call('folder_discovery','include_estimation_table','可研.xlsx','安装机械汇总表'))

    def test_all_civil_inventory_sheets_excluded(self):
        for title in ('给排水','消防','暖通','构支架及设备基础'):
            self.assertFalse(self.skills.call('folder_discovery','include_inventory_table','35kV土建材料一览表.xlsx',title))
        self.assertTrue(self.skills.call('folder_discovery','include_inventory_table','电气一次清册.xlsx','设备材料'))
        self.assertTrue(self.skills.call('folder_discovery','include_inventory_table','2.线路部分/材料表.xls','材料清册'))  # 线路材料表也纳入
        self.assertTrue(self.skills.call('folder_discovery','include_estimation_table','线路部分估算书.xlsx','11111架空线路工程甲供主材汇总表'))
        self.assertFalse(self.skills.call('folder_discovery','include_estimation_table','可研.xlsx','安装单位工程汇总表（余物清理表三）'))

    def test_detail_uses_original_design_quantity_and_supply_price(self):
        table=dict(file='可研.xlsx',sheet='安装单位工程汇总表（表三甲）',rows=[
            ['序号','编制依据','项目名称及规格','单位','数量','设备费单价','装置性材料单价'],
            ['', '（甲设）','设备','台',1,100,None],
            ['', '（甲材）','金具','件',6,None,33.9],
            ['', '（乙材）','导线','t',0.009,None,None],
            ['', '（甲设）','未计价子项','套',1,None,None],
            ['', 'GD1','设备安装','台',1,200,300]])
        mapping=dict(header_end=1,columns=dict(name=3,unit=4,qty=5,supply=2,equipment_price=6,material_price=7),quantity_basis='设计用量')
        result=dict(sources=[],targets=[],rejected_rows=[]);audit=dict(rows=0)
        extract_table(table,mapping,'sources',result,audit,self.skills)
        self.assertEqual(len(result['sources']),3)
        self.assertEqual(result['sources'][1]['Estimation_Quantity'],'6')
        self.assertEqual(result['sources'][1]['Supply_Type'],'甲材')
        self.assertEqual(result['sources'][1]['Estimation_Price'],'33.9')
        self.assertEqual(result['sources'][2]['Estimation_Price'],'')
        self.assertTrue(result['rejected_rows'][0]['retained'])

    def test_continuation_and_nested_scope_do_not_leak(self):
        table=dict(file='清册.xlsx',sheet='电气一次',rows=[
            ['序号','名称','型号','单位','数量','备注'],
            [1,'低压电力电缆','A','m',150,''],
            ['', '', 'B','m',250,''],
            ['', '', '', '', '', ''],
            ['', '', 'C','m',300,''],
            ['十','施工电源部分','','','',''],
            ['(2)','计量','','','',''],
            [1,'仪表','A','台',1,''],
            ['十一','其他永久设备','','','',''],
            [1,'交换机','A','台',1,'']])
        mapping=dict(header_end=1,columns=dict(name=2,spec=3,unit=4,qty=5,notes=6),quantity_basis='数量')
        result=dict(sources=[],targets=[],rejected_rows=[]);audit=dict(rows=0)
        extract_table(table,mapping,'targets',result,audit,self.skills)
        self.assertEqual([r['设备材料名称'] for r in result['targets']],['低压电力电缆','低压电力电缆','交换机'])
        self.assertEqual(result['targets'][1]['_source']['name_source_row'],2)

    def test_real_report_created_only_after_success(self):
        old_state,old_data,old_config=copy.deepcopy(app.STATE),app.DATA,dict(app.CONFIG)
        try:
            ds=clean_folder_local(self.inputs,self.skills)
            app.DATA=self.root/'runtime'; app.CONFIG.update(mode='online')
            app.STATE.update(dataset=None,history=[],messages=[],artifacts=[],logs=[])
            app.activate_dataset(ds)
            app.STATE.update(stage=3, rows=app.pair_rows(ds['sources'], ds['targets'], {}))
            with patch.object(app,'write_report',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): app.advance()
            self.assertEqual(app.STATE['stage'],3)
            app.advance()
            # 成果报告只出 Word 总结
            self.assertEqual([a['name'].rsplit('.',1)[1] for a in app.STATE['material_artifacts']],['docx'])
            self.assertTrue(all((app.DATA/'outputs'/a['id']).exists() for a in app.STATE['material_artifacts']))
        finally:
            app.STATE.clear();app.STATE.update(old_state)
            app.DATA=old_data;app.CONFIG.clear();app.CONFIG.update(old_config)


if __name__=='__main__': unittest.main()
