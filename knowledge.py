"""Local provenance-first knowledge library with CPU-only full-text retrieval."""
import collections
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager


CUSTOM_RULES='自定义规则'
STANDARD='规范文件'
KINDS=(CUSTOM_RULES,STANDARD)
LEGACY_SOURCE='用户研究方案：14_技术_3-5章_咨询研究计划_方案_措施（研究子课题2）.docx'
BUILTIN_RULES=Path(__file__).resolve().parent/'domain_knowledge'/'kb_custom_rules.json'
# 早期库没有这些列；打开时补齐，旧条目默认为自定义规则
EXTRA_COLUMNS=(('kind',CUSTOM_RULES),('standard',''),('clause',''),('edition',''))


def tokens(text):
    text=str(text).lower()
    out=re.findall(r'[a-z0-9]+(?:[./-][a-z0-9]+)*',text)
    for segment in re.findall(r'[\u4e00-\u9fff]+',text):
        out.extend(segment[i:i+2] for i in range(max(1,len(segment)-1)))
    return out


class KnowledgeStore:
    def __init__(self, folder):
        self.path=Path(folder)/'knowledge.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY,title TEXT,content TEXT,source TEXT,location TEXT,category TEXT,approved INTEGER,active INTEGER,version INTEGER,updated TEXT)')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(id UNINDEXED,tokens)')
            db.execute('CREATE TABLE IF NOT EXISTS revisions (id TEXT, version INTEGER, payload TEXT, updated TEXT)')
            columns={r['name'] for r in db.execute('PRAGMA table_info(entries)')}
            for name,default in EXTRA_COLUMNS:
                if name not in columns: db.execute(f"ALTER TABLE entries ADD COLUMN {name} TEXT DEFAULT '{default}'")

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key):
        with self.connect() as db:
            row=db.execute('SELECT * FROM entries WHERE id=?',(key,)).fetchone()
        return dict(row) if row else None

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM entries ORDER BY updated DESC,title')]

    def save(self, item):
        for key in ('title','content','source'):
            if not str(item.get(key,'')).strip(): raise ValueError('知识标题、正文和来源必填')
        if len(item['content'])>24000: raise ValueError('单条知识超过24000字，请拆分')
        kind=str(item.get('kind') or CUSTOM_RULES)
        if kind not in KINDS: raise ValueError('知识类型须为自定义规则或规范文件')
        if kind==STANDARD and not (str(item.get('standard','')).strip() and str(item.get('clause','')).strip()):
            raise ValueError('规范文件须填写标准号和条款号')
        key=str(item.get('id') or uuid.uuid4().hex[:12])
        with self.connect() as db:
            old=db.execute('SELECT * FROM entries WHERE id=?',(key,)).fetchone()
            if old: db.execute('INSERT INTO revisions VALUES (?,?,?,?)',(key,old['version'],json.dumps(dict(old),ensure_ascii=False),time.strftime('%Y-%m-%d %H:%M:%S')))
            value={'id':key,'title':str(item['title']),'content':str(item['content']),'source':str(item['source']),
                   'location':str(item.get('location','')),'category':str(item.get('category','电力物资')),
                   'approved':int(bool(item.get('approved',False))),'active':int(bool(item.get('active',True))),
                   'version':(old['version']+1) if old else 1,'updated':time.strftime('%Y-%m-%d %H:%M:%S'),'kind':kind,
                   **{k:str(item.get(k,'')).strip() if kind==STANDARD else '' for k in ('standard','clause','edition')}}
            db.execute(f'INSERT OR REPLACE INTO entries ({",".join(value)}) VALUES ({",".join(":"+k for k in value)})',value)
            db.execute('DELETE FROM search_index WHERE id=?',(key,))
            db.execute('INSERT INTO search_index VALUES (?,?)',(key,' '.join(tokens(' '.join(value[k] for k in ('standard','clause','title','category','content'))))))
        return value

    def search(self, query, limit=8, kind=None):
        words=[w for w,_ in collections.Counter(tokens(query)).most_common(100)]
        if not words: return []
        expression=' OR '.join('"'+w.replace('"','')+'"' for w in words)
        with self.connect() as db:
            rows=db.execute('SELECT e.*,bm25(search_index) AS rank FROM search_index JOIN entries e ON e.id=search_index.id WHERE search_index MATCH ? AND e.active=1 AND (? IS NULL OR e.kind=?) ORDER BY rank LIMIT ?',(expression,kind,kind,limit)).fetchall()
        return [dict(r) for r in rows]

    def history(self, key):
        with self.connect() as db:
            return [{'version':r['version'],'updated':r['updated'],'entry':json.loads(r['payload'])} for r in db.execute('SELECT * FROM revisions WHERE id=? ORDER BY version DESC',(key,))]

    def seed(self):
        rules=[
          ('物资主体与规格优先','匹配必须先判断物资主体、电压等级、型号、芯数、截面、铠装、材质及新旧工程对象。附件不得冒充主材；数量相同不能证明物资同一。','物资同一性'),
          ('数量核对四档口径','一致：主体同一且单位相同或明确可精确换算，数量差异不超过1%。基本一致：主体一致，单位或包装写法有差异但换算后一致，或属于一对多包含关系。部分一致：主体一致但数量差异超过1%或无法可靠换算。不一致：主体不同或单侧无对应。','数量核对'),
          ('铜母排长度重量换算','方案示例：TMY-50×5，截面积250mm²，按铜密度8900kg/m³计算，250×8900÷1000000=2.225kg/m。每次换算输出完整算式。实际截面与材质须核实；经验估算参数只作说明，不参与配对打分。','计量换算'),
          ('包装及成套关系','网线305m/箱时，1箱=305m；蓄电池104只=1组只是方案举例，必须有具体成套组成依据。成套物资按每串件数×串数进行拆项补配，不可跨工程套用数量。','成套包装'),
          ('价格口径与出处','信息价必须保留文件、发布机构、期次、区域、税口径、交货口径、单位、页码及条目。口径无法对齐时禁止直接比较。相近规格与推算价格只作参考，不视为完全对应正式价格。','信息价'),
          ('价格偏差默认分级','偏差率=(造价单价－信息价)÷信息价×100%。绝对偏差≤2%一致，2%～5%基本一致，5%～10%偏差，超过10%显著偏差。阈值可经业务确认调整。','价格核对'),
          ('架空线路图纸交叉校核','各耐张段长度之和等于线路总长；地形比例合计100%；各塔型基数之和等于总基数；单基塔重×基数核对总塔材量；基础数量与杆塔基数及塔腿数核对；接地装置套数与杆塔基数核对。冲突字段须人工确认并溯源到图纸。','图纸校核'),
          ('价格库审核发布规则','新期次价格应完成映射、环比和同比变动分析、异常预警、人工审核后再发布版本。推算价必须显式标注。版本发布后服务价格核对，并支持回退和出处追踪。','价格库')]
        # 早期版本把内置规则的出处记为研究方案文件及其章节号；统一改为“自定义规则”，原地改写，不留旧名修订
        with self.connect() as db:
            db.execute('UPDATE entries SET source=?,location=? WHERE source=?',(CUSTOM_RULES,'',LEGACY_SOURCE))
        count=0
        existing={x['id'] for x in self.list()}
        for title,content,category in rules:
            key='research-'+hashlib.sha256(title.encode()).hexdigest()[:8]
            if key in existing: continue
            self.save(dict(id=key,title=title,content=content,source=CUSTOM_RULES,location='',category=category,approved=False,active=True))
            count+=1
        # 物资识别与配对的电力领域条目（domain_knowledge/kb_custom_rules.json），全部待专家审核；已有条目不覆盖，保留用户编辑
        for item in builtin_rules()['entries']:
            key=builtin_id(item['title'])
            if key in existing: continue
            self.save(dict(id=key,title=item['title'],content=item['content'],source=CUSTOM_RULES,location=builtin_rules()['location'],
                           category=item['category'],approved=False,active=True))
            count+=1
        return count


def builtin_rules():
    return json.loads(BUILTIN_RULES.read_text(encoding='utf-8'))


def builtin_id(title):
    return 'kb-'+hashlib.sha256(title.encode()).hexdigest()[:10]


def citation(e):
    """知识出处：规范文件写“标准号《名称》第x条（版本）”，自定义规则写来源与位置。"""
    if e.get('kind')==STANDARD:
        clause=str(e.get('clause',''))
        label=clause if clause.startswith(('表','附录','前言','引言')) else f'条文说明第{clause[5:]}条' if clause.startswith('条文说明 ') else f'第{clause}条'
        return f'{e.get("standard","")}《{e["source"]}》{label}'+(f'（{e["edition"]}版）' if e.get('edition') else '')
    return e['source']+(f' / {e["location"]}' if e.get('location') else '')


def knowledge_context(entries):
    return '\n'.join(f'[KB:{e["id"]}@v{e["version"]}] {e["title"]}（{e.get("kind") or CUSTOM_RULES}·'+('已审核' if e['approved'] else '待专家审核')+f'）\n{e["content"][:2200]}\n出处：{citation(e)}' for e in entries)
