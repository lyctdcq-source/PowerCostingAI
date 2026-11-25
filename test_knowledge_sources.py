import sqlite3
import tempfile
import unittest
from pathlib import Path

import server as app
from domain_kg import DomainKnowledge
from knowledge import CUSTOM_RULES, LEGACY_SOURCE, KnowledgeStore, builtin_id, builtin_rules, knowledge_context


class CustomRuleSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = KnowledgeStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_seeded_rules_use_custom_rule_source(self):
        builtin = builtin_rules()
        self.assertEqual(self.store.seed(), 8 + len(builtin['entries']))
        self.assertEqual(self.store.seed(), 0)  # 再次启动不重复添加
        entries = self.store.list()
        self.assertEqual({(e['source'], e['location']) for e in entries}, {(CUSTOM_RULES, ''), (CUSTOM_RULES, builtin['location'])})
        self.assertTrue(all(e['kind'] == CUSTOM_RULES and not e['approved'] for e in entries))
        self.assertNotIn(LEGACY_SOURCE, knowledge_context(entries))
        self.assertNotIn(' / ', knowledge_context([e for e in entries if not e['location']][:1]))

    def test_builtin_power_rules_are_well_formed_and_user_edits_survive(self):
        builtin = builtin_rules()['entries']
        self.assertGreaterEqual(len(builtin), 20)
        self.assertEqual(len({e['title'] for e in builtin}), len(builtin))
        self.assertTrue(all(e['category'] and 40 <= len(e['content']) <= 1200 for e in builtin))
        self.store.seed()
        key = builtin_id('电力电缆型号识别')
        self.assertEqual([e['id'] for e in self.store.search('YJV22 铠装 电力电缆')][:1], [key])
        self.store.save(dict(self.store.get(key), content='专家修订后的内容', approved=True))
        self.store.seed()
        self.assertEqual((self.store.get(key)['content'], self.store.get(key)['approved']), ('专家修订后的内容', 1))

    def test_batch_knowledge_follows_batch_items_and_skips_price_rules(self):
        self.store.seed()
        old_kb = app.KB
        app.KB = self.store
        try:
            cables = [e['title'] for e in app.knowledge_for(['KYN28-12 主变进线柜', 'ZA-YJV22 电力电缆 价格 信息价 图纸'])]
            insulators = [e['title'] for e in app.knowledge_for(['绝缘子 U100BP/146D型', 'U形挂环 U-21型'])]
            pipes = [e['title'] for e in app.knowledge_for(['镀锌焊接钢管 DN50', '热镀锌扁钢 -60×6'])]
        finally:
            app.KB = old_kb
        self.assertIn('高压开关柜型号与柜内元件', cables)
        self.assertIn('电力电缆型号识别', cables)
        self.assertFalse({'价格偏差默认分级', '价格口径与出处', '架空线路图纸交叉校核'} & set(cables))
        self.assertEqual(insulators[0], '绝缘子识别')
        self.assertIn('型钢、扁钢与钢管计量', pipes)
        self.assertLessEqual(len(cables), 5)

    def test_legacy_entries_are_relabelled_in_place_without_revision(self):
        self.store.save({'id': 'research-x', 'title': '旧规则', 'content': '正文', 'source': LEGACY_SOURCE, 'location': '4.4.1 表4-5'})
        self.store.save({'id': 'doc-1', 'title': '导入资料', 'content': '正文', 'source': '某规范.pdf', 'location': '第3页'})
        self.store.seed()
        entries = {e['id']: e for e in self.store.list()}
        self.assertEqual((entries['research-x']['source'], entries['research-x']['location'], entries['research-x']['version']), (CUSTOM_RULES, '', 1))
        self.assertEqual(entries['doc-1']['source'], '某规范.pdf')
        db = sqlite3.connect(Path(self.temp.name) / 'knowledge.sqlite')
        try:
            self.assertNotIn(LEGACY_SOURCE, ' '.join(str(r) for r in db.execute('SELECT * FROM revisions')))
        finally:
            db.close()

    def test_saved_results_and_domain_sources_are_relabelled(self):
        state = {'knowledge_used': [{'source': LEGACY_SOURCE, 'location': '4.4.1'}],
                 'rows': [{'knowledge': [{'source': LEGACY_SOURCE, 'location': '4.4.2'}], 'domain': [{'source': '研究方案4.4.1'}],
                           'raw': {'领域知识': [{'source': '研究方案4.4.1'}, {'source': 'GB/T 706-2016'}]}}]}
        app.relabel_legacy_sources(state)
        self.assertEqual(state['knowledge_used'][0], {'source': CUSTOM_RULES, 'location': ''})
        self.assertEqual(state['rows'][0]['knowledge'][0]['source'], CUSTOM_RULES)
        self.assertEqual([d['source'] for d in state['rows'][0]['raw']['领域知识']], [CUSTOM_RULES, 'GB/T 706-2016'])
        self.assertEqual(state['rows'][0]['domain'][0]['source'], CUSTOM_RULES)

    def test_domain_knowledge_cites_registered_custom_rule_source(self):
        kg = DomainKnowledge()
        self.assertIn(CUSTOM_RULES, kg.sources)
        self.assertEqual(kg.validate(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
