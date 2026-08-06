# p2000-decoder — open-source PDW-alternatief

Een moderne, laagdrempelige, open-source FLEX-decoder om P2000-pagermeldingen
live te decoderen vanaf een RTL-SDR-dongle. Werkt als open-source alternatief
voor **PDW** (Pager Decoder for Windows) en gebruikt bewust hetzelfde
logregelformaat, zodat bestaande PDW-scripts en -tools ongewijzigd blijven
werken.

Koppel je SDR, zie meldingen live binnenkomen op het scherm, en sla ze
optioneel op in een dag-logbestand (.log/.txt) voor verdere verwerking.

## Waarom dit project bestaat

`p2000-decoder` bevat een eigen, van de grond af herschreven FLEX-decoder
(FSK-demodulatie, framesynchronisatie, BCH-foutcorrectie, berichtparsing),
gebouwd als modern, open-source gereedschap zonder installatie-poespas — een
vrij verkrijgbaar alternatief voor PDW. Het gebruikt bewust hetzelfde
logregelformaat als **PDW** (Pager Decoder for Windows), zodat bestaande
scripts en tools uit de P2000-hobbywereld ongewijzigd kunnen blijven werken.

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

## Gebruik: de GUI (aanbevolen)

```bash
python gui.py
# of de kant-en-klare .exe, zie "Build een .exe" hieronder:
p2000-decoder.exe
```

Een venster met:
- **Instellingen** — apparaat (dropdown, automatisch gedetecteerd), frequentie, gain, optioneel wegschrijven naar een .log-map, en een geluidssignaal bij spoedmeldingen (A1/B1/P1/SPOED)
- **Start/Stop**
- Een **live, PDW-achtige tabel** (Tijd/Capcode/Type/Tekst) — spoedmeldingen worden rood gemarkeerd
- **Zoeken/filteren** — typ om de tabel live te filteren op capcode, type of tekst
- **Log openen...** — laad een bestaand .log-bestand (van deze tool of van PDW zelf) terug in de tabel om te doorzoeken
- **Exporteer naar CSV...** — de huidige (eventueel gefilterde) weergave wegschrijven als CSV

### Gebruik: command-line (power users, meerdere SDR's tegelijk)

```bash
# Standaard: P2000 in Nederland (169.650 MHz), alleen op het scherm tonen
python cli.py

# Meldingen ook wegschrijven naar dag-logbestanden
python cli.py --out logs/

# Aangesloten SDR-apparaten tonen
python cli.py --list-devices

# Andere frequentie/gain/apparaat
python cli.py --frequency 169650000 --gain 30 --device-index 0

# Kant-en-klare .exe (geen Python nodig)
p2000-decoder-cli.exe --out logs/
```

Stop met **Ctrl+C** — alle `rtl_fm.exe`-processen worden daarbij netjes afgesloten.

De GUI (`gui.py`/`p2000-decoder.exe`) ondersteunt op dit moment een enkele
dongle tegelijk. Wil je meerdere dongles tegelijk laten draaien, gebruik dan
de command-line-variant hieronder.

### Meerdere SDR-dongles tegelijk

Heb je meerdere RTL-SDR-dongles aangesloten? Met `--sdr` (herhaalbaar) draait
elke dongle in zijn eigen, onafhankelijke verbind/decodeer-lus binnen dezelfde
sessie — als er eentje de SDR kwijtraakt of opnieuw moet verbinden, blijven de
andere gewoon doorgaan.

```bash
# Twee dongles op device-index 0 en 1, allebei op P2000
python cli.py --sdr 0 --sdr 1

# Op serienummer i.p.v. index, en/of met een eigen frequentie per dongle
python cli.py --sdr serial:00000001@169650000 --sdr serial:00000002@169700000
```

Formaat per `--sdr`: `INDEX[@FREQUENTIE_HZ]` of `serial:SERIENUMMER[@FREQUENTIE_HZ]`.
Zonder `@FREQUENTIE_HZ` wordt de standaard P2000-frequentie gebruikt. Zodra
`--sdr` gebruikt wordt, tellen `--frequency`/`--device-index`/`--device-serial`
niet meer mee (die zijn alleen voor de simpele enkele-dongle-modus). Met meer
dan een dongle krijgt elke regel op het scherm een `[dev0 169.6500MHz]`-achtig
label zodat je ziet van welke dongle een melding kwam; met een dongle blijft
de uitvoer ongelabeld.

### Build een .exe

```bash
pip install pyinstaller
build_exe.bat
```

Bouwt zowel `p2000-decoder.exe` (de GUI, met eigen icoon en versie-info) als
`p2000-decoder-cli.exe` (command-line, multi-SDR). **Belangrijk:** `tools/`
(rtl_fm.exe e.d.) wordt bewust niet in de .exe's gebundeld — die map moet
naast de .exe('s) blijven staan wanneer je ze uitdeelt.

Zie `python cli.py --help` voor alle command-line-opties (sample rate,
ppm-correctie, decodervenster, single-instance lock-bestand, etc.).

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

## Credits

Gemaakt door **Starlight FM**.

## Licentie

GPL-3.0 — zie `LICENSE`. Meegeleverde `rtl_fm.exe`/`librtlsdr.dll` e.d. vallen
onder hun eigen licenties, zie `THIRD_PARTY_LICENSES.md`.
