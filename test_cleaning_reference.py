import unittest
from cleaning_reference import compare_side


class ReferenceTests(unittest.TestCase):
    def row(self, quantity='1', price='10'):
        return dict(Estimation_Name='导线 A', Estimation_Unit='m', Estimation_Quantity=quantity,
                    Estimation_Price=price, Supply_Type='甲材')

    def test_duplicate_records_are_not_collapsed(self):
        report=compare_side([self.row(),self.row()],[self.row()],'sources')
        self.assertEqual(report['matched_count'],1)
        self.assertEqual(len(report['missing']),1)

    def test_formatting_and_order_ignored_but_price_is_not(self):
        manual=[self.row('2'),self.row('1','1')]
        actual=[dict(self.row('1.0',''),Estimation_Name='"导线　Ａ"'),self.row('2.00')]
        report=compare_side(manual,actual,'sources')
        self.assertEqual(report['matched_count'],2)
        self.assertEqual(report['equivalent_count'],1)
        self.assertEqual(report['core_equivalent_count'],2)
        self.assertEqual(report['differences'][0]['fields'][0]['field'],'Estimation_Price')

    def test_distinct_models_are_not_treated_as_same_item(self):
        manual=[{'设备材料名称':'导线','型号及规范':'A','单位':'m','数量':'1'}]
        actual=[dict(manual[0],型号及规范='B')]
        report=compare_side(manual,actual,'targets')
        self.assertEqual(len(report['missing']),1)
        self.assertEqual(len(report['extra']),1)

    def test_quantity_matching_takes_priority_over_price(self):
        report=compare_side([self.row('2','10')],
                            [self.row('1','10'),self.row('2','')],'sources')
        self.assertEqual(report['core_equivalent_count'],1)
        self.assertEqual(report['extra'][0]['Estimation_Quantity'],'1')


if __name__=='__main__': unittest.main()
