"""Original R1L002 application descriptors and component-manager startup."""
import struct

from .tools.trace_messages import cstring

COUNT = 52
IDS = {52, 56, 30, 27, 29, 55, 2, 3, 10, 18, 16, 13, 15, 19, 12, 32,
       6, 5, 26, 44, 45, 46, 47, 48, 49, 21, 22, 34, 23, 24, 9, 17, 50,
       8, 43, 31, 35, 36, 37, 38, 39, 41, 4, 7, 14, 11, 25, 40, 51, 53, 57, 58}


def descriptors(session):
    base = session.word(0x4c04efd0)
    if not 0x4c000000 <= base <= 0x4c800000 - (COUNT + 1) * 32:
        raise RuntimeError('Original component table is outside RAM')
    rows = []
    for index in range(COUNT):
        address = base + index * 32
        component, flags, _, handle, entry, name, modes, started = struct.unpack(
            '<8I', session.d.read(address, 32))
        if (not 0x4c000000 <= handle <= 0x4c7ffffc or
                not 0x44000000 <= name < 0x46000000):
            raise RuntimeError('Original component descriptor is invalid')
        rows.append({'id': component, 'name': cstring(session.d, name),
                     'flags': flags, 'mode_mask': modes >> 16,
                     'startup_data': modes & 0xffff, 'entry': hex(entry),
                     'handle_address': handle, 'started': bool(started),
                     'handle': hex(session.word(handle))})
    if {r['id'] for r in rows} != IDS or session.word(base + COUNT * 32) != 0:
        raise RuntimeError('Unsupported original component table')
    return rows


def start_remaining(session):
    """Start every remaining descriptor enabled for the original mode.

    Retain real errors. Creation does not establish service readiness, hardware
    availability, network connectivity or success of the later boot lifecycle.
    """
    attempts = []
    exclusions = []
    mode = session.provenance['original_manager_mode']
    rows = descriptors(session)
    # Handsfree acquires its Connection Manager interface during its first
    # scheduled initialization and does not retry a missing interface.
    # Publish CM's real task before allowing Handsfree to run.
    cm = next(row for row in rows if row['id'] == 45)
    rows.remove(cm)
    rows.insert(next(i for i, row in enumerate(rows) if row['id'] == 44), cm)
    for row in rows:
        if row['started']:
            continue
        # The original manager tests the halfword at descriptor+0x1a.
        # Customization is a separate mode 0x80 task, not a mode 1 service.
        if not row['mode_mask'] & mode:
            exclusions.append({'id': row['id'], 'name': row['name'],
                               'mode_mask': hex(row['mode_mask'])})
            continue
        session.progress('Starting original ' + row['name'] + '…')
        result = session.invoke(0x44f773f8, session.manager, row['id'], mode)
        attempts.append({'id': row['id'], 'name': row['name'], 'result': hex(result)})
        session.advance(100)
    session.provenance['additional_service_attempts'] = attempts
    session.provenance['service_mode_exclusions'] = exclusions
    session.provenance['components'] = descriptors(session)
    session.provenance['service_creation_errors'] = [r for r in attempts if r['result'] != '0x0']
