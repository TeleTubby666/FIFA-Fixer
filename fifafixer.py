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

VERSION = "2.1"

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


def sound_analyse(data):
    """Return (state, offsets, message).

    state: 'unpatched', 'patched', 'mixed', 'notneeded', 'unknown'
    """
    size = len(data)
    if size == FLOPPY_SIZE:
        return ("notneeded", None,
                "Floppy build: all sound effects are played, the sound fix is not needed.")
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
    name = SOUND_BUILDS[size][0] if size in SOUND_BUILDS else "Floppy build"
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
DRIVER_SIZE = 6986
DRIVER_CRC32 = 0x666274FD
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
    print("                  (includes the sound fix, test version)")
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
        print("with the mixer the game plays all effects as samples, the FM card is not used.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
