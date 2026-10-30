"""Tests for vramguard's bad-block search and hold plan.

These use a fake GPU, so they run without an NVIDIA card.
Run with:  python3 -m unittest -v
"""

import ctypes as C
import os
import socket
import tempfile
import unittest

import vramguard
from vramguard import CUDA_ERROR_OUT_OF_MEMORY, CudaError

BLOCK = 4096


class FakeCuda:
    """Video memory as bytearrays. Blocks whose allocation index is in `bad` read back one flipped bit."""

    def __init__(self, total_blocks, bad=()):
        self.total = total_blocks * BLOCK
        self.bad = set(bad)
        self.mem = {}
        self.order = []
        self.freed = []
        self._host = []

    def mem_info(self):
        return self.total - sum(len(b) for b in self.mem.values()), self.total

    def alloc(self, size):
        if self.mem_info()[0] < size:
            raise CudaError("cuMemAlloc_v2", CUDA_ERROR_OUT_OF_MEMORY, "CUDA_ERROR_OUT_OF_MEMORY")
        ptr = (len(self.order) + 1) << 32
        self.mem[ptr] = bytearray(size)
        self.order.append(ptr)
        return ptr

    def free(self, ptr):
        del self.mem[ptr]
        self.freed.append(ptr)

    def alloc_host(self, size):
        buf = C.create_string_buffer(size)
        self._host.append(buf)
        return C.addressof(buf)

    def free_host(self, ptr):
        pass

    def upload(self, dst, src, size):
        self.mem[dst][:] = C.string_at(src, size)

    def download(self, dst, src, size):
        data = bytearray(self.mem[src])
        if self.order.index(src) in self.bad:
            data[size // 2] ^= 0x04
        C.memmove(dst, bytes(data), size)

    def sync(self):
        pass


def quiet(_line):
    pass


def run_scan(cuda, margin=1, max_hold=4):
    return vramguard.scan(cuda, BLOCK, 2 * BLOCK, passes=1, margin=margin, max_hold=max_hold, out=quiet)


class ScanTest(unittest.TestCase):
    def test_keeps_bad_blocks_and_their_neighbours(self):
        cuda = FakeCuda(20, bad={5, 6, 12})
        blocks, keep = run_scan(cuda)
        self.assertEqual(len(blocks), 18)  # stops while 2 blocks are still free (--leave)
        self.assertEqual(keep, [4, 5, 6, 7, 11, 12, 13])

    def test_clean_memory_keeps_nothing(self):
        _, keep = run_scan(FakeCuda(20))
        self.assertEqual(keep, [])

    def test_refuses_to_hold_too_much(self):
        cuda = FakeCuda(20, bad=range(20))
        with self.assertRaisesRegex(ValueError, "nothing is held"):
            run_scan(cuda, max_hold=5 * BLOCK / vramguard.GIB)


class PlanTest(unittest.TestCase):
    def test_margin_is_clipped_at_the_ends(self):
        self.assertEqual(vramguard.plan_hold([0, 9], 10, 2), [0, 1, 2, 7, 8, 9])

    def test_runs(self):
        self.assertEqual(vramguard.runs([1, 2, 3, 7, 9, 10]), [(1, 3), (7, 7), (9, 10)])

    def test_pattern_offsets_stay_inside_the_pattern_buffer(self):
        block = 32 * vramguard.MIB
        for i in range(2000):
            off = vramguard.pattern_offset(i, block)
            self.assertTrue(0 <= off < block and off % 4096 == 0)


class NotifyTest(unittest.TestCase):
    def test_sends_to_systemd_socket(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(path)
                os.environ["NOTIFY_SOCKET"] = path
                try:
                    vramguard.notify("READY=1")
                finally:
                    del os.environ["NOTIFY_SOCKET"]
                self.assertEqual(server.recv(64), b"READY=1")


if __name__ == "__main__":
    unittest.main()
