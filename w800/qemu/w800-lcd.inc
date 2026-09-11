/* DB2010 PDI transport and Renesas R61500 panel.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Register/bus evidence: reports/lcd-research.md. No guest PC hooks or menus.
 * The PDI sequencer implements the write/DMA program forms supplied by MAIN/FS;
 * unknown programs fail visibly rather than being treated as successful DMA.
 */
#include "ui/console.h"
#include "qemu/bswap.h"

typedef struct W800LCD {
    W800State *machine;
    MemoryRegion pdi_port, dma_port, fifo_port;
    QemuConsole *console;
    QEMUTimer *timer;
    uint8_t pdi[256], dma[256];
    uint32_t fifo_control;
    uint16_t regs[256], command;
    uint32_t gram[176 * 240], bus_word;
    unsigned bus_bytes;
    bool bus_data, dirty, dma_wait;
    uint32_t pixels_written, transfers, dma_count;
    unsigned dma_channel;
} W800LCD;

static void w800_lcd_advance(W800LCD *s)
{
    unsigned x = s->regs[0x21] & 255, y = s->regs[0x21] >> 8;
    unsigned x0 = s->regs[0x44] & 255, x1 = s->regs[0x44] >> 8;
    unsigned y0 = s->regs[0x45] & 255, y1 = s->regs[0x45] >> 8;
    bool ix = s->regs[3] & 0x10, iy = s->regs[3] & 0x20;
    if (s->regs[3] & 8) {
        if ((iy && y >= y1) || (!iy && y <= y0)) {
            y = iy ? y0 : y1;
            x = ((ix && x >= x1) || (!ix && x <= x0)) ? (ix ? x0 : x1) : x + (ix ? 1 : -1);
        } else { y += iy ? 1 : -1; }
    } else {
        if ((ix && x >= x1) || (!ix && x <= x0)) {
            x = ix ? x0 : x1;
            y = ((iy && y >= y1) || (!iy && y <= y0)) ? (iy ? y0 : y1) : y + (iy ? 1 : -1);
        } else { x += ix ? 1 : -1; }
    }
    s->regs[0x21] = (y << 8) | x;
}

static void w800_lcd_pixel(W800LCD *s, uint32_t rgb)
{
    unsigned x = s->regs[0x21] & 255, y = s->regs[0x21] >> 8;
    if (x < 176 && y < 240) {
        /* Preserve all six bits per channel in high-color mode. GRAM is
         * stored in console RGB form so switching back to RGB565 does not
         * reinterpret or truncate pixels that have already been written. */
        if (s->regs[3] & 0x1000) {
            rgb = (rgb & 0x00ff00) | ((rgb & 255) << 16) | (rgb >> 16);
        }
        s->gram[y * 176 + x] = rgb;
        s->pixels_written++;
        s->dirty = true;
    }
    w800_lcd_advance(s);
}

static void w800_lcd_word(W800LCD *s, bool data, uint16_t value)
{
    if (!data) { s->command = value & 255; return; }
    if (s->command == 0x22) {
        unsigned r = (value >> 11) * 255 / 31;
        unsigned g = ((value >> 5) & 63) * 255 / 63;
        unsigned b = (value & 31) * 255 / 31;
        w800_lcd_pixel(s, (r << 16) | (g << 8) | b);
    } else {
        s->regs[s->command] = value;
        s->dirty = true;
    }
}

static void w800_lcd_byte(W800LCD *s, bool data, uint8_t value)
{
    if (data != s->bus_data) { s->bus_bytes = 0; s->bus_data = data; }
    if (!s->bus_bytes) { s->bus_word = 0; }
    if (data && s->command == 0x22 && (s->regs[3] & 0xe000) == 0xc000) {
        /* R61500 p36/p88: TRI=1, DFM=10 transfers R, G, B in three
         * bus cycles; each component occupies DB17..12 (byte bits7..2).
         * Register commands remain two-byte transfers in either mode. */
        s->bus_word = (s->bus_word << 6) | (value >> 2);
        if (++s->bus_bytes == 3) {
            unsigned r = (s->bus_word >> 12) * 255 / 63;
            unsigned g = ((s->bus_word >> 6) & 63) * 255 / 63;
            unsigned b = (s->bus_word & 63) * 255 / 63;
            s->bus_bytes = 0;
            w800_lcd_pixel(s, (r << 16) | (g << 8) | b);
        }
    } else {
        s->bus_word = (s->bus_word << 8) | value;
        if (++s->bus_bytes == 2) {
            s->bus_bytes = 0;
            w800_lcd_word(s, data, s->bus_word);
        }
    }
}

static void w800_lcd_update(void *opaque)
{
    W800LCD *s = opaque;
    DisplaySurface *surface = qemu_console_surface(s->console);
    if (!s->dirty || !surface) { return; }
    bool on = (s->regs[7] & 3) && !(s->regs[0x10] & 3);
    for (unsigned y = 0; y < 220; y++) {
        uint32_t *line = (uint32_t *)(surface_data(surface) + y * surface_stride(surface));
        for (unsigned x = 0; x < 176; x++) {
            unsigned sy = (y + (s->regs[0x41] & 255)) % 240;
            uint32_t p = on ? s->gram[sy * 176 + x] : 0;
            unsigned r = p >> 16, g = (p >> 8) & 255, b = p & 255;
            /* R01.SS reverses source-dot order. R61500_B programs SS=1
             * with BGR=1, preserving RGB on the W800 module (p32/table 12).
             * This console uses logical GRAM coordinates for orientation. */
            if (s->regs[1] & 0x100) { unsigned t = r; r = b; b = t; }
            line[x] = (r << 16) | (g << 8) | b;
        }
    }
    s->dirty = false;
    dpy_gfx_update(s->console, 0, 0, 176, 220);
}
static void w800_lcd_invalidate(void *opaque) { ((W800LCD *)opaque)->dirty = true; }
static const GraphicHwOps w800_lcd_graphics = {
    .invalidate = w800_lcd_invalidate, .gfx_update = w800_lcd_update,
};

static bool w800_lcd_program(W800LCD *s, unsigned entry, bool dma)
{
    /* 1d1f/201f/281f setup followed by 9817 length loads the right-aligned
     * PDI command buffer. 8013/8817 are nested DMA loop counts in R61500_B.
     * This bounded decoder accepts only that evidenced program family. */
    static const uint8_t setup[] = {0x1f,0x1d,9,0,0x1f,0x20,0x90,0x99,0x1f,0x28,0x9c,0x99,0x17,0x98};
    if (entry > 80 || memcmp(s->pdi + entry, setup, sizeof(setup))) { return false; }
    unsigned n = lduw_le_p(s->pdi + entry + 14);
    if (n > 24) { return false; }
    s->bus_bytes = 0;
    for (unsigned i = 128 - n; i < 128; i++) {
        bool rs = (s->pdi[0x80 + i / 8] >> (i % 8)) & 1;
        w800_lcd_byte(s, rs, s->pdi[i]);
    }
    s->dma_count = 0;
    if (dma) {
        if (entry + 30 > 128 || lduw_le_p(s->pdi + entry + 22) != 0x8013 ||
            lduw_le_p(s->pdi + entry + 26) != 0x8817) { return false; }
        unsigned outer = lduw_le_p(s->pdi + entry + 24);
        unsigned inner = lduw_le_p(s->pdi + entry + 28);
        /* Both fields count bus bytes. R61500_B supplies 38720 x 2 for
         * a full screen, but 51744 x 1 for the observed menu redraw.
         * The outer count is not necessarily a count of RGB565 pixels. */
        unsigned bytes = outer * inner;
        unsigned bytes_per_pixel = (s->regs[3] & 0x8000) ? 3 : 2;
        if ((s->regs[3] & 0x8000) && (s->regs[3] & 0x6000) != 0x4000) {
            return false;
        }
        if ((inner != 1 && inner != 2) || !bytes || bytes % bytes_per_pixel ||
            bytes > 176 * 240 * bytes_per_pixel) { return false; }
        s->dma_count = bytes;
    }
    s->transfers++;
    return true;
}

static void w800_lcd_complete(void *opaque)
{
    W800LCD *s = opaque;
    stl_le_p(s->pdi + 0xf4, 0);
    s->dma_wait = false;
    s->machine->lcd_pending = true;
    w800_irq_update(s->machine);
}

static void w800_lcd_error(W800LCD *s, const char *what)
{
    stl_le_p(s->pdi + 0xf4, 8);
    qemu_log_mask(LOG_UNIMP, "W800_LCD_UNSUPPORTED %s\n", what);
}

static uint64_t w800_lcd_read(void *opaque, hwaddr addr, unsigned size)
{
    W800LCD *s = opaque;
    uint32_t result = 0;
    for (unsigned i = 0; i < size; i++) { result |= (uint32_t)s->pdi[addr + i] << (i * 8); }
    return result;
}
static void w800_lcd_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    W800LCD *s = opaque;
    for (unsigned i = 0; i < size; i++) { s->pdi[addr + i] = value >> (i * 8); }
    if (addr != 0xdc) { return; }
    s->machine->lcd_pending = false;
    w800_irq_update(s->machine);
    if (value == 1) {
        if (!w800_lcd_program(s, 0, false)) { w800_lcd_error(s, "PIO program"); }
        else { stl_le_p(s->pdi + 0xf4, 0); }
    } else if (value == 3) {
        /* Register-read program transfers two index bytes from the buffer.
         * Device code register 0 returns the documented 0x1500. */
        s->command = s->pdi[127];
        unsigned v = s->command ? s->regs[s->command] : 0x1500;
        stl_le_p(s->pdi + 0xf4, v << 8);
    } else if (value == 5) {
        /* Arm/acknowledge DMA. Only an enabled DMA channel starts transfer. */
        stl_le_p(s->pdi + 0xf4, 0);
    }
}
static const MemoryRegionOps w800_lcd_ops = {
    .read = w800_lcd_read, .write = w800_lcd_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};
static uint64_t w800_lcd_dma_read(void *opaque, hwaddr addr, unsigned size)
{
    W800LCD *s = opaque;
    uint32_t result = 0;
    for (unsigned i = 0; i < size; i++) { result |= (uint32_t)s->dma[addr + i] << (i * 8); }
    return result;
}
static void w800_lcd_dma_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    W800LCD *s = opaque;
    for (unsigned i = 0; i < size; i++) { s->dma[addr + i] = value >> (i * 8); }
    if ((addr & 31) == 0x10) {
        unsigned bit = 1u << (addr / 32);
        s->machine->dma_tc_mask = (s->machine->dma_tc_mask & ~bit) |
                                 ((value & 0x8000) ? bit : 0);
        w800_irq_update(s->machine);
    }
    if ((addr & 31) != 0x10 || !(value & 1)) { return; }
    unsigned ch = addr & ~31;
    uint32_t source = ldl_le_p(s->dma + ch), dest = ldl_le_p(s->dma + ch + 4);
    if (source == 0xfa040020 || dest == 0xfa040020) {
        w800_dsp_transport(s->machine);
        return;
    }
    if (source == 0xf7000308 && s->machine->camera) {
        w800_camera_transport(s->machine);
        return;
    }
    if (dest != 0xf7000308) { w800_lcd_error(s, "non-PDI DMA destination"); return; }
    if (!w800_lcd_program(s, s->pdi[0xfc], true)) { w800_lcd_error(s, "DMA program"); return; }
    if (s->command != 0x22) { w800_lcd_error(s, "DMA without GRAM index"); return; }
    uint8_t *buffer = g_malloc(s->dma_count);
    MemTxResult result = address_space_read(&address_space_memory, source,
                                           MEMTXATTRS_UNSPECIFIED, buffer, s->dma_count);
    if (result != MEMTX_OK) { g_free(buffer); w800_lcd_error(s, "DMA source read"); return; }
    for (unsigned i = 0; i < s->dma_count; i++) {
        /* DMA words drain into the 8-bit PDI bus least-significant byte
         * first. The buffer already contains panel byte order (MSB first
         * within RGB565, or R/G/B bytes in RGB666). The panel's current
         * entry mode determines when these bus bytes form a complete pixel. */
        w800_lcd_byte(s, true, buffer[i]);
    }
    g_free(buffer);
    stl_le_p(s->dma + ch, source + s->dma_count);
    stl_le_p(s->dma + ch + 0x10, value & ~1);
    s->dma_wait = true;
    s->dma_channel = ch / 32;
    stl_le_p(s->pdi + 0xf4, 1);
    qemu_log_mask(LOG_UNIMP, "W800_LCD_DMA source=0x%08x bytes=%u pixels=%u entry=0x%04x\n", source, s->dma_count, s->pixels_written, s->regs[3]);
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100000);
}
static const MemoryRegionOps w800_lcd_dma_ops = {
    .read = w800_lcd_dma_read, .write = w800_lcd_dma_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};
static uint64_t w800_lcd_fifo_read(void *opaque, hwaddr addr, unsigned size)
{
    W800LCD *s = opaque;
    return addr ? 0 : s->fifo_control;
}
static void w800_lcd_fifo_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    W800LCD *s = opaque;
    if (!addr) { s->fifo_control = value; }
    else { for (unsigned i = 0; i < size; i++) { w800_lcd_byte(s, true, value >> (i * 8)); } }
}
static const MemoryRegionOps w800_lcd_fifo_ops = {
    .read = w800_lcd_fifo_read, .write = w800_lcd_fifo_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
static void w800_lcd_reset(W800State *machine)
{
    W800LCD *s = machine->lcd;
    memset(s->pdi, 0, sizeof(s->pdi));
    memset(s->dma, 0, sizeof(s->dma));
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->gram, 0, sizeof(s->gram));
    s->regs[0x44] = 0xaf00; s->regs[0x45] = 0xef00;
    /* Reset SS=0, NL=11101 per R61500 p78. MAIN later selects SS=1. */
    s->regs[1] = 0x001d; s->regs[3] = 0x30;
    s->command = s->bus_word = 0;
    s->pixels_written = s->transfers = s->dma_count = 0;
    s->bus_bytes = 0;
    s->bus_data = s->dma_wait = false;
    s->dirty = true; s->fifo_control = 0;
    machine->lcd_pending = false;
    timer_del(s->timer);
}
static void w800_lcd_init(W800State *machine)
{
    W800LCD *s = g_new0(W800LCD, 1);
    machine->lcd = s; s->machine = machine;
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_lcd_complete, s);
    memory_region_init_io(&s->pdi_port, NULL, &w800_lcd_ops, s, "w800.pdi", 0x100);
    memory_region_add_subregion_overlap(get_system_memory(), 0xf7000100, &s->pdi_port, 2);
    memory_region_init_io(&s->dma_port, NULL, &w800_lcd_dma_ops, s, "w800.pdi-dma", 0x100);
    memory_region_add_subregion_overlap(get_system_memory(), 0xf2000100, &s->dma_port, 2);
    memory_region_init_io(&s->fifo_port, NULL, &w800_lcd_fifo_ops, s, "w800.pdi-fifo", 8);
    memory_region_add_subregion_overlap(get_system_memory(), 0xf7000304, &s->fifo_port, 2);
    s->console = graphic_console_init(NULL, 0, &w800_lcd_graphics, s);
    qemu_console_resize(s->console, 176, 220);
    w800_lcd_reset(machine);
}
