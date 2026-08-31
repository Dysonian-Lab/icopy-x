"""ics_decoder   ICS Decoder (ATmega32U4) serial bridge.

Bridges the DIY decoder firmware to the existing iCLASS write path.
Protocol:
  Host -> Dev : Who\r\n   -> Dev -> Host : ISE\r\n
  Host -> Dev : RD\r\n    -> Dev -> Host : OK\r\n + $A_CARD_START$...$A_CARD_STOP$
  or                      -> Dev -> Host : ??\r\n  (no card)
"""

import glob
import os
import sys

try:
    import serial
    from serial.tools.list_ports import comports as _comports
except ImportError:
    serial = None
    _comports = None

try:
    import iclasswrite
except ImportError:
    try:
        from . import iclasswrite
    except ImportError:
        iclasswrite = None

_BAUD_RATE = 115200
_CMD_WHO = 'Who\r\n'
_CMD_RD = 'RD\r\n'
_READLINE_TIMEOUT = 3.0


def _open_serial(port):
    if serial is None:
        return None
    try:
        ser = serial.Serial(port, _BAUD_RATE, timeout=_READLINE_TIMEOUT)
        return ser
    except Exception:
        return None


def detect_decoder():
    """Scan serial ports for the ICS Decoder.

    Returns the serial handle if a device replies with ISE to Who,
    otherwise closes any opened handle and returns None.
    """
    if serial is None:
        return None

    candidates = []

    if sys.platform.startswith('linux'):
        for g in ('/dev/ttyACM*', '/dev/ttyUSB*'):
            candidates.extend(glob.glob(g))
        seen = set()
        candidates = [c for c in candidates if not (c in seen or seen.add(c))]
    elif sys.platform.startswith('win'):
        if _comports is not None:
            try:
                candidates = [p.device for p in _comports()]
            except Exception:
                candidates = []
        if not candidates:
            for i in range(1, 257):
                candidates.append('COM%d' % i)
    elif sys.platform.startswith('darwin'):
        for g in ('/dev/cu.usbmodem*', '/dev/cu.usbserial*'):
            candidates.extend(glob.glob(g))
        seen = set()
        candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for port in candidates:
        ser = _open_serial(port)
        if ser is None:
            continue
        try:
            ser.write(_CMD_WHO.encode('utf-8'))
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if 'ISE' in line:
                return ser
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass

    return None


def read_card(ser):
    """Send RD and read a card block from the decoder.

    Returns a parsed dict (see parse_block) or None on no-card / error.
    """
    if ser is None or not ser.is_open:
        return None

    try:
        ser.write(_CMD_RD.encode('utf-8'))
    except Exception:
        return None

    buf = ''
    while True:
        try:
            raw = ser.readline()
            if not raw:
                break
            line = raw.decode('utf-8', errors='ignore').strip()
        except Exception:
            return None

        if not line:
            continue

        buf += line + '\n'

        if '??' in line:
            return None

        if '$A_CARD_STOP$' in line:
            break

    return parse_block(buf)


def parse_block(text):
    """Parse a $A_CARD_START$...$A_CARD_STOP$ block into a dict.

    Keys: blk7, wiedata, bits, bit, fc, id, hex.
    Returns None if Blk7# is missing.
    """
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('Blk7#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['blk7'] = val[:16].zfill(16)
        elif line.startswith('wiedata#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['wiedata'] = val
        elif line.startswith('Bit#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            try:
                result['bit'] = int(val)
            except ValueError:
                pass
        elif line.startswith('Bits#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['bits'] = val
        elif line.startswith('FC#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            try:
                result['fc'] = int(val)
            except ValueError:
                pass
        elif line.startswith('ID#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            try:
                result['id'] = int(val)
            except ValueError:
                pass
        elif line.startswith('Hex#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['hex'] = val

    if 'blk7' not in result:
        return None
    return result


def write_to_card(blk7_hex):
    """Write SE data derived from Blk7 to an iClass tag.

    Uses iclasswrite.make_se_data + writeDataBlocks with ICLASS_LEGACY (17).
    Returns True on success (ret == 0), False otherwise.
    """
    if iclasswrite is None:
        return False
    try:
        se_data = iclasswrite.make_se_data(blk7_hex)
        ret = iclasswrite.writeDataBlocks(17, se_data, '2020666666668888')
        return ret == 0
    except Exception:
        return False