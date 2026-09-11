/* DB2010 service UART (SIF) at fd000000, used by the EROM flash listener.
 * Register semantics recovered from the original EROM dispatcher:
 * fd000004 command/control, fd000008 RX byte count (read) / TX count
 * (write), fd00000c 16-bit data FIFO, fd000010 sub-status (bit1 = RX data
 * present), fd000030 event status (bit0 TX-feed, bit1 RX, bit4 TX done;
 * write-back acknowledges). INTC sources 66 (data events) / 67 (done). */
struct W800Sif {
    W800State *board;
    MemoryRegion port;
    CharFrontend chr;
    QEMUTimer *timer;
    uint16_t regs[0x80];
    uint8_t rx[4096], tx[4096];
    unsigned rx_head, rx_size, tx_head, tx_size, tx_want;
    uint16_t events;
};
static uint32_t w800_sif_irqs(W800State *s)
{
    if (!s->sif) { return 0; }
    return ((s->sif->events & 0x0f) || s->sif->rx_size >= 8 ? 1u << 2 : 0) |
           (s->sif->events & 0x30 ? 1u << 3 : 0);    /* source 67 */
}
static void w800_sif_eoi(W800State *s)
{
    if (s->sif) { s->sif->events = 0; }
}
static void w800_sif_flush(void *opaque)
{
    struct W800Sif *u = opaque;
    if (u->tx_size) {
        unsigned length = MIN(u->tx_size, sizeof(u->tx) - u->tx_head);
        int n = qemu_chr_fe_write(&u->chr, u->tx + u->tx_head, length);
        if (n > 0) {
            u->tx_head = (u->tx_head + n) % sizeof(u->tx);
            u->tx_size -= n;
        }
        if (u->tx_size) {
            timer_mod(u->timer, qemu_clock_get_ms(QEMU_CLOCK_REALTIME) + 10);
            return;
        }
    }
    w800_irq_update(u->board);
}
static int w800_sif_can_receive(void *opaque)
{
    struct W800Sif *u = opaque;
    return sizeof(u->rx) - u->rx_size;
}
static void w800_sif_receive(void *opaque, const uint8_t *buf, int size)
{
    struct W800Sif *u = opaque;
    for (int i = 0; i < size && u->rx_size < sizeof(u->rx); i++) {
        u->rx[(u->rx_head + u->rx_size++) % sizeof(u->rx)] = buf[i];
    }
    if (size > 0) { u->events |= 2; }  /* bit1 = RX data event */
    w800_irq_update(u->board);
}
static uint64_t w800_sif_read(void *opaque, hwaddr offset, unsigned size)
{
    struct W800Sif *u = opaque;
    uint16_t value = u->regs[offset / 2 & 0x7f];
    switch (offset) {
    case 0x08: {
        /* RX data port: pops one byte per byte-read, two per halfword. */
        value = 0;
        for (unsigned i = 0; i < size && u->rx_size; i++) {
            value |= u->rx[u->rx_head] << (i * 8);
            u->rx_head = (u->rx_head + 1) % sizeof(u->rx);
            u->rx_size--;
        }
        qemu_chr_fe_accept_input(&u->chr);
        break;
    }
    case 0x14:
        /* RX byte count read by the original drain at 0x44001350. */
        value = u->rx_size;
        break;
    case 0x10:
        /* Sub-status polled by the RX drain: bit3 = RX data present,
         * bit1 = RX FIFO empty. */
        value = (u->regs[0x10 / 2] & ~0xau) |
                (u->rx_size ? 0x8 : 0x2);
        break;
    case 0x30:
        /* bit2 is a level flag: an 8-byte header block is buffered. */
        value = (u->events & ~4u) | (u->rx_size >= 8 ? 4 : 0);
        break;
    }
    if (u->board->exploratory) {
        qemu_log_mask(LOG_UNIMP, "W800_SIF read off=0x%02" HWADDR_PRIx
                      " value=0x%04x pc=0x%08x\n",
                      offset, value, u->board->cpu->env.regs[15]);
    }
    w800_irq_update(u->board);
    return value;
}
static void w800_sif_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    struct W800Sif *u = opaque;
    if (u->board->exploratory) {
        qemu_log_mask(LOG_UNIMP, "W800_SIF write off=0x%02" HWADDR_PRIx
                      " value=0x%04" PRIx64 " pc=0x%08x\n",
                      offset, value & 0xffff, u->board->cpu->env.regs[15]);
    }
    switch (offset) {
    case 0x04:
        /* Command writes observed: 0x10 transmits the staged FIFO data. */
        u->regs[offset / 2] = value;
        if ((value & 0x10) && u->tx_size) {
            w800_sif_flush(u);
            if (!u->tx_size) { u->events |= 0x10; }  /* TX done */
        }
        break;
    case 0x0c:
        /* TX data port: the EROM stages one byte per halfword write
         * (response path at 0x44001878), then writes 0x10 to fd000004. */
        if (u->tx_size < sizeof(u->tx)) {
            u->tx[(u->tx_head + u->tx_size++) % sizeof(u->tx)] = value & 0xff;
        }
        break;
    case 0x30:
        u->events &= ~(value & 0x3f);
        break;
    default:
        u->regs[offset / 2 & 0x7f] = value;
        break;
    }
    w800_irq_update(u->board);
}
static const MemoryRegionOps w800_sif_ops = {
    .read = w800_sif_read, .write = w800_sif_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};
static void w800_sif_reset(W800State *s)
{
    struct W800Sif *u = s->sif;
    if (!u) { return; }
    timer_del(u->timer);
    memset(u->regs, 0, sizeof(u->regs));
    u->rx_head = u->rx_size = u->tx_head = u->tx_size = u->tx_want = 0;
    u->events = 0;
}
static void w800_sif_init(W800State *s, MemoryRegion *sysmem)
{
    Chardev *chr = serial_hd(3);
    if (!chr) { return; }
    struct W800Sif *u = g_new0(struct W800Sif, 1);
    s->sif = u;
    u->board = s;
    qemu_chr_fe_init(&u->chr, chr, &error_fatal);
    u->timer = timer_new_ms(QEMU_CLOCK_REALTIME, w800_sif_flush, u);
    memory_region_init_io(&u->port, OBJECT(s), &w800_sif_ops, u,
                          "w800.sif-uart", 0x100);
    memory_region_add_subregion(sysmem, 0xfd000000, &u->port);
    qemu_chr_fe_set_handlers(&u->chr, w800_sif_can_receive,
                            w800_sif_receive, NULL, NULL, u, NULL, true);
}
