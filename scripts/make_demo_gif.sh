#!/bin/bash
# Turn a screen recording into the README demo GIF (docs/demo.gif).
#
#   scripts/make_demo_gif.sh recording.mp4            # any video ffmpeg can read
#   scripts/make_demo_gif.sh recording.mov 10 720     # start at 0s, 10 fps, 720 px wide
#
# Keep the clip short (10-20 s) so the GIF stays under ~5 MB and loads fast on GitHub.
# The rendered demo from scripts/demo_video (out/codelens-demo.mp4) works too.
# Needs ffmpeg (brew install ffmpeg / apt install ffmpeg).
set -euo pipefail

IN="${1:?usage: $0 <video> [fps=12] [width=800] [out=docs/demo.gif]}"
FPS="${2:-12}"
WIDTH="${3:-800}"
OUT="${4:-$(dirname "$0")/../docs/demo.gif}"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found"; exit 1; }
mkdir -p "$(dirname "$OUT")"
PALETTE="$(mktemp -t codelens-palette-XXXX).png"
trap 'rm -f "$PALETTE"' EXIT

FILTERS="fps=$FPS,scale=$WIDTH:-1:flags=lanczos"
# Two passes: build a palette from the whole clip, then dither with it (much smaller, sharper GIF)
ffmpeg -v error -y -i "$IN" -vf "$FILTERS,palettegen=stats_mode=diff" "$PALETTE"
ffmpeg -v error -y -i "$IN" -i "$PALETTE" \
    -lavfi "$FILTERS [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
    -loop 0 "$OUT"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
