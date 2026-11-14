#!/usr/bin/env python3
"""Analysis helpers for the OWON HDS2102S MCU image (unpacked/mcu.bin, base 0x0800C800).

CLI:
  hds.py dis  ADDR [COUNT]      linear disassembly of COUNT instructions (default 60)
  hds.py func ADDR              disassemble the function containing/starting at ADDR
  hds.py xref ADDR              who references ADDR (literal pools, BL, pointer tables)
  hds.py calls ADDR             functions called from the function at ADDR
  hds.py hex  ADDR [LEN]        hex dump
  hds.py ptrs ADDR [COUNT]      dump pointer table with string previews
Requires: pip install capstone
"""
import bisect
import collections
import os
import struct
import sys

BASE = 0x0800C800
DEFAULT_IMG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'unpacked', 'mcu.bin')

PERIPH = {
    0x40000000: 'TIMER1', 0x40000400: 'TIMER2', 0x40000800: 'TIMER3', 0x40000C00: 'TIMER4',
    0x40001000: 'TIMER5', 0x40001400: 'TIMER6', 0x40002800: 'RTC', 0x40003000: 'FWDGT',
    0x40003800: 'SPI1', 0x40003C00: 'SPI2', 0x40004400: 'USART1', 0x40004800: 'USART2',
    0x40004C00: 'UART3', 0x40005000: 'UART4', 0x40005400: 'I2C0', 0x40005800: 'I2C1',
    0x40005C00: 'USBD', 0x40006000: 'USBD_RAM', 0x40006C00: 'BKP', 0x40007000: 'PMU',
    0x40007400: 'DAC', 0x40010000: 'AFIO', 0x40010400: 'EXTI', 0x40010800: 'GPIOA',
    0x40010C00: 'GPIOB', 0x40011000: 'GPIOC', 0x40011400: 'GPIOD', 0x40011800: 'GPIOE',
    0x40012400: 'ADC0', 0x40012800: 'ADC1', 0x40012C00: 'TIMER0', 0x40013000: 'SPI0',
    0x40013400: 'TIMER7', 0x40013800: 'USART0', 0x40020000: 'DMA0', 0x40020400: 'DMA1',
    0x40021000: 'RCU', 0x40022000: 'FMC', 0x40023000: 'CRC', 0xE000E000: 'SCS',
    0xE000ED00: 'SCB', 0xE000E100: 'NVIC',
}


class FW:
    def __init__(self, path=DEFAULT_IMG, base=BASE):
        self.m = open(path, 'rb').read()
        self.base = base
        self.n = len(self.m)
        self._lit = self._bl = self._starts = self._md = None

    # --- raw access -------------------------------------------------------
    def u8(self, a): return self.m[a - self.base]
    def u16(self, a): return struct.unpack_from('<H', self.m, a - self.base)[0]
    def u32(self, a): return struct.unpack_from('<I', self.m, a - self.base)[0]
    def inimg(self, a): return self.base <= a < self.base + self.n
    def data(self, a, n): return self.m[a - self.base:a - self.base + n]

    def cstr(self, a, maxlen=120):
        o = a - self.base
        e = self.m.find(b'\0', o, o + maxlen)
        if e < 0: e = o + maxlen
        return self.m[o:e]

    def looks_str(self, a, minlen=3):
        if not self.inimg(a): return None
        s = self.cstr(a, 80)
        if len(s) < minlen: return None
        try:
            t = s.decode('utf-8')
        except UnicodeDecodeError:
            return None
        if all(ch >= ' ' or ch in '\r\n\t' for ch in t):
            return t
        return None

    # --- scans -------------------------------------------------------------
    def literals(self):
        """(insn_addr, reg, literal_addr, value) for pc-relative LDR (16- and 32-bit encodings)."""
        if self._lit is None:
            res = []
            m, n, b = self.m, self.n, self.base
            for o in range(0, n - 4, 2):
                w = struct.unpack_from('<H', m, o)[0]
                if (w & 0xF800) == 0x4800:
                    la = ((o + 4) & ~3) + (w & 0xFF) * 4
                    if la + 4 <= n:
                        res.append((b + o, (w >> 8) & 7, b + la, struct.unpack_from('<I', m, la)[0]))
                elif w == 0xF8DF:
                    w2 = struct.unpack_from('<H', m, o + 2)[0]
                    la = ((o + 4) & ~3) + (w2 & 0xFFF)
                    if la + 4 <= n:
                        res.append((b + o, w2 >> 12, b + la, struct.unpack_from('<I', m, la)[0]))
            self._lit = res
        return self._lit

    def bls(self):
        if self._bl is None:
            res = []
            m, n, b = self.m, self.n, self.base
            for o in range(0, n - 4, 2):
                w1, w2 = struct.unpack_from('<HH', m, o)
                if (w1 & 0xF800) != 0xF000 or (w2 & 0xD000) != 0xD000: continue
                s = (w1 >> 10) & 1
                i1 = 1 - (((w2 >> 13) & 1) ^ s)
                i2 = 1 - (((w2 >> 11) & 1) ^ s)
                imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((w1 & 0x3FF) << 12) | ((w2 & 0x7FF) << 1)
                if s: imm -= 1 << 25
                t = b + o + 4 + imm
                if self.inimg(t):
                    res.append((b + o, t))
            self._bl = res
        return self._bl

    CODE_END = 0x08047B00

    def starts(self):
        """sorted function start candidates: BL targets + code pointers found in the image."""
        if self._starts is None:
            s = {t for _, t in self.bls() if t < self.CODE_END}
            for o in range(0, self.n - 3, 4):
                v = struct.unpack_from('<I', self.m, o)[0]
                if (v & 1) and self.base <= v < self.CODE_END:
                    w = self.u16(v & ~1)
                    # accept only if it looks like a prologue (push / push.w) or trivially short fn
                    if (w & 0xFE00) == 0xB400 or w == 0xE92D:
                        s.add(v & ~1)
            self._starts = sorted(s)
        return self._starts

    def func_of(self, a):
        st = self.starts()
        i = bisect.bisect_right(st, a) - 1
        return st[i] if i >= 0 else None

    def func_end(self, a):
        st = self.starts()
        i = bisect.bisect_right(st, a)
        return st[i] if i < len(st) else self.CODE_END

    # --- disassembly -------------------------------------------------------
    def md(self):
        if self._md is None:
            import capstone
            self._md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        return self._md

    def annotate(self, v):
        if 0x20000000 <= v < 0x20010000:
            return 'RAM'
        blk = v & 0xFFFFFC00
        if blk in PERIPH:
            return '%s+0x%x' % (PERIPH[blk], v & 0x3FF)
        if (v & 0xFFFFFF00) in PERIPH:
            return '%s+0x%x' % (PERIPH[v & 0xFFFFFF00], v & 0xFF)
        if 0x42000000 <= v < 0x44000000:
            off = v - 0x42000000
            per = 0x40000000 + (off >> 5)
            return 'bitband %s+0x%x bit %d' % (PERIPH.get(per & 0xFFFFFC00, '%08x' % (per & 0xFFFFFC00)), per & 0x3FF, (off >> 2) & 7 | ((per & 1) << 3))
        if self.inimg(v):
            if (v & 1) and v < self.CODE_END:
                return 'fn_%08x' % (v & ~1)
            s = self.looks_str(v)
            if s is not None:
                return 'str %r' % s[:60]
            return 'data'
        return ''

    def dis(self, a, count=60, end=None):
        """yield text lines; stops at `end` address if given. Literal pools are skipped."""
        a &= ~1
        out = []
        pool = set()
        md = self.md()
        while count > 0 and (end is None or a < end):
            if a in pool:
                out.append('%08x:  .word 0x%08x  %s' % (a, self.u32(a), self.annotate(self.u32(a))))
                a += 4
                continue
            code = self.data(a, 4)
            ins = next(md.disasm(code, a, 1), None)
            if ins is None:
                out.append('%08x:  .hword 0x%04x' % (a, self.u16(a)))
                a += 2
                continue
            note = ''
            w = self.u16(a)
            la = None
            if (w & 0xF800) == 0x4800:
                la = ((a + 4) & ~3) + (w & 0xFF) * 4
            elif w == 0xF8DF:
                la = ((a + 4) & ~3) + (self.u16(a + 2) & 0xFFF)
            if la is not None and self.inimg(la):
                v = self.u32(la)
                pool.add(la)
                note = '; =0x%08x %s' % (v, self.annotate(v))
            elif (w & 0xF800) == 0xA000:  # adr
                t = ((a + 4) & ~3) + (w & 0xFF) * 4
                s = self.looks_str(t, 1)
                note = '; ->0x%08x %s' % (t, ('str %r' % s[:60]) if s is not None else '')
            out.append('%08x:  %-8s %-34s %s' % (a, ins.mnemonic, ins.op_str, note))
            a += ins.size
            count -= 1
        return out

    def func(self, a):
        s = self.func_of(a & ~1)
        return self.dis(s, 100000, self.func_end(s))

    def calls(self, a):
        s = self.func_of(a & ~1); e = self.func_end(s)
        return [(ia, t) for ia, t in self.bls() if s <= ia < e]

    def xref(self, a):
        res = []
        for ia, rt, la, v in self.literals():
            if v == a or v == (a | 1):
                res.append(('ldr', ia, self.func_of(ia)))
        for ia, t in self.bls():
            if t == (a & ~1):
                res.append(('bl', ia, self.func_of(ia)))
        for o in range(0, self.n - 3, 4):
            v = struct.unpack_from('<I', self.m, o)[0]
            if (v == a or v == (a | 1)) and self.base + o >= self.CODE_END:
                res.append(('ptr', self.base + o, None))
        return res


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    f = FW()
    cmd, a = argv[1], int(argv[2], 16)
    arg = int(argv[3], 0) if len(argv) > 3 else None
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    if cmd == 'dis':
        print('\n'.join(f.dis(a, arg or 60)))
    elif cmd == 'func':
        print('\n'.join(f.func(a)))
    elif cmd == 'calls':
        c = collections.Counter(t for _, t in f.calls(a))
        for t, k in sorted(c.items()):
            print('%08x x%d' % (t, k))
    elif cmd == 'xref':
        for kind, ia, fn in f.xref(a):
            print('%-4s %08x %s' % (kind, ia, ('in fn_%08x' % fn) if fn else ''))
    elif cmd == 'hex':
        n = arg or 0x100
        for i in range(0, n, 16):
            c = f.data(a + i, 16)
            print('%08x  %-48s %s' % (a + i, c.hex(' '), ''.join(chr(x) if 32 <= x < 127 else '.' for x in c)))
    elif cmd == 'ptrs':
        for i in range(arg or 32):
            v = f.u32(a + 4 * i)
            print('%08x: %08x %s' % (a + 4 * i, v, f.annotate(v)))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
