"""Versioned user-authored pure-data skills. This is not a general OS sandbox."""
import copy
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from skill_runtime import validate_source, BUILTINS, invoke
import protected

FORMAT = 'power-cost-skill/v1'

def validate(item):
    if not isinstance(item, dict): raise ValueError('技能包必须为对象')
    key=str(item.get('id',''))
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,47}',key): raise ValueError('技能ID须为2～48位英文小写字母、数字、下划线或短横线')
    name=str(item.get('name','')).strip()
    if not name or len(name)>80: raise ValueError('请输入80字以内的技能名称')
    code=str(item.get('code',''))
    validate_source(key,code,{'run':1})
    tests=item.get('tests',[])
    if not isinstance(tests,list) or len(tests)>10 or any(not isinstance(t,dict) or 'input' not in t or 'expected' not in t for t in tests): raise ValueError('测试须为含 input 和 expected 的数组，最多10项')
    result=dict(id=key,name=name,description=str(item.get('description',''))[:2000],instructions=str(item.get('instructions',''))[:12000],code=code,tests=tests)
    if len(json.dumps(result,ensure_ascii=False))>60000: raise ValueError('技能包超过60KB')
    return result

def run_code(code,data):
    raw=json.dumps({'code':code,'input':data},ensure_ascii=False)
    if len(raw)>200000: raise ValueError('试运行输入超过200KB')
    command=[str(Path(sys.executable).parent/'PowerCostSkillWorker.exe')] if getattr(sys,'frozen',False) else [str(Path(sys.executable).with_name('python.exe')) if sys.platform=='win32' and Path(sys.executable).name.lower()=='pythonw.exe' else sys.executable,str(Path(__file__).resolve()),'--skill-worker']
    try:
        proc=subprocess.run(command,input=raw,encoding='utf-8',capture_output=True,timeout=5,creationflags=0x08000000 if sys.platform=='win32' else 0)
    except subprocess.TimeoutExpired: raise ValueError('技能执行超时（5秒），请简化循环和输入')
    if proc.returncode or not proc.stdout: raise ValueError('技能子进程未完成，检查代码或资源用量')
    result=json.loads(proc.stdout)
    if 'error' in result: raise ValueError(result['error'])
    return result['result']

def worker_main():
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    try:
        payload=json.load(sys.stdin)
        ns={'__builtins__':BUILTINS}
        exec(validate_source('custom',payload['code'],{'run':1}),ns)
        result=invoke(ns,'run',payload['input'])
        raw=json.dumps({'result':result},ensure_ascii=False,allow_nan=False)
        if len(raw)>200000: raise ValueError('输出超过200KB')
        print(raw)
    except Exception as exc: print(json.dumps({'error':str(exc)[:400]},ensure_ascii=False))

class CustomSkills:
    def __init__(self,root):
        self.path=Path(root)/'user_skills';self.path.mkdir(exist_ok=True,parents=True)
        self.lock=threading.RLock()
    def get(self,key):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,47}',str(key)): raise ValueError('无效技能ID')
        file=self.path/(key+'.json')
        if not file.exists(): raise ValueError('技能不存在')
        return json.loads(file.read_text(encoding='utf-8'))
    def list(self):
        with self.lock:
            result=[]
            for f in sorted(self.path.glob('*.json')):
                e=json.loads(f.read_text(encoding='utf-8'))
                result.append({k:e[k] for k in ('id','name','description','version','enabled','tested','updated')})
            return result
    def write(self,item):
        f=self.path/(item['id']+'.tmp');f.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8');protected.replace_file(f,self.path/(item['id']+'.json'))
    def save(self,item,base_version=None):
        cleaned=validate(item)
        with self.lock:
            file=self.path/(cleaned['id']+'.json');old=self.get(cleaned['id']) if file.exists() else None
            if old and base_version!=old['version']: raise ValueError('技能已更新或ID重复，请重新打开后编辑')
            history=old.get('history',[]) if old else []
            if old: history=history+[{k:v for k,v in old.items() if k!='history'}]
            cleaned.update(version=old['version']+1 if old else 1,enabled=False,tested=False,updated=time.strftime('%Y-%m-%d %H:%M:%S'),history=history)
            self.write(cleaned);return cleaned
    def test(self,key):
        item=self.get(key)
        if not item['tests']: raise ValueError('启用前至少添加1个带预期输出的测试样例')
        results=[]
        for i,t in enumerate(item['tests']):
            actual=run_code(item['code'],t['input']);results.append({'case':i+1,'passed':actual==t['expected'],'actual':actual,'expected':t['expected']})
        with self.lock:
            current=self.get(key)
            if current['version']!=item['version']: raise ValueError('测试期间代码已修改，请重新测试')
            current['tested']=all(r['passed'] for r in results)
            if not current['tested']:current['enabled']=False
            current['last_test']=results;self.write(current)
        return results
    def toggle(self,key,enabled):
        with self.lock:
            item=self.get(key)
            if enabled and not item['tested']:raise ValueError('请先运行并通过该版本的样例测试')
            item['enabled']=bool(enabled);self.write(item);return item
    def run(self,key,data):
        item=self.get(key)
        if not item['enabled']:raise ValueError('技能尚未启用，请先通过测试并启用')
        return {'skill_id':key,'version':item['version'],'result':run_code(item['code'],data)}
    def export(self,key):
        return {'format':FORMAT,'skill':validate(self.get(key))}
    def import_pack(self,pack):
        if not isinstance(pack,dict) or pack.get('format')!=FORMAT:raise ValueError('请选择本平台导出的 skill.json 技能包')
        return self.save(pack.get('skill',{}))
    def rollback(self,key,version):
        item=self.get(key);old=next((x for x in item['history'] if x['version']==version),None)
        if not old:raise ValueError('未找到历史版本')
        return self.save(old,item['version'])

if __name__=='__main__':worker_main()
