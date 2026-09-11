/* DB2010 battery-backed RTC registers, inferred from original 44911310/44911418.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Calendar digits are binary, with separate century/year registers. The
 * 32-kHz oscillator and 128-Hz fraction use QEMU virtual time. Daily and
 * absolute alarm comparators feed IRQ43; periodic IRQ remains unmodeled.
 * SoC reset retains this battery domain.
 */
#include "system/rtc.h"
#include "qemu/cutils.h"

static bool w800_rtc_valid_date(unsigned year, unsigned month, unsigned day)
{
    static const unsigned days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    if (month < 1 || month > 12 || day < 1) { return false; }
    unsigned limit = days[month - 1];
    if (month == 2 && !(year % 4) && ((year % 100) || !(year % 400))) { limit++; }
    return day <= limit;
}
static void w800_rtc_validity(W800State *s)
{
    uint8_t *r = s->rtc_regs;
    uint8_t flags = r[0x1e] & ~0xd8;
    unsigned century = r[0x12] * 100;
    if (r[4] < 60 && r[6] < 60 && r[8] < 24 &&
        w800_rtc_valid_date(century + r[0x10], r[0xe], r[0xc])) { flags |= 0xc0; }
    if (r[0x2a] < 60 && r[0x2c] < 60 && r[0x2e] < 24) { flags |= 0x10; }
    if (r[0x30] < 60 && r[0x32] < 60 && r[0x34] < 24 &&
        w800_rtc_valid_date(century + r[0x3a], r[0x38], r[0x36])) { flags |= 8; }
    r[0x1e] = flags;
}

static int64_t w800_rtc_elapsed(W800State *s)
{
    return s->rtc_fraction_ns + ((s->rtc_regs[0x58] & 0x8c) == 0x8c ?
        qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->rtc_epoch : 0);
}

static int64_t w800_rtc_seconds(W800State *s)
{
    return s->rtc_seconds + w800_rtc_elapsed(s) / 1000000000;
}

static bool w800_rtc_irq_pending(W800State *s)
{
    return (s->rtc_regs[0x1c] & s->rtc_regs[0x18] & 0x24) != 0;
}

static void w800_rtc_sync(W800State *s)
{
    if (s->rtc_date_pending) { return; }
    time_t seconds = w800_rtc_seconds(s);
    struct tm tm;
    gmtime_r(&seconds, &tm);
    s->rtc_regs[4] = tm.tm_sec;
    s->rtc_regs[6] = tm.tm_min;
    s->rtc_regs[8] = tm.tm_hour;
    s->rtc_regs[0xa] = tm.tm_wday;
    s->rtc_regs[0xc] = tm.tm_mday;
    s->rtc_regs[0xe] = tm.tm_mon + 1;
    s->rtc_regs[0x10] = (tm.tm_year + 1900) % 100;
    s->rtc_regs[0x12] = (tm.tm_year + 1900) / 100;
    w800_rtc_validity(s);
}

static void w800_rtc_commit(W800State *s)
{
    struct tm tm = {
        .tm_sec = s->rtc_regs[4], .tm_min = s->rtc_regs[6],
        .tm_hour = s->rtc_regs[8], .tm_mday = s->rtc_regs[0xc],
        .tm_mon = s->rtc_regs[0xe] - 1,
        .tm_year = s->rtc_regs[0x12] * 100 + s->rtc_regs[0x10] - 1900,
    };
    if (tm.tm_sec < 60 && tm.tm_min < 60 && tm.tm_hour < 24 &&
        tm.tm_mday >= 1 && tm.tm_mday <= 31 && tm.tm_mon >= 0 && tm.tm_mon < 12) {
        s->rtc_seconds = mktimegm(&tm);
        s->rtc_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        s->rtc_fraction_ns = (s->rtc_regs[2] & 127) * INT64_C(7812500);
        s->rtc_regs[0x1e] |= 0xc0;
    } else {
        s->rtc_regs[0x1e] &= ~0xc0;
    }
    s->rtc_date_pending = false;
}

static void w800_rtc_alarm_arm(W800State *s)
{
    timer_del(s->rtc_alarm_timer);
    s->rtc_alarm_when[0] = s->rtc_alarm_when[1] = INT64_MAX;
    if (s->rtc_date_pending || (s->rtc_regs[0x58] & 0x8c) != 0x8c ||
        (s->rtc_regs[0x1e] & 0xc0) != 0xc0) { return; }
    int64_t seconds = w800_rtc_seconds(s);
    time_t calendar = seconds;
    struct tm tm;
    gmtime_r(&calendar, &tm);
    uint8_t *r = s->rtc_regs;
    /* SetAlarm449115d4 disables each comparator before rewriting fields.
     * Comparator enables (16) and IRQ masks (18) are separate: a match
     * remains latched even when its interrupt delivery is masked.
     */
    if ((r[0x16] & 0x20) && (r[0x1e] & 0x10)) {
        tm.tm_sec = r[0x2a]; tm.tm_min = r[0x2c]; tm.tm_hour = r[0x2e];
        int64_t when = mktimegm(&tm);
        if (when <= seconds) { when += 86400; }
        s->rtc_alarm_when[0] = when;
    }
    if ((r[0x16] & 0x80) && (r[0x1e] & 8)) {
        tm.tm_sec = r[0x30]; tm.tm_min = r[0x32]; tm.tm_hour = r[0x34];
        tm.tm_mday = r[0x36]; tm.tm_mon = r[0x38] - 1;
        tm.tm_year = r[0x12] * 100 + r[0x3a] - 1900;
        int64_t when = mktimegm(&tm);
        if (when > seconds) { s->rtc_alarm_when[1] = when; }
    }
    int64_t when = MIN(s->rtc_alarm_when[0], s->rtc_alarm_when[1]);
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (when != INT64_MAX && when - seconds <= (INT64_MAX - now) / 1000000000) {
        timer_mod(s->rtc_alarm_timer, now + (when - seconds) * 1000000000 -
                  w800_rtc_elapsed(s) % 1000000000);
    }
}

static void w800_rtc_alarm_fire(void *opaque)
{
    W800State *s = opaque;
    int64_t seconds = w800_rtc_seconds(s);
    for (unsigned slot = 0; slot < 2; slot++) {
        if (s->rtc_alarm_when[slot] <= seconds) {
            unsigned status = slot ? 0x20 : 4;
            unsigned wake = slot ? 4 : 1;
            s->rtc_regs[0x1c] |= status;
            if ((s->rtc_regs[0x5a] & (0x80 | wake)) == (0x80 | wake)) {
                s->rtc_regs[0x5c] |= wake;
            }
            qemu_log_mask(LOG_GUEST_ERROR, "W800_RTC_ALARM type=%u seconds=%"PRId64
                          " status=%02x enable=%02x\n", slot + 1, seconds,
                          s->rtc_regs[0x1c], s->rtc_regs[0x18]);
        }
    }
    w800_rtc_sync(s);
    w800_rtc_alarm_arm(s);
    w800_irq_update(s);
}

static uint64_t w800_rtc_read(void *opaque, hwaddr offset, unsigned size)
{
    W800State *s = opaque;
    int64_t elapsed = w800_rtc_elapsed(s);
    w800_rtc_sync(s);
    s->rtc_regs[0] = ((elapsed % 1000000000) * 32768 / 1000000000) & 255;
    s->rtc_regs[2] = (elapsed % 1000000000) * 128 / 1000000000;
    uint64_t value = 0;
    for (unsigned i = 0; i < size && offset + i < sizeof(s->rtc_regs); i++) {
        value |= (uint64_t)s->rtc_regs[offset + i] << (i * 8);
    }
    /* ISR449118d8 reads1c through449109f0 once, caches it, masks delivery,
     * dispatches, and restores enables. There is no later status write.
     */
    if (offset <= 0x1c && offset + size > 0x1c) {
        s->rtc_regs[0x1c] &= ~0x24;
        w800_irq_update(s);
    }
    return value;
}

static void w800_rtc_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    W800State *s = opaque;
    if (offset >= 4 && offset <= 0x12) {
        w800_rtc_sync(s);
        s->rtc_date_pending = true;
    }
    if (offset == 0x58) {
        bool running = (s->rtc_regs[0x58] & 0x8c) == 0x8c;
        bool next_running = (value & 0x8c) == 0x8c;
        if (running != next_running) {
            int64_t elapsed = w800_rtc_elapsed(s);
            s->rtc_seconds = w800_rtc_seconds(s);
            s->rtc_fraction_ns = elapsed % 1000000000;
            s->rtc_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        }
    }
    for (unsigned i = 0; i < size && offset + i < sizeof(s->rtc_regs); i++) {
        if (offset + i == 0x5e) { s->rtc_regs[offset + i] &= ~(value >> (i * 8)); }
        else if (offset + i != 0x1c) { s->rtc_regs[offset + i] = value >> (i * 8); }
    }
    /* Original SetTime writes fraction last after all calendar components. */
    if (offset == 2 && s->rtc_date_pending) { w800_rtc_commit(s); }
    w800_rtc_validity(s);
    w800_rtc_alarm_arm(s);
    w800_irq_update(s);
}
static const MemoryRegionOps w800_rtc_ops = {
    .read = w800_rtc_read, .write = w800_rtc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 2, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 2, .unaligned = true },
};
static void w800_rtc_init(W800State *s, MemoryRegion *sysmem)
{
    struct tm tm;
    qemu_get_timedate(&tm, 0);
    s->rtc_seconds = mktimegm(&tm);
    s->rtc_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->rtc_alarm_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_rtc_alarm_fire, s);
    s->rtc_alarm_when[0] = s->rtc_alarm_when[1] = INT64_MAX;
    s->rtc_regs[0x58] = 0x8c;
    s->rtc_regs[0x54] = 0xa5;
    /* Original CheckCalendarValidity at44910ca4 tests these status bits. */
    s->rtc_regs[0x1e] = 0xc0;
    w800_rtc_sync(s);
    memory_region_init_io(&s->rtc_port, OBJECT(s), &w800_rtc_ops, s,
                          "w800.rtc", sizeof(s->rtc_regs));
    memory_region_add_subregion(sysmem, 0xf9050000, &s->rtc_port);
}
