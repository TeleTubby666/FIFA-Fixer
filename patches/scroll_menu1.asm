; CD-ROM build, file 8527h..8569h (in the routine at 8520h): start address in
; the display period, pel panning in the vertical retrace (as the game does it
; in the match), instead of pel panning first
        bits 32
        org 0x8527
s:      mov dx,0x3DA
.a:     in al,dx
        test al,8
        jnz .a
        mov ecx,ebx                     ; pel panning comes from the low bits
        shr ebx,2
        add bx,[dword 0x4828]
        xor word [dword 0x4828],0x8000
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
e:      times 0x856A - 0x8527 - (e - s) nop
