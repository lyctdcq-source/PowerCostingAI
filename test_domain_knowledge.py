import unittest
from domain_kg import DomainKnowledge


def src(name, qty='1', unit='台', **extra):
    return dict(Estimation_Name=name, Estimation_Quantity=qty, Estimation_Unit=unit, **extra)


def tgt(name, spec='', qty='1', unit='台', **extra):
    return {'设备材料名称': name, '型号及规范': spec, '数量': qty, '单位': unit, **extra}


class DomainKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kg = DomainKnowledge()

    def category(self, item):
        node = self.kg.classify(item)
        return node and node['id']

    def test_knowledge_base_is_consistent(self):
        self.assertEqual(self.kg.validate(), [])

    def test_specific_categories_win_over_general_words(self):
        cases = {'1.1\t、1号主变压器保护1': 'sec.protection', '主变端子箱': 'pri.box', '开关柜电动底盘': 'pri.trolley',
                 '2.5Gbit/s光电端机(ADM型)  光传输设备的主要部件（包括主控、交叉板、时钟板）': 'comm.transmission',
                 '保护网线用槽盒　阻燃PVC槽盒': 'cable.tray', '阴极保护装置': 'gnd.cathodic', '1号主变保护屏：费用包含1.1~1.6': 'sec.panel',
                 '角钢（E-4-300，（50*5支架=0.5m ，40*4格架=0.30m））': 'cable.support'}
        for name, expected in cases.items():
            self.assertEqual(self.category(src(name)), expected, name)

    def test_cable_cores_and_section_define_identity(self):
        estimate = src('阻燃交联乙烯绝缘钢带铠装聚氯乙烯护套电力电缆　ZR-YJV22 1kV 双芯 10', '0.3', 'km')
        wrong = self.kg.compare(estimate, tgt('电力电缆', 'ZR-YJV22-0.6/1kV-2×4', '1000', '米'))
        right = self.kg.compare(estimate, tgt('低压电力电缆', 'ZAN-YJV22-0.6/1-2×10 mm²', '300', 'm'))
        self.assertEqual([(f['attr'], f['severity']) for f in wrong if f['severity'] == '冲突'], [('cores_section', '冲突')])
        self.assertFalse(any(f['severity'] == '冲突' for f in right))
        self.assertTrue(any(f['attr'] == 'fire_class' for f in right))

    def test_single_core_armour_difference_is_only_a_hint(self):
        single = self.kg.compare(src('电力电缆　ZR-YJV22 15kV 单芯 300', '0.225', 'km'), tgt('10kV电力电缆', 'ZA-YJV62-8.7/15-1×300mm²', '225', 'm'))
        three = self.kg.compare(src('电力电缆　ZR-YJV22 10kV 三芯 300', '0.4', 'km'), tgt('10kV电力电缆', 'ZA-YJV62-8.7/15-3×300mm²', '400', 'm'))
        self.assertEqual([f['severity'] for f in single if f['attr'] == 'cable_armour'], ['提示'])
        self.assertEqual([f['severity'] for f in three if f['attr'] == 'cable_armour'], ['冲突'])

    def test_pending_knowledge_is_never_decisive(self):
        a, b = src('电力电缆　ZR-YJV22 1kV 双芯 10', '0.3', 'km'), tgt('电力电缆', 'ZR-YJV22-0.6/1kV-2×4', '1000', '米')
        self.assertTrue(self.kg.conflicts(a, b))
        self.kg.taxonomy['status'] = '待审核'
        try:
            findings = self.kg.compare(a, b)
            self.assertTrue(findings and not any(f['decisive'] for f in findings))
            self.assertEqual(self.kg.conflicts(a, b), [])
        finally:
            self.kg.taxonomy['status'] = '已审核'

    def test_cable_voltage_ratings_map_to_system_voltage(self):
        self.assertEqual(self.kg.voltage_system('ZA-YJV22-8.7/15-3X300MM2'), '10kV系统')
        self.assertEqual(self.kg.voltage_system('ZR-YJV22 10KV 三芯 300'), '10kV系统')
        self.assertEqual(self.kg.voltage_system('ZR-YJV22-0.6/1KV-2X4'), '低压(0.6/1kV)')

    def test_galvanized_pipe_uses_gbt3091_2015_table_and_coefficients(self):
        mass = self.kg.linear_mass(tgt('热镀锌钢管', 'φ40', '1200', '米'))
        black = 0.0246615 * (48.3 - 3.5) * 3.5
        self.assertAlmostEqual(mass['range'][0], black * 1.022, places=6)
        self.assertAlmostEqual(mass['range'][1], black * 1.036, places=6)
        self.assertAlmostEqual(self.kg.linear_mass(tgt('焊接钢管', 'DN40'))['range'][0], black, places=6)

    def test_support_mass_comes_from_spec_profiles(self):
        item = tgt('600×600电缆沟支架', 'E-4-300，（50*5支架=0.5m ，40*4格架=0.30m）', '300', '套')
        self.assertAlmostEqual(self.kg.piece_mass(item)['range'][0], 0.5 * 3.770 + 4 * 0.30 * 2.422, places=6)
        check = self.kg.quantity_check(src('角钢（E-4-300，（50*5支架=0.5m ，40*4格架=0.30m））', '1.437', 't'), item)
        self.assertEqual(check['verdict'], '一致')
        gap = self.kg.quantity_check(src('角钢（E-1-400，（格架=0.40m））', '0.452', 't'), tgt('400×400电缆沟支架', 'E-1-400，（格架=0.40m）', '300', '套'))
        self.assertEqual(gap['verdict'], '无法核实')

    def test_back_calculated_density_points_to_wrong_material(self):
        check = self.kg.quantity_check(src('紫铜　—50×5', '0.314', 't'), tgt('紫铜', '-50×5mm', '160', 'm'))
        self.assertEqual(check['verdict'], '不一致')
        self.assertIn('与钢的密度吻合', check['diagnostic'])

    def test_quantity_units_without_conversion_need_no_knowledge(self):
        self.assertIsNone(self.kg.quantity_check(src('打印机'), tgt('打印机')))

    def test_declared_components_stop_at_next_priced_or_quota_row(self):
        rows = [['', '（甲设）', '1号主变保护屏：费用包含1.1~1.2', '面', '1', '120000'],
                ['', '', '1.1\t、1号主变压器保护1', '台', '1', ''],
                ['', '', '1.2\t、打印机', '台', '1', ''],
                ['', '调 GD5-2*0.85', '控制盘台柜安装', '面', '1', '1193.54'],
                ['', '', '不应纳入', '台', '1', '']]
        cols = dict(name=3, unit=4, qty=5, supply=2)
        comps = self.kg.declared_components(rows, 0, cols)
        self.assertEqual([(c['name'], c['label'], c['row']) for c in comps], [('1号主变压器保护1', '1.1', 2), ('打印机', '1.2', 3)])
        self.assertEqual(self.kg.declared_components(rows, 1, cols), [])
        matched, score, qty_ok = self.kg.match_component(tgt('打印机'), comps)
        self.assertEqual((matched['row'], qty_ok), (3, True))

    def test_measurement_kinds(self):
        self.assertEqual(self.kg.kind(tgt('更换原35kV待上线间隔保护调试', unit='项'))['id'], 'kind.service')
        self.assertEqual(self.kg.kind(tgt('更换原35kV期片线间隔标识牌', unit='项'))['id'], 'kind.lump')
        self.assertEqual(self.kg.kind(tgt('控制电缆', qty='500', unit='米', 层级路径=['对侧35kV待补变电站', '拆除量']))['id'], 'kind.demolition')
        self.assertIsNone(self.kg.kind(tgt('控制电缆', qty='500', unit='米')))

    def test_accessory_declared_in_other_side_spec(self):
        holder = tgt('铜母排', 'TMY-125×10，配绝缘护套及相色标识', '15', 'm')
        self.assertEqual(self.kg.accessory_of(src('母线绝缘护套', '15', 'm'), holder), '绝缘护套')
        self.assertEqual(self.kg.accessory_of(src('设备线夹', '1', '件'), holder), '')

    def test_post_insulator_ratings_follow_system_voltage(self):
        same = self.kg.compare(src('电站电瓷 高压棒式支柱绝缘子　ZSW-35/8', '3', '只'), tgt('35kV支柱绝缘子', 'ZSW-40.5/8', '3', '套'))
        weaker = self.kg.compare(src('高压棒式支柱绝缘子　ZSW-40.5/4', '3', '只'), tgt('35kV支柱绝缘子', 'ZSW-40.5/8', '3', '套'))
        self.assertFalse(any(f['severity'] == '冲突' for f in same))
        self.assertEqual([f['attr'] for f in weaker if f['severity'] == '冲突'], ['post_insulator'])

    def test_approved_entries_must_record_review(self):
        entry = self.kg.params['pipe_size_rule']
        saved = entry.pop('review')
        try:
            self.assertTrue(any('审核人' in p for p in self.kg.validate()))
        finally:
            entry['review'] = saved

    def test_missing_profile_still_gives_audit_hint(self):
        check = self.kg.quantity_check(src('角钢（E-1-400，（格架=0.40m））', '0.452', 't'),
                                       tgt('400×400电缆沟支架', 'E-1-400，（格架=0.40m）', '300', '套'))
        self.assertEqual(check['verdict'], '无法核实')
        self.assertIn('∠50×5', check['diagnostic'])

    def test_conflicting_pairs_are_reassigned_within_category(self):
        sources = {'1': src('电力电缆　ZR-YJV22 1kV 双芯 10', '0.3', 'km'), '2': src('电力电缆　ZR-YJV22 1kV 四芯 4', '0.2', 'km')}
        targets = {'1': tgt('电力电缆', 'ZR-YJV22-0.6/1kV-4×4', '200', '米'), '2': tgt('电力电缆', 'ZR-YJV22-0.6/1kV-2×10', '300', '米'),
                   '3': tgt('电力电缆', 'ZR-YJV22-0.6/1kV-2×4', '1000', '米')}
        pairs = {'1': '1', '2': '3'}
        changes = self.kg.resolve_conflicts(pairs, sources, targets)
        self.assertEqual(pairs, {'1': '2', '2': '1'})
        self.assertEqual(len(changes), 2)

    def test_unpaired_explanations_and_accessory_holder_choice(self):
        sheath = src('母线绝缘护套', '15', 'm', 层级路径=['主要生产工程', '35kV主变压器'])
        holders = [tgt('铜母排', 'TMY-50×5，配绝缘护套', '15', 'm', 序号=29, 层级路径=['35kV配电装置部分']),
                   tgt('铜母排', 'TMY-125×10，配绝缘护套', '15', 'm', 序号=5, 层级路径=['主变部分'])]
        accessory = self.kg.explain_unpaired(sheath, 'source', [], holders)
        self.assertEqual((accessory['type'], accessory['band'], accessory['related']), ('规格声明附件', '基本一致', 5))
        clock = src('时钟同步系统 ：', '1', '套', 序号=134)
        template = self.kg.explain_unpaired(tgt('主时钟', '具备GPS+北斗对时功能'), 'target', [clock], [])
        self.assertEqual((template['band'], template['related']), ('待复核', 134))
        self.assertEqual(self.kg.explain_unpaired(tgt('膨胀螺栓', '', '250', '只'), 'target', [clock], []), None)

    def test_graph_export_links_every_edge_to_known_nodes(self):
        graph = self.kg.graph()
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))


if __name__ == '__main__':
    unittest.main()
