"""Local rule-based table selection, deterministic extraction, and a complete source audit.

清洗全程在本机按规则完成，不调用任何模型。

选表、列映射与逐行提取的业务规则在流程技能“清洗·选表与列映射”“清洗·逐行提取”中，
可在“技能开发与管理”中查看和修改；本模块只负责遍历工程文件夹、读取工作簿，并保留原有函数名。
"""
import hashlib
from pathlib import Path
from cleaning import sheet_tables
from domain_kg import DomainKnowledge
from pipeline_skills import PipelineContext, PipelineStore

_KNOWLEDGE = None
STORE = PipelineStore()   # 平台启动时调用 configure() 换成数据目录中的技能版本
_CACHE = {}


def configure(store):
    global STORE
    STORE = store
    _CACHE.clear()


def knowledge():
    global _KNOWLEDGE
    if _KNOWLEDGE is None:
        _KNOWLEDGE = DomainKnowledge()
    return _KNOWLEDGE


def _skills():
    """当前版本的清洗技能；代码未变时复用已加载的实例。"""
    codes = STORE.codes()
    key = hashlib.sha256(''.join(codes[k] for k in ('table_selection', 'row_extraction', 'text_rules')).encode()).hexdigest()
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = STORE.instantiate(PipelineContext(knowledge=knowledge))
    return _CACHE[key]


def preview(rows, limit=16):
    return _skills().table_selection.preview(rows, limit)


def heading_level(label):
    return _skills().row_extraction.heading_level(label)


def extract_table(table, mapping, side, result, audit, skills):
    return _skills().row_extraction.extract_table(table, mapping, side, result, audit, skills)


def clean_folder_local(folder, skills, log=lambda text: None):
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('工程文件夹不存在')
    tables, files, warnings = [], [], []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name.startswith(('~', '.')):
            continue
        relative = str(path.relative_to(root))
        if not skills.call('data_cleaning', 'include_file', relative):
            warnings.append('按清洗技能排除：' + relative)
            continue
        if path.suffix.lower() not in ('.xlsx', '.xls', '.pdf', '.dwg', '.dxf'):
            continue
        files.append(dict(name=path.name, path=relative, size=path.stat().st_size, type=path.suffix.lower()))
        if path.suffix.lower() not in ('.xlsx', '.xls'):
            continue
        log('读取工作簿：' + relative)
        try:
            for title, rows, merges in sheet_tables(path):
                tables.append(dict(id=len(tables), file=relative, sheet=title, rows=rows, merges=merges))
        except Exception as exc:
            raise ValueError(f'{relative} 无法读取（{type(exc).__name__}），原工程未替换') from exc
    result = _skills().table_selection.clean_tables(root.name, str(root), tables, files, warnings, skills, log)
    result['cleaning_rules_hash'] = hashlib.sha256(result['cleaning_rules'].encode()).hexdigest()
    return result
