"""Windows UI integrations invoked only by explicit user actions."""
import ctypes
import sys

def style_menus(window):
    from System import Action
    from System.Drawing import Font, FontStyle, Color
    from System.Windows.Forms import Padding
    def apply():
        for control in window.native.Controls:
            if str(control.GetType().Name)!='MenuStrip':continue
            control.Font=Font('Microsoft YaHei UI',10.5,FontStyle.Regular)
            control.Padding=Padding(12,4,12,4)
            control.BackColor=Color.FromArgb(250,250,250)
            for item in control.Items:
                item.Padding=Padding(16,6,16,6)
                item.Margin=Padding(2,0,2,0)
                item.DropDown.Font=control.Font
                for child in item.DropDownItems:
                    child.Padding=Padding(12,7,12,7)
    window.native.Invoke(Action(apply))

class DesktopBridge:
    def __init__(self): self._window=None
    def open_url(self, url):
        """对话中的网页链接用系统默认浏览器打开，只接受 http/https 地址。"""
        import webbrowser
        from urllib.parse import urlparse
        if urlparse(str(url)).scheme not in ('http', 'https'):
            return {'error': '只能打开网页链接'}
        webbrowser.open(str(url))
        return {'ok': True}
    def choose_folder(self):
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return {'path': selected[0] if selected else ''}
    def voice_input(self):
        if sys.platform!='win32':return {'error':'语音输入仅支持Windows客户端'}
        # Keep the caret in the composer. Windows controls microphone permissions,
        # transcription, language choice and the listening indicator.
        from System import Action
        def activate(): self._window.native.Activate()
        self._window.native.Invoke(Action(activate))
        self._window.evaluate_js("document.getElementById('chatInput').focus()")
        user32=ctypes.windll.user32
        user32.keybd_event(0x5B,0,0,0)
        user32.keybd_event(0x48,0,0,0)
        user32.keybd_event(0x48,0,2,0)
        user32.keybd_event(0x5B,0,2,0)
        return {'ok':True,'method':'windows-dictation'}
