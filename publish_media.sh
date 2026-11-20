#!/usr/bin/env bash
# Put a finished video on the repo's "media" branch so vidIQ can import it from a public link.
# The branch keeps only the last 7 days of videos (one commit, force-pushed) so the repo stays small.
# Usage: bash publish_media.sh path/to/video.mp4 YYYY-MM-DD-slug.mp4   (also works for a cover .png)
# Prints the public raw URL on the last line.
set -euo pipefail
SRC=$(realpath "$1"); NAME="$2"
cd "$(dirname "$0")"
REMOTE=$(git remote get-url origin)
SLUG=$(echo "$REMOTE" | sed -E 's#.*github.com[:/]+([^/]+/[^/.]+)(\.git)?$#\1#')
TMP=$(mktemp -d)
if git ls-remote --exit-code --heads origin media >/dev/null 2>&1; then
  git clone -q --depth 1 --branch media "$REMOTE" "$TMP/m"
else
  git init -q "$TMP/m" && git -C "$TMP/m" remote add origin "$REMOTE"
fi
cd "$TMP/m"
mkdir -p videos
CUT=$(date -u -d '7 days ago' +%F)
for f in videos/*; do   # names start with YYYY-MM-DD (videos and cover images)
  [ -e "$f" ] || continue
  d=$(basename "$f" | cut -c1-10)
  [[ "$d" < "$CUT" ]] && rm -f "$f"
done
cp "$SRC" "videos/$NAME"
rm -rf .git && git init -q && git checkout -q -b media && git remote add origin "$REMOTE"
git add -A && git -c user.name="Reels Engine" -c user.email="reels-engine@users.noreply.github.com" commit -q -m "media $(date -u +%F)"
git push -q -f origin media
echo "https://raw.githubusercontent.com/$SLUG/media/videos/$NAME"
