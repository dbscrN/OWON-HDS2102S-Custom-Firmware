#!/usr/bin/env python3
"""OWON HDS200-series firmware package (.upp) unpacker / packer.

Container layout (after de-obfuscation), as found in HDS2102S_LS V4.7.1:

  0x00  u32   CRC32 (zlib) of everything from 0x04 to the end of the file
  0x04  char  version string, e.g. "V4.7.1"            (16 bytes, NUL padded)
  0x14  char  "UpgradePack"                             (12 bytes)
  0x20  entry section 0  -> Anlogic FPGA bitstream      (0x40 bytes)
  0x60  entry section 1  -> MCU application image       (0x40 bytes)
  0xA0  char  model tag, e.g. "HDS2102S_LS"             (0x28 bytes)
  0xC8  section data, sections stored back to back

  entry: u32 type(=1), u32 file offset, u32 size, u32 CRC32 of the section, rest zero

Obfuscation: every byte of the file has KEY[offset % 8] added to it (mod 256).

The MCU image is a raw Cortex-M application linked at 0x0800C800
(the bootloader below that address is NOT part of the package).

Usage:
  upp_tool.py info   Scope.upp
  upp_tool.py unpack Scope.upp outdir
  upp_tool.py pack   outdir new.upp
"""
import json
import os
import struct
import sys
import zlib

KEY = bytes.fromhex('949490A191899192')
HDR_SIZE = 0xC8
ENTRY_OFFS = (0x20, 0x60)
SECTION_FILES = ('fpga.bit', 'mcu.bin')
MCU_BASE = 0x0800C800


def _xform(data, sign):
    return bytes((b + sign * KEY[i & 7]) & 0xFF for i, b in enumerate(data))


def decode(data):
    return _xform(data, -1)


def encode(data):
    return _xform(data, +1)


def crc(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def cstr(b):
    return b.split(b'\0', 1)[0].decode('ascii', 'replace')


def parse(raw):
    d = decode(raw)
    if d[0x14:0x1F] != b'UpgradePack':
        raise SystemExit('not an OWON UpgradePack (magic mismatch)')
    info = {
        'file_crc': struct.unpack_from('<I', d, 0)[0],
        'file_crc_ok': struct.unpack_from('<I', d, 0)[0] == crc(d[4:]),
        'version': cstr(d[0x04:0x14]),
        'magic': cstr(d[0x14:0x20]),
        'model': cstr(d[0xA0:0xC8]),
        'sections': [],
    }
    for eo, name in zip(ENTRY_OFFS, SECTION_FILES):
        typ, off, size, c = struct.unpack_from('<4I', d, eo)
        body = d[off:off + size]
        info['sections'].append({
            'file': name, 'type': typ, 'offset': off, 'size': size,
            'crc': c, 'crc_ok': c == crc(body) and len(body) == size,
        })
    return d, info


def cmd_info(path):
    d, info = parse(open(path, 'rb').read())
    print('version : %s' % info['version'])
    print('model   : %s' % info['model'])
    print('file crc: %08X %s' % (info['file_crc'], 'OK' if info['file_crc_ok'] else 'BAD'))
    for s in info['sections']:
        print('  %-8s type=%d off=0x%06X size=0x%06X crc=%08X %s' % (
            s['file'], s['type'], s['offset'], s['size'], s['crc'], 'OK' if s['crc_ok'] else 'BAD'))
    m = info['sections'][1]
    sp, rst = struct.unpack_from('<2I', d, m['offset'])
    print('mcu     : load 0x%08X..0x%08X  SP=0x%08X  reset=0x%08X' % (
        MCU_BASE, MCU_BASE + m['size'], sp, rst))
    return info


def cmd_unpack(path, outdir):
    d, info = parse(open(path, 'rb').read())
    os.makedirs(outdir, exist_ok=True)
    for s in info['sections']:
        with open(os.path.join(outdir, s['file']), 'wb') as f:
            f.write(d[s['offset']:s['offset'] + s['size']])
    with open(os.path.join(outdir, 'header.bin'), 'wb') as f:
        f.write(d[:HDR_SIZE])
    meta = {'version': info['version'], 'model': info['model'],
            'types': [s['type'] for s in info['sections']]}
    with open(os.path.join(outdir, 'header.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print('unpacked to', outdir)
    cmd_info(path)


def cmd_pack(indir, outpath):
    meta = json.load(open(os.path.join(indir, 'header.json'), encoding='utf-8'))
    # start from the original header so that any bytes we do not understand are preserved
    hdr = bytearray(open(os.path.join(indir, 'header.bin'), 'rb').read())
    if len(hdr) != HDR_SIZE:
        raise SystemExit('header.bin must be %d bytes' % HDR_SIZE)
    hdr[0x04:0x14] = meta['version'].encode('ascii').ljust(0x10, b'\0')[:0x10]
    hdr[0xA0:0xC8] = meta['model'].encode('ascii').ljust(0x28, b'\0')[:0x28]
    bodies = [open(os.path.join(indir, n), 'rb').read() for n in SECTION_FILES]
    off = HDR_SIZE
    for eo, body, typ in zip(ENTRY_OFFS, bodies, meta['types']):
        struct.pack_into('<4I', hdr, eo, typ, off, len(body), crc(body))
        off += len(body)
    d = bytes(hdr) + b''.join(bodies)
    d = struct.pack('<I', crc(d[4:])) + d[4:]
    with open(outpath, 'wb') as f:
        f.write(encode(d))
    print('packed', outpath, '(%d bytes)' % len(d))
    cmd_info(outpath)


def main(argv):
    if len(argv) == 3 and argv[1] == 'info':
        cmd_info(argv[2])
    elif len(argv) == 4 and argv[1] == 'unpack':
        cmd_unpack(argv[2], argv[3])
    elif len(argv) == 4 and argv[1] == 'pack':
        cmd_pack(argv[2], argv[3])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
