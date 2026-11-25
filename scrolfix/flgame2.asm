; floppy build, file 51AB0h..51AE3h: start address in the display period,
; pel panning in the vertical retrace (instead of both in the retrace)
        bits 32
        org 0x51AB0
s:      mov dx,0x3DA
.a:     in al,dx
        test al,8
        jnz .a
        cli
        mov ecx,ebx
        shr ebx,2
        mov dl,0xD4
        mov ah,bh
        mov al,0x0C
        out dx,ax
        mov ah,bl
        mov al,0x0D
        out dx,ax
        mov dl,0xDA
.b:     in al,dx
        test al,8
        jz .b
        mov dl,0xC0
        mov al,0x33
        out dx,al
        lea eax,[ecx+ecx]
        and al,6
        out dx,al
e:      times 52 - (e - s) nop
