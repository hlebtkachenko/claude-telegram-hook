#!/usr/bin/env bash
# Regenerate the mock screenshots in docs/images from docs/demo/mock.html (macOS, Google Chrome).
# Message text and buttons come from the plugin's own builders (messages.py), run offline in an empty
# environment with a throwaway HOME, TMPDIR and fixture repo. Never uses the real bot, token or chat.
set -euo pipefail
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home" "$T/tmp" "$T/acme-web"
git -C "$T/acme-web" init -q -b main
git -C "$T/acme-web" remote add origin https://github.com/example/acme-web.git
git -C "$T/acme-web" -c user.name=Demo -c user.email=demo@example.com commit -q --allow-empty -m "chore: initial commit"
env -i PATH=/usr/bin:/bin HOME="$T/home" TMPDIR="$T/tmp/" python3 -B messages.py "$T/acme-web" > messages.js

# Headless Chrome may keep running after it has written its output: wait for the output, then stop it.
chrome() {  # output file, Chrome arguments...
  local out="$1"; shift
  rm -f "$out"
  "$CHROME" --headless=new --user-data-dir="$T/chrome" --hide-scrollbars "$@" > "$out.log" 2>/dev/null &
  local pid=$! i
  for i in $(seq 60); do
    if [[ "$out" == *.png ]] && [ -s "$out" ]; then sleep 1; break; fi
    if [[ "$out" != *.png ]] && grep -q "</html>" "$out.log"; then mv "$out.log" "$out"; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -f "$out.log"
  [ -s "$out" ] || { echo "render failed: $out" >&2; exit 1; }
}

shot() {  # view, CSS width, output
  local url="file://$PWD/mock.html#$1" h
  chrome "$T/dom.html" --window-size="$2,2000" --dump-dom "$url"
  h="$(sed -n 's/.*data-height="\([0-9]*\)".*/\1/p' "$T/dom.html" | head -1)"
  chrome "$PWD/../images/$3" --force-device-scale-factor=2 --window-size="$2,$h" --screenshot="$PWD/../images/$3" "$url"
}
shot question 520 question.png
shot permission 520 permission.png
shot plan 520 plan.png
shot stop 520 turn-question.png
shot answered 520 answered.png
shot hero 1000 hero.png
chrome "$PWD/../images/social-preview.png" --force-device-scale-factor=1 --window-size=1280,640 \
  --screenshot="$PWD/../images/social-preview.png" "file://$PWD/social-card.html"
