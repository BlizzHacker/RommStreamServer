# Analog sticks on the stream tier

**Status: client side complete, server side blocked on a broken RetroArch install.**

Streamed sessions are digital-only today. Buttons and d-pad work; the sticks do
not. That makes anything with a 3D camera — GameCube, Wii, N64, Dreamcast —
awkward at best.

## What is already done

* **Client** (`RommForXbox`): `GP.onAxes` reports quantised stick values on change
  only, `RTC.sendAxes` ships them over the existing data channel, and the stream
  view turns reporting on and off with the view. Nothing more is needed there.
* **Transport**: the data channel carries `{"axes":[lx,ly,rx,ry]}` alongside
  `{"key":…,"pressed":…}`. Both directions are wired and the server accepts both.
* **Virtual pad** (`vpad.py`): creates a uinput device per session named
  "Microsoft X-Box 360 pad", and — because the container's `/dev` is a private
  tmpfs that uinput's node never appears in — discovers its own `eventN`/`jsN`
  numbers from sysfs (which *is* shared) and `mknod`s them itself. **Verified
  working**: `/dev/input/event6` and `/dev/input/js0` both appear, and writing
  axis and button events succeeds.

So the pad exists, is fed correctly, and is visible in the container. The gap is
one step further on.

## Why it does not work yet

**RetroArch never opens the pad**, confirmed directly rather than inferred —
`/proc/<pid>/fd` contains no input device while a session runs.

The chain of reasons, each one verified:

1. RetroArch picks its **`udev`** joypad driver. That driver enumerates through
   libudev, which needs a udev database. There is no `udevd` in the container, so
   enumeration returns *nothing* — not an error, just an empty list.
2. Running `udevd` in the container does not fix it: it starts, but
   `udevadm trigger` cannot write to sysfs (read-only in an LXC guest), so the
   database stays empty.
3. The obvious fix is `input_joypad_driver = "linuxraw"`, which reads
   `/dev/input/js*` directly and needs none of that machinery. `linuxraw` **is**
   compiled into this binary (confirmed with `strings`). But it can only be set in
   a config file, and:
4. **This RetroArch aborts whenever it loads a config file, whatever the
   contents.** Measured:

   | invocation | result |
   |---|---|
   | no config at all | runs (killed by `timeout`, exit 124) |
   | `-c` with a single `video_fullscreen` line | **SIGABRT, exit 134** |
   | `~/.config/retroarch/retroarch.cfg` via `HOME=/root` | **SIGABRT, exit 134** |
   | `--appendconfig` | silently ignored — no config context without `HOME` |

   This is why the unit deliberately sets **no `HOME`**: RetroArch then cannot find
   or create a config, and that is the only state in which it runs at all. The
   missing `HOME` is load-bearing, not an oversight — `runner_retroarch.py` says so
   in a comment, so nobody "fixes" it and breaks streaming.

`/etc/retroarch.cfg` is not a way around it either: RetroArch uses it only as a
skeleton to *create* a user config, not as a runtime config.

## The actual fix

Repair or replace the RetroArch install on LXC 104 so it can load a config
without aborting. Options, roughly in order of preference:

1. **Find the abort.** Run it under `gdb` or with `RETROARCH_LOG_LEVEL=0` and get a
   backtrace; a missing asset or filter directory referenced by the skeleton config
   is the likely culprit, and would be a small fix.
2. **Replace the binary** with an upstream static build or AppImage, which does not
   depend on the Debian package's asset layout.
3. **Reinstall the assets** (`retroarch-assets`, `libretro-*` data packages) that
   the packaged config expects.

Once a config loads, set `input_joypad_driver = "linuxraw"` plus the binds in
[`../retroarch.cfg`](../retroarch.cfg) — the indices there already match how
`vpad.py` declares the device, and `deploy/merge_retroarch_cfg.py` can merge them
into the user config. Then re-run:

```bash
python3 tests/verify_stream.py ngc '<some GameCube rom>.rvz'
```

`emulator holds an input device` should turn from `INFO` to `PASS`.

## Guard against the false positive that hid this

A core with no firmware, and an emulator that is not running at all, both stream a
still image at a perfectly healthy 30 fps. This suite passed **every** check with
no RetroArch process alive, which is how "the stream works" got mistaken for "the
game works". `verify_stream.py` now asserts the emulator process exists, and
`tiers.py` gates routing on firmware presence. Never treat frame rate as evidence
that anything is being emulated.
