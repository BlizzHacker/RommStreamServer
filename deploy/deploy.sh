#!/bin/bash
# Run ON CT104. Installs deps, libretro cores, nginx site, systemd unit.
set -e
cd /opt/romm-stream

echo "== apt deps =="
apt-get update -qq
apt-get install -y -qq retroarch xdotool pulseaudio xvfb nginx \
    python3-venv python3-dev libffi-dev libssl-dev libopus-dev libvpx-dev \
    libsrtp2-dev pkg-config unzip curl

echo "== python venv =="
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q aiohttp aiortc av websocket-client

echo "== libretro cores =="
mkdir -p cores
BB=https://buildbot.libretro.com/nightly/linux/x86_64/latest
for c in dolphin flycast mupen64plus_next ppsspp mednafen_saturn \
         mednafen_psx_hw fbneo pcsx2 citra; do
    if [ ! -f "cores/${c}_libretro.so" ]; then
        echo "  fetching $c"
        curl -fsSL "$BB/${c}_libretro.so.zip" -o /tmp/core.zip \
            && unzip -oq /tmp/core.zip -d cores || echo "  ! $c unavailable"
    fi
done

echo "== pulseaudio (system-wide for headless capture) =="
pulseaudio --check 2>/dev/null || pulseaudio -D --exit-idle-time=-1 \
    --system=false 2>/dev/null || true

echo "== nginx =="
cp deploy/nginx-xbox.conf /etc/nginx/sites-available/romm-xbox
ln -sf /etc/nginx/sites-available/romm-xbox /etc/nginx/sites-enabled/romm-xbox
nginx -t && systemctl reload nginx

echo "== systemd =="
cp deploy/romm-stream.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now romm-stream
sleep 2
systemctl --no-pager -l status romm-stream | head -6

echo "== smoke =="
curl -sf 'http://localhost:8090/api/play/route?platform=snes'; echo
curl -sf 'http://localhost:8092/api/play/route?platform=wii'; echo
curl -sfo /dev/null 'http://localhost:8092/' && echo 'xbox app: OK'
echo "deploy complete"
