#!/usr/bin/env python3
"""Merge our input settings into RetroArch's own config.

`--appendconfig` was the obvious way to do this and it silently did nothing —
RetroArch logged no "Appending config" line and kept `joypad driver: "udev"`
however the appended file was written. Editing the main config is dull but
verifiable: read it back and the setting is either there or it is not.

Idempotent; run after deploying retroarch.cfg. Existing keys are replaced, and a
backup is kept the first time.

    python3 deploy/merge_retroarch_cfg.py [source.cfg] [target.cfg]
"""
import shutil
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else '/opt/romm-stream/retroarch.cfg')
DST = Path(sys.argv[2] if len(sys.argv) > 2
           else '/root/.config/retroarch/retroarch.cfg')


def parse(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        out[k.strip()] = v.strip()
    return out


def main():
    if not SRC.is_file():
        print(f'no source config at {SRC}', file=sys.stderr)
        return 1
    wanted = parse(SRC.read_text())
    if not wanted:
        print('source config has no settings', file=sys.stderr)
        return 1

    DST.parent.mkdir(parents=True, exist_ok=True)
    existing_text = DST.read_text() if DST.is_file() else ''
    if DST.is_file() and not DST.with_suffix('.cfg.orig').exists():
        shutil.copy2(DST, DST.with_suffix('.cfg.orig'))

    kept, replaced = [], 0
    for line in existing_text.splitlines():
        key = line.partition('=')[0].strip()
        if key in wanted:
            replaced += 1
            continue                      # our value wins
        kept.append(line)

    body = '\n'.join(kept).rstrip()
    added = '\n'.join(f'{k} = {v}' for k, v in sorted(wanted.items()))
    DST.write_text((body + '\n\n' if body else '')
                   + '# --- managed by RommStreamServer (merge_retroarch_cfg.py) ---\n'
                   + added + '\n')

    check = parse(DST.read_text())
    missing = [k for k, v in wanted.items() if check.get(k) != v]
    print(f'merged {len(wanted)} settings into {DST} '
          f'({replaced} replaced, {len(missing)} failed)')
    if missing:
        print('  did not take: ' + ', '.join(missing), file=sys.stderr)
        return 1
    print(f'  joypad driver now: {check.get("input_joypad_driver")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
