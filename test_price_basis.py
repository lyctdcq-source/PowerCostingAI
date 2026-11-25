"""装置性材料的比价基准：从主材价差表取市场价，回填到造价行。

表三甲的装材单价是基准期预算价，与市场价的差额在价差表里单列；价差表只收有预算价的物资，
是表三甲的真子集，所以回填只覆盖对得上的那些，其余标注基准待确认。
"""
import unittest

from pipeline_skills import PipelineContext, PipelineStore

DIFF_ROWS = [
    ['安装乙供装置性材料价差汇总表'],
    [None, None, None, None, None, None, None, None, None, '金额单位:元'],
    ['编号', '材料名称', '单位', '设计用量', '损耗率(%)', '单价(不含税)', None, '合价(不含税)'],
    [None, None, None, None, None, '预算价', '市场价', '预算价', '市场价', '价差'],
    [None, '安装工程'],
    [None, '铝包钢芯铝绞线 JL/LB20A 300/40', 't', 0.017, None, 13034, 18995.58, 221.58, 322.92, 101.34],
    [None, '电缆支架(包含材料费、镀锌费)', 't', 11.183, None, 5446, 6415, 60903, 71739, 10836],
    [None, '单价为零的材料', 't', 1, None, 100, 0, 100, 0, -100],
]
BUILD_ROWS = [
    ['建筑乙供材料价差汇总表'],
    ['编号', '材料名称', '单位', '设计用量', '单价', None, '合价'],
    [None, None, None, None, '预算价', '市场价', '预算价', '市场价'],
    ['C01020701', '铁件 钢筋', 'kg', 4045.318, 3.246, 4.11, 13131.102, 16626.257],
]


def table(index, sheet, rows, file='110kV变电站工程.xlsx'):
    return dict(id=index, file=file, sheet=sheet, rows=[list(r) for r in rows])


def source(seq, name, supply, price):
    return {'序号': seq, 'Estimation_Name': name, 'Estimation_Unit': 't', 'Estimation_Quantity': '1',
            'Estimation_Price': price, 'Supply_Type': supply, '_source': {'file': '110kV变电站工程.xlsx', 'row': seq}}


class PriceBasisTests(unittest.TestCase):

    def setUp(self):
        self.skills = PipelineStore().instantiate(PipelineContext())
        self.tables = [table(1, '安装乙供主材价差汇总表', DIFF_ROWS), table(2, '建筑乙供材料价差汇总表', BUILD_ROWS),
                       table(3, '安装单位工程汇总表（表三甲）', [['无关']])]
        self.files = {'110kV变电站工程.xlsx'}

    def records(self):
        return self.skills.table_selection.price_diff_records(self.tables, self.files)

    def test_columns_pick_unit_price_not_total_price(self):
        """预算价与市场价在“单价”和“合价”下各出现一次，只能取单价那一组。"""
        cols = self.skills.table_selection.price_diff_columns(self.tables[0]['rows'])
        self.assertEqual((cols['header'], cols['name'], cols['unit']), (3, 1, 2))   # 数据从 header+1 行起
        self.assertEqual((cols['budget'], cols['market']), (5, 6))
        cols = self.skills.table_selection.price_diff_columns(self.tables[1]['rows'])
        self.assertEqual((cols['budget'], cols['market']), (4, 5))

    def test_records_skip_headers_and_zero_prices(self):
        records = self.records()
        self.assertEqual(len(records), 3)
        entry = records['铝包钢芯铝绞线JL/LB20A300/40']
        self.assertEqual((entry['market'], entry['budget'], entry['unit']), (18995.58, 13034, 't'))
        self.assertEqual(entry['sheet'], '安装乙供主材价差汇总表')
        self.assertFalse(any('单价为零' in k for k in records))
        self.assertIn('铁件钢筋', records)                       # 建筑专业的材料价差表同样可用

    def test_other_workbooks_are_ignored(self):
        self.tables.append(table(4, '安装乙供主材价差汇总表', DIFF_ROWS, file='别的工程.xlsx'))
        self.assertEqual(len(self.skills.table_selection.price_diff_records(self.tables, self.files)), 3)

    def test_fill_market_price_only_for_materials(self):
        result = dict(sources=[source(1, '铝包钢芯铝绞线 JL/LB20A 300/40', '乙材', '13034'),
                               source(2, '裸铜绞线 TJR-120', '乙材', '44603'),
                               source(3, '110kV主变压器', '甲设', '3280000')])
        warnings = []
        basis = self.skills.table_selection.apply_price_basis(result, self.records(), warnings)
        self.assertEqual((basis['records'], basis['filled'], basis['pending']), (3, 1, 1))
        filled, pending, equipment = result['sources']
        self.assertEqual((filled['市场价'], filled['预算价']), (18995.58, 13034))
        self.assertEqual(filled['比价基准'], '主材价差表市场价（不含税）')
        self.assertEqual(filled['_source']['price_basis_sheet'], '安装乙供主材价差汇总表')
        self.assertEqual(filled['Estimation_Price'], '13034')    # 原表单价保留不动
        self.assertNotIn('市场价', pending)
        self.assertIn('待确认', pending['比价基准'])
        self.assertNotIn('比价基准', equipment)                   # 设备不走价差表
        self.assertIn('1 条取自主材价差表的市场价', warnings[0])


if __name__ == '__main__':
    unittest.main()
