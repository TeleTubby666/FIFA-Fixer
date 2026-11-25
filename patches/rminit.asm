; Digital init of the real-mode sound module (44 bytes): card 13 (the mixer
; driver CUST_DIG.ADV) gets the eight-channel flag 1, card 12 (Gravis
; UltraSound) FFh as before, all other cards 0.
;   CD-ROM / "4 MB" build: module offset 22D4h (default defines)
;   floppy build:          module offset 22FDh, -DORG=0x22FD -DF1=0x3079
;                          -DF2=0x3182 -DPORT=0x310 -DVOL=0x302 -DFLAG=0x2E2
        bits 16
%ifndef ORG
%define ORG 0x22D4
%define F1 0x3038
%define F2 0x3141
%define PORT 0x30A
%define VOL 0x2FC
%define FLAG 0x2DC
%endif
        org ORG
        cmp bx,13
        mov al,1
        jz .set
        cmp bx,12
        mov al,0
        jnz .set
        mov si,0xE
        push bx
        call F1
        call F2
        mov [PORT],bx
        db 0x8B,0xD3                    ; mov dx,bx (as the game encodes it)
        mov al,0xA
        out dx,al
        mov byte [VOL],0xA
        pop bx
        mov al,0xFF
.set:   mov [FLAG],al
