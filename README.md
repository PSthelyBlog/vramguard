# vramguard

Keep programs away from defective video memory on NVIDIA GPUs under Linux.

When part of a graphics card's memory fails, games freeze or fill with colored
specks once they reach the bad area, usually with `Xid 109`, `31` or `13`
errors in the kernel log. GeForce cards have no ECC or page retirement, so the
driver cannot avoid the bad memory on its own. `vramguard` finds it and keeps
it reserved, so nothing else can use it.

It is a workaround for a failing card, not a repair. If your card is under
warranty, use that first. Failing memory can get worse.

## How it works

1. It allocates nearly all free video memory in 32 MiB blocks through CUDA
   (`libcuda.so.1`, shipped with the driver).
2. It writes random data and its inverse to every block, twice, and reads it
   back.
3. It frees the good blocks and keeps the bad ones, plus 2 blocks on each side,
   allocated for as long as it runs.

Run at boot, it finishes before the desktop starts, so games, the desktop and
the driver's own data only get healthy memory. Each boot scans again, so it
adapts if the bad area changes.

## Results so far

Tested on **one card only**: an MSI GeForce RTX 4070 with a real VRAM defect,
on Ubuntu 26.04, GNOME on Wayland, kernel 7.0, driver 595.112.07. Other cards
and drivers are untested.

```
GPU 0: NVIDIA GeForce RTX 4070, 11.84 GiB total, 11.54 GiB free
Testing 11.28 GiB in 361 blocks of 32 MiB, 2 passes:
  pass 1/2, random data: 55 bad blocks
  pass 1/2, inverted data: 55 bad blocks
  pass 2/2, random data: 55 bad blocks
  pass 2/2, inverted data: 55 bad blocks
Done in 11 s.
Bad: blocks 155-209, 4.84 GiB to 6.56 GiB into the tested memory
```

| Check | Without vramguard | With vramguard |
|---|---|---|
| [memtest_vulkan](https://github.com/GpuZelenograd/memtest_vulkan) | errors in 443 of 443 passes, 10.9 GB tested | 0 errors in 3,542 passes, 9.0 GB tested |
| Cyberpunk 2077 with ray tracing, loading a save | froze every time | one 10-minute session with 2 loads, no driver errors |

## Requirements

- An NVIDIA GPU and the proprietary or open NVIDIA driver, which includes
  `libcuda.so.1`
- Python 3.9+
- For the panel icon: `python3-gi` and `gir1.2-ayatanaappindicator3-0.1`, plus
  an AppIndicator extension on GNOME (Ubuntu enables one by default)

## Try it first

No installation or root needed. Close games first: the test fills the free
video memory for about 15 seconds, and the desktop may stutter.

```sh
./vramguard.py scan     # test and report only; frees everything when done
./vramguard.py hold     # test, then hold the bad blocks until stopped
```

Then check that it works: while `hold` runs, run a VRAM tester such as
[memtest_vulkan](https://github.com/GpuZelenograd/memtest_vulkan) in another
terminal. It should find no errors in the memory that is left.

## Start it at boot

```sh
sudo install -m 755 vramguard.py /usr/local/bin/vramguard
sudo install -m 644 vramguard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vramguard.service    # takes effect at the next boot
systemctl status vramguard                 # shows how much it holds
```

Don't use `enable --now` while a `hold` you started by hand is running. The
second copy would find no bad memory left to hold.

## Panel icon

`vramguard_tray.py` adds a shield to the desktop panel:

| Icon | Meaning |
|---|---|
| Green check | on, holding the bad memory |
| Amber dots | testing, or turning off |
| Red cross | off; programs can reach the bad memory |
| Red exclamation mark | failed to start |

Its menu shows how much memory is held, turns the service on or off, and sets
whether it starts at boot. Those actions ask for your password.

```sh
sudo apt install gir1.2-ayatanaappindicator3-0.1
sudo install -m 755 vramguard_tray.py /usr/local/bin/vramguard-tray
sudo install -m 644 vramguard-tray.desktop /etc/xdg/autostart/
```

The icon then starts at every login. Run `vramguard-tray` to start it now.

Close games before turning vramguard on. Reboot for full protection if games
ran while it was off, since they may still occupy the bad memory.

## Options

Both `scan` and `hold` take these:

| Option | Default | Meaning |
|---|---|---|
| `--gpu N` | 0 | CUDA device index |
| `--block MIB` | 32 | test block size |
| `--passes N` | 2 | test passes |
| `--leave MIB` | 256 | video memory left free for other programs while testing |
| `--margin N` | 2 | extra blocks held on each side of a bad block |
| `--max-hold GIB` | 4 | hold nothing if more than this looks bad |

## Limits

- **It relies on the driver not moving allocated memory.** That held in
  testing, but NVIDIA does not document it. The memtest_vulkan check above
  tells you whether it holds on your system.
- **It can only find memory that is free** and that fails during the test. It
  can miss memory already in use, and faults that only appear sometimes or
  when hot. The margin is based on allocation order, which only tends to match
  the physical layout.
- **It costs some capacity:** everything it holds, plus a few hundred MiB for
  the CUDA session.
- **The open CUDA session can change the power state.** On the test card, games
  ran in P2 instead of P0, with memory at 10251 MHz instead of 10501 MHz. Idle
  power was not affected.
- **If it stops, the memory is released.** Reboot to protect it again.
- **Sleep and resume is untested.**
- **`--max-hold` stops it from reserving too much.** If more than 4 GiB looks
  bad, it holds nothing and exits with an error, since that points to a
  different problem.
- The boot service and the panel icon handle one GPU, device 0 by default.

## Tests

```sh
python3 -m unittest -v
```

The tests use a fake GPU, so they need neither an NVIDIA card nor root.

## License

[MIT](LICENSE). vramguard is an independent project, not affiliated with or
endorsed by NVIDIA.
