; CUST_DIG.ADV - 8-channel software-mixing digital sound driver for
; Sound Blaster compatible cards (8-bit mono, 22 kHz).
;
; Written for the sound manager of FIFA International Soccer (CD-ROM
; build), which talks to its digital driver through the AIL 2 driver
; interface, extended by a channel number as last argument (the way the
; game's GF1DIGI.ADV does it).  The game supports eight simultaneous
; samples, but only on a Gravis UltraSound; this driver mixes the eight
; channels in software so that any Sound Blaster can play them.
;
; Part of FIFA-Fixer.  Assemble with:  nasm -f bin -o CUST_DIG.ADV sbmix.asm

        bits 16
        cpu 386
        org 0

OUT_TC  equ 211                         ; 1000000/(256-211) = 22222 Hz
STEPK   equ (256-OUT_TC) << 16          ; 16.16 step = STEPK / (256 - tc)
HALF    equ 512                         ; samples per interrupt
BUF     equ 2*HALF
NCH     equ 8
MAXHOP  equ 16                          ; block headers parsed per call

DAC_STOPPED equ 0
DAC_PAUSED  equ 1
DAC_PLAYING equ 2
DAC_DONE    equ 3

; ---------------------------------------------------------------- header
        dw fn_table
        db 'Copyright (C) 2026 FIFA-Fixer. Software mixer for the AIL 2 '
        db 'driver interface.',0x1a

fn_table:
        dw 100, describe
        dw 101, detect
        dw 102, init
        dw 104, shutdown
        dw 123, play_voc
        dw 124, status
        dw 125, start
        dw 126, stop
        dw 127, pause
        dw 128, resume
        dw 129, set_vol
        dw 130, get_vol
        dw 131, set_pan
        dw 132, get_pan
        dw -1

desc:   dw 200                          ; minimum API version
        dw 2                            ; driver type: digital
        db 'VOC',0
.names: dw dev_names, 0                 ; far pointer, segment set at run time
        dw 0x220, 5, 1, -1              ; default I/O, IRQ, DMA, DRQ
        dw -1                           ; no timer service
        dw 0

dev_names:
        db 'Sound Blaster, 8-channel software mixer (FIFA-Fixer)',0,0

; ------------------------------------------------------------------ data
        align 2
io:         dw 0x220
irq:        dw 5
dma:        dw 1
active:     dw 0
autoinit:   db 0
picmask:    db 0
oldvec:     dd 0
buf_off:    dw 0                        ; offset of the DMA buffer in CS
retax:      dw 0
retdx:      dw 0
n_playing:  dw 0
silent:     db 0                        ; both halves already hold silence
            db 0

dma_page_port: db 0x87, 0x83, 0x81, 0x82

            align 4
ch_stat:    times NCH dw 0
ch_vol:     times NCH dw 127
ch_pan:     times NCH dw 64
ch_loaded:  times NCH dw 0
ch_frac:    times NCH dw 0
ch_repcnt:  times NCH dw 0
ch_ptr:     times NCH dd 0              ; linear address of the next sample
ch_rem:     times NCH dd 0              ; bytes left in the current block
ch_step:    times NCH dd 0              ; 16.16 input samples per output sample
ch_blk:     times NCH dd 0              ; linear address of the next block
ch_rep:     times NCH dd 0              ; block to repeat from (0 = none)
ch_ext:     times NCH dd 0              ; step from an extended block (0 = none)

cur_step_f: dw 0
cur_step_i: dw 0
cur_start:  dw 0
cur_limit:  dw 0
cur_end:    dw 0
cur_vol:    dw 0

; ---------------------------------------------------------------- macros
; Every entry point is a far C function: [bp+6] = driver handle, then the
; arguments.  All registers are preserved, the result is passed in ax (dx).
%macro PROLOG 0
        push bp
        mov bp,sp
        pushf
        cli
        pushad
        push ds
        push es
        mov ax,cs
        mov ds,ax
        cld
        mov word [retax],0
%endmacro

%macro EPILOG 0
        pop es
        pop ds
        popad
        mov ax,[cs:retax]
        popf
        pop bp
        retf
%endmacro

; ----------------------------------------------------------- DSP helpers
dsp_write:                              ; al = byte
        push cx
        push dx
        push ax
        mov dx,[io]
        add dx,0xC
        mov cx,0xFFFF
.w:     in al,dx
        test al,0x80
        jz .ok
        loop .w
.ok:    pop ax
        out dx,al
        pop dx
        pop cx
        ret

dsp_read:                               ; -> al, CF=1 on timeout
        push cx
        push dx
        mov dx,[io]
        add dx,0xE
        mov cx,0xFFFF
.w:     in al,dx
        test al,0x80
        jnz .ok
        loop .w
        stc
        jmp short .x
.ok:    sub dx,4
        in al,dx
        clc
.x:     pop dx
        pop cx
        ret

dsp_reset:                              ; -> ZF=1 if a DSP answered
        push cx
        push dx
        mov dx,[io]
        add dx,6
        mov al,1
        out dx,al
        mov cx,64
.d:     in al,dx
        loop .d
        xor al,al
        out dx,al
        mov cx,4
.t:     push cx
        call dsp_read
        pop cx
        jc .next
        cmp al,0xAA
        je .x
.next:  loop .t
        or al,1                         ; ZF=0
.x:     pop dx
        pop cx
        ret

; -------------------------------------------------------------- describe
describe:
        PROLOG
        mov [desc.names+2],cs
        mov word [retax],desc
        pop es
        pop ds
        popad
        mov ax,[cs:retax]
        mov dx,cs
        popf
        pop bp
        retf

; ---------------------------------------------------------------- detect
detect:                                 ; (drv, io, irq, dma, drq)
        PROLOG
        mov ax,[io]
        push ax
        mov ax,[bp+8]
        mov [io],ax
        call dsp_reset
        pop ax
        mov [io],ax
        jnz .no
        mov word [retax],1
.no:    EPILOG

; ------------------------------------------------------------------ init
init:                                   ; (drv, io, irq, dma, drq)
        PROLOG
        cmp word [active],0
        jne near .x
        mov ax,[bp+8]
        mov [io],ax
        mov ax,[bp+10]
        mov [irq],ax
        mov ax,[bp+12]
        and ax,3
        mov [dma],ax

        call dsp_reset
        jnz near .x
        mov al,0xE1                     ; DSP version
        call dsp_write
        call dsp_read
        mov ah,al
        call dsp_read
        cmp ah,2
        setae byte [autoinit]

        ; all channels idle
        push ds
        pop es
        mov di,ch_stat
        mov cx,NCH
        xor ax,ax
        rep stosw
        mov di,ch_loaded
        mov cx,NCH
        rep stosw

        ; place the DMA buffer so that it does not cross a 64K page
        xor eax,eax
        mov ax,cs
        shl eax,4
        add eax,dmabuf
        mov ebx,eax
        and ebx,0xFFFF
        add ebx,BUF
        cmp ebx,0x10000
        jbe .fits
        or eax,0xFFFF
        inc eax
.fits:  mov esi,eax                     ; linear address of the buffer
        xor ebx,ebx
        mov bx,cs
        shl ebx,4
        sub eax,ebx
        mov [buf_off],ax
        mov di,ax
        mov cx,BUF
        mov al,0x80
        rep stosb
        mov byte [silent],1

        ; interrupt vector and PIC mask
        mov bx,[irq]
        mov cx,bx
        cmp bx,8
        jb .lowv
        add bx,0x70-8-8
.lowv:  add bx,8
        shl bx,2
        xor ax,ax
        mov es,ax
        mov eax,[es:bx]
        mov [oldvec],eax
        mov word [es:bx],isr
        mov [es:bx+2],cs
        mov dx,0x21
        cmp cl,8
        jb .lowm
        mov dx,0xA1
        sub cl,8
.lowm:  mov ah,1
        shl ah,cl
        in al,dx
        mov [picmask],al
        and [picmask],ah                ; remember the old state of our bit
        not ah
        and al,ah
        out dx,al
        cmp dx,0xA1
        jne .picok
        in al,0x21
        and al,0xFB                     ; cascade
        out 0x21,al
.picok:
        ; DMA controller: single mode, auto-initialise, playback
        mov bx,[dma]
        mov al,bl
        or al,4
        out 0x0A,al
        xor al,al
        out 0x0C,al
        mov al,bl
        or al,0x58
        out 0x0B,al
        mov dx,bx
        shl dx,1
        mov eax,esi
        out dx,al
        mov al,ah
        out dx,al
        shr eax,16
        push dx
        xor dh,dh
        mov dl,[dma_page_port+bx]
        out dx,al
        pop dx
        inc dx
        mov al,(BUF-1) & 0xFF
        out dx,al
        mov al,(BUF-1) >> 8
        out dx,al
        mov al,bl
        out 0x0A,al

        mov al,0xD1                     ; speaker on
        call dsp_write
        mov al,0x40                     ; time constant
        call dsp_write
        mov al,OUT_TC
        call dsp_write
        mov word [active],1
        cmp byte [autoinit],0
        je .single
        mov al,0x48                     ; block size
        call dsp_write
        mov al,(HALF-1) & 0xFF
        call dsp_write
        mov al,(HALF-1) >> 8
        call dsp_write
        mov al,0x1C                     ; 8-bit auto-initialised output
        call dsp_write
        jmp short .ok
.single:
        call start_single
.ok:    mov word [retax],1
.x:     EPILOG

start_single:                           ; one block, for DSP 1.xx
        mov al,0x14
        call dsp_write
        mov al,(HALF-1) & 0xFF
        call dsp_write
        mov al,(HALF-1) >> 8
        call dsp_write
        ret

; -------------------------------------------------------------- shutdown
shutdown:
        PROLOG
        cmp word [active],0
        je .x
        mov word [active],0
        mov al,0xD0                     ; halt
        call dsp_write
        mov al,0xDA                     ; leave auto-initialise mode
        call dsp_write
        mov al,0xD3                     ; speaker off
        call dsp_write
        call dsp_reset
        mov al,[dma]
        or al,4
        out 0x0A,al
        ; PIC mask and vector back
        mov bx,[irq]
        mov cx,bx
        mov dx,0x21
        cmp cl,8
        jb .lowm
        mov dx,0xA1
.lowm:  in al,dx
        or al,[picmask]
        out dx,al
        cmp bx,8
        jb .lowv
        add bx,0x70-8-8
.lowv:  add bx,8
        shl bx,2
        xor ax,ax
        mov es,ax
        mov eax,[oldvec]
        mov [es:bx],eax
.x:     EPILOG

; ------------------------------------------------------ channel functions
; get_ch: bx = 2*channel from the word at [bp+si], CF=1 if out of range
get_ch: mov bx,[bp+si]
        cmp bx,NCH
        cmc
        jc .x
        shl bx,1
        clc
.x:     ret

play_voc:                               ; (drv, voc far, marker, ch)
        PROLOG
        mov si,14
        call get_ch
        jc .x
        mov word [ch_stat+bx],DAC_STOPPED
        mov word [ch_loaded+bx],0
        les di,[bp+8]
        cmp word [es:di],0x7243         ; "Cr"
        jne .x
        xor eax,eax
        mov ax,es
        shl eax,4
        movzx edx,di
        add eax,edx
        movzx edx,word [es:di+0x14]     ; offset of the first block
        add eax,edx
        movzx ebx,bx
        xor edx,edx
        mov [ch_blk+ebx*2],eax
        mov [ch_rem+ebx*2],edx
        mov [ch_rep+ebx*2],edx
        mov [ch_ext+ebx*2],edx
        mov [ch_frac+bx],dx
        mov [ch_repcnt+bx],dx
        mov word [ch_loaded+bx],1
.x:     EPILOG

start:                                  ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        cmp word [ch_loaded+bx],0
        je .x
        cmp word [ch_stat+bx],DAC_PLAYING
        je .x
        mov word [ch_stat+bx],DAC_PLAYING
.x:     EPILOG

stop:                                   ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        mov word [ch_stat+bx],DAC_STOPPED
        mov word [ch_loaded+bx],0
.x:     EPILOG

pause:                                  ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        cmp word [ch_stat+bx],DAC_PLAYING
        jne .x
        mov word [ch_stat+bx],DAC_PAUSED
.x:     EPILOG

resume:                                 ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        cmp word [ch_stat+bx],DAC_PAUSED
        jne .x
        mov word [ch_stat+bx],DAC_PLAYING
.x:     EPILOG

status:                                 ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_stat+bx]
        mov [retax],ax
.x:     EPILOG

set_vol:                                ; (drv, volume, ch)
        PROLOG
        mov si,10
        call get_ch
        jc .x
        mov ax,[bp+8]
        cmp ax,127
        jbe .ok
        mov ax,127
.ok:    mov [ch_vol+bx],ax
.x:     EPILOG

get_vol:                                ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_vol+bx]
        mov [retax],ax
.x:     EPILOG

set_pan:                                ; (drv, pan, ch)
        PROLOG
        mov si,10
        call get_ch
        jc .x
        mov ax,[bp+8]
        mov [ch_pan+bx],ax
.x:     EPILOG

get_pan:                                ; (drv, ch)
        PROLOG
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_pan+bx]
        mov [retax],ax
.x:     EPILOG

; ------------------------------------------------------------- interrupt
isr:    push ax
        push dx
        push ds
        mov ax,cs
        mov ds,ax
        mov dx,[io]
        add dx,0xE
        in al,dx                        ; acknowledge
        cmp word [active],0
        je .eoi
        cmp byte [autoinit],0
        jne .mix
        call start_single
.mix:   pushad
        push es
        cld
        call mix_half
        pop es
        popad
.eoi:   mov al,0x20
        cmp word [irq],8
        jb .low
        out 0xA0,al
.low:   out 0x20,al
        pop ds
        pop dx
        pop ax
        iret

; ----------------------------------------------------------------- mixer
mix_half:
        ; the half that is not being played right now gets the new data
        xor al,al
        out 0x0C,al
        mov dx,[dma]
        shl dx,1
        inc dx
        in al,dx
        mov ah,al
        in al,dx
        xchg al,ah                      ; ax = bytes left - 1
        mov bx,BUF-1
        sub bx,ax
        and bx,BUF-1                    ; play position
        mov di,[buf_off]
        cmp bx,HALF
        jae .first
        add di,HALF
.first: push di

        ; any channel playing?
        xor bx,bx
        xor ax,ax
.cnt:   cmp word [ch_stat+bx],DAC_PLAYING
        jne .c1
        inc ax
.c1:    add bx,2
        cmp bx,2*NCH
        jb .cnt
        mov [n_playing],ax
        test ax,ax
        jnz .work
        pop di
        cmp byte [silent],2
        jae .ret
        inc byte [silent]
        push ds
        pop es
        mov cx,HALF/2
        mov ax,0x8080
        rep stosw
.ret:   ret

.work:  mov byte [silent],0
        push ds
        pop es
        mov di,accbuf
        mov cx,HALF
        xor ax,ax
        rep stosw

        xor bx,bx
.chan:  cmp word [ch_stat+bx],DAC_PLAYING
        jne .next
        push bx
        call mix_channel
        pop bx
.next:  add bx,2
        cmp bx,2*NCH
        jb .chan

        ; accumulator -> unsigned 8 bit with clipping
        pop di
        push ds
        pop es
        mov si,accbuf
        mov cx,HALF
.out:   lodsw
        sar ax,3
        cmp ax,511
        jle .o1
        mov ax,511
.o1:    cmp ax,-512
        jge .o2
        mov ax,-512
.o2:    mov bx,ax
        mov al,[softclip+512+bx]
        stosb
        loop .out
        ret

; mix one channel into accbuf.  bx = 2*channel
mix_channel:
        movzx ebx,bx
        mov di,accbuf
        mov cx,HALF
.again: mov eax,[ch_rem+ebx*2]
        test eax,eax
        jnz .have
        call next_block
        jnc .again
        ret                             ; sample finished

.have:  cmp eax,0xF000
        jbe .l1
        mov eax,0xF000
.l1:    mov edx,[ch_ptr+ebx*2]
        mov si,dx
        and si,15
        shr edx,4
        mov es,dx
        mov [cur_start],si
        add ax,si
        mov [cur_limit],ax
        mov word [cur_end],0xFFFF
        mov eax,[ch_rem+ebx*2]
        cmp eax,0xF000
        ja .l2
        add ax,si
        mov [cur_end],ax                ; the block ends in this part
.l2:
        mov eax,[ch_step+ebx*2]
        mov [cur_step_f],ax
        shr eax,16
        mov [cur_step_i],ax
        mov dx,[ch_frac+bx]
        mov ax,[ch_vol+bx]
        mov [cur_vol],ax
        push bx
.loop:  mov ax,[es:si]                  ; al = this sample, ah = the next one
        lea bx,[si+1]
        cmp bx,[cur_end]
        jb .ip
        mov ah,al                       ; last sample of the block
.ip:    movzx bx,ah
        xor ah,ah
        sub bx,ax
        movzx bp,dh
        shr bp,1                        ; fraction, 7 bits
        imul bx,bp
        sar bx,7
        add ax,bx                       ; linear interpolation
        sub ax,0x80
        imul ax,[cur_vol]
        sar ax,4
        add [di],ax
        add di,2
        add dx,[cur_step_f]
        adc si,[cur_step_i]
        cmp si,[cur_limit]
        jae .part
        dec cx
        jnz .loop
        pop bx
        ; the half is full
        mov [ch_frac+bx],dx
        sub si,[cur_start]
        movzx eax,si
        add [ch_ptr+ebx*2],eax
        sub [ch_rem+ebx*2],eax
        ret
.part:  ; this part of the block is used up
        pop bx
        mov [ch_frac+bx],dx
        sub si,[cur_start]
        movzx eax,si
        cmp eax,[ch_rem+ebx*2]
        jbe .p1
        mov eax,[ch_rem+ebx*2]
.p1:    add [ch_ptr+ebx*2],eax
        sub [ch_rem+ebx*2],eax
        dec cx
        jnz .again
        ret

; next_block: read block headers until one with sample data is found.
; ebx = 2*channel.  CF=1: the sample is finished (or broken), channel stopped.
next_block:
        push cx
        push di
        mov cx,MAXHOP
.hop:   mov edx,[ch_blk+ebx*2]
        mov si,dx
        and si,15
        mov eax,edx
        shr eax,4
        mov es,ax
        mov al,[es:si]
        test al,al
        jz near .end
        mov edi,[es:si+1]
        and edi,0xFFFFFF                ; length of the block
        lea ebp,[edx+edi+4]             ; next block
        mov [ch_blk+ebx*2],ebp
        cmp al,1
        je .data
        cmp al,2
        je .cont
        cmp al,6
        je .rep
        cmp al,7
        je .endrep
        cmp al,8
        je near .ext
        cmp al,9
        je near .new
.skip:  dec cx
        jnz .hop
        jmp .end

.data:  cmp edi,2
        jbe .skip
        cmp byte [es:si+5],0            ; only unpacked 8-bit data
        jne .skip
        mov eax,[ch_ext+ebx*2]
        test eax,eax
        jnz .d1
        movzx ebp,byte [es:si+4]
        neg bp
        add bp,256                      ; 256 - time constant
        mov eax,STEPK
        push edx
        xor edx,edx
        div ebp
        pop edx
.d1:    mov dword [ch_ext+ebx*2],0
        mov [ch_step+ebx*2],eax
        add edx,6
        sub edi,2
.set:   mov [ch_ptr+ebx*2],edx
        mov [ch_rem+ebx*2],edi
        clc
        pop di
        pop cx
        ret

.cont:  test edi,edi
        jz .skip
        cmp dword [ch_step+ebx*2],0
        je .skip
        add edx,4
        jmp .set

.rep:   mov ax,[es:si+4]
        mov [ch_repcnt+bx],ax
        mov [ch_rep+ebx*2],ebp
        jmp .skip

.endrep:
        mov eax,[ch_rep+ebx*2]
        test eax,eax
        jz .skip
        cmp word [ch_repcnt+bx],0xFFFF
        je .back
        cmp word [ch_repcnt+bx],0
        je .norep
        dec word [ch_repcnt+bx]
.back:  mov [ch_blk+ebx*2],eax
        jmp .skip
.norep: mov dword [ch_rep+ebx*2],0
        jmp .skip

.ext:   movzx ebp,word [es:si+4]        ; 65536 - 256000000/rate
        neg ebp
        add ebp,65536
        jz .skip
        cmp byte [es:si+7],0
        je .e1
        shl ebp,1                       ; stereo flag: half the rate per channel
.e1:    mov eax,(256-OUT_TC) << 24      ; 256 * STEPK
        push edx
        xor edx,edx
        div ebp
        pop edx
        mov [ch_ext+ebx*2],eax
        jmp .skip

.new:   cmp edi,12
        jbe .skip
        cmp word [es:si+8],0x0108       ; 8 bits, one channel
        jne near .skip
        cmp word [es:si+10],0           ; PCM
        jne near .skip
        mov eax,[es:si+4]               ; sample rate
        cmp eax,0xFFFF
        ja near .skip
        push edx
        shl eax,16
        xor edx,edx
        mov ebp,1000000/(256-OUT_TC)
        div ebp
        pop edx
        mov [ch_step+ebx*2],eax
        add edx,16
        sub edi,12
        jmp .set

.end:   mov word [ch_stat+bx],DAC_DONE
        mov word [ch_loaded+bx],0
        stc
        pop di
        pop cx
        ret

; ---------------------------------------------------------------- tables
softclip:
%include "softclip.inc"

; --------------------------------------------------------------- buffers
        align 2
accbuf: times HALF dw 0
dmabuf: times 2*BUF db 0x80
