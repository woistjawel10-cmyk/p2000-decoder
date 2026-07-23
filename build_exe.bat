@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --console ^
  --distpath "." ^
  --version-file "version_info.txt" ^
  --name "p2000-decoder" ^
  cli.py

if errorlevel 1 exit /b %errorlevel%

echo.
echo Gebouwd: p2000-decoder.exe
echo Belangrijk: tools\ (rtl_fm.exe e.d.) wordt NIET in de .exe gebundeld -
echo die map moet naast p2000-decoder.exe blijven staan (of geef een ander
echo pad op met --rtl-fm-path). Kopieer bij het uitleveren dus p2000-decoder.exe
echo + de tools\-map samen.
