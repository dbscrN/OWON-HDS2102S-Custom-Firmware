#!/usr/bin/env python3
"""Predict the checksum the device shows in System -> About.

fn_0802fea4 computes a standard CRC32 (init 0xFFFFFFFF, final inversion) over
  1) MCU flash 0x0800C800 .. +45*0x2800 (0x70800 bytes: the app image plus whatever follows it)
  2) the first 150*0x2800 (0x177000) bytes of the external SPI flash
Region 2 is assumed to hold the FPGA bitstream followed by erased (0xFF) bytes - an assumption
that is confirmed when the predicted value matches what the device shows.

  device_checksum.py DIR [DIR ...]      DIR contains mcu.bin and fpga.bit
"""
import os
import sys
import zlib

APP_SPAN = 45 * 0x2800
SPI_SPAN = 150 * 0x2800


def predict(d, pad=b'\xff'):
    app = open(os.path.join(d, 'mcu.bin'), 'rb').read()
    bit = open(os.path.join(d, 'fpga.bit'), 'rb').read()
    blob = app + pad * (APP_SPAN - len(app)) + bit + pad * (SPI_SPAN - len(bit))
    return zlib.crc32(blob) & 0xFFFFFFFF


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    for d in argv[1:]:
        print('%-20s %08X' % (d, predict(d)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
