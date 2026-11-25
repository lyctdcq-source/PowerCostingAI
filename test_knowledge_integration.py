"""领域知识在清洗、配对、评审和平台流程中的接入。全部离线运行，不调用模型。"""
import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server as app
from folder_cleaning import extract_table
from skill_runtime import SkillStore

HERE = Path(__file__).resolve().parent


def load_engine(results_dir):
    os.environ['LLM_RESULTS_PATH'] = results_dir
    spec = importlib.util.spec_from_file_location('kg_engine_under_test', HERE / 'LLM_service.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._log = lambda *args, **kwargs: None
    return mod


def src(seq, name, qty, unit):
    return {'序号': seq, 'Estimation_Name': name, 'Estimation_Quantity': qty, 'Estimation_Unit': unit}


def tgt(seq, name, spec, qty, unit, **extra):
    return {'序号': seq, '设备材料名称': name, '型号及规范': spec, '数量': qty, '单位': unit, **extra}


CABLES_S = [src(1, '电力电缆　ZR-YJV22 1kV 双芯 10', '0.3', 'km'), src(2, '电力电缆　ZR-YJV22 1kV 双芯 4', '1', 'km'),
            src(3, '电力电缆　ZR-YJV22 1kV 四芯 4', '0.2', 'km')]
CABLES_T = [tgt(1, '低压电力电缆', 'ZAN-YJV22-0.6/1-2×10 mm²', '300', 'm'), tgt(2, '电力电缆', 'ZR-YJV22-0.6/1kV-2×4', '1000', '米'),
            tgt(3, '电力电缆', 'ZR-YJV22-0.6/1kV-4×4', '200', '米')]


class KnowledgeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.mod = load_engine(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    # ---------------------------------------------------------------- 清洗
    def test_cleaning_keeps_declared_components_and_marks_kinds(self):
        skills = SkillStore(Path(self.temp.name) / 'runtime', HERE / 'skill_templates')
        detail = dict(file='可研.xlsx', sheet='安装单位工程汇总表（表三甲）', rows=[
            ['序号', '编制依据', '项目名称及规格', '单位', '数量', '设备费单价', '装置性材料单价'],
            ['', '（甲设）', '1号主变保护屏：费用包含1.1~1.2', '面', 1, 120000, None],
            ['', '', '1.1\t、1号主变压器保护1', '台', 1, None, None],
            ['', '', '1.2\t、打印机', '台', 1, None, None],
            ['', '调 GD5-2*0.85', '控制盘台柜安装', '面', 1, 1193.54, 783.08],
            ['', '（甲设）', '低频低压减载屏', '面', 1, 9000, None]])
        mapping = dict(header_end=1, columns=dict(name=3, unit=4, qty=5, supply=2, equipment_price=6, material_price=7), quantity_basis='设计用量')
        result, audit = dict(sources=[], targets=[], rejected_rows=[]), dict(rows=0)
        extract_table(detail, mapping, 'sources', result, audit, skills)
        panel, other = result['sources']
        self.assertEqual([(c['name'], c['row']) for c in panel['成套组成']], [('1号主变压器保护1', 3), ('打印机', 4)])
        self.assertNotIn('成套组成', other)
        self.assertEqual((len(result['sources']), audit['component_rows']), (2, 2))

        inventory = dict(file='清册.xlsx', sheet='二次', rows=[['序号', '名称', '规格', '单位', '数量'],
                                                                ['1', '等级保护测评', '', '项', 1], ['2', '主时钟', '', '台', 1]])
        result = dict(sources=[], targets=[], rejected_rows=[])
        extract_table(inventory, dict(header_end=1, columns=dict(name=2, spec=3, unit=4, qty=5), quantity_basis='数量'),
                      'targets', result, dict(rows=0), skills)
        self.assertEqual([r.get('计量性质', '') for r in result['targets']], ['服务/调试费用', ''])

    # ---------------------------------------------------------------- 配对
    def test_rule_refine_reassigns_cable_cycle_by_cores_and_section(self):
        pairs = {'1': '2', '2': '3', '3': '1'}
        refined = self.mod.rule_refine(dict(pairs), CABLES_S, CABLES_T, 'test')
        self.assertEqual(refined, {'1': '1', '2': '2', '3': '3'})

    def test_llm_pair_drops_candidates_with_identity_conflict(self):
        answer = {'rows': [{'src': 1, 'candidates': [{'qc': 2, 'conf': 95}, {'qc': 1, 'conf': 90}]},
                           {'src': 2, 'candidates': [{'qc': 2, 'conf': 92}]},
                           {'src': 3, 'candidates': [{'qc': 1, 'conf': 91}]}]}
        with patch.object(self.mod, '_ask_llm', return_value=(answer, {})):
            pairs, cand = self.mod.llm_pair(CABLES_S, CABLES_T, 'dummy', 'test')
        self.assertEqual(pairs, {'1': '1', '2': '2'})
        self.assertNotIn('3', cand)

    # ---------------------------------------------------------------- 评审
    def review_rows(self, pairs, sources, targets):
        rows, group = [], 0
        def row(s, t):
            nonlocal group
            group += 1
            return {'分组序号': f'{group}_1', '源_序号': s['序号'] if s else '', '源_同类项名称': '测试', '源_名称': s['Estimation_Name'] if s else '',
                    '源_型号': '', '源_数量': s['Estimation_Quantity'] if s else '', '源_单位': s['Estimation_Unit'] if s else '',
                    '匹配_同类项名称': '测试', '匹配_发现': bool(t), '匹配_原因': '', '匹配_置信': '', '匹配_得分(%)': 99.0 if t and s else 0.0,
                    '细项_得分(%)': 0.0, '匹配_序号': t['序号'] if t else '', '匹配_数量': t['数量'] if t else '', '匹配_单位': t['单位'] if t else '',
                    '匹配_名称': t['设备材料名称'] if t else '', '匹配_型号': t['型号及规范'] if t else '', '匹配_价格': '', 'Top3_备选': [], '语义分组标签': str(group)}
        by_t = {t['序号']: t for t in targets}
        for s in sources:
            rows.append(row(s, by_t.get(pairs.get(s['序号']))))
        for t in targets:
            if t['序号'] not in pairs.values():
                rows.append(row(None, t))
        return self.mod.review(rows, '', 'test', sources, targets)

    def test_review_uses_knowledge_for_bands_and_evidence(self):
        sources = [src(1, '1号主变保护屏：费用包含1.1~1.2', '1', '面'), src(2, '电力电缆　ZR-YJV22 1kV 双芯 10', '0.3', 'km'),
                   src(3, '镀锌钢管　φ40', '4.92', 't'), src(4, '角钢（E-4-300，（50*5支架=0.5m ，40*4格架=0.30m））', '1.437', 't'),
                   src(5, '母线绝缘护套', '15', 'm')]
        sources[0]['成套组成'] = [dict(name='打印机', unit='台', quantity='1', row=497, label='1.6')]
        targets = [tgt(1, '1号主变保护屏', '', '1', '面'), tgt(2, '电力电缆', 'ZR-YJV22-0.6/1kV-2×4', '1000', '米'),
                   tgt(3, '热镀锌钢管', 'φ40', '1200', '米'), tgt(4, '600×600电缆沟支架', 'E-4-300，（50*5支架=0.5m ，40*4格架=0.30m）', '300', '套'),
                   tgt(5, '打印机', '', '1', '台'), tgt(6, '等级保护测评', '', '1', '项'), tgt(7, '铜母排', 'TMY-125×10，配绝缘护套及相色标识', '15', 'm')]
        rows = self.review_rows({1: 1, 2: 2, 3: 3, 4: 4}, sources, targets)
        by = {(r['源_序号'], r['匹配_序号']): r for r in rows}
        band = lambda key: by[key]['匹配_发现'].split('。')[0]
        # 未接入模型：配对行不用规则结论顶替，标为评审未完成，并列出程序核对与知识证据供人工复核
        self.assertEqual(band((2, 2)), '评审未完成')
        self.assertIn('芯数×截面', by[(2, 2)]['匹配_发现'])
        self.assertEqual(band((3, 3)), '评审未完成')
        self.assertIn('GB/T 3091-2015', by[(3, 3)]['匹配_发现'])
        self.assertEqual(band((4, 4)), '评审未完成')
        self.assertNotIn('匹配_得分', rows[0])
        self.assertEqual(band(('', 5)), '基本一致')
        self.assertEqual(by[('', 5)]['领域知识'][0]['type'], '成套组成（原表）')
        self.assertEqual(band(('', 6)), '不参与核对')
        self.assertEqual(band((5, '')), '基本一致')
        self.assertTrue(all(not k.startswith('_') for r in rows for k in r))

    def test_conversion_review_prefers_knowledge_and_old_table_is_not_used(self):
        review = self.mod._mass_length_conversion_review(
            {'设备材料名称': '镀锌钢管　φ32 mm', '单位': 't', '数量': '1.78'},
            {'Estimation_Name': '热镀锌钢管', 'Spec': 'φ32 mm', 'Estimation_Unit': 'm', 'Estimation_Quantity': '500'})
        self.assertIn('param.steel-pipe.gbt3091-2015', review['unit_text'])
        self.assertFalse(review['estimated_basis'])
        kg_m, basis = self.mod._steel_pipe_kg_per_m('热镀锌钢管 DN32')
        self.assertAlmostEqual(kg_m, 0.0246615 * (42.4 - 3.5) * 3.5 * (1.022 + 1.036) / 2, places=6)
        self.assertIn('2015', basis)


class PlatformKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_state, self.old_config = app.DATA, copy.deepcopy(app.STATE), dict(app.CONFIG)
        app.DATA = Path(self.temp.name)
        app.STATE.update(stage=1, scope='', rows=[], pairs={}, messages=[], history=[], logs=[])
        app.CONFIG.update(mode='online', api_key='test-key')
        app.STATE['dataset'] = app.annotate_dataset({'sources': [src(1, '主时钟', '1', '台')],
                                                     'targets': [tgt(1, '主时钟', '', '1', '台'), tgt(2, '等级保护测评', '', '1', '项')]})

    def tearDown(self):
        app.DATA = self.old_data
        app.STATE.clear(); app.STATE.update(self.old_state)
        app.CONFIG.clear(); app.CONFIG.update(self.old_config)
        self.temp.cleanup()

    def test_rows_carry_category_kind_domain_and_excluded_band(self):
        model = Mock()
        model.match_pairs.return_value = ({'1': '1'}, {})
        model.review.return_value = [
            {'源_序号': '1', '匹配_序号': '1', '匹配_发现': '一致。名称相同'},
            {'源_序号': '', '匹配_序号': '2', '匹配_发现': '不参与核对。服务/调试费用', '领域知识': [{'type': '计量性质', 'id': 'kind.service'}]}]
        app.record_consent()   # 用户已在确认框中同意发送
        with patch.object(app, 'engine', return_value=model):
            app.advance()
            self.assertEqual(app.STATE['rows'][0]['category'], ['二次及自动化设备', '时间同步设备'])
            self.assertEqual(app.STATE['rows'][1]['kind'], '服务/调试费用')
            app.advance()
        self.assertEqual(model.review.call_args.args[3:], (app.STATE['dataset']['sources'], app.STATE['dataset']['targets']))
        self.assertEqual(app.STATE['rows'][1]['band'], '不参与核对')
        self.assertEqual(app.STATE['rows'][1]['domain'][0]['id'], 'kind.service')


if __name__ == '__main__':
    unittest.main()
