#!/bin/sh
# assembles SCROLFIX.COM; the *.bin files are the replacement code that
# went into scrtab.inc (as XOR difference to the encrypted original bytes)
cd "$(dirname "$0")"
set -e
nasm -f bin -o SCROLFIX.COM scrolfix.asm
nasm -f bin -DORG=0x8527 -DADDR=0x4828 -o m1_cd.bin menu1g.asm
nasm -f bin -DORG=0x8142 -DADDR=0x4258 -o m1_4m.bin menu1g.asm
nasm -f bin -DORG=0x7D3E -DADDR=0x41C8 -o m1_fl.bin menu1g.asm
nasm -f bin -o m2_cd.bin menu2.asm
nasm -f bin -o g2_fl.bin flgame2.asm
ls -l SCROLFIX.COM
