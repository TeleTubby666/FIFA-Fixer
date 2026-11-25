#!/usr/bin/env python3
"""Print MIXLOG.BIN written by the debug build of CUST_DIG.ADV."""
import struct, sys
NAMES = {100: 'describe', 101: 'detect', 102: 'init', 104: 'shutdown', 123: 'play', 124: 'status',
         125: 'start', 126: 'stop', 127: 'pause', 128: 'resume', 129: 'set_vol', 130: 'get_vol',
         131: 'set_pan', 132: 'get_pan', 0xEE: 'FINISHED', 0xDD: 'STALL'}
d = open(sys.argv[1], 'rb').read()
assert d[:4] == b'MXLG'
n, sz = struct.unpack_from('<HH', d, 4)
idx, last, tot, isr = struct.unpack_from('<HHII', d, 8)
buf = d[20:20 + n * sz]
print('entries', tot, 'kept', min(tot, n), 'interrupts', isr)
order = list(range(idx, n)) + list(range(idx)) if tot > n else list(range(idx))
t0 = None
prev = None
for i in order:
    tf, tl, ic, fn, ch, arg, res, cnt = struct.unpack_from('<HHHBBHHH', buf, i * sz)
    if t0 is None: t0 = tf
    t = ((tf - t0) & 0xFFFF) / 43.4
    gap = ''
    if prev is not None and ((tf - prev) & 0xFFFF) > 43 * 3:
        gap = '   <-- %.1f s without a call' % (((tf - prev) & 0xFFFF) / 43.4)
    dur = ((tl - tf) & 0xFFFF) / 43.4
    print('%7.1f s  irq %5d  %-8s ch %-3s arg %04X res %d%s%s' % (
        t, ic, NAMES.get(fn, str(fn)), ch if ch != 0xFF else '-', arg, res,
        ('  x%d over %.1f s' % (cnt, dur)) if cnt > 1 else '', gap))
    prev = tl
