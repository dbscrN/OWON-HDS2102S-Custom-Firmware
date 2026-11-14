#!/usr/bin/env python3
"""Decompress the initialised-data (.data) image that the Keil startup code copies to RAM.

Scatter table at 0x0807C4F0: {src=0x0807C510, dst=0x20000000, size=0x834, fn=__decompress (0x0800DCCE)}.
Writes analysis/data_init.bin (to be loaded at 0x20000000) and prints the strings in it.
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hds import FW

TABLE = 0x0807C4F0


def decompress(buf, size):
    out = bytearray()
    i = 0
    while len(out) < size:
        tok = buf[i]; i += 1
        lit = tok & 7
        if lit == 0:
            lit = buf[i]; i += 1
        mlen = tok >> 4
        if mlen == 0:
            mlen = buf[i]; i += 1
        out += buf[i:i + lit - 1]; i += lit - 1
        if tok & 8:
            off = buf[i]; i += 1
            for _ in range(mlen + 2):
                out.append(out[-off])
        else:
            out += bytes(mlen)
    return bytes(out[:size]), i


def load(f=None):
    f = f or FW()
    src, dst, size, fn = struct.unpack_from('<4I', f.m, TABLE - f.base)
    data, used = decompress(f.m[src - f.base:], size)
    return dst, data, src, used


def main():
    f = FW()
    dst, data, src, used = load(f)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'analysis', 'data_init.bin')
    open(out, 'wb').write(data)
    print('.data: %d bytes at 0x%08x (compressed 0x%08x..0x%08x)' % (len(data), dst, src, src + used))
    for m in re.finditer(rb'[\x20-\x7e]{4,}', data):
        print('  %08x %s' % (dst + m.start(), m.group().decode()))


if __name__ == '__main__':
    main()
