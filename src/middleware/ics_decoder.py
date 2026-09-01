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

    Keys: blk7, wiedata, bits, bit, fc, id, hex, sio_pacs.
    Returns None if Blk7# is missing.

    For SEOS cards, FC/CN may not be in plaintext - they must be extracted
    from the SIO PACS payload using the NN right-shift method.
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
        elif line.startswith('SIO#') or line.startswith('PACS#') or line.startswith('sio#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['sio_pacs'] = val
        elif line.startswith('SIO_CONTAINER#') or line.startswith('CONTAINER#'):
            val = line.split(':', 1)[1].strip() if ':' in line else ''
            result['sio_container'] = val

    if 'blk7' not in result:
        return None

    if 'fc' not in result or 'id' not in result:
        if 'sio_pacs' in result:
            parsed = parse_sio_pacs(result['sio_pacs'])
            if parsed['valid']:
                result['fc'] = parsed['fc']
                result['id'] = parsed['cn']
                result['raw'] = parsed['raw_26bit']
        elif 'hex' in result:
            parsed = parse_sio_pacs(result['hex'])
            if parsed['valid']:
                result['fc'] = parsed['fc']
                result['id'] = parsed['cn']
                result['raw'] = parsed['raw_26bit']
        elif 'sio_container' in result:
            parsed = parse_sio_container(result['sio_container'])
            if parsed['valid']:
                result['fc'] = parsed['fc']
                result['id'] = parsed['cn']
                result['raw'] = parsed['raw_26bit']

    return result


def extract_and_shift_wiegand(payload_bytes: bytes) -> dict:
    """Strip ASN.1 Tag 85 if present, extract padding byte NN,
    and apply right-shift to construct the Wiegand frame.

    Handles both raw decrypted payloads ([NN] [Payload]) and
    ASN.1 TLV containers with Tag 0x85 (PACS payload container).

    Args:
        payload_bytes: Raw bytes from decoder (with or without ASN.1 wrapper)

    Returns:
        Dict with keys: valid, fc, cn, shifted_hex
    """
    data = payload_bytes

    if len(data) > 2 and data[0] == 0x85:
        length = data[1]
        data = data[2:2 + length]

    if len(data) < 2:
        return {"valid": False, "fc": 0, "cn": 0, "shifted_hex": "0"}

    shift_nn = data[0]
    payload_data = data[1:]

    raw_int = int.from_bytes(payload_data, byteorder="big")
    shifted = raw_int >> shift_nn

    fc = (shifted >> 17) & 0xFF
    cn = (shifted >> 1) & 0xFFFF

    return {
        "valid": True,
        "fc": fc,
        "cn": cn,
        "shifted_hex": hex(shifted)
    }


def parse_sio_pacs(hex_string):
    """Parse SEOS SIO PACS payload to extract FC and Card Number.

    SIO PACS Wiegand Format (Black Hat Asia 2025 - Iceman & evildaemond):
        [NN] [Payload Bytes]
        NN = number of trailing zero padding bits

    Extraction:
        1. Read shift count NN = payload[0]
        2. Convert remaining bytes to integer bitstream (big-endian)
        3. Right-shift bitstream by NN bits (>> NN)
        4. Parse 26-bit Wiegand frame from result

    26-bit Wiegand Frame (H10301):
        [P_even (1b)] [FC (8b)] [CN (16b)] [P_odd (1b)]

    Args:
        hex_string: Hex string of SIO PACS payload (e.g., "061B7D0040")

    Returns:
        Dict with keys: fc, cn, raw_26bit, valid
    """
    if not hex_string:
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}

    try:
        hex_string = hex_string.strip().replace(' ', '').replace(':', '')
        if len(hex_string) < 4:
            return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}

        raw_bytes = bytes.fromhex(hex_string)
        result = extract_and_shift_wiegand(raw_bytes)
        if result['valid']:
            return {
                "fc": result['fc'],
                "cn": result['cn'],
                "raw_26bit": result['shifted_hex'],
                "valid": True
            }
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}
    except Exception:
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}


def parse_sio_container(hex_string):
    """Parse SEOS SIO ASN.1 TLV container to extract PACS payload.

    SEOS SIO Container Format (ASN.1 TLV):
        Tag 85: Encrypted PACS data (cipher bytes + 16-byte MAC)
        Tag Other: Additional data objects

    The ICS Decoder hardware performs:
        1. ISO 7816 ADF selection (00 A4 04 00...)
        2. Challenge-response mutual authentication
        3. EAX/EAX' decryption with diversified KDF key
        4. Output of decrypted PACS payload (with NN padding byte)

    This function handles raw container output from the decoder if it
    outputs the ASN.1 TLV format instead of pre-parsed PACS.

    Args:
        hex_string: Hex string of raw SIO container

    Returns:
        Dict with keys: fc, cn, raw_26bit, valid
    """
    if not hex_string:
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}

    try:
        hex_string = hex_string.strip().replace(' ', '').replace(':', '')
        data = bytes.fromhex(hex_string)
        result = extract_and_shift_wiegand(data)
        if result['valid']:
            return {
                "fc": result['fc'],
                "cn": result['cn'],
                "raw_26bit": result['shifted_hex'],
                "valid": True
            }
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}
    except Exception:
        return {"fc": 0, "cn": 0, "raw_26bit": "0", "valid": False}


def _extract_tlv_tag(data, target_tag):
    """Extract value from ASN.1 TLV encoded data.

    Simple TLV parser for SEOS SIO containers.
    Format: [Tag] [Length] [Value]

    Args:
        data: Bytes of TLV encoded data
        target_tag: Tag byte to extract (e.g., 0x85 for PACS)

    Returns:
        Bytes of the tag's value, or None if not found
    """
    i = 0
    while i < len(data) - 2:
        tag = data[i]
        length = data[i + 1]
        if length == 0x81:
            length = data[i + 2]
            value_start = i + 3
        elif length == 0x82:
            length = (data[i + 2] << 8) | data[i + 3]
            value_start = i + 4
        else:
            value_start = i + 2

        if tag == target_tag:
            return data[value_start:value_start + length]

        i = value_start + length
    return None


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


def detect_target_card():
    """Detect if a writable iClass card is present on the coil.

    Uses hf iclass rdbl to check for card presence (hf iclass info hangs
    on iCopy-X hardware due to FPGA chip mismatch).

    Returns True if a card is detected, False otherwise.
    """
    try:
        import executor
    except ImportError:
        try:
            from . import executor
        except ImportError:
            return False

    try:
        cmd = 'hf iclass rdbl --blk 00 -k 2020666666668888'
        ret = executor.startPM3Task(cmd, timeout=3000)
        if ret == -1:
            return False
        return executor.hasKeyword('block')
    except Exception:
        return False


def detect_t5577():
    """Detect if a T5577 blank is present on the LF coil.

    Uses lf t55xx detect to check for T5577 card presence.
    Returns True if T5577 detected, False otherwise.
    """
    try:
        import executor
    except ImportError:
        try:
            from . import executor
        except ImportError:
            return False

    try:
        cmd = 'lf t55xx detect'
        ret = executor.startPM3Task(cmd, timeout=5000)
        if ret == -1:
            return False
        return executor.hasKeyword('T55x7') or executor.hasKeyword('T5577')
    except Exception:
        return False


def detect_target():
    """Detect what type of blank card is on the coil/antenna.

    Returns:
        'hf_iclass' if iClass/Picopass blank detected (HF 13.56 MHz)
        'lf_t5577' if T5577 blank detected (LF 125 kHz)
        None if no supported blank detected
    """
    if detect_target_card():
        return 'hf_iclass'
    if detect_t5577():
        return 'lf_t5577'
    return None


def write_to_t5577(fc, card_id, raw_bits=None):
    """Write HID Prox credential to T5577 blank.

    Uses lf hid clone -w H10301 to let Proxmark3 handle H10301 framing
    natively with correct parity bit calculation.

    For non-26-bit formats (fc=0, cn=0), falls back to raw bitstream
    cloning via lf hid clone -r <shifted_hex> if raw_bits provided.

    Args:
        fc: Facility Code (int, 0-255)
        card_id: Card ID/Number (int, 0-65535)
        raw_bits: Optional raw shifted hex bitstream for non-26-bit formats

    Returns:
        True on success, False on failure
    """
    try:
        import executor
    except ImportError:
        try:
            from . import executor
        except ImportError:
            return False

    try:
        if int(fc) == 0 and int(card_id) == 0:
            if raw_bits:
                cmd = 'lf hid clone -r {}'.format(raw_bits)
            else:
                return False
        else:
            cmd = 'lf hid clone -w H10301 --fc {} --cn {}'.format(int(fc), int(card_id))
        ret = executor.startPM3Task(cmd, timeout=5000)
        return ret != -1
    except Exception:
        return False


def is_valid_26bit(fc, cn):
    """Check if extracted FC/CN represent a valid 26-bit Wiegand frame.

    Args:
        fc: Facility Code
        cn: Card Number

    Returns:
        True if valid 26-bit format (fc > 0 or cn > 0)
    """
    return int(fc) > 0 or int(cn) > 0


