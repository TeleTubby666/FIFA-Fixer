#!/bin/sh
# assemble the replacement code for patches/mkmix.py
cd "$(dirname "$0")"
nasm -f bin -o cfg_cd.bin cfgparse.asm
nasm -f bin -DBASE=0x778E -DNUM=0x7900 -DHEX=0x784D -DVARS=0x3B59 -o cfg_4m.bin cfgparse.asm
nasm -f bin -DBASE=0x741D -DNUM=0x758F -DHEX=0x74DC -DVARS=0x3AD9 -DKEEPMUSIC -o cfg_fl.bin cfgparse.asm
nasm -f bin -DORG=0x22FD -DF1=0x3079 -DF2=0x3182 -DPORT=0x310 -DVOL=0x302 -DFLAG=0x2E2 -o rm_fl.bin rminit.asm
nasm -f bin -o sel_fl.bin selector.asm
