#!/usr/bin/env bash
set -Eeuo pipefail

export DISPLAY="${DISPLAY:-:99}"
export PVP_DATA_DIR="${PVP_DATA_DIR:-/app/data}"

runtime_dir="${PVP_RUNTIME_DIR:-/tmp/pvp-runtime}"
desktop_size="${PVP_DESKTOP_SIZE:-1365x900x24}"
novnc_web="${PVP_NOVNC_WEB:-/usr/share/novnc}"
background_pids=()

mkdir -p "$runtime_dir" /tmp/.X11-unix "$PVP_DATA_DIR"
chmod 1777 /tmp/.X11-unix 2>/dev/null || true

Xvfb "$DISPLAY" \
    -screen 0 "$desktop_size" \
    -ac \
    -nolisten tcp \
    +extension RANDR \
    >"$runtime_dir/xvfb.log" 2>&1 &
background_pids+=("$!")
xvfb_pid="$!"

for _ in $(seq 1 50); do
    if [[ -S "/tmp/.X11-unix/X${DISPLAY#:}" ]]; then
        break
    fi
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then
        cat "$runtime_dir/xvfb.log"
        exit 1
    fi
    sleep 0.2
done

if [[ ! -S "/tmp/.X11-unix/X${DISPLAY#:}" ]]; then
    cat "$runtime_dir/xvfb.log"
    echo "Xvfb did not become ready on $DISPLAY" >&2
    exit 1
fi

xset s off >/dev/null 2>&1 || true
xset -dpms >/dev/null 2>&1 || true

openbox --sm-disable >"$runtime_dir/openbox.log" 2>&1 &
background_pids+=("$!")

vnc_args=(
    -display "$DISPLAY"
    -rfbport 5900
    -localhost
    -forever
    -shared
    -noxdamage
    -repeat
    -desktop "PVP Login"
)
if [[ -n "${PVP_VNC_PASSWORD:-}" ]]; then
    umask 077
    x11vnc -storepasswd "$PVP_VNC_PASSWORD" "$runtime_dir/vnc.pass" \
        >"$runtime_dir/vnc-passwd.log" 2>&1
    vnc_args+=(-rfbauth "$runtime_dir/vnc.pass")
else
    vnc_args+=(-nopw)
fi
x11vnc "${vnc_args[@]}" >"$runtime_dir/x11vnc.log" 2>&1 &
background_pids+=("$!")

websockify \
    --web="$novnc_web" \
    0.0.0.0:6080 \
    127.0.0.1:5900 \
    >"$runtime_dir/novnc.log" 2>&1 &
background_pids+=("$!")

sleep 1
for pid in "${background_pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
        for log in \
            "$runtime_dir/xvfb.log" \
            "$runtime_dir/openbox.log" \
            "$runtime_dir/x11vnc.log" \
            "$runtime_dir/novnc.log"; do
            [[ -f "$log" ]] && cat "$log"
        done
        echo "Remote desktop failed to start." >&2
        exit 1
    fi
done

cleanup() {
    for pid in "${background_pids[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT

"$@" &
app_pid="$!"

shutdown() {
    kill -TERM "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

echo "Remote desktop ready on noVNC port 6080."
wait "$app_pid"
