@echo off
rem Установщик для Windows: двойной клик или `install.cmd --auto`.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 install.py %* & goto :end)
where python >nul 2>nul && (python install.py %* & goto :end)
echo Python не найден. Установите Python 3.12: winget install -e --id Python.Python.3.12
echo (или https://www.python.org/downloads/ с галочкой "Add python.exe to PATH"), затем запустите снова.
:end
pause
