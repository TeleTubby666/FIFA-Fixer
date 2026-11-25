#!/bin/sh
# assemble the replacement FIFA.CFG parsers for patches/mkmix.py
cd "$(dirname "$0")"
nasm -f bin -o cfg_cd.bin cfgparse.asm
nasm -f bin -DBASE=0x778E -DNUM=0x7900 -DHEX=0x784D -DVARS=0x3B59 -o cfg_4m.bin cfgparse.asm
