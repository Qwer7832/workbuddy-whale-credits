@echo off
rem Restart WorkBuddy with a LOCAL debugging port (127.0.0.1 only).
rem Path auto-detected; falls back to the desktop shortcut (already patched).
setlocal
set "WB_EXE="
for %%P in ("E://workbuudy//WorkBuddy//WorkBuddy.exe" "%LOCALAPPDATA%\Programs\WorkBuddy\WorkBuddy.exe" "C://Program Files//WorkBuddy//WorkBuddy.exe") do (
  if not defined WB_EXE if exist %%P set "WB_EXE=%%~P"
)
echo 正在关闭 WorkBuddy ...
taskkill /IM WorkBuddy.exe >nul 2>&1
timeout /t 3 /nobreak >nul
taskkill /IM WorkBuddy.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
if defined WB_EXE (
  echo 正在带调试端口 9223 启动 ...
  start "" "%WB_EXE%" --remote-debugging-port=9223 --remote-allow-origins=*
) else (
  echo 未找到 WorkBuddy.exe，改用桌面快捷方式（已带调试参数）...
  start "" "%USERPROFILE%\Desktop\WorkBuddy.lnk"
)
timeout /t 4 /nobreak >nul
exit
