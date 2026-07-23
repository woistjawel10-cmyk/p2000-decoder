# p2000-decoder

Een moderne, laagdrempelige, open-source vervanger voor **PDW** (Pager Decoder
for Windows) om P2000/FLEX-pagermeldingen live te decoderen vanaf een
RTL-SDR-dongle.

Koppel je SDR, zie meldingen live binnenkomen op het scherm, en sla ze
optioneel op in een dag-logbestand (.log/.txt) voor verdere verwerking —
zonder een oud, gesloten Windows-programma nodig te hebben.

## Waarom dit project bestaat

De meeste P2000-hobbyprojecten leunen op **PDW**, een oude Windows-freeware
tool die al jaren niet meer actief onderhouden wordt. `p2000-decoder` bevat
een eigen, van de grond af herschreven FLEX-decoder (FSK-demodulatie,
framesynchronisatie, BCH-foutcorrectie, berichtparsing) die functioneel
gelijkwaardig is aan PDW, maar modern, open-source en zonder installatie-
poespas.

## Installatie

Vereist: Python 3.11+ en Windows (rtl_fm wordt als Windows-binary meegeleverd;
zie "Andere platforms" hieronder).

```bash
git clone <repo-url> p2000-decoder
cd p2000-decoder
pip install -r requirements.txt
```

Een ondersteunde RTL-SDR-dongle (bijv. de gangbare RTL2832U + R820T/R820D)
moet zijn aangesloten en de driver moet aanwezig zijn (dezelfde driver die
PDW/SDR#/rtl_fm ook gebruiken).

## Gebruik

```bash
# Standaard: P2000 in Nederland (169.650 MHz), alleen op het scherm tonen
python cli.py

# Meldingen ook wegschrijven naar dag-logbestanden
python cli.py --out logs/

# Aangesloten SDR-apparaten tonen
python cli.py --list-devices

# Andere frequentie/gain/apparaat
python cli.py --frequency 169650000 --gain 30 --device-index 0
```

Stop met **Ctrl+C** — `rtl_fm.exe` wordt daarbij netjes afgesloten.

Zie `python cli.py --help` voor alle opties (sample rate, ppm-correctie,
decodervenster, single-instance lock-bestand, etc.).

### Uitvoerformaat

Elke gedecodeerde melding wordt getoond (en, met `--out`, weggeschreven) in
hetzelfde regelformaat als PDW zelf gebruikt:

```
0302229 14:12:54 23-07-26 FLEX-A  ALPHA  1600  SPOED AMBU
```

`<capcode> <tijd> <datum> FLEX-A  <type>  1600  <berichttekst>` — zodat
bestaande scripts/tools die PDW-logregels verwachten, ongewijzigd kunnen
blijven werken.

## Hoe het werkt

```
RTL-SDR  ->  rtl_fm.exe (audio/PCM)  ->  FLEX-decoder (dit project)  ->  scherm + optioneel .log
```

`rtl_fm` (onderdeel van librtlsdr) doet het SDR-tunen en levert ruwe
PCM-audio; alle FLEX/POCSAG-decodering (FSK-demodulatie, framesync,
BCH-foutcorrectie, berichtparsing) gebeurt in Python, in `decoder/`.

## Andere platforms (Linux/macOS)

De decoder-logica zelf (`decoder/`) is platform-onafhankelijk (numpy/scipy).
Alleen `sdr/rtl_source.py` verwacht op dit moment `tools/rtl_fm.exe` op
Windows. Op Linux/macOS: installeer `rtl-sdr` via je package manager en geef
het pad naar het systeem-`rtl_fm`-binary mee via `--rtl-fm-path`.

## Ontwikkeling / tests

```bash
python -m unittest discover -s decoder/tests -v
```

60 tests, allemaal decoder-logica (FSK-demodulatie, BCH-foutcorrectie,
framesynchronisatie, berichtparsing) — geen SDR-hardware nodig om te draaien.

## Herkomst

De decoder in dit project is oorspronkelijk ontwikkeld als onderdeel van
[GrunnAlert](https://grunnalert.nl), een P2000-alerteringsapp, en vandaar
afgesplitst tot een losstaand, herbruikbaar stuk gereedschap.

## Licentie

GPL-3.0 — zie `LICENSE`. Meegeleverde `rtl_fm.exe`/`librtlsdr.dll` e.d. vallen
onder hun eigen licenties, zie `THIRD_PARTY_LICENSES.md`.
