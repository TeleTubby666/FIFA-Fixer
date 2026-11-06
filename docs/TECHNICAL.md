# Technical notes

How the start-up crash on AMD K6 CPUs comes about and what the patch changes.
All of this was derived by disassembling the loader inside the EXE (no source code was available). Section 6 lists what
was verified and what was not.

Numbers refer to the standard build with SHA-256 `8f1ae8d4…06a48971` (816,980 bytes). In that build
**virtual address = file offset + 0x5450** for everything described here. The CD-ROM build (856,244 bytes) and the "4 MB"
executable (835,860 bytes) contain the same loader code at other addresses: patch site A always lies exactly `0x99` bytes
behind the `74` byte of site B (`0xA1` bytes behind the start of its pattern); only the absolute addresses inside the instructions differ (which is why the
patcher treats them as wildcards). The loader's file name string sits `0xF8` bytes behind the start of the site B
pattern in all three builds (it reads `fifa.exe` in each, checked with `--check`); the patcher prints it from there if it finds a plausible 8.3 name, and prints nothing otherwise.

## 1. The EXE is a Phar Lap TNT program, not DOS/4GW

| File offset | Content |
|---|---|
| `0x0000` | DOS stub of Phar Lap's "bind" utility (`Copyright (C) 1986-1991 Phar Lap Software`, error texts `GOTNTB …`) |
| `0x1816` | Directory (`DIRH`) of two bound-in files: `fifa.exe` at `0x18A0` (`0x95851` bytes) and `TNTB.EXE` at `0x97100` (`0x30654` bytes) |
| `0x18A0` | `fifa.exe`: Phar Lap flat-model executable (`P3`), 0x180-byte header, run-time parameters, load image at `0x1AA0` (`0x95651` bytes) |
| `0x97100` | `TNTB.EXE`: the Phar Lap TNT DOS extender that runs the program |

The string `DOS/4G` that appears in the file belongs to the extender's list of "other extender present" messages. The game
does not use DOS/4GW.

The load image is high-entropy (encrypted and/or compressed, no readable strings). The only readable part is a small
**loader at the very end of the image** (virtual `0x9C270`–`0x9C541`, file `0x96E20`–`0x970F1`), which is also the
program's entry point (`0x9C310`).

## 2. What the loader does (as far as analysed)

1. Hooks INT 3 through the extender and triggers it, so execution continues in its own handler (presumably an anti-debugging
   construction: a debugger that owns INT 3 changes what happens next).
2. Looks for a file with a hard-coded name (`fifa.exe` in all three known builds) in the current directory (DOS find-first) and reads it into a buffer. **The EXE must
   therefore keep that name.**
3. Derives a 32-bit **key** from values that are identical on every machine for a normal run (return address, selectors,
   saved flags, a few dwords of low memory, the current TF/IF flags). A debugger, breakpoint or modified loader changes
   the result.
4. Passes the key through `cpu_check` (section 3) and adds it to five dwords at `0x9C4E5…0x9C4F5`. Those 20 bytes are
   **encrypted loader code** and only become valid instructions with the correct key.
5. Continues into the decryptor, which uses the same key as a rolling XOR key (`xor [esi],al / inc esi / ror eax,1`), checks
   the loaded image against the file, and finally jumps into the real program.

With a wrong key step 4 yields garbage, the CPU executes it and the extender reports
`Memory protection fault`. The reported `CS:EIP = 000F:0009C4E8` is exactly three bytes into the 20-byte block, i.e. the
second instruction of the garbage. Other addresses were reported for the same crash on a K6-III+ in 2004
([Planet 3DNow! thread](https://forum.planet3dnow.de/index.php?threads/amd-prozessoren-zu-alten-dos-games-100-kompatibel.137334/)):
`000F:000A0697` with the default memory setup and `000F:000AC0D8` after switching to different HIMEM/EMM386 drivers. Both lie
beyond the end of the image (memory that is only filled at run time). That the address changes with the memory
configuration fits the explanation: garbage code is executed, and where it ends up depends on what happens to be in
memory. It is not evidence of a second problem. None of these addresses appear after the patch.

## 3. The two pieces that make the key CPU-dependent

### Site A: a store into the next instruction

```
9C38D  add  eax,[0]
9C393  mov  [9C39A],ecx             ; 89 0D 9A C3 09 00   <- overwrites the immediate of the next instruction
9C399  add  eax,0A5A5A5A5h          ; 05 A5 A5 A5 A5      (its imm32 is at 9C39A)
9C39E  call cpu_check               ; E8 CE FE FF FF
9C3A3  add  [9C4E5],eax             ; ... five of these
```

The store is directly followed by the instruction it modifies. A 386/486 has already fetched the old instruction into
its prefetch queue and executes it with the **old** constant `0A5A5A5A5h`. A CPU that detects self-modifying code
(Pentium and later) executes the instruction with the **new** value, `ecx`. This is the prefetch-queue behaviour
that DOSBox-X emulates with its `486_prefetch` CPU type (see DOSBox-X issue #2481).

### Site B: the compensation in `cpu_check`

`cpu_check` (entry `9C271`, saves everything with `pusha`/`pushf`) detects the CPU class with the usual flag tricks (AC
and ID bit) and calls `cpuid`:

```
9C2ED  mov  eax,1
9C2F2  cpuid
9C2F4  and  ah,0Fh
9C2F7  cmp  ah,4
9C2FA  jz   done                    ; 74 10   <- family 4 (486): leave the key alone
9C2FC  mov  eax,[esp+1Ch]           ; saved ECX
9C300  sub  [esp+20h],eax           ; saved EAX -= ECX
9C304  add  dword [esp+20h],0A5A5A5A5h
9C30C  popf / popa / ret            ; done
```

For every CPU with a family other than 4 the loader turns "added `ecx`" back into "added `0A5A5A5A5h`".

### The algebra

Let `X` be `eax` before the add, `E` the value of `ecx`, `C = 0A5A5A5A5h`, and `R = X + C` the key the game was built for.

| CPU | modification of the next instruction seen? | CPUID family | resulting key |
|---|---|---|---|
| 386 / 486 | no | none / 4 | `X + C` = **R** |
| Pentium | yes | 5 | `X + E`, then `- E + C` = **R** |
| K6 (assumption, see below) | no | 5 | `X + C`, then `- E + C` = `R + C - E` (wrong) |
| Cyrix 486 upgrade, reported on VOGONS (unverified guess) | yes | 4 (assumed) | `X + E` (wrong) |

The design works only when "modification seen" and "family != 4" go together. The K6 identifies itself as a
Pentium-class CPU, so the loader subtracts `E` and adds `C` again. The most likely reason for the failure is that the K6
does not pick up the store to the instruction directly behind it (its decoders are already working on it), so the
compensation is applied on top of a key that was already correct. The crash persists with caches disabled and with a lower
multiplier, which fits: it is neither a timing nor a cache-size problem. The K6 mechanism itself was **not** measured; what
was verified is the effect of the patch (section 6).

## 4. The patch

Two edits, 7 bytes in total, so that the key is `X + C` on every CPU:

| Site | File offset (tested build) | Before | After | Effect |
|---|---|---|---|---|
| A | `0x96F43` | `89 0D 9A C3 09 00` | `90 90 90 90 90 90` | The store is removed, the next instruction always uses the constant `0A5A5A5A5h`. |
| B | `0x96EAA` | `74 10` | `EB 10` | The family test always takes the "done" branch, nothing is compensated. |

Both edits are needed: with only A, the K6 would still be "compensated"; with only B, a Pentium would use `E`.

The patched loader then behaves like the original on a 486, the machine the key was designed for, on every CPU. The other
inputs to the key are not touched. Self-modifying writes elsewhere in the loader are either far (more than 0x100 bytes)
from where the modified code runs or are followed by a control transfer (call/return), which flushes the prefetch queue on
every CPU. Nothing else in the loader appears to depend on CPU behaviour in the way site A does.

## 5. Locating the sites in another build

The loader is plain code, so the sites can be found by pattern; absolute addresses differ between builds, the code does
not. This is what `fifafixer.py` / `fifafix.c` (`FIFAFIX.EXE`) do:

* Site A: `89 0D xx xx xx 00 05 A5 A5 A5 A5 E8 xx xx xx xx`, exactly one match in the file.
* Site B: `0F A2 80 E4 0F 80 FC 04 74 10 8B 44 24 1C 29 44 24 20 81 44 24 20 A5 A5 A5 A5 9D 61 C3`, exactly one match.
* A must come after B, less than `0x400` bytes behind it.
* Anything else (no match, several matches, wrong order, only one site already changed) is refused and nothing is written.
* The result is re-analysed before it is written, and exactly seven bytes must differ.

If a build has a different loader (different encryption wrapper, other constants) the tool says so and stops. Such a
build needs its own analysis.

## 6. What was verified, what was not

Verified

* The disassembly of the loader is consistent (all call targets land on instruction boundaries, string and data references
  match the code that uses them).
* The patched file starts and runs on an AMD K6-III+ (Asus P5A) and in DOSBox-X. The unpatched file crashed on the K6-III+
  with `CS:EIP = 000F:0009C4E8` (caches off, lowered multiplier); earlier runs (2004, see section 2) showed `000F:000A0697` and, with other memory drivers, `000F:000AC0D8`.
  The same thread reports that an Intel Pentium 200 MMX ran the game, and that the crash stayed with the CPU clocked down
  to 200 MHz and the L1 cache disabled.
* The patcher's output is byte-identical to the file that was tested on the hardware. The Python and C versions produce
  identical results; `tests/test_patcher.py` covers patched, unpatched, mixed, ambiguous, foreign-file and existing-backup cases
  on synthetic files.

Not verified

* The actual K6 behaviour at site A (it is the explanation that fits, not a measurement). A small test program that stores
  into the next instruction and reports the result would settle it.
* Other languages and releases, other K6 variants (K6, K6-2, K6-III), other CPUs (Cyrix,
  Pentium MMX, K5, Pentium Pro/II).
* `FIFAFIX.EXE` patching on real DOS hardware (`--check` was run on all three builds on a real DOS machine; patching is tested in DOSBox by the workflow).

## 7. The sound fix (CD-ROM and "4 MB" builds)

Symptom: in these two builds some in-game effects are silent, most noticeably the referee's whistle at kick-off, while
crowd chants (and, in the CD build, the commentary) are audible. The floppy build plays everything on the same setup.

**Where it comes from.** The play-effect routine takes one of two paths depending on the music-card setting. While the
CD streaming code is active it sets the music card to the effects card, so effects go through the sound driver's
play-effect call (driver function `1Dh`, effect number in `cx`). In the CD-ROM and "4 MB" builds that path starts with

```
cmp  eax, 0Bh        ; effect 11 = kick-off whistle
jz   return
cmp  eax, 0Ch        ; effect 12
jz   return
```

i.e. the plain bytes `3D 0B 00 00 00 74 F6 3D 0C 00 00 00 74 EF`. The floppy build has no such lines. Which card is
installed does not matter, the effects are dropped by the game before the driver is called. (Earlier theories that the
cause was a missing FM music card or the sample loading restricted to the Gravis Ultrasound did not hold up: removing the
load condition alone did not bring the whistle back; removing the two skips did.)

**Why it is a fixed-position patch.** The game image in the EXE is encrypted with a byte-wise XOR key stream
(`xor [esi],al / inc esi / ror eax,1`, period 32 bytes, no compression); the loader decrypts it in memory before it
jumps in. A byte can therefore be changed in the file without knowing the key: the new ciphertext byte is

```
new = old ^ plain ^ 0x90          ; 0x90 = NOP
```

for the four conditional-jump bytes (`74 F6 74 EF` in plain) so that they decrypt to `90 90 90 90` ("4 MB" build; the CD-ROM
build uses different plain bytes, see below). The positions are
fixed per build, which is why the sound fix works only on the known file sizes:

| Build | Size | File offsets | Ciphertext before | after |
|---|---|---|---|---|
| "4 MB" | 835,860 | `0x57B85`, `0x57B86`, `0x57B8C`, `0x57B8D` | `B4 16 AF 02` | `50 70 4B 7D` |
| CD-ROM | 856,244 | `0x7F4B`, `0x7F4C`, `0x5BB38`, `0x5BB3F` | `07 72 05 24` | `E2 A9 51 50` |

The tool compares the four bytes with the expected "before" values (or the "after" values for an already fixed file) and
refuses everything else. Exactly four bytes differ afterwards, which the tool checks before writing.

**CD-ROM build.** Skipping alone is not enough there: its FM effect bank (`music\adlib\sfx.xmi`) is only 202 bytes and has no
whistle. The effects exist as samples `SFX\SFX0.VOC` ... `SFX15.VOC` (8-bit, 11,111 Hz) on the CD, and the sample path of the
play-effect routine (`edx = [[table] + effect*8 + 4]`, driver function 9) is used when the music card is not an FM card.
The table loader (`cs:BA8A`) returns early unless the effects card is 12 (Gravis). Four plain bytes are changed:

| Plain before | after | Meaning |
|---|---|---|
| `75 4B` at `cs:BA99` (file `0x7F4B`, `0x7F4C`) | `90 90` | load the `SFX0`-`SFX15` table for every card |
| `74 F6` (second byte, file `0x5BB38`) | `74 A2` | effect 11: jump to the sample path (`cs:5F7D1`) instead of returning |
| `74 EF` (second byte, file `0x5BB3F`) | `74 9B` | effect 12: same |

What was verified: with the "4 MB" build in DOSBox-X (SB16 and AWE32 settings, music on and off) the kick-off whistle is
audible after the fix. With the CD-ROM build in DOSBox-X (`SFX 14,220h,5,1,5`, `MUSIC 3,388h,-1,-1,-1`) the whistle and other
effects are audible; with `MUSIC -1,...` (no FM card, all effects through the sample path) nothing is audible, which is not
understood. Neither build was tried on real hardware. The floppy build needs no fix (and has a different size).
