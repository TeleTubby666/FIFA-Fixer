# Software mixer for Sound Blaster cards (work in progress)

Status: CD-ROM build tested on real hardware (AMD K6-III+, AWE32) and in DOSBox-X; "4 MB" build in DOSBox-X. One
unexplained 20-second freeze (black screen, crowd still audible) on real hardware is being looked into. FIFA-Fixer 2.1
installs it (`--mixer`); `patches/mkmix.py` is the older test tool that applies the same patches.

## What the game does

The sound manager of the CD-ROM build is a real-mode module inside `FIFA.EXE`. It drives the digital card through the
AIL 2 driver interface (`*.ADV`), but every digital call carries a **channel number (0-7) as an extra last argument**:

| AIL function | arguments |
|---|---|
| 123 `play_VOC_file` | driver, VOC far pointer, block marker, channel |
| 124 `VOC_playback_status` | driver, channel |
| 125 `start`, 126 `stop`, 127 `pause`, 128 `resume` | driver, channel |
| 129 `set_volume`, 131 `set_pan` | driver, value, channel |
| 130 `get_volume`, 132 `get_pan` | driver, channel |

Only the game's own `GF1DIGI.ADV` (Gravis UltraSound, card number 12) reads the channel. The Sound Blaster drivers are
plain AIL drivers with one voice, and the module keeps a flag (`[2DCh]`, set for card 12 only) that switches between
"eight channels" and "one voice with priorities". In the one-voice mode crowd, commentary and sampled effects replace
each other.

Card 12 has more consequences in the protected-mode part:

* `cs:BA8A` loads `SFX\SFX0.VOC` ... `SFX15.VOC` (the sampled effects) only for card 12.
* The effect player (`cs:5F7CA`) uses these samples when no FM card is configured (`MUSIC -1`), otherwise the FM
  effects (`sfx.xmi` + `SAMPLE.AD`).
* `cs:BAFF` picks another set of crowd samples for card 12, `cs:BB7F` skips the FM effect set-up for card 12.

Card number 13 is an unused slot for a driver file named `CUST_DIG.ADV`.

All samples of the game are 8-bit mono at 11111 Hz (`EASPORTS.VOC`: 22222 Hz); the looped ones use VOC repeat blocks
(type 6/7, endless). The game changes the pitch of an effect by rewriting the time constant of the first data block
before it starts the sample.

## The driver

The mixing runs in the interrupt handler with interrupts enabled (end of interrupt is sent first), every channel is
mixed with interrupts disabled, so that the game never changes a channel in the middle of it.

`driver/sbmix.asm` is an AIL 2 digital driver with the channel extension. It plays one endless 8-bit stream at
22222 Hz (DMA auto-initialise, two halves of 512 samples; DSP 2.00+ in auto-initialise mode, DSP 1.xx with single
blocks restarted in the interrupt) and mixes up to eight VOC samples into it in the interrupt handler: resampling with
linear interpolation, volume per channel, VOC block types 1, 2, 6, 7, 8 and 9 (8-bit PCM only), a soft clipping table
for the sum (`driver/mktab.py`). Pan is stored but not used (mono).

Build: `nasm -f bin -o CUST_DIG.ADV sbmix.asm`

Debug build: `nasm -f bin -DLOG -o CUST_DIG.ADV sbmix.asm`. It records every driver call in a ring buffer
(identical calls in a row are counted, not stored again) and writes `MIXLOG.BIN` when the game quits;
`driver/mixlog.py MIXLOG.BIN` prints it. Time stamps are counted in mixer interrupts (about 43 per second). Once the
game is past the menus, the log is frozen 120 entries after a stall (no driver call for 12 seconds while a sample
plays, or the same call 400 times in a row), so that the stall stays in the file.

## The patches in FIFA.EXE

The driver is card 13. Three places make card 13 an eight-channel card with the sampled effects loaded, and leave the
Gravis UltraSound path as it was; a fourth one maps the Sound Blaster cards to card 13, so that the card can be chosen
in `CONFIG.EXE` as usual. Offsets are file offsets of the CD-ROM build (856244 bytes); the bytes are the plain
(decrypted) ones. `patches/mkmix.py` has the offsets for both builds ("4 MB" build: 835860 bytes, same code, other
addresses).

1. Real-mode module, digital init (module offset `22D4h`, file `0x4151`, 44 bytes): the flag `[2DCh]` becomes 1 for
   card 13, FFh for card 12, 0 otherwise.

   ```
   old: 83FB0C 7522 BE0E00 53 E8580D E85E0E 8BD3 89160A03 B00A EE BB0A00 891EFC02 5B C606DC02FF EB18 C606DC0200
   new: 83FB0D B001 7422 83FB0C B000 751B BE0E00 53 E84F0D E8550E 891E0A03 8BD3 B00A EE C606FC020A 5B B0FF A2DC02
   ```

2. Real-mode module (module offset `2423h`, file `0x42A0`, 1 byte): the UltraSound-only code after the init tests bit 7
   of the flag instead of all bits: `FF` -> `80`.

3. Protected mode, `cs:BA8B` (file `0x7F3D`, 22 bytes): load the effect samples for cards 12 and 13.

   ```
   old: 8B2DDC400000 66833D114100000C 754B 8D357B410000
   new: A011410000 24FE 3C0C 8B2DDC400000 754A BE7B410000
   ```

4. The `FIFA.CFG` parser (file `0x7B73`-`0x7C31`, called from `0x7AD3`) is rewritten (`patches/cfgparse.asm`): the SFX
   and MUSIC lines share one tail now, which makes room for the mapping. Sound Blaster, 2.0, Pro, Pro New, 16, AWE32
   and Pro MCA (cards 5-8, 14, 15, 17) become card 13, and for card 13 the MUSIC line is set to -1: the effects are
   then played from the samples, and the FM effects, which would need more conventional memory, are not loaded.

`CONFIG.EXE` stays as it is; "Custom" (card 13) is not in its menu, but any Sound Blaster entry works.

## Notes on the encryption (needed to find the plain bytes)

The image is XORed with the low byte of a 32-bit key that is rotated right by one bit per byte. The 32 key bits,
starting at a file offset that is a multiple of 32, are `10100110001000101100100111001111` (bit *n* is bit 0 of the
key byte at offset *n*) with a phase that differs between parts of the file: phase 2 for the real-mode module
(file `0x1DE0`-`0x54F0`, module start = file `0x1E7D`), phase 8 for file `0x6040`-`0xE040` (protected-mode code,
`cs` = file + `0x3B4E`), then 2 less for every further `0x8000` bytes. The key byte at offset *o* with phase *p* is
`sum(bit[(o + p + j) % 32] << j for j in 0..7)`.
