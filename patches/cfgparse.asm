; FIFA.CFG parser (CD-ROM build: file offset 7B73h .. 7C31h; "4 MB" build:
; 778Eh .. 784Ch, assemble with -DBASE=0x778E -DNUM=0x7900 -DHEX=0x784D
; -DVARS=0x3B59), rewritten:
; one shared tail for the SFX and the MUSIC line, which makes room for the
; card mapping: a Sound Blaster family card on the SFX line becomes card 13
; (the mixer driver CUST_DIG.ADV) and switches the FM effects off; card 13
; (set by hand) switches them off as well.
        bits 32
%ifndef BASE
%define BASE 0x7B73
%endif
%ifndef NUM
%define NUM 0x7CE5
%endif
%ifndef HEX
%define HEX 0x7C32
%endif
        org BASE
%ifndef VARS
%define VARS 0x4109                     ; CD-ROM build; "4 MB" build: 3B59h
%endif
CFGLEN    equ VARS
CFGBUF    equ VARS+4
SFXCARD   equ VARS+8
MUSICCARD equ VARS+0x12
SBMASK    equ (1<<5)|(1<<6)|(1<<7)|(1<<8)|(1<<13)|(1<<14)|(1<<15)|(1<<17)

sfx:    push esi
        mov al,'S'
        mov ecx,[CFGLEN]
        mov edi,[CFGBUF]
.f:     repne scasb
        jnz remap
        cmp word [es:edi],'FX'
        jnz .f
        add edi,2
        mov esi,SFXCARD
        jmp short line

music:  push esi
        mov al,'M'
        mov ecx,[CFGLEN]
        mov edi,[CFGBUF]
.f:     repne scasb
        jnz remap
        cmp dword [es:edi],'USIC'
        jnz .f
        add edi,4
        mov esi,MUSICCARD

line:   call NUM                        ; card
        jc remap
        mov [esi],ax
        call HEX                        ; port
        jc remap
        mov [esi+2],ax
        call NUM                        ; IRQ
        jc remap
        mov [esi+4],ax
        call NUM                        ; DMA
        jc remap
        mov [esi+6],ax
        call NUM                        ; DRQ
        jc remap
        mov [esi+8],ax

remap:  movzx eax,word [SFXCARD]
        cmp eax,31
        ja .x
        mov edx,SBMASK
        bt edx,eax
        jnc .x
        mov word [SFXCARD],13
        or eax,-1                       ; MUSIC -1,-1,-1,-1,-1
        mov [MUSICCARD],eax
        mov [MUSICCARD+4],eax
        mov [MUSICCARD+8],ax
.x:     pop esi
        ret
end:
        times 0xBF - (end - sfx) db 0xCC
