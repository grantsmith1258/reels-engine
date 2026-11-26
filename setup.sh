#!/usr/bin/env bash
# One-time setup in a fresh workspace (about 1-2 minutes). Safe to re-run.
#   bash setup.sh          -> map data, fonts, Python packages
#   bash setup.sh --kokoro -> also the free Kokoro voice model (only needed without a vidIQ voiceover)
set -euo pipefail
cd "$(dirname "$0")"
NE=https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson

python3 -m pip install --break-system-packages -q pycairo soundfile numpy pillow rasterio opencv-python-headless 2>&1 | grep -v -i warning || true

python3 -c "from PIL import Image" 2>/dev/null || python3 -m pip install --break-system-packages --ignore-installed -q pillow 2>&1 | grep -v -i warning || true

mkdir -p data fonts ~/.fonts
for f in ne_50m_admin_0_countries ne_10m_admin_0_countries ne_10m_admin_1_states_provinces ne_10m_lakes; do
  [ -s data/$f.geojson ] || curl -sSfL -o data/$f.geojson $NE/$f.geojson
done
[ -s fonts/Anton-Regular.ttf ] || curl -sSfL -o fonts/Anton-Regular.ttf https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf
fc-list | grep -qi "Noto Color Emoji" || [ -s fonts/NotoColorEmoji.ttf ] || curl -sSfL -o fonts/NotoColorEmoji.ttf https://raw.githubusercontent.com/googlefonts/noto-emoji/v2.047/fonts/NotoColorEmoji.ttf   # CBDT build: PIL cannot draw the newer COLRv1 font
cp fonts/Anton-Regular.ttf ~/.fonts/ && fc-cache -f >/dev/null
fc-list : family | grep -i "inter" >/dev/null || echo "WARNING: Inter font missing; captions will fall back to a default sans font"

if [ "${1:-}" = "--kokoro" ]; then
  pip install --break-system-packages -q kokoro-onnx 2>&1 | grep -v -i warning || true
  mkdir -p models
  R=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
  [ -s models/kokoro-v1.0.int8.onnx ] || curl -sSfL -o models/kokoro-v1.0.int8.onnx $R/kokoro-v1.0.int8.onnx
  [ -s models/voices-v1.0.bin ] || curl -sSfL -o models/voices-v1.0.bin $R/voices-v1.0.bin
fi
python3 -c "import cairo, soundfile, numpy, rasterio, cv2; print('setup ok')"
