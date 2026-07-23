# Licenties van meegeleverde onderdelen

De map `tools/` bevat gecompileerde binaries van het
[rtl-sdr / librtlsdr](https://github.com/osmocom/rtl-sdr)-project (Osmocom):

- `rtl_fm.exe`
- `rtl_sdr.exe`
- `rtl_test.exe`
- `librtlsdr.dll`
- `libusb-1.0.dll` (libusb-project)
- `libwinpthread-1.dll` (MinGW-w64-project)

`librtlsdr` en de bijbehorende command-line tools zijn uitgebracht onder de
**GNU General Public License v2.0 (of later)**. De volledige licentietekst is
te vinden op <https://www.gnu.org/licenses/old-licenses/gpl-2.0.html> en in de
broncode van het rtl-sdr-project zelf.

Dit project (`p2000-decoder`) gebruikt deze tools als losstaande subprocessen
(via `subprocess.Popen`, geen gelinkte code) en is zelf uitgebracht onder de
GPL-3.0 (zie `LICENSE`), wat compatibel is met het meeleveren van deze
GPL-2.0(+)-tools.

`libusb-1.0.dll` (libusb) is uitgebracht onder de GNU Lesser General Public
License v2.1. `libwinpthread-1.dll` (onderdeel van MinGW-w64) is uitgebracht
onder een BSD-achtige licentie ("MinGW-w64 runtime licensing"). Beide staan
hier alleen als runtime-dependency van `librtlsdr.dll`/`rtl_fm.exe`.
