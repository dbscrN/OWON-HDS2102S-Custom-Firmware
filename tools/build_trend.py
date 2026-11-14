#!/usr/bin/env python3
"""Build the DMM trend-graph add-on and produce build/NAME/Scope.upp.

  build_trend.py [NAME] [--base DIR]

DIR is an unpacked firmware directory to start from (default: unpacked; use e.g. build/red to keep
a palette change). Steps: compile src/trend.c, place the blob inside the 18 px CJK glyph table,
redirect five BL instructions to it, verify, pack. The image size does not change.
"""
import glob
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import upp_tool
from hds import FW, BASE

LOAD = 0x0804A510     # inside the 18 px CJK glyph table (0x0804A496..0x0804E402)
LIMIT = 0x0804E400
STOCK_END = 0x0807C7BC
STR_TABLE, STR_COUNT, STR_LANGS, STR_ENGLISH = 0x08048A70, 188, 8, 1   # UI strings: 8 languages x 188 pointers
# (address of the BL, original target, symbol that replaces it)
HOOKS = [
    (0x0801FFBC, 0x080304E4, 'trend_tick'),
    (0x080150CA, 0x08019784, 'trend_parse'),
    (0x08015136, 0x08041578, 'trend_show'),
    (0x08018638, 0x0803CC5C, 'trend_flush'),
    (0x0802047E, 0x08035B7C, 'trend_key'),
]


def find_gcc():
    for pat in (r'C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\*\bin',
                r'C:\Program Files\Arm GNU Toolchain arm-none-eabi\*\bin'):
        for d in sorted(glob.glob(pat), reverse=True):
            if os.path.exists(os.path.join(d, 'arm-none-eabi-gcc.exe')):
                return d
    d = shutil.which('arm-none-eabi-gcc')
    if d:
        return os.path.dirname(d)
    raise SystemExit('arm-none-eabi-gcc not found')


def enc_bl(src, dst):
    off = dst - (src + 4)
    if off & 1 or not -(1 << 24) <= off < (1 << 24):
        raise SystemExit('BL out of range')
    s = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (~(i1 ^ s)) & 1, (~(i2 ^ s)) & 1
    return struct.pack('<HH', 0xF000 | (s << 10) | ((off >> 12) & 0x3FF),
                       0xD000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF))


def bl_target(f, a):
    ins = next(f.md().disasm(f.data(a, 4), a, 1))
    return int(ins.op_str.lstrip('#'), 16) if ins.mnemonic == 'bl' else None


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    name = args[0] if args else 'trend'
    base = os.path.join(ROOT, 'unpacked')
    if '--base' in argv:
        base = os.path.join(ROOT, argv[argv.index('--base') + 1])
    out = os.path.join(ROOT, 'build', name)
    obj = os.path.join(out, 'obj')
    os.makedirs(obj, exist_ok=True)

    f = FW(os.path.join(base, 'mcu.bin'))
    if BASE + f.n != STOCK_END:
        raise SystemExit('base image has unexpected size (already extended?)')
    for a, tgt, _ in HOOKS:
        if bl_target(f, a) != tgt:
            raise SystemExit('hook site %08x is not "bl %08x"' % (a, tgt))

    b = find_gcc()
    gcc = os.path.join(b, 'arm-none-eabi-gcc.exe')
    elf = os.path.join(obj, 'trend.elf')
    blob_path = os.path.join(obj, 'trend.bin')
    subprocess.check_call([
        gcc, '-mcpu=cortex-m4', '-mthumb', '-mfpu=fpv4-sp-d16', '-mfloat-abi=hard',
        '-Os', '-ffreestanding', '-fno-builtin', '-fno-tree-loop-distribute-patterns',
        '-fno-jump-tables', '-fsingle-precision-constant', '-Wall', '-Wextra', '-Wdouble-promotion',
        '-nostdlib', '-nostartfiles', '-Wl,--gc-sections', '-Wl,-T,' + os.path.join(ROOT, 'src', 'trend.ld'),
        '-Wl,--no-warn-rwx-segments', '-Wl,-e,trend_flush',
        '-Wl,-u,trend_parse', '-Wl,-u,trend_tick', '-Wl,-u,trend_show', '-Wl,-u,trend_key', '-Wl,-u,trend_flush',
        '-o', elf, os.path.join(ROOT, 'src', 'trend.c')] + (['-DPROBE'] if '--probe' in argv else []))
    und = subprocess.check_output([os.path.join(b, 'arm-none-eabi-nm.exe'), '-u', elf], text=True).strip()
    if und:
        raise SystemExit('undefined symbols (library calls?):\n' + und)
    syms = {}
    for ln in subprocess.check_output([os.path.join(b, 'arm-none-eabi-nm.exe'), elf], text=True).split('\n'):
        p = ln.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    subprocess.check_call([os.path.join(b, 'arm-none-eabi-objcopy.exe'), '-O', 'binary', '-j', '.text', elf, blob_path])
    blob = open(blob_path, 'rb').read()
    end = LOAD + len(blob)
    print('add-on: %d bytes at %08x..%08x (%d free in the CJK glyph table)' % (len(blob), LOAD, end, LIMIT - end))
    if end > LIMIT:
        raise SystemExit('add-on too large')

    img = bytearray(f.m)
    img[LOAD - BASE:end - BASE] = blob
    for a, tgt, sym in HOOKS:
        dst = syms[sym] & ~1
        img[a - BASE:a - BASE + 4] = enc_bl(a, dst)
        print('hook %08x: bl %08x -> bl %08x (%s)' % (a, tgt, dst, sym))

    # one language only: every language block of the UI string table points at the English strings
    extra = set()
    if '--keep-langs' not in argv:
        tab = STR_TABLE - BASE
        eng = img[tab + STR_ENGLISH * STR_COUNT * 4:tab + (STR_ENGLISH + 1) * STR_COUNT * 4]
        for lang in range(STR_LANGS):
            img[tab + lang * STR_COUNT * 4:tab + (lang + 1) * STR_COUNT * 4] = eng
        extra = set(range(tab, tab + STR_LANGS * STR_COUNT * 4))
        print('languages: all %d blocks of the string table now use English' % STR_LANGS)

    for fn in ('header.bin', 'header.json', 'fpga.bit'):
        shutil.copyfile(os.path.join(base, fn), os.path.join(out, fn))
    with open(os.path.join(out, 'mcu.bin'), 'wb') as fo:
        fo.write(img)

    # verification on the written file
    g = FW(os.path.join(out, 'mcu.bin'))
    for a, tgt, sym in HOOKS:
        if bl_target(g, a) != syms[sym] & ~1:
            raise SystemExit('hook verification failed at %08x' % a)
    diff = [i for i in range(f.n) if g.m[i] != f.m[i]]
    allowed = {a - BASE + k for a, _, _ in HOOKS for k in range(4)} | set(range(LOAD - BASE, end - BASE)) | extra
    if not set(diff) <= allowed or g.n != f.n:
        raise SystemExit('unexpected bytes changed in the stock image')
    print('image size unchanged; %d BL instructions redirected, %d bytes of add-on code placed' % (len(HOOKS), len(blob)))
    upp_tool.cmd_pack(out, os.path.join(out, 'Scope.upp'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
