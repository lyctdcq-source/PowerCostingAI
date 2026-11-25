"""平台流程技能：安全检查、版本管理、保存校验、成套结构与核对单元。全部离线运行。"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server as app
from domain_kg import DomainKnowledge
from pipeline_skills import SAFE_BUILTINS, PipelineContext, PipelineStore, SkillSet, validate_source
from skill_runtime import SkillStore

HERE = Path(__file__).resolve().parent


def src(seq, name, qty, unit, price='', **extra):
    return {'序号': seq, 'Estimation_Name': name, 'Estimation_Quantity': qty, 'Estimation_Unit': unit, 'Estimation_Price': price, **extra}


def tgt(seq, name, qty, unit, spec='', **extra):
    return {'序号': seq, '设备材料名称': name, '型号及规范': spec, '数量': qty, '单位': unit, **extra}


class PipelineStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PipelineStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_every_builtin_skill_passes_static_checks(self):
        for item in self.store.describe():
            self.assertEqual(len(validate_source(item['id'], item['code'])), 3, item['id'])
            self.assertFalse(item['modified'])

    def test_forbidden_capabilities_are_rejected(self):
        base = self.store.code('result_export')
        for bad, reason in [('import os\n', '不允许导入'), ('X = open("a.txt")\n', 'open'),
                            ('Y = columns.__globals__\n', '双下划线'), ('Z = getattr(columns, "x")\n', 'getattr')]:
            with self.assertRaisesRegex(ValueError, reason):
                validate_source('result_export', bad + base)
        with self.assertRaisesRegex(ValueError, '缺少接口函数 flatten'):
            validate_source('result_export', base.replace('def flatten(rows):', 'def flatten_rows(rows):'))
        self.assertNotIn('open', SAFE_BUILTINS)
        with self.assertRaises(ImportError):
            SAFE_BUILTINS['__import__']('os')

    def test_save_rollback_and_reset_create_versions(self):
        code = self.store.code('result_export')
        changed = code.replace('"成套项_复核"]', '"成套项_复核", "备注"]')
        record = self.store.apply('result_export', changed, '增加备注列', '导出增加备注列')
        self.assertIn('备注', self.store.code('result_export'))
        self.assertTrue(any('样例匹配、评审与导出通过' in t for t in record['tests']))
        self.assertTrue(next(x for x in self.store.describe() if x['id'] == 'result_export')['modified'])
        self.store.rollback(record['id'])
        self.assertEqual(self.store.code('result_export'), code)
        self.store.apply('result_export', changed, '再次修改', '再次修改')
        self.store.reset('result_export')
        self.assertEqual(self.store.code('result_export'), self.store.template('result_export'))
        self.assertEqual(len([v for v in self.store.versions if v['skill_id'] == 'result_export']), 4)

    def test_changes_that_break_the_pipeline_are_refused(self):
        code = self.store.code('check_units')
        with self.assertRaisesRegex(ValueError, '每条物资恰好出现一次'):
            self.store.apply('check_units', code.replace('            rows.append(row)\n', '            pass\n'), '误删', '误删')
        with self.assertRaisesRegex(ValueError, '语法错误'):
            self.store.apply('check_units', code + '\ndef broken(:\n', '语法', '语法')
        self.assertEqual(self.store.code('check_units'), code)

    def test_behaviour_changes_are_reported_not_blocked(self):
        code = self.store.code('set_coverage')
        record = self.store.apply('set_coverage', code.replace('SET_NAME = r"系统|成套"', 'SET_NAME = r"不会命中的词"'), '收紧', '收紧成套判定')
        self.assertTrue(any('行为检查' in t and '成套项只覆盖了' in t for t in record['tests']))

    def test_unmodified_runtime_copy_follows_template_upgrade(self):
        templates = Path(self.temp.name) / 'tpl'
        templates.mkdir()
        for path in (HERE / 'skill_templates' / 'pipeline').glob('*.py'):
            (templates / path.name).write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        data = Path(self.temp.name) / 'data'
        PipelineStore(data, templates)
        (templates / 'result_export.py').write_text((templates / 'result_export.py').read_text(encoding='utf-8') + '\n# 升级\n', encoding='utf-8')
        self.assertIn('# 升级', PipelineStore(data, templates).code('result_export'))

    def test_each_run_gets_isolated_skill_modules(self):
        codes = self.store.codes()
        first, second = SkillSet(codes, PipelineContext()), SkillSet(codes, PipelineContext())
        first.rule_check._TOKEN_CACHE['x'] = {'Y'}
        self.assertNotIn('x', second.rule_check._TOKEN_CACHE)


class SetStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.rules = SkillStore(Path(cls.temp.name), HERE / 'skill_templates')
        cls.kg = DomainKnowledge()
        cls.skills = PipelineStore().instantiate(PipelineContext(knowledge=cls.kg))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_inventory_set_rows_become_parents_of_their_components(self):
        table = dict(file='清册.xlsx', sheet='二次', rows=[
            ['序号', '名称', '规格', '单位', '数量'],
            ['（七）', '端子箱', '', '', ''], ['1', '主变端子箱', '', '台', 1],
            ['（八）', '时钟同步系统', '', '套', 1], ['1', 'GPS对时屏', '', '面', 1], ['2', '主时钟', '', '台', 1],
            ['（九）', '计量系统', '', '', ''], ['1', '电能表屏', '', '面', 1], ['1.1', '电能表', '', '只', 5]])
        result = dict(sources=[], targets=[], rejected_rows=[])
        self.skills.row_extraction.extract_table(table, dict(header_end=1, columns=dict(name=2, spec=3, unit=4, qty=5), quantity_basis='数量'),
                                                 'targets', result, dict(rows=0), self.rules)
        by = {r['设备材料名称']: r for r in result['targets']}
        self.assertEqual(by['时钟同步系统']['层级'], '二次')
        self.assertTrue(by['时钟同步系统']['成套行'])
        self.assertEqual(by['时钟同步系统']['下级物资'], [by['GPS对时屏']['序号'], by['主时钟']['序号']])
        self.assertEqual((by['主时钟']['层级'], by['主时钟']['成套项']['名称']), ('时钟同步系统', '时钟同步系统'))
        self.assertEqual(by['电能表']['上级物资'], by['电能表屏']['序号'])
        self.assertNotIn('成套项', by['电能表'])

    def test_check_unit_names_follow_original_structure(self):
        name = self.skills.check_units.unit_name
        clock_set = src(1, '时钟同步系统 ：', '1', '套')
        self.assertEqual(name(None, tgt(2, '主时钟', '1', '台'), clock_set, tgt(9, '时钟同步系统', '1', '套')), '时钟同步系统')
        self.assertEqual(name(None, tgt(3, '公用测控装置', '6', '台', 层级='公用测控部分'), None, None), '公用测控部分')
        self.assertEqual(name(None, tgt(4, 'III区网关', '1', '台', 层级='智能辅助', 成套项={'序号': 1, '名称': '智能网关'}), None, None), '智能网关')
        self.assertEqual(name(src(5, '电力电缆', '1', 'km', 层级='一次部分'), None, None, None), '一次部分')
        self.assertEqual(name(src(6, '膨胀螺栓', '1', '只'), None, None, None), '膨胀螺栓')

    def test_whole_priced_sets_cover_components_and_itemized_systems_do_not(self):
        sets = self.skills.set_coverage
        sources = [src(1, '时钟同步系统 ：', '1', '套', '68200'),
                   src(2, '1、监控主站兼操作员站', '1', '套', '50000', 编号='1'),
                   src(3, '1.1、主机/操作员工作站', '2', '台', '30000', 编号='1.1', 上级物资=2)]
        targets = [tgt(1, '时钟同步系统', '1', '套', 成套行=True, 下级物资=[2, 3]), tgt(2, 'GPS对时屏', '1', '面'), tgt(3, '主时钟', '1', '台'),
                   tgt(4, '监控主站兼操作员站', '1', '套'), tgt(5, '主机/操作员工作站', '2', '台')]
        self.assertTrue(sets.is_whole_priced(sources[0], sources))
        self.assertFalse(sets.is_whole_priced(sources[1], sources))
        covers = sets.cover_sets(sources, targets, {'1': '1', '2': '4', '3': '5'}, '', 'test')
        self.assertEqual({k: [c['target'] for c in v] for k, v in covers.items()}, {'1': ['2', '3']})

    def test_set_rows_get_holistic_model_verdict_or_unreviewed(self):
        sources = [src(1, '时钟同步系统 ：', '1', '套', '68200')]
        targets = [tgt(1, '时钟同步系统', '1', '套', 成套行=True, 下级物资=[2]), tgt(2, '主时钟', '1', '台')]
        rows = self.skills.check_units.build_rows(sources, targets, {'1': '1'}, {'1': [{'target': '2', 'basis': '清册原表成套结构'}]}, 'test', '')
        reviewed = self.skills.match_flow.review(copy.deepcopy(rows), '', 'test', sources, targets)
        bands = [r['匹配_发现'].split('。')[0] for r in reviewed]
        # 未接入模型时不按规则定档：成套项与组成件同为评审未完成，附程序比对供人工复核
        self.assertEqual(bands, ['评审未完成', '评审未完成'])
        self.assertIn('成套对应（多对多）', reviewed[1]['匹配_发现'])
        self.assertIn('程序比对', reviewed[0]['匹配_发现'])
        self.assertEqual(reviewed[0]['成套项_复核'], '评审未完成')
        self.assertEqual({r['源_同类项名称'] for r in reviewed}, {'时钟同步系统'})

        prompts = []
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            prompts.append(prompt)
            if '整体复核的成套项' in prompt:
                return {'sets': [{'row_id': 0, 'band': '一致', 'finding': '一致。同一时钟同步系统，整套数量一致，主时钟配置相符。'}]}, {}
            return {}, {}
        live = PipelineStore().instantiate(PipelineContext(ask=ask, knowledge=self.kg))
        reviewed = live.match_flow.review(copy.deepcopy(rows), 'key', 'test', sources, targets)
        self.assertTrue(reviewed[0]['匹配_发现'].startswith('一致。同一时钟同步系统'))
        self.assertTrue(reviewed[1]['匹配_发现'].startswith('一致。成套对应（多对多）'))
        self.assertEqual(reviewed[0]['成套项_复核'], '模型整体复核')
        self.assertTrue(any('主时钟' in p and '整体复核' in p for p in prompts))


class RowReviewTests(unittest.TestCase):
    """配对行的结论与原因来自模型同一次评审；程序只校验，不通过则重试，仍不通过标为评审未完成。"""

    @classmethod
    def setUpClass(cls):
        cls.kg = DomainKnowledge()
        cls.sources = [src(1, '主变压器', '1', '台'), src(2, '电力电缆', '300', 'm')]
        cls.targets = [tgt(1, '主变压器', '1', '台', 'SZ11-8000/35'), tgt(2, '电力电缆', '200', 'm', 'YJV22-3×70')]

    def review(self, ask):
        live = PipelineStore().instantiate(PipelineContext(ask=ask, knowledge=self.kg))
        rows = live.check_units.build_rows(self.sources, self.targets, {'1': '1', '2': '2'}, {}, 'test', '')
        reviewed = live.match_flow.review(rows, 'key', 'test', self.sources, self.targets)
        return {str(r['源_序号']): r for r in reviewed}

    @staticmethod
    def row_ids(prompt):
        import json
        payload = json.loads(prompt.split('待评审记录：', 1)[1])
        return {str(p['source']['seq']): p['row_id'] for p in payload}, payload

    def test_model_band_and_reason_are_used_together(self):
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' not in prompt:
                return {}, {}
            ids, _ = self.row_ids(prompt)
            out = []
            if '1' in ids:
                out.append({'row_id': ids['1'], 'band': '一致', 'finding': '一致。同为主变压器，型号相符，单位台、数量1吻合。'})
            if '2' in ids:
                out.append({'row_id': ids['2'], 'band': '部分一致', 'finding': '部分一致。数量不一致：造价300m，清册200m，相对差33.33%，超过1%。'})
            return {'findings': out}, {}
        by = self.review(ask)
        self.assertTrue(by['1']['匹配_发现'].startswith('一致。同为主变压器'))
        self.assertTrue(by['2']['匹配_发现'].startswith('部分一致。数量不一致：'))
        self.assertNotIn('匹配_得分', by['1'])
        self.assertNotIn('细项_得分', by['1'])

    def test_mismatch_reason_must_name_the_difference_only(self):
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' not in prompt:
                return {}, {}
            ids, payload = self.row_ids(prompt)
            out = [{'row_id': ids['1'], 'band': '一致', 'finding': '一致。同为主变压器，型号相符，单位台、数量1吻合。'}] if '1' in ids else []
            if '2' in ids:
                retried = any('上次评审未通过校验' in p for p in payload if str(p['source']['seq']) == '2')
                out.append({'row_id': ids['2'], 'band': '部分一致', 'finding': (
                    '部分一致。数量不一致：造价300m，清册200m，相对差33.33%，超过1%。' if retried
                    else '部分一致。同为电力电缆，型号相符，但数量相对差33.33%。')})
            return {'findings': out}, {}
        by = self.review(ask)
        self.assertEqual(by['2']['匹配_发现'], '部分一致。数量不一致：造价300m，清册200m，相对差33.33%，超过1%。')

    def test_difference_reason_checks(self):
        issue = PipelineStore().instantiate(PipelineContext(ask=lambda *a, **k: ({}, {}), knowledge=self.kg)).row_review._difference_issue
        self.assertIn('差异类别开头', issue('部分一致', '数量对不上，相对差5%', {}))
        self.assertIn('不要写一致的地方', issue('部分一致', '型号规格不一致：造价BV-4，清册BVR-4；数量一致', {}))
        self.assertIn('仅数量对不上', issue('不一致', '数量不一致：造价3台，清册5台', {}))
        self.assertIn('程序核实两侧数量相等', issue('部分一致', '数量不一致：造价3台，清册3台', {'_qty_verified': True, '_qty_state': 'same'}))
        self.assertEqual(issue('部分一致', '型号规格不一致：造价BV-4，清册BVR-4；数量不一致：造价3台，清册5台', {}), '')
        self.assertEqual(issue('不一致', '名称不一致：造价为膨胀螺栓，清册为阻火模块', {}), '')

    def test_contradicting_verdict_is_retried_with_feedback(self):
        prompts = []
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' not in prompt:
                return {}, {}
            prompts.append(prompt)
            ids, payload = self.row_ids(prompt)
            out = [{'row_id': ids['1'], 'band': '一致', 'finding': '一致。同为主变压器，型号相符，单位台、数量1吻合。'}] if '1' in ids else []
            if '2' in ids:
                retried = any('上次评审未通过校验' in p for p in payload if str(p['source']['seq']) == '2')
                out.append({'row_id': ids['2'], 'band': '部分一致' if retried else '一致',
                            'finding': ('部分一致。数量不一致：造价300m，清册200m，相对差33.33%，超过1%。' if retried
                                        else '一致。同为电力电缆，数量吻合。')})
            return {'findings': out}, {}
        by = self.review(ask)
        self.assertTrue(by['2']['匹配_发现'].startswith('部分一致。'))
        self.assertTrue(any('超过1%允许误差' in p for p in prompts[1:]))

    def test_verdict_that_never_passes_is_marked_unreviewed(self):
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' not in prompt:
                return {}, {}
            ids, _ = self.row_ids(prompt)
            out = [{'row_id': ids['1'], 'band': '一致', 'finding': '一致。同为主变压器，型号相符，单位台、数量1吻合。'}] if '1' in ids else []
            if '2' in ids:
                out.append({'row_id': ids['2'], 'band': '基本一致', 'finding': '基本一致。同为电力电缆，相对差0.5%，数量吻合。'})
            return {'findings': out}, {}
        by = self.review(ask)
        finding = by['2']['匹配_发现']
        self.assertTrue(finding.startswith('评审未完成。模型评审3次未通过校验'), finding)
        self.assertNotIn('基本一致。', finding)
        self.assertIn('需人工判定', by['2']['匹配_置信'])


class WebSearchInPipelineTests(unittest.TestCase):
    """匹配与评审中的联网检索：换算参数从检索资料取值并写明出处；评审为查得参数的换算附联网资料。"""

    WEB = [{'title': '焊接钢管理论重量表', 'url': 'https://example.com/pipe', 'site_name': '示例钢管厂',
            'authority': 3, 'rerank_score': 2.0, 'summary': 'DN25 镀锌管 2.554kg/m'}]

    def skills(self, ask, search):
        return PipelineStore().instantiate(PipelineContext(ask=ask, search=search))

    def test_parameter_comes_from_web_sources_with_citation(self):
        prompts = []
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            prompts.append(prompt)
            return {'kg_per_m': 2.554, 'basis': 'DN25镀锌管2.554kg/m', 'source': 'W1'}, {}
        live = self.skills(ask, lambda query, count: self.WEB)
        live.quantity_rules.ctx.set_api_key('key')
        kg, basis = live.quantity_rules._resolve_linear_mass_via_llm('异型镀锌钢管 DN25')
        self.assertEqual(kg, 2.554)
        self.assertEqual(basis, '联网查得（示例钢管厂）：DN25镀锌管2.554kg/m')
        self.assertIn('只能依据下面联网检索到的资料取值', prompts[0])
        self.assertTrue(live.quantity_rules._is_estimated(basis))

    def test_sheet_metal_trough_weight_from_spec(self):
        q = self.skills(lambda *a, **k: ({}, {}), lambda query, count: []).quantity_rules
        kg, basis, steps = q._trough_kg_per_m('不锈钢电缆槽盒 200×200，d=2mm')
        self.assertAlmostEqual(kg, 12.688)          # (200+2×200+盖板200)mm×2mm×7930
        self.assertIn('展开宽度=底宽200+2×侧高200+盖板200=800mm', steps)
        self.assertAlmostEqual(q._trough_kg_per_m('热镀锌钢板电缆桥架 300×100 厚1.5mm')[0], 5.8875)   # 桥架无盖板
        self.assertEqual(q._trough_kg_per_m('透气型耐火电缆槽盒 400×150 δ=2')[0], 0.0)           # 材质未写明不按钢算
        self.assertEqual(q._trough_kg_per_m('热镀锌扁钢 -60×6')[0], 0.0)

    def test_parameter_without_cited_source_is_rejected_and_no_web_is_marked(self):
        answer = {'kg_per_m': 3.0, 'basis': '估计', 'source': 'W9'}
        live = self.skills(lambda *a, **k: (answer, {}), lambda query, count: self.WEB)
        live.quantity_rules.ctx.set_api_key('key')
        self.assertEqual(live.quantity_rules._resolve_linear_mass_via_llm('某型钢 A'), (0.0, ''))
        offline = self.skills(lambda *a, **k: ({'kg_per_m': 3.0, 'basis': '估计'}, {}), lambda query, count: [])
        offline.quantity_rules.ctx.set_api_key('key')
        self.assertEqual(offline.quantity_rules._resolve_linear_mass_via_llm('某型钢 B'), (3.0, '查得（未联网核实）：估计'))

    def test_review_attaches_web_material_and_cites_it(self):
        sources = [src(1, '镀锌钢管　φ25', '2.554', 't')]
        targets = [tgt(1, '热镀锌钢管', '1000', '米', 'φ25')]
        queries, payloads = [], []
        def search(query, count):
            queries.append(query)
            return self.WEB
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' in prompt:
                import json
                payload = json.loads(prompt.split('待评审记录：', 1)[1])
                payloads.extend(payload)
                return {'findings': [{'row_id': p['row_id'], 'band': '一致',
                                      'finding': '一致。同为φ25热镀锌钢管，厂家表DN25镀锌管2.554kg/m[W1]，1000m折合2.554t，与造价吻合。'}
                                     for p in payload]}, {}
            return {}, {}
        live = PipelineStore().instantiate(PipelineContext(ask=ask, search=search, knowledge=DomainKnowledge()))
        rows = live.check_units.build_rows(sources, targets, {'1': '1'}, {}, 'test', '')
        reviewed = live.match_flow.review(rows, 'key', 'test', sources, targets)
        self.assertTrue(any('理论重量 kg/m' in q for q in queries), queries)
        self.assertEqual(payloads[0]['联网资料'][0]['编号'], 'W1')
        self.assertNotIn('_url', payloads[0]['联网资料'][0])
        finding = reviewed[0]['匹配_发现']
        self.assertTrue(finding.startswith('一致。'), finding)
        self.assertIn('联网依据：[W1]示例钢管厂 https://example.com/pipe', finding)


class WebSourceAuthorityTests(unittest.TestCase):
    """只有标准、行业或生产厂家资料给出的参数才能改变结论；论坛问答、电商只作旁证，由程序判定和校验。"""

    FORUM = [{'title': 'DN25镀锌管每米多重', 'url': 'https://www.fwxgx.com/questions/582666', 'site_name': '服务新干线',
              'authority': 2, 'rerank_score': 2.0, 'summary': 'DN25 镀锌管 2.554kg/m'}]
    STANDARD = [{'title': 'GB/T 3091-2015 低压流体输送用焊接钢管', 'url': 'https://example.com/std', 'site_name': '标准馆',
                 'authority': 2, 'rerank_score': 2.0, 'summary': 'DN25 镀锌管理论重量 2.554kg/m'}]

    def test_grades(self):
        grade = PipelineStore().instantiate(PipelineContext()).quantity_rules._web_source_grade
        self.assertEqual(grade(self.FORUM[0]), (False, '论坛问答/百科/电商（旁证）'))
        self.assertEqual(grade(self.STANDARD[0]), (True, '标准或行业资料'))
        self.assertEqual(grade({'title': '产品参数', 'url': 'https://www.alibaba.com/p', 'site_name': 'Alibaba.com'})[0], False)
        self.assertEqual(grade({'title': '钢管规格表', 'url': 'http://tpcochina.cn/show.asp?id=414', 'site_name': '天津管线钢管集团股份有限公司'}),
                         (True, '生产厂家资料'))
        self.assertEqual(grade({'title': '环评报告', 'url': 'http://sthj.gxgg.gov.cn/a.pdf', 'site_name': ''}), (True, '标准或行业资料'))
        self.assertEqual(grade({'title': '资料', 'url': 'https://x.com/a', 'authority': 3}), (True, '高权威网页'))
        self.assertEqual(grade({'title': '资料', 'url': 'https://x.com/a', 'authority': 1}), (False, '一般网页（旁证）'))
        self.assertFalse(grade({'title': '镀锌无缝管', 'url': 'https://www.zjtcn.com/zhishi/duxinwufengguan', 'authority': 3})[0])

    def test_reason_stating_another_conclusion_is_rejected(self):
        row, _ = self.review(self.STANDARD, '部分一致', '部分一致。同为φ25热镀锌钢管，标准给出2.554kg/m[W1]，按资料判数量吻合，结论为基本一致。')
        self.assertTrue(row['匹配_发现'].startswith('评审未完成。'), row['匹配_发现'])
        self.assertIn('原因中写明结论为“基本一致”', row['匹配_发现'])

    def review(self, web, band, finding):
        sources = [src(1, '镀锌钢管　φ25', '2.554', 't')]
        targets = [tgt(1, '热镀锌钢管', '1000', '米', 'φ25')]
        payloads = []
        def ask(prompt, api_key, max_tokens=3000, retries=3):
            if '待评审记录' not in prompt:
                return {}, {}
            import json
            payload = json.loads(prompt.split('待评审记录：', 1)[1])
            payloads.extend(payload)
            return {'findings': [{'row_id': p['row_id'], 'band': band, 'finding': finding} for p in payload]}, {}
        live = PipelineStore().instantiate(PipelineContext(ask=ask, search=lambda q, c: web, knowledge=DomainKnowledge()))
        rows = live.check_units.build_rows(sources, targets, {'1': '1'}, {}, 'test', '')
        return live.match_flow.review(rows, 'key', 'test', sources, targets)[0], payloads

    def test_forum_source_cannot_overturn_program_mismatch(self):
        row, payloads = self.review(self.FORUM, '基本一致', '基本一致。同为φ25热镀锌钢管，资料DN25镀锌管2.554kg/m[W1]，1000m折合2.554t，与造价吻合。')
        self.assertFalse(payloads[0]['联网资料'][0]['可作定档依据'])
        self.assertTrue(row['匹配_发现'].startswith('评审未完成。'), row['匹配_发现'])
        self.assertIn('均为旁证', row['匹配_发现'])
        self.assertTrue(any('均为旁证' in p.get('上次评审未通过校验', '') for p in payloads[1:]))

    def test_forum_source_may_be_cited_as_corroboration(self):
        row, _ = self.review(self.FORUM, '部分一致', '部分一致。数量换算不一致：按程序参数相对差2.00%；旁证W1给出2.554kg/m，建议人工按标准或厂家资料核实。')
        self.assertTrue(row['匹配_发现'].startswith('部分一致。'), row['匹配_发现'])
        self.assertIn('[W1]服务新干线（旁证）', row['匹配_发现'])

    def test_standard_source_may_change_conclusion(self):
        row, _ = self.review(self.STANDARD, '基本一致', '基本一致。同为φ25热镀锌钢管，标准给出DN25镀锌管2.554kg/m[W1]，1000m折合2.554t，与造价吻合。')
        self.assertTrue(row['匹配_发现'].startswith('基本一致。'), row['匹配_发现'])


class PlatformPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = app.DATA, app.PIPELINE, copy.deepcopy(app.STATE)
        app.DATA, app.PIPELINE = Path(self.temp.name), PipelineStore(self.temp.name)
        app.STATE.update(stage=3, pairs={'1': '1'}, covers={}, rows=[{'id': 1}], messages=[], history=[], running=False,
                         dataset={'sources': [src(1, '主时钟', '1', '台')], 'targets': [tgt(1, '主时钟', '1', '台')]}, scope='')

    def tearDown(self):
        app.DATA, app.PIPELINE = self.old[0], self.old[1]
        app.STATE.clear(); app.STATE.update(self.old[2])
        self.temp.cleanup()

    def test_review_skill_change_resets_to_pairing_checkpoint(self):
        app.pipeline_changed('row_review')
        self.assertEqual(app.STATE['stage'], 2)
        self.assertEqual(app.STATE['pairs'], {'1': '1'})
        self.assertEqual(app.STATE['rows'][0]['target_id'], '1')

    def test_pairing_skill_change_clears_pairs(self):
        app.pipeline_changed('candidate_pairing')
        self.assertEqual((app.STATE['stage'], app.STATE['pairs'], app.STATE['covers']), (1, {}, {}))

    def test_model_draft_applies_unique_edits_only(self):
        code = app.PIPELINE.code('result_export')
        with patch.object(app, 'llm', return_value={'summary': '加列', 'edits': [{'old': '"成套项_复核"]', 'new': '"成套项_复核", "备注"]'}]}):
            draft = app.draft_pipeline_skill('result_export', '导出增加备注列', code)
        self.assertIn('"备注"', draft['code'])
        self.assertEqual(app.PIPELINE.code('result_export'), code)
        with patch.object(app, 'llm', return_value={'edits': [{'old': '不存在的代码', 'new': 'x'}]}):
            with self.assertRaisesRegex(ValueError, '不唯一或不存在'):
                app.draft_pipeline_skill('result_export', '随便改', code)


if __name__ == '__main__':
    unittest.main()
