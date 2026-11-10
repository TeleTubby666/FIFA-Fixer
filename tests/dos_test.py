#!/usr/bin/env python3
"""
End-to-end test of the DOS executable (FIFAFIX.EXE) inside DOSBox, without any game file.

    python tests/dos_test.py prepare DIR   # create synthetic test files and RUN.BAT in DIR
    ... copy FIFAFIX.EXE into DIR and run RUN.BAT in DOS / DOSBox ...
    python tests/dos_test.py verify DIR    # check what the DOS program did

The test files contain only the short byte patterns the patcher looks for, surrounded by
random bytes. Some are larger than 64 KB so that offsets beyond the 16-bit range are used.
This is what the GitHub Actions workflow runs; see .github/workflows/build.yml.
"""

import os
import random
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import fifafixer as fix  # noqa: E402
from test_patcher import build, sound_exe  # noqa: E402

RUN_BAT = "\r\n".join([
    "@ECHO OFF",
    "FIFAFIX.EXE --check TEST1.EXE > R1.TXT",
    "FIFAFIX.EXE -o OUT1.EXE TEST1.EXE > R2.TXT",
    "FIFAFIX.EXE -o OUT2.EXE BIG.EXE > R3.TXT",
    "FIFAFIX.EXE TEST3.EXE > R4.TXT",
    "FIFAFIX.EXE TEST2.EXE > R5.TXT",
    "IF NOT ERRORLEVEL 1 ECHO ok > RC5.TXT",
    "FIFAFIX.EXE JUNK.BIN > R6.TXT",
    "IF ERRORLEVEL 1 ECHO ok > RC6.TXT",
    "FIFAFIX.EXE MIXED.EXE > R7.TXT",
    "IF ERRORLEVEL 1 ECHO ok > RC7.TXT",
    "FIFAFIX.EXE --sound -o SNDOUT.EXE SND.EXE > R8.TXT",
    "FIFAFIX.EXE --all -o ALLOUT.EXE COMBO.EXE > R9.TXT",
    "ECHO done > DONE.TXT",
    "",
])


def combo():
    data = bytearray(sound_exe(835860))
    blob = build(lead=100)
    data[0x20000:0x20000 + len(blob)] = blob
    return bytes(data)


def files():
    rnd = random.Random(42)
    return {
        "TEST1.EXE": build(lead=150000),                   # needs the fix, > 64 KB
        "TEST2.EXE": build(True, True, lead=3000),         # already patched
        "TEST3.EXE": build(lead=150000, seed=2),           # patched in place (backup TEST3.ORG)
        "BIG.EXE": build(lead=700000, seed=3),             # about the size of the real game EXE
        "JUNK.BIN": bytes(rnd.randrange(256) for _ in range(30000)),
        "MIXED.EXE": build(True, False, lead=1000),
        "SND.EXE": sound_exe(856244),                      # CD-ROM build size, sound fix only
        "COMBO.EXE": combo(),                              # 4 MB size, both fixes
    }


def patched(data):
    state, a, b, _ = fix.analyse(data)
    assert state == "unpatched"
    return fix.apply_patch(data, a, b)


def prepare(directory):
    os.makedirs(directory, exist_ok=True)
    for name, data in files().items():
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(directory, "RUN.BAT"), "wb") as fh:
        fh.write(RUN_BAT.encode("ascii"))
    print("prepared", directory)


def verify(directory):
    problems = []

    def path(n):
        return os.path.join(directory, n)

    def read(n):
        try:
            with open(path(n), "rb") as fh:
                return fh.read()
        except OSError:
            problems.append("missing file " + n)
            return None

    def text(n):
        raw = read(n)
        return "" if raw is None else raw.decode("latin-1").replace("\r\n", "\n")

    def expect(cond, message):
        if not cond:
            problems.append(message)

    orig = files()

    expect(read("DONE.TXT") is not None, "RUN.BAT did not finish (DONE.TXT missing)")

    expect("unpatched" in text("R1.TXT") and "'fifa.exe'" in text("R1.TXT"),
           "--check output unexpected: " + repr(text("R1.TXT")[:200]))
    expect(read("TEST1.EXE") == orig["TEST1.EXE"], "--check or -o modified the input file")

    expect(read("OUT1.EXE") == patched(orig["TEST1.EXE"]), "OUT1.EXE is not the expected patched file")
    expect(read("OUT2.EXE") == patched(orig["BIG.EXE"]), "OUT2.EXE (big file) is not the expected patched file")
    expect(read("BIG.EXE") == orig["BIG.EXE"], "BIG.EXE was modified")

    expect(read("TEST3.EXE") == patched(orig["TEST3.EXE"]), "in-place patch of TEST3.EXE is wrong")
    expect(read("TEST3.ORG") == orig["TEST3.EXE"], "backup TEST3.ORG is not the original")

    want_crc = "%08X" % (zlib.crc32(patched(orig["BIG.EXE"])) & 0xFFFFFFFF)
    expect(("CRC-32 : " + want_crc) in text("R3.TXT"), "CRC-32 in R3.TXT does not match " + want_crc)

    expect("already carry the fix" in text("R5.TXT"), "patched file not recognised")
    expect(read("RC5.TXT") is not None, "already-patched file should end with exit code 0")
    expect(read("TEST2.EXE") == orig["TEST2.EXE"], "TEST2.EXE was modified")

    expect(read("RC6.TXT") is not None, "foreign file should end with a non-zero exit code")
    expect(read("JUNK.BIN") == orig["JUNK.BIN"], "JUNK.BIN was modified")
    expect(read("RC7.TXT") is not None, "half-patched file should end with a non-zero exit code")
    expect(read("MIXED.EXE") == orig["MIXED.EXE"], "MIXED.EXE was modified")

    snd = orig["SND.EXE"]
    offs = fix.SOUND_BUILDS[len(snd)][1]
    expect(read("SNDOUT.EXE") == fix.apply_changes(snd, fix.sound_changes(snd, offs)),
           "SNDOUT.EXE is not the expected sound-fixed file")
    c = orig["COMBO.EXE"]
    _, a, b, _ = fix.analyse(c)
    offs = fix.SOUND_BUILDS[len(c)][1]
    want = fix.apply_changes(c, fix.k6_changes(a, b) + fix.sound_changes(c, offs))
    expect(read("ALLOUT.EXE") == want, "ALLOUT.EXE (--all) is not the expected file")

    if problems:
        print("DOS test FAILED:")
        for p in dict.fromkeys(problems):
            print("  -", p)
        return 1
    print("DOS test passed: all checks OK")
    return 0


def main(argv):
    if len(argv) != 3 or argv[1] not in ("prepare", "verify"):
        print(__doc__)
        return 2
    if argv[1] == "prepare":
        prepare(argv[2])
        return 0
    return verify(argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
