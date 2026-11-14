#!/usr/bin/env python3
"""Change UI palette colours of the OWON HDS2102S_LS V4.7.1 firmware and build a new .upp.

The screen is a 4-bit indexed framebuffer. Colours 0..2 are fixed (0 black/transparent, 1 white,
2 black); colours 3..15 are loaded into FPGA registers 0xC0..0xCC by fn_0802fd38 as RGB565
immediates. A 16-entry RGB565 copy at 0x08048910 is used when saving screenshots.

  patch_palette.py show
  patch_palette.py build NAME IDX=RRGGBB [IDX=RRGGBB ...]

Example (blue UI elements -> purple):
  patch_palette.py build purple 3=63318C

Output: build/NAME/Scope.upp (plus the patched mcu.bin next to it).
"""
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, HERE)
import upp_tool
from hds import FW, BASE

EXPECT_VERSION, EXPECT_MODEL = 'V4.7.1', 'HDS2102S_LS'
PAL_FN = 0x0802FD38
FIRST_INSN = 0x0802FD40      # movw r1, #colour for palette index 3
INSN_STEP = 14               # each "load table byte, movw, bl" block is 14 bytes
WR16 = 0x0801561A
SHOT_TABLE = 0x08048910      # 16 x u32 RGB565, indexed by colour
FIRST_IDX, LAST_IDX = 3, 16  # 16 = extra register 0xCD (purpose unknown)

NOTES = {
    3: 'main UI fill: menu buttons, status boxes (the "blue")',
    4: 'box background (near black)',
    5: 'yellow: DMM digits / CH1',
    6: 'light blue (probably CH2)',
    7: 'green ("Auto")',
    8: 'light grey: text, selection highlight',
    9: 'red',
    16: 'register 0xCD, not a framebuffer colour',
}


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return (r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31


def enc_movw(rd, imm16):
    imm4, i, imm3, imm8 = imm16 >> 12, (imm16 >> 11) & 1, (imm16 >> 8) & 7, imm16 & 0xFF
    return struct.pack('<HH', 0xF240 | (i << 10) | imm4, (imm3 << 12) | (rd << 8) | imm8)


def insn_addr(idx):
    return FIRST_INSN + INSN_STEP * (idx - FIRST_IDX)


def read_colour(f, idx):
    """decode the immediate of the mov at insn_addr(idx) with capstone; also sanity-check the block."""
    a = insn_addr(idx)
    ins = next(f.md().disasm(f.data(a, 4), a, 1))
    if ins.mnemonic not in ('movw', 'mov.w') or not ins.op_str.startswith('r1, #'):
        raise SystemExit('unexpected instruction at %08x: %s %s' % (a, ins.mnemonic, ins.op_str))
    bl = next(f.md().disasm(f.data(a + 4, 4), a + 4, 1))
    if bl.mnemonic != 'bl' or int(bl.op_str.lstrip('#'), 16) != WR16:
        raise SystemExit('palette block at %08x is not followed by the FPGA write call' % a)
    return int(ins.op_str.split('#')[1], 0)


def check_image(indir):
    meta_path = os.path.join(indir, 'header.json')
    import json
    meta = json.load(open(meta_path, encoding='utf-8'))
    if meta['version'] != EXPECT_VERSION or meta['model'] != EXPECT_MODEL:
        raise SystemExit('this patcher only knows %s %s, got %s %s' % (
            EXPECT_MODEL, EXPECT_VERSION, meta['model'], meta['version']))


def cmd_show(indir):
    check_image(indir)
    f = FW(os.path.join(indir, 'mcu.bin'))
    print('idx  FPGA    RGB565  R   G   B    screenshot  note')
    for idx in range(FIRST_IDX, LAST_IDX + 1):
        v = read_colour(f, idx)
        shot = ('%04X' % (f.u32(SHOT_TABLE + 4 * idx) & 0xFFFF)) if idx < 16 else '  - '
        print('%3d  0x%02X    %04X   %3d %3d %3d   %s        %s' % (
            (idx, 0xC0 + idx - FIRST_IDX, v) + from565(v) + (shot, NOTES.get(idx, ''))))


def cmd_build(indir, name, specs):
    check_image(indir)
    src = os.path.join(indir, 'mcu.bin')
    f = FW(src)
    img = bytearray(f.m)
    changes = []
    for spec in specs:
        idx_s, col_s = spec.split('=')
        idx, col_s = int(idx_s), col_s.lstrip('#')
        if not FIRST_IDX <= idx <= LAST_IDX:
            raise SystemExit('colour index must be %d..%d' % (FIRST_IDX, LAST_IDX))
        if len(col_s) != 6:
            raise SystemExit('colour must be RRGGBB hex, got %r' % col_s)
        r, g, b = (int(col_s[i:i + 2], 16) for i in (0, 2, 4))
        new = rgb565(r, g, b)
        old = read_colour(f, idx)
        a = insn_addr(idx)
        img[a - BASE:a - BASE + 4] = enc_movw(1, new)
        if idx < 16:
            struct.pack_into('<I', img, SHOT_TABLE + 4 * idx - BASE, new)
        changes.append((idx, old, new))

    outdir = os.path.join(ROOT, 'build', name)
    os.makedirs(outdir, exist_ok=True)
    for fn in ('header.bin', 'header.json', 'fpga.bit'):
        shutil.copyfile(os.path.join(indir, fn), os.path.join(outdir, fn))
    with open(os.path.join(outdir, 'mcu.bin'), 'wb') as fo:
        fo.write(img)

    # verify: re-read through the disassembler, and make sure nothing else changed
    g = FW(os.path.join(outdir, 'mcu.bin'))
    for idx, old, new in changes:
        got = read_colour(g, idx)
        if got != new:
            raise SystemExit('verification failed for index %d' % idx)
        print('index %2d: %04X %s -> %04X %s' % (idx, old, from565(old), new, from565(new)))
    diff = [i for i in range(len(img)) if img[i] != f.m[i]]
    allowed = set()
    for idx, _, _ in changes:
        allowed.update(range(insn_addr(idx) - BASE, insn_addr(idx) - BASE + 4))
        if idx < 16:
            allowed.update(range(SHOT_TABLE + 4 * idx - BASE, SHOT_TABLE + 4 * idx - BASE + 4))
    if not set(diff) <= allowed or len(img) != len(f.m):
        raise SystemExit('unexpected bytes changed')
    print('changed bytes: %d (all inside the palette loader / screenshot table)' % len(diff))
    upp_tool.cmd_pack(outdir, os.path.join(outdir, 'Scope.upp'))


def main(argv):
    indir = os.path.join(ROOT, 'unpacked')
    if len(argv) == 2 and argv[1] == 'show':
        cmd_show(indir)
    elif len(argv) >= 4 and argv[1] == 'build':
        cmd_build(indir, argv[2], argv[3:])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
