/* Host-connected DB2010 UART0/1/4. Register model follows the observed UART
 * driver and existing UART3 model; DMA and precise baud timing are not modeled.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
struct W800UART {
    W800State *board;
    MemoryRegion port;
    CharFrontend chr;
    QEMUTimer *timer;
    uint16_t regs[24];
    uint8_t rx[4096], tx[4096];
    unsigned rx_head, rx_size, tx_head, tx_size, irq;
    uint16_t pending;
};
static uint16_t w800_uart_status(struct W800UART *u)
{
    /* Baud discovery precedes delivery of the measured byte stream. */
    if (u->pending & 0x20) {
        return u->pending & u->regs[0x12 / 2];
    }
    return ((u->tx_size < sizeof(u->tx) ? 2 : 0) |
            (u->rx_size ? 0x1041 : 0) | u->pending) & u->regs[0x12 / 2];
}
static uint32_t w800_uart_irqs(W800State *s)
{
    uint32_t pending = 0;
    for (unsigned i = 0; i < 3; i++) {
        if (s->uart[i] && w800_uart_status(s->uart[i])) {
            pending |= 1u << (s->uart[i]->irq - 28);
        }
    }
    return pending;
}
static void w800_uart_flush(void *opaque)
{
    struct W800UART *u = opaque;
    if (u->tx_size) {
        unsigned length = MIN(u->tx_size, sizeof(u->tx) - u->tx_head);
        int n = qemu_chr_fe_write(&u->chr, u->tx + u->tx_head, length);
        if (n > 0) {
            u->tx_head = (u->tx_head + n) % sizeof(u->tx);
            u->tx_size -= n;
        }
        if (u->tx_size) {
            timer_mod(u->timer, qemu_clock_get_ms(QEMU_CLOCK_REALTIME) + 10);
        }
    }
    w800_irq_update(u->board);
}
static int w800_uart_can_receive(void *opaque)
{
    struct W800UART *u = opaque;
    return sizeof(u->rx) - u->rx_size;
}
static void w800_uart_receive(void *opaque, const uint8_t *buf, int size)
{
    struct W800UART *u = opaque;
    for (int i = 0; i < size && u->rx_size < sizeof(u->rx); i++) {
        u->rx[(u->rx_head + u->rx_size++) % sizeof(u->rx)] = buf[i];
    }
    /* The host byte stream has fixed 115200/8N1 framing. In automatic
     * baud mode the original driver waits for this measurement interrupt
     * before releasing its queued AT replies (44a98234). */
    if (size > 0 && (u->regs[0x0a / 2] & 7) == 2) {
        u->regs[0x20 / 2] = (u->regs[0x0a / 2] & 0x100) ? 4 : 16;
        u->regs[0x0c / 2] = (u->regs[0x0c / 2] & ~0x1f) | 1;
        u->regs[0x0a / 2] &= ~7;
        u->pending |= 0x20;
    }
    w800_irq_update(u->board);
}
static uint64_t w800_uart_read(void *opaque, hwaddr offset, unsigned size)
{
    struct W800UART *u = opaque;
    uint16_t value;
    switch (offset) {
    case 0:
        value = 0;
        if (u->rx_size) {
            value = u->rx[u->rx_head];
            u->rx_head = (u->rx_head + 1) % sizeof(u->rx);
            u->rx_size--;
            qemu_chr_fe_accept_input(&u->chr);
        }
        break;
    case 0x0e: value = u->tx_size ? 0 : 0x10; break;
    case 0x14: value = w800_uart_status(u); break;
    case 0x2c: value = MIN(256, sizeof(u->tx) - u->tx_size); break;
    case 0x2e: value = MIN(256, u->rx_size); break;
    default: value = u->regs[offset / 2]; break;
    }
    w800_irq_update(u->board);
    return value;
}
static void w800_uart_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    struct W800UART *u = opaque;
    if (offset == 2) {
        if (u->tx_size < sizeof(u->tx)) {
            u->tx[(u->tx_head + u->tx_size++) % sizeof(u->tx)] = value;
            w800_uart_flush(u);
        } else {
            qemu_log_mask(LOG_GUEST_ERROR, "W800 UART TX FIFO overrun\n");
        }
    } else if (offset == 0x14) {
        u->pending &= ~value;
    } else {
        u->regs[offset / 2] = value;
    }
    w800_irq_update(u->board);
}
static const MemoryRegionOps w800_uart_ops = {
    .read = w800_uart_read, .write = w800_uart_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 2, .max_access_size = 2 },
    .impl = { .min_access_size = 2, .max_access_size = 2 },
};
static void w800_uart_reset(W800State *s)
{
    for (unsigned i = 0; i < 3; i++) {
        struct W800UART *u = s->uart[i];
        if (!u) { continue; }
        timer_del(u->timer);
        memset(u->regs, 0, sizeof(u->regs));
        u->rx_head = u->rx_size = u->tx_head = u->tx_size = 0;
        u->pending = 0;
    }
}
static void w800_uart_init(W800State *s, MemoryRegion *sysmem)
{
    const unsigned ports[] = {0, 1, 4};
    /* Original eipd_marita-uarts.c table at 0x441cff98, indexed by unit. */
    const hwaddr bases[] = {0xfb060000, 0xfc040000, 0xfb040000};
    for (unsigned i = 0; i < 3; i++) {
        Chardev *chr = serial_hd(i);
        if (!chr) { continue; }
        struct W800UART *u = g_new0(struct W800UART, 1);
        s->uart[i] = u;
        u->board = s;
        u->irq = 44 + ports[i];
        qemu_chr_fe_init(&u->chr, chr, &error_fatal);
        u->timer = timer_new_ms(QEMU_CLOCK_REALTIME, w800_uart_flush, u);
        memory_region_init_io(&u->port, OBJECT(s), &w800_uart_ops, u,
                              "w800.host-uart", 0x30);
        memory_region_add_subregion(sysmem, bases[i], &u->port);
        qemu_chr_fe_set_handlers(&u->chr, w800_uart_can_receive,
                                w800_uart_receive, NULL, NULL, u, NULL, true);
    }
}
