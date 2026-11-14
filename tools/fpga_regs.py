#!/usr/bin/env python3
"""List FPGA register accesses (EXMC, reg index = address >> 17) per register.

For every call of the bus primitives, look back a few instructions for the constant loaded into r0.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hds import FW

PRIMS = {
    0x080155EA: 'rd16s', 0x080155F6: 'rd32', 0x0801560E: 'rd16',
    0x0801561A: 'wr16', 0x08015626: 'wr32', 0x08015644: 'wr48', 0x0801566C: 'wr16',
}


REGTAB = 0x08047EC8


def main():
    f = FW()
    md = f.md()
    regs = collections.defaultdict(lambda: collections.defaultdict(set))
    unknown = collections.Counter()
    for ia, t in f.bls():
        if t not in PRIMS or ia >= f.CODE_END: continue
        fn = f.func_of(ia)
        # disassemble from function start up to the call, keep the last r0 constant
        val = None
        a = max(fn, ia - 40)
        # resync: walk linear from fn start is expensive; use a short window, decode 2-byte aligned
        lines = f.dis(a, 40, ia)
        for k, ln in enumerate(reversed(lines)):
            m = re.search(r'\bmovs?(?:\.w)?\s+r0, #(0x[0-9a-f]+|\d+)', ln) or re.search(r'\bmovw\s+r0, #(0x[0-9a-f]+|\d+)', ln)
            if m:
                val = ('imm', int(m.group(1), 0))
                break
            m = re.search(r'\bldrb(?:\.w)?\s+r0, \[(r\d+)(?:, #(0x[0-9a-f]+|\d+))?\]', ln)
            if m:
                # was the base register loaded with the register-index table?
                prev = lines[:len(lines) - 1 - k]
                for pl in reversed(prev[-6:]):
                    if re.search(r'\bldr\s+%s, \[pc' % m.group(1), pl):
                        if '=0x%08x' % REGTAB in pl:
                            off = int(m.group(2), 0) if m.group(2) else 0
                            val = ('tab', off)
                        break
                break
            if re.search(r'\b(mov|ldr\w*|add\w*|sub\w*|uxt\w+|sxt\w+)\s+r0,', ln) or re.search(r'\bbl\s', ln):
                break
        if val is None:
            unknown[(PRIMS[t], fn)] += 1
        else:
            regs[val][PRIMS[t]].add(fn)
    print('REGTAB @0x%08x: id -> FPGA register index (bus address = 0x60000000 + (index << 17))' % REGTAB)
    for r in sorted(regs):
        parts = []
        for k in sorted(regs[r]):
            fns = sorted(regs[r][k])
            parts.append('%s[%s]' % (k, ' '.join('%08x' % x for x in fns[:6]) + (' ...' if len(fns) > 6 else '')))
        kind, v = r
        if kind == 'tab':
            print('id 0x%02x -> reg 0x%02x: %s' % (v, f.u8(REGTAB + v), '  '.join(parts)))
        else:
            print('imm       reg 0x%02x: %s' % (v, '  '.join(parts)))
    print('--- calls with computed register index (function: count) ---')
    for (k, fn), c in sorted(unknown.items(), key=lambda x: x[0][1]):
        print('  %s in fn_%08x x%d' % (k, fn, c))


if __name__ == '__main__':
    main()
