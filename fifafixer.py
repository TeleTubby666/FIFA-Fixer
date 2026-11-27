#!/usr/bin/env python3
"""
fifafixer.py - FIFA-Fixer: fixes for FIFA International Soccer (DOS, EA 1993/94)

Fixes for your own FIFA.EXE:

  K6 fix     The game crashes right after start on AMD K6 CPUs
                 Abnormal program termination: Memory protection fault
                 CS:EIP = 000Fh:0009C4E8h        (the address may differ)
             The loader is patched (7 bytes).

  Sound fix  The CD-ROM and "4 MB" builds do not play some in-game effects
             (for example the referee's kick-off whistle) on most sound cards.
             The game code explicitly skips two effects; 4 bytes are patched.
             (The floppy build does not have this problem.)

  Mixer      All effects as the original samples (whistle, kicks) together
             with crowd and commentary (CD-ROM) on Sound Blaster cards; in
             the floppy build the FM card keeps the music. Includes the
             sound fix where it is needed. Writes the driver
             CUST_DIG.ADV next to the EXE; choose your Sound Blaster card in
             CONFIG.EXE as usual.

The tool contains no game files. It looks for short byte patterns or known file
layouts in your own EXE; for the mixer it compares a few hundred bytes of the
(encrypted) file with what the known builds have. See README.md, docs/TECHNICAL.md
and docs/MIXER.md.

Usage:
    python fifafixer.py [FIFA.EXE]              menu: choose K6 fix, sound fix or both
    python fifafixer.py --all FIFA.EXE          apply every fix that applies, no questions
    python fifafixer.py --k6 FIFA.EXE           only the K6 fix
    python fifafixer.py --sound FIFA.EXE        only the sound fix
    python fifafixer.py --mixer FIFA.EXE        the mixer (with the sound fix)
    python fifafixer.py --k6 --mixer FIFA.EXE   K6 fix and mixer
    python fifafixer.py --check FIFA.EXE        only report, change nothing
    python fifafixer.py --all -o OUT.EXE FIFA.EXE   write a patched copy

Without a selection and without a terminal (pipe, script) only the K6 fix
is applied, like in versions before 2.0.

Public domain (Unlicense). No warranty.
"""

import argparse
import base64
import hashlib
import os
import re
import sys
import tempfile
import zlib

VERSION = "2.2"

ANY3 = rb"[\x00-\xff]{3}"
ANY4 = rb"[\x00-\xff]{4}"

# --------------------------------------------------------------------------
# Site A - self-modifying store directly in front of the instruction it modifies
#
#   89 0D <abs32>      mov  [abs32], ecx      <- overwrites the immediate of ...
#   05 A5 A5 A5 A5     add  eax, 0A5A5A5A5h   <- ... this instruction
#   E8 <rel32>         call cpu_check
#
# Patch: the six bytes of the store become NOPs.
# --------------------------------------------------------------------------
SITE_A_ORIG = re.compile(rb"\x89\x0d" + ANY3 + rb"\x00" + rb"\x05\xa5\xa5\xa5\xa5\xe8" + ANY4)
SITE_A_DONE = re.compile(rb"\x90{6}" + rb"\x05\xa5\xa5\xa5\xa5\xe8" + ANY4)
A_LEN = 6

# --------------------------------------------------------------------------
# Site B - CPU family test inside cpu_check
#
#   0F A2              cpuid
#   80 E4 0F           and  ah, 0Fh
#   80 FC 04           cmp  ah, 4
#   74 10              jz   done              <- becomes EB 10 (jmp short done)
#   8B 44 24 1C        mov  eax, [esp+1Ch]
#   29 44 24 20        sub  [esp+20h], eax
#   81 44 24 20 A5 A5 A5 A5   add dword [esp+20h], 0A5A5A5A5h
#   9D 61 C3           popf / popa / ret
# --------------------------------------------------------------------------
B_COMMON_HEAD = rb"\x0f\xa2\x80\xe4\x0f\x80\xfc\x04"
B_COMMON_TAIL = rb"\x10\x8b\x44\x24\x1c\x29\x44\x24\x20\x81\x44\x24\x20\xa5\xa5\xa5\xa5\x9d\x61\xc3"
SITE_B_ORIG = re.compile(B_COMMON_HEAD + rb"\x74" + B_COMMON_TAIL)
SITE_B_DONE = re.compile(B_COMMON_HEAD + rb"\xeb" + B_COMMON_TAIL)
B_JUMP_OFFSET = len(b"\x0f\xa2\x80\xe4\x0f\x80\xfc\x04")  # offset of the 74/EB byte

# Site A must follow site B closely (same loader blob).
MAX_DISTANCE = 0x400


# Informational only: the loader looks for its own EXE by a hard-coded name that is stored
# 0xF8 bytes behind the start of site B's pattern (same loader blob in all known builds).
NAME_OFFSET_FROM_B = 0xF8
NAME_RE = re.compile(rb"[A-Za-z0-9_\-]{1,8}\.[Ee][Xx][Ee]\x00")


def loader_filename(data, b_off):
    """Return the file name the loader searches for, or None if it cannot be read plausibly."""
    start = b_off + NAME_OFFSET_FROM_B
    m = NAME_RE.match(data[start:start + 14])
    return m.group()[:-1].decode("ascii") if m else None


def find_all(regex, data):
    # overlapping matches are irrelevant here; patterns cannot overlap themselves
    return [m.start() for m in regex.finditer(data)]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def analyse(data):
    """Return (state, a_off, b_off, message).

    state: 'unpatched', 'patched', 'mixed', 'unknown'
    """
    a_orig = find_all(SITE_A_ORIG, data)
    a_done = find_all(SITE_A_DONE, data)
    b_orig = find_all(SITE_B_ORIG, data)
    b_done = find_all(SITE_B_DONE, data)

    n_a = len(a_orig) + len(a_done)
    n_b = len(b_orig) + len(b_done)

    if n_a == 0 or n_b == 0:
        return ("unknown", None, None,
                "The loader signature was not found (site A: %d, site B: %d). "
                "This file is either not an affected build of the game, or it is a "
                "variant this tool does not know yet." % (n_a, n_b))
    if n_a != 1 or n_b != 1:
        return ("unknown", None, None,
                "Ambiguous: found %d candidate(s) for site A and %d for site B "
                "(exactly one of each is expected). Nothing was changed." % (n_a, n_b))

    a_off = (a_orig or a_done)[0]
    b_off = (b_orig or b_done)[0]

    if not (0 < a_off - b_off < MAX_DISTANCE):
        return ("unknown", None, None,
                "The two signatures were found, but not in the expected layout "
                "(site A at 0x%X, site B at 0x%X). Nothing was changed." % (a_off, b_off + B_JUMP_OFFSET))

    if a_orig and b_orig:
        return ("unpatched", a_off, b_off, "Both patch sites found, file is unpatched.")
    if a_done and b_done:
        return ("patched", a_off, b_off, "Both patch sites already carry the fix.")
    return ("mixed", a_off, b_off,
            "Only one of the two patch sites carries the fix (site A at file offset 0x%X, "
            "site B at 0x%X). The file was modified by something else." % (a_off, b_off + B_JUMP_OFFSET))



# ---------------------------------------------------------------------------
# K6 fix: result and changes
# ---------------------------------------------------------------------------
def k6_changes(a_off, b_off):
    """List of (offset, new_byte) of the K6 fix."""
    ch = [(a_off + i, 0x90) for i in range(A_LEN)]
    ch.append((b_off + B_JUMP_OFFSET, 0xEB))
    return ch


def apply_patch(data, a_off, b_off):
    return apply_changes(data, k6_changes(a_off, b_off))


def apply_changes(data, changes):
    out = bytearray(data)
    for off, val in changes:
        out[off] = val
    return bytes(out)


# ---------------------------------------------------------------------------
# Sound fix
#
# In the CD-ROM and "4 MB" builds the play-effect routine contains
#     cmp eax, 0Bh / jz skip      (effect 11 = kick-off whistle)
#     cmp eax, 0Ch / jz skip      (effect 12)
# i.e. the plain bytes  74 F6 ... 74 EF  at four known positions. The game image
# in the EXE is encrypted with a byte-wise XOR key stream, so one byte of the
# file can be changed without knowing the key: new = old ^ plain ^ new_plain.
# Because the positions are fixed, the fix is only offered for the known file
# sizes, and only if the ciphertext bytes match what the known build has.
# ---------------------------------------------------------------------------
# size: (name, file offsets, original ciphertext, fixed ciphertext, earlier fix or None)
#
# 4 MB build: the two jz (74 F6 / 74 EF) are replaced by NOPs. The effects are then played by
# the FM driver.
# CD-ROM build: the same two jz are replaced by NOPs, but that alone stays silent there. The
# game allocates a fixed block of 63BBh paragraphs of DOS memory for its sound data; after
# that, less than the 3942 bytes for the FM instrument bank SAMPLE.AD are left, the bank is
# not loaded and the FM driver stays mute. One more byte (cs:B8B0, 63h -> 62h) makes the block
# 4 KB smaller. A pre-release of version 2.0 used other bytes (it played two effects as samples,
# which cut off crowd and commentary, and also changed 0x7F4B/0x7F4C, see PRE_RELEASE_EXTRA).
# Such a file is recognised and converted.
SOUND_BUILDS = {
    835860: ("4 MB build",
             (0x57B85, 0x57B86, 0x57B8C, 0x57B8D),
             (0xB4, 0x16, 0xAF, 0x02),
             (0x50, 0x70, 0x4B, 0x7D),
             None),
    856244: ("CD-ROM build",
             (0x7D62, 0x5BB37, 0x5BB38, 0x5BB3E, 0x5BB3F),
             (0xB2, 0x93, 0x05, 0xE3, 0x24),
             (0xB3, 0x77, 0x63, 0x07, 0x5B),
             (0xB2, 0x93, 0x51, 0xE3, 0x50)),
}
# bytes the pre-release changed besides the ones above: (offset, pre-release, original)
PRE_RELEASE_EXTRA = {856244: ((0x7F4B, 0xE2, 0x07), (0x7F4C, 0xA9, 0x72))}
FLOPPY_SIZE = 816980          # floppy build: the effects are not skipped there
FLOPPY_NAMES = {816980: "Floppy build", 816916: "Spanish floppy build"}


def sound_analyse(data):
    """Return (state, offsets, message).

    state: 'unpatched', 'patched', 'mixed', 'notneeded', 'unknown'
    """
    size = len(data)
    if size in FLOPPY_NAMES:
        return ("notneeded", None,
                "%s: all sound effects are played, the sound fix is not needed." % FLOPPY_NAMES[size])
    if size not in SOUND_BUILDS:
        return ("unknown", None,
                "Unknown file size (%d bytes): the sound fix is only available for the known "
                "CD-ROM and 4 MB builds." % size)
    name, offs, orig, done, old = SOUND_BUILDS[size]
    cur = tuple(data[o] for o in offs)
    if cur == orig:
        return ("unpatched", offs, "%s recognised, effects 11 and 12 are skipped." % name)
    if old is not None and cur == old:
        return ("unpatched", offs, "%s recognised, with the sound fix of a pre-release; "
                "it will be replaced by the final one." % name)
    if cur == done:
        return ("patched", offs, "%s recognised, the sound fix is already applied." % name)
    return ("mixed", offs,
            "File size matches the %s, but the bytes at the patch positions are neither "
            "the original nor the fixed ones. The file was modified by something else." % name)


def sound_changes(data, offs):
    name, offs, orig, done, old = SOUND_BUILDS[len(data)]
    ch = [(o, d) for o, d in zip(offs, done) if data[o] != d]
    if old is not None and tuple(data[o] for o in offs) == old:
        ch += [(o, b) for o, a, b in PRE_RELEASE_EXTRA.get(len(data), ()) if data[o] == a]
    return ch


# ---------------------------------------------------------------------------
# Mixer
#
# The game has an eight-channel sample mode for the Gravis UltraSound only. The
# mixer driver CUST_DIG.ADV (driver/sbmix.asm) mixes eight channels for Sound
# Blaster cards; five changes in the EXE make the game use it (docs/MIXER.md):
# the card number of the driver (13) gets the eight-channel mode and the
# sampled effects, and the FIFA.CFG parser maps the Sound Blaster cards to it.
# In the floppy build the FM card keeps the music; there the routine that picks
# FM or sample for an effect is changed instead of switching the FM card off.
# For every region: the ciphertext of the original file and the XOR difference
# that is applied (plain old ^ plain new, which is the same for the ciphertext).
# ---------------------------------------------------------------------------
MIXER_BUILDS = {
    816916: (
        (0x3E2A,
         "1A37EA069BE22017D8ADD25C4022241E19EC24090F36494FEBA254A38BC86669"
         "5FCA047146B736D18DA7A051",
         "000001C523CA2C83A8E4C00D9D6DB085D3DAFE770E5867E03214108A95D1B351"
         "28C0E400FCE14376F940E002",
        ),
        (0x3F75,
         "A7AEB6286ABE6F",
         "000000007F0000",
        ),
        (0x737D,
         "63DEA251A8BCDF95CA65",
         "0000000000004E000000",
        ),
        (0x741D,
         "3B16295C716E2A954158EF230C86B10F25E4320C14F5236AEC3865B4BBB47E16"
         "8B45D06BCEF7CBAFCA65DA890C8643D37DCEF7C9AFCA65DAAFCDE673CB7C48B4"
         "6E7FA251407D2B95CA17217FAF6179A15040482B95CA1734FF6F0F49B95CEDA7"
         "C6CEAF8892542A1EF7B808190C74EDD41D8ED515C0992C71EC3F65B4BDB4DE17"
         "8B45D06BCEF7C1AFCA65DA290C8643D37DCEF7C7AFCA65DA4FCCE673CB7C48B4"
         "647FA251409D2A95CA17217FAF7779A15040E82A95CA1734FF6F1549B95CED",
         "E6E3D886D4E33A008BB6E0E73A00F25CDB390A00E7BE791E2D817744C5EAEEE0"
         "3A0072D142F551778B0D31AA00008B4FF05CA3E3C8AE75AF10803F55216925D6"
         "16B9C70456C23B00009A3767A3E7482F66611AE96700720614868FB346022BBF"
         "4C8B0DAB206689CD39353E0100F2DC7A2BAFC739BDAA4943758187A18DAEF80F"
         "B705930066A368C21F77FB8AE0E1027222C5619E3366C7ED373A00007F203860"
         "23F6CCCC2405CCCCCCBEDFAA6F3DF6CCCC2470CCCCCCBECAAA6F3FF6CCCC0F",
        ),
        (0x56CC4,
         "01CED7177EF065321A7EC625226D436E2A95C01338FF4FDB98835C2E19F9691A"
         "50A8542AF34958D9230C8653D548CED7177EF0653296B8E8153A61C52D8B45B0"
         "25ACD1EACD09E0F2E054457061086B",
         "000000000A0000000E06730022D6D13A0066898E0914A45B68C20A760814AF40"
         "0F721B66E57B2D9F2A6683E87B6C6CE5BE13287404069056A58AD91262C331D2"
         "2CC7490C940F490C35940FFF0C940F",
        ),
    ),
    816980: (
        (0x3E2A,
         "1A37EA069BE22017D8ADD25C4022241E19EC24098FF6297F73EE72B082CC6468"
         "5FCA047146B736D18DA7A051",
         "000001C523CA2C83A8E4C00D9D6DB085D3DAFE770E5867E03214108A95D1B351"
         "28C0E400FCE14376F940E002",
        ),
        (0x3F75,
         "A7AEB6286ABE6F",
         "000000007F0000",
        ),
        (0x737D,
         "63DEA251A8BCDF95CA65",
         "0000000000004E000000",
        ),
        (0x741D,
         "3B16295C716E2A954158EF238C46D13FBDA8141F1DF1216BEC3865B4BBB47E16"
         "8B45D06BCEF7CBAFCA65DA898C4623E3E582D1DAA6CE67DBAFCDE673CB7C48B4"
         "6E7FA251407D2B95CA17217F2FA11991C80C6E389CCE1535FF6F0F49B95CEDA7"
         "C6CEAF8892542A1EF7B808198CB48DE485C2F306C99D2E70EC3F65B4BDB4DE17"
         "8B45D06BCEF7C1AFCA65DA298C4623E3E582D1D4A6CE67DB4FCCE673CB7C48B4"
         "647FA251409D2A95CA17217F2FB71991C80CCE399CCE1535FF6F1549B95CED",
         "E6E3D886D4E33A008BB6E0E73A00F25CDB390A00E7BE791E2D817744C5EAEEE0"
         "3A0072D142F551778B0D31AA00008B4FF05CA3E3C8AE75AF10803F55216925D6"
         "16B9C70456C23B00009A3767A3E7482F66611AE96700720614868FB346022BBF"
         "4C8B0DAB206689CD39353E0100F2DC7A2BAFC739BDAA4943758187A18DAEF80F"
         "B705930066A368C21F77FB8AE0E1027222C5619E3366C7ED373A00007F203860"
         "23F6CCCC2405CCCCCCBEDFAA6F3DF6CCCC2470CCCCCCBECAAA6F3FF6CCCC0F",
        ),
        (0x56CD4,
         "69FA4D5AD8A3CCE670CB1C4894B6AE9851A85E5C9FACE60FF2B646239FBAC8CA"
         "389CCE67551AF10D49B95C3E639323216C436E2A95C5113C7F0F7BC8ABC8E460"
         "4D984BA76B5A49268AE19F1DD7D386",
         "000000000A0000000E06730022D6D13A0066898E0914A45B68C20A760814AF40"
         "0F721B66E57B2D9F2A6683E87B6C6CE5BE13287404069056A58AD91262C331D2"
         "2CC7490C940F490C35940FFF0C940F",
        ),
    ),
    835860: (
        (0x40E6,
         "B96642523137CA6262F0540BEBDFCE6BA331CA64B46BE7980057AEDEB529E82E"
         "FC9B9225EC62DCA437C40E06",
         "000001C523CA2C83A8E4E80D9D45B085D3DAFE450E585FE032140A8A952FB251"
         "28C0DAFEFDE14376F97EDE02",
        ),
        (0x4231,
         "F0055DC21F04B2",
         "000000007F0000",
        ),
        (0x76EE,
         "3D71753A9DA6D21389C4",
         "0000000000004E000000",
        ),
        (0x778E,
         "65B9FE37C475271302F93F0A180CF4ADF48C865639E328EFAE19F57C5F4607AA"
         "D5EA0700FBED462889C48AA1180C0671ACA6431383DC6E5FEDEC76BB2F8E3108"
         "B0D1753A7567261389B67157BB6B3D038128FC71B8DC1CB1BD4E1F805DAE941B"
         "98617863A64E2798B499593118FEA876CCE6614FED8F27F4AE1EF57C5946A7AB"
         "D5EA0700FBED4C2889C48A01180C0671ACA6431D83DC6E5F0DED76BB2F8E3108"
         "BAD1753A7587271389B67157BB7D3D0381285C70B8DC1CB1BD4E05805DAE94",
         "E6E3D88654623B008BB660663B00F25CDB390A00E7BE791E2D817744C5EAEE60"
         "3B0072D142F5D1768B0DB1AB00008B4F705DA363C9AE75AF10803F55216925D6"
         "96B8C70456423A00009A3767A367492F66611AE96700720614860FB246022BBF"
         "4C8B0D2B216689CD39B53F0100F2DC7A2BAFC739BDAA4943758187A18DAEF80F"
         "B705130166A3E8C31F77CE8AE0E1027222C5611E2766C7EDB73B00007F20E56B"
         "90986B3BE8C9A36F3B7213000002000000B67FCCCCCCBECAAA6FBFF7CCCC0F",
        ),
        (0x7B58,
         "E91C34370603E743DD1183DC6EBBAEA6FB8E969557AB",
         "2B4C173B002498BF31EA162C3B0C753EC78B00000000",
        ),
    ),
    856244: (
        (0x4151,
         "F1C290BBC54DF77CEDB7779A233B3C925FCF351B8BF4A83FD33E9AC4386FCBBF"
         "B43F40CC1818E1BAB8832D97",
         "000001C523CA2C83A8E4E80D9D45B085D3DAFE450E585FE032140A8A952FB251"
         "28C0DAFEFDE14376F97EDE02",
        ),
        (0x429C,
         "61CDB930E6F84C",
         "000000007F0000",
        ),
        (0x7AD3,
         "56C42F97CB8DC7198C46",
         "0000000000004E000000",
        ),
        (0x7B73,
         "0E0CA49AC2243219077B2E508844507F1D78FC6BA7AC8FBC07CD1F09E51BA97D"
         "BE5F5DADADC623588C46CB818844A2A34552395E6793C90C44389CCE95D39FDF"
         "AB1E2F97234C33198C3430772B53E3D168DC864C2693BBE2149A858FE7F33ACC"
         "F3D4229E8A653292B14B621188B60CA425121B7273C080A707CA1F09E31B097C"
         "BE5F5DADADC629588C46CB218844A2A3455239506793C90CA4399CCE95D39FDF"
         "A11E2F9723AC32198C3430772B65E3D168DC264D2693BBE2149ABF8FE7F33A",
         "E6E3D886044841008BB6304C4100F25CDB390A00E7BE791E2D817744C5EAEE10"
         "410072D142F5A10C8B0DE1D100008B4F2027A313B3AE75AF10803F55216925D6"
         "E6C2C70456324000009A3767A317332F66611AE96700720614867FC846022BBF"
         "4C8B0D7B5B6689CD39E5450100F2DC7A2BAFC739BDAA4943758187A18DAEF80F"
         "B705637B66A398B91F77CE8AE0E1027222C5616E5D66C7EDC74100007F20E56B"
         "E0E21B41E8C9A31F417213000002000000B67FCCCCCCBECAAA6FEF8DCCCC0F",
        ),
        (0x7F3D,
         "A83C5404A2D10EB7A75C6793C9E8077211FB9CB2F97C",
         "2B3C9D40002498BF319A6CDC400C753EC78B00000000",
        ),
    ),
}


def _mixer_regions(size):
    return [(off, bytes.fromhex(c), bytes.fromhex(d)) for off, c, d in MIXER_BUILDS[size]]


def mixer_analyse(data):
    """Return (state, message); call it on the file with the sound fix applied.

    state: 'unpatched', 'patched', 'mixed', 'unknown'
    """
    size = len(data)
    if size not in MIXER_BUILDS:
        return ("unknown", "Unknown file size: the mixer is only available for the known builds.")
    name = SOUND_BUILDS[size][0] if size in SOUND_BUILDS else FLOPPY_NAMES[size]
    n_orig = n_done = 0
    regions = _mixer_regions(size)
    for off, orig, delta in regions:
        cur = data[off:off + len(orig)]
        if cur == orig:
            n_orig += 1
        elif cur == bytes(a ^ b for a, b in zip(orig, delta)):
            n_done += 1
    if n_orig == len(regions):
        return ("unpatched", "%s recognised, the mixer can be installed." % name)
    if n_done == len(regions):
        return ("patched", "%s recognised, the mixer is installed." % name)
    return ("mixed", "File size matches the %s, but the bytes for the mixer are neither the "
            "original nor the patched ones. The file was modified by something else." % name)


def mixer_changes(data):
    ch = []
    for off, orig, delta in _mixer_regions(len(data)):
        ch += [(off + i, data[off + i] ^ x) for i, x in enumerate(delta) if x]
    return ch


DRIVER_NAME = "CUST_DIG.ADV"
DRIVER_MARK = b"FIFA-Fixer"          # in the copyright line of the driver header

# --- DRIVER BEGIN (generated by driver/embed.py, do not edit) ---
DRIVER_SIZE = 9406
DRIVER_CRC32 = 0x52437593
DRIVER_Z = (
    "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"
)
# --- DRIVER END ---


def driver_bytes():
    drv = zlib.decompress(base64.b64decode("".join(DRIVER_Z)))
    assert len(drv) == DRIVER_SIZE and zlib.crc32(drv) & 0xFFFFFFFF == DRIVER_CRC32
    return drv


def driver_state(directory):
    """Return ('missing' | 'current' | 'other' | 'foreign', path)."""
    path = None
    for name in os.listdir(directory or "."):
        if name.upper() == DRIVER_NAME:
            path = os.path.join(directory, name)
            break
    if path is None:
        return ("missing", os.path.join(directory, DRIVER_NAME))
    with open(path, "rb") as fh:
        cur = fh.read(1 << 20)
    if cur == driver_bytes():
        return ("current", path)
    if DRIVER_MARK in cur[:200]:
        return ("other", path)          # an older version of the mixer driver
    return ("foreign", path)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def write_atomic(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".fifafixer_", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        try:
            os.chmod(tmp, os.stat(path).st_mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_backup_and_target(args, data, patched):
    target = args.output or args.exe
    if not args.output and not args.no_backup:
        backup = args.exe + ".orig"
        if os.path.exists(backup):
            print("error: backup file %s already exists. Move it away, use -o to write "
                  "a separate file, or --no-backup." % backup, file=sys.stderr)
            return 1
        write_atomic(backup, data)
        print("Backup : %s" % backup)
    write_atomic(target, patched)
    return 0


def ask_menu(k6_state, snd_state, mix_state):
    """Interactive selection. Returns (do_k6, do_sound, do_mixer) or None for quit."""
    def label(state):
        return {"unpatched": "can be applied", "patched": "already applied",
                "notneeded": "not needed for this build", "unknown": "not available for this file",
                "mixed": "file modified by something else"}[state]

    print("")
    print("Which fix do you want to apply?")
    print("  1) K6 fix     - start-up crash on AMD K6 CPUs        [%s]" % label(k6_state))
    print("  2) Sound fix  - missing effects (whistle, ...)       [%s]" % label(snd_state))
    print("  3) Mixer      - original samples on Sound Blaster    [%s]" % label(mix_state))
    print("                  (includes the sound fix)")
    print("  4) K6 fix + sound fix")
    print("  5) K6 fix + mixer")
    print("  q) Quit")
    choices = {"1": (True, False, False), "2": (False, True, False), "3": (False, True, True),
               "4": (True, True, False), "5": (True, True, True)}
    while True:
        try:
            ans = input("Your choice [1-5/q]: ").strip().lower()
        except EOFError:
            return None
        if ans in ("q", "quit", "exit", ""):
            return None
        if ans in choices:
            return choices[ans]
        print("Please enter 1, 2, 3, 4, 5 or q.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="FIFA-Fixer - fixes for FIFA International Soccer (DOS): "
                    "K6 start-up crash and missing sound effects in the CD-ROM / 4 MB builds, "
                    "and a mixer for the original samples on Sound Blaster cards. "
                    "Without --k6/--sound/--mixer/--all a menu asks which fix to apply.")
    ap.add_argument("exe", nargs="?", help="path to FIFA.EXE (default: FIFA.EXE in the current directory)")
    ap.add_argument("--k6", action="store_true", help="apply the K6 fix")
    ap.add_argument("--sound", action="store_true", help="apply the sound fix")
    ap.add_argument("--mixer", action="store_true",
                    help="install the mixer (includes the sound fix; writes CUST_DIG.ADV next to the EXE)")
    ap.add_argument("--all", action="store_true",
                    help="apply the K6 fix and the sound fix where they apply (not the mixer)")
    ap.add_argument("--check", action="store_true",
                    help="only report the state of the file, change nothing")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="write the patched file here instead of patching in place")
    ap.add_argument("--no-backup", action="store_true",
                    help="patch in place without creating FILE.orig (not recommended)")
    ap.add_argument("--version", action="version", version="FIFA-Fixer " + VERSION)
    args = ap.parse_args(argv)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if args.exe is None:
        if os.path.exists("FIFA.EXE"):
            args.exe = "FIFA.EXE"
        elif os.path.exists("fifa.exe"):
            args.exe = "fifa.exe"
        elif interactive:
            try:
                args.exe = input("Path to FIFA.EXE: ").strip().strip('"')
            except EOFError:
                args.exe = ""
            if not args.exe:
                return 1
        else:
            ap.error("no file given and no FIFA.EXE in the current directory")

    try:
        with open(args.exe, "rb") as fh:
            data = fh.read(64 * 1024 * 1024 + 1)
    except OSError as exc:
        print("error: cannot read %s: %s" % (args.exe, exc), file=sys.stderr)
        return 1
    if len(data) > 64 * 1024 * 1024:
        print("error: file is larger than 64 MB, this is not the game EXE.", file=sys.stderr)
        return 1

    print("FIFA-Fixer %s" % VERSION)
    print("File   : %s" % args.exe)
    print("Size   : %d bytes" % len(data))
    print("SHA-256: %s" % sha256(data))
    print("CRC-32 : %08X" % (zlib.crc32(data) & 0xFFFFFFFF))
    if data[:2] != b"MZ":
        print("warning: file does not start with 'MZ', it is not a DOS executable.")

    k6_state, a_off, b_off, k6_msg = analyse(data)
    snd_state, snd_offs, snd_msg = sound_analyse(data)
    # the mixer is judged on the file as it is after the sound fix
    with_snd = apply_changes(data, sound_changes(data, snd_offs)) if snd_state == "unpatched" else data
    if snd_state in ("unpatched", "patched"):
        mix_state, mix_msg = mixer_analyse(with_snd)
    elif snd_state == "notneeded":
        mix_state, mix_msg = mixer_analyse(data)
    else:
        mix_state, mix_msg = ("unknown", "Not available, the sound fix cannot be applied to this file.")
    print("")
    print("K6 fix   : %s" % k6_msg)
    print("Sound fix: %s" % snd_msg)
    print("Mixer    : %s" % mix_msg)
    out_dir = os.path.dirname(os.path.abspath(args.output or args.exe))
    if mix_state == "patched":
        dstate, dpath = driver_state(out_dir)
        print("           %s: %s" % (DRIVER_NAME, {
            "current": "present, current version",
            "other": "present, other version (run --mixer again to update it)",
            "foreign": "present, but not the mixer driver",
            "missing": "MISSING, run --mixer again to write it"}[dstate]))
    if k6_state in ("unpatched", "patched"):
        name = loader_filename(data, b_off)
        if name:
            print("The loader looks for a file named '%s' in the current directory, "
                  "so the EXE must keep exactly this name." % name)

    if args.check:
        print("\n--check: nothing was changed.")
        # exit status 1 if the file is not recognised at all or was modified by something else
        recognised = k6_state in ("unpatched", "patched") or snd_state in ("unpatched", "patched", "notneeded")
        if "mixed" in (k6_state, snd_state, mix_state) or not recognised:
            return 1
        return 0

    # --- which fixes? -----------------------------------------------------
    explicit = args.k6 or args.sound or args.mixer or args.all
    if explicit:
        want_k6 = args.k6 or args.all
        want_snd = args.sound or args.all or args.mixer
        want_mix = args.mixer
    elif interactive:
        sel = ask_menu(k6_state, snd_state, mix_state)
        if sel is None:
            print("Nothing was changed.")
            return 0
        want_k6, want_snd, want_mix = sel
    else:
        want_k6, want_snd, want_mix = True, False, False      # scripts: behave like version 1.x

    # --- refuse on anything unexpected, for the selected fixes only --------
    problems = []
    if want_k6 and k6_state in ("mixed", "unknown"):
        problems.append("K6 fix")
    if want_snd and snd_state == "mixed":
        problems.append("Sound fix")
    if want_mix and mix_state in ("mixed", "unknown"):
        problems.append("mixer")
    # a lone request for the sound fix on an unsupported file is an error; when the K6 fix
    # was requested as well, an unsupported sound fix is only skipped (the K6 fix still applies)
    if want_snd and not want_mix and not want_k6 and snd_state == "unknown":
        problems.append("Sound fix")
    if want_snd and not want_mix and want_k6 and snd_state == "unknown":
        print("Sound fix: skipped, not available for this file.")
    if problems:
        print("\nNothing was changed:")
        for p in problems:
            print("  - the %s cannot be applied to this file (see above)." % p)
        print("Restore the original FIFA.EXE (for example from the FIFA.EXE.orig backup) "
              "and run this tool again. If you are sure this is the game EXE, please report "
              "it and include the size and SHA-256 shown above (see README.md).")
        return 1

    todo = []
    patched = data
    if want_k6 and k6_state == "unpatched":
        patched = apply_changes(patched, k6_changes(a_off, b_off))
        todo.append("K6 fix")
    if want_snd and snd_state == "unpatched":
        patched = apply_changes(patched, sound_changes(patched, snd_offs))
        todo.append("sound fix")
    if want_mix and mix_state == "unpatched":
        patched = apply_changes(patched, mixer_changes(patched))
        todo.append("mixer")

    for what, want, state in (("K6 fix", want_k6, k6_state), ("sound fix", want_snd, snd_state),
                              ("mixer", want_mix, mix_state)):
        if want and state == "patched":
            print("%s: already applied." % what.capitalize())
        elif want and state == "notneeded":
            print("%s: not needed for this build." % what.capitalize())

    # the driver goes next to the patched EXE
    drv_todo = None
    if want_mix:
        dstate, dpath = driver_state(out_dir)
        if dstate == "foreign":
            print("\nNothing was changed: %s exists and is not the mixer driver. Move it away "
                  "and run this tool again." % dpath)
            return 1
        if dstate != "current":
            drv_todo = dpath

    if patched == data and drv_todo is None:
        print("Nothing to do.")
        if args.output:
            write_atomic(args.output, data)
            print("Copied unchanged to %s" % args.output)
        return 0

    n_changed = sum(1 for x, y in zip(data, patched) if x != y)
    if patched != data:
        # Self-check: the analysis agrees with what was planned.
        ok = len(patched) == len(data)
        if want_k6 and k6_state == "unpatched":
            ok = ok and analyse(patched)[0] == "patched"
        if want_snd and snd_state == "unpatched":
            ok = ok and sound_analyse(patched)[0] == "patched"
        if want_mix and mix_state == "unpatched":
            ok = ok and mixer_analyse(patched)[0] == "patched"
        if not ok:
            print("internal error: self-check failed, nothing was written.", file=sys.stderr)
            return 2
        rc = write_backup_and_target(args, data, patched)
        if rc:
            return rc
        print("Patched: %s  (%s; %d bytes changed)" % (args.output or args.exe, " + ".join(todo), n_changed))
        print("SHA-256: %s" % sha256(patched))
        print("CRC-32 : %08X" % (zlib.crc32(patched) & 0xFFFFFFFF))
    elif args.output:
        write_atomic(args.output, data)
        print("Copied unchanged to %s" % args.output)
    if drv_todo:
        write_atomic(drv_todo, driver_bytes())
        print("Driver : %s written (%d bytes)" % (drv_todo, DRIVER_SIZE))
    if want_mix:
        print("Choose your Sound Blaster card in CONFIG.EXE as usual (or keep the current setting);")
        print("with the mixer the game plays all effects as samples.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
