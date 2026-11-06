#!/usr/bin/env python3
"""
fifafixer.py - FIFAFixer: fixes for FIFA International Soccer (DOS, EA 1993/94)

Two independent fixes for your own FIFA.EXE:

  K6 fix     The game crashes right after start on AMD K6 CPUs
                 Abnormal program termination: Memory protection fault
                 CS:EIP = 000Fh:0009C4E8h        (the address may differ)
             The loader is patched (7 bytes).

  Sound fix  The CD-ROM and "4 MB" builds do not play some in-game effects
             (for example the referee's kick-off whistle) on most sound cards.
             The game code explicitly skips two effects; 4 bytes are patched.
             (The floppy build does not have this problem.)

The tool contains NO game code or data. It only looks for short byte patterns
or known file layouts in your own EXE. See README.md and docs/TECHNICAL.md.

Usage:
    python fifafixer.py [FIFA.EXE]              menu: choose K6 fix, sound fix or both
    python fifafixer.py --all FIFA.EXE          apply every fix that applies, no questions
    python fifafixer.py --k6 FIFA.EXE           only the K6 fix
    python fifafixer.py --sound FIFA.EXE        only the sound fix
    python fifafixer.py --check FIFA.EXE        only report, change nothing
    python fifafixer.py --all -o OUT.EXE FIFA.EXE   write a patched copy

Without a selection and without a terminal (pipe, script) only the K6 fix
is applied, like in versions before 2.0.

Public domain (Unlicense). No warranty.
"""

import argparse
import hashlib
import os
import re
import sys
import tempfile
import zlib

VERSION = "2.0"

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
# file can be changed without knowing the key: new = old ^ plain ^ 0x90 (NOP).
# Because the positions are fixed, the fix is only offered for the known file
# sizes, and only if the four ciphertext bytes match what the known build has.
# ---------------------------------------------------------------------------
SOUND_PLAIN = (0x74, 0xF6, 0x74, 0xEF)          # jz $-8 / jz $-17
_NOP_DELTA = tuple(p ^ 0x90 for p in SOUND_PLAIN)

# size: (name, file offsets, original ciphertext bytes, xor delta ciphertext old -> new)
#
# 4 MB build: the two jz are replaced by NOPs.
# CD-ROM build: the CD build has no FM version of effects 11/12, the sample (VOC) path has to
# be used. Two plain bytes at cs:BA99 (jnz -> 2 NOPs, so the SFX0..15.VOC table is loaded for
# every card, not only for the Gravis) and the two jz displacements are changed
# (74 F6 -> 74 A2, 74 EF -> 74 9B, i.e. jump to the sample path 5F7D1 instead of returning).
SOUND_BUILDS = {
    835860: ("4 MB build",   (0x57B85, 0x57B86, 0x57B8C, 0x57B8D), (0xB4, 0x16, 0xAF, 0x02),
             _NOP_DELTA),
    856244: ("CD-ROM build", (0x7F4B, 0x7F4C, 0x5BB38, 0x5BB3F), (0x07, 0x72, 0x05, 0x24),
             (0xE5, 0xDB, 0x54, 0x74)),
}
FLOPPY_SIZE = 816980          # floppy build: the effects are not skipped there
SOUND_LEN = 4


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
    name, offs, orig, delta = SOUND_BUILDS[size]
    cur = tuple(data[o] for o in offs)
    done = tuple(o ^ d for o, d in zip(orig, delta))
    if cur == orig:
        return ("unpatched", offs, "%s recognised, effects 11 and 12 are skipped." % name)
    if cur == done:
        return ("patched", offs, "%s recognised, the sound fix is already applied." % name)
    return ("mixed", offs,
            "File size matches the %s, but the bytes at the patch positions are neither "
            "the original nor the fixed ones. The file was modified by something else." % name)


def sound_changes(data, offs):
    delta = SOUND_BUILDS[len(data)][3]
    return [(o, data[o] ^ d) for o, d in zip(offs, delta)]


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


def ask_menu(k6_state, snd_state):
    """Interactive selection. Returns (do_k6, do_sound) or None for quit."""
    def label(state):
        return {"unpatched": "can be applied", "patched": "already applied",
                "notneeded": "not needed for this build", "unknown": "not available for this file",
                "mixed": "file modified by something else"}[state]

    k6_ok = k6_state == "unpatched"
    snd_ok = snd_state == "unpatched"
    print("")
    print("Which fix do you want to apply?")
    print("  1) K6 fix     - start-up crash on AMD K6 CPUs      [%s]" % label(k6_state))
    print("  2) Sound fix  - missing effects (whistle, ...)     [%s]" % label(snd_state))
    print("  3) Both")
    print("  q) Quit")
    while True:
        try:
            ans = input("Your choice [1/2/3/q]: ").strip().lower()
        except EOFError:
            return None
        if ans in ("q", "quit", "exit", ""):
            return None
        if ans in ("1", "2", "3"):
            return (ans in ("1", "3"), ans in ("2", "3"))
        print("Please enter 1, 2, 3 or q.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="FIFAFixer - fixes for FIFA International Soccer (DOS): "
                    "K6 start-up crash and missing sound effects in the CD-ROM / 4 MB builds. "
                    "Without --k6/--sound/--all a menu asks which fix to apply.")
    ap.add_argument("exe", nargs="?", help="path to FIFA.EXE (default: FIFA.EXE in the current directory)")
    ap.add_argument("--k6", action="store_true", help="apply the K6 fix")
    ap.add_argument("--sound", action="store_true", help="apply the sound fix")
    ap.add_argument("--all", action="store_true", help="apply every fix that applies to the file")
    ap.add_argument("--check", action="store_true",
                    help="only report the state of the file, change nothing")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="write the patched file here instead of patching in place")
    ap.add_argument("--no-backup", action="store_true",
                    help="patch in place without creating FILE.orig (not recommended)")
    ap.add_argument("--version", action="version", version="FIFAFixer " + VERSION)
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

    print("FIFAFixer %s" % VERSION)
    print("File   : %s" % args.exe)
    print("Size   : %d bytes" % len(data))
    print("SHA-256: %s" % sha256(data))
    print("CRC-32 : %08X" % (zlib.crc32(data) & 0xFFFFFFFF))
    if data[:2] != b"MZ":
        print("warning: file does not start with 'MZ', it is not a DOS executable.")

    k6_state, a_off, b_off, k6_msg = analyse(data)
    snd_state, snd_offs, snd_msg = sound_analyse(data)
    print("")
    print("K6 fix   : %s" % k6_msg)
    print("Sound fix: %s" % snd_msg)
    if k6_state in ("unpatched", "patched"):
        name = loader_filename(data, b_off)
        if name:
            print("The loader looks for a file named '%s' in the current directory, "
                  "so the EXE must keep exactly this name." % name)

    if args.check:
        print("\n--check: nothing was changed.")
        # exit status 1 if the file is not recognised at all or was modified by something else
        recognised = k6_state in ("unpatched", "patched") or snd_state in ("unpatched", "patched", "notneeded")
        if "mixed" in (k6_state, snd_state) or not recognised:
            return 1
        return 0

    # --- which fixes? -----------------------------------------------------
    explicit = args.k6 or args.sound or args.all
    if args.all:
        want_k6, want_snd = True, True
    elif explicit:
        want_k6, want_snd = args.k6, args.sound
    elif interactive:
        sel = ask_menu(k6_state, snd_state)
        if sel is None:
            print("Nothing was changed.")
            return 0
        want_k6, want_snd = sel
    else:
        want_k6, want_snd = True, False          # scripts: behave like version 1.x

    # --- refuse on anything unexpected, for the selected fixes only --------
    problems = []
    if want_k6 and k6_state in ("mixed", "unknown"):
        problems.append("K6 fix")
    if want_snd and snd_state == "mixed":
        problems.append("Sound fix")
    # a lone request for the sound fix on an unsupported file is an error; when both fixes
    # were requested, an unsupported sound fix is only skipped (the K6 fix still applies)
    if want_snd and not want_k6 and snd_state == "unknown":
        problems.append("Sound fix")
    if want_snd and want_k6 and snd_state == "unknown":
        print("Sound fix: skipped, not available for this file.")
    if problems:
        print("\nNothing was changed:")
        for p in problems:
            print("  - the %s cannot be applied to this file (see above)." % p)
        print("Restore the original FIFA.EXE (for example from the FIFA.EXE.orig backup) "
              "and run this tool again. If you are sure this is the game EXE, please report "
              "it and include the size and SHA-256 shown above (see README.md).")
        return 1

    changes = []
    todo = []
    if want_k6 and k6_state == "unpatched":
        changes += k6_changes(a_off, b_off)
        todo.append("K6 fix")
    if want_snd and snd_state == "unpatched":
        changes += sound_changes(data, snd_offs)
        todo.append("sound fix")

    for what, want, state in (("K6 fix", want_k6, k6_state), ("sound fix", want_snd, snd_state)):
        if want and state == "patched":
            print("%s: already applied." % what.capitalize())
        elif want and state == "notneeded":
            print("%s: not needed for this build." % what.capitalize())

    if not changes:
        print("Nothing to do.")
        if args.output:
            write_atomic(args.output, data)
            print("Copied unchanged to %s" % args.output)
        return 0

    patched = apply_changes(data, changes)

    # Self-check: exactly the planned bytes differ, and the analysis agrees.
    ok = len(patched) == len(data) and \
        sum(1 for x, y in zip(data, patched) if x != y) == len(changes)
    if want_k6 and k6_state == "unpatched":
        ok = ok and analyse(patched)[0] == "patched"
    if want_snd and snd_state == "unpatched":
        ok = ok and sound_analyse(patched)[0] == "patched"
    if not ok:
        print("internal error: self-check failed, nothing was written.", file=sys.stderr)
        return 2

    rc = write_backup_and_target(args, data, patched)
    if rc:
        return rc
    print("Patched: %s  (%s; %d bytes changed)" % (args.output or args.exe, " + ".join(todo), len(changes)))
    print("SHA-256: %s" % sha256(patched))
    print("CRC-32 : %08X" % (zlib.crc32(patched) & 0xFFFFFFFF))
    return 0


if __name__ == "__main__":
    sys.exit(main())
