/* Provisional DB2010 OS-tick timer and interrupt cascade, inferred from MAIN.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Included by w800.c after W800State. No firmware addresses are patched.
 */
static uint32_t w800_counter(W800State *s)
{
    return (uint32_t)((qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->timer_epoch) * 32768 / 1000000000);
}
static void w800_irq_update(W800State *s)
{
    s->irq_pending[1] = s->timer_pending ? (1u << 25) : 0; /* IRQ53 - 28 */
    if (s->timer2_pending) { s->irq_pending[1] |= 1u << 26; } /* IRQ54 */
    if (w800_camera_irq_pending(s)) { s->irq_pending[1] |= 1u << 12; } /* IRQ40 */
    if (s->lcd_pending) { s->irq_pending[1] |= 1u << 13; } /* IRQ41 - 28 */
    /* Original dda/RTC descriptor44420e0c selects IRQ43. */
    if (w800_rtc_irq_pending(s)) { s->irq_pending[1] |= 1u << 15; }
    if (w800_bt_irq_pending(s)) { s->irq_pending[1] |= 1u << 19; } /* UART3 IRQ47 */
    s->irq_pending[1] |= w800_uart_irqs(s);
    s->irq_pending[2] = s->i2c_pending && (s->i2c_control & 0x80) ? (1u << 10) : 0;
    /* Original dda/dmacon ARM_PL080 config4442004a selects IRQ77. */
    if (s->dma_tc_raw & s->dma_tc_mask) { s->irq_pending[2] |= 1u << 17; }
    if (s->keypad_pending && (s->keypad_control & 0x280) == 0x280) {
        s->irq_pending[2] |= 1u;
    }
    s->irq_pending[0] = (s->irq_pending[1] & ~s->irq_mask[1]) ? (1u << 28) : 0;
    /* Original DSPIF_INT_IntProcess descriptor451795cc selects IRQ9. */
    /* Service UART: source 66 RX event, source 67 TX-drain event. */
    s->irq_pending[2] |= w800_sif_irqs(s);
    if (s->dsp_regs[0x10 / 4] & s->dsp_regs[0x0c / 4]) {
        s->irq_pending[0] |= 1u << 9;
    }
    if (w800_power_irq_pending(s)) { s->irq_pending[0] |= 1u << 25; }
    if (s->irq_pending[2] & ~s->irq_mask[2]) { s->irq_pending[0] |= 1u << 30; }
    /* The guest masks all banks after reading the root vector, before
     * reading its child vector. Preserve the selected source across masking. */
    for (unsigned i = 0; i < 3; i++) {
        uint32_t active = s->irq_pending[i] & ~s->irq_mask[i];
        if (active) { s->irq_vector[i] = ctz32(active); }
    }
    qemu_set_irq(qdev_get_gpio_in(DEVICE(s->cpu), ARM_CPU_IRQ),
                 !!(s->irq_pending[0] & ~s->irq_mask[0]));
}
static void w800_tick(void *opaque)
{
    W800State *s = opaque;
    /* Keep the startup-verified counter model until rollover timing has
     * passed full firmware startup, not just isolated register tests. */
    s->timer_expired = true;
    s->timer_pending = true;
    w800_irq_update(s);
}
static void w800_timer_arm(W800State *s)
{
    timer_del(s->ostick);
    if (s->exploratory && (s->timer_control & 1)) {
        int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        int64_t deadline = s->timer_epoch +
            ((uint64_t)s->timer_compare * 1000000000 + 32767) / 32768;
        /* A passed compare requests an immediate match, not a 36-hour wait. */
        if (deadline <= now) { deadline = now + 30518; }
        timer_mod(s->ostick, deadline);
    }
}
/* TIMER2 is the original millisecond-signal timer (44a3f738).
 * Its ISR reloads 31/32 oscillator ticks through f9010008. */
static void w800_tick2(void *opaque)
{
    W800State *s = opaque;
    s->timer2_pending = true;
    w800_irq_update(s);
}
static void w800_timer2_arm(W800State *s)
{
    timer_del(s->timer2);
    if (s->exploratory && (s->timer_control & 2)) {
        /* The reload includes zero: firmware alternates 31 and 32 with
         * accumulator768/1000, averaging32.768 oscillator ticks per ms. */
        int64_t delta = (((uint64_t)s->timer2_compare + 1) * 1000000000 + 32767) / 32768;
        timer_mod(s->timer2, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + delta);
    } else {
        s->timer2_pending = false;
    }
}
static uint32_t w800_intc_read(W800State *s, hwaddr addr)
{
    unsigned bank = ((addr - 0xfb020000) >> 8) - 1;
    unsigned reg = addr & 255;
    if (reg == 0) { return s->irq_pending[bank]; }
    if (reg == 4) { return s->irq_mask[bank]; }
    if (reg == 0x10 || reg == 0x14) {
        uint32_t selected = s->irq_vector[bank];
        /* The external power source is edge-latched: its ISR posts work
         * and leaves PMIC event reads to that worker. Reasserting the same
         * level here starves the worker and exhausts the guest signal heap. */
        if (bank == 0 && selected == 25) {
            s->power_irq_latched = false;
            w800_irq_update(s);
        }
        return selected;
    }
    if (reg >= 0x1c && reg < 0x9c) { return s->irq_config[bank][(reg - 0x1c) / 4]; }
    return 0;
}
static void w800_intc_write(W800State *s, hwaddr addr, uint32_t value)
{
    unsigned bank = ((addr - 0xfb020000) >> 8) - 1;
    unsigned reg = addr & 255;
    if (reg == 4) { s->irq_mask[bank] = value; }
#if 0
    if (reg == 0x18) {
        /* End-of-interrupt: the EROM dispatcher writes 1 to fb0201x8 after
         * each handler returns. Releases the latched source so lower
         * priority pending vectors can be selected. */
        unsigned v = s->irq_vector[bank];
        if (bank == 1 && v == 25) { s->timer_pending = false; }
        if (bank == 1 && v == 26) { s->timer2_pending = false; }
        if (bank == 2 && (v == 2 || v == 3)) { w800_sif_eoi(s); }
    }
#endif
    if (reg >= 0x1c && reg < 0x9c) { s->irq_config[bank][(reg - 0x1c) / 4] = value; }
    w800_irq_update(s);
}
