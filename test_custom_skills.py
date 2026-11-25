import copy
import tempfile
import unittest
from custom_skills import CustomSkills, run_code

class UserSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=CustomSkills(self.tmp.name)
        self.item=dict(id='sum_cost',name='计算金额',description='示例',instructions='合计',code='def run(data):\n    return sum(data)\n',tests=[dict(input=[1,2],expected=3)])
    def tearDown(self):self.tmp.cleanup()
    def test_create_test_enable_execute_export_import(self):
        saved=self.store.save(self.item);self.assertFalse(saved['enabled'])
        with self.assertRaises(ValueError):self.store.toggle('sum_cost',True)
        self.assertTrue(self.store.test('sum_cost')[0]['passed']);self.store.toggle('sum_cost',True)
        self.assertEqual(self.store.run('sum_cost',[4,5])['result'],9)
        pack=self.store.export('sum_cost');pack['skill']['id']='sum_copy'
        self.assertFalse(self.store.import_pack(pack)['enabled'])
        with self.assertRaises(ValueError):self.store.import_pack(pack)
    def test_edit_invalidates_enable_and_rollback_keeps_history(self):
        self.store.save(self.item);self.store.test('sum_cost');self.store.toggle('sum_cost',True)
        changed=dict(self.item,code='def run(data):\n    return len(data)\n')
        edit=self.store.save(changed,1);self.assertEqual(edit['version'],2);self.assertFalse(edit['enabled'])
        self.assertFalse(self.store.test('sum_cost')[0]['passed'])
        rollback=self.store.rollback('sum_cost',1);self.assertEqual(rollback['version'],3);self.assertFalse(rollback['tested']);self.assertEqual(rollback['code'],self.item['code'])
    def test_reject_system_code_and_path_traversal(self):
        for change in [dict(id='../escape'),dict(code='import os\ndef run(data):\n    return 1'),dict(code='def run(data):\n    return data.__class__')]:
            with self.assertRaises(ValueError):self.store.save(dict(self.item,**change))
    def test_run_error_and_budget_reported(self):
        with self.assertRaises(ValueError):run_code('def run(data):\n    return 1 / 0',{})
        with self.assertRaises(ValueError):run_code('def run(data):\n    result = 0\n    for i in range(100000000):\n        result = result + i\n    return result',{})
    def test_stale_revision_cannot_overwrite(self):
        self.store.save(self.item)
        with self.assertRaises(ValueError):self.store.save(self.item,0)

if __name__=='__main__':unittest.main()
