"""Create the hosted Internet profile using the firmware's own account services."""
import struct


def provision(session):
    s = session
    for address, signature in ((0x44f3ddf4, "ffb582b0"),
                               (0x44f3e5fc, "11b50c1c"),
                               (0x44efb260, "f1b50c1c")):
        if s.d.read(address, 4) != bytes.fromhex(signature):
            raise RuntimeError("Unsupported native Internet profile service")
    def ram(pointer):
        if not 0x4c000000 <= pointer < 0x4c7ffff0:
            raise RuntimeError('Native Internet profile service returned an invalid object')
        return pointer
    def release(pointer):
        method = s.word(s.word(pointer) + 12)
        if not method & 1 or not 0x44000000 <= method < 0x46000000:
            raise RuntimeError('Unsupported native profile release method')
        s.call_on_mmi(method & ~1, pointer)

    name = s.files.stage(12000, 'Host Internet\0'.encode('utf-16le'))
    apn = s.files.stage(12200, 'host\0'.encode('utf-16le'))
    # The account constructor takes a native write-transaction token as
    # its fourth argument. Its returned wrapper alone does not imply success.
    if not s.word(0x4c0b8d80):
        s.call_on_mmi(0x44d5c814, 0x441eeb20, 0x441ef330, 0x4c0b8d80)
    accounts = ram(s.word(0x4c0b8d80))
    lock_slot = s.files.stage(12300, bytes(4))
    acquire = s.word(s.word(accounts) + 0x34) & ~1
    result = s.call_on_mmi(acquire, accounts, 2, lock_slot)
    if result != 1:
        raise RuntimeError('Native data account write transaction unavailable')
    token = s.word(lock_slot)
    account = None
    try:
        account = ram(s.call_on_mmi(0x44f3ddf4, 2, 0, 0, token))
        account_id = s.word(account)
        interface = ram(s.word(account + 16))
        if not account_id or s.d.read(account + 4, 1) != b'\x02':
            raise RuntimeError('Native GPRS data account creation failed')
        if s.call_on_mmi(0x44f3e0f4, account, name):
            raise RuntimeError('Native data account name save failed')
        if s.call_on_mmi(0x44f3e5fc, account, apn):
            raise RuntimeError('Native data account APN save failed')
        readback = s.files.stage(12800, bytes(104))
        getter = s.word(s.word(interface) + 0x1c) & ~1
        if s.call_on_mmi(getter, interface, readback):
            raise RuntimeError('Native data account name readback failed')
        expected = 'Host Internet\0'.encode('utf-16le')
        if s.d.read(readback, len(expected)) != expected:
            raise RuntimeError('Native data account name did not persist')
    finally:
        if account is not None:
            slot = s.files.stage(12400, struct.pack('<I', account))
            s.call_on_mmi(0x44f3ddc8, slot)
        commit = s.word(s.word(accounts) + 0x38) & ~1
        if s.call_on_mmi(commit, accounts, token) != 1:
            raise RuntimeError('Native data account transaction did not commit')
    factory = creator = None
    try:
        root = ram(s.call_on_mmi(0x44c90070))
        slot = s.files.stage(12400, bytes(8))
        result = s.call_on_mmi(0x44766ce0, root, 0x441eec20, 0x441ef5b0, slot)
        if result: raise RuntimeError('Native Internet profile factory failed')
        factory = ram(s.word(slot))
        result = s.call_on_mmi(0x44ced8a4, factory, root, slot + 4)
        if result: raise RuntimeError('Native Internet profile creator failed')
        creator = ram(s.word(slot + 4))
        data = bytearray(300)
        struct.pack_into('<I', data, 0, account_id)
        label = 'Host Internet\0'.encode('utf-16le')
        data[6:6+len(label)] = label
        pointer = s.files.stage(12500, data)
        result = s.call_on_mmi(0x44efb260, creator, pointer)
        if result: raise RuntimeError('Native Internet profile save failed')
        s.provenance['internet_profile'] = {
            'name': 'Host Internet', 'account_id': account_id,
            'provider': 'original data account and Internet profile services',
            'apn': 'host', 'name_readback_verified': True, 'cellular_bearer': False,
        }
    finally:
        if creator is not None: release(creator)
        if factory is not None: release(factory)
