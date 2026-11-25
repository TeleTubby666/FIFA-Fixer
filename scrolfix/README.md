# SCROLFIX – scroll timing test

A small DOS program (2 KB, no extender) that writes two test copies of
`FIFA.EXE` with a changed timing of the VGA hardware scrolling. It is meant
for testing the "shaking" picture when the view scrolls fast (reported on a
486 with a Matrox Mystique) and changes nothing else.

    SCROLFIX [FIFA.EXE]

writes `FIFA_S1.EXE` and `FIFA_S2.EXE` next to the file; `FIFA.EXE` itself
is not changed. The file is recognised by its size (floppy, CD-ROM and
"4 MB" build) and every changed region is compared with the original bytes
first, so it also works on an EXE that has other FIFA-Fixer patches, but not
on an unknown one.

## What is changed

The game scrolls with two VGA registers: the CRTC start address (3D4h,
index 0Ch/0Dh, moves the picture by 4 pixels) and the attribute controller
pel panning (3C0h, index 33h, 0–3 pixels in between). A standard VGA takes
the start address at the start of the vertical retrace, but the pel panning
at once. If the panning is written while the picture is being drawn, or the
start address too late, the picture jumps back and forth by a few pixels.

| routine | CD-ROM | 4 MB | floppy | original | S1 | S2 |
|---|---|---|---|---|---|---|
| menus, statistics | 8527h | 8142h | 7D3Eh | pan in the display period, then start | start in the display period, pan in the retrace | = S1 |
| second menu routine | 917Bh | – | – | the same | the same as above | = S1 |
| match | 551B7h, 55E02h | 51F84h, 52BE7h | 50E71h | CD/4 MB: start in the display period, pan in the retrace; floppy: pan in the display period | CD/4 MB: unchanged; floppy: like CD | start and pan both in the retrace |
| floppy 2nd match routine | – | – | 51AB0h | both in the retrace | start in the display period, pan in the retrace | unchanged |

S1 is the "standard VGA" order everywhere, S2 is for cards that take the
start address at once (then it must be written in the retrace as well).

`scrtab.inc` holds, per build and region: file offset, length, which copies
get it (bit 0 S1, bit 1 S2), the original (encrypted) bytes and the XOR
difference. FIFA.EXE is encrypted with a byte-wise key stream, so the
difference is simply old code XOR new code; the new code is assembled from
`menu1g.asm`, `menu2.asm` and `flgame2.asm`, the match changes of the
CD-ROM and 4 MB builds only move the existing instructions.
