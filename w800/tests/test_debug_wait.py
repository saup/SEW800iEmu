"""Run replies can outlast socket timeouts without losing partial RSP data."""
import socket
import threading
import time
import unittest

from w800.tools.debug import Debugger


class DebugWaitTests(unittest.TestCase):
    def setUp(self):
        self.client, self.server = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.close)
        self.client.settimeout(.01)
        self.debugger = Debugger.__new__(Debugger)
        self.debugger.sock = self.client

    def test_delayed_fragmented_reply_keeps_one_run_request(self):
        observed = []
        def peer():
            observed.append(self.server.recv(64))
            self.server.sendall(b'+')
            time.sleep(.04)
            self.server.sendall(b'$O')
            time.sleep(.04)
            self.server.sendall(b'K#9a')
            observed.append(self.server.recv(64))
        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        self.debugger.send_packet('c')
        observations = []
        self.assertEqual(self.debugger.receive_packet(timeout=1,
            on_wait=lambda: observations.append(time.monotonic()), poll_interval=.01), 'OK')
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed, [b'$c#63', b'+'])
        self.assertGreater(len(observations), 1)

    def test_cancel_is_observed_while_firmware_is_running(self):
        cancelled = threading.Event()
        timer = threading.Timer(.03, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            self.debugger.receive_packet(timeout=2, cancelled=cancelled.is_set)
        self.assertLess(time.monotonic() - started, .5)

    def test_deadline_remains_bounded_without_a_reply(self):
        with self.assertRaisesRegex(TimeoutError, 'debugger checkpoint'):
            self.debugger.receive_packet(timeout=.03)
