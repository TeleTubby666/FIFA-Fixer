#!/usr/bin/env python3
"""
Self-test for fifafixer.py (and, if a C compiler is found, c/fifafix.c).

No game file is needed: the test builds a synthetic file that only contains
the two short byte patterns the patcher looks for, surrounded by random bytes.

    python tests/test_patcher.py
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fifafixer as fix  # noqa: E402

TAIL_B = bytes.fromhex("10 8B 44 24 1C 29 44 24 20 81 44 24 20 A5 A5 A5 A5 9D 61 C3")
HEAD_B = bytes.fromhex("0F A2 80 E4 0F 80 FC 04")
SITE_B_ORIG = HEAD_B + b"\x74" + TAIL_B
SITE_B_DONE = HEAD_B + b"\xEB" + TAIL_B


def site_a(patched, operand=b"\x9a\xc3\x09\x00", rel=b"\xce\xfe\xff\xff"):
    store = b"\x90" * 6 if patched else b"\x89\x0d" + operand
    return store + b"\x05\xa5\xa5\xa5\xa5\xe8" + rel


def build(patched_a=False, patched_b=False, gap=0x99 - 0x1d, extra_a=0, extra_b=0, seed=1,
          name=b"fifa.exe\x00", lead=5000):
    """Synthetic 'EXE': MZ header, noise, site B, small gap, site A, noise."""
    rnd = random.Random(seed)

    def noise(n):
        # random bytes that cannot accidentally form one of our patterns
        return bytes(rnd.choice(range(0x20, 0x70)) for _ in range(n))

    parts = [b"MZ", noise(lead)]
    for _ in range(extra_b):
        parts += [SITE_B_ORIG, noise(300)]
    parts += [SITE_B_DONE if patched_b else SITE_B_ORIG, noise(gap)]
    parts += [site_a(patched_a)]
    parts += [noise(0xF8 - 0xA9), name]      # the loader stores its file name 0xF8 bytes behind site B
    for _ in range(extra_a):
        parts += [noise(300), site_a(False)]
    parts += [noise(7000)]
    return b"".join(parts)


class PatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fifafixer_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    # ----- analysis ------------------------------------------------------
    def test_unpatched_detected(self):
        self.assertEqual(fix.analyse(build())[0], "unpatched")

    def test_patched_detected(self):
        self.assertEqual(fix.analyse(build(True, True))[0], "patched")

    def test_mixed_detected(self):
        self.assertEqual(fix.analyse(build(True, False))[0], "mixed")
        self.assertEqual(fix.analyse(build(False, True))[0], "mixed")

    def test_unknown_when_missing(self):
        self.assertEqual(fix.analyse(b"MZ" + bytes(10000))[0], "unknown")

    def test_unknown_when_ambiguous(self):
        self.assertEqual(fix.analyse(build(extra_a=1))[0], "unknown")
        self.assertEqual(fix.analyse(build(extra_b=1))[0], "unknown")

    def test_unknown_when_layout_wrong(self):
        self.assertEqual(fix.analyse(build(gap=0x2000))[0], "unknown")

    def test_address_operand_is_wildcard(self):
        # another build of the game has other absolute addresses
        data = build().replace(b"\x89\x0d\x9a\xc3\x09\x00", b"\x89\x0d\x12\x34\x0b\x00")
        self.assertEqual(fix.analyse(data)[0], "unpatched")

    def test_loader_filename(self):
        data = build(name=b"fifacd.exe\x00")
        _, a, b, _ = fix.analyse(data)
        self.assertEqual(fix.loader_filename(data, b), "fifacd.exe")
        self.assertEqual(fix.loader_filename(build(name=b"FIFA4MB.EXE\x00"), b), "FIFA4MB.EXE")
        self.assertIsNone(fix.loader_filename(build(name=b"TOOLONGNAME.EXE\x00"), b))  # not an 8.3 name
        self.assertIsNone(fix.loader_filename(build(name=b"\x00" * 12), b))
        self.assertIsNone(fix.loader_filename(build(name=b"no name here"), b))

    # ----- patch result --------------------------------------------------
    def test_exactly_seven_bytes_change(self):
        data = build()
        _, a, b, _ = fix.analyse(data)
        out = fix.apply_patch(data, a, b)
        diff = [i for i in range(len(data)) if data[i] != out[i]]
        self.assertEqual(len(out), len(data))
        self.assertEqual(len(diff), 7)
        self.assertEqual(out[a:a + 6], b"\x90" * 6)
        self.assertEqual(out[b + 8], 0xEB)
        self.assertEqual(fix.analyse(out)[0], "patched")

    # ----- command line --------------------------------------------------
    def run_cli(self, *args):
        saved = sys.stdout
        try:
            with open(os.devnull, "w") as null:
                sys.stdout = null
                saved_err = sys.stderr
                sys.stderr = null
                try:
                    return fix.main(list(args))
                finally:
                    sys.stderr = saved_err
        finally:
            sys.stdout = saved

    def test_cli_inplace_with_backup(self):
        original = build()
        path = self.write("GAME.EXE", original)
        self.assertEqual(self.run_cli(path), 0)
        self.assertEqual(self.read(path + ".orig"), original)
        self.assertEqual(fix.analyse(self.read(path))[0], "patched")
        # second run: nothing to do, still success, backup untouched
        self.assertEqual(self.run_cli(path), 0)
        self.assertEqual(self.read(path + ".orig"), original)

    def test_cli_check_changes_nothing(self):
        original = build()
        path = self.write("GAME.EXE", original)
        self.assertEqual(self.run_cli("--check", path), 0)
        self.assertEqual(self.read(path), original)
        self.assertFalse(os.path.exists(path + ".orig"))

    def test_cli_output_file(self):
        original = build()
        path = self.write("GAME.EXE", original)
        out = os.path.join(self.tmp, "OUT.EXE")
        self.assertEqual(self.run_cli("-o", out, path), 0)
        self.assertEqual(self.read(path), original)
        self.assertEqual(fix.analyse(self.read(out))[0], "patched")

    def test_cli_refuses_existing_backup(self):
        original = build()
        path = self.write("GAME.EXE", original)
        self.write("GAME.EXE.orig", b"precious")
        self.assertEqual(self.run_cli(path), 1)
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.read(path + ".orig"), b"precious")

    def test_cli_refuses_foreign_and_mixed(self):
        junk = self.write("JUNK.BIN", os.urandom(5000))
        self.assertEqual(self.run_cli(junk), 1)
        mixed_data = build(True, False)
        mixed = self.write("MIXED.EXE", mixed_data)
        self.assertEqual(self.run_cli(mixed), 1)
        self.assertEqual(self.read(mixed), mixed_data)

    def test_cli_missing_file(self):
        self.assertEqual(self.run_cli(os.path.join(self.tmp, "nope.exe")), 1)


def sound_exe(size, patched=False, mutate=None):
    """Synthetic file of a known build size with the four cipher bytes at their offsets."""
    _, offs, orig, delta = fix.SOUND_BUILDS[size]
    data = bytearray(random.Random(size).getrandbits(8 * size).to_bytes(size, "little"))
    data[:2] = b"MZ"
    for i, (o, c) in enumerate(zip(offs, orig)):
        data[o] = c ^ delta[i] if patched else c
    if mutate:
        data[offs[mutate]] ^= 0x55
    return bytes(data)


class SoundFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fsndtest_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def run_cli(self, *args):
        with open(os.devnull, "w") as null:
            so, se = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = null
            try:
                return fix.main(list(args))
            finally:
                sys.stdout, sys.stderr = so, se

    def test_states(self):
        for size in fix.SOUND_BUILDS:
            self.assertEqual(fix.sound_analyse(sound_exe(size))[0], "unpatched")
            self.assertEqual(fix.sound_analyse(sound_exe(size, True))[0], "patched")
            self.assertEqual(fix.sound_analyse(sound_exe(size, mutate=2))[0], "mixed")
        self.assertEqual(fix.sound_analyse(bytes(fix.FLOPPY_SIZE))[0], "notneeded")
        self.assertEqual(fix.sound_analyse(bytes(1234))[0], "unknown")

    def test_plain_bytes_become_nops(self):
        # decrypting with the key stream is not possible here, but the delta must be
        # the per-build xor delta for each of the four bytes
        for size in fix.SOUND_BUILDS:
            data = sound_exe(size)
            _, offs, orig, delta = fix.SOUND_BUILDS[size]
            out = fix.apply_changes(data, fix.sound_changes(data, offs))
            diff = [i for i in range(len(data)) if data[i] != out[i]]
            self.assertEqual(diff, sorted(offs))
            for i, o in enumerate(offs):
                self.assertEqual(out[o] ^ data[o], delta[i])

    def test_cli_sound_only(self):
        for size in fix.SOUND_BUILDS:
            data = sound_exe(size)
            path = self.write("S%d.EXE" % size, data)
            self.assertEqual(self.run_cli("--sound", path), 0)
            out = self.read(path)
            self.assertEqual(fix.sound_analyse(out)[0], "patched")
            self.assertEqual(sum(a != b for a, b in zip(data, out)), 4)
            self.assertEqual(self.read(path + ".orig"), data)
            self.assertEqual(self.run_cli("--sound", path), 0)       # idempotent
            self.assertEqual(self.read(path), out)

    def test_cli_both_on_synthetic_build(self):
        # a file that has the sound layout AND the K6 sites
        size = 835860
        data = bytearray(sound_exe(size))
        blob = build(lead=100)
        pos = 0x20000
        data[pos:pos + len(blob)] = blob
        path = self.write("BOTH.EXE", bytes(data))
        self.assertEqual(self.run_cli("--all", path), 0)
        out = self.read(path)
        self.assertEqual(fix.analyse(out)[0], "patched")
        self.assertEqual(fix.sound_analyse(out)[0], "patched")
        self.assertEqual(sum(a != b for a, b in zip(data, out)), 7 + 4)

    def test_cli_k6_only_leaves_sound_alone(self):
        size = 835860
        data = bytearray(sound_exe(size))
        data[0x20000:0x20000 + len(build(lead=100))] = build(lead=100)
        path = self.write("K6.EXE", bytes(data))
        self.assertEqual(self.run_cli("--k6", path), 0)
        out = self.read(path)
        self.assertEqual(fix.analyse(out)[0], "patched")
        self.assertEqual(fix.sound_analyse(out)[0], "unpatched")

    def test_default_without_terminal_is_k6_only(self):
        data = bytearray(sound_exe(835860))
        data[0x20000:0x20000 + len(build(lead=100))] = build(lead=100)
        path = self.write("DEF.EXE", bytes(data))
        self.assertEqual(self.run_cli(path), 0)
        out = self.read(path)
        self.assertEqual(fix.analyse(out)[0], "patched")
        self.assertEqual(fix.sound_analyse(out)[0], "unpatched")

    def test_cli_refusals(self):
        junk = self.write("J.EXE", os.urandom(3000))
        self.assertEqual(self.run_cli("--sound", junk), 1)
        mixed = sound_exe(856244, mutate=1)
        path = self.write("M.EXE", mixed)
        self.assertEqual(self.run_cli("--sound", path), 1)
        self.assertEqual(self.read(path), mixed)
        # floppy size: nothing to do, success, file untouched
        fl = bytes(fix.FLOPPY_SIZE)
        path = self.write("F.EXE", fl)
        self.assertEqual(self.run_cli("--sound", path), 0)
        self.assertEqual(self.read(path), fl)

    def test_check_never_writes(self):
        data = sound_exe(856244)
        path = self.write("C.EXE", data)
        self.assertEqual(self.run_cli("--check", path), 0)
        self.assertEqual(self.read(path), data)

    def test_menu_choices(self):
        size = 835860
        data = bytearray(sound_exe(size))
        data[0x20000:0x20000 + len(build(lead=100))] = build(lead=100)
        original = bytes(data)
        for answer, (k6, snd) in {"1": (True, False), "2": (False, True), "3": (True, True)}.items():
            path = self.write("MENU.EXE", original)
            if os.path.exists(path + ".orig"):
                os.unlink(path + ".orig")
            import builtins
            saved = builtins.input
            builtins.input = lambda prompt="", a=answer: a
            tty = fix.sys.stdin, fix.sys.stdout
            try:
                class FakeTTY(object):
                    def isatty(self):
                        return True
                    def write(self, s):
                        pass
                    def flush(self):
                        pass
                    def read(self, *a):
                        return ""
                fix.sys.stdin, fix.sys.stdout = FakeTTY(), FakeTTY()
                rc = fix.main([path])
            finally:
                fix.sys.stdin, fix.sys.stdout = tty
                builtins.input = saved
            out = self.read(path)
            self.assertEqual(rc, 0, answer)
            self.assertEqual(fix.analyse(out)[0], "patched" if k6 else "unpatched", answer)
            self.assertEqual(fix.sound_analyse(out)[0], "patched" if snd else "unpatched", answer)


@unittest.skipUnless(shutil.which("cc") or shutil.which("gcc"), "no C compiler found")
class CVersionTests(unittest.TestCase):
    """The C version (the DOS tool) must behave exactly like the Python version.

    It is built twice: normally, and with a 64-byte read buffer so that the patterns
    are forced across block borders in many different alignments.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fifafixer_c_")
        cc = shutil.which("cc") or shutil.which("gcc")
        src = os.path.join(ROOT, "c", "fifafix.c")
        cls.exe = os.path.join(cls.tmp, "fifafix")
        cls.exe_small = os.path.join(cls.tmp, "fifafix_small")
        flags = ["-std=c89", "-pedantic", "-Wall", "-Wextra", "-O2", "-D_POSIX_C_SOURCE=200112L"]
        subprocess.check_call([cc] + flags + ["-o", cls.exe, src])
        subprocess.check_call([cc] + flags + ["-DBUF_SIZE=64u", "-o", cls.exe_small, src])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def run_c(self, exe, *args):
        return subprocess.run([exe] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, stdin=subprocess.DEVNULL).returncode

    def py_patch(self, out, src):
        return subprocess.run([sys.executable, os.path.join(ROOT, "fifafixer.py"), "-o", out, src],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def test_same_result_as_python(self):
        out_py = os.path.join(self.tmp, "out_py.exe")
        out_c = os.path.join(self.tmp, "out_c.exe")
        for lead in (5000, 8192 - 40, 8192 - 5, 8192 + 3):
            for exe in (self.exe, self.exe_small):
                data = build(lead=lead)
                src = self.write("in.exe", data)
                for p in (out_c, out_py):
                    if os.path.exists(p):
                        os.unlink(p)
                self.assertEqual(self.run_c(exe, "-o", out_c, src), 0, lead)
                self.assertEqual(self.py_patch(out_py, src), 0)
                self.assertEqual(self.read(out_c), self.read(out_py), lead)

    def test_small_buffer_all_alignments(self):
        """Site B/A start at every possible offset inside a 64-byte block."""
        for lead in range(5000, 5064):
            data = build(lead=lead)
            src = self.write("al.exe", data)
            out = os.path.join(self.tmp, "al_out.exe")
            if os.path.exists(out):
                os.unlink(out)
            self.assertEqual(self.run_c(self.exe_small, "-o", out, src), 0, lead)
            _, a, b, _ = fix.analyse(data)
            self.assertEqual(self.read(out), fix.apply_patch(data, a, b), lead)

    def test_same_refusals_as_python(self):
        cases = {
            "junk": os.urandom(4000),
            "mixed": build(True, False),
            "ambiguous": build(extra_a=1),
            "layout": build(gap=0x2000),
        }
        for exe in (self.exe, self.exe_small):
            for name, data in cases.items():
                src = self.write(name + ".exe", data)
                self.assertEqual(self.run_c(exe, "--check", src), 1, name)
                self.assertEqual(self.read(src), data, name)

    def test_c_prints_loader_filename(self):
        src = self.write("name.exe", build(name=b"fifacd.exe\x00"))
        out = subprocess.run([self.exe, "--check", src], stdout=subprocess.PIPE,
                             stdin=subprocess.DEVNULL).stdout.decode()
        self.assertIn("'fifacd.exe'", out)

    def test_c_backup_uses_dos_name(self):
        original = build()
        src = self.write("GAME.EXE", original)
        self.assertEqual(self.run_c(self.exe, src), 0)
        self.assertEqual(self.read(os.path.join(self.tmp, "GAME.ORG")), original)
        self.assertEqual(fix.analyse(self.read(src))[0], "patched")
        # a second backup would overwrite the first one: refused, file untouched
        src2 = self.write("GAME2.EXE", original)
        self.write("GAME2.ORG", b"precious")
        self.assertEqual(self.run_c(self.exe, src2), 1)
        self.assertEqual(self.read(src2), original)
        self.assertEqual(self.read(os.path.join(self.tmp, "GAME2.ORG")), b"precious")

    def test_c_refuses_same_output_file(self):
        original = build()
        src = self.write("SAME.EXE", original)
        self.assertEqual(self.run_c(self.exe, "-o", src, src), 1)
        self.assertEqual(self.read(src), original)

    def test_c_patched_is_idempotent(self):
        src = self.write("idem.exe", build())
        self.assertEqual(self.run_c(self.exe, src), 0)
        first = self.read(src)
        self.assertEqual(self.run_c(self.exe, src), 0)
        self.assertEqual(self.read(src), first)

    def run_c_in(self, exe, answer, *args):
        return subprocess.run([exe] + list(args), input=answer.encode(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).returncode

    def combo(self, patched_snd=False):
        data = bytearray(sound_exe(835860, patched_snd))
        blob = build(lead=100)
        data[0x20000:0x20000 + len(blob)] = blob
        return bytes(data)

    def test_c_sound_same_as_python(self):
        for size in fix.SOUND_BUILDS:
            data = sound_exe(size)
            for exe in (self.exe, self.exe_small):
                src = self.write("snd.exe", data)
                out_c = os.path.join(self.tmp, "snd_c.exe")
                if os.path.exists(out_c):
                    os.unlink(out_c)
                self.assertEqual(self.run_c(exe, "--sound", "-o", out_c, src), 0)
                _, offs, _, _ = fix.SOUND_BUILDS[size]
                self.assertEqual(self.read(out_c), fix.apply_changes(data, fix.sound_changes(data, offs)))

    def test_c_all_and_k6_and_default(self):
        data = self.combo()
        src = self.write("combo.exe", data)
        out = os.path.join(self.tmp, "combo_out.exe")
        for opts, k6, snd in ((["--all"], True, True), (["--k6"], True, False),
                              (["--sound"], False, True), ([], True, False)):
            if os.path.exists(out):
                os.unlink(out)
            self.assertEqual(self.run_c(self.exe, *(opts + ["-o", out, src])), 0, opts)
            res = self.read(out)
            self.assertEqual(fix.analyse(res)[0], "patched" if k6 else "unpatched", opts)
            self.assertEqual(fix.sound_analyse(res)[0], "patched" if snd else "unpatched", opts)

    def test_c_sound_refusals(self):
        mixed = sound_exe(856244, mutate=3)
        src = self.write("m.exe", mixed)
        self.assertEqual(self.run_c(self.exe, "--sound", src), 1)
        self.assertEqual(self.read(src), mixed)
        junk = self.write("j.exe", os.urandom(3000))
        self.assertEqual(self.run_c(self.exe, "--sound", junk), 1)
        fl = bytes(fix.FLOPPY_SIZE)
        src = self.write("fl.exe", fl)
        self.assertEqual(self.run_c(self.exe, "--sound", src), 0)
        self.assertEqual(self.read(src), fl)

    def test_c_sound_idempotent_and_check(self):
        src = self.write("i.exe", sound_exe(835860))
        self.assertEqual(self.run_c(self.exe, "--check", src), 0)
        self.assertEqual(self.read(src), sound_exe(835860))
        self.assertEqual(self.run_c(self.exe, "--sound", src), 0)
        first = self.read(src)
        self.assertEqual(self.run_c(self.exe, "--sound", src), 0)
        self.assertEqual(self.read(src), first)
        self.assertEqual(sum(a != b for a, b in zip(first, sound_exe(835860))), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
