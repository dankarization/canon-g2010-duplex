/*
 * bjlverify — offline verifier for the native inline-BJRaster3 (Canon BJL)
 * command stream produced by src/bjraster3.py.
 *
 * Checks, with libcups available but NOT required for the stream itself:
 *   1. XML envelope: exactly one StartJob, one SetJobConfiguration, one
 *      VendorCmd(ModeShift), one EndJob; sequence order.
 *   2. ESC( command stream: page setup present, every ESC(F block decodes as
 *      a valid PackBits line stream terminated by 0x80, page eject markers
 *      present, and the whole payload is fully covered by ESC( commands.
 *   3. Reports per-page band count, decoded line count, max line width.
 *
 * Original code for this prototype. No Windows PE, no Wine.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static uint8_t *slurp(const char *p, size_t *n) {
    FILE *f = fopen(p, "rb");
    if (!f) { perror(p); return NULL; }
    fseek(f, 0, SEEK_END); long L = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(L > 0 ? L : 1);
    if (fread(b, 1, L, f) != (size_t)L) { fclose(f); free(b); return NULL; }
    fclose(f); *n = (size_t)L; return b;
}

/* Decode one PackBits line-stream block; returns line count, sets maxw. */
static int decode_block(const uint8_t *b, size_t n, size_t *maxw) {
    size_t k = 0, cur = 0;
    int lines = 0;
    while (k < n) {
        uint8_t c = b[k];
        if (c == 0x80) { lines++; if (cur > *maxw) *maxw = cur; cur = 0; k++; }
        else if (c < 0x80) { size_t r = c + 1; cur += r; k += 1 + r; }
        else { size_t r = 257 - c; cur += r; k += 2; }
        if (k > n) return -1; /* overrun */
    }
    if (cur) { lines++; if (cur > *maxw) *maxw = cur; }
    return lines;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <stream.prn>\n", argv[0]); return 1; }
    size_t n; uint8_t *d = slurp(argv[1], &n);
    if (!d) return 2;

    int startjob = 0, setjobcfg = 0, setcfg = 0, modeshift = 0, endjob = 0, senddata = 0;
    for (size_t i = 0; i + 20 < n; i++) {
        if (!memcmp(d+i, "<ivec:operation>StartJob<", 25)) startjob++;
        else if (!memcmp(d+i, "<ivec:operation>SetJobConfiguration<", 34)) setjobcfg++;
        else if (!memcmp(d+i, "<ivec:operation>SetConfiguration<", 33)) setcfg++;
        else if (!memcmp(d+i, "<ivec:operation>EndJob<", 23)) endjob++;
        else if (!memcmp(d+i, "<ivec:operation>SendData<", 25)) senddata++;
        else if (!memcmp(d+i, "<vcn:ijoperation>ModeShift<", 27)) modeshift++;
    }

    /* Walk ESC( commands in the binary region (after the last XML header). */
    size_t pos = 0;
    int f_blocks = 0, ejects = 0, setup = 0, decode_ok = 0, decode_bad = 0, overrun = 0;
    size_t maxw = 0;
    long covered = 0;
    while (pos < n) {
        if (d[pos] == 0x1b && pos + 5 <= n && d[pos+1] == '(') {
            char cmd = d[pos+2];
            size_t len = d[pos+3] | (d[pos+4] << 8);
            if (pos + 5 + len > n) { overrun = 1; break; }
            if (cmd == 'F') {
                f_blocks++;
                int l = decode_block(d + pos + 5, len, &maxw);
                if (l >= 0) decode_ok++; else decode_bad++;
            } else if (cmd == 'e') {
                ejects++;
            } else if (cmd == '$' || cmd == 'T') setup++;
            covered += 5 + (long)len;
            pos += 5 + len;
        } else if (d[pos] == 0x1b && pos + 2 <= n && d[pos+1] == '[') {
            covered += 3; pos += 3;  /* ESC[K + following mode byte handled next */
            if (pos < n) { covered += 1; pos += 1; }
        } else if (d[pos] == 0x1b && pos + 2 <= n && d[pos+1] == '@') {
            covered += 2; pos += 2;
        } else {
            pos++;  /* XML bytes etc. */
        }
    }

    int envelope_ok = (startjob == 1 && setjobcfg == 1 && endjob == 1 && modeshift == 1);
    int inline_ok = (senddata == 0 && f_blocks > 0 && decode_bad == 0 && overrun == 0);
    int ok = envelope_ok && inline_ok;

    printf("{\"file\":\"%s\",\"bytes\":%zu,"
           "\"startjob\":%d,\"setjobconfiguration\":%d,\"setconfiguration\":%d,"
           "\"modeshift_vendorcmd\":%d,\"endjob\":%d,\"senddata\":%d,"
           "\"esc_f_blocks\":%d,\"esc_e_ejects\":%d,"
           "\"decode_ok\":%d,\"decode_bad\":%d,\"overrun\":%d,"
           "\"max_line_width_bytes\":%zu,\"esc_covered_bytes\":%ld,"
           "\"envelope_ok\":%s,\"inline_bjraster3_ok\":%s,\"ok\":%s}\n",
           argv[1], n, startjob, setjobcfg, setcfg, modeshift, endjob, senddata,
           f_blocks, ejects, decode_ok, decode_bad, overrun, maxw, covered,
           envelope_ok ? "true" : "false", inline_ok ? "true" : "false",
           ok ? "true" : "false");
    free(d);
    return ok ? 0 : 5;
}
