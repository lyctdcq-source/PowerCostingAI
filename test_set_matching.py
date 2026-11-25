import unittest

import server as app
from domain_kg import DomainKnowledge
from pipeline_skills import PipelineContext, PipelineStore


def src(seq, name, qty='1', unit='套', price='1000', parent=None, **extra):
    item = {'序号': seq, 'Estimation_Name': name, 'Estimation_Quantity': qty, 'Estimation_Unit': unit, 'Estimation_Price': price,
            '型号及规范': '', '层级': '二次', '层级路径': ['二次']}
    if parent:
        item['上级物资'] = parent
    item.update(extra)
    return item


def tgt(seq, name, qty='1', unit='套', children=None, **extra):
    item = {'序号': seq, '设备材料名称': name, '数量': qty, '单位': unit, '型号及规范': '', '层级': '二次', '层级路径': ['二次']}
    if children:
        item['下级物资'] = children
    item.update(extra)
    return item


SOURCES = [src(1, '110kV 变电站直流系统　4面馈线屏，2面充电屏', price='321200'),
           src(2, '阀控式密封铅酸蓄电池', qty='2', unit='组', price='84000'),
           src(3, '电子围栏　主机2台，390米', price='82088'),
           src(4, '环境及视频监控系统', price='959674'),
           src(5, '打印机', unit='台', price='3000')]
TARGETS = [tgt(10, '直流一体化电源系统', children=[11, 12, 13], 成套行=True),
           tgt(11, '充电机屏', '2', '面'), tgt(12, '直流馈线屏', '4', '面'), tgt(13, '阀控式密封铅酸蓄电池', '2', '组'),
           tgt(20, '电子围栏', children=[21, 22], 成套行=True), tgt(21, '脉冲电子围栏主机', '2'), tgt(22, '承力杆', '140', '根'),
           tgt(30, '打印机', unit='台')]


class SetMatchingTests(unittest.TestCase):
    def skills(self, groups):
        prompts = []

        def ask(prompt, api_key, max_tokens=3000, retries=3):
            prompts.append(prompt)
            return ({'groups': groups} if '成套对应组' in prompt else {}), {}
        return PipelineStore().instantiate(PipelineContext(ask=ask, knowledge=DomainKnowledge())), prompts

    def test_site_is_judged_by_station_name_not_parent_wording(self):
        """父级只是两侧各自的归类：造价“对侧间隔 / 35kV弄岛变”与清册“35kV弄岛变”是同一站点；不同站名、本站对对侧仍区分。"""
        skills, _ = self.skills([])
        same = skills.text_rules.same_site
        path = lambda *p: {'层级路径': list(p)}
        self.assertTrue(same(path('对侧间隔', '35kV弄岛变', '二次'), path('35kV弄岛变')))
        self.assertFalse(same(path('对侧间隔', '35kV弄岛变'), path('35kV班岭变')))
        self.assertFalse(same(path('110kV配电装置部分'), path('对侧220kV汉弄变扩建110kV间隔工程部分')))
        self.assertTrue(same(path('站用配电装置'), path('10kV站用变部分')))      # 站用变不是站名
        self.assertTrue(same(path('主变压器系统', '110kV主变压器'), path('主变压器部分')))

    def test_set_review_sees_hardware_written_in_the_inventory_spec(self):
        """清册耐张串把金具写在规格“内含……”里：成套复核看到完整规格，组成比对不得标“清册未列”。"""
        skills, _ = self.skills([])
        cov = skills.set_coverage
        string = tgt(40, '耐张绝缘子串', '12', '串', 型号及规范='2x9(U100BP/146D) 内含：18片U100BP/146D型绝缘子，2只U-21型U型挂环；'
                     '4只U-10型U型挂环；1只L-2140型联板；2只QP-10型球头挂环；2只WS-10型碗头挂板；1只LV-1640联板，2只Z-12挂板。')
        self.assertTrue(cov._written_in_spec({'name': 'U形挂环　U-21型'}, string))
        self.assertTrue(cov._written_in_spec({'name': '挂板　Z-12'}, string))
        self.assertFalse(cov._written_in_spec({'name': '均压环　FJ-500'}, string))
        self.assertIn('2只Z-12挂板', cov._brief(string, cov.SET_SPEC_LIMIT))
        self.assertNotIn('Z-12', cov._brief(string))   # 普通条目仍截短

    def test_many_to_many_groups_with_inner_pairs(self):
        groups = [{'name': '直流系统', 'kesheng': ['K1', 'K2'], 'qingce': ['Q10', 'Q11', 'Q12', 'Q13'],
                   'pairs': [['K2', 'Q13']], 'basis': '直流系统含充电屏、馈线屏，蓄电池另列'},
                  {'name': '电子围栏', 'kesheng': ['K3'], 'qingce': ['Q20', 'Q21', 'Q22'], 'pairs': [], 'basis': '电子围栏'},
                  {'name': '打印机', 'kesheng': ['K5'], 'qingce': ['Q30'], 'pairs': [['K5', 'Q30']], 'basis': '同一打印机'}]
        skills, prompts = self.skills(groups)
        pairs, covers = skills.set_matching.match_sets(SOURCES, TARGETS, {'1': '11', '4': '20'}, 'key', 'task')
        self.assertIn('K1 110kV 变电站直流系统', prompts[0])
        self.assertEqual(pairs['1'], '10')        # 主项改配到清册成套行，原来与充电机屏的一对一被组内关系取代
        self.assertEqual(pairs['2'], '13')        # 组内逐项配对保留，逐项核对数量
        self.assertEqual(pairs['5'], '30')        # 一对一的组只是配对，不形成成套组
        self.assertNotIn('5', covers)
        self.assertEqual({(c.get('source'), c.get('target')) for c in covers['1']},
                         {('2', '13'), (None, '11'), (None, '12')})
        self.assertEqual(pairs['3'], '20')        # 电子围栏单独计价，自成一组；原先误配到视频监控系统的配对被拆开
        self.assertNotIn('4', pairs)
        self.assertEqual({c.get('target') for c in covers['3']}, {'21', '22'})

        rows = app.pair_rows(SOURCES, TARGETS, pairs, covers)
        by = {(r['source_id'], r['target_id']): r for r in rows}
        self.assertEqual(by[('1', '10')]['covers'], ['11', '12'])
        self.assertEqual(by[('1', '10')]['set_pairs'], [['2', '13']])
        self.assertEqual(by[('2', '13')]['in_set'], '1')
        self.assertEqual(by[('', '11')]['covered_by'], '1')
        self.assertEqual(sorted(r['source_id'] for r in rows if r['source_id']), ['1', '2', '3', '4', '5'])
        self.assertEqual(sorted(r['target_id'] for r in rows if r['target_id']), ['10', '11', '12', '13', '20', '21', '22', '30'])

        built = skills.check_units.build_rows(SOURCES, TARGETS, pairs, covers, 'task', '')
        anchor = next(r for r in built if r['源_序号'] == 1)
        self.assertEqual(anchor['成套项_组内配对'], [[2, 13]])
        self.assertEqual(next(r for r in built if r['源_序号'] == 2)['成套项_所属'], 1)
        fence = next(r for r in built if r['匹配_序号'] == 21)
        self.assertEqual(fence['成套项_可研序号'], 3)

    def test_invalid_or_duplicate_members_are_dropped(self):
        groups = [{'kesheng': ['K1', 'K99'], 'qingce': ['Q10', 'Q11'], 'pairs': [['K1', 'Q99']]},
                  {'kesheng': ['K1'], 'qingce': ['Q12', 'Q13']},           # K1 已在上一组
                  {'kesheng': ['K2'], 'qingce': []}]                       # 缺一侧
        skills, _ = self.skills(groups)
        pairs, covers, broken = skills.set_matching.apply_groups(groups, SOURCES, TARGETS, {})
        self.assertEqual(pairs, {'1': '10'})
        self.assertEqual(covers, {'1': [{'target': '11', 'basis': covers['1'][0]['basis']}]})

    def test_batches_merge_by_kesheng_and_anchor_keeps_original_pair(self):
        skills, _ = self.skills([])
        merged = skills.set_matching._merge([{'kesheng': ['K4'], 'qingce': ['Q21'], 'basis': '视频监控'},
                                             {'kesheng': ['K4'], 'qingce': ['Q22'], 'pairs': []},
                                             {'kesheng': ['K5'], 'qingce': ['Q30']}])
        self.assertEqual([(m['kesheng'], m['qingce']) for m in merged], [(['K4', 'K4'], ['Q21', 'Q22']), (['K5'], ['Q30'])])
        # 模型漏列主项原来的对应物：补回组内，原配对不丢
        pairs, covers, broken = skills.set_matching.apply_groups([{'kesheng': ['K3'], 'qingce': ['Q21', 'Q22']}], SOURCES, TARGETS, {'3': '20'})
        self.assertEqual((pairs['3'], broken), ('20', 0))
        self.assertEqual({c['target'] for c in covers['3']}, {'21', '22'})

    def test_member_pairs_are_kept_and_remote_station_items_are_separated(self):
        skills, _ = self.skills([])
        targets = TARGETS + [tgt(40, '悬垂绝缘子串', '3', '串', 层级路径=['对侧220kV瑞丽变扩建110kV间隔工程部分'])]
        groups = [{'kesheng': ['K1'], 'qingce': ['Q10', 'Q11', 'Q12', 'Q13', 'Q40']}]
        pairs, covers, broken = skills.set_matching.apply_groups(groups, SOURCES, targets, {'1': '10', '2': '13'})
        self.assertEqual(broken, 0)
        self.assertEqual(pairs['2'], '13')       # 蓄电池原配对保留为组内逐项配对
        self.assertIn({'source': '2', 'target': '13', 'basis': covers['1'][0]['basis']}, covers['1'])
        self.assertNotIn('40', {c.get('target') for c in covers['1']})   # 对侧条目不进本站的组

    def test_existing_pairs_survive_scrambled_insulator_groups(self):
        """实测：模型把 108 片绝缘子编到 12 串的组里。规则校验已按数量配好的 216片↔12串、108片↔6串 不应被改掉。"""
        skills, _ = self.skills([])
        sources = [src(51, '绝缘子　U100BP/146D型', '216', '片', '102'), src(52, 'U形挂环　U-21型', '24', '只', '30'),
                   src(59, '绝缘子　U100BP/146D型', '108', '片', '102'), src(60, 'U形挂环　U-21型', '18', '只', '30')]
        targets = [tgt(25, '耐张绝缘子串', '12', '串'), tgt(26, '耐张绝缘子串', '6', '串')]
        groups = [{'kesheng': ['K59', 'K52'], 'qingce': ['Q25']}, {'kesheng': ['K60'], 'qingce': ['Q26']}]
        pairs, covers, _ = skills.set_matching.apply_groups(groups, sources, targets, {'51': '25', '59': '26'})
        self.assertEqual((pairs['51'], pairs['59']), ('25', '26'))
        self.assertEqual({(c.get('source'), c.get('target')) for c in covers['51']}, {('59', '26'), ('52', None)})

    def test_insulator_strings_follow_count_rule_not_the_model(self):
        sk, _ = self.skills([])
        chapter = {'层级路径': ['屋外配电装置', '110kV配电装置']}
        sources = [src(51, '绝缘子　U100BP/146D型', '216', '片', '102', **chapter), src(52, 'U形挂环　U-21型', '24', '只', '30', **chapter),
                   src(53, '联板　L-2140型', '12', '只', '26', **chapter), src(54, '线路 耐张线夹(压缩式)', '34', '件', '90', **chapter),
                   src(59, '绝缘子　U100BP/146D型', '108', '片', '102', **chapter), src(60, 'U形挂环　U-21型', '18', '只', '30', **chapter)]
        spec = '2x9(U100BP/146D) 内含：18片U100BP/146D型绝缘子，3只U-21型U型挂环'
        targets = [tgt(25, '耐张绝缘子串', '12', '串', 型号及规范=spec, 层级路径=['110kV配电装置部分']),
                   tgt(26, '耐张绝缘子串', '6', '串', 型号及规范=spec, 层级路径=['110kV配电装置部分'])]
        groups = sk.set_matching.string_groups(sources, targets)
        self.assertEqual([(g['qingce'], g['kesheng']) for g in groups], [(['25'], ['51', '52', '53']), (['26'], ['59', '60'])])
        # 前一步把挂环错配到 6 串上：规则编组纠正为 108 片 ↔ 6 串
        pairs, covers, _ = sk.set_matching.apply_groups(groups, sources, targets, {'60': '26', '51': '25'})
        self.assertEqual((pairs['51'], pairs['59'], pairs.get('60')), ('25', '26', None))
        self.assertEqual({c.get('source') for c in covers['59']}, {'60'})

    def test_chapter_sweep_adds_missed_components_only(self):
        sk, _ = self.skills([])
        fire = {'层级路径': ['火灾报警系统']}
        targets = [tgt(i, '探测器%d' % i, **fire) for i in range(1, 6)] + [tgt(9, '打印机', 层级路径=['其他'])]
        covers = {'4': [{'target': str(i), 'basis': 'b'} for i in (1, 2, 3, 4)]}
        added = sk.set_matching.sweep_chapters(covers, SOURCES, targets, {'4': '5'})
        self.assertEqual(added, 0)                  # 5 号已与造价配对，不动；其余都已在组里
        targets.append(tgt(6, '感温探测器', **fire))
        self.assertEqual(sk.set_matching.sweep_chapters(covers, SOURCES, targets, {'4': '5'}), 1)
        self.assertEqual(covers['4'][-1]['target'], '6')
        self.assertNotIn('9', {c['target'] for c in covers['4']})

    def test_no_model_falls_back_to_structure(self):
        skills, prompts = self.skills([])
        pairs, covers = skills.set_matching.match_sets(SOURCES, TARGETS, {'1': '10'}, '', 'task')
        self.assertEqual(prompts, [])
        self.assertEqual(pairs, {'1': '10'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
