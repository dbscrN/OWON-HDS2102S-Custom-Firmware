# Custom firmware for the OWON HDS2102S handheld oscilloscope-multimeter

**English** | [Русский](README.ru.md)

An unofficial modification of firmware **V4.7.1** for the **OWON HDS2102S** handheld oscilloscope-multimeter (hardware variant `HDS2102S_LS`), together with the tools it was made with: a `.upp` package unpacker, a disassembler wrapper, a palette patcher and the build of an add-on written in C.

> **Warning.** Everything here is at your own risk. The firmware has been tested on a single device. A failed flash can brick the device: no recovery method without a programmer is known. It only fits the `HDS2102S_LS` variant running firmware V4.7.1 — this model exists in incompatible hardware revisions.

## What is added

- **A trend graph of the measured value in multimeter mode** — 120 points, a window of 30 s / 1 min / 5 min / 30 min, the scale follows the minimum and maximum within the window.
- **Minimum and maximum** over the window, next to the reading.
- **Frequency on AC voltage** (Hz / kHz / MHz) — the multimeter switches between voltage and frequency.
- **The graph pauses** while the reading is held (Hold), on overload (`OL`) and while the range is changing.
- **A graph menu** on the F1–F4 buttons, opened with the `HOR` key.
- A build with a **red interface palette** instead of the blue one.
- **English-only** interface (all other languages are redirected to the English strings).

## Controls

In multimeter mode the `HOR` key opens a menu on the bottom buttons:

| Button | Action |
|---|---|
| F1 `Trend` | turn the graph on / off |
| F2 `Time` | window length: 30s → 1min → 5min → 30min |
| F3 `Freq` | on AC voltage: voltage ↔ frequency |
| F3 `Clear` | in all other modes: clear the graph |
| F4 `Close` | close the menu (pressing `HOR` again does the same) |

While the menu is open, a service line is shown inside the graph — the raw bytes of the latest frame from the multimeter controller.

## Ready-made firmware

| Folder | What it is | Checksum in "System → About" |
|---|---|---|
| `firmware/stock/` | original V4.7.1 | `2601FC70` |
| `firmware/trend-red/` | graph + frequency, red palette | `61C604B7` |
| `firmware/trend-blue/` | graph + frequency, stock blue palette | `CF2AD55D` |

The file in every folder is named `Scope.upp` — that is the name the device accepts.

### How to flash

1. In the device settings turn on the USB "MSC" (mass storage) mode — without it the computer will not see the device as a disk.
2. Connect the device to the computer over USB; it shows up as a disk.
3. Copy the `Scope.upp` you want onto it.
4. On the device open `System → About` and choose `Upgrade`.
5. Reboot the device — the update is applied at start-up.
6. Check the checksum in `System → About`: it must match the table above.

You can go back by flashing `firmware/stock/Scope.upp` — as long as the device still boots and accepts updates.

## Building from source

You need Python 3 with the `capstone` package and the `arm-none-eabi-gcc` compiler (Arm GNU Toolchain).

```bash
pip install -r requirements.txt
```

```bash
python tools/upp_tool.py unpack firmware/stock/Scope.upp unpacked
```

```bash
python tools/patch_palette.py build red 3=C81010
```

```bash
python tools/build_trend.py trend --base build/red
```

The result is `build/trend/Scope.upp`. Without `--base build/red` you get the stock-palette build; the `--keep-langs` switch keeps all languages. `python tools/device_checksum.py build/trend` prints the checksum the device will show for any build.

## What is in the repository

| Path | Purpose |
|---|---|
| `src/trend.c`, `src/trend.ld` | the add-on code (graph, menu, frequency) |
| `tools/upp_tool.py` | unpacks and packs `.upp` |
| `tools/build_trend.py` | compiles the add-on, embeds it into the image, packs it |
| `tools/patch_palette.py` | replaces palette colours |
| `tools/device_checksum.py` | computes the checksum the device will show |
| `tools/hds.py` | disassembler: functions, cross-references, dumps |
| `tools/mklisting.py` | full listing and function index into `analysis/` |
| `tools/fpga_regs.py`, `tools/scpi_tree.py`, `tools/rwdata.py` | FPGA register and SCPI maps, data-section unpacker |
| `docs/NOTES.md` | how the firmware works and how the add-on is embedded (in Russian) |
| `docs/scpi_tree.txt`, `docs/fpga_regs.txt` | the recovered SCPI tree and FPGA register map |
| `CLAUDE.md` | instructions for an AI agent continuing the project (in Russian) |

The `unpacked/`, `build/` and `analysis/` folders are created by the tools and are not part of the repository.

## Limitations

- The multimeter controller does not report voltage and frequency at the same time — only one of them.
- Frequency is measured only if the signal is strong enough for the multimeter input (the device's own calibration output is too weak).
- The graph updates at the multimeter's pace, about 3.5 readings per second: it will not show short pulses.
- Graph settings are not kept after power-off.
- The add-on code occupies the place of the 18 px CJK glyph table, so the Chinese and Japanese interface is not supported in this build.
- The dangerous SCPI command `:SECurity` changes the microcontroller's read protection — do not send it.

## License

The code in `src/` and `tools/` and the documentation are released under the [MIT License](LICENSE). The original firmware belongs to OWON; it and the builds derived from it under `firmware/` are included only for restoring the device and for a reproducible build, and are not covered by that license.
