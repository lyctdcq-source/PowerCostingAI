"""Editable constrained Python business functions, versioned and validated.
Not a general Python or operating-system sandbox.
"""
import ast
import copy
import difflib
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

import protected

CONTRACTS = {
 'excel_read': {'name':'Excel读取','functions':{'allow_format':1},'effect':'读取XLS/XLSX原始工作表，无需安装Excel'},
 'excel_view': {'name':'Excel展示','functions':{'page_size':0},'effect':'在平台内分页查看全部工作表、行列及公式'},
 'excel_edit': {'name':'Excel操作','functions':{'allow_operation':1},'effect':'修改单元格并另存副本，保留原文件'},
 'folder_discovery': {'name':'数据清洗·工程选表','functions':{'discovery_rules':0,'include_estimation_table':2,'include_inventory_table':2,'is_estimation_detail':1,'inventory_exclusion':2},'effect':'本地规则识别造价表与设计清册（不调用模型），回溯设计用量明细，并按人工基准核查'},
 'data_cleaning': {'name':'数据清洗','functions':{'include_file':1,'include_sheet':3,'transform_record':2},'effect':'重新读取当前工程并清洗，数量匹配和报告失效'},
 'material_matching': {'name':'物资数量匹配','functions':{'matching_guidance':0,'quantity_tolerance':0},'effect':'修改真实LLM配对专家提示或离线数量容差，重新执行匹配'},
 'price_review': {'name':'信息价核对','functions':{'classify_price':2},'effect':'使用新代码重算价格核对结果'},
 'drawing_review': {'name':'图纸字段校核','functions':{'validate_fields':1},'effect':'重新执行提资字段工程校核'},
 'price_database': {'name':'价格库异常预警','functions':{'is_anomaly':3},'effect':'使用新代码重算变动预警'}
}
BUILTINS = {name: __builtins__[name] if isinstance(__builtins__,dict) else getattr(__builtins__,name) for name in ('abs','all','any','bool','dict','enumerate','float','int','isinstance','len','list','max','min','range','round','set','sorted','str','sum','tuple','zip')}
METHODS = {'get','items','keys','values','copy','update','startswith','endswith','lower','upper','strip','replace','split','join','append','casefold'}
BANNED = (ast.Import,ast.ImportFrom,ast.ClassDef,ast.AsyncFunctionDef,ast.Await,ast.With,ast.AsyncWith,ast.While,ast.Global,ast.Nonlocal,ast.Delete,ast.Try,ast.Raise,ast.Lambda,ast.Yield,ast.YieldFrom,ast.Pow)


def validate_source(skill_id, code, functions=None):
    if (functions is None and skill_id not in CONTRACTS) or len(code)>18000:
        raise ValueError('技能ID无效或代码超过18KB')
    tree=ast.parse(code)
    expected=functions if functions is not None else CONTRACTS[skill_id]['functions']
    found={}
    for node in tree.body:
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            continue
        if not isinstance(node,ast.FunctionDef) or node.name not in expected:
            raise ValueError('仅允许定义契约中的业务函数')
        if node.name in found or node.decorator_list or node.args.defaults or node.args.kw_defaults or node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.posonlyargs or node.returns or any(a.annotation for a in node.args.args):
            raise ValueError('不支持重复函数、装饰器、注解或可选参数')
        found[node.name]=len(node.args.args)
    if found!=expected:
        raise ValueError('不得更改技能函数名称与参数数量')
    for node in ast.walk(tree):
        if isinstance(node,BANNED):
            raise ValueError('技能不能导入模块、访问系统或使用无限循环')
        if isinstance(node,ast.Name) and node.id.startswith('_'):
            raise ValueError('不允许访问内部对象')
        if isinstance(node,ast.Attribute) and node.attr not in METHODS:
            raise ValueError('不允许调用此对象属性：'+node.attr)
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name) and node.func.id not in BUILTINS:
                raise ValueError('仅允许纯业务内置函数，不允许递归或外部调用')
            if not isinstance(node.func,(ast.Name,ast.Attribute)):
                raise ValueError('不允许动态调用')
    return compile(tree,skill_id+'.py','exec')


def invoke(namespace, function, *args):
    count=0
    deadline=time.monotonic()+1
    def trace(frame,event,arg):
        nonlocal count
        count+=1
        if count>10000 or time.monotonic()>deadline:
            raise ValueError('技能执行超过预算，请简化业务逻辑')
        return trace
    previous=sys.gettrace()
    try:
        sys.settrace(trace)
        return namespace[function](*copy.deepcopy(args))
    finally:
        sys.settrace(previous)


def compile_skill(skill_id, code):
    namespace={'__builtins__':BUILTINS}
    exec(validate_source(skill_id,code),namespace)
    return namespace


def contract_tests(skill_id, code):
    ns=compile_skill(skill_id,code)
    def run(name,*args): return invoke(ns,name,*args)
    if skill_id=='excel_read':
        if not isinstance(run('allow_format','.xlsx'),bool): raise ValueError('格式规则须返回布尔值')
    elif skill_id=='excel_view':
        if type(run('page_size')) is not int or not 10<=run('page_size')<=200: raise ValueError('分页行数须为10到200')
    elif skill_id=='excel_edit':
        if not isinstance(run('allow_operation','set_cell'),bool): raise ValueError('操作规则须返回布尔值')
    elif skill_id=='folder_discovery':
        if not isinstance(run('discovery_rules'),str) or not run('discovery_rules').strip(): raise ValueError('选表规则不能为空')
        if not isinstance(run('include_estimation_table','可研.xlsx','安装甲供设备汇总表'),bool): raise ValueError('造价表范围规则须返回布尔值')
        if not isinstance(run('include_inventory_table','土建清册.xlsx','消防'),bool): raise ValueError('清册范围规则须返回布尔值')
        if not isinstance(run('is_estimation_detail','安装单位工程汇总表（表三甲）'),bool): raise ValueError('明细表规则须返回布尔值')
        if not isinstance(run('inventory_exclusion',{'设备材料名称':'导线','数量':'1','备注':''},'导线'),str): raise ValueError('行排除规则须返回原因文本')
    elif skill_id=='data_cleaning':
        for path in ('主工程/估算.xlsx','临时施工用电.xlsx'):
            if not isinstance(run('include_file',path),bool): raise ValueError('include_file必须返回布尔值')
        if not isinstance(run('include_sheet','可研.xlsx','设备汇总表',True),bool): raise ValueError('include_sheet必须返回布尔值')
        if not isinstance(run('transform_record',{'Estimation_Name':'铜母排','Estimation_Unit':'米','Estimation_Quantity':'1'},True),dict): raise ValueError('transform_record必须返回对象')
    elif skill_id=='material_matching':
        if not isinstance(run('matching_guidance'),str) or not 0<=run('quantity_tolerance')<=.5: raise ValueError('专家提示须为文本，离线容差应在0～0.5之间')
    elif skill_id=='price_review':
        if any(run('classify_price',x,10) not in ('一致','基本一致','偏差','显著偏差') for x in (0,2,5,10,11,-15)): raise ValueError('价格档位不符合约定')
    elif skill_id=='drawing_review':
        if not isinstance(run('validate_fields',[{'field':'杆塔总基数','value':'48'},{'field':'接地装置套数','value':'46'}]),list): raise ValueError('校核须返回冲突列表')
    elif skill_id=='price_database':
        if not isinstance(run('is_anomaly',120,100,10),bool): raise ValueError('预警须返回布尔值')
    return ['Python语法通过','纯业务函数边界通过','接口契约样例通过']


# 已发布过的内置模板摘要：数据目录中的技能与其中之一完全相同，说明用户没有改过，可随新模板升级
SUPERSEDED_TEMPLATES={
    'folder_discovery':{'5a6d7ac2d28a50d995b4647aed8b513f01d91e504b27c12c8f0dbc9639df455a'},
}


class SkillStore:
    def __init__(self,data_path,templates):
        self.folder=Path(data_path)/'skills'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.history=self.folder/'versions'
        self.history.mkdir(exist_ok=True)
        self.index_path=self.folder/'history.json'
        self.versions=json.loads(protected.read_text(self.index_path)) if self.index_path.exists() else []
        self.cache={}
        base_path=self.folder/'base.json'
        base=json.loads(protected.read_text(base_path)) if base_path.exists() else {}
        saved=dict(base) if base_path.exists() else None
        digest=lambda text: hashlib.sha256(text.encode()).hexdigest()
        for key in CONTRACTS:
            path=self.folder/(key+'.py')
            template=protected.source(Path(templates)/(key+'.py'))
            current=protected.read_text(path) if path.exists() else None
            # 未修改过的技能随内置模板升级；在平台中改过的保留用户版本
            if current is None or digest(current) in {base.get(key)}|SUPERSEDED_TEMPLATES.get(key,set()):
                if current!=template: protected.write_atomic(path,template)
                base[key]=digest(template)
            elif digest(current)==digest(template):
                base[key]=digest(template)
        if base!=saved: protected.write_atomic(base_path,json.dumps(base,ensure_ascii=False,indent=1))

    def code(self,key):
        if key not in CONTRACTS: raise ValueError('未知技能')
        return protected.read_text(self.folder/(key+'.py'))

    def call(self,key,function,*args):
        code=self.code(key)
        digest=hashlib.sha256(code.encode()).hexdigest()
        if (key,digest) not in self.cache: self.cache[(key,digest)]=compile_skill(key,code)
        return invoke(self.cache[(key,digest)],function,*args)

    def describe(self):
        return [{'id':key,**meta,'code':self.code(key),'path':str(self.folder/(key+'.py'))} for key,meta in CONTRACTS.items()]

    def apply(self,key,code,instruction,summary):
        tests=contract_tests(key,code)
        before=self.code(key)
        if before.strip()==code.strip(): raise ValueError('模型未产生代码变更')
        version_id=uuid.uuid4().hex[:12]
        record={'id':version_id,'skill_id':key,'time':time.strftime('%Y-%m-%d %H:%M:%S'),'instruction':instruction,'summary':summary,'tests':tests,'before':before,'after':code,'diff':'\n'.join(difflib.unified_diff(before.splitlines(),code.splitlines(),fromfile=key+'.py 原版本',tofile=key+'.py 新版本',lineterm=''))}
        protected.write_text(self.history/(version_id+'.json'),json.dumps(record,ensure_ascii=False,indent=2))
        protected.write_atomic(self.folder/(key+'.py'),code)
        self.versions.append(record)
        protected.write_atomic(self.index_path,json.dumps(self.versions,ensure_ascii=False,indent=2))
        self.cache.clear()
        return record

    def rollback(self,version_id):
        record=next(r for r in self.versions if r['id']==version_id)
        return self.apply(record['skill_id'],record['before'],'用户回退技能版本 '+version_id,'恢复该次修改之前的代码')
