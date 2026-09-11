/* DB2010 I2C master, inferred from the original polled and IRQ70 drivers.
 * GPL-2.0-or-later. Slave 4a is the Vincenne power companion.
 * Register behavior is implemented in w800-power.inc. The opt-in camera
 * transport at1f uses length-prefixed packets instead of register bytes.
 */
static void w800_i2c_complete(void *opaque)
{
    W800State *s = opaque;
    s->i2c_pending = true;
    w800_irq_update(s);
}
static void w800_i2c_reset(W800State *s)
{
    timer_del(s->i2c_timer);
    s->i2c_pending = false;
    s->i2c_control = s->i2c_data = s->i2c_divider = 0;
    s->i2c_status = 0xf8;
    s->i2c_phase = s->i2c_slave = s->i2c_register = 0;
    s->i2c_reading = false;
    w800_power_reset(s);
}
static void w800_i2c_control(W800State *s, uint16_t value)
{
    bool completion = false;
    timer_del(s->i2c_timer);
    s->i2c_pending = false;
    s->i2c_control = value & ~0x38;
    if (value & 0x20) {
        s->i2c_status = s->i2c_phase ? 0x10 : 8;
        s->i2c_phase = 1;
        completion = true;
    } else if (value & 0x10) {
        s->i2c_phase = 0;
        s->i2c_status = 0xf8;
    } else if (s->i2c_phase == 1) {
        s->i2c_slave = s->i2c_data >> 1;
        s->i2c_reading = s->i2c_data & 1;
        bool acknowledged = s->i2c_slave == 0x4a ||
            (s->i2c_slave == 0x1f && w800_camera_address(s, s->i2c_reading));
        s->i2c_status = acknowledged ?
            (s->i2c_reading ? 0x40 : 0x18) : (s->i2c_reading ? 0x48 : 0x20);
        if (s->i2c_slave == 0x1f) {
            qemu_log_mask(LOG_UNIMP,
                          "W800_CAMERA_I2C address=%02x read=%u status=%02x pc=%08x\n",
                          s->i2c_slave, s->i2c_reading, s->i2c_status,
                          s->cpu->env.regs[15]);
        }
        s->i2c_phase = 2;
        completion = true;
    } else if (s->i2c_phase >= 2) {
        if (s->i2c_reading) {
            s->i2c_data = s->i2c_slave == 0x4a ? w800_power_read(s, s->i2c_register++) :
                s->i2c_slave == 0x1f ? w800_camera_read_byte(s) : 0xff;
            s->i2c_status = value & 4 ? 0x50 : 0x58;
        } else {
            bool acknowledged = false;
            if (s->i2c_slave == 0x4a) {
                if (s->i2c_phase == 2) { s->i2c_register = s->i2c_data; }
                else { w800_power_write(s, s->i2c_register++, s->i2c_data); }
                acknowledged = true;
            } else if (s->i2c_slave == 0x1f) {
                acknowledged = w800_camera_write_byte(s, s->i2c_data);
            }
            s->i2c_status = acknowledged ? 0x28 : 0x30;
        }
        s->i2c_phase = 3;
        completion = true;
    }
    w800_irq_update(s);
    if (completion) {
        timer_mod(s->i2c_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 10000);
    }
}
static uint64_t w800_i2c_read(void *opaque, hwaddr offset, unsigned size)
{
    W800State *s = opaque;
    switch (offset) {
    case 0: return s->i2c_data;
    case 2: return s->i2c_control | (s->i2c_pending ? 8 : 0);
    case 4: return s->i2c_divider;
    case 6: return s->i2c_status;
    default: return 0;
    }
}
static void w800_i2c_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    W800State *s = opaque;
    if (offset == 0) { s->i2c_data = value; }
    if (offset == 2) { w800_i2c_control(s, value); }
    if (offset == 4) { s->i2c_divider = value; }
}
static const MemoryRegionOps w800_i2c_ops = {
    .read = w800_i2c_read, .write = w800_i2c_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 2 },
    .impl = { .min_access_size = 1, .max_access_size = 2 },
};
