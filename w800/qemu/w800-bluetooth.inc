/* Offline HCI controller on the observed DB2010 UART3 transport.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * UART offsets are inferred from R1L002's original UART driver. H4 events use
 * Bluetooth command-complete framing. Unknown commands return status 0x01;
 * this device does not implement RF, pairing, controller firmware download,
 * or substitute firmware/UI validation results.
 * Standard packet layouts cross-checked against Linux include/net/bluetooth/hci.h:
 * https://github.com/torvalds/linux/blob/master/include/net/bluetooth/hci.h
 */
struct W800Bluetooth {
    W800State *board;
    MemoryRegion port;
    uint16_t regs[24];
    uint8_t command[260], rx[4096];
    unsigned command_size, rx_head, rx_size;
    uint16_t pending;
    uint64_t commands;
    uint8_t event_filter[255], event_filter_size;
    uint8_t host_buffer_size[7], host_flow_control, authentication_enable;
    uint8_t local_name[248], class_of_device[3], scan_enable;
    uint8_t settings[0x80][4];
    uint16_t default_link_policy;
    uint8_t iac[6], iac_count;
};
/* Paired Read/Write controller settings, in octets. These settings only
 * affect future links; no radio peers or connection requests are generated. */
static unsigned w800_bt_setting_size(uint16_t opcode)
{
    switch (opcode) {
    case 0x0c0a: return 1; /* PIN type */
    case 0x0c16: return 2; /* connection accept timeout */
    case 0x0c18: return 2; /* page timeout */
    case 0x0c1c: return 4; /* page scan activity */
    case 0x0c1e: return 4; /* inquiry scan activity */
    case 0x0c22: return 1; /* encryption mode */
    case 0x0c26: return 2; /* voice setting */
    case 0x0c43: return 1; /* inquiry scan type */
    case 0x0c45: return 1; /* inquiry mode */
    case 0x0c47: return 1; /* page scan type */
    default: return 0;
    }
}
static uint16_t w800_bt_interrupts(struct W800Bluetooth *b)
{
    if (!b) { return 0; }
    return (b->pending | 2 | (b->rx_size ? 0x41 : 0)) & b->regs[0x12 / 2];
}
static bool w800_bt_irq_pending(W800State *s)
{
    return w800_bt_interrupts(s->bluetooth) != 0;
}
static void w800_bt_enqueue(struct W800Bluetooth *b, const uint8_t *data, unsigned size)
{
    if (size > sizeof(b->rx) - b->rx_size) {
        qemu_log_mask(LOG_GUEST_ERROR, "W800_BT RX queue full\n");
        return;
    }
    for (unsigned i = 0; i < size; i++) {
        b->rx[(b->rx_head + b->rx_size++) % sizeof(b->rx)] = data[i];
    }
}
static void w800_bt_command(struct W800Bluetooth *b)
{
    uint16_t opcode = lduw_le_p(b->command + 1);
    uint8_t result[256] = {0};
    unsigned size = 1;
    if ((opcode == 0x0c03 || opcode == 0x0c1f || opcode == 0x0c14 ||
         opcode == 0x0c23 || opcode == 0x0c19 || opcode == 0x1001 ||
         opcode == 0x1003 || opcode == 0x1005 || opcode == 0x1009) && b->command[3]) {
        result[0] = 0x12; /* Invalid HCI Command Parameters */
        goto respond;
    }
    switch (opcode) {
    case 0x0c03: /* Reset */
        b->event_filter_size = b->host_flow_control = b->authentication_enable = 0;
        memset(b->host_buffer_size, 0, sizeof(b->host_buffer_size));
        memset(b->local_name, 0, sizeof(b->local_name));
        memset(b->class_of_device, 0, sizeof(b->class_of_device));
        b->scan_enable = 0;
        memset(b->settings, 0, sizeof(b->settings));
        b->default_link_policy = 0;
        b->iac_count = 1;
        b->iac[0] = 0x33; b->iac[1] = 0x8b; b->iac[2] = 0x9e;
        break;
    case 0x0c05: /* Set Event Filter: retained for this RF-free controller */
        if (b->command[3] >= 1 && b->command[4] <= 2) {
            b->event_filter_size = b->command[3];
            memcpy(b->event_filter, b->command + 4, b->event_filter_size);
        } else { result[0] = 0x12; }
        break;
    case 0x0c33: /* Host Buffer Size */
        if (b->command[3] == 7) { memcpy(b->host_buffer_size, b->command + 4, 7); }
        else { result[0] = 0x12; }
        break;
    case 0x0c31: /* Set Controller To Host Flow Control */
        if (b->command[3] == 1 && b->command[4] <= 3) { b->host_flow_control = b->command[4]; }
        else { result[0] = 0x12; }
        break;
    case 0x0c20: /* Write Authentication Enable */
        if (b->command[3] == 1 && b->command[4] <= 1) { b->authentication_enable = b->command[4]; }
        else { result[0] = 0x12; }
        break;
    case 0x0c1f: /* Read Authentication Enable */
        result[1] = b->authentication_enable; size = 2;
        break;
    case 0x0c13: /* Write Local Name */
        if (b->command[3] == 248) { memcpy(b->local_name, b->command + 4, 248); }
        else { result[0] = 0x12; }
        break;
    case 0x0c14: /* Read Local Name */
        memcpy(result + 1, b->local_name, 248); size = 249;
        break;
    case 0x0c24: /* Write Class Of Device */
        if (b->command[3] == 3) { memcpy(b->class_of_device, b->command + 4, 3); }
        else { result[0] = 0x12; }
        break;
    case 0x0c23: /* Read Class Of Device */
        memcpy(result + 1, b->class_of_device, 3); size = 4;
        break;
    case 0x0c1a: /* Write Scan Enable: no remote RF peers are present */
        if (b->command[3] == 1 && b->command[4] <= 3) { b->scan_enable = b->command[4]; }
        else { result[0] = 0x12; }
        break;
    case 0x0c19: /* Read Scan Enable */
        result[1] = b->scan_enable; size = 2;
        break;
    case 0x080f: /* Write Default Link Policy Settings */
        if (b->command[3] == 2) { b->default_link_policy = lduw_le_p(b->command + 4); }
        else { result[0] = 0x12; }
        break;
    case 0x080e: /* Read Default Link Policy Settings */
        stw_le_p(result + 1, b->default_link_policy); size = 3;
        break;
    case 0x0c0d: /* Read Stored Link Key: no keys in this offline controller */
        if (b->command[3] == 7) { size = 5; }
        else { result[0] = 0x12; }
        break;
    case 0x0c38: /* Read Number Of Supported IAC */
        if (!b->command[3]) { result[1] = 2; size = 2; }
        else { result[0] = 0x12; }
        break;
    case 0x0c39: /* Read Current IAC LAP */
        if (!b->command[3]) {
            result[1] = b->iac_count;
            memcpy(result + 2, b->iac, b->iac_count * 3);
            size = 2 + b->iac_count * 3;
        } else { result[0] = 0x12; }
        break;
    case 0x0c3a: /* Write Current IAC LAP */
        if (b->command[3] >= 4 && b->command[4] >= 1 && b->command[4] <= 2 &&
            b->command[3] == 1 + 3 * b->command[4]) {
            b->iac_count = b->command[4];
            memcpy(b->iac, b->command + 5, b->iac_count * 3);
        } else { result[0] = 0x12; }
        break;
    case 0x1001: /* Read Local Version Information: provisional HCI 1.2 */
        result[1] = 2; result[2] = 1; result[4] = 2;
        result[5] = 0; result[6] = 0; result[7] = 1;
        size = 9;
        break;
    case 0x1003: /* Read Local Supported Features: basic BR only */
        size = 9;
        break;
    case 0x1005: /* Read Buffer Size: bounded virtual ACL/SCO queues */
        result[1] = 0xa0; result[2] = 1; result[3] = 0;
        result[4] = 4; size = 8;
        break;
    case 0x1009: /* Read BD_ADDR: locally administered emulated address */
        result[1] = 1; result[2] = 0; result[3] = 0;
        result[4] = 0; result[5] = 0; result[6] = 2; size = 7;
        break;
    default:
        if (w800_bt_setting_size(opcode)) {
            unsigned length = w800_bt_setting_size(opcode);
            if (b->command[3] == length) {
                memcpy(b->settings[opcode & 0x7f], b->command + 4, length);
            } else { result[0] = 0x12; }
        } else if (w800_bt_setting_size(opcode + 1)) {
            if (!b->command[3]) {
                size = 1 + w800_bt_setting_size(opcode + 1);
                memcpy(result + 1, b->settings[(opcode + 1) & 0x7f], size - 1);
            } else { result[0] = 0x12; }
        } else { result[0] = 1; } /* Unknown HCI Command */
        break;
    }
respond:;
    uint8_t event[262] = {4, 0x0e, size + 3, 1, opcode & 255, opcode >> 8};
    memcpy(event + 6, result, size);
    w800_bt_enqueue(b, event, size + 6);
    b->commands++;
    qemu_log_mask(LOG_UNIMP, "W800_BT HCI opcode=0x%04x parameters=%u status=0x%02x\n",
                  opcode, b->command[3], result[0]);
}
static void w800_bt_tx(struct W800Bluetooth *b, uint8_t value)
{
    if (!b->command_size && value != 1) {
        qemu_log_mask(LOG_UNIMP, "W800_BT unsupported H4 type=0x%02x\n", value);
        return;
    }
    b->command[b->command_size++] = value;
    if (b->command_size >= 4 && b->command_size == 4 + b->command[3]) {
        w800_bt_command(b);
        b->command_size = 0;
    }
}
static uint64_t w800_bt_read(void *opaque, hwaddr offset, unsigned size)
{
    struct W800Bluetooth *b = opaque;
    uint16_t value = 0;
    switch (offset) {
    case 0:
        if (b->rx_size) {
            value = b->rx[b->rx_head++ % sizeof(b->rx)];
            b->rx_size--;
        }
        break;
    case 0x0e: value = 0x10; break; /* transmitter idle */
    case 0x14: value = w800_bt_interrupts(b); break;
    case 0x2c: value = 256; break; /* transmit FIFO capacity */
    case 0x2e: value = MIN(b->rx_size, 256); break;
    default: value = b->regs[offset / 2]; break;
    }
    w800_irq_update(b->board);
    return value;
}
static void w800_bt_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    struct W800Bluetooth *b = opaque;
    switch (offset) {
    case 2: w800_bt_tx(b, value); break;
    case 0x14: b->pending &= ~value; break;
    default: b->regs[offset / 2] = value; break;
    }
    w800_irq_update(b->board);
}
static const MemoryRegionOps w800_bt_ops = {
    .read = w800_bt_read, .write = w800_bt_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 2, .max_access_size = 2 },
    .impl = { .min_access_size = 2, .max_access_size = 2 },
};
static void w800_bt_reset(W800State *s)
{
    struct W800Bluetooth *b = s->bluetooth;
    memset(b->regs, 0, sizeof(b->regs));
    b->command_size = b->rx_head = b->rx_size = b->pending = 0;
    b->commands = 0;
    b->event_filter_size = b->host_flow_control = b->authentication_enable = 0;
    b->scan_enable = 0;
    memset(b->host_buffer_size, 0, sizeof(b->host_buffer_size));
    memset(b->local_name, 0, sizeof(b->local_name));
    memset(b->class_of_device, 0, sizeof(b->class_of_device));
    memset(b->settings, 0, sizeof(b->settings));
    b->default_link_policy = 0;
    b->iac_count = 1;
    b->iac[0] = 0x33; b->iac[1] = 0x8b; b->iac[2] = 0x9e;
}
static void w800_bt_init(W800State *s, MemoryRegion *sysmem)
{
    struct W800Bluetooth *b = g_new0(struct W800Bluetooth, 1);
    s->bluetooth = b;
    b->board = s;
    memory_region_init_io(&b->port, OBJECT(s), &w800_bt_ops, b, "w800.bluetooth-uart", 0x30);
    memory_region_add_subregion(sysmem, 0xfc030000, &b->port);
}
