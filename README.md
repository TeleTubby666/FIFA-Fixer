# FIFA-Fixer – fixes for FIFA International Soccer (DOS)

A tiny patcher for **FIFA International Soccer** (EA Sports, DOS, 1993/94). It offers two independent fixes for *your own*
`FIFA.EXE`, and asks at start-up which one you want – **K6 fix**, **sound fix** or **both**:

| Fix | Problem | Bytes changed |
|---|---|---|
| **K6 fix** | The game does not start on **AMD K6** processors (`Memory protection fault`) | 7 |
| **Sound fix** | The **CD-ROM** and **"4 MB"** builds do not play some in-game effects (kick-off whistle, ...) | 4 |

It contains no game code, no game data and does not ship the game. You need a legally obtained copy of the game.

```
Abnormal program termination: Memory protection fault
CS:EIP = 000Fh:0009C4E8h
```

That message right after typing `FIFA` (the address differs, e.g. `000Fh:000A0697h` or `000Fh:000AC0D8h`, depending on the memory setup) is what the K6 fix cures.
Lowering the multiplier, disabling the L1/L2 caches or slowing the CPU down does **not** help, because the cause is not speed.
It is a CPU-dependent value in the game's start-up loader (details in [docs/TECHNICAL.md](docs/TECHNICAL.md)).

## Quick start

**Make a copy of your game folder first** (the tool also makes a backup, but be careful anyway). Then pick one way:

### A. On the DOS machine: `FIFAFIX.EXE`

Download `FIFAFIX.EXE` from the [Releases](../../releases) page. It is a small 16-bit DOS program (no DOS extender, no
memory manager) that the workflow in this repository builds from `c/fifafix.c` and tests in DOSBox;
its SHA-256 is listed next to it. Copy it next to the game and run:

```
FIFAFIX FIFA.EXE
```

The tool shows what it found and asks:

```
Which fix do you want to apply?
  1) K6 fix     - start-up crash on AMD K6 CPUs      [can be applied]
  2) Sound fix  - missing effects (whistle, ...)     [can be applied]
  3) Both
  q) Quit
```

The original is kept as `FIFA.ORG`. Then start the game as usual.

| Command | Effect |
|---|---|
| `FIFAFIX FIFA.EXE` | Menu: K6 fix, sound fix or both. |
| `FIFAFIX --all FIFA.EXE` | Apply every fix that applies to the file, no questions. |
| `FIFAFIX --k6 FIFA.EXE` / `--sound` | Apply only one fix, no questions. |
| `FIFAFIX --check FIFA.EXE` | Only report the state of the file. Changes nothing. |
| `FIFAFIX --all -o NEWFIFA.EXE FIFA.EXE` | Write a patched copy and leave the original untouched. |
| `FIFAFIX --no-backup FIFA.EXE` | Patch in place without creating `FIFA.ORG`. |

Without `--k6`/`--sound`/`--all` the menu is shown only if the tool runs on a terminal; in a script (redirected input or
output) only the K6 fix is applied, as in version 1.x.

### B. On a modern computer: Python

Install [Python 3](https://www.python.org/downloads/) (3.8 or newer, Windows / macOS / Linux), then run:

```
python fifafixer.py "path/to/FIFA.EXE"
```

The original is kept as `FIFA.EXE.orig`. Same menu and options as above. Copy the patched game
folder to your DOS machine afterwards. The Python version also prints the SHA-256 of the file.

### C. Build `FIFAFIX.EXE` yourself

* [Open Watcom](https://github.com/open-watcom/open-watcom-v2), 16-bit DOS target (what the workflow uses):
  `wcl -zq -bt=dos -ms -os -fe=FIFAFIX.EXE c/fifafix.c`
* any modern system: `cc -std=c89 -D_POSIX_C_SOURCE=200112L -O2 -o fifafix c/fifafix.c`

The source is plain C89 and reads the file in small blocks, so other DOS compilers should work too; only Open Watcom is
used and tested by the author.

### D. By hand (hex editor), K6 fix only

Search for these two byte patterns in `FIFA.EXE` (`??` = any byte, each must occur exactly once):

| # | Search for | Change |
|---|---|---|
| A | `89 0D ?? ?? ?? 00 05 A5 A5 A5 A5 E8` | replace the first six bytes `89 0D ?? ?? ?? 00` with `90 90 90 90 90 90` |
| B | `0F A2 80 E4 0F 80 FC 04 74 10 8B 44 24 1C` | replace the `74` (9th byte) with `EB` |

For the standard build listed below this is offset `0x96F43` (A) and `0x96EAA` (B). The sound fix cannot be done by hand
in a sensible way, because the game code in the EXE is encrypted (see [docs/TECHNICAL.md](docs/TECHNICAL.md)).

### Things to know

* **Keep the file name `FIFA.EXE`.** The start-up loader looks for its own EXE by that hard-coded name in the current
  directory, so a renamed copy will most likely not start.
* The tool refuses to touch anything it does not recognise: wrong file, ambiguous matches, or a file that was already
  modified by something else. Running it twice is harmless ("already applied").
* The sound fix is only offered for the known CD-ROM and "4 MB" file sizes (table below). The floppy build does not need it.

## Known builds

The patcher recognises these builds (all of them were checked with `--check`; the loader is the same code block in each,
the two patch sites are always `0x99` bytes apart):

| Build | Size | SHA-256 | K6 patch sites (file offset A / B) | Sound fix | Status |
|---|---|---|---|---|---|
| Floppy release (1994) | 816,980 | `8f1ae8d4080729df06a8ec4d2fceee820f21b21325b7146f183cc50d06a48971` | `0x96F43` / `0x96EAA` | not needed | K6 fix **tested** on real K6-III+ hardware and in DOSBox-X |
| CD-ROM release ("Special Edition") | 856,244 | `00956baf8ef9d1cffde9e5e0d85ddc3b9a2e2dff61711523321443cadf545d3c` | `0xA08AF` / `0xA0816` | yes (DOSBox-X; needs an FM card on the MUSIC line, see Troubleshooting) | K6 fix **tested** on real K6-III+ hardware (needs the CD and a working DOS CD driver, see Troubleshooting) |
| Patched "4 MB" EXE for the CD-ROM release | 835,860 | `30e405ba6e9195bdea6fcfb7316424b3ce15d1c2f405465c14134f8a5aaaf774` | `0x9B907` / `0x9B86E` | yes | K6 fix **tested** on real K6-III+ hardware; sound fix tested in DOSBox-X only (needs the CD and a working DOS CD driver, see Troubleshooting) |
| `EDITOR.EXE` of the CD-ROM release (team editor) | 313,780 | `d07bff908524c0d00ec485c53caa6a7301a6c617ff72a92c58360c79e535e0ff` | `0x1C1AD` / `0x1C114` | n/a | K6 fix **tested** on real K6-III+ hardware |

CRC-32 of the unpatched files (as printed by `FIFAFIX.EXE`): floppy `2D5602DB`, CD-ROM `5E0BCA79`, "4 MB" `A5ADB291`, `EDITOR.EXE` `F73CEBB5`.

For the standard build, CRC-32 is `2D5602DB` before and `4A08FA59` after patching (`FIFAFIX.EXE` prints CRC-32, the Python
tool prints both); SHA-256 after patching is `ecdecd544ac0d0ad4e677a6269f671980de1759c2b42745820b9b4bdeeff7395`.

The K6 fix finds its targets by byte pattern, not by fixed offset, so it also works on other builds of the game as long
as their loader is built the same way. If you have another version (other languages, other releases, ...), run `--check`
on it:

* **"file needs the fix"** or **"already carry the fix"**: it works, please report the size, SHA-256 and the result so the
  table can be extended. Reports from real hardware (does the patched file start?) are the most valuable ones.
* **"loader signature was not found"**: that build differs. Please report its size and SHA-256 (never upload the game
  itself).

The sound fix, in contrast, works at fixed positions and therefore only on the two file sizes marked "yes" (and only if the
bytes there are the expected ones).

The tool also prints the file name the loader looks for (see "Keep the file name" above); run `--check` once on your copy
to see it.

## Tested on

* Real hardware: AMD K6-III+ on an Asus P5A (Super Socket 7), MS-DOS. Before: crash as above. After: the game starts and runs
  (floppy build, CD-ROM build and "4 MB" build; the latter two with the CD inserted).
* DOSBox-X: the patched file runs.
* `FIFAFIX.EXE` itself: built and tested automatically in DOSBox against synthetic files (including files larger than
  64 KB) on every change. On real DOS hardware (K6 machine) `--check` was run on all three known builds and reports the
  same offsets as the Python tool. Reports of patching runs from other DOS machines are welcome.

Reports from other CPUs are welcome. The patch should also be harmless on Intel/Cyrix/other CPUs, because it makes the loader
use the same value as a 486 would on every CPU. A report in the VOGONS forum describes the same message on a Cyrix 486
upgrade; whether this patch fixes that has **not** been verified.

## Troubleshooting (CD-ROM and "4 MB" builds)

The CD-ROM and "4 MB" builds read their game data from the CD (`D:\FIFA\...`). The loader patch only fixes the start-up
crash; the following problems were seen on the test machine **after** patching and had other causes:

* **"No CDROM drive detected"**: no CD driver / MSCDEX is loaded, or the CD is not inserted.
* **Hard freeze right after starting the game (only a reset helps)**: seen with the original Windows MS-DOS `MSCDEX`
  together with `VIDE-CDD`. Using `SHSUCDX` (with the same `VIDE-CDD` driver) instead of `MSCDEX` fixed it. If
  `DIR D:` does not work in plain DOS, the game cannot work either; fix the CD driver first.
* **"Insufficient free conventional memory" (CD-ROM build)**: seen with the original `MSCDEX` set-up even though
  about 620 KB of conventional memory were free. It disappeared after switching to `SHSUCDX` + `VIDE-CDD`. Disabling the
  sound card in `FIFA.CFG` also moved the game past this message once, so the exact cause (CD driver or sound set-up)
  is not settled; try the CD driver first.
* **Stuttering in the CD-ROM and "4 MB" builds** (the floppy build is smooth on the same machine): observed on the K6-III+ at full
  speed, with and without sound, with PIO and DMA CD access. It disappeared when the CPU was slowed down with a CPU
  throttle setting (the multiplier alone did not make the difference). The cause has not been found; it looks like a
  timing problem that appears on a very fast CPU. Reports from other CPUs are welcome.
* **Missing in-game sound effects in the CD-ROM and "4 MB" builds** (e.g. the referee's whistle at kick-off; crowd chants
  and, in the CD build, the commentary are audible; the floppy build plays everything): this is what the **sound fix**
  is for, see above. The cause is in the game code, not in the loader and not in your sound card or driver: when the
  effects are played through the sound-effects driver, these two builds explicitly skip effect 11 (the kick-off whistle)
  and effect 12; the floppy build does not. In the "4 MB" build the fix replaces the two skip instructions by `NOP`s
  (4 bytes). The CD-ROM build has no FM version of these effects (its `sfx.xmi` is only 202 bytes); its effects exist
  as `SFX0.VOC` ... `SFX15.VOC` on the CD, but the game loads these only for a Gravis Ultrasound. The CD fix therefore
  loads them for every card and sends effects 11 and 12 to the sample path instead of skipping them (4 bytes as well,
  details in [docs/TECHNICAL.md](docs/TECHNICAL.md)).
  Checked in DOSBox-X: "4 MB" build with SB16 and AWE32 settings, music on and off; CD-ROM build with
  `SFX 14,220h,5,1,5` and `MUSIC 3,388h,-1,-1,-1` (kick-off whistle and other effects audible). **In the CD-ROM build the
  MUSIC line must name an FM card (e.g. `MUSIC 3,388h,-1,-1,-1`)**: with `MUSIC -1,...` it stays silent. Neither build has
  been tried on real hardware. Reports are welcome.
* Replays: floppy-generation replays are 21,634 bytes, CD-generation replays 21,674 bytes. Keep each replay set with the
  build that created it (the mismatch is a suspected cause of a black screen when loading a replay, not confirmed).

## What exactly does the K6 fix change?

Short version: the loader computes a decryption key. One step depends on whether the CPU notices that an instruction was
overwritten right before it executes (486: no; Pentium: yes) and the loader compensates by asking the CPU family via CPUID.
The K6 reports "Pentium-class" but most likely does not behave like a Pentium in that one spot, so the compensation is
applied wrongly, the key is wrong and the decrypted code is garbage. (This explanation fits every observation made so far,
and the patch built on it works, but the K6 internals involved were not measured separately.) The patch removes the self-modifying store (site A) and
disables the compensation (site B), so every CPU gets the key a 486 gets. Full explanation with the instruction listing:
[docs/TECHNICAL.md](docs/TECHNICAL.md).

Neither fix removes or bypasses any licence check, serial number, manual question or disc check, and neither changes the
game beyond the bytes described.

## Running the self-tests

```
python tests/test_patcher.py
```

The test builds synthetic files that contain only the short patterns and needs no game file. If a C compiler is installed,
it also checks that the C version (the source of `FIFAFIX.EXE`) produces the same output as the Python version, including a
build with a 64-byte read buffer that forces the patterns across block borders. `tests/dos_test.py` plus
`.github/workflows/build.yml` run the real DOS executable in DOSBox.

## Undo

Copy the backup back over the game EXE: `FIFA.ORG` (DOS tool) or `FIFA.EXE.orig` (Python tool). The two fixes are independent, so there is no half-way state to worry about.

## Disclaimer and licence

This is an independent compatibility tool. It is not affiliated with, endorsed by or sponsored by Electronic Arts, FIFA or
AMD; all names and trademarks belong to their respective owners. The tool contains no part of the game except the two
short byte patterns needed to locate the patch sites. You are responsible for using it only on a copy of the game you are
entitled to use. It comes without any warranty; keep a backup.

The tool and its documentation are released into the public domain under [The Unlicense](LICENSE).

## Changelog

* **2.0**: renamed to **FIFAFixer** (`fifafixer.py`, `FIFAFIX.EXE`, `c/fifafix.c`; before: `fifa_k6_fix.py`, `FK6FIX.EXE`).
  New **sound fix** for the CD-ROM and "4 MB" builds; at start-up the tool asks for K6 fix, sound fix or both
  (`--k6`, `--sound`, `--all` skip the question). Without a terminal and without a selection it still applies only the K6 fix.
* **1.2**: `FK6FIX.EXE`, a 16-bit DOS version of the tool (`c/fk6fix.c`, built and tested by the workflow); the Python tool
  prints CRC-32 as well. The IPS patch was removed: it only fit one build and could not check what it was applied to.
* **1.1**: prints the file name the loader looks for; CD-ROM and "4 MB" builds added to the list of recognised builds.
* **1.0**: first release.
