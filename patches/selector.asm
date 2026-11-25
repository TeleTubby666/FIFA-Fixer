; Floppy build: the routine that decides whether an effect is played by the
; FM driver or as a sample (file offset 56CD4h .. 56D22h). Rewritten with the
; same result, plus: with the mixer driver (SFX card 13) always as a sample,
; so that the FM card keeps the music.
; Result as before: "above" (CF=0, ZF=0) = FM, otherwise sample.
        bits 32
%ifndef ORG
%define ORG 0x56CD4
%define SFXCARD 0x3AE1
%define MUSICCARD 0x3AEB
%endif
        org ORG
start:  push eax
        cmp word [SFXCARD],13
        je .sample
        mov ax,[MUSICCARD]
        cmp ax,3
        jb .sample
        cmp ax,10
        jbe .fm
        cmp ax,14
        jb .sample
.fm:    cmp ax,16
        je .fm2
        cmp ax,15
        je .fm2
        cmp ax,18
        je .fm2
        or esp,esp                      ; ZF=0, CF=0: FM
        pop eax
        ret
.fm2:   or esp,esp                      ; FM, CF=1 (as the original)
        stc
        pop eax
        ret
.sample:
        xor eax,eax                     ; ZF=1: sample
        pop eax
        ret
end:
        times 79 - (end - start) db 0xCC
