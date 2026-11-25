"""测试一律使用临时数据目录：server 导入时按 POWER_COST_DATA_DIR 定 DATA，避免测试改写 runtime 中正在使用的对话和结果。"""
import os
import tempfile

os.environ['POWER_COST_DATA_DIR'] = tempfile.mkdtemp(prefix='power-cost-test-')
