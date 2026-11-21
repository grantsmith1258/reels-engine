# Storyboard guide (Reels Engine)

You turn a finished, fact-checked script into `storyboards/<slug>.json`, render previews, check them, then render the video.
Style: animated dark map explainer. Every scene is the map; the story is told by camera moves, highlighted places, routes, pins, date stamps and big key-word cards. Captions of the full script appear automatically at the top middle.

## 1. One scene per script paragraph
- `scenes[i].vo` = paragraph *i* of the script, **exactly as written in Airtable's Script field** (captions are made from it, and the voiceover pauses between paragraphs are used to time scenes). Same number of scenes as paragraphs, same order.
- Optional `say`: only for Kokoro (free voice) pronunciation fixes. Ignored with the vidIQ voiceover.

## 2. Scene fields
| field | what it does |
|---|---|
| `camera` `{center:[lon,lat], zoom}` | **required**. zoom = pixels per degree of longitude. Frame is 1080 px wide: zoom 7 ≈ Europe+Africa, 13 ≈ Europe+Middle East, 22 ≈ western US / southern Africa, 50 ≈ one mid-size country (Yemen, Ethiopia), 150 ≈ Lesotho fills the frame, 400 ≈ 2.5° wide, 900+ ≈ a town. Rule of thumb: `zoom ≈ 800 / width_in_degrees` to fit something with margin. The camera glides from the previous scene's camera over `camera_move` seconds (default 1.1). |
| `highlight` `[{iso:[...], admin1:[...], shape:[[lon,lat],...], color, fill, label, label_at:[lon,lat], label_scale, pulse, residual, delay}]` | fills places with a glow. `iso` = ISO-3 country codes as Natural Earth spells them (ADM0_A3: USA, CAN, GBR, FRA, DEU, RUS, CHN, IND, ZAF, LSO, EGY, CHL, ARG...; check `data/ne_10m_admin_0_countries.geojson` if unsure). `admin1` = ISO 3166-2 state/province codes (US-WA, ZA-FS, CA-BC...). `shape` = your own polygon: **only when you are sure of the coordinates** (never guess a river or a historical border; use admin1 areas, pins or labels instead). `fill` 0-1 (default 0.78; use ~0.45-0.6 for big background countries). `pulse: true` for the place the story is about. `residual: 0.25` leaves a faint fill after the scene. |
| `pins` `[{at:[lon,lat], label, side: right/left/above/below, color, delay}]` | dot with pulse ring and label. Use for towns, battles, river mouths, a "you are here". |
| `labels` `[{at, text, size, color, delay}]` | text placed on the map (sea names, "CANADA", "USA"). |
| `routes` `[{points:[[lon,lat],...], color, delay, draw, scene_only}]` | animated dashed path with a travelling head (trade routes, migrations, a drive). Curves smoothly through the points. Stays on screen for the rest of the video unless `scene_only: true` (use that for one-off long arcs). |
| `lines` `[{points, color, label, label_at, delay, draw, persist}]` | straight dashed line (a parallel, a treaty line, a border), shown during its scene only unless `persist: true`. |
| `stamp` `{text, x, y, color, delay}` | slammed date stamp ("1846", "1500s"). Default position x 830, y 640; move it if it covers the focus. One per scene max. |
| `word` `{text, sub, size, color, delay, y}` | the key term or number, big and yellow, lower-middle (y 1440), with a small `sub` line above. "QAHWA" / "ARABIC", "1,400 m" / "ITS LOWEST POINT". Keep `text` ≤ 14 characters at size 150 (it shrinks to fit). |
| `delay` (any item) | seconds after the scene starts before the item appears. Time items to the moment the voiceover says them (scene length ≈ chars/15 s). |

Top level: `title`, `admin1_borders: ["USA"]` (faint dashed state lines for those countries; use when states matter), `outro_hold` (seconds).

Identical items in consecutive scenes (same JSON) stay on screen instead of re-popping, so copy a highlight exactly to keep it.

Colors: names `red, green, orange, blue, purple, teal, accent (yellow), white` or `#hex`. Keep one color per side of the story and reuse it all video (e.g. the place the video is about = green, its neighbour = orange).

## 3. What makes it good (retention rules)
- **Scene 1 (hook)** must show the payoff map within 1 second: the place highlighted, the surprising contrast visible. No slow zoom from space.
- A **visual change every 3-5 seconds**: new highlight, pin, route, stamp or word card inside long scenes (use `delay`).
- Use `word` for the 3-5 most memorable terms or numbers only, not every scene.
- Use `stamp` for dates the script says aloud.
- Go wide for context, then close for the detail, alternate; don't stay at one zoom for 3 scenes.
- Last scene (the follow line): pull out to the wide context with the main place pulsing.
- Never show something false: no invented borders, no guessed coordinates, no labels the script doesn't support. If the map can't show a claim accurately, show a pin, label or word card instead.

## 4. Check before rendering (required)
1. `python3 engine/render.py storyboards/<slug>.json /tmp/x.mp4 --audio build/vo.mp3 --preview auto` prints each scene's timing and writes `build/<slug>/contact_sheet.png` (one frame per scene).
2. Open the contact sheet with Read and check every frame: the subject is visible and inside the frame, labels readable and not overlapping each other, the stamp, word card or captions, nothing important under the top captions (y < 450) or the bottom 250 px (app buttons), and colors consistent.
3. Fix and preview again (max 3 rounds), then render: `python3 engine/render.py storyboards/<slug>.json build/<slug>.mp4 --audio build/vo.mp3`. Takes about 12-14 minutes for 70 s.

## 5. Example
`storyboards/examples/` has full examples (Lesotho with a vidIQ voiceover, Point Roberts, coffee).

## 6. Cover image (required on every video)
Top-level `cover` makes the profile-grid thumbnail: the hook map with the video's single craziest fact in huge lettering. The full render writes it to `build/<slug>_cover.png` and also uses it as the video's first 2 frames, so TikTok and Instagram show it on the profile grid by default (neither app's posting API takes a separate cover).
```json
"cover": {
  "lines": [{"text": "IT'S ONLY", "size": 150}, {"text": "2019", "size": 380, "color": "accent"}, {"text": "IN ETHIOPIA", "size": 150}],
  "badge": "AND THE YEAR HAS 13 MONTHS",
  "camera": {"center": [39.5, 15.5], "zoom": 52},
  "y": 640
}
```
- `lines`: 2-4 short lines, ALL CAPS, the fact that sounds impossible (the Hook, squeezed). Make the one shocking word or number the giant yellow line (size 300-380); the others ~130-160. Each line ≤ ~12 characters at 150.
- `badge`: optional red strip under the text, the second jaw-drop in ≤ 28 characters.
- `camera`: frame the subject so it sits in the lower half (y ≈ 1150-1650) and the text block (centred on `y`, default 900) sits above it. Without it, the camera of `scene` (default 0) is used.
- Keep everything between y 260 and 1680: the profile grid crops the 9:16 frame to 3:4 around the centre.
- Check it alone first: `python3 engine/render.py storyboards/<file>.json /tmp/x.mp4 --audio build/vo.mp3 --cover build/cover.png`, then Read the PNG. Never put a claim on the cover that the script doesn't make.
- Host it with `bash publish_media.sh build/<slug>_cover.png <YYYY-MM-DD>-<slug>-cover.png` and save the link to the row's "Cover image" field.

## 7. Everyday-comparison pictures (required in the hook)
Every video hooks with a semi-ridiculous comparison people know from real life, shown as a picture on the map (owner, 2026-11-21; modeled on an El Niño reel that opens "Just imagine if a giant natural fan that helps cool the eastern Pacific suddenly stopped working", with a desk fan sitting on South America that spins, then stops). Scene field:
```json
"pictures": [{"emoji": "🪭", "at": [-80.0, -2.0], "size": 240, "motion": "spin", "stop_after": 2.5, "label": "A GIANT FAN", "delay": 0.3}]
```
| field | what it does |
|---|---|
| `emoji` | one emoji, drawn in full colour (Noto Color Emoji). Pick an everyday object: 🍕 🚪 🛋️ 🏠 🪑 🧀 ⏰ 🎂 🧱 🚗 🔑 🧾 🪭 🌀 📦 🛏️ ... |
| `at` `[lon,lat]` or `x`,`y` | on the map (moves with the camera) or fixed on screen (default 540, 900) |
| `size` | height in px (default 260). Keep it big: 200-320 in the hook. |
| `motion` | `bob` (default), `spin` (+ `speed` turns/s, `stop_after` s to slow to a halt: the "it stopped working" beat), `shake` (alarm, conflict), `pulse`, `move` (+ `to` [lon,lat] or [x,y], `move_time`) |
| `label` | optional short caption under it |
| `delay` | as elsewhere |
Rules: the picture appears with the comparison's words (scene 1, by second 2) and comes back once later when the script returns to the comparison. Keep it out of the caption zone (y < 450) and the bottom 250 px, and off the word card (y ≈ 1300-1500). Identical items in consecutive scenes stay on screen.
