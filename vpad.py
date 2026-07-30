"""A uinput virtual gamepad per streaming session.

Why this exists: input used to be injected with xdotool key events, which are
digital. Anything with a 3D camera — GameCube, Wii, N64, Dreamcast — is close to
unplayable without real analog sticks, so the session presents a pad instead of a
keyboard.

Two container details make this fiddly, and both are handled here rather than in
the LXC config so the server does not need a restart to gain the feature:

  * uinput creates its event node in the *host's* devtmpfs. LXC 104 has its own
    /dev tmpfs, and a bind mount made from the host into /proc/<pid>/root does not
    propagate into the container's mount namespace. So after creating the device
    we find its event number in sysfs (which *is* shared) and mknod the node
    ourselves. Harmless if a real bind mount is present — the node already exists.
  * The device is named "Microsoft X-Box 360 pad" with matching vendor/product so
    RetroArch's own autoconfig profile applies without hand-written binds.
"""

import logging
import os
import stat
from pathlib import Path

log = logging.getLogger('vpad')

PAD_NAME = 'Microsoft X-Box 360 pad'
INPUT_MAJOR = 13
EVENT_MINOR_BASE = 64          # /dev/input/eventN is char 13:(64+N)
JS_MINOR_BASE = 0              # /dev/input/jsN   is char 13:N

# App button name → evdev key. Matches the Standard Gamepad layout the client
# sends, and xpad's own mapping so RetroArch's 360 profile lines up.
_BUTTONS = {
    'a': 'BTN_SOUTH', 'b': 'BTN_EAST', 'x': 'BTN_WEST', 'y': 'BTN_NORTH',
    'l1': 'BTN_TL', 'r1': 'BTN_TR',
    'select': 'BTN_SELECT', 'start': 'BTN_START',
    'l3': 'BTN_THUMBL', 'r3': 'BTN_THUMBR',
    'menu': 'BTN_MODE',
}
# Triggers and the d-pad are axes on a 360 pad, not buttons.
_HATS = {'left': ('ABS_HAT0X', -1), 'right': ('ABS_HAT0X', 1),
         'up': ('ABS_HAT0Y', -1), 'down': ('ABS_HAT0Y', 1)}
_TRIGGERS = {'l2': 'ABS_Z', 'r2': 'ABS_RZ'}

AXIS_MAX = 32767


class VirtualPad:
    """One pad. Not thread-safe; drive it from a single event loop."""

    def __init__(self):
        from evdev import UInput, AbsInfo, ecodes as e
        self._e = e
        self._nodes = []

        abs_axis = AbsInfo(0, -AXIS_MAX - 1, AXIS_MAX, 16, 128, 0)
        trigger = AbsInfo(0, 0, 255, 0, 0, 0)
        hat = AbsInfo(0, -1, 1, 0, 0, 0)
        caps = {
            e.EV_KEY: [getattr(e, n) for n in _BUTTONS.values()],
            e.EV_ABS: [
                (e.ABS_X, abs_axis), (e.ABS_Y, abs_axis),
                (e.ABS_RX, abs_axis), (e.ABS_RY, abs_axis),
                (e.ABS_Z, trigger), (e.ABS_RZ, trigger),
                (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat),
            ],
        }
        self._ui = UInput(caps, name=PAD_NAME, vendor=0x045e,
                          product=0x028e, version=0x110)
        self._ensure_node()

    # ---------------------------------------------------------------- node

    def _ensure_node(self):
        """Make our device nodes visible inside this container.

        Both interfaces are created. The jsN (joydev) node is the one that
        matters: RetroArch's `udev` joypad driver enumerates through libudev, and
        with no udevd running in the container that finds nothing at all — an
        empty enumeration, not an error. Its `linuxraw` driver reads /dev/input/js*
        directly, which works without any of that machinery.
        """
        self._nodes = []
        found = self._sysfs_nodes()
        if not found:
            log.warning('virtual pad created but no sysfs entry found; '
                        'RetroArch will not see it')
            return
        for name in found:
            if name.startswith('event'):
                minor = EVENT_MINOR_BASE + int(name[len('event'):])
            elif name.startswith('js'):
                minor = JS_MINOR_BASE + int(name[len('js'):])
            else:
                continue
            path = Path('/dev/input') / name
            if path.exists():
                self._nodes.append(path)
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                os.mknod(str(path), stat.S_IFCHR | 0o660,
                         os.makedev(INPUT_MAJOR, minor))
                self._nodes.append(path)
                log.info('created %s for the virtual pad', path)
            except OSError as ex:
                log.warning('could not create %s: %s', path, ex)

    def _sysfs_nodes(self):
        """The event*/js* node names belonging to our device, via sysfs.

        sysfs is shared with the host even though /dev is not, which is what makes
        this discoverable at all.
        """
        root = Path('/sys/class/input')
        if not root.is_dir():
            return []
        best_input, best_num = None, -1
        for entry in root.glob('input*'):
            try:
                name = (entry / 'name').read_text().strip()
            except OSError:
                continue
            if name != PAD_NAME:
                continue
            try:
                num = int(entry.name[len('input'):])
            except ValueError:
                continue
            # Sessions run concurrently; ours was created last, so it is the
            # highest-numbered input with this name.
            if num > best_num:
                best_input, best_num = entry, num
        if best_input is None:
            return []
        return sorted(child.name for child in best_input.iterdir()
                      if child.name.startswith(('event', 'js')))

    # --------------------------------------------------------------- input

    def press(self, button: str, pressed: bool) -> bool:
        e = self._e
        b = (button or '').lower()
        if b in _BUTTONS:
            self._ui.write(e.EV_KEY, getattr(e, _BUTTONS[b]), 1 if pressed else 0)
        elif b in _HATS:
            code, value = _HATS[b]
            self._ui.write(e.EV_ABS, getattr(e, code), value if pressed else 0)
        elif b in _TRIGGERS:
            self._ui.write(e.EV_ABS, getattr(e, _TRIGGERS[b]),
                           255 if pressed else 0)
        else:
            return False
        self._ui.syn()
        return True

    def axes(self, values) -> bool:
        """Left/right stick, as [lx, ly, rx, ry] in -1.0..1.0.

        The client already uses the Gamepad API's sign convention (up is
        negative), which is the same as evdev's, so no inversion here.
        """
        e = self._e
        codes = (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY)
        wrote = False
        for code, raw in zip(codes, values or []):
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            v = max(-1.0, min(1.0, v))
            self._ui.write(e.EV_ABS, code, int(round(v * AXIS_MAX)))
            wrote = True
        if wrote:
            self._ui.syn()
        return wrote

    def close(self):
        try:
            self._ui.close()
        except Exception:
            pass
        # The kernel removes the device; the nodes we made by hand are ours to tidy.
        for path in self._nodes:
            try:
                path.unlink()
            except OSError:
                pass
        self._nodes = []


def create():
    """A pad, or None if uinput is unavailable — input falls back to xdotool."""
    try:
        return VirtualPad()
    except Exception as ex:
        log.warning('no virtual pad (%s); falling back to key injection', ex)
        return None
