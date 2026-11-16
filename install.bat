@echo off
rem 小鲸鱼积分挂件 —— 安装引导（守护进程不需要 tkinter）
setlocal
set "SCRIPT=%~dp0install.py"
if not exist "%SCRIPT%" (
  echo 找不到 install.py，请在解压后的完整目录里运行本文件。
  pause
  exit /b 1
)

rem 1) 优先用 WorkBuddy 自带的 Python，省得另装
set "WBPY=%USERPROFILE%\.workbuddy\binaries\python\versions"
if exist "%WBPY%" (
  for /d %%d in ("%WBPY%\*") do (
    if exist "%%d\python.exe" (
      echo 使用 WorkBuddy 自带 Python: %%d\python.exe
      "%%d\python.exe" "%SCRIPT%" %*
      goto finish
    )
  )
)

rem 2) 系统里的 Python
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT%" %*
  goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%SCRIPT%" %*
  goto finish
)
where python3 >nul 2>nul
if not errorlevel 1 (
  python3 "%SCRIPT%" %*
  goto finish
)
echo.
echo 未找到 Python 3，也没找到 WorkBuddy 自带的运行时。
echo 请先装 Python 3.8+（勾选 Add python.exe to PATH），再双击本文件。
pause
exit /b 1

:finish
echo.
pause
endlocal
