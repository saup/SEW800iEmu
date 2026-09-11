/* Sony Ericsson W800i / DB2010 bring-up machine.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Provisional memory map. No fabricated firmware display or boot-ROM code.
 */
#include "qemu/osdep.h"
#include "chardev/char-fe.h"
#include "system/system.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qemu/host-utils.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "qapi/visitor.h"
#include "hw/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/loader.h"
#include "hw/block/flash.h"
#include "ui/input.h"
#include "target/arm/cpu.h"
#include "target/arm/cpregs.h"
#include "system/reset.h"
#include "system/runstate.h"
#include "system/address-spaces.h"

#define TYPE_W800_MACHINE MACHINE_TYPE_NAME("w800")
OBJECT_DECLARE_SIMPLE_TYPE(W800State, W800_MACHINE)
struct W800State {
    MachineState parent;
    ARMCPU *cpu;
    MemoryRegion iram, shared_ram, vectors, dtcm, unknown, erom_alias, low_ram;
    MemoryRegion mask_rom;
    char *mask_rom_file;
    char *nor_otp_file;
    uint16_t chip_id;
    bool reported, exploratory;
    GHashTable *latches;
    unsigned accesses;
    uint32_t mmio_limit;
    uint32_t tcm[2];
    uint32_t mmu_range[12], mmu_attr[12], emc_control;
    uint32_t irq_mask[3], irq_pending[3], irq_vector[3], irq_config[3][32];
    QEMUTimer *ostick, *timer2;
    uint32_t timer2_compare;
    bool timer2_pending;
    int64_t timer_epoch;
    uint32_t timer_control, timer_compare;
    bool timer_pending, timer_expired;
    MemoryRegion nand_port;
    uint8_t *nand_data;
    uint8_t nand_cmd, nand_status, nand_addr[4], nand_program[528];
    uint8_t nand_cache[528];
    bool nand_cache_valid;
    unsigned nand_area, nand_addr_count, nand_cursor;
    MemoryRegion i2c_port;
    QEMUTimer *i2c_timer;
    bool i2c_pending, i2c_reading;
    bool power_irq_level, power_irq_latched;
    uint16_t i2c_control, i2c_divider;
    uint8_t i2c_data, i2c_status, i2c_phase, i2c_slave, i2c_register, i2c_registers[256];
    MemoryRegion keypad_port;
    QEMUTimer *keypad_timer;
    uint16_t keypad_control, keypad_scan;
    bool keypad_pending, keypad_keys[Q_KEY_CODE__MAX];
    struct W800LCD *lcd;
    struct W800Camera *camera;
    bool camera_transport, host_camera, camera_paused;
    bool lcd_pending;
    MemoryRegion rtc_port;
    uint8_t rtc_regs[0x60];
    int64_t rtc_seconds, rtc_epoch;
    int64_t rtc_fraction_ns, rtc_alarm_when[2];
    QEMUTimer *rtc_alarm_timer;
    bool rtc_date_pending;
    MemoryRegion dsp_port;
    uint32_t dsp_regs[48];
    uint32_t *dsp_memory;
    uint64_t dsp_reads, dsp_writes;
    QEMUTimer *dsp_timer;
    uint8_t dsp_reply[16380];
    unsigned dsp_reply_size, dsp_reply_channel;
    bool dsp_reply_pending, dsp_reply_announced;
    bool dsp_protocol_initialized;
    struct W800DspMixer *dsp_mixer;
    struct W800DspAAC *dsp_aac;
    uint8_t dma_tc_raw, dma_tc_mask;
    uint16_t dsp_stream_capacity[6];
    uint16_t dsp_stream_words[6][1024];
    uint8_t dsp_pending_requests[16][16380];
    uint16_t dsp_pending_lengths[16], dsp_pending_channels[16];
    unsigned dsp_pending_count;
    bool dsp_queue_full_reported;
    struct W800Bluetooth *bluetooth;
    struct W800UART *uart[3];
    struct W800Sif *sif;
    char *erom_file;
    bool usb_attached, flash;
};

static bool w800_power_irq_pending(W800State *s);
static void w800_dsp_transport(W800State *s);
static void w800_camera_transport(W800State *s);
static bool w800_camera_irq_pending(W800State *s);
static bool w800_bt_irq_pending(W800State *s);
static uint32_t w800_uart_irqs(W800State *s);
static uint32_t w800_sif_irqs(W800State *s);
static void w800_sif_eoi(W800State *s);
static bool w800_rtc_irq_pending(W800State *s);
#include "w800-timer-intc.inc"
#include "w800-power.inc"
#include "w800-keypad.inc"
#include "w800-lcd.inc"
#include "w800-camera.inc"
#include "w800-rtc.inc"
#include "w800-dsp.inc"
#include "w800-nand.inc"
#include "w800-i2c.inc"
#include "w800-bluetooth.inc"
#include "w800-uart.inc"
#include "w800-sif.inc"

static void w800_unknown(W800State *s, hwaddr addr, unsigned size, bool write, uint64_t value)
{
    if (s->exploratory && s->accesses++ < s->mmio_limit) {
        if (s->accesses <= 10000) {
            qemu_log_mask(LOG_UNIMP, "W800_PROVISIONAL %s addr=0x%08" HWADDR_PRIx " size=%u value=0x%" PRIx64 " pc=0x%08x\n",
                      write ? "write" : "read", addr, size, value, s->cpu->env.regs[15]);
        } else if (s->accesses == 10001) {
            qemu_log_mask(LOG_UNIMP, "W800_PROVISIONAL further MMIO logging suppressed\n");
        }
        return;
    }
    if (!s->reported) {
        error_report("%s %s addr=0x%08" HWADDR_PRIx " size=%u value=0x%" PRIx64 " pc=0x%08x",
                     s->exploratory ? "W800_MMIO_LIMIT" : "W800_UNMODELED",
                     write ? "write" : "read", addr, size, value, s->cpu->env.regs[15]);
        s->reported = true;
        qemu_system_vmstop_request_prepare();
        qemu_system_vmstop_request(RUN_STATE_PAUSED);
    }
}
static uint64_t w800_read(void *opaque, hwaddr addr, unsigned size)
{
    W800State *s = opaque;
    uint64_t result = 0;
    /* The supplied-ROM profile identifies the selected DB2010 revision.
     * Original GetChipId at44a369f0 reads this immutable halfword. */
    if (s->mask_rom_file && addr >= 0xf9090000 && addr + size <= 0xf9090002) {
        return (s->chip_id >> ((addr - 0xf9090000) * 8)) &
               ((UINT64_C(1) << (size * 8)) - 1);
    }
    /* Stop unbacked accesses instead of assuming zero-filled ROM. The known
     * ROM API window stop even in exploratory mode; a supplied image is
     * mapped over this fallback. The image may cover only part of the window. */
    if (addr >= 0xffff0000) {
        if (!s->reported) {
            error_report("W800_MASK_ROM_UNAVAILABLE addr=0x%08" HWADDR_PRIx
                         " pc=0x%08x: no supplied ROM bytes at this address",
                         addr, s->cpu->env.regs[15]);
            s->reported = true;
            qemu_system_vmstop_request_prepare();
            qemu_system_vmstop_request(RUN_STATE_PAUSED);
        }
        return 0;
    }
    if (addr >= 0xfb020100 && addr < 0xfb020400 && size == 4) {
        return w800_intc_read(s, addr);
    }
    if (size == 4 && (addr == 0xf2000000 || addr == 0xf2000004)) {
        return s->dma_tc_raw & s->dma_tc_mask;
    }
    if (size == 4 && addr == 0xf2000014) { return s->dma_tc_raw; }
    if (size == 4 && (addr == 0xf200000c || addr == 0xf2000018)) { return 0; }
    if (size == 4 && addr == 0xf200001c) {
        uint32_t enabled = 0;
        for (unsigned ch = 0; ch < 8; ch++) {
            if (ldl_le_p(s->lcd->dma + ch * 32 + 0x10) & 1) { enabled |= 1u << ch; }
        }
        return enabled;
    }
    if (addr == 0xf9010000) { return s->timer_control; }
    if (addr == 0xf9010004) { return s->timer_compare; }
    if (addr == 0xf9010008) { return s->timer2_compare; }
    if (addr == 0xf901001c) { return w800_counter(s); }
    if (addr == 0xf9010018) {
        uint32_t pending = (s->timer_pending ? 1 : 0) | (s->timer2_pending ? 2 : 0);
        s->timer2_pending = false;
        s->timer_pending = false;
        w800_irq_update(s);
        return pending;
    }
    /* Unpopulated external chip-selects float high, not read/write RAM.
     * Latching probe writes here fabricates a second RAM bank and makes
     * firmware replace the real RAM's MMU range with a phantom at 46000000. */
    if ((addr >= 0x46000000 && addr < 0x4c000000) ||
        (addr >= 0x4c800000 && addr < 0x54000000)) {
        return size == 4 ? UINT32_MAX : ((UINT64_C(1) << (size * 8)) - 1);
    }
    /* DB2010 range-driven section table. Firmware configures twelve ranges
     * at FE004000 and descriptors at FE004030, then uses TTBR=FE000000.
     * Identity section mapping is inferred from these original writes. */
    if (addr >= 0xfe000000 && addr < 0xfe004000 && size == 4) {
        unsigned section = (addr - 0xfe000000) / 4;
        for (unsigned i = 0; i < 12; i++) {
            unsigned first = s->mmu_range[i] & 0xfff;
            unsigned last = (s->mmu_range[i] >> 16) & 0xfff;
            if ((s->mmu_range[i] & 0x80000000) && (s->mmu_attr[i] & 3) == 2 && section >= first && section <= last) {
                return (section << 20) | (s->mmu_attr[i] & 0xfffff);
            }
        }
        return 0;
    }
    if (addr >= 0xfe004000 && addr < 0xfe004060 && size == 4) {
        unsigned index = (addr - 0xfe004000) / 4;
        return index < 12 ? s->mmu_range[index] : s->mmu_attr[index - 12];
    }
    if (s->exploratory) {
        for (unsigned i = 0; i < size; i++) {
            result |= (uint64_t)GPOINTER_TO_UINT(g_hash_table_lookup(s->latches,
                      GUINT_TO_POINTER((uint32_t)addr + i))) << (i * 8);
        }
    }
    /* GPIO port 4: an empty Memory Stick socket pulls card-detect high.
     * Original 44accedc reads this port; 4487f5d8 tests bit 2 active-low.
     * The input pin is independent of the port's stored output bits. A zero
     * latch falsely inserts a card and stalls FSU before MMI initialization.
     * Card insertion and the remaining GPIO controller are not yet modeled. */
    if (addr <= 0xf9000028 && addr + size > 0xf9000028) {
        /* Pin 0 mirrors USB VBUS for the USBVBUS alternate route. */
        result |= (UINT64_C(4) | (s->usb_attached ? 1 : 0)) <<
                  ((0xf9000028 - addr) * 8);
    }
    /* The virtual controller's UART RX line is idle high. Original UART3
     * open samples GPIO port2 pin4 before sending its open completion. */
    if (addr <= 0xf9000014 && addr + size > 0xf9000014) {
        result |= UINT64_C(0x10) << ((0xf9000014 - addr) * 8);
    }
    /* Host UART RX inputs idle high, like the Bluetooth UART above.
     * eipd_marita-uarts.c (table 441d016c) samples these pins before
     * delivering the original open confirmation to the serial service. */
    if (s->uart[0] && addr <= 0xf900000a && addr + size > 0xf900000a) {
        result |= UINT64_C(1) << ((0xf900000a - addr) * 8);
    }
    if (s->uart[1] && addr <= 0xf900000a && addr + size > 0xf900000a) {
        result |= UINT64_C(0x10) << ((0xf900000a - addr) * 8);
    }
    if (s->uart[2] && addr <= 0xf900001e && addr + size > 0xf900001e) {
        result |= UINT64_C(1) << ((0xf900001e - addr) * 8);
    }
    /* Provisional clock lock completion: MAIN writes 0x810/0x410 and
     * polls bit 0 while selecting 104/52 MHz in 0x44a35f34.
     * This is a functional assumption, not measured PLL timing. */
    if (s->exploratory && addr == 0xf909f800 && (result & 0x10)) {
        result |= 1;
    }
    w800_unknown(s, addr, size, false, result);
    return result;
}
static void w800_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    W800State *s = opaque;
    /* Original forced-reset routine 4490cf80 clears F9010030/F901002C,
     * then writes 1 to this control register until reset occurs. Merely
     * latching that request leaves the CPU spinning forever at 4490cf98.
     * Cover this observed immediate-reset command; countdown watchdog
     * timing and other control values are not implemented by this path.
     * QEMU performs the reset outside this MMIO callback, preserving the
     * board's existing nonvolatile-storage and reset-handler semantics.
     */
    if (addr == 0xf9010014 && size == 4 && value == 1) {
        qemu_system_reset_request(SHUTDOWN_CAUSE_GUEST_RESET);
        return;
    }
    if (s->mask_rom_file && addr >= 0xf9090000 && addr + size <= 0xf9090002) { return; }
    if (addr >= 0xfe004000 && addr < 0xfe004060 && size == 4) {
        unsigned index = (addr - 0xfe004000) / 4;
        if (index < 12) { s->mmu_range[index] = value; }
        else { s->mmu_attr[index - 12] = value; }
        return;
    }
    if ((addr >= 0x46000000 && addr < 0x4c000000) ||
        (addr >= 0x4c800000 && addr < 0x54000000)) {
        return;
    }
    if (addr == 0x14000000 && size == 4) {
        /* DB2010 EMC control: bit6 remaps the low window from the NOR boot
         * alias to DRAM. EROM (0x40) and MAIN (0x77) both set it before
         * installing low-memory vectors and workspace. */
        s->emc_control = value;
        memory_region_set_enabled(&s->low_ram, s->erom_file && (value & 0x40));
    }
    if (addr >= 0xfb020100 && addr < 0xfb020400 && size == 4) {
        w800_intc_write(s, addr, value); return;
    }
    if (addr == 0xf2000008 && size == 4) {
        s->dma_tc_raw &= ~value;
        w800_irq_update(s);
        return;
    }
    if (addr == 0xf9010000) {
        bool timer2_changed = (s->timer_control ^ value) & 2;
        if (!(s->timer_control & 1) && (value & 1)) {
            s->timer_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->timer_expired = false;
        }
        s->timer_control = value;
        w800_timer_arm(s);
        if (timer2_changed) { w800_timer2_arm(s); }
        w800_irq_update(s); return;
    }
    if (addr == 0xf9010004) {
        /* The compare is a deadline in the current counter cycle. MAIN's
         * idle routine extends/shortens it without restarting that cycle.
         * Reload an expired cycle when MAIN programs the next deadline. */
        if (s->timer_expired) {
            s->timer_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->timer_expired = false;
        }
        s->timer_compare = value;
        s->timer_pending = false;
        w800_irq_update(s); w800_timer_arm(s); return;
    }
    if (addr == 0xf9010008) {
        s->timer2_compare = value;
        s->timer2_pending = false;
        w800_timer2_arm(s);
        w800_irq_update(s); return;
    }
    if (addr == 0xf9010018) {
        s->timer2_pending = false;
        s->timer_pending = false;
        w800_irq_update(s); return;
    }
    w800_unknown(s, addr, size, true, value);
    if (s->exploratory) {
        for (unsigned i = 0; i < size; i++) {
            g_hash_table_insert(s->latches, GUINT_TO_POINTER((uint32_t)addr + i),
                                GUINT_TO_POINTER((value >> (i * 8)) & 255));
        }
    }
}
static const MemoryRegionOps w800_unknown_ops = {
    .read = w800_read, .write = w800_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
};
/* ARM DDI0198E 2-29: physical base [31:12], fixed size [5:2], enable [0].
 * MAIN requests 64 KiB ITCM at zero and DTCM at 0x42000000.
 * QEMU's generic ARM926 omits these CP15 registers. */
static uint64_t w800_tcm_read(CPUARMState *env, const ARMCPRegInfo *ri)
{
    W800State *s = W800_MACHINE(qdev_get_machine());
    return s->tcm[ri->opc2];
}
static void w800_tcm_write(CPUARMState *env, const ARMCPRegInfo *ri, uint64_t value)
{
    W800State *s = W800_MACHINE(qdev_get_machine());
    MemoryRegion *mr = ri->opc2 ? &s->vectors : &s->dtcm;
    s->tcm[ri->opc2] = (value & 0xfffff001) | 0x1c;
    memory_region_transaction_begin();
    memory_region_set_address(mr, value & 0xfffff000);
    memory_region_set_enabled(mr, value & 1);
    memory_region_transaction_commit();
}
static const ARMCPRegInfo w800_tcm_regs[] = {
    { .name = "W800_DTCM", .cp = 15, .crn = 9, .crm = 1, .opc1 = 0, .opc2 = 0,
      .access = PL1_RW, .type = ARM_CP_NO_RAW | ARM_CP_IO,
      .readfn = w800_tcm_read, .writefn = w800_tcm_write },
    { .name = "W800_ITCM", .cp = 15, .crn = 9, .crm = 1, .opc1 = 0, .opc2 = 1,
      .access = PL1_RW, .type = ARM_CP_NO_RAW | ARM_CP_IO,
      .readfn = w800_tcm_read, .writefn = w800_tcm_write },
};
static void w800_reset(void *opaque)
{
    W800State *s = opaque;
    cpu_reset(CPU(s->cpu));
    s->cpu->env.regs[15] = s->erom_file ? 0 : 0x44020000;
    s->reported = false;
    s->accesses = 0;
    memset(s->irq_mask, 255, sizeof(s->irq_mask));
    memset(s->irq_pending, 0, sizeof(s->irq_pending));
    for (unsigned i = 0; i < 3; i++) { s->irq_vector[i] = 0x100; }
    memset(s->irq_config, 0, sizeof(s->irq_config));
    s->timer_control = s->timer_compare = 0;
    s->timer_pending = s->timer_expired = false;
    s->timer_epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    timer_del(s->ostick);
    timer_del(s->timer2);
    s->timer2_compare = 0;
    s->timer2_pending = false;
    w800_irq_update(s);
    s->tcm[0] = s->tcm[1] = 0x1c;
    s->emc_control = 0;
    memory_region_set_enabled(&s->low_ram, false);
    memset(s->mmu_range, 0, sizeof(s->mmu_range));
    memset(s->mmu_attr, 0, sizeof(s->mmu_attr));
    memory_region_set_enabled(&s->vectors, false);
    memory_region_set_enabled(&s->dtcm, false);
    g_hash_table_remove_all(s->latches);
    w800_nand_reset(s);
    w800_i2c_reset(s);
    w800_keypad_reset(s);
    /* Flash-mode entry on DB2010 is a held 'C' key at EROM startup. */
    if (s->flash) {
        s->keypad_keys[Q_KEY_CODE_BACKSPACE] = true;
        timer_mod(s->keypad_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 5000000);
    }
    w800_lcd_reset(s);
    if (s->camera) { w800_camera_reset(s); }
    w800_dsp_reset(s);
    w800_bt_reset(s);
    w800_uart_reset(s);
    w800_sif_reset(s);
    w800_irq_update(s);
}
static void w800_init(MachineState *machine)
{
    W800State *s = W800_MACHINE(machine);
    if (s->nor_otp_file && !s->mask_rom_file) {
        error_report("W800 nor-otp requires a supplied mask-rom image");
        exit(1);
    }
    MemoryRegion *sysmem = get_system_memory();
    s->ostick = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_tick, s);
    s->timer2 = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_tick2, s);
    s->i2c_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_i2c_complete, s);
    s->dsp_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_dsp_reply_announce, s);
    s->latches = g_hash_table_new(g_direct_hash, g_direct_equal);
    s->cpu = ARM_CPU(cpu_create(machine->cpu_type));
    w800_keypad_init(s, sysmem);
    w800_lcd_init(s);
    if (s->camera_transport || s->host_camera) { w800_camera_init(s); }
    w800_rtc_init(s, sysmem);
    w800_bt_init(s, sysmem);
    w800_uart_init(s, sysmem);
    w800_sif_init(s, sysmem);
    s->dsp_memory = g_new0(uint32_t, 0x400000);
    memory_region_init_io(&s->dsp_port, OBJECT(s), &w800_dsp_ops, s, "w800.dsp-host", 0xc0);
    memory_region_add_subregion(sysmem, 0xfa040000, &s->dsp_port);
    /* Low DRAM/SRAM window: shadowed by the EROM boot alias until the EMC
     * remap bit flips it in. */
    memory_region_init_ram(&s->low_ram, NULL, "w800.low-ram", 0x400000, &error_fatal);
    memory_region_set_enabled(&s->low_ram, false);
    memory_region_add_subregion_overlap(sysmem, 0, &s->low_ram, -40);
    memory_region_init_io(&s->unknown, OBJECT(machine), &w800_unknown_ops, s,
                          "w800.unmodeled", UINT64_C(0x100000000));
    memory_region_add_subregion_overlap(sysmem, 0, &s->unknown, -100);
    if (s->mask_rom_file) {
        int64_t length = get_image_size(s->mask_rom_file, &error_fatal);
        /* Supported raw-image format, not a claim about physical ROM size.
         * The known MAIN API table references the high FFFF0000 window. */
        if (length < 0x4000 || length > 0x10000 || (length & 0xfff)) {
            error_report("W800 mask-rom must be a raw 16–64 KiB image, in 4 KiB increments, based at 0xffff0000");
            exit(1);
        }
        memory_region_init_rom(&s->mask_rom, NULL, "w800.mask-rom", length, &error_fatal);
        memory_region_add_subregion(sysmem, 0xffff0000, &s->mask_rom);
        if (load_image_targphys(s->mask_rom_file, 0xffff0000, length, NULL) != length) {
            error_report("W800 could not load the complete mask-rom image");
            exit(1);
        }
    }
    memory_region_init_ram(&s->iram, NULL, "w800.iram", 0x10000, &error_fatal);
    memory_region_add_subregion(sysmem, 0xf3000000, &s->iram);
    /* MAIN scatter table at 44133840/4c initializes this internal RAM. */
    memory_region_init_ram(&s->shared_ram, NULL, "w800.shared-ram", 0x10000, &error_fatal);
    memory_region_add_subregion(sysmem, 0xf6000000, &s->shared_ram);
    memory_region_add_subregion(sysmem, 0x4c000000, machine->ram);
    s->nand_data = g_malloc(W800_NAND_BYTES);
    memset(s->nand_data, 255, W800_NAND_BYTES);
    memory_region_init_io(&s->nand_port, OBJECT(machine), &w800_nand_ops, s,
                         "w800.nand-port", 0x100);
    memory_region_add_subregion(sysmem, 0x50000000, &s->nand_port);
    memory_region_init_io(&s->i2c_port, OBJECT(machine), &w800_i2c_ops, s, "w800.i2c", 8);
    memory_region_add_subregion(sysmem, 0xf9040000, &s->i2c_port);
    /* Intel89/880D,32MiB. The W800 CFI extension implements255x128KiB plus
     * four top32KiB erase blocks. No writable host block backend is used. */
    DeviceState *flash = qdev_new(TYPE_PFLASH_CFI01);
    qdev_prop_set_string(flash, "name", "w800.flash");
    qdev_prop_set_uint32(flash, "num-blocks", 512);
    qdev_prop_set_uint64(flash, "sector-length", 0x10000);
    qdev_prop_set_uint8(flash, "width", 2);
    qdev_prop_set_uint8(flash, "device-width", 2);
    qdev_prop_set_bit(flash, "w800-read-compat", true);
    /* The provisional MAIN-only profile predates protection-field support.
     * Enabling that field also exposes original chip-specific ETX/KGEN calls;
     * retain the existing profile until their ROM backing is available.
     * This coupling is a bring-up limitation, not physical NOR geometry. */
    qdev_prop_set_bit(flash, "w800-rom-layout", s->mask_rom_file != NULL);
    if (s->nor_otp_file) {
        qdev_prop_set_string(flash, "w800-otp-file", s->nor_otp_file);
    }
    qdev_prop_set_uint16(flash, "id0", 0x89);
    qdev_prop_set_uint16(flash, "id1", 0x880d);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(flash), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(flash), 0, 0x44000000);
    define_arm_cp_regs(s->cpu, w800_tcm_regs);
    /* TCM regions configured by MAIN at 0x44020ec4..0x44020ee8.
     * 64 KiB sizes follow MAIN register values; these are not boot-ROM replacements. */
    memory_region_init_ram(&s->vectors, NULL, "w800.itcm", 0x10000, &error_fatal);
    memory_region_init_ram(&s->dtcm, NULL, "w800.dtcm", 0x10000, &error_fatal);
    memory_region_add_subregion(sysmem, 0x42000000, &s->dtcm);
    memory_region_add_subregion(sysmem, 0, &s->vectors);
    /* Seed private flash storage once. load_image_targphys registers a ROM
     * reset image and would overwrite guest-written nonvolatile data on every
     * system_reset. A phone reset must retain those program/erase results. */
    MemoryRegion *flash_memory = pflash_cfi01_get_memory(PFLASH_CFI01(flash));
    if (!machine->firmware ||
        get_image_size(machine->firmware, &error_fatal) != 0x02000000 ||
        load_image_size(machine->firmware, memory_region_get_ram_ptr(flash_memory),
                        0x02000000) != 0x02000000) {
        error_report("W800 requires a 32 MiB prepared flash image via -bios");
        exit(1);
    }
    /* Optional EROM image occupies the first 128 KiB of NOR, exactly where
     * physical DB2010 keeps it. MAIN-only sessions leave it blank; when
     * supplied, the reset vector enters EROM and its own key check decides
     * between a normal jump to MAIN and the serial flash listen mode. */
    if (s->erom_file) {
        int64_t length = get_image_size(s->erom_file, &error_fatal);
        if (length < 0x4000 || length > 0x20000 || (length & 0xfff)) {
            error_report("W800 erom must be a raw 16-128 KiB image, in 4 KiB increments");
            exit(1);
        }
        if (load_image_size(s->erom_file, memory_region_get_ram_ptr(flash_memory),
                            length) != length) {
            error_report("W800 could not load the complete erom image");
            exit(1);
        }
        /* Cold-boot bottom alias: the NOR's first 128 KiB also appears at
         * address 0 until firmware remaps, so the EROM vector table is the
         * real reset entry. Sits below the CP15-managed vector ITCM (0) and
         * above the unmodeled fallback (-100). */
        memory_region_init_alias(&s->erom_alias, NULL, "w800.erom-boot",
                                 flash_memory, 0, 0x20000);
        memory_region_add_subregion_overlap(sysmem, 0, &s->erom_alias, -50);
    }
    qemu_register_reset(w800_reset, s);
}
static bool w800_get_exploratory(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->exploratory;
}
static void w800_set_exploratory(Object *obj, bool value, Error **errp)
{
    W800_MACHINE(obj)->exploratory = value;
}
static bool w800_get_camera_transport(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->camera_transport;
}
static void w800_set_camera_transport(Object *obj, bool value, Error **errp)
{
    W800_MACHINE(obj)->camera_transport = value;
}
static bool w800_get_host_camera(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->host_camera;
}
static void w800_set_host_camera(Object *obj, bool value, Error **errp)
{
    W800_MACHINE(obj)->host_camera = value;
}
static bool w800_get_camera_paused(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->camera_paused;
}
static void w800_set_camera_paused(Object *obj, bool value, Error **errp)
{
    W800State *s = W800_MACHINE(obj);
    if (s->camera_paused != value) {
        s->camera_paused = value;
        w800_camera_host_pause(s);
    }
}
static void w800_get_camera_host_status(Object *obj, Visitor *v, const char *name,
                                         void *opaque, Error **errp)
{
    W800State *s = W800_MACHINE(obj);
    int64_t value = s->host_camera ? w800_host_camera_status() : 0;
    visit_type_int64(v, name, &value, errp);
}
static void w800_get_camera_frames(Object *obj, Visitor *v, const char *name,
                                    void *opaque, Error **errp)
{
    W800State *s = W800_MACHINE(obj);
    int64_t value = s->camera ? s->camera->frames_written : 0;
    visit_type_int64(v, name, &value, errp);
}
static void w800_get_limit(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    visit_type_uint32(v, name, &W800_MACHINE(obj)->mmio_limit, errp);
}
static void w800_set_limit(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    uint32_t value;
    if (!visit_type_uint32(v, name, &value, errp)) { return; }
    if (value < 1000 || value > 1000000) {
        error_setg(errp, "mmio-limit must be between 1000 and 1000000"); return;
    }
    W800_MACHINE(obj)->mmio_limit = value;
}
static bool w800_get_usb(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->usb_attached;
}
static void w800_set_usb(Object *obj, bool value, Error **errp)
{
    W800State *s = W800_MACHINE(obj);
    if (s->usb_attached != value) {
        s->usb_attached = value;
        /* Before machine init there is no IRQ line yet; reset applies it. */
        if (s->cpu) { w800_power_vbus(s, value); }
    }
}
static bool w800_get_flash(Object *obj, Error **errp)
{
    return W800_MACHINE(obj)->flash;
}
static void w800_set_flash(Object *obj, bool value, Error **errp)
{
    W800_MACHINE(obj)->flash = value;
}
static char *w800_get_erom(Object *obj, Error **errp)
{
    return g_strdup(W800_MACHINE(obj)->erom_file);
}
static void w800_set_erom(Object *obj, const char *value, Error **errp)
{
    if (!value || !*value) {
        error_setg(errp, "erom must name a raw EROM image"); return;
    }
    g_free(W800_MACHINE(obj)->erom_file);
    W800_MACHINE(obj)->erom_file = g_strdup(value);
}
static void w800_instance_init(Object *obj)
{
    W800_MACHINE(obj)->mmio_limit = 10000;
    W800_MACHINE(obj)->chip_id = 0x8040;
}
static char *w800_get_mask_rom(Object *obj, Error **errp)
{
    return g_strdup(W800_MACHINE(obj)->mask_rom_file);
}
static void w800_set_mask_rom(Object *obj, const char *value, Error **errp)
{
    if (!value || !*value) {
        error_setg(errp, "mask-rom must name a raw ROM image"); return;
    }
    g_free(W800_MACHINE(obj)->mask_rom_file);
    W800_MACHINE(obj)->mask_rom_file = g_strdup(value);
}
static void w800_get_chip_id(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    visit_type_uint16(v, name, &W800_MACHINE(obj)->chip_id, errp);
}
static void w800_set_chip_id(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    uint16_t value;
    if (!visit_type_uint16(v, name, &value, errp)) { return; }
    if (value != 0x8000 && value != 0x8040) {
        error_setg(errp, "chip-id must be 0x8000 or 0x8040 for DB2010"); return;
    }
    W800_MACHINE(obj)->chip_id = value;
}
static void w800_get_virtual_time(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    visit_type_int64(v, name, &now, errp);
}
static void w800_instance_finalize(Object *obj)
{
    g_free(W800_MACHINE(obj)->mask_rom_file);
    g_free(W800_MACHINE(obj)->nor_otp_file);
    g_free(W800_MACHINE(obj)->erom_file);
}
static char *w800_get_nor_otp(Object *obj, Error **errp)
{
    return g_strdup(W800_MACHINE(obj)->nor_otp_file);
}
static void w800_set_nor_otp(Object *obj, const char *value, Error **errp)
{
    if (!value || !*value) {
        error_setg(errp, "nor-otp must name a raw 18-byte protection-field image"); return;
    }
    g_free(W800_MACHINE(obj)->nor_otp_file);
    W800_MACHINE(obj)->nor_otp_file = g_strdup(value);
}
static void w800_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    mc->desc = "Sony Ericsson W800i (DB2010, experimental firmware bring-up)";
    mc->init = w800_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("arm926");
    mc->default_ram_size = 8 * 1024 * 1024;
    mc->default_ram_id = "w800.ram";
    mc->max_cpus = 1;
    object_class_property_add_str(oc, "mask-rom", w800_get_mask_rom, w800_set_mask_rom);
    object_class_property_add_str(oc, "nor-otp", w800_get_nor_otp, w800_set_nor_otp);
    object_class_property_set_description(oc, "nor-otp",
        "Optional raw 18-byte image of NOR words 80h..88h in little-endian order; requires mask-rom");
    object_class_property_set_description(oc, "mask-rom",
        "User-supplied raw DB2010 ROM at FFFF0000; also enables revision ID and unprovisioned NOR protection layout");
    object_class_property_add(oc, "chip-id", "uint16", w800_get_chip_id, w800_set_chip_id, NULL, NULL);
    object_class_property_add(oc, "virtual-time-ns", "int64", w800_get_virtual_time, NULL, NULL, NULL);
    object_class_property_set_description(oc, "virtual-time-ns",
        "Read-only QEMU clock for comparing guest scheduler and peripheral progress");
    object_class_property_set_description(oc, "chip-id",
        "DB2010 revision for supplied mask-rom: 0x8000 or 0x8040 (default)");
    object_class_property_add(oc, "mmio-limit", "uint32", w800_get_limit, w800_set_limit, NULL, NULL);
    object_class_property_add_bool(oc, "exploratory", w800_get_exploratory,
                                   w800_set_exploratory);
    object_class_property_add_bool(oc, "camera-transport", w800_get_camera_transport,
                                   w800_set_camera_transport);
    object_class_property_add_bool(oc, "host-camera", w800_get_host_camera,
                                   w800_set_host_camera);
    object_class_property_add(oc, "camera-host-status", "int64", w800_get_camera_host_status, NULL, NULL, NULL);
    object_class_property_add(oc, "camera-frames", "int64", w800_get_camera_frames, NULL, NULL, NULL);
    object_class_property_add_bool(oc, "camera-paused", w800_get_camera_paused,
                                   w800_set_camera_paused);
    object_class_property_set_description(oc, "host-camera",
        "Opt-in local host camera source for original CAMIF preview DMA");
    object_class_property_set_description(oc, "camera-transport",
        "Opt-in camera register transport research; no host frames or capture completion");
    object_class_property_set_description(oc, "exploratory",
        "Unverified register latches for boot research; not hardware emulation");
    object_class_property_add_str(oc, "erom", w800_get_erom, w800_set_erom);
    object_class_property_set_description(oc, "erom",
        "Raw EROM image loaded at NOR 44000000; reset then enters EROM, which decides between MAIN and serial flash listen mode");
    object_class_property_add_bool(oc, "usb-cable", w800_get_usb, w800_set_usb);
    object_class_property_set_description(oc, "usb-cable",
        "USB cable (DCU-60) VBUS state; writable at runtime for attach/detach");
    object_class_property_add_bool(oc, "flash", w800_get_flash, w800_set_flash);
    object_class_property_set_description(oc, "flash",
        "Hold the 'C' key at reset so EROM enters its serial flash listen mode");
}
static const TypeInfo w800_type = {
    .name = TYPE_W800_MACHINE, .parent = TYPE_MACHINE,
    .interfaces = arm_machine_interfaces,
    .instance_size = sizeof(W800State), .class_init = w800_class_init,
    .instance_init = w800_instance_init,
    .instance_finalize = w800_instance_finalize,
};
static void w800_register_types(void) { type_register_static(&w800_type); }
type_init(w800_register_types)
