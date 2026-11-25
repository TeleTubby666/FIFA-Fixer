; CD-ROM build, file 917Bh..91A6h (routine at 917Ah): the same order
        bits 32
        org 0x917B
s:      lea ecx,[eax+eax]
        mov ebx,eax
        shr ebx,2
        mov dx,0x3D4
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
        mov al,cl
        and al,6
        out dx,al
e:      times 0x91A7 - 0x917B - (e - s) nop
