#!/usr/bin/env python3
"""vramguard - keep programs away from defective video memory.

Fills the GPU's free video memory with test blocks through CUDA (libcuda,
shipped with the NVIDIA driver), checks each block with test patterns, then
keeps the blocks that read back wrong data allocated for as long as it runs.
The driver does not give allocated memory to anyone else, and in testing it
never moved it, so games, the desktop and the driver's own data structures
only get the healthy memory.

Run `vramguard hold` at boot, before the desktop starts (see
vramguard.service). It does not repair the card; it keeps a failing one
usable. No root needed.
"""

import argparse
import ctypes as C
import os
import signal
import socket
import sys
import time

MIB = 1 << 20
GIB = 1 << 30

# Constants from cuda.h
CUDA_ERROR_OUT_OF_MEMORY = 2


class CudaError(Exception):
    def __init__(self, func, code, name):
        super().__init__(f"{func}: {name}")
        self.code = code


class Cuda:
    """Minimal ctypes binding for the CUDA driver API calls vramguard uses."""

    def __init__(self, device=0, libname="libcuda.so.1"):
        try:
            self._lib = C.CDLL(libname)
        except OSError as e:
            raise CudaError("dlopen", -1, f"cannot load {libname}; is the NVIDIA driver installed? ({e})") from e
        lib = self._lib
        lib.cuMemAlloc_v2.argtypes = [C.POINTER(C.c_uint64), C.c_size_t]
        lib.cuMemFree_v2.argtypes = [C.c_uint64]
        lib.cuMemAllocHost_v2.argtypes = [C.POINTER(C.c_void_p), C.c_size_t]
        lib.cuMemFreeHost.argtypes = [C.c_void_p]
        lib.cuMemcpyHtoD_v2.argtypes = [C.c_uint64, C.c_void_p, C.c_size_t]
        lib.cuMemcpyDtoH_v2.argtypes = [C.c_void_p, C.c_uint64, C.c_size_t]
        self.call("cuInit", 0)
        self._dev = C.c_int()
        self.call("cuDeviceGet", C.byref(self._dev), device)
        ctx = C.c_void_p()
        self.call("cuDevicePrimaryCtxRetain", C.byref(ctx), self._dev)
        self.call("cuCtxSetCurrent", ctx)

    def call(self, func, *args):
        code = getattr(self._lib, func)(*args)
        if code != 0:
            name = C.c_char_p()
            self._lib.cuGetErrorName(code, C.byref(name))
            raise CudaError(func, code, name.value.decode() if name.value else f"error {code}")

    def name(self):
        buf = C.create_string_buffer(256)
        self.call("cuDeviceGetName", buf, len(buf), self._dev)
        return buf.value.decode()

    def mem_info(self):
        """Return (free, total) video memory in bytes."""
        free, total = C.c_size_t(), C.c_size_t()
        self.call("cuMemGetInfo_v2", C.byref(free), C.byref(total))
        return free.value, total.value

    def alloc(self, size):
        ptr = C.c_uint64()
        self.call("cuMemAlloc_v2", C.byref(ptr), size)
        return ptr.value

    def free(self, ptr):
        self.call("cuMemFree_v2", ptr)

    def alloc_host(self, size):
        """Page-locked host memory, which copies to and from the GPU at full speed."""
        ptr = C.c_void_p()
        self.call("cuMemAllocHost_v2", C.byref(ptr), size)
        return ptr.value

    def free_host(self, ptr):
        self.call("cuMemFreeHost", ptr)

    def upload(self, dst, src, size):
        self.call("cuMemcpyHtoD_v2", dst, src, size)

    def download(self, dst, src, size):
        self.call("cuMemcpyDtoH_v2", dst, src, size)

    def sync(self):
        self.call("cuCtxSynchronize")


def gib(n):
    return f"{n / GIB:.2f} GiB"


def allocate_blocks(cuda, block, leave):
    """Allocate block-sized buffers until only about `leave` bytes stay free."""
    blocks = []
    while cuda.mem_info()[0] >= block + leave:
        try:
            blocks.append(cuda.alloc(block))
        except CudaError as e:
            if e.code != CUDA_ERROR_OUT_OF_MEMORY:
                raise
            break
    return blocks


def pattern_offset(index, block):
    """Where block `index` reads from the double-length pattern buffer.

    Shifting the pattern per block gives every block different data without
    generating a new pattern for each one.
    """
    return (index * 7919 * 4096) % block


def find_bad_blocks(cuda, blocks, block, passes, out=print):
    """Return the sorted indices of blocks that read back wrong data in any pass.

    Each pass writes every block before reading any back, with random data and
    then its inverse, so every bit is tested as both 0 and 1.
    """
    libc = C.CDLL(None)
    libc.memcmp.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
    pattern = cuda.alloc_host(2 * block)
    readback = cuda.alloc_host(block)
    invert = bytes(range(255, -1, -1))
    bad = set()
    try:
        for p in range(passes):
            data = os.urandom(2 * block)
            for label, fill in (("random", data), ("inverted", data.translate(invert))):
                C.memmove(pattern, fill, len(fill))
                for i, ptr in enumerate(blocks):
                    cuda.upload(ptr, pattern + pattern_offset(i, block), block)
                cuda.sync()
                found = 0
                for i, ptr in enumerate(blocks):
                    cuda.download(readback, ptr, block)
                    if libc.memcmp(readback, pattern + pattern_offset(i, block), block) != 0:
                        found += 1
                        bad.add(i)
                out(f"  pass {p + 1}/{passes}, {label} data: {found} bad blocks")
    finally:
        cuda.free_host(pattern)
        cuda.free_host(readback)
    return sorted(bad)


def plan_hold(bad, count, margin):
    """Indices of the blocks to keep: every bad block plus `margin` neighbours on each side.

    Neighbours are by allocation order, which tends to follow the physical
    layout of video memory, so the margin covers weak cells next to failing ones.
    """
    keep = set()
    for i in bad:
        keep.update(range(max(0, i - margin), min(count, i + margin + 1)))
    return sorted(keep)


def runs(indices):
    """Group sorted indices into (first, last) runs of consecutive numbers."""
    groups = []
    for i in indices:
        if groups and i == groups[-1][1] + 1:
            groups[-1][1] = i
        else:
            groups.append([i, i])
    return [tuple(g) for g in groups]


def scan(cuda, block, leave, passes, margin, max_hold, out=print):
    """Test the free video memory; return (blocks, keep).

    `blocks` are all the allocated block pointers and `keep` the indices of the
    ones to hold. Every block stays allocated until the caller frees it.
    """
    blocks = allocate_blocks(cuda, block, leave)
    out(f"Testing {gib(len(blocks) * block)} in {len(blocks)} blocks of {block // MIB} MiB, {passes} passes:")
    start = time.monotonic()
    bad = find_bad_blocks(cuda, blocks, block, passes, out)
    out(f"Done in {time.monotonic() - start:.0f} s.")
    if not bad:
        out("No bad blocks found.")
    for first, last in runs(bad):
        out(f"Bad: blocks {first}-{last}, {gib(first * block)} to {gib((last + 1) * block)} into the tested memory")

    keep = plan_hold(bad, len(blocks), margin)
    if len(keep) * block > max_hold * GIB:
        raise ValueError(f"{gib(len(keep) * block)} would be held, more than the {max_hold} GiB limit (--max-hold). "
                         "That much failing memory points to another problem, so nothing is held.")
    return blocks, keep


def notify(message):
    """Send a state update to systemd; does nothing outside a Type=notify service."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    if path.startswith("@"):
        path = "\0" + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(path)
        s.sendall(message.encode())


def open_cuda(args):
    cuda = Cuda(args.gpu)
    free, total = cuda.mem_info()
    print(f"GPU {args.gpu}: {cuda.name()}, {gib(total)} total, {gib(free)} free")
    return cuda


def scan_args(args, cuda):
    return scan(cuda, args.block * MIB, args.leave * MIB, args.passes, args.margin, args.max_hold)


def cmd_scan(args):
    cuda = open_cuda(args)
    _, keep = scan_args(args, cuda)
    if keep:
        print(f"'vramguard hold' would keep {gib(len(keep) * args.block * MIB)} "
              f"({len(keep)} blocks, including a margin of {args.margin} on each side).")
    return 0


def cmd_hold(args):
    cuda = open_cuda(args)
    blocks, keep = scan_args(args, cuda)
    held = set(keep)
    for i, ptr in enumerate(blocks):
        if i not in held:
            cuda.free(ptr)
    if not keep:
        notify("READY=1\nSTATUS=No bad video memory found; holding nothing")
        return 0
    status = f"Holding {gib(len(keep) * args.block * MIB)} of bad video memory ({len(keep)} blocks)"
    print(f"{status}. It stays reserved until this program stops.")
    notify(f"READY=1\nSTATUS={status}")
    while True:
        signal.pause()


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--gpu", type=int, default=0, metavar="N", help="CUDA device index (default: 0)")
    common.add_argument("--block", type=int, default=32, metavar="MIB", help="test block size (default: 32)")
    common.add_argument("--passes", type=int, default=2, metavar="N", help="test passes (default: 2)")
    common.add_argument("--leave", type=int, default=256, metavar="MIB",
                        help="video memory left free for other programs while testing (default: 256)")
    common.add_argument("--margin", type=int, default=2, metavar="N",
                        help="extra blocks held on each side of a bad block (default: 2)")
    common.add_argument("--max-hold", type=float, default=4, metavar="GIB",
                        help="hold nothing if more than this looks bad (default: 4)")

    parser = argparse.ArgumentParser(
        prog="vramguard",
        description="Find defective video memory and keep it allocated so nothing else uses it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", parents=[common], help="test the free video memory and report bad blocks")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("hold", parents=[common], help="test, then keep the bad blocks allocated until stopped")
    p.set_defaults(func=cmd_hold)
    return parser


def main(argv=None):
    sys.stdout.reconfigure(line_buffering=True)  # show progress live under systemd too
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 0
    except (CudaError, ValueError, OSError) as e:
        sys.exit(f"vramguard: {e}")


if __name__ == "__main__":
    sys.exit(main())
