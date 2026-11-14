#!/usr/bin/env python3
"""Generate analysis/mcu.lst (full disassembly) and analysis/functions.txt (per-function index).

  mklisting.py [outdir]
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hds import FW, PERIPH


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'analysis')
    os.makedirs(out, exist_ok=True)
    f = FW()
    st = f.starts()
    callers = collections.defaultdict(set)
    callees = collections.defaultdict(list)
    for ia, t in f.bls():
        if ia >= f.CODE_END: continue
        fn = f.func_of(ia)
        callers[t].add(fn)
        callees[fn].append(t)
    lits = collections.defaultdict(list)
    for ia, rt, la, v in f.literals():
        if ia >= f.CODE_END: continue
        lits[f.func_of(ia)].append(v)

    with open(os.path.join(out, 'mcu.lst'), 'w', encoding='utf-8') as lst:
        for i, s in enumerate(st):
            e = st[i + 1] if i + 1 < len(st) else f.CODE_END
            lst.write('\n; ======== fn_%08x  size=%d  callers=%d ========\n' % (s, e - s, len(callers[s])))
            lst.write('\n'.join(f.dis(s, 1 << 30, e)))
            lst.write('\n')

    with open(os.path.join(out, 'functions.txt'), 'w', encoding='utf-8') as idx:
        idx.write('# addr size ncallers | callees | peripherals | strings | data refs\n')
        for i, s in enumerate(st):
            e = st[i + 1] if i + 1 < len(st) else f.CODE_END
            per, strs, datas = [], [], []
            for v in lits[s]:
                a = f.annotate(v)
                if a.startswith('str '):
                    strs.append(a[4:])
                elif a == 'data':
                    datas.append('%08x' % v)
                elif a and a != 'RAM' and not a.startswith('fn_'):
                    per.append(a.split('+')[0] if not a.startswith('bitband') else a)
            cs = sorted(set(callees[s]))
            idx.write('fn_%08x %5d %3d | %s | %s | %s | %s\n' % (
                s, e - s, len(callers[s]),
                ' '.join('%08x' % c for c in cs),
                ' '.join(sorted(set(per))),
                ' '.join(sorted(set(strs)))[:300],
                ' '.join(sorted(set(datas)))[:200]))
    print('functions:', len(st), '->', out)


if __name__ == '__main__':
    main()
