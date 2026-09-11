/* DB2010 keypad, derived from unmodified R1L002 MAIN driver 44911dd4.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Requires ui/input.h and fields documented in reports/keypad-research.md.
 */
static uint8_t w800_keypad_columns(W800State *s)
{
    static const struct { unsigned code, row, col; } keys[] = {
        { Q_KEY_CODE_3, 0, 0 }, { Q_KEY_CODE_6, 0, 1 },
        { Q_KEY_CODE_9, 0, 2 }, { Q_KEY_CODE_H, 0, 3 },
        { Q_KEY_CODE_F1, 1, 0 }, { Q_KEY_CODE_F2, 1, 1 },
        { Q_KEY_CODE_ESC, 1, 2 }, { Q_KEY_CODE_BACKSPACE, 1, 3 },
        { Q_KEY_CODE_F5, 1, 4 },
        { Q_KEY_CODE_2, 2, 0 }, { Q_KEY_CODE_5, 2, 1 },
        { Q_KEY_CODE_8, 2, 2 }, { Q_KEY_CODE_0, 2, 3 },
        { Q_KEY_CODE_VOLUMEDOWN, 2, 4 },
        { Q_KEY_CODE_1, 3, 0 }, { Q_KEY_CODE_4, 3, 1 },
        { Q_KEY_CODE_7, 3, 2 }, { Q_KEY_CODE_ASTERISK, 3, 3 },
        { Q_KEY_CODE_VOLUMEUP, 3, 4 },
        { Q_KEY_CODE_F3, 5, 1 }, { Q_KEY_CODE_F6, 5, 2 },
        { Q_KEY_CODE_F4, 5, 3 },
    };
    uint8_t columns = 0x1f;
    for (unsigned i = 0; i < G_N_ELEMENTS(keys); i++) {
        if (s->keypad_keys[keys[i].code] &&
            !(s->keypad_scan & (1u << keys[i].row))) {
            columns &= ~(1u << keys[i].col);
        }
    }
    /* The five joystick contacts encode a direction as a whole. MAIN's
     * 44e44efc decoder explicitly recognises these nine bit patterns. */
    if (!(s->keypad_scan & 0x10)) {
        bool up = s->keypad_keys[Q_KEY_CODE_UP];
        bool down = s->keypad_keys[Q_KEY_CODE_DOWN];
        bool left = s->keypad_keys[Q_KEY_CODE_LEFT];
        bool right = s->keypad_keys[Q_KEY_CODE_RIGHT];
        uint8_t contacts = 0x1f;
        if (s->keypad_keys[Q_KEY_CODE_RET]) { contacts = 0x17; }
        else if (up && !down) { contacts = left ? 0x07 : right ? 0x13 : 0x05; }
        else if (down && !up) { contacts = left ? 0x15 : right ? 0x16 : 0x12; }
        else if (left && !right) { contacts = 0x11; }
        else if (right && !left) { contacts = 0x06; }
        columns &= contacts;
    }
    /* Power grounds a column independent of row output, detected by the
     * final 0x3f scan; MAIN removes it from the other six row results. */
    if (s->keypad_keys[Q_KEY_CODE_POWER]) { columns &= ~1u; }
    return columns;
}

static void w800_keypad_debounce(void *opaque)
{
    W800State *s = opaque;
    s->keypad_pending = true;
    w800_irq_update(s);
}

static uint64_t w800_keypad_read(void *opaque, hwaddr offset, unsigned size)
{
    W800State *s = opaque;
    uint16_t value = offset < 2 ? s->keypad_control :
                     (s->keypad_scan & 0x3f) | (w800_keypad_columns(s) << 6);
    return (value >> ((offset & 1) * 8)) & (size == 1 ? 0xff : 0xffff);
}

static void w800_keypad_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size)
{
    W800State *s = opaque;
    uint16_t *reg = offset < 2 ? &s->keypad_control : &s->keypad_scan;
    uint16_t merged = size == 1 ?
        (*reg & ~(0xffu << ((offset & 1) * 8))) |
        ((value & 0xff) << ((offset & 1) * 8)) : value;
    if (offset < 2) {
        if (merged & 0x100) { s->keypad_pending = false; }
        *reg = merged & ~0x100; /* IRQ acknowledge is a strobe. */
        w800_irq_update(s);
    } else {
        *reg = merged & 0x3f;
    }
}

static const MemoryRegionOps w800_keypad_ops = {
    .read = w800_keypad_read, .write = w800_keypad_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 2 },
    .impl = { .min_access_size = 1, .max_access_size = 2 },
};

static void w800_keypad_event(DeviceState *dev, QemuConsole *src, InputEvent *evt)
{
    W800State *s = W800_MACHINE(qdev_get_machine());
    InputKeyEvent *key = evt->u.key.data;
    int code = qemu_input_key_value_to_qcode(key->key);
    if (code < 0 || code >= Q_KEY_CODE__MAX ||
        s->keypad_keys[code] == key->down) { return; }
    s->keypad_keys[code] = key->down;
    if (code == Q_KEY_CODE_POWER) { w800_power_key(s, key->down); }
    /* Coalesce switch bounce. Exact DB2010 analogue debounce timing has
     * not been measured; five milliseconds keeps host repeats out. */
    timer_mod(s->keypad_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 5000000);
    qemu_log_mask(LOG_UNIMP, "W800_KEY qcode=%d down=%d\n", code, key->down);
}

static const QemuInputHandler w800_keypad_handler = {
    .name = "W800i keypad",
    .mask = INPUT_EVENT_MASK_KEY,
    .event = w800_keypad_event,
};

static void w800_keypad_reset(W800State *s)
{
    s->keypad_control = 0;
    s->keypad_scan = 0x3f;
    s->keypad_pending = false;
    memset(s->keypad_keys, 0, sizeof(s->keypad_keys));
    timer_del(s->keypad_timer);
}

static void w800_keypad_init(W800State *s, MemoryRegion *sysmem)
{
    s->keypad_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_keypad_debounce, s);
    memory_region_init_io(&s->keypad_port, OBJECT(s), &w800_keypad_ops, s,
                          "w800.keypad", 4);
    memory_region_add_subregion(sysmem, 0xfb010000, &s->keypad_port);
    qemu_input_handler_register(NULL, &w800_keypad_handler);
    w800_keypad_reset(s);
}
