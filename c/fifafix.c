/*
 * fifafix.c - FIFA-Fixer for DOS: fixes for FIFA International Soccer (EA 1993/94)
 *
 * Same tool as fifafixer.py, written to run on DOS itself. It reads the file in
 * small blocks, so it builds as a plain 16-bit real-mode DOS program (no DOS
 * extender, no memory manager, about 20 KB of RAM) and also as an ordinary
 * 32/64-bit program on any modern system.
 *
 * Fixes:
 *   K6 fix     start-up crash on AMD K6 CPUs (loader, 7 bytes)
 *   Sound fix  missing effects (kick-off whistle ...) in the CD-ROM and "4 MB" builds
 *              (4 or 5 bytes, only for the known file sizes)
 *   Mixer      all effects as the original samples on Sound Blaster cards; includes the
 *              sound fix where it is needed, writes CUST_DIG.ADV next to the EXE
 *
 * The tool contains no game files. It looks for short byte patterns or known file
 * layouts in your own EXE; for the mixer it compares a few hundred bytes of the
 * (encrypted) file with what the known builds have. See README.md, docs/TECHNICAL.md
 * and docs/MIXER.md.
 *
 * Usage:
 *   FIFAFIX                         menu: choose the fixes (FIFA.EXE)
 *   FIFAFIX FIFA.EXE                same, for the given file
 *   FIFAFIX --all FIFA.EXE          K6 fix and sound fix where they apply (not the mixer)
 *   FIFAFIX --k6 FIFA.EXE           only the K6 fix
 *   FIFAFIX --sound FIFA.EXE        only the sound fix
 *   FIFAFIX --mixer FIFA.EXE        the mixer (with the sound fix)
 *   FIFAFIX --check FIFA.EXE        only report, change nothing
 *   FIFAFIX --all -o OUT.EXE FIFA.EXE   write a patched copy
 *   FIFAFIX --no-backup FIFA.EXE    patch in place without backup (FIFA.ORG)
 * Without a selection and without a terminal (redirected input or output) only the
 * K6 fix is applied, like in versions before 2.0.
 *
 * Build (see README.md):
 *   Open Watcom, 16-bit DOS:  wcl -zq -bt=dos -ms -os fifafix.c
 *   any modern system:        cc -std=c89 -D_POSIX_C_SOURCE=200112L -O2 -o fifafix fifafix.c
 *
 * Public domain (Unlicense). No warranty.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __WATCOMC__
#include <io.h>
#else
#include <unistd.h>
#endif

#define VERSION "2.1"
#define MAX_DISTANCE 0x400UL
#define A_LEN 6
#define W (-1)                /* wildcard in a pattern */
#define MAXPAT 29u            /* length of the longest pattern */

#ifndef BUF_SIZE              /* the tests build with a tiny buffer to stress block borders */
#define BUF_SIZE 8192u
#endif

typedef char buf_size_is_large_enough[(BUF_SIZE > 2u * MAXPAT) ? 1 : -1];

/* Site A: mov [abs32],ecx / add eax,0A5A5A5A5h / call rel32 */
static const int SITE_A_ORIG[] = {
    0x89, 0x0D, W, W, W, 0x00, 0x05, 0xA5, 0xA5, 0xA5, 0xA5, 0xE8, W, W, W, W };
static const int SITE_A_DONE[] = {
    0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x05, 0xA5, 0xA5, 0xA5, 0xA5, 0xE8, W, W, W, W };
#define A_PLEN 16u

/* Site B: cpuid / and ah,0Fh / cmp ah,4 / jz|jmp short / ... / popf / popa / ret */
#define B_JUMP_OFFSET 8UL
static const int SITE_B_ORIG[] = {
    0x0F, 0xA2, 0x80, 0xE4, 0x0F, 0x80, 0xFC, 0x04, 0x74, 0x10,
    0x8B, 0x44, 0x24, 0x1C, 0x29, 0x44, 0x24, 0x20,
    0x81, 0x44, 0x24, 0x20, 0xA5, 0xA5, 0xA5, 0xA5, 0x9D, 0x61, 0xC3 };
static const int SITE_B_DONE[] = {
    0x0F, 0xA2, 0x80, 0xE4, 0x0F, 0x80, 0xFC, 0x04, 0xEB, 0x10,
    0x8B, 0x44, 0x24, 0x1C, 0x29, 0x44, 0x24, 0x20,
    0x81, 0x44, 0x24, 0x20, 0xA5, 0xA5, 0xA5, 0xA5, 0x9D, 0x61, 0xC3 };
#define B_PLEN 29u

/* The loader stores the name of its own EXE 0xF8 bytes behind the start of site B's pattern. */
#define NAME_OFFSET_FROM_B 0xF8UL

static unsigned char buf[BUF_SIZE];
static unsigned char buf2[BUF_SIZE];
static unsigned long crc_tab[256];

typedef struct {
    unsigned n_a_orig, n_a_done, n_b_orig, n_b_done;
    unsigned long a_orig, a_done, b_orig, b_done;   /* offset of the first match */
    unsigned long size;
    unsigned long crc;
} Scan;

enum { S_UNKNOWN = 0, S_UNPATCHED, S_PATCHED, S_MIXED };

/* ---------------------------------------------------------------- helpers */

static void crc_init(void)
{
    unsigned long c;
    unsigned n, k;

    for (n = 0; n < 256u; n++) {
        c = (unsigned long)n;
        for (k = 0; k < 8u; k++)
            c = (c & 1UL) ? (c >> 1) ^ 0xEDB88320UL : (c >> 1);
        crc_tab[n] = c;
    }
}

static unsigned long crc_update(unsigned long crc, const unsigned char *p, unsigned n)
{
    unsigned i;

    for (i = 0; i < n; i++)
        crc = crc_tab[(unsigned)((crc ^ p[i]) & 0xFFUL)] ^ (crc >> 8);
    return crc;
}

static int match(const unsigned char *d, const int *pat, unsigned plen)
{
    unsigned j;

    for (j = 0; j < plen; j++)
        if (pat[j] != W && d[j] != (unsigned char)pat[j])
            return 0;
    return 1;
}

static int read_at(FILE *f, unsigned long off, unsigned char *out, unsigned n)
{
    if (fseek(f, (long)off, SEEK_SET) != 0)
        return 0;
    return fread(out, 1, n, f) == (size_t)n;
}

static int write_at(FILE *f, unsigned long off, const unsigned char *d, unsigned n)
{
    if (fseek(f, (long)off, SEEK_SET) != 0)
        return 0;
    return fwrite(d, 1, n, f) == (size_t)n;
}

static void count_one(unsigned *n, unsigned long *first, unsigned long off)
{
    if (*n == 0)
        *first = off;
    if (*n < 0xFFFFu)
        (*n)++;
}

/* Read the whole file once, block by block: look for the four patterns and compute the CRC-32. */
static int scan_file(FILE *f, Scan *s)
{
    unsigned have = 0, got, i, limit, carry;
    unsigned long base = 0UL, crc = 0xFFFFFFFFUL;
    int eof = 0;

    memset(s, 0, sizeof(*s));
    if (fseek(f, 0L, SEEK_SET) != 0)
        return 0;
    while (!eof) {
        got = (unsigned)fread(buf + have, 1, BUF_SIZE - have, f);
        if (got < BUF_SIZE - have) {
            if (ferror(f))
                return 0;
            eof = 1;
        }
        crc = crc_update(crc, buf + have, got);
        s->size += (unsigned long)got;
        have += got;

        /* start positions below 'limit' are decided in this round; the rest is carried over */
        limit = eof ? have : have - (MAXPAT - 1u);
        for (i = 0; i < limit; i++) {
            if (buf[i] != 0x89 && buf[i] != 0x90 && buf[i] != 0x0F)
                continue;
            if (i + A_PLEN <= have) {
                if (match(buf + i, SITE_A_ORIG, A_PLEN))
                    count_one(&s->n_a_orig, &s->a_orig, base + i);
                else if (match(buf + i, SITE_A_DONE, A_PLEN))
                    count_one(&s->n_a_done, &s->a_done, base + i);
            }
            if (i + B_PLEN <= have) {
                if (match(buf + i, SITE_B_ORIG, B_PLEN))
                    count_one(&s->n_b_orig, &s->b_orig, base + i);
                else if (match(buf + i, SITE_B_DONE, B_PLEN))
                    count_one(&s->n_b_done, &s->b_done, base + i);
            }
        }
        carry = have - limit;
        if (carry > 0)
            memmove(buf, buf + limit, carry);
        base += (unsigned long)limit;
        have = carry;
    }
    s->crc = crc ^ 0xFFFFFFFFUL;
    return 1;
}

static int analyse(const Scan *s, int quiet, unsigned long *a_off, unsigned long *b_off)
{
    unsigned na = s->n_a_orig + s->n_a_done;
    unsigned nb = s->n_b_orig + s->n_b_done;

    if (na == 0 || nb == 0) {
        if (!quiet)
            printf("The loader signature was not found (site A: %u, site B: %u).\n"
                   "This file is either not an affected build of the game, or it is a\n"
                   "variant this tool does not know yet.\n", na, nb);
        return S_UNKNOWN;
    }
    if (na != 1 || nb != 1) {
        if (!quiet)
            printf("Ambiguous: found %u candidate(s) for site A and %u for site B "
                   "(exactly one of each is expected).\n", na, nb);
        return S_UNKNOWN;
    }
    *a_off = s->n_a_orig ? s->a_orig : s->a_done;
    *b_off = s->n_b_orig ? s->b_orig : s->b_done;

    if (!(*a_off > *b_off && *a_off - *b_off < MAX_DISTANCE)) {
        if (!quiet)
            printf("The two signatures were found, but not in the expected layout "
                   "(site A at 0x%lX, site B at 0x%lX).\n",
                   *a_off, *b_off + B_JUMP_OFFSET);
        return S_UNKNOWN;
    }
    if (s->n_a_orig && s->n_b_orig) {
        if (!quiet)
            printf("Both patch sites found, file is unpatched.\n");
        return S_UNPATCHED;
    }
    if (s->n_a_done && s->n_b_done) {
        if (!quiet)
            printf("Both patch sites already carry the fix.\n");
        return S_PATCHED;
    }
    if (!quiet)
        printf("Only one of the two patch sites carries the fix (site A at file offset "
               "0x%lX, site B at 0x%lX). The file was modified by something else.\n",
               *a_off, *b_off + B_JUMP_OFFSET);
    return S_MIXED;
}

/* ------------------------------------------------------------- sound fix
 *
 * The CD-ROM and "4 MB" builds skip effects 11 (kick-off whistle) and 12 in the play-effect
 * routine: cmp eax,0Bh / jz skip / cmp eax,0Ch / jz skip, i.e. the plain bytes 74 F6 .. 74 EF.
 * The game image is encrypted with a byte-wise XOR key stream, so a byte of the file can be
 * changed without knowing the key: new = old ^ plain ^ new_plain. The positions are fixed,
 * therefore the fix exists only for the known file sizes and only if the bytes match.
 */
#define SOUND_LEN 5u               /* largest number of positions of a build */
#define FLOPPY_SIZE 816980UL

/*
 * 4 MB build: the two jz (74 F6 / 74 EF) become NOPs, the effects are played by the FM driver.
 * CD-ROM build: the same, plus one byte: the game allocates a fixed block of 63BBh paragraphs
 * of DOS memory for its sound data, after that there is no room for the FM instrument bank
 * SAMPLE.AD and the FM driver stays mute. The byte at cs:B8B0 (63h -> 62h) makes the block
 * 4 KB smaller. A pre-release of version 2.0 used other bytes ("old", and two more, see
 * PRE_EXTRA), such a file is recognised and converted.
 */
typedef struct {
    unsigned long size;
    const char *name;
    unsigned n;                        /* number of positions */
    unsigned long off[SOUND_LEN];
    unsigned char orig[SOUND_LEN];     /* original ciphertext */
    unsigned char done[SOUND_LEN];     /* fixed ciphertext */
    int has_old;
    unsigned char old[SOUND_LEN];      /* fix of a pre-release */
} SoundBuild;

static const SoundBuild SOUND_BUILDS[] = {
    { 835860UL, "4 MB build", 4u,
      { 0x57B85UL, 0x57B86UL, 0x57B8CUL, 0x57B8DUL, 0UL },
      { 0xB4, 0x16, 0xAF, 0x02, 0 },
      { 0x50, 0x70, 0x4B, 0x7D, 0 },
      0, { 0, 0, 0, 0, 0 } },
    { 856244UL, "CD-ROM build", 5u,
      { 0x7D62UL, 0x5BB37UL, 0x5BB38UL, 0x5BB3EUL, 0x5BB3FUL },
      { 0xB2, 0x93, 0x05, 0xE3, 0x24 },
      { 0xB3, 0x77, 0x63, 0x07, 0x5B },
      1, { 0xB2, 0x93, 0x51, 0xE3, 0x50 } }
};
#define N_SOUND_BUILDS 2u

/* bytes the pre-release changed besides the ones above (CD-ROM build only) */
#define PRE_SIZE 856244UL
#define N_PRE_EXTRA 2u
static const unsigned long PRE_EXTRA_OFF[N_PRE_EXTRA] = { 0x7F4BUL, 0x7F4CUL };
static const unsigned char PRE_EXTRA_OLD[N_PRE_EXTRA] = { 0xE2, 0xA9 };
static const unsigned char PRE_EXTRA_ORIG[N_PRE_EXTRA] = { 0x07, 0x72 };

enum { N_UNKNOWN = 0, N_UNPATCHED, N_PATCHED, N_MIXED, N_NOTNEEDED };

static int is_prerelease;           /* set by sound_analyse */

/* Examine the sound-fix bytes of an open file of the given size. cur gets the bytes. */
static int sound_analyse(FILE *f, unsigned long size, int quiet, const SoundBuild **bld,
                         unsigned char *cur)
{
    unsigned i, k, same_orig, same_done, same_old;

    *bld = NULL;
    is_prerelease = 0;
    if (size == FLOPPY_SIZE) {
        if (!quiet)
            printf("Floppy build: all sound effects are played, the sound fix is not needed.\n");
        return N_NOTNEEDED;
    }
    for (k = 0; k < N_SOUND_BUILDS; k++)
        if (SOUND_BUILDS[k].size == size)
            break;
    if (k == N_SOUND_BUILDS) {
        if (!quiet)
            printf("Unknown file size (%lu bytes): the sound fix is only available for the "
                   "known CD-ROM and 4 MB builds.\n", size);
        return N_UNKNOWN;
    }
    *bld = &SOUND_BUILDS[k];
    same_orig = same_done = 1;
    same_old = SOUND_BUILDS[k].has_old ? 1u : 0u;
    for (i = 0; i < SOUND_BUILDS[k].n; i++) {
        if (!read_at(f, SOUND_BUILDS[k].off[i], cur + i, 1u))
            return N_UNKNOWN;
        if (cur[i] != SOUND_BUILDS[k].orig[i])
            same_orig = 0;
        if (cur[i] != SOUND_BUILDS[k].done[i])
            same_done = 0;
        if (cur[i] != SOUND_BUILDS[k].old[i])
            same_old = 0;
    }
    if (same_orig) {
        if (!quiet)
            printf("%s recognised, effects 11 and 12 are skipped.\n", SOUND_BUILDS[k].name);
        return N_UNPATCHED;
    }
    if (same_old) {
        is_prerelease = 1;
        if (!quiet)
            printf("%s recognised, with the sound fix of a pre-release; it will be replaced by "
                   "the final one.\n", SOUND_BUILDS[k].name);
        return N_UNPATCHED;
    }
    if (same_done) {
        if (!quiet)
            printf("%s recognised, the sound fix is already applied.\n", SOUND_BUILDS[k].name);
        return N_PATCHED;
    }
    if (!quiet)
        printf("File size matches the %s, but the bytes at the patch positions are neither the "
               "original nor the fixed ones. The file was modified by something else.\n",
               SOUND_BUILDS[k].name);
    return N_MIXED;
}

/* ------------------------------------------------------------------ mixer
 *
 * The game has an eight-channel sample mode for the Gravis UltraSound only. The mixer
 * driver CUST_DIG.ADV (driver/sbmix.asm) mixes eight channels for Sound Blaster cards;
 * five changes in the EXE make the game use it (docs/MIXER.md). For every region: the
 * ciphertext of the original file and the XOR difference that is applied.
 */
typedef struct {
    unsigned long size, off;
    unsigned len;
    const unsigned char *orig, *delta;
} MixRegion;

#include "mixtab.h"
#include "custdig.h"

#define MIX_MAXLEN 191u
#define DRIVER_NAME "CUST_DIG.ADV"

/* Examine the mixer regions; judged on the file as it is after the sound fix. */
static int mixer_analyse(FILE *f, unsigned long size, int quiet, int snd, const char *name)
{
    unsigned k, i, j, n = 0, n_orig = 0, n_done = 0;
    unsigned char r[MIX_MAXLEN];
    int is_orig, is_done;

    if (snd == N_MIXED || (snd == N_UNKNOWN && size != FLOPPY_SIZE)) {
        if (!quiet)
            printf("Not available, the sound fix cannot be applied to this file.\n");
        return N_UNKNOWN;
    }
    for (k = 0; k < N_MIX_REGIONS; k++) {
        if (MIX_REGIONS[k].size != size)
            continue;
        n++;
        if (!read_at(f, MIX_REGIONS[k].off, r, MIX_REGIONS[k].len))
            return N_UNKNOWN;
        if (is_prerelease && size == PRE_SIZE)
            for (j = 0; j < N_PRE_EXTRA; j++)
                if (PRE_EXTRA_OFF[j] >= MIX_REGIONS[k].off &&
                    PRE_EXTRA_OFF[j] < MIX_REGIONS[k].off + MIX_REGIONS[k].len &&
                    r[PRE_EXTRA_OFF[j] - MIX_REGIONS[k].off] == PRE_EXTRA_OLD[j])
                    r[PRE_EXTRA_OFF[j] - MIX_REGIONS[k].off] = PRE_EXTRA_ORIG[j];
        is_orig = is_done = 1;
        for (i = 0; i < MIX_REGIONS[k].len; i++) {
            if (r[i] != MIX_REGIONS[k].orig[i])
                is_orig = 0;
            if (r[i] != (unsigned char)(MIX_REGIONS[k].orig[i] ^ MIX_REGIONS[k].delta[i]))
                is_done = 0;
        }
        n_orig += (unsigned)is_orig;
        n_done += (unsigned)is_done;
    }
    if (n == 0) {
        if (!quiet)
            printf("Unknown file size: the mixer is only available for the known builds.\n");
        return N_UNKNOWN;
    }
    if (n_orig == n) {
        if (!quiet)
            printf("%s recognised, the mixer can be installed.\n", name);
        return N_UNPATCHED;
    }
    if (n_done == n) {
        if (!quiet)
            printf("%s recognised, the mixer is installed.\n", name);
        return N_PATCHED;
    }
    if (!quiet)
        printf("File size matches the %s, but the bytes for the mixer are neither the original "
               "nor the patched ones. The file was modified by something else.\n", name);
    return N_MIXED;
}

/* bytes the mixer changes (all regions of the build) */
static unsigned long mixer_count(unsigned long size)
{
    unsigned k, i;
    unsigned long n = 0;

    for (k = 0; k < N_MIX_REGIONS; k++)
        if (MIX_REGIONS[k].size == size)
            for (i = 0; i < MIX_REGIONS[k].len; i++)
                if (MIX_REGIONS[k].delta[i])
                    n++;
    return n;
}

static int mixer_apply(const char *path, unsigned long size)
{
    FILE *f = fopen(path, "r+b");
    unsigned char r[MIX_MAXLEN];
    unsigned k, i;
    int ok = 1;

    if (!f)
        return 0;
    for (k = 0; k < N_MIX_REGIONS && ok; k++) {
        if (MIX_REGIONS[k].size != size)
            continue;
        ok = read_at(f, MIX_REGIONS[k].off, r, MIX_REGIONS[k].len);
        for (i = 0; ok && i < MIX_REGIONS[k].len; i++)
            r[i] ^= MIX_REGIONS[k].delta[i];
        ok = ok && write_at(f, MIX_REGIONS[k].off, r, MIX_REGIONS[k].len);
    }
    if (fclose(f) != 0)
        ok = 0;
    return ok;
}

/* the driver file next to the given EXE */
static int driver_path(const char *exe, char *out, size_t outsize)
{
    size_t len = strlen(exe), cut = 0, i;

    for (i = len; i > 0; i--)
        if (exe[i - 1] == '\\' || exe[i - 1] == '/' || exe[i - 1] == ':') {
            cut = i;
            break;
        }
    if (cut + sizeof(DRIVER_NAME) > outsize)
        return 0;
    memcpy(out, exe, cut);
    strcpy(out + cut, DRIVER_NAME);
    return 1;
}

enum { D_MISSING = 0, D_CURRENT, D_OTHER, D_FOREIGN };

static int driver_state(const char *path)
{
    FILE *f = fopen(path, "rb");
    unsigned char head[200];
    size_t n, i, pos = 0;
    int same = 1, mark = 0;

    if (!f)
        return D_MISSING;
    n = fread(head, 1, sizeof(head), f);
    for (i = 0; i + 10 <= n; i++)
        if (memcmp(head + i, "FIFA-Fixer", 10) == 0)
            mark = 1;
    /* compare the whole file with the driver, block by block */
    if (fseek(f, 0L, SEEK_SET) != 0)
        same = 0;
    while (same && (n = fread(buf, 1, BUF_SIZE, f)) > 0) {
        if (pos + n > (size_t)DRIVER_SIZE || memcmp(buf, DRIVER + pos, n) != 0)
            same = 0;
        pos += n;
    }
    if (pos != (size_t)DRIVER_SIZE)
        same = 0;
    fclose(f);
    if (same)
        return D_CURRENT;
    return mark ? D_OTHER : D_FOREIGN;
}

static int driver_write(const char *path)
{
    FILE *f = fopen(path, "wb");
    int ok;

    if (!f)
        return 0;
    ok = fwrite(DRIVER, 1, DRIVER_SIZE, f) == (size_t)DRIVER_SIZE;
    if (fclose(f) != 0)
        ok = 0;
    return ok;
}

/* Informational only: the file name the loader searches for. */
static void print_loader_name(FILE *f, unsigned long b_off)
{
    unsigned char d[14];
    char name[10];
    unsigned i = 0;
    unsigned char c;

    if (!read_at(f, b_off + NAME_OFFSET_FROM_B, d, 14u))
        return;
    while (i < 8u) {
        c = d[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-'))
            break;
        name[i] = (char)c;
        i++;
    }
    if (i == 0 || d[i] != '.')
        return;
    if ((d[i + 1] | 0x20) != 'e' || (d[i + 2] | 0x20) != 'x' ||
        (d[i + 3] | 0x20) != 'e' || d[i + 4] != 0)
        return;
    name[i] = '\0';
    printf("The loader looks for a file named '%s.exe' in the current directory, "
           "so the EXE must keep exactly this name.\n", name);
}

static int file_exists(const char *path)
{
    FILE *f = fopen(path, "rb");

    if (f) {
        fclose(f);
        return 1;
    }
    return 0;
}

static int copy_file(const char *src, const char *dst)
{
    FILE *in = fopen(src, "rb"), *out;
    size_t n;
    int ok = 1;

    if (!in)
        return 0;
    out = fopen(dst, "wb");
    if (!out) {
        fclose(in);
        return 0;
    }
    while ((n = fread(buf, 1, BUF_SIZE, in)) > 0) {
        if (fwrite(buf, 1, n, out) != n) {
            ok = 0;
            break;
        }
    }
    if (ferror(in))
        ok = 0;
    fclose(in);
    if (fclose(out) != 0)
        ok = 0;
    return ok;
}

/* Count differing bytes between two files; -1 if the sizes differ or on error. */
static long count_diffs(const char *pa, const char *pb)
{
    FILE *fa = fopen(pa, "rb"), *fb;
    size_t na, nb, i;
    long diffs = 0;

    if (!fa)
        return -1L;
    fb = fopen(pb, "rb");
    if (!fb) {
        fclose(fa);
        return -1L;
    }
    for (;;) {
        na = fread(buf, 1, BUF_SIZE, fa);
        nb = fread(buf2, 1, BUF_SIZE, fb);
        if (na != nb) {
            diffs = -1L;
            break;
        }
        if (na == 0)
            break;
        for (i = 0; i < na; i++)
            if (buf[i] != buf2[i])
                diffs++;
    }
    fclose(fa);
    fclose(fb);
    return diffs;
}

/* Backup name for DOS: replace the extension by .ORG (FIFA.EXE -> FIFA.ORG). */
static int backup_name(const char *exe, char *out, size_t outsize)
{
    size_t len = strlen(exe), cut = len, i;

    if (len + 5 > outsize)
        return 0;
    for (i = len; i > 0; i--) {
        if (exe[i - 1] == '\\' || exe[i - 1] == '/' || exe[i - 1] == ':')
            break;
        if (exe[i - 1] == '.') {
            cut = i - 1;
            break;
        }
    }
    memcpy(out, exe, cut);
    strcpy(out + cut, ".ORG");
    if (strcmp(out, exe) == 0)
        strcpy(out + cut, ".BAK");
    return 1;
}

/* Re-examine a written file: K6 state, sound state, CRC. */
static void rescan(const char *path, int *k6, int *snd, int *mix, unsigned long *crc)
{
    FILE *f = fopen(path, "rb");
    Scan s;
    unsigned long a, b;
    const SoundBuild *bld;
    unsigned char cur[SOUND_LEN];

    *k6 = S_UNKNOWN;
    *snd = N_UNKNOWN;
    *mix = N_UNKNOWN;
    if (!f)
        return;
    if (scan_file(f, &s)) {
        *k6 = analyse(&s, 1, &a, &b);
        *snd = sound_analyse(f, s.size, 1, &bld, cur);
        *mix = mixer_analyse(f, s.size, 1, *snd, "");
        *crc = s.crc;
    }
    fclose(f);
}

static int patch_in_place(const char *path, int do_k6, unsigned long a_off, unsigned long b_off,
                          int do_snd, const SoundBuild *bld, const unsigned char *snd_new)
{
    FILE *f = fopen(path, "r+b");
    unsigned char nops[A_LEN], jmp = 0xEB;
    unsigned i;
    int ok = 1;

    if (!f)
        return 0;
    for (i = 0; i < A_LEN; i++)
        nops[i] = 0x90;
    if (do_k6)
        ok = write_at(f, a_off, nops, A_LEN) && write_at(f, b_off + B_JUMP_OFFSET, &jmp, 1u);
    if (ok && do_snd) {
        for (i = 0; i < bld->n && ok; i++)
            ok = write_at(f, bld->off[i], snd_new + i, 1u);
        if (is_prerelease && bld->size == PRE_SIZE)
            for (i = 0; i < N_PRE_EXTRA && ok; i++)
                ok = write_at(f, PRE_EXTRA_OFF[i], PRE_EXTRA_ORIG + i, 1u);
    }
    if (fclose(f) != 0)
        ok = 0;
    return ok;
}

static void usage(void)
{
    printf("FIFA-Fixer " VERSION " - fixes for FIFA International Soccer (DOS)\n\n"
           "usage: FIFAFIX [--k6] [--sound] [--mixer] [--all] [--check] [--no-backup]\n"
           "               [-o OUTPUT] [FIFA.EXE]\n\n"
           "  (no selection) menu: choose the fixes\n"
           "  --k6          apply the K6 fix (start-up crash on AMD K6 CPUs)\n"
           "  --sound       apply the sound fix (missing effects in CD-ROM / 4 MB builds)\n");
    printf("  --mixer       install the mixer (original samples on Sound Blaster cards;\n"
           "                includes the sound fix, writes CUST_DIG.ADV next to the EXE)\n"
           "  --all         apply the K6 fix and the sound fix where they apply\n"
           "  --check       only report the state of the file, change nothing\n");
    printf("  -o OUTPUT     write the patched file there instead of patching in place\n"
           "  --no-backup   patch in place without creating FIFA.ORG\n");
    printf("\nWithout options the file is patched in place and the original is kept\n"
           "under the same name with the extension .ORG. Without a terminal and without a\n"
           "selection only the K6 fix is applied.\n");
}

static const char *state_label(int st)
{
    switch (st) {
    case S_UNPATCHED: return "can be applied";
    case S_PATCHED:   return "already applied";
    case S_MIXED:     return "file modified by something else";
    default:          return "not available for this file";
    }
}

static const char *n_label(int st)
{
    return (st == N_NOTNEEDED) ? "not needed for this build"
         : (st == N_UNPATCHED) ? "can be applied"
         : (st == N_PATCHED)   ? "already applied"
         : (st == N_MIXED)     ? "file modified by something else"
         :                       "not available for this file";
}

/* Interactive selection. Returns 0 to quit. */
static int ask_menu(int k6, int snd, int mix, int *want_k6, int *want_snd, int *want_mix)
{
    char line[32];
    char c;

    printf("\nWhich fix do you want to apply?\n");
    printf("  1) K6 fix     - start-up crash on AMD K6 CPUs        [%s]\n", state_label(k6));
    printf("  2) Sound fix  - missing effects (whistle, ...)       [%s]\n", n_label(snd));
    printf("  3) Mixer      - original samples on Sound Blaster    [%s]\n", n_label(mix));
    printf("                  (includes the sound fix, test version)\n");
    printf("  4) K6 fix + sound fix\n");
    printf("  5) K6 fix + mixer\n");
    printf("  q) Quit\n");
    for (;;) {
        printf("Your choice [1-5/q]: ");
        fflush(stdout);
        if (!fgets(line, sizeof(line), stdin))
            return 0;
        c = line[0];
        if (c == 'q' || c == 'Q' || c == '\n')
            return 0;
        if (c >= '1' && c <= '5') {
            *want_k6 = (c == '1' || c == '4' || c == '5');
            *want_snd = (c != '1');
            *want_mix = (c == '3' || c == '5');
            return 1;
        }
        printf("Please enter 1, 2, 3, 4, 5 or q.\n");
    }
}

/* ------------------------------------------------------------------- main */

int main(int argc, char **argv)
{
    const char *exe = NULL, *out = NULL;
    int check = 0, nobackup = 0, i, k6, snd, mix, k6_after, snd_after, mix_after;
    int opt_k6 = 0, opt_snd = 0, opt_mix = 0, opt_all = 0;
    int want_k6, want_snd, want_mix = 0, do_k6, do_snd, do_mix, dstate = D_CURRENT, need_drv = 0;
    char drvpath[260];
    int interactive;
    FILE *f;
    Scan s;
    unsigned char hdr[2], sa[A_LEN], sb[2], cur[SOUND_LEN], snd_new[SOUND_LEN];
    unsigned long a_off = 0, b_off = 0, crc2 = 0, nchanged;
    const SoundBuild *bld = NULL;
    char bak[260], pathbuf[260];
    long diffs;
    const char *ref;

    crc_init();
    interactive = isatty(fileno(stdin)) && isatty(fileno(stdout));

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--check")) {
            check = 1;
        } else if (!strcmp(argv[i], "--k6")) {
            opt_k6 = 1;
        } else if (!strcmp(argv[i], "--sound")) {
            opt_snd = 1;
        } else if (!strcmp(argv[i], "--mixer")) {
            opt_mix = 1;
        } else if (!strcmp(argv[i], "--all")) {
            opt_all = 1;
        } else if (!strcmp(argv[i], "--no-backup")) {
            nobackup = 1;
        } else if (!strcmp(argv[i], "-o") && i + 1 < argc) {
            out = argv[++i];
        } else if (!strcmp(argv[i], "--version") || !strcmp(argv[i], "-h") ||
                   !strcmp(argv[i], "--help") || !strcmp(argv[i], "/?")) {
            usage();
            return 0;
        } else if (argv[i][0] == '-' && argv[i][1] != '\0') {
            fprintf(stderr, "error: unknown option %s\n", argv[i]);
            usage();
            return 1;
        } else if (!exe) {
            exe = argv[i];
        } else {
            usage();
            return 1;
        }
    }
    if (!exe) {
        if (file_exists("FIFA.EXE")) {
            exe = "FIFA.EXE";
        } else if (file_exists("fifa.exe")) {
            exe = "fifa.exe";
        } else if (interactive) {
            printf("Path to FIFA.EXE: ");
            fflush(stdout);
            if (!fgets(pathbuf, sizeof(pathbuf), stdin))
                return 1;
            pathbuf[strcspn(pathbuf, "\r\n")] = '\0';
            if (pathbuf[0] == '\0')
                return 1;
            exe = pathbuf;
        } else {
            usage();
            return 1;
        }
    }
    if (out && !strcmp(out, exe)) {
        fprintf(stderr, "error: the output file must differ from the input file.\n");
        return 1;
    }

    f = fopen(exe, "rb");
    if (!f) {
        fprintf(stderr, "error: cannot open %s\n", exe);
        return 1;
    }
    printf("FIFA-Fixer " VERSION " - reading %s, this can take a minute on a slow PC ...\n", exe);
    fflush(stdout);
    if (!scan_file(f, &s)) {
        fprintf(stderr, "error: cannot read %s\n", exe);
        fclose(f);
        return 1;
    }
    printf("File   : %s\n", exe);
    printf("Size   : %lu bytes\n", s.size);
    printf("CRC-32 : %08lX\n", s.crc);
    if (s.size > 67108864UL) {
        fprintf(stderr, "error: file is larger than 64 MB, this is not the game EXE.\n");
        fclose(f);
        return 1;
    }
    if (!read_at(f, 0UL, hdr, 2u) || hdr[0] != 'M' || hdr[1] != 'Z')
        printf("warning: file does not start with 'MZ', it is not a DOS executable.\n");

    printf("\nK6 fix   : ");
    k6 = analyse(&s, 0, &a_off, &b_off);
    printf("Sound fix: ");
    snd = sound_analyse(f, s.size, 0, &bld, cur);
    printf("Mixer    : ");
    mix = mixer_analyse(f, s.size, 0, snd,
                        bld ? bld->name : s.size == FLOPPY_SIZE ? "Floppy build" : "");
    if (!driver_path(out ? out : exe, drvpath, sizeof(drvpath))) {
        fprintf(stderr, "error: file name too long\n");
        fclose(f);
        return 1;
    }
    if (mix == N_PATCHED) {
        dstate = driver_state(drvpath);
        printf("           " DRIVER_NAME ": %s\n",
               dstate == D_CURRENT ? "present, current version" :
               dstate == D_OTHER ? "present, other version (run --mixer again to update it)" :
               dstate == D_FOREIGN ? "present, but not the mixer driver" :
               "MISSING, run --mixer again to write it");
    }
    if (k6 == S_UNPATCHED || k6 == S_PATCHED)
        print_loader_name(f, b_off);

    if (check) {
        fclose(f);
        printf("\n--check: nothing was changed.\n");
        /* status 1: file not recognised at all, or modified by something else */
        if (k6 == S_MIXED || snd == N_MIXED || mix == N_MIXED ||
            !(k6 == S_UNPATCHED || k6 == S_PATCHED || snd == N_UNPATCHED ||
              snd == N_PATCHED || snd == N_NOTNEEDED))
            return 1;
        return 0;
    }

    /* --- which fixes? --------------------------------------------------- */
    if (opt_all || opt_k6 || opt_snd || opt_mix) {
        want_k6 = opt_k6 || opt_all;
        want_snd = opt_snd || opt_all || opt_mix;
        want_mix = opt_mix;
    } else if (interactive) {
        if (!ask_menu(k6, snd, mix, &want_k6, &want_snd, &want_mix)) {
            fclose(f);
            printf("Nothing was changed.\n");
            return 0;
        }
    } else {
        want_k6 = 1;                 /* scripts: behave like version 1.x */
        want_snd = 0;
    }

    /* --- refuse on anything unexpected, for the selected fixes only ------- */
    if (want_mix) {
        dstate = driver_state(drvpath);
        need_drv = (dstate != D_CURRENT);
    }
    if ((want_k6 && (k6 == S_MIXED || k6 == S_UNKNOWN)) ||
        (want_snd && snd == N_MIXED) ||
        (want_mix && (mix == N_MIXED || mix == N_UNKNOWN)) ||
        (want_snd && !want_mix && !want_k6 && snd == N_UNKNOWN)) {
        printf("\nNothing was changed. The selected fix cannot be applied to this file (see above).\n"
               "Restore the original FIFA.EXE (for example from the backup with the extension\n"
               ".ORG) and run this tool again. If you are sure this is the game EXE, please\n"
               "report it and include the size and CRC-32 shown above (see README.md).\n");
        fclose(f);
        return 1;
    }
    if (want_mix && dstate == D_FOREIGN) {
        printf("\nNothing was changed: %s exists and is not the mixer driver. Move it away\n"
               "and run this tool again.\n", drvpath);
        fclose(f);
        return 1;
    }
    if (want_snd && !want_mix && want_k6 && snd == N_UNKNOWN)
        printf("Sound fix: skipped, not available for this file.\n");

    do_k6 = want_k6 && k6 == S_UNPATCHED;
    do_snd = want_snd && snd == N_UNPATCHED;
    do_mix = want_mix && mix == N_UNPATCHED;
    if (want_k6 && k6 == S_PATCHED)
        printf("K6 fix: already applied.\n");
    if (want_snd && snd == N_PATCHED)
        printf("Sound fix: already applied.\n");
    if (want_snd && snd == N_NOTNEEDED)
        printf("Sound fix: not needed for this build.\n");
    if (want_mix && mix == N_PATCHED)
        printf("Mixer: already applied.\n");

    if (!do_k6 && !do_snd && !do_mix) {
        fclose(f);
        printf("Nothing to do.\n");
        if (out) {
            if (!copy_file(exe, out)) {
                fprintf(stderr, "error: cannot write %s\n", out);
                return 1;
            }
            printf("Copied unchanged to %s\n", out);
        }
        if (need_drv) {
            if (!driver_write(drvpath)) {
                fprintf(stderr, "error: cannot write %s\n", drvpath);
                return 1;
            }
            printf("Driver : %s written (%u bytes)\n", drvpath, (unsigned)DRIVER_SIZE);
        }
        return 0;
    }

    nchanged = 0UL;
    if (do_k6) {
        if (!read_at(f, a_off, sa, A_LEN) || !read_at(f, b_off + B_JUMP_OFFSET, sb, 2u)) {
            fprintf(stderr, "error: cannot read %s\n", exe);
            fclose(f);
            return 1;
        }
        printf("  site A (self-modifying store) at file offset 0x%lX: %02X %02X %02X %02X %02X %02X\n",
               a_off, sa[0], sa[1], sa[2], sa[3], sa[4], sa[5]);
        printf("  site B (CPU family test)      at file offset 0x%lX: %02X %02X\n",
               b_off + B_JUMP_OFFSET, sb[0], sb[1]);
        nchanged += 7UL;
    }
    if (do_snd) {
        printf("  sound fix (%s) at file offsets", bld->name);
        for (i = 0; i < (int)bld->n; i++) {
            snd_new[i] = bld->done[i];
            if (cur[i] != bld->done[i]) {
                printf(" 0x%lX", bld->off[i]);
                nchanged++;
            }
        }
        printf("\n");
        if (is_prerelease && bld->size == PRE_SIZE && !do_mix)
            nchanged += N_PRE_EXTRA;  /* with the mixer these bytes change anyway */
    }
    if (do_mix) {
        printf("  mixer: %lu bytes in %u regions\n", mixer_count(s.size), 5u);
        nchanged += mixer_count(s.size);
    }
    fclose(f);

    if (out) {
        if (!copy_file(exe, out)) {
            fprintf(stderr, "error: cannot write %s\n", out);
            return 1;
        }
        if (!patch_in_place(out, do_k6, a_off, b_off, do_snd, bld, snd_new) ||
            (do_mix && !mixer_apply(out, s.size))) {
            fprintf(stderr, "error: cannot patch %s\n", out);
            return 1;
        }
        ref = exe;
        printf("Patched: %s  (%lu bytes changed)\n", out, nchanged);
    } else {
        ref = NULL;
        if (!nobackup) {
            if (!backup_name(exe, bak, sizeof(bak))) {
                fprintf(stderr, "error: file name too long\n");
                return 1;
            }
            if (file_exists(bak)) {
                fprintf(stderr, "error: backup file %s already exists. Move it away, use -o to "
                                "write a separate file, or --no-backup.\n", bak);
                return 1;
            }
            if (!copy_file(exe, bak)) {
                fprintf(stderr, "error: cannot write backup %s\n", bak);
                return 1;
            }
            printf("Backup : %s\n", bak);
            ref = bak;
        }
        if (!patch_in_place(exe, do_k6, a_off, b_off, do_snd, bld, snd_new) ||
            (do_mix && !mixer_apply(exe, s.size))) {
            fprintf(stderr, "error: cannot patch %s (read-only? the backup is intact)\n", exe);
            return 1;
        }
        printf("Patched: %s  (%lu bytes changed)\n", exe, nchanged);
    }

    /* Self-check: recognised as fixed, and exactly the planned number of bytes differ. */
    {
        const char *res = out ? out : exe;

        rescan(res, &k6_after, &snd_after, &mix_after, &crc2);
        diffs = ref ? count_diffs(ref, res) : (long)nchanged;
        if ((do_k6 && k6_after != S_PATCHED) || (do_snd && snd_after != N_PATCHED) ||
            (do_mix && mix_after != N_PATCHED) || diffs != (long)nchanged) {
            fprintf(stderr, "internal error: self-check of %s failed. Restore the original and "
                            "report this.\n", res);
            return 2;
        }
    }
    printf("CRC-32 : %08lX\n", crc2);
    if (need_drv) {
        if (!driver_write(drvpath)) {
            fprintf(stderr, "error: cannot write %s\n", drvpath);
            return 1;
        }
        printf("Driver : %s written (%u bytes)\n", drvpath, (unsigned)DRIVER_SIZE);
    }
    if (want_mix)
        printf("Choose your Sound Blaster card in CONFIG.EXE as usual (or keep the current\n"
               "setting); with the mixer the game plays all effects as samples.\n");
    return 0;
}
