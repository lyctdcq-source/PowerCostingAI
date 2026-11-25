param([string]$PythonExe = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference='Stop'
# 项目模块编译为本机代码、技能源码加密后组装客户端与安装程序，详见 build_secure.py
& $PythonExe "$PSScriptRoot\build_secure.py" @args
if($LASTEXITCODE -ne 0){throw '安装包生成失败'}
