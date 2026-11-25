"""最终评审的结论与依据必须来自同一份有效模型答复。全部离线。"""
import copy
import json
import unittest

from pipeline_skills import PipelineContext, PipelineStore


def row():
    return {'源_序号': '1', '匹配_序号': '2', '源_名称': '发电车接入箱',
            '匹配_名称': '发电车接入箱', '源_数量': '1', '匹配_数量': '1',
            '源_单位': '台', '匹配_单位': '只', '匹配_得分(%)': 19.8,
            '_kg_band': '不一致', '_kg_finding': '名称关键词不同',
            '匹配_发现': '不一致。旧结论。'}


GOOD = {'row_id': 0, 'band': '基本一致',
        'finding': '基本一致。同为发电车接入箱，仅单位台与只写法不同，数量1一致。'}


class VerdictTests(unittest.TestCase):
    def skills(self, replies):
        self.calls = []
        def ask(prompt, api_key, **kwargs):
            self.calls.append(prompt)
            reply = replies[min(len(self.calls) - 1, len(replies) - 1)]
            if isinstance(reply, Exception):
                raise reply
            return copy.deepcopy(reply), {}
        return PipelineStore().instantiate(PipelineContext(ask=ask))

    def test_model_verdict_survives_knowledge_and_publication(self):
        skills = self.skills([{'findings': [GOOD]}])
        item = row()
        skills.row_review._format_final_match_findings([item], 'test')
        skills.group_review._apply_group_level_scores([item])
        self.assertEqual(item['匹配_发现'], GOOD['finding'])
        self.assertEqual(item['匹配_原因'], GOOD['finding'])
        self.assertNotIn('匹配_得分(%)', item)
        self.assertNotIn('细项_得分(%)', item)
        payload = json.loads(self.calls[0].split('待复核记录：')[1])[0]
        self.assertEqual(payload['知识提示'], '名称关键词不同')
        self.assertNotIn('score', payload)
        self.assertNotIn('系统初判', payload)

    def test_conflicting_band_retries_whole_verdict(self):
        skills = self.skills([{'findings': [{**GOOD, 'band': '不一致'}]}, {'findings': [GOOD]}])
        item = row()
        skills.row_review._format_final_match_findings([item], 'test')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(item['匹配_发现'], GOOD['finding'])

    def test_screenshot_contradiction_never_published(self):
        bad = {**GOOD, 'band': '不一致', 'finding': GOOD['finding'].replace('基本一致。', '不一致。')}
        skills = self.skills([{'findings': [bad]}])
        item = row()
        skills.row_review._format_final_match_findings([item], 'test')
        skills.group_review._apply_group_level_scores([item])
        self.assertTrue(item['匹配_发现'].startswith('待复核。'))
        self.assertNotIn('_llm_band', item)

    def test_partial_response_retries_only_missing_rows(self):
        skills = self.skills([{'findings': [GOOD]}, {'findings': [{**GOOD, 'row_id': 1}]}])
        items = [row(), row()]
        skills.row_review._format_final_match_findings(items, 'test')
        retry = json.loads(self.calls[1].split('待复核记录：')[1])
        self.assertEqual([r['row_id'] for r in retry], [1])
        self.assertTrue(all(r['匹配_发现'] == GOOD['finding'] for r in items))

    def test_failure_or_missing_key_clears_old_verdict(self):
        for key in ('', 'test'):
            skills = self.skills([RuntimeError('offline')])
            item = {**row(), '_llm_band': '一致'}
            skills.row_review._format_final_match_findings([item], key)
            self.assertTrue(item['匹配_发现'].startswith('待复核。'))
            self.assertNotIn('_llm_band', item)

    def test_duplicate_unknown_and_wrong_subject_are_not_accepted(self):
        cases = [[GOOD, GOOD], [{**GOOD, 'row_id': 99}],
                 [{**GOOD, 'finding': '基本一致。同为主变压器，仅名称写法不同，数量一致。'}]]
        for findings in cases:
            skills = self.skills([{'findings': findings}])
            item = row()
            skills.row_review._format_final_match_findings([item], 'test')
            self.assertTrue(item['匹配_发现'].startswith('待复核。'))

    def test_no_truncation_of_specs_or_findings(self):
        finding = GOOD['finding'] + '参数核对依据。' * 100
        skills = self.skills([{'findings': [{**GOOD, 'finding': finding}]}])
        item = {**row(), '匹配_型号': '完整技术规格' * 100}
        skills.row_review._format_final_match_findings([item], 'test')
        self.assertEqual(item['匹配_发现'], finding)
        self.assertIn(item['匹配_型号'], self.calls[0])


if __name__ == '__main__':
    unittest.main()
