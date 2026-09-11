/* Small-page NAND behind the DB2010 byte port. GPL-2.0-or-later.
 * Guest driver 451140e4/45114900: data +0, ALE +8, CLE +10 (hex).
 * 32 MiB Samsung-compatible geometry: 512+16 bytes/page, 32 pages/block.
 * Storage is session-local. Timing, ECC circuitry and CE strobes are pending.
 */
#define W800_NAND_PAGES 65536u
#define W800_NAND_PAGE 528u
#define W800_NAND_BYTES (W800_NAND_PAGES * W800_NAND_PAGE)

static void w800_nand_reset(W800State *s)
{
    s->nand_cmd = 0xff;
    s->nand_area = s->nand_addr_count = s->nand_cursor = 0;
    s->nand_status = 0xc0; /* ready and not write-protected */
    memset(s->nand_addr, 0, sizeof(s->nand_addr));
    memset(s->nand_program, 255, sizeof(s->nand_program));
    s->nand_cache_valid = false;
}

static unsigned w800_nand_row(W800State *s)
{
    return s->nand_addr[1] | (s->nand_addr[2] << 8);
}

static void w800_nand_command(W800State *s, uint8_t cmd)
{
    if (cmd == 0xff) { w800_nand_reset(s); return; }
    if (cmd == 0x00 || cmd == 0x01 || cmd == 0x50) {
        s->nand_area = cmd == 0x50 ? 512 : cmd == 1 ? 256 : 0;
        s->nand_cursor = s->nand_area + s->nand_addr[0];
    } else if (cmd == 0x80) {
        memset(s->nand_program, 255, sizeof(s->nand_program));
        s->nand_status = 0xc0;
    } else if (cmd == 0x8a) {
        /* Original copy-back path 45114510..45114576: read source,
         * poll status, then 8A + destination column/row. The final
         * address cycle commits the internal page buffer, without 10. */
        s->nand_status = s->nand_cache_valid ? 0xc0 : 0xc1;
    } else if (cmd == 0x60) {
        s->nand_status = 0xc0;
    } else if (cmd == 0x10 && s->nand_cmd == 0x80) {
        unsigned row = w800_nand_row(s);
        if (row < W800_NAND_PAGES && s->nand_addr_count >= 3) {
            for (unsigned i = 0; i < W800_NAND_PAGE; i++) {
                s->nand_data[row * W800_NAND_PAGE + i] &= s->nand_program[i];
            }
        } else { s->nand_status |= 1; }
    } else if (cmd == 0xd0 && s->nand_cmd == 0x60) {
        unsigned row = s->nand_addr[0] | (s->nand_addr[1] << 8);
        if (row < W800_NAND_PAGES && s->nand_addr_count >= 2) {
            memset(s->nand_data + (row & ~31u) * W800_NAND_PAGE,
                   255, 32 * W800_NAND_PAGE);
        } else { s->nand_status |= 1; }
    } else if (cmd != 0x60 && cmd != 0x70 && cmd != 0x90) {
        s->nand_status |= 1;
        qemu_log_mask(LOG_UNIMP, "W800_NAND unsupported command 0x%02x\n", cmd);
    }
    s->nand_cmd = cmd;
    s->nand_addr_count = 0;
    if (cmd == 0x90) { s->nand_cursor = 0; }
}

static uint64_t w800_nand_read(void *opaque, hwaddr offset, unsigned size)
{
    W800State *s = opaque;
    if (offset != 0) { return 0xff; }
    if (s->nand_cmd == 0x70) { return s->nand_status; }
    if (s->nand_cmd == 0x90) {
        static const uint8_t id[] = { 0xec, 0x75, 0, 0 };
        unsigned i = s->nand_cursor++;
        return i < sizeof(id) ? id[i] : 0xff;
    }
    if (s->nand_cmd == 0 || s->nand_cmd == 1 || s->nand_cmd == 0x50) {
        unsigned row = w800_nand_row(s);
        if (row < W800_NAND_PAGES && s->nand_cursor < W800_NAND_PAGE) {
            return s->nand_data[row * W800_NAND_PAGE + s->nand_cursor++];
        }
    }
    return 0xff;
}

static void w800_nand_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    W800State *s = opaque;
    if (offset == 0x10) { w800_nand_command(s, value); }
    else if (offset == 8) {
        if (s->nand_addr_count < sizeof(s->nand_addr)) {
            s->nand_addr[s->nand_addr_count++] = value;
        }
        s->nand_cursor = s->nand_cmd == 0x90 ? value : s->nand_area + s->nand_addr[0];
        if (s->nand_addr_count == 3) {
            unsigned row = w800_nand_row(s);
            if (s->nand_cmd == 0 || s->nand_cmd == 1 || s->nand_cmd == 0x50) {
                memcpy(s->nand_cache, s->nand_data + row * W800_NAND_PAGE,
                       W800_NAND_PAGE);
                s->nand_cache_valid = true;
            } else if (s->nand_cmd == 0x8a) {
                if (s->nand_cache_valid && s->nand_addr[0] == 0) {
                    for (unsigned i = 0; i < W800_NAND_PAGE; i++) {
                        s->nand_data[row * W800_NAND_PAGE + i] &= s->nand_cache[i];
                    }
                } else { s->nand_status |= 1; }
            }
        }
    } else if (offset == 0 && s->nand_cmd == 0x80) {
        if (s->nand_cursor < W800_NAND_PAGE) {
            s->nand_program[s->nand_cursor++] = value;
        } else { s->nand_status |= 1; }
    }
    /* +20/+24 are the original driver's transfer enable/disable strobes.
     * This synchronous model completes transfers without pin-level timing. */
}

static const MemoryRegionOps w800_nand_ops = {
    .read = w800_nand_read, .write = w800_nand_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 1 },
    .impl = { .min_access_size = 1, .max_access_size = 1 },
};
