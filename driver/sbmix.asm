; CUST_DIG.ADV - 8-channel software-mixing digital sound driver for
; Sound Blaster compatible cards (22 kHz mono; 16 bit on a Sound Blaster 16
; or AWE32 with a 16-bit DMA channel, 8 bit on all other cards).
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
OUT_HZ  equ 22222                       ; the same rate for DSP 4.xx
KNEE16  equ 88*32                       ; 16-bit output: linear up to here
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
bits16:     db 0                        ; 1: 16-bit output (DSP 4.xx)
hdma:       dw 0                        ; 16-bit DMA channel (5-7)
ackport:    dw 0xE                      ; io offset that acknowledges the IRQ
cntport:    dw 3                        ; DMA count register of our channel
ffport:     dw 0x0C                     ; DMA flip-flop reset
active:     dw 0
autoinit:   db 0
picmask:    db 0
oldvec:     dd 0
buf_off:    dw 0                        ; offset of the DMA buffer in CS
retax:      dw 0
retdx:      dw 0
n_playing:  dw 0
silent:     db 0                        ; both halves already hold silence
in_mix:     db 0                        ; the interrupt handler is mixing

dma_page_port: db 0x87, 0x83, 0x81, 0x82
dma16_page:    db 0x8B, 0x89, 0x8A      ; channels 5, 6, 7

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
%macro PROLOG 0-3 0,0,0
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
%ifdef LOG
        mov byte [lg_fn],%1
        mov word [lg_ch],0xFF
        mov word [lg_arg],0
%if %2
        mov ax,[bp+%2]
        mov [lg_ch],ax
%endif
%if %3
        mov ax,[bp+%3]
        mov [lg_arg],ax
%endif
%endif
%endmacro

%macro EPILOG 0
%ifdef LOG
        mov ax,[retax]
        mov [lg_res],ax
        call do_log
%endif
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
        PROLOG 100,0,0
        mov [desc.names+2],cs
        mov word [retax],desc
%ifdef LOG
        mov word [lg_res],desc
        call do_log
%endif
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
        PROLOG 101,0,8
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
        PROLOG 102,0,8
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
        mov byte [bits16],0
        mov word [ackport],0xE
        mov word [ffport],0x0C
        mov bx,[dma]
        shl bx,1
        inc bx
        mov [cntport],bx
        cmp ah,4
        jb .no16
        ; Sound Blaster 16 / AWE32: the 16-bit DMA channel from the mixer
        ; chip (register 81h: bit 5-7 = channel 5-7)
        mov dx,[io]
        add dx,4
        mov al,0x81
        out dx,al
        inc dx
        in al,dx
        mov bx,5
        test al,0x20
        jnz .h16
        inc bx
        test al,0x40
        jnz .h16
        inc bx
        test al,0x80
        jz .no16
.h16:   mov [hdma],bx
        mov byte [bits16],1
        mov word [ackport],0xF
        mov word [ffport],0xD8
        sub bx,4
        shl bx,2
        add bx,0xC2
        mov [cntport],bx
.no16:

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
        add ebx,2*BUF                   ; (16-bit: the whole buffer)
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
        cmp byte [bits16],0
        je .fill
        xor al,al
        shl cx,1
.fill:  rep stosb
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
        cmp byte [bits16],0
        jne near .start16
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

.start16:
        ; second DMA controller, addresses and counts in words
        mov bx,[hdma]
        sub bx,4
        mov al,bl
        or al,4
        out 0xD4,al
        xor al,al
        out 0xD8,al
        mov al,bl
        or al,0x58
        out 0xD6,al
        mov dx,bx
        shl dx,2
        add dx,0xC0
        mov eax,esi
        shr eax,1
        out dx,al
        mov al,ah
        out dx,al
        mov eax,esi
        shr eax,16
        push dx
        xor dh,dh
        mov dl,[dma16_page+bx-1]
        out dx,al
        pop dx
        add dx,2
        mov al,(BUF-1) & 0xFF
        out dx,al
        mov al,(BUF-1) >> 8
        out dx,al
        mov al,bl
        out 0xD4,al

        mov al,0xD1                     ; speaker on
        call dsp_write
        mov al,0x41                     ; output rate in Hz
        call dsp_write
        mov al,OUT_HZ >> 8
        call dsp_write
        mov al,OUT_HZ & 0xFF
        call dsp_write
        mov word [active],1
        mov al,0xB6                     ; 16-bit DAC, auto-initialised, FIFO
        call dsp_write
        mov al,0x10                     ; mono, signed
        call dsp_write
        mov al,(HALF-1) & 0xFF
        call dsp_write
        mov al,(HALF-1) >> 8
        call dsp_write
        jmp .ok

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
        PROLOG 104,0,0
        cmp word [active],0
        je .x
        mov word [active],0
        cmp byte [bits16],0
        jne .sd16
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
        jmp short .sdok
.sd16:  mov al,0xD5                     ; halt 16-bit DMA
        call dsp_write
        mov al,0xD9                     ; leave 16-bit auto-initialise mode
        call dsp_write
        mov al,0xD3
        call dsp_write
        call dsp_reset
        mov al,[hdma]
        out 0xD4,al                     ; (channel - 4) | 4 = channel
.sdok:
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
%ifdef LOG
        call write_log
%endif
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
        PROLOG 123,14,10
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
%ifdef LOG
        ; extra entry: the length of the sample (first data block, in 16-byte
        ; units), so that the log tells which sample was played
        push bx
        mov ax,bx
        shr ax,1
        mov [lg_ch],ax
        mov bx,di
        add bx,[es:di+0x14]
        cmp byte [es:bx],6              ; skip a repeat block
        jne .len
        add bx,6
.len:   mov eax,[es:bx+1]
        and eax,0xFFFFFF
        shr eax,4
        mov [lg_arg],ax
        mov byte [lg_fn],0xEF
        mov word [lg_res],0
        call do_log
        pop bx
        mov byte [lg_fn],123            ; the play entry itself follows in EPILOG
        mov ax,[bp+10]
        mov [lg_arg],ax
        mov ax,[bp+14]
        mov [lg_ch],ax
%endif
.x:     EPILOG

start:                                  ; (drv, ch)
        PROLOG 125,8,0
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
        PROLOG 126,8,0
        mov si,8
        call get_ch
        jc .x
        mov word [ch_stat+bx],DAC_STOPPED
        mov word [ch_loaded+bx],0
.x:     EPILOG

pause:                                  ; (drv, ch)
        PROLOG 127,8,0
        mov si,8
        call get_ch
        jc .x
        cmp word [ch_stat+bx],DAC_PLAYING
        jne .x
        mov word [ch_stat+bx],DAC_PAUSED
.x:     EPILOG

resume:                                 ; (drv, ch)
        PROLOG 128,8,0
        mov si,8
        call get_ch
        jc .x
        cmp word [ch_stat+bx],DAC_PAUSED
        jne .x
        mov word [ch_stat+bx],DAC_PLAYING
.x:     EPILOG

status:                                 ; (drv, ch)
        PROLOG 124,8,0
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_stat+bx]
        mov [retax],ax
.x:     EPILOG

set_vol:                                ; (drv, volume, ch)
        PROLOG 129,10,8
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
        PROLOG 130,8,0
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_vol+bx]
        mov [retax],ax
.x:     EPILOG

set_pan:                                ; (drv, pan, ch)
        PROLOG 131,10,8
        mov si,10
        call get_ch
        jc .x
        mov ax,[bp+8]
        mov [ch_pan+bx],ax
.x:     EPILOG

get_pan:                                ; (drv, ch)
        PROLOG 132,8,0
        mov si,8
        call get_ch
        jc .x
        mov ax,[ch_pan+bx]
        mov [retax],ax
.x:     EPILOG

%ifdef LOG
; ------------------------------------------------------------------- log
; Debug build only: every driver call is recorded in a ring buffer (repeated
; identical calls are counted, not stored again); shutdown writes MIXLOG.BIN.
LOGN    equ 512
LOGSZ   equ 14
STALL   equ 520                         ; interrupts, about 12 seconds
AFTER   equ 120                         ; entries kept after a stall
ARM     equ 300                         ; entries before stalls are looked for

lg_frz:  db 0                           ; 1: a stall was seen
         db 0
lg_after: dw 0
lg_lastcall: dw 0

lg_fn:   db 0
         db 0
lg_ch:   dw 0
lg_arg:  dw 0
lg_res:  dw 0
lg_idx:  dw 0
lg_last: dw 0                           ; offset of the last entry, 0 = none
lg_tot:  dd 0
isr_cnt: dd 0
lg_name: db 'MIXLOG.BIN',0

freeze: mov byte [lg_frz],1
        mov word [lg_after],AFTER
        ret

do_log: push ax
        push bx
        push dx
        push es
        cmp byte [lg_frz],0
        je .on
        cmp word [lg_after],0
        je .x
.on:    cmp byte [lg_fn],0xEE
        jae .noc
        mov ax,[isr_cnt]
        mov [lg_lastcall],ax
.noc:   mov dx,[isr_cnt]                ; time stamp = interrupt count
        mov bx,[lg_last]
        test bx,bx
        jz .new
        mov al,[lg_fn]
        cmp al,[bx+6]
        jne .new
        mov al,[lg_ch]
        cmp al,[bx+7]
        jne .new
        mov ax,[lg_arg]
        cmp ax,[bx+8]
        jne .new
        mov ax,[lg_res]
        cmp ax,[bx+10]
        jne .new
        cmp word [bx+12],0xFFFF
        je .new
        inc word [bx+12]
        mov [bx+2],dx
        cmp word [bx+12],400            ; the same call 400 times in a row
        jne .x
        cmp byte [lg_frz],0
        jne .x
        cmp word [lg_tot],ARM
        jb .x
        call freeze
        jmp short .x
.new:   mov ax,[lg_idx]
        mov bx,LOGSZ
        push dx
        mul bx
        pop dx
        mov bx,ax
        add bx,logbuf
        mov [bx],dx
        mov [bx+2],dx
        mov ax,[isr_cnt]
        mov [bx+4],ax
        mov al,[lg_fn]
        mov [bx+6],al
        mov al,[lg_ch]
        mov [bx+7],al
        mov ax,[lg_arg]
        mov [bx+8],ax
        mov ax,[lg_res]
        mov [bx+10],ax
        mov word [bx+12],1
        mov [lg_last],bx
        cmp byte [lg_frz],0
        je .cnt
        dec word [lg_after]
.cnt:   inc word [lg_idx]
        cmp word [lg_idx],LOGN
        jb .t
        mov word [lg_idx],0
.t:     add word [lg_tot],1
        adc word [lg_tot+2],0
.x:     pop es
        pop dx
        pop bx
        pop ax
        ret

write_log:
        mov ah,0x3C
        xor cx,cx
        mov dx,lg_name
        int 0x21
        jc .x
        mov bx,ax
        mov ah,0x40
        mov cx,lg_hdr_end-lg_hdr
        mov dx,lg_hdr
        int 0x21
        mov ah,0x40
        mov cx,12
        mov dx,lg_idx
        int 0x21
        mov ah,0x40
        mov cx,LOGN*LOGSZ
        mov dx,logbuf
        int 0x21
        mov ah,0x3E
        int 0x21
.x:     ret

lg_hdr:  db 'MXLG'
         dw LOGN, LOGSZ
lg_hdr_end:
%endif

; ------------------------------------------------------------- interrupt
isr:    push ax
        push dx
        push ds
        mov ax,cs
        mov ds,ax
        mov dx,[io]
        add dx,[ackport]
        in al,dx                        ; acknowledge
%ifdef LOG
        add word [isr_cnt],1
        adc word [isr_cnt+2],0
        cmp byte [lg_frz],0
        jne .nostall
        cmp word [lg_tot],ARM           ; not during the intro and the menus
        jb .idle
        cmp word [n_playing],0          ; only while something plays
        jne .chk
.idle:  mov ax,[isr_cnt]
        mov [lg_lastcall],ax
        jmp short .nostall
.chk:   mov ax,[isr_cnt]
        sub ax,[lg_lastcall]
        cmp ax,STALL
        jb .nostall
        mov [lg_arg],ax                 ; no driver call for 12 seconds
        mov byte [lg_fn],0xDD
        mov word [lg_ch],0xFF
        mov word [lg_res],0
        call do_log
        call freeze
.nostall:
%endif
        cmp word [active],0
        je .eoi
        cmp byte [autoinit],0
        jne .eoi
        call start_single
.eoi:   mov al,0x20                     ; end of interrupt before mixing
        cmp word [irq],8
        jb .low
        out 0xA0,al
.low:   out 0x20,al
        cmp word [active],0
        je .out
        cmp byte [in_mix],0
        jne .out
        ; mix with interrupts enabled (the CD-ROM, the timer and the
        ; keyboard must not wait); every channel is mixed with interrupts
        ; disabled, so the game never changes a channel in the middle
        mov byte [in_mix],1
        sti
        pushad
        push es
        cld
        call mix_half
        pop es
        popad
        cli
        mov byte [in_mix],0
.out:   pop ds
        pop dx
        pop ax
        iret

; ----------------------------------------------------------------- mixer
mix_half:
        ; the half that is not being played right now gets the new data
        cli
        xor al,al
        mov dx,[ffport]
        out dx,al
        mov dx,[cntport]
        in al,dx
        mov ah,al
        in al,dx
        sti
        xchg al,ah                      ; ax = bytes left - 1
        mov bx,BUF-1
        sub bx,ax
        and bx,BUF-1                    ; play position (samples)
        mov di,[buf_off]
        cmp bx,HALF
        jae .first
        add di,HALF
        cmp byte [bits16],0
        je .first
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
        cmp byte [bits16],0
        je .sil
        xor ax,ax
        shl cx,1
.sil:   rep stosw
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
        cli
        call mix_channel
        sti
        pop bx
.next:  add bx,2
        cmp bx,2*NCH
        jb .chan

        pop di
        push ds
        pop es
        mov si,accbuf
        mov cx,HALF
        cmp byte [bits16],0
        jne .out16
        ; accumulator -> unsigned 8 bit with clipping
.out:   lodsw
        sar ax,5
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

        ; accumulator -> signed 16 bit: linear (times 8) up to the knee,
        ; above it the curve of the 8-bit table
.out16: lodsw
        cmp ax,KNEE16
        jg .h
        cmp ax,-KNEE16
        jl .h
        shl ax,3
        stosw
        loop .out16
        ret
.h:     sar ax,5
        cmp ax,511
        jle .h1
        mov ax,511
.h1:    cmp ax,-512
        jge .h2
        mov ax,-512
.h2:    mov bx,ax
        mov ah,[softclip+512+bx]
        sub ah,0x80
        xor al,al
        stosw
        loop .out16
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
        sub ax,0x80
        shl ax,7
        add ax,bx                       ; linear interpolation, 7 more bits
        movzx ebp,word [cur_vol]
        movsx eax,ax
        imul eax,ebp
        sar eax,9                       ; one channel: up to +-4032
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
%ifdef LOG
        mov byte [lg_fn],0xEE
        mov ax,bx
        shr ax,1
        mov [lg_ch],ax
        mov word [lg_arg],0
        mov word [lg_res],0
        call do_log
%endif
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
dmabuf: times 4*BUF db 0x80               ; room for a 16-bit buffer that
                                        ; does not cross a 64K page
%ifdef LOG
logbuf: times LOGN*LOGSZ db 0
%endif
