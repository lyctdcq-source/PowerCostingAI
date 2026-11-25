"""配对后同名同规格物资按数量重新分配，以及配对时按批次检索知识。全部离线运行。"""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server as app
from domain_kg import DomainKnowledge
from pipeline_skills import PipelineContext, PipelineStore
from test_pipeline_skills import src, tgt


def skills(**settings):
    prompts, found = [], []

    def ask(prompt, api_key, max_tokens=3000, retries=3):
        prompts.append(prompt)
        return {'rows': []}, {}
    ctx = PipelineContext(ask=ask, knowledge=DomainKnowledge(), settings=settings)
    return PipelineStore().instantiate(ctx), prompts, found


class RebalanceTests(unittest.TestCase):
    def setUp(self):
        self.skills = skills()[0]

    def rebalance(self, pairs, sources, targets):
        pairs = dict(pairs)
        moved = self.skills.rule_check._rebalance_same_spec(pairs, sources, targets, 'test')
        return pairs, moved

    def test_insulator_strings_follow_pieces_per_string_and_wrong_type_is_released(self):
        sources = [src(51, '绝缘子　U100BP/146D型', '216', '片'), src(59, '绝缘子　U100BP/146D型', '108', '片'),
                   src(98, '绝缘子　U100BP/146D型', '60', '片')]
        targets = [tgt(25, '耐张绝缘子串', '12', '串', '2x9(U100BP/146D)，内含：18片U100BP/146D型绝缘子'),
                   tgt(26, '耐张绝缘子串', '6', '串', '2x9(U100BP/146D)，内含：18片U100BP/146D型绝缘子'),
                   tgt(74, '耐张绝缘子串', '6', '串', '2x5(U100BP/146D)，内含：10片U100BP/146D型绝缘子'),
                   tgt(76, '悬垂绝缘子串', '3', '串', '5（U70BP/146D），内含：5片U70BP/146D型绝缘子')]
        pairs, moved = self.rebalance({'51': '26', '59': '74', '98': '25'}, sources, targets)
        self.assertEqual((pairs, moved), ({'51': '25', '59': '26', '98': '74'}, 3))
        pairs, _ = self.rebalance({'51': '26', '59': '76', '98': '74'}, sources, targets)
        self.assertEqual(pairs, {'51': '25', '59': '26', '98': '74'})  # 空闲的同规格 12 串补上，U70BP 串不转给另一条 U100BP
        pairs, _ = self.rebalance({'51': '26', '59': '76', '98': '74', '52': '25'}, sources + [src(52, 'U形挂环　U-21型', '24', '只')], targets)
        self.assertEqual(pairs, {'52': '25', '59': '26', '98': '74'})  # 正确的 12 串被别的物资占用时，216片暂不配，U70BP 串释放

    def test_ratio_order_used_when_units_cannot_be_converted(self):
        sources = [src(82, '裸铜绞线　TJR-120', '0.109', 't'), src(10, '裸铜绞线　TJR-120', '0.011', 't'),
                   src(117, '裸铜绞线　TJR-120', '0.033', 't')]
        targets = [tgt(53, '软铜绞线', '100', '米', 'TJR-120'), tgt(92, '软铜绞线', '30', '米', 'TJR-120'),
                   tgt(13, '软铜绞线', '10', '米', 'TJR-120')]
        pairs, moved = self.rebalance({'82': '92', '10': '13', '117': '53'}, sources, targets)
        self.assertEqual((pairs, moved), ({'82': '53', '10': '13', '117': '92'}, 2))

    def test_conductor_weight_conversion_from_domain_knowledge(self):
        sources = [src(3, '铝包钢芯铝绞线　JL/LB20A 300/40', '0.017', 't'), src(65, '铝包钢芯铝绞线　JL/LB20A 300/40', '1.133', 't')]
        targets = [tgt(7, '铝包钢芯铝绞线', '1000', 'm', 'JL/LB20A-300/40'), tgt(8, '铝包钢芯铝绞线', '15', 'm', 'JL/LB20A-300/40')]
        pairs, _ = self.rebalance({'3': '7', '65': '8'}, sources, targets)
        self.assertEqual(pairs, {'3': '8', '65': '7'})

    def test_equal_quantities_keep_model_choice(self):
        sources = [src(281, '机柜（带电源盘）', '1', '套'), src(289, '机柜（带电源盘）', '1', '套')]
        targets = [tgt(653, '机柜（带电源盘）', '1', '面', '2260x800x600mm'), tgt(661, '机柜（带电源盘）', '1', '面', '2260x800x600mm')]
        pairs, moved = self.rebalance({'281': '661', '289': '653'}, sources, targets)
        self.assertEqual((pairs, moved), ({'281': '661', '289': '653'}, 0))


class BatchKnowledgeTests(unittest.TestCase):
    def test_each_batch_prompt_carries_its_own_knowledge(self):
        asked = []

        def find(seqs, names):
            asked.append((seqs, names))
            return '[KB:x@v1] 条目' + seqs[0]
        skillset, prompts, _ = skills(MATCH_KNOWLEDGE=find, MATCH_BATCH=1)
        sources = [src(1, '绝缘子　U100BP/146D型', '60', '片'), src(2, '镀锌焊接钢管　DN50', '1', 't')]
        skillset.candidate_pairing.llm_pair(sources, [tgt(9, '耐张绝缘子串', '6', '串')], 'k', 't')
        self.assertEqual({tuple(seqs) for seqs, _ in asked}, {('1',), ('2',)})  # 首轮与二次补查都按批次检索
        self.assertTrue(any('绝缘子' in names[0] for _, names in asked))
        first = next(p for p in prompts if '1|绝缘子' in p)
        self.assertIn('本批物资相关知识', first)
        self.assertIn('条目1', first)
        self.assertNotIn('条目2', first)

    def test_without_knowledge_search_prompt_is_unchanged(self):
        skillset, prompts, _ = skills(MATCH_BATCH=5)
        skillset.candidate_pairing.llm_pair([src(1, '绝缘子', '60', '片')], [tgt(9, '耐张绝缘子串', '6', '串')], 'k', 't')
        self.assertNotIn('本批物资相关知识', prompts[0])

    def test_rows_keep_the_entries_used_for_their_own_batch(self):
        temp = tempfile.TemporaryDirectory()
        old_data, old_state, old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(temp.name)
        app.CONFIG['api_key'] = 'test-key'
        app.STATE.update(stage=1, scope='', rows=[], pairs={}, covers={}, messages=[], history=[],
                         dataset={'sources': [src(1, '绝缘子', '60', '片'), src(2, '钢管', '1', 't')],
                                  'targets': [tgt(9, '耐张绝缘子串', '6', '串')]})
        entry = {'id': 'kb-1', 'title': '绝缘子识别'}
        model = SimpleNamespace(match_pairs=lambda *a: ({'1': '9'}, {}), MATCH_KNOWLEDGE_USED={'1': [entry], '2': []})
        app.record_consent()
        try:
            with patch.object(app, 'engine', return_value=model):
                app.advance()
            rows = {r['source_id']: r for r in app.STATE['rows']}
            self.assertEqual((rows['1']['knowledge'], rows['2']['knowledge'], app.STATE['knowledge_used']), ([entry], [], [entry]))
        finally:
            app.DATA = old_data
            app.STATE.clear(); app.STATE.update(old_state)
            app.CONFIG.clear(); app.CONFIG.update(old_config)
            temp.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
