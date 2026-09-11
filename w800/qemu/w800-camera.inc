/* Sony KNC20115 control transport, from supplied camdriver0.dat.
 * GPL-2.0-or-later. Only real host frames complete preview DMA.
 * Sensor-only mode remains available for register tests without host access.
 */
#include "w800-camera-host.h"
#include "w800-camera-pixels.h"

typedef struct W800Camera {
    W800State *machine;
    MemoryRegion receiver_port, control_port;
    uint32_t receiver[12], control;
    uint8_t registers[256], properties[256];
    uint8_t command[256], reply[256];
    unsigned command_bytes, reply_bytes, reply_position;
    bool mode_requested, mode_pending, reply_event;
    uint8_t events[16];
    unsigned event_head, event_count;
    QEMUTimer *frame_timer;
    uint8_t *host_frame, *frame;
    unsigned host_width, host_height, host_stride;
    uint64_t host_sequence, consumed_sequence, frames_written;
    int64_t next_frame_ns;
} W800Camera;

static bool w800_camera_irq_pending(W800State *s)
{
    return s->camera &&
        (s->camera->receiver[0x20 / 4] & s->camera->receiver[0x24 / 4]);
}

static bool w800_camera_host_enabled(W800Camera *camera)
{
    return camera->machine->host_camera && !camera->machine->camera_paused &&
        (camera->control & 0x100);
}

static void w800_camera_event(W800Camera *camera, uint8_t event)
{
    if (camera->event_count == G_N_ELEMENTS(camera->events)) {
        qemu_log_mask(LOG_UNIMP, "W800_CAMERA_EVENT_FULL event=%02x\n", event);
        return;
    }
    camera->events[(camera->event_head + camera->event_count) % 16] = event;
    camera->event_count++;
    qemu_log_mask(LOG_UNIMP, "W800_CAMERA_EVENT event=%02x host_sequence=%"PRIu64"\n",
                  event, camera->host_sequence);
}

static void w800_camera_poll(void *opaque)
{
    W800Camera *camera = opaque;
    W800State *s = camera->machine;
    if (!w800_camera_host_enabled(camera)) { return; }
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    timer_mod(camera->frame_timer, now + 10000000);
    int copied = w800_host_camera_copy(camera->host_frame, W800_HOST_CAMERA_CAPACITY,
                                      &camera->host_width, &camera->host_height,
                                      &camera->host_stride, &camera->host_sequence);
    if (copied != 1 || !camera->host_sequence) { return; }
    if (camera->mode_pending) {
        camera->mode_pending = false;
        w800_camera_event(camera, 0xea);
    }
    if (!camera->mode_requested || !(camera->receiver[0] & 1) ||
        now < camera->next_frame_ns ||
        camera->host_sequence == camera->consumed_sequence) { return; }
    unsigned bytes = camera->receiver[1], row_bytes = camera->receiver[3];
    unsigned height = camera->receiver[4], width = row_bytes / 2;
    if (camera->receiver[5] != 0xb1 || !row_bytes || (row_bytes & 3) ||
        !height || width > 640 || height > 480 || bytes != row_bytes * height) {
        return;
    }
    for (unsigned ch = 0; ch < 8; ch++) {
        uint8_t *dma = s->lcd->dma + ch * 32;
        uint32_t source = ldl_le_p(dma), destination = ldl_le_p(dma + 4);
        uint32_t lli = ldl_le_p(dma + 8), control = ldl_le_p(dma + 12);
        uint32_t config = ldl_le_p(dma + 16);
        if (!(config & 1) || source != 0xf7000308) { continue; }
        /* Original channel1: peripheral-controlled flow6, fixed32-bit
         * FIFO source, incrementing32-bit RAM destination and no LLI. */
        if (((config >> 11) & 7) != 6 || lli ||
            ((control >> 18) & 7) != 2 || ((control >> 21) & 7) != 2 ||
            (control & (1u << 26)) || !(control & (1u << 27)) ||
            destination < 0x4c000000 || destination > 0x4c800000 - bytes) {
            qemu_log_mask(LOG_UNIMP, "W800_CAMERA_DMA_UNSUPPORTED ch=%u control=%08x config=%08x\n",
                          ch, control, config);
            return;
        }
        if (!w800_camera_bgra_yuyv(camera->host_frame, W800_HOST_CAMERA_CAPACITY,
                                  camera->host_width, camera->host_height,
                                  camera->host_stride, camera->frame, 640 * 480 * 2,
                                  width, height)) { return; }
        MemTxResult result = address_space_write(&address_space_memory, destination,
                                                 MEMTXATTRS_UNSPECIFIED,
                                                 camera->frame, bytes);
        if (result != MEMTX_OK) { return; }
        stl_le_p(dma + 4, destination + bytes);
        stl_le_p(dma + 16, config & ~1u);
        if (control & (1u << 31)) { s->dma_tc_raw |= 1u << ch; }
        camera->receiver[0x20 / 4] |= 2; /* actual frame boundary */
        camera->consumed_sequence = camera->host_sequence;
        camera->frames_written++;
        /* Driver normal08=3c with command34=81 means15fps; night08=1e
         * with34=01 also means15fps. The register is half-frame/s before
         * the alternate-frame selection (VR28 with81 gives10fps). */
        unsigned rate = camera->registers[8];
        unsigned divisor = camera->properties[0x34] & 0x80 ? 2 : 1;
        camera->next_frame_ns = now + (rate ? 2000000000LL * divisor / rate : 100000000);
        w800_irq_update(s);
        qemu_log_mask(LOG_UNIMP,
                      "W800_CAMERA_DMA host_sequence=%"PRIu64" frame=%"PRIu64" ch=%u destination=%08x width=%u height=%u bytes=%u format=YUYV\n",
                      camera->host_sequence, camera->frames_written, ch,
                      destination, width, height, bytes);
        return;
    }
}

static void w800_camera_transport(W800State *s)
{
    if (s->camera && w800_camera_host_enabled(s->camera)) {
        timer_mod(s->camera->frame_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1);
    }
}

static void w800_camera_host_pause(W800State *s)
{
    W800Camera *camera = s->camera;
    if (!camera || !s->host_camera) { return; }
    timer_del(camera->frame_timer);
    if (w800_camera_host_enabled(camera)) {
        camera->consumed_sequence = camera->host_sequence = 0;
        w800_host_camera_start();
        w800_camera_transport(s);
    } else {
        w800_host_camera_stop();
    }
}

static void w800_camera_reset(W800State *s)
{
    W800Camera *camera = s->camera;
    timer_del(camera->frame_timer);
    if (s->host_camera) { w800_host_camera_stop(); }
    memset(camera->receiver, 0, sizeof(camera->receiver));
    memset(camera->registers, 0, sizeof(camera->registers));
    memset(camera->properties, 0, sizeof(camera->properties));
    camera->command_bytes = camera->reply_bytes = camera->reply_position = 0;
    camera->control = 0;
    camera->mode_requested = camera->mode_pending = camera->reply_event = false;
    camera->event_head = camera->event_count = 0;
    camera->host_sequence = camera->consumed_sequence = 0;
    camera->frames_written = 0;
    camera->next_frame_ns = 0;
}

static bool w800_camera_address(W800State *s, bool reading)
{
    W800Camera *camera = s->camera;
    /* Original4474242c drives the external sensor reset through bit8:
     * clearing it asserts reset; setting it releases reset after power-up.
     */
    if (!camera || !(camera->control & 0x100)) { return false; }
    if (s->host_camera && !s->camera_paused &&
        w800_host_camera_status() >= W800_HOST_CAMERA_DENIED) { return false; }
    if (reading) { camera->reply_position = 0; }
    else { camera->command_bytes = 0; }
    return true;
}

static bool w800_camera_command(W800Camera *camera)
{
    const uint8_t *data = camera->command;
    unsigned length = camera->command_bytes;
    GString *bytes = g_string_new(NULL);
    for (unsigned i = 0; i < length; i++) {
        g_string_append_printf(bytes, "%s%02x", i ? " " : "", data[i]);
    }
    qemu_log_mask(LOG_UNIMP, "W800_CAMERA_COMMAND bytes=%s\n", bytes->str);
    g_string_free(bytes, true);
    if (length < 5) { return false; }
    unsigned address = data[2] << 8 | data[3];
    if (data[1] == 2) {
        if (address + length - 4 > sizeof(camera->registers)) { return false; }
        memcpy(camera->registers + address, data + 4, length - 4);
        if (address == 1 && length == 6) {
            unsigned operation = data[5], parameter = data[4];
            camera->properties[operation] = parameter;
            if (operation == 0x34 || operation == 0x32 || operation == 0x33) {
                camera->mode_requested = operation == 0x34;
                camera->mode_pending = operation == 0x34 && camera->machine->host_camera;
                w800_camera_transport(camera->machine);
                qemu_log_mask(LOG_UNIMP,
                              "W800_CAMERA_MODE_REQUEST command=%02x parameter=%02x host_enabled=%d\n",
                              operation, parameter, camera->machine->host_camera);
            }
        }
        return true;
    }
    if (data[1] == 1 && length == 5) {
        unsigned count = data[4];
        if (!count || count >= 255 ||
            address + count > sizeof(camera->registers)) { return false; }
        camera->reply[0] = count + 1;
        camera->reply_bytes = count + 1;
        camera->reply_position = 0;
        camera->reply_event = false;
        if (address == 3 && count == 3) {
            /* EMPTY_EVENT_FIFO reads length plus three bytes. Selection
             * peeks; only delivery of the fourth byte consumes the event.
             * Sensor-only mode has no completed transition to report. */
            memset(camera->reply + 1, 0, count);
            if (camera->event_count) {
                camera->reply[3] = camera->events[camera->event_head];
                camera->reply_event = true;
            }
        } else {
            memcpy(camera->reply + 1, camera->registers + address, count);
        }
        return true;
    }
    return false;
}

static bool w800_camera_write_byte(W800State *s, uint8_t value)
{
    W800Camera *camera = s->camera;
    if (!camera || !(camera->control & 0x100) ||
        camera->command_bytes >= sizeof(camera->command)) { return false; }
    if (!camera->command_bytes && value < 3) { return false; }
    camera->command[camera->command_bytes++] = value;
    if (camera->command_bytes < camera->command[0]) { return true; }
    if (camera->command_bytes == camera->command[0] &&
        w800_camera_command(camera)) { return true; }
    qemu_log_mask(LOG_UNIMP, "W800_CAMERA_UNSUPPORTED_COMMAND bytes=%u\n",
                  camera->command_bytes);
    return false;
}

static uint8_t w800_camera_read_byte(W800State *s)
{
    W800Camera *camera = s->camera;
    if (camera && camera->reply_position < camera->reply_bytes) {
        uint8_t value = camera->reply[camera->reply_position++];
        if (camera->reply_event && camera->reply_position == 4) {
            camera->event_head = (camera->event_head + 1) % 16;
            camera->event_count--;
            camera->reply_event = false;
        }
        return value;
    }
    return 0xff;
}

static uint64_t w800_camera_receiver_read(void *opaque, hwaddr offset, unsigned size)
{
    W800Camera *camera = opaque;
    return camera->receiver[offset / 4];
}

static void w800_camera_receiver_write(void *opaque, hwaddr offset,
                                       uint64_t value, unsigned size)
{
    W800Camera *camera = opaque;
    /* IRQ status is read-only. Acknowledgement clears actual pending
     * bits and cannot create a capture completion. */
    if (offset == 0x28) { camera->receiver[0x20 / 4] &= ~value; }
    else if (offset != 0x20) { camera->receiver[offset / 4] = value; }
    w800_irq_update(camera->machine);
    w800_camera_transport(camera->machine);
    qemu_log_mask(LOG_UNIMP, "W800_CAMIF_WRITE offset=%02"HWADDR_PRIx" value=%08"PRIx64" pc=%08x\n",
                  offset, value, camera->machine->cpu->env.regs[15]);
}

static uint64_t w800_camera_control_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((W800Camera *)opaque)->control;
}

static void w800_camera_control_write(void *opaque, hwaddr offset,
                                      uint64_t value, unsigned size)
{
    W800Camera *camera = opaque;
    bool was_released = camera->control & 0x100;
    if (was_released && !(value & 0x100)) {
        memset(camera->registers, 0, sizeof(camera->registers));
        memset(camera->properties, 0, sizeof(camera->properties));
        camera->command_bytes = camera->reply_bytes = camera->reply_position = 0;
        camera->mode_requested = camera->mode_pending = camera->reply_event = false;
        camera->event_head = camera->event_count = 0;
        camera->host_sequence = camera->consumed_sequence = 0;
        timer_del(camera->frame_timer);
        if (camera->machine->host_camera) { w800_host_camera_stop(); }
    }
    camera->control = value;
    if (!was_released && (value & 0x100)) { w800_camera_host_pause(camera->machine); }
    qemu_log_mask(LOG_UNIMP, "W800_CAMERA_CONTROL value=%08"PRIx64" pc=%08x\n",
                  value, camera->machine->cpu->env.regs[15]);
}

static const MemoryRegionOps w800_camera_receiver_ops = {
    .read = w800_camera_receiver_read, .write = w800_camera_receiver_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
static const MemoryRegionOps w800_camera_control_ops = {
    .read = w800_camera_control_read, .write = w800_camera_control_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void w800_camera_init(W800State *s)
{
    W800Camera *camera = g_new0(W800Camera, 1);
    s->camera = camera;
    camera->machine = s;
    camera->host_frame = g_malloc(W800_HOST_CAMERA_CAPACITY);
    camera->frame = g_malloc(640 * 480 * 2);
    camera->frame_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, w800_camera_poll, camera);
    memory_region_init_io(&camera->receiver_port, NULL,
                         &w800_camera_receiver_ops, camera, "w800.camif", 0x30);
    memory_region_add_subregion_overlap(get_system_memory(), 0xf7000200,
                                       &camera->receiver_port, 2);
    memory_region_init_io(&camera->control_port, NULL,
                         &w800_camera_control_ops, camera, "w800.camera-control", 4);
    memory_region_add_subregion_overlap(get_system_memory(), 0xf7000300,
                                       &camera->control_port, 2);
    w800_camera_reset(s);
}
