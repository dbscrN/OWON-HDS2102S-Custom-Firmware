/*
 * DMM trend graph for OWON HDS2102S_LS firmware V4.7.1 (add-on code).
 *
 * Five stock BL instructions are redirected here (see tools/build_trend.py):
 *   0x0801FFBC  bl systick_body  -> trend_tick    (free-running millisecond counter)
 *   0x080150CA  bl dmm_parse     -> trend_parse   (notes that a new DMM frame was decoded)
 *   0x08015136  bl dmm_show      -> trend_show    (gets the reading exactly as the stock screen shows it)
 *   0x08018638  bl flush_frame   -> trend_flush   (draws the overlay just before the frame goes to the FPGA)
 *   0x0802047E  bl dmm_key       -> trend_key     (HOR opens the trend menu on F1..F4)
 *
 * The code lives inside the 18 px CJK glyph table (0x0804A510..0x0804E400), which is only read when
 * Chinese/Japanese text is drawn in that size. No .data/.bss: state sits in the ~0.5 KB of RAM above
 * the stock stack top (0x2000FDE8).
 */
#include <stdint.h>

typedef uint8_t u8;
typedef int16_t s16;
typedef uint32_t u32;
typedef int32_t s32;

/* ---- stock firmware ---- */
#define FW_MODE   (*(volatile u8 *)0x20001C60)    /* 0 scope, 1 generator, 2 multimeter */
#define FW_DIRTY  (*(volatile u8 *)0x20000538)    /* non-zero -> main loop redraws and flushes */
#define FW_HOLD   (*(volatile u8 *)0x200003C9)    /* 1 while the reading is on hold */
#define MM_FUNC   (*(volatile u8 *)0x2000194C)    /* MultiMeter+0x10: 1 Ohm 2 kOhm 3 MOhm 4 uA 5 mA 6 A 7 pF 8 nF 9 uF 10 mF 11 uV 12 mV 13 V */
#define MM_SEL    (*(volatile u8 *)0x20001944)    /* MultiMeter+0x08: 1 AC voltage, 2 DC voltage, 6/7 current, ... */
#define MM_FREQ   (*(volatile u8 *)0x20001985)    /* MultiMeter+0x49: stock 'frequency' display flag (never set by stock code) */
#define MM_FSCALE (*(volatile u8 *)0x20001986)    /* MultiMeter+0x4A: stock unit selector, 0 Hz, 1 kHz, 2 MHz */
#define MM_TEXT   ((const char *)0x20002ABE)      /* reading as formatted by the stock code ("OL", "0.0000", ...) */
#define MM_UNITS  ((const char *)0x08047690)      /* 10-byte unit strings indexed by MM_FUNC, each starts with ' ' */
#define DMM_SLOTS ((volatile u8 *)0x20001988)     /* 6 x 14-byte frame slots, [0] = valid */

#define fw_systick ((void (*)(void))0x080304E5)
#define fw_parse   ((void (*)(void))0x08019785)
#define fw_show    ((void (*)(const char *))0x08041579)
#define fw_flush   ((void (*)(u8 *))0x0803CC5D)
#define fw_key     ((void (*)(int))0x08035B7D)
#define fw_dmm_tx  ((void (*)(int))0x08021F5D)      /* send one byte to the DMM controller (USART1) */
#define DMM_WR    (*(volatile u32 *)0x20000064)   /* slot the next frame goes to */
#define fw_refresh ((void (*)(void))0x0802FCFD)   /* called by the stock key handlers before acting */

#define FONT14  ((const u8 *)0x08050B62)  /* 14 px, 2 bytes/row, 29-byte records from ' ' */
#define FONT26  ((const u8 *)0x08053702)  /* 26 px, 4 bytes/row, 105-byte records from ' ' */
#define OMEGA26 ((const u8 *)0x0804A24E)  /* bare 26-row, 4 bytes/row bitmap of the Ohm sign */

/* palette indices */
enum { C_BLACK = 2, C_FILL = 3, C_YELLOW = 5, C_TEXT = 8, C_GRID = 10, C_CYAN = 14 };

/* ---- layout (320x240) ---- */
#define AREA_Y0 28
#define AREA_Y1 202
#define BTN_Y0 204
#define BTN_Y1 236
#define PX0 58          /* plot frame */
#define PX1 312
#define PY0 68
#define PY1 196
#define NPT 120

/* key codes delivered to the per-mode key handlers (6 = CH1/2, 11..14 = arrows, 17 = Trig) */
enum { K_F1 = 1, K_F2, K_F3, K_F4, K_HOR = 7 };
#define REPEAT_MS 300   /* events closer than this to the previous one are auto-repeat */
#define SETTLE 3        /* samples dropped after the meter changes range */

#define TICK (*(volatile u32 *)0x2000FFF8)  /* our own millisecond counter, bumped in SysTick */

struct state {
    u32 magic;
    u32 t0;             /* tick when the open bucket started */
    u32 tkey;           /* tick of the last handled key event */
    u8 *fb;
    s16 bmin, bmax;     /* open bucket */
    s16 last;           /* latest sample, S->dec decimals */
    u8 head, cnt;       /* ring of closed buckets */
    u8 on, span, dec, func, have, wipe, menu;
    u8 fresh, paused, skip, sdec, hz;
    char txt[8];        /* reading as last shown while not on hold */
    s16 mn[NPT], mx[NPT];
};
#define S ((struct state *)0x2000FDEC)
#define MAGIC 0x54524E49u

static const u32 bucket_ms[4] = { 250, 500, 2500, 15000 };   /* span / NPT */
static const char span_txt[4][6] = { "30s", "1min", "5min", "30min" };
static const s16 pow10[5] = { 1, 10, 100, 1000, 10000 };

void trend_tick(void)
{
    TICK++;
    fw_systick();
}

static void reset_hist(void)
{
    S->head = S->cnt = S->have = 0;
}

static void ensure(void)
{
    if (S->magic != MAGIC) {
        u32 *p = (u32 *)S;
        for (unsigned i = 0; i < sizeof(struct state) / 4; i++) p[i] = 0;
        S->magic = MAGIC;
        S->span = 1;
    }
}

/* ---- sampling ---- */
void trend_parse(void)
{
    const volatile u8 *f = 0;
    for (int i = 0; i < 6; i++)
        if (DMM_SLOTS[i * 14] == 1) f = DMM_SLOTS + i * 14;
    /* frame flags seen when the controller measures frequency: byte 5 bit 2; byte 7 bit 0 = kilo,
       bit 2 = mega (confirmed with 50 Hz, 2 kHz and 1.5 MHz). hz: 0 none, 1 Hz, 2 kHz, 3 MHz */
    int hz = 0;
    if (f && (f[5] & 0x04)) hz = (f[7] & 1) ? 2 : (f[7] & 4) ? 3 : 1;
    fw_parse();
    ensure();
    if (!f) return;
    S->fresh = 1;
    S->hz = hz;
    if (hz) {                       /* lets the stock screen label the reading as Hz/kHz too */
        MM_FREQ = 1;
        MM_FSCALE = hz - 1;
    }
}

static void push(void)
{
    S->mn[S->head] = S->bmin;
    S->mx[S->head] = S->bmax;
    S->head = (S->head + 1) % NPT;
    if (S->cnt < NPT) S->cnt++;
}

/* history is kept in fixed point with S->dec decimals; change that without losing the window */
static void rescale(int mul, int div)
{
    for (int i = 0; i < NPT; i++) {
        S->mn[i] = S->mn[i] * mul / div;
        S->mx[i] = S->mx[i] * mul / div;
    }
    S->bmin = S->bmin * mul / div;
    S->bmax = S->bmax * mul / div;
    S->last = S->last * mul / div;
}

static s32 iabs(s32 v)
{
    return v < 0 ? -v : v;
}

static s32 max_abs(void)
{
    s32 m = iabs(S->last);
    for (int i = 0; i < S->cnt; i++) {
        int k = (S->head + NPT - 1 - i) % NPT;
        if (iabs(S->mn[k]) > m) m = iabs(S->mn[k]);
        if (iabs(S->mx[k]) > m) m = iabs(S->mx[k]);
    }
    return m;
}

/* v has sd decimals -> d decimals */
static s32 conv(s32 v, int sd, int d)
{
    return d >= sd ? v * pow10[d - sd] : v / pow10[sd - d];
}

#define FITS(x) (iabs(x) <= 32000)

static void sample(const char *s)
{
    /* the text the stock screen shows: optional '-', digits, optional '.', digits; anything else ("OL") is no value */
    int neg = 0, sd = -1, ok = 0;
    s32 v = 0;
    if (*s == '-') { neg = 1; s++; }
    for (; *s; s++) {
        if (*s == '.' && sd < 0) { sd = 0; continue; }
        if (*s < '0' || *s > '9' || v > 99999) { ok = 0; break; }
        v = v * 10 + (*s - '0');
        if (sd >= 0) sd++;
        ok = 1;
    }
    if (sd < 0) sd = 0;
    if (sd > 4) ok = 0;
    if (neg) v = -v;

    u8 fn = MM_FUNC | (S->hz << 5);
    if (fn != S->func) {            /* another quantity or unit: the old window means nothing */
        S->func = fn;
        S->sdec = sd;
        S->skip = SETTLE;
        reset_hist();
    }
    if (ok && sd != S->sdec) {      /* autorange step: the next readings are garbage while it settles */
        S->sdec = sd;
        S->skip = SETTLE;
    }
    if (!ok || FW_HOLD == 1 || S->skip) {
        if (S->skip) S->skip--;
        S->paused = 1;              /* the graph stands still instead of drawing a false level */
        return;
    }

    u32 now = TICK, bm = bucket_ms[S->span];
    if (S->paused) {
        S->paused = 0;
        S->t0 = now;
    }
    if (!S->cnt && !S->have) {
        S->dec = sd;
        S->last = 0;
    }
    /* follow the meter's resolution down at once, and back up only when the whole window still
       fits, so that a 24 V step is not thrown away when the meter drops to a mV-resolution range */
    while (S->dec > sd || (S->dec && !FITS(conv(v, sd, S->dec)))) {
        rescale(1, 10);
        S->dec--;
    }
    while (S->dec < sd && max_abs() * 10 <= 32000 && FITS(conv(v, sd, S->dec + 1))) {
        rescale(10, 1);
        S->dec++;
    }
    s32 x = conv(v, sd, S->dec);
    if (x > 32000) x = 32000;
    if (x < -32000) x = -32000;

    if (!S->have) {
        S->bmin = S->bmax = x;
        S->t0 = now;
        S->have = 1;
    } else {
        /* buckets are cut by the clock, not by sample arrival; a bucket without a sample repeats the level */
        while (now - S->t0 >= bm) {
            push();
            S->t0 += bm;
            S->bmin = S->bmax = S->last;
        }
        if (x < S->bmin) S->bmin = x;
        if (x > S->bmax) S->bmax = x;
    }
    S->last = x;
}

void trend_show(const char *s)
{
    fw_show(s);
    ensure();
    if (FW_HOLD != 1) {             /* on hold the stock digits freeze, so does our copy */
        int i = 0;
        for (; i < 7 && s[i]; i++) S->txt[i] = s[i];
        S->txt[i] = 0;
    }
    if (!S->fresh) return;
    S->fresh = 0;
    if (!S->on || FW_MODE != 2) return;
    sample(s);
    FW_DIRTY = 1;
}

/* ---- drawing ---- */
static void px(int x, int y, int c)
{
    u8 *p = S->fb + ((y * 320 + x) >> 1);
    *p = (x & 1) ? (*p & 0xF0) | c : (*p & 0x0F) | (c << 4);
}

static void hline(int x0, int x1, int y, int c, int step)
{
    for (; x0 <= x1; x0 += step) px(x0, y, c);
}

static void vline(int x, int y0, int y1, int c)
{
    if (y0 > y1) { int t = y0; y0 = y1; y1 = t; }
    for (; y0 <= y1; y0++) px(x, y0, c);
}

static void fill(int x0, int x1, int y0, int y1, int c)
{
    for (; y0 <= y1; y0++) hline(x0, x1, y0, c, 1);
}

static void bitmap(int x, int y, const u8 *rows, int rb, int w, int h, int c)
{
    for (int r = 0; r < h; r++)
        for (int b = 0; b < w; b++)
            if (rows[r * rb + (b >> 3)] & (0x80 >> (b & 7))) px(x + b, y + r, c);
}

/* draw (c >= 0) or just measure (c < 0); returns x after the last glyph. UTF-8 Ohm sign is supported in the big font */
static int text(int x, int y, const char *s, int big, int c)
{
    const u8 *font = big ? FONT26 : FONT14;
    int rb = big ? 4 : 2, h = big ? 26 : 14;
    for (; *s; s++) {
        u8 ch = *s;
        if (ch == 0xCE && (u8)s[1] == 0xA9) {
            s++;
            if (big) {
                if (c >= 0) bitmap(x, y, OMEGA26, 4, 26, 26, c);
                x += 20;
                continue;
            }
            ch = 'R';
        }
        if (ch < ' ' || ch > '~') ch = '?';
        const u8 *g = font + (ch - ' ') * (rb * h + 1);
        if (c >= 0) bitmap(x, y, g + 1, rb, g[0], h, c);
        x += g[0];
    }
    return x;
}

static int text_w(const char *s)
{
    return text(0, 0, s, 0, -1);
}

static void text_r(int xr, int y, const char *s, int c)
{
    text(xr - text_w(s), y, s, 0, c);
}

static void text_c(int xc, int y, const char *s, int c)
{
    text(xc - text_w(s) / 2, y, s, 0, c);
}

/* v scaled by 10^dec -> decimal string; returns pointer into buf (12 bytes) */
static char *fmt(char *buf, s32 v, int dec)
{
    char *p = buf + 11;
    u32 a = v < 0 ? -v : v;
    int i = 0;
    *p = 0;
    do {
        *--p = '0' + a % 10;
        a /= 10;
        if (++i == dec) *--p = '.';
    } while (a || i <= dec);
    if (v < 0) *--p = '-';
    return p;
}

static s32 nice(s32 x)
{
    for (s32 m = 1;; m *= 10) {
        if (x <= m) return m;
        if (x <= 2 * m) return 2 * m;
        if (x <= 5 * m) return 5 * m;
    }
}

static s32 floor_to(s32 v, s32 d)
{
    s32 q = v / d;
    if (v % d < 0) q--;
    return q * d;
}

static void bucket(int i, s32 *a, s32 *z)
{
    if (i < S->cnt) {
        int k = (S->head + NPT - S->cnt + i) % NPT;
        *a = S->mn[k];
        *z = S->mx[k];
    } else {
        *a = S->bmin;
        *z = S->bmax;
    }
}

static void draw_graph(void)
{
    char b[12];
    int dec = S->dec;
    int n = S->cnt + S->have;
    u8 fn = MM_FUNC;

    fill(0, 319, AREA_Y0, AREA_Y1 - 1, C_BLACK);
    /* the reading and its unit exactly as the stock screen has them */
    int x = text(8, 34, S->txt, 1, C_YELLOW);
    static const char funit[3][5] = { " Hz", " kHz", " MHz" };
    if (S->hz) text(x, 34, funit[S->hz - 1], 1, C_YELLOW);
    else if (fn >= 1 && fn <= 13) text(x, 34, MM_UNITS + fn * 10, 1, C_YELLOW);
    if (FW_HOLD == 1) text(PX0 + 60, PY1 - 16, "HOLD", 0, C_YELLOW);

    for (int y = PY0; y <= PY1; y += 32) hline(PX0, PX1, y, C_GRID, (y == PY0 || y == PY1) ? 1 : 4);
    vline(PX0, PY0, PY1, C_GRID);
    vline(PX1, PY0, PY1, C_GRID);
    text(PX0 + 4, PY1 - 16, span_txt[S->span], 0, C_GRID);
    if (S->menu) {   /* service line: raw bytes 1..7 of the newest DMM frame and the unit code */
        static const char hx[] = "0123456789ABCDEF";
        const volatile u8 *f = DMM_SLOTS + ((DMM_WR + 5) % 6) * 14;
        char h[24];
        int k = 0;
        for (int i = 1; i <= 7; i++) {
            h[k++] = hx[f[i] >> 4];
            h[k++] = hx[f[i] & 15];
            h[k++] = ' ';
        }
        h[k++] = hx[fn & 15];
        h[k] = 0;
        text(PX0 + 4, PY0 + 20, h, 0, C_TEXT);
    }
    if (!n) return;

    s32 lo = 32767, hi = -32767, a, z;
    for (int i = 0; i < n; i++) {
        bucket(i, &a, &z);
        if (a < lo) lo = a;
        if (z > hi) hi = z;
    }
    text_r(316, 30, fmt(b, hi, dec), C_TEXT);
    text_r(316, 46, fmt(b, lo, dec), C_TEXT);
    text_r(262, 30, "max", C_GRID);
    text_r(262, 46, "min", C_GRID);

    s32 div = nice((hi - lo + 3) / 4 > 0 ? (hi - lo + 3) / 4 : 1);
    s32 base = floor_to(lo, div);
    while (base + 4 * div < hi) {
        div = nice(div + 1);
        base = floor_to(lo, div);
    }
    text_r(PX0 - 3, PY0 - 2, fmt(b, base + 4 * div, dec), C_GRID);
    text_r(PX0 - 3, PY0 + 57, fmt(b, base + 2 * div, dec), C_GRID);
    text_r(PX0 - 3, PY1 - 12, fmt(b, base, dec), C_GRID);
    text(text(PX0 + 4, PY0 + 3, fmt(b, div, dec), 0, C_CYAN), PY0 + 3, "/div", 0, C_CYAN);

    int px_prev = 0, py_prev = 0;
    for (int i = 0; i < n; i++) {
        bucket(i, &a, &z);
        int xx = PX0 + 1 + (NPT - n + i) * (PX1 - PX0 - 2) / (NPT - 1);
        int ya = PY1 - (a - base) * (PY1 - PY0) / (4 * div);
        int yz = PY1 - (z - base) * (PY1 - PY0) / (4 * div);
        if (i) {
            hline(px_prev, xx, py_prev, C_YELLOW, 1);
            vline(xx, py_prev, yz, C_YELLOW);
        }
        vline(xx, ya, yz, C_YELLOW);
        px_prev = xx;
        py_prev = ya;
    }
}

static void button(int i, const char *top, const char *bottom)
{
    int x0 = 4 + i * 78;
    fill(x0, x0 + 75, BTN_Y0, BTN_Y1 - 1, C_FILL);
    text_c(x0 + 38, bottom ? BTN_Y0 + 2 : BTN_Y0 + 9, top, C_TEXT);
    if (bottom) text_c(x0 + 38, BTN_Y0 + 17, bottom, C_YELLOW);
}

/* the controller's 'second function' byte means frequency only on AC voltage (on DC it is the relative mode) */
static int freq_ok(void)
{
    return S->hz || MM_SEL == 1;
}

static void draw_menu(void)
{
    fill(0, 319, BTN_Y0 - 2, 239, C_BLACK);
    button(0, "Trend", S->on ? "On" : "Off");
    button(1, "Time", span_txt[S->span]);
    if (freq_ok()) button(2, "Freq", S->hz ? "On" : "Off");
    else button(2, "Clear", 0);
    button(3, "Close", 0);
}

void trend_flush(u8 *buf)
{
    ensure();
    if (FW_MODE == 2) {
        S->fb = buf;
        if (S->on) draw_graph();
        else if (S->wipe & 1) fill(0, 319, AREA_Y0, AREA_Y1 - 1, C_BLACK);
        if (S->menu) draw_menu();
        else if (S->wipe & 2) fill(0, 319, BTN_Y0 - 2, 239, C_BLACK);
        S->wipe = 0;
    } else {
        S->menu = 0;
    }
    fw_flush(buf);
}

/* ---- keys ---- */
static void close_menu(void)
{
    if (S->menu) {
        S->menu = 0;
        S->wipe |= 2;
        fw_refresh();
    }
}

void trend_key(int k)
{
    ensure();
    int mine = k == K_HOR || (S->menu && k >= K_F1 && k <= K_F4);
    if (!mine) {
        close_menu();
        fw_key(k);
        return;
    }
    /* a held key keeps sending events: act only on the first one after a pause */
    u32 now = TICK;
    int repeat = now - S->tkey < REPEAT_MS;
    S->tkey = now;
    if (repeat) return;

    if (k == K_HOR) {
        if (S->menu) close_menu(); else S->menu = 1;
    } else if (k == K_F3 && freq_ok()) {
        fw_dmm_tx(5);               /* toggles the DMM controller between the value and its frequency */
    } else if (k == K_F4) {
        close_menu();
    } else {
        if (k == K_F1) {
            S->on = !S->on;
            if (!S->on) S->wipe |= 1;
        }
        if (k == K_F2) S->span = (S->span + 1) & 3;
        reset_hist();
    }
    FW_DIRTY = 1;
}
