@echo off
setlocal
cd /d "%~dp0"

rem Hoofdprogramma: de GUI (venster, geen command-line-scherm).
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --distpath "." ^
  --version-file "version_info.txt" ^
  --icon "icon.ico" ^
  --paths "decoder" ^
  --paths "sdr" ^
  --name "p2000-decoder" ^
  gui.py

if errorlevel 1 exit /b %errorlevel%

rem Power-user-variant: command-line-tool met multi-SDR-ondersteuning (--sdr).
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --distpath "." ^
  --version-file "version_info.txt" ^
  --icon "icon.ico" ^
  --paths "decoder" ^
  --paths "sdr" ^
  --name "p2000-decoder-cli" ^
  cli.py

if errorlevel 1 exit /b %errorlevel%

echo.
echo Gebouwd: p2000-decoder.exe (GUI, hoofdprogramma) en p2000-decoder-cli.exe (command-line, multi-SDR).
echo Belangrijk: tools\ (rtl_fm.exe e.d.) wordt NIET in de .exe's gebundeld -
echo die map moet naast de .exe's blijven staan (of geef een ander pad op met
echo --rtl-fm-path). Kopieer bij het uitleveren dus de .exe('s) + de tools\-map samen.
