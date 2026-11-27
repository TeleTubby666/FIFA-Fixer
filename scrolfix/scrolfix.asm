; SCROLFIX.COM - scroll test for FIFA International Soccer (DOS)
;
; Makes two test copies of FIFA.EXE with a changed timing of the hardware
; scrolling (the "shaking" when the picture scrolls fast):
;   FIFA_S1.EXE  menus / statistics screen like the match (and in the floppy
;                build also the match): start address in the display period,
;                pel panning in the vertical retrace
;   FIFA_S2.EXE  as S1, but in the match both values in the vertical retrace
;                (for cards that take the start address at once)
; FIFA.EXE itself is not changed. Works on the floppy, CD-ROM and "4 MB"
; builds, with or without other patches (only the scroll code is checked).
;
; Part of FIFA-Fixer.   nasm -f bin -o SCROLFIX.COM scrolfix.asm

        bits 16
        cpu 386
        org 0x100

start:  cld
        mov dx,msg_hello
        call print
        ; file name from the command line, default FIFA.EXE
        mov si,0x81
        mov di,fname
.sk:    lodsb
        cmp al,' '
        je .sk
        cmp al,9
        je .sk
.cp:    cmp al,13
        je .end
        cmp al,' '
        je .end
        stosb
        lodsb
        jmp .cp
.end:   cmp di,fname
        jne .named
        mov si,def_name
.dn:    lodsb
        stosb
        test al,al
        jnz .dn
        jmp short .open
.named: xor al,al
        stosb

.open:  mov ax,0x3D00
        mov dx,fname
        int 0x21
        jnc .opened
        mov dx,msg_noopen
        jmp fail
.opened:
        mov [hin],ax
        mov bx,ax
        mov ax,0x4202                   ; size
        xor cx,cx
        xor dx,dx
        int 0x21
        mov [fsize],ax
        mov [fsize+2],dx

        ; find the build
        mov si,builds
.find:  mov eax,[si]
        test eax,eax
        jz .unknown
        cmp eax,[fsize]
        je .found
        add si,4
        mov cx,[si+2]                   ; number of regions
        add si,4
.skipr: mov ax,[si+4]                   ; length
        shl ax,1
        add ax,7
        add si,ax
        loop .skipr
        jmp .find
.unknown:
        mov dx,msg_unknown
        jmp fail
.found: push si
        mov dx,[si+4]
        call printz
        pop si
        mov dx,msg_crlf
        call print
        mov cx,[si+6]
        add si,8
        mov [regions],si
        mov [nreg],cx

        ; check every region against the original bytes
        push si
        push cx
.chk:   push cx
        mov dx,[si]
        mov cx,[si+2]
        mov bx,[hin]
        mov ax,0x4200
        int 0x21
        mov cx,[si+4]
        mov dx,buf
        mov ah,0x3F
        int 0x21
        jc .bad
        cmp ax,cx
        jne .bad
        push si
        lea si,[si+7]
        mov di,buf
        repe cmpsb
        pop si
        jne .bad
        mov ax,[si+4]
        shl ax,1
        add ax,7
        add si,ax
        pop cx
        loop .chk
        pop cx
        pop si
        jmp short .make
.bad:   mov dx,msg_changed
        jmp fail

.make:  mov byte [variant],1
        mov dx,out1
        call make_copy
        mov byte [variant],2
        mov dx,out2
        call make_copy
        mov dx,msg_done
        call print
        mov ax,0x4C00
        int 0x21

; make_copy: dx = name of the copy; copies the input file and applies the
; regions of [variant]
make_copy:
        mov [outname],dx
        mov ah,0x3C
        xor cx,cx
        int 0x21
        jnc .c1
        mov dx,msg_nowrite
        jmp fail
.c1:    mov [hout],ax
        mov bx,[hin]
        mov ax,0x4200
        xor cx,cx
        xor dx,dx
        int 0x21
.copy:  mov bx,[hin]
        mov ah,0x3F
        mov cx,BUFSZ
        mov dx,buf
        int 0x21
        jc .err
        test ax,ax
        jz .copied
        mov cx,ax
        mov bx,[hout]
        mov ah,0x40
        int 0x21
        jc .err
        cmp ax,cx
        jne .err
        jmp .copy
.copied:
        mov si,[regions]
        mov cx,[nreg]
.reg:   push cx
        mov al,[si+6]
        test al,[variant]
        jz .next
        mov cx,[si+4]                   ; original ^ difference
        push si
        lea di,[si+7]
        mov bx,di
        add bx,cx
        mov di,buf
        lea si,[si+7]
.x:     lodsb
        xor al,[bx]
        inc bx
        stosb
        loop .x
        pop si
        mov dx,[si]
        mov cx,[si+2]
        mov bx,[hout]
        mov ax,0x4200
        int 0x21
        mov cx,[si+4]
        mov dx,buf
        mov ah,0x40
        int 0x21
        jc .err
.next:  mov ax,[si+4]
        shl ax,1
        add ax,7
        add si,ax
        pop cx
        loop .reg
        mov bx,[hout]
        mov ah,0x3E
        int 0x21
        jc .err
        mov dx,msg_written
        call print
        mov dx,[outname]
        call printz
        mov dx,msg_crlf
        call print
        ret
.err:   mov dx,msg_nowrite
        jmp fail

fail:   call print
        mov ax,0x4C01
        int 0x21

print:  mov ah,9
        int 0x21
        ret

printz: mov si,dx                       ; zero-terminated string
.l:     lodsb
        test al,al
        jz .x
        mov dl,al
        mov ah,2
        int 0x21
        jmp .l
.x:     ret

BUFSZ   equ 0x4000

msg_hello:   db 'SCROLFIX - scroll test for FIFA International Soccer (FIFA-Fixer)',13,10,'$'
msg_crlf:    db 13,10,'$'
msg_noopen:  db 'Cannot open the file. Usage: SCROLFIX [FIFA.EXE]',13,10,'$'
msg_unknown: db 'Unknown file size: this FIFA.EXE is not one of the known builds',13,10
             db '(floppy, Spanish floppy, CD-ROM, 4 MB). Please report its size.',13,10,'$'
msg_changed: db 'The scroll code of this file is not the original one, nothing was written.',13,10,'$'
msg_nowrite: db 'Cannot write the test copy.',13,10,'$'
msg_written: db 'written: $'
msg_done:    db 'FIFA.EXE was not changed. To test, keep a copy of FIFA.EXE and copy',13,10
             db 'FIFA_S1.EXE or FIFA_S2.EXE over FIFA.EXE (the game only starts as FIFA.EXE).',13,10,'$'
def_name:    db 'FIFA.EXE',0
out1:        db 'FIFA_S1.EXE',0
out2:        db 'FIFA_S2.EXE',0

%include "scrtab.inc"

        section .bss
hin:     resw 1
hout:    resw 1
fsize:   resd 1
regions: resw 1
nreg:    resw 1
variant: resb 1
outname: resw 1
fname:   resb 128
buf:     resb BUFSZ
