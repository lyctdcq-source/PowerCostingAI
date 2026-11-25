"""Native Windows client using WebView2, with an embedded local Python service."""
import json
import sys
import threading
import time
import os
import urllib.error
import urllib.request
from pathlib import Path
if '--smoke-test' in sys.argv:
    sys.stdout = sys.stderr = open(sys.argv[sys.argv.index('--smoke-test')+1]+'.log', 'w', encoding='utf-8')
if '--pdf-preview' in sys.argv:
    # 土石方资料清洗的 PDF 页面渲染：在单独的进程里做，见 earthwork_preview.py
    from earthwork_preview import main as preview_main
    raise SystemExit(preview_main(sys.argv[sys.argv.index('--pdf-preview') + 1:]))
if '--skill-worker' in sys.argv:
    from custom_skills import worker_main
    worker_main()
    raise SystemExit(0)
import protected

# 冒烟测试可改用其他端口，避免与正在运行的平台冲突
PORT = int(os.environ.get('POWER_COST_PORT') or 8766)


def main():
    import server
    from desktop_features import DesktopBridge, style_menus
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator
    smoke = '--smoke-test' in sys.argv
    httpd = None
    try:
        httpd = server.LocalServer(('127.0.0.1',PORT),server.Handler)
    except OSError:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/ping',timeout=3) as response:
                running = json.load(response)
        except urllib.error.HTTPError:
            running = {'app_id': 'power-cost-agent-platform'}
            if running.get('app_id') != 'power-cost-agent-platform':
                raise RuntimeError(f'本地端口{PORT}被其他程序占用')
            if running.get('server_revision') != server.SERVER_REVISION:
                raise RuntimeError(f'端口{PORT}仍运行旧版平台服务。请在原启动终端按Ctrl+C停止旧服务，再重新启动desktop.py；仅关闭桌面窗口不会停止独立服务。')
    if httpd:
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
    webview.settings['ALLOW_DOWNLOADS'] = True
    bridge=DesktopBridge()
    window = webview.create_window('电力造价智能体平台', f'http://127.0.0.1:{PORT}',
        width=1440,height=960,min_size=(900,640),background_color='#FFFFFF',
        text_select=True,hidden=smoke,js_api=bridge,confirm_close=not smoke)
    bridge._window=window
    window.events.loaded += lambda: style_menus(window)
    def action(name):
        return lambda: window.evaluate_js('desktopAction('+json.dumps(name)+')')
    menus = [
        Menu('文件', [MenuAction('添加工程文件夹  Ctrl+O', action('folder')), MenuAction('导出审计包', action('export')), MenuSeparator(), MenuAction('退出登录', action('logout')), MenuAction('退出平台', window.destroy)]),
        Menu('编辑', [MenuAction('撤销  Ctrl+Z',action('undo')), MenuAction('重做  Ctrl+Y',action('redo')), MenuSeparator(), MenuAction('剪切  Ctrl+X',action('cut')), MenuAction('复制  Ctrl+C',action('copy')), MenuAction('粘贴  Ctrl+V',action('paste')), MenuAction('全选  Ctrl+A',action('selectAll')), MenuSeparator(), MenuAction('模型与连接',action('settings'))]),
        Menu('视图', [MenuAction('当前对话',action('conversation')), MenuAction('侧栏  Ctrl+B',action('sidebar')), MenuAction('结果面板  Ctrl+J',action('panel')), MenuSeparator(), MenuAction('物资智能评审',action('quantity')), MenuAction('图纸识别与提资',action('drawing')), MenuAction('物资价格数据库',action('database')), MenuAction('电力知识库',action('knowledge')), MenuAction('土石方数智化算量',action('earthwork')), MenuAction('技能开发与管理',action('skills')), MenuSeparator(), MenuAction('全屏切换',window.toggle_fullscreen)]),
        Menu('帮助', [MenuAction('使用帮助  F1',action('help')), MenuAction('关于平台',action('about'))]),
    ]
    if httpd:
        def choose_folder():
            selected=window.create_file_dialog(webview.FileDialog.FOLDER)
            return selected[0] if selected else ''
        def choose_file():
            selected=window.create_file_dialog(webview.FileDialog.OPEN,file_types=('Excel 工作簿 (*.xlsx;*.xlsm)','所有文件 (*.*)'))
            return selected[0] if selected else ''
        server.FOLDER_PICKER=choose_folder
        server.FILE_PICKER=choose_file
        def choose_drawing(file_types):
            selected=window.create_file_dialog(webview.FileDialog.OPEN,file_types=tuple(file_types))
            return selected[0] if selected else ''
        server.DRAWING_PICKER=choose_drawing
        server.SHUTDOWN=window.destroy
    def closing():
        log_close_request()
        if server.STATE.get('running'):
            return window.create_confirmation_dialog('任务正在执行','关闭客户端将中断当前执行，已保存的检查点会保留。确定关闭？')
        return True
    window.events.closing += closing

    def log_close_request():
        """记录每次关闭请求的来源，排查“没关窗口也弹出退出确认”：调用栈里有 destroy 说明是平台代码请求关闭，
        只有窗口消息循环说明是 Windows 或其他程序发来的关闭请求；同时记下当时的前台窗口。"""
        try:
            import ctypes, traceback
            user32 = ctypes.windll.user32
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(user32.GetForegroundWindow(), buffer, 256)
            stack = ''.join(traceback.format_stack(limit=30)[:-2])
            (server.DATA / 'close-requests.log').open('a', encoding='utf-8').write(
                f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} 前台窗口：{buffer.value or '（无）'}；任务执行中：{bool(server.STATE.get('running'))}\n{stack}\n")
        except Exception:
            pass
    if smoke:
        def verify():
            if window.evaluate_js('location.pathname')=='/login':
                # 冒烟测试用环境变量中的测试口令登录，再验证主界面
                status=window.evaluate_js("(function(){var x=new XMLHttpRequest();x.open('POST','/api/auth/login',false);x.setRequestHeader('Content-Type','application/json');x.setRequestHeader('X-Agent-Demo','1');x.send(JSON.stringify({username:'admin',password:"+json.dumps(os.environ.get('POWER_COST_SMOKE_PASSWORD',''))+"}));return x.status})()")
                if status!=200:
                    Path(sys.argv[sys.argv.index('--smoke-test')+1]).write_text(json.dumps({'login_status':status}),encoding='utf-8')
                    window.destroy()
                    return
                window.load_url(f'http://127.0.0.1:{PORT}/')
                return
            try:
                for _ in range(50):
                    if window.evaluate_js("document.body.classList.contains('is-admin')"):
                        break
                    time.sleep(0.1)
                style_menus(window)
                result=window.evaluate_js("({title:document.title,composer:!!document.getElementById('chatInput'),native:!!window.pywebview,icons:document.querySelectorAll('.app-icon').length,menuActions:typeof desktopAction==='function',user:document.getElementById('userName').textContent})")
                result['voiceBridge']=window.evaluate_js("typeof window.pywebview.api.voice_input==='function'")
                result['menuFont']=[str(c.Font.Name)+' '+str(c.Font.Size) for c in window.native.Controls if str(c.GetType().Name)=='MenuStrip']
                result['menus']=[str(item.Text) for control in window.native.Controls if str(control.GetType().Name)=='MenuStrip' for item in control.Items]
                window.evaluate_js("desktopAction('help')")
                result['helpOpened']=window.evaluate_js("document.getElementById('detailDialog').open")
                window.evaluate_js("closeDialog('detailDialog'); desktopAction('panel')")
                result['panelOpened']=window.evaluate_js("document.body.classList.contains('panel-open')")
                # 土石方三维模块与随包发布的 three.js：进入视图后应在本机加载完成，不依赖外网
                window.evaluate_js("desktopAction('earthwork')")
                for _ in range(150):
                    result['earthwork']=window.evaluate_js("Earthwork?true:(document.querySelector('#earthworkView .error')?.textContent||false)")
                    if result['earthwork']:
                        break
                    time.sleep(0.2)
                assert result['voiceBridge'] and result['menus']==['文件','编辑','视图','帮助'] and result['helpOpened'] and result['panelOpened']
                Path(sys.argv[sys.argv.index('--smoke-test')+1]).write_text(json.dumps(result),encoding='utf-8')
            finally:
                window.destroy()
        window.events.loaded += verify
    try:
        webview.start(gui='edgechromium',menu=menus,localization={'global.quitConfirmation':'确定要退出电力造价智能体平台吗？','global.quit':'退出','global.ok':'确定','global.cancel':'取消'},icon=str(server.HERE/'assets'/'app.ico'),debug=False,private_mode=True,storage_path=str(server.DATA/('client-cache-smoke' if smoke else 'client-cache')))
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()


def run():
    try:
        # 安装版启动前校验程序文件完整性并拒绝调试器附加
        protected.guard(Path(sys.executable).resolve().parent)
        main()
    except Exception as exc:
        if '--smoke-test' in sys.argv:
            raise
        # Keep startup failures visible without falling back to a web browser.
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw()
        messagebox.showerror('客户端启动失败',str(exc)[:400])
        root.destroy()
        if getattr(sys, 'frozen', False):
            raise SystemExit(1)
        raise


if __name__=='__main__':
    run()
