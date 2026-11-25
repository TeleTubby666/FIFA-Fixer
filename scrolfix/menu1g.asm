; the menu / statistics scroll routine of all three builds (CD 8527h, 4 MB 8142h,
; floppy 7D3Eh): start address in the display period, pel panning in the
; vertical retrace, like the match routine of the CD-ROM build
        bits 32
        org ORG
s:      mov dx,0x3DA
.a:     in al,dx
        test al,8
        jnz .a
        mov ecx,ebx
        shr ebx,2
        add bx,[dword ADDR]
        xor word [dword ADDR],0x8000
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
e:      times 67 - (e - s) nop
