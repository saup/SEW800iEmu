/* Vincenne analog baseband/power companion, original DB2010 firmware interface.
 * GPL-2.0-or-later. See reports/power-research.md for evidence and limits.
 * The external environment is a fitted battery, no charger, released ON key.
 * These are device inputs, not modifications of guest firmware or memory.
 */
#define W800_POWER_ID       0x20
#define W800_POWER_EVENT1   0x21
#define W800_POWER_EVENT2   0x22
#define W800_POWER_STATUS   0x23
/* MAIN44ace9c6/ce programs these masks as08/72. AB3100 uses a
 * different map: importing its24/25 mask addresses prevents every ON IRQ. */
#define W800_POWER_MASK1    0x26
#define W800_POWER_MASK2    0x27
#define W800_POWER_ADC_CTL  0x70
#define W800_POWER_ADC_DATA 0x71

/* A stationary 3.9 V type-1 battery at 25 C. R1L002 GDFS unit 9 calibrates
 * 4090 mV at ADC126 and 3200 mV at ADC42. Unit12 calibrates the auxiliary
 * ADC at 0..2750 mV; MAIN44ab55e6/55ec fixes ADC endpoints to1 and255.
 * Unit1403's type-1 thermistor table has 404 mV at25 C.
 * These model electrical inputs. The original HAL performs its own
 * calibration, temperature interpolation and low-voltage decisions.
 */
static uint8_t w800_power_battery_adc(void)
{
    return 42 + ((3900 - 3200) * (126 - 42) + (4090 - 3200) / 2) /
                (4090 - 3200);
}

static uint8_t w800_power_thermistor_adc(void)
{
    return 1 + (404 * (255 - 1) + 2750 / 2) / 2750;
}

static bool w800_power_irq_pending(W800State *s)
{
    bool level = (s->i2c_registers[W800_POWER_EVENT1] &
            ~s->i2c_registers[W800_POWER_MASK1]) ||
           (s->i2c_registers[W800_POWER_EVENT2] &
            ~s->i2c_registers[W800_POWER_MASK2]);
    /* The original IRQ25 task posts work; the asynchronous worker clears
     * PMIC events later over I2C. Capture the external assertion once until
     * the controller acknowledges its vector. A continuously high input
     * must not allocate another work item after every exception return.
     * Edge capture/vector acknowledgement is inferred from this runtime
     * contract; the DB2010 electrical/config-bit specification is unavailable.
     */
    if (level && !s->power_irq_level) {
        s->power_irq_latched = true;
    }
    s->power_irq_level = level;
    return s->power_irq_latched;
}

static void w800_power_reset(W800State *s)
{
    memset(s->i2c_registers, 0, sizeof(s->i2c_registers));
    s->power_irq_level = false;
    s->power_irq_latched = false;
    /* 44ace8b4..44ace8ce distinguishes d* and e* Vincenne revisions. */
    s->i2c_registers[W800_POWER_ID] = 0xe0;
    /* Latched ON-switch power-up cause survives releasing the button. */
    s->i2c_registers[W800_POWER_EVENT1] = 1;
    s->i2c_registers[W800_POWER_MASK1] = 0xff;
    s->i2c_registers[W800_POWER_MASK2] = 0xff;
    /* An attached USB cable keeps VBUS across guest resets and reports it
     * as a pending startup event, like attaching before power-on. */
    if (s->usb_attached) {
        s->i2c_registers[W800_POWER_STATUS] |= 0x80;
        s->i2c_registers[W800_POWER_EVENT1] |= 0x40;
    }
}

static uint8_t w800_power_read(W800State *s, uint8_t reg)
{
    uint8_t value = s->i2c_registers[reg];
    /* Original44ab71a0 reads75/76 as AD/AD_TX;44ab500c reads77 as
     * average VBAT. With a stationary supply all three measurements agree.
     */
    if (reg >= 0x75 && reg <= 0x77) {
        return w800_power_battery_adc();
    }
    if (reg == W800_POWER_EVENT1 || reg == W800_POWER_EVENT2) {
        /* 44acea68 reads events, then status, then dispatches subscribers. */
        s->i2c_registers[reg] = 0;
        w800_irq_update(s);
    }
    return value;
}

static void w800_power_write(W800State *s, uint8_t reg, uint8_t value)
{
    if ((reg >= W800_POWER_ID && reg <= W800_POWER_STATUS) ||
        reg == W800_POWER_ADC_DATA || (reg >= 0x75 && reg <= 0x77)) {
        return; /* identification, latched events and physical status are RO */
    }
    s->i2c_registers[reg] = value;
    if (reg == W800_POWER_ADC_CTL &&
        ((value & 0xe0) == 0xa0 || value == 0x6a)) {
        /* The manual mux has14 channels. Channel10 is the VBAT fallback
         * (44ab5062);11 feeds both battery identification and temperature
         * (44ab5682/44ab5262). Other channel inputs remain provisional.
         * Latch each result at conversion selection, not on result reads.
         * Original cold-start44a948de selects VBAT with6A, before the
         * ordinary driver'sAA command. Both measure the same supply.
         * Only6A is established for this additional control mode; do not
         * infer conversion behavior for all other6x commands.
         */
        switch (value & 0x0f) {
        case 10: s->i2c_registers[W800_POWER_ADC_DATA] = w800_power_battery_adc(); break;
        case 11: s->i2c_registers[W800_POWER_ADC_DATA] = w800_power_thermistor_adc(); break;
        default: s->i2c_registers[W800_POWER_ADC_DATA] = 0x80; break;
        }
    }
    if (reg == W800_POWER_MASK1 || reg == W800_POWER_MASK2) {
        w800_irq_update(s);
    }
}

static void w800_power_key(W800State *s, bool down)
{
    bool was_down = s->i2c_registers[W800_POWER_STATUS] & 1;
    if (was_down == down) {
        return;
    }
    s->i2c_registers[W800_POWER_STATUS] =
        (s->i2c_registers[W800_POWER_STATUS] & ~1) | down;
    s->i2c_registers[W800_POWER_EVENT1] |= 1;
    w800_irq_update(s);
}

/* DCU-60-style USB cable: the AB3100 VBUS pin reports through status23 bit7
 * (AB3100_STR_VBUS) and latches event21 bit6 (AB3100_EVENTA1_VBUS) on every
 * level change. Attach/detach share the same event bit; firmware reads the
 * status register to learn the direction. The Vincenne IRQ then reaches the
 * original subscriber broadcast at 44aceb68. */
static void w800_power_vbus(W800State *s, bool attached)
{
    s->usb_attached = attached;
    if (attached) {
        s->i2c_registers[W800_POWER_STATUS] |= 0x80;
    } else {
        s->i2c_registers[W800_POWER_STATUS] &= ~0x80;
    }
    s->i2c_registers[W800_POWER_EVENT1] |= 0x40;
    w800_irq_update(s);
}
