#!/usr/bin/env python3
"""Recover the SCPI command tree by statically walking the registration function fn_0801d7b8.

scpi_add(parent=r0, name=r1, set_handler=r2, query_handler=r3) -> node   (fn_08028474)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hds import FW

REG_FN = 0x0801D7B8
ADD_FN = 0x08028474


def main():
    f = FW()
    lines = f.dis(REG_FN, 1 << 30, f.func_end(REG_FN))
    reg = {}          # register -> symbolic value
    nodes = {0: ''}   # node id -> full path
    nid = 0
    out = []
    for ln in lines:
        m = re.match(r'[0-9a-f]{8}:\s+(\S+)\s+(.*?)\s*(;.*)?$', ln)
        if not m: continue
        mn, ops, note = m.group(1), m.group(2), m.group(3) or ''
        if mn in ('movs', 'mov', 'mov.w'):
            d, s = [x.strip() for x in ops.split(',', 1)]
            if s.startswith('#'):
                reg[d] = ('imm', int(s[1:], 0))
            else:
                reg[d] = reg.get(s, ('?', None))
        elif mn in ('adr', 'subw', 'addw') and 'pc' in ops or mn == 'adr':
            d = ops.split(',')[0].strip()
            if mn == 'adr':
                t = int(re.search(r'->0x([0-9a-f]+)', note).group(1), 16)
            else:
                a = int(ln[:8], 16)
                imm = int(re.search(r'#(0x[0-9a-f]+|\d+)', ops).group(1), 0)
                t = ((a + 4) & ~3) + (imm if mn == 'addw' else -imm)
            reg[d] = ('str', f.cstr(t, 40).decode('ascii', 'replace'))
        elif mn == 'ldr' and '[pc' in ops:
            d = ops.split(',')[0].strip()
            v = int(re.search(r'=0x([0-9a-f]+)', note).group(1), 16)
            reg[d] = ('fn', v & ~1)
        elif mn == 'bl':
            t = int(ops.lstrip('#'), 16)
            if t == ADD_FN:
                par = reg.get('r0', ('?', None))
                name = reg.get('r1', ('?', '?'))[1]
                h_set = reg.get('r2', ('imm', 0)); h_q = reg.get('r3', ('imm', 0))
                pid = par[1] if par[0] == 'node' else 0
                nid += 1
                nodes[nid] = nodes.get(pid, '?') + ':' + str(name)
                fs = ('%08x' % h_set[1]) if h_set[0] == 'fn' else '-'
                fq = ('%08x' % h_q[1]) if h_q[0] == 'fn' else '-'
                out.append((nodes[nid], fs, fq))
                reg['r0'] = ('node', nid)
            else:
                reg['r0'] = ('?', None)
    print('%-44s %-9s %-9s' % ('command', 'set', 'query'))
    for path, fs, fq in out:
        print('%-44s %-9s %-9s' % (path, fs, fq))
    print('nodes:', nid, '(table capacity 126)')


if __name__ == '__main__':
    main()
