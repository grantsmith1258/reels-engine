# Storyboard guide (Reels Engine)

You turn a finished, fact-checked script into `storyboards/<slug>.json`, render previews, check them, then render the video.
Style: animated dark map explainer. Every scene is the map; the story is told by camera moves, highlighted places, routes, pins, date stamps and big key-word cards. Captions of the full script appear automatically at the top middle.

## 0. Satellite style (default since 2026-11-23)
Owner asked for more realistic, less "blank map" visuals. New storyboards set top-level `"style": "satellite"`:
- **Real imagery**: cloud-free Sentinel-2 satellite mosaics (wide and regional views) and ESA WorldCover 10 m composites (towns, rivers), with terrain relief and depth-tinted oceans. Built automatically from the scene cameras on the first preview (a few minutes; cached in `data/sat_cache` and `build/<storyboard>/levels`). Credit line for Sources: "Imagery: contains modified Copernicus Sentinel data (2020-2021), Sentinel Hub / ESA WorldCover (CC BY 4.0). Terrain: AWS Terrain Tiles. Borders: Natural Earth."
- **3D camera**: the map is tilted (`"pitch"`, default 28 degrees; per scene `camera.pitch`, `camera.bearing`). Far-apart scenes fly over (zoom out, travel, zoom in) with motion blur; inside a scene the camera keeps drifting (`drift_zoom`, default 0.07; `drift_rotate`, default 2.5 degrees). Never static.
- **Highlights** draw their glowing border on over `draw` seconds (default 1.0), sit slightly raised with a shadow, and are translucent (`fill` default 0.3 so the imagery shows through; use 0.4-0.5 for the subject, 0.6-0.7 for small countries on world views).
- **Pins and route labels** are frosted dark cards with a coloured edge; `labels` and highlight labels are letter-spaced place names. All stay upright on the tilted map.
- Faint country borders everywhere (`"country_borders": false` to hide); `admin1_borders` adds state lines.
- Pitch 22-26 for world views, 28-36 for regions; world views: keep Antarctica out of frame (centre latitude 25-32 at zoom ~3.3).
- Everything in sections 1-7 still applies (one scene per paragraph, colours per side of the story, 3-4 coloured places, a change every 2-3 s, cover, captions).

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
`cuts` (optional): scene start times in seconds, one per paragraph break, when the preview shows scenes split at the wrong pause (scripts with many short sentences). Read the pauses with `ffmpeg -i build/vo.mp3 -af silencedetect=noise=-35dB:d=0.25 -f null -` and put each cut in the middle of the pause that ends a paragraph.

Identical items in consecutive scenes (same JSON) stay on screen instead of re-popping, so copy a highlight exactly to keep it.

Colors: names `red, green, orange, blue, purple, teal, accent (yellow), white` or `#hex`. Keep one color per side of the story and reuse it all video (e.g. the place the video is about = green, its neighbour = orange).

## 3. What makes it good (retention rules)
- **Scene 1 (hook)** must show the payoff map within 1 second: the place highlighted, the surprising contrast visible. No slow zoom from space.
- A **visual change every 3-5 seconds**: new highlight, pin, route, stamp or word card inside long scenes (use `delay`).
- Use `word` for the 3-5 most memorable terms or numbers only, not every scene.
- Use `stamp` for dates the script says aloud.
- Go wide for context, then close for the detail, alternate; don't stay at one zoom for 3 scenes.
- Last scene (the follow line): pull out to the wide context with the main place pulsing.
- **Color and variety (owner, 2026-11-22, after the Denver v1 was called "terrible"):** never one flat color. Most scenes show 3-4 colored places: the subject plus its neighbors, each in its own color (e.g. subject green, neighbors blue / purple / red / teal at fill ~0.4), kept identical across scenes so they stay on screen; put neighbor names in each scene's `labels` so they fit that camera. Never zoom so close that the frame becomes one flat fill or an empty dark map (Natural Earth has no streets or small rivers): for towns, stay at zoom ≤ ~170 and use pins. Every zoom-in must reveal something new, and something must move or appear every 2-3 seconds.
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

## 7. Ridiculous-comparison hook pictures
The hook is a comparison that sounds a bit ridiculous ("no way, a FAN does that?!") and then turns out to fit the story exactly (owner, 2026-11-21; model: an El Niño reel opening "Just imagine if a giant natural fan that helps cool the eastern Pacific suddenly stopped working", where the fan turns out to be the trade winds). See the Channel Settings row "Ridiculous-comparison hook".
Use a picture ONLY for that comparison, when it adds to it: the fan that spins and then stops, the door that slams, the bedroom inside the neighbor's house. Never decorative emojis, never one per scene. Usually one picture: in the hook (by second 2) and again at the payoff line that explains what the object really is. If the comparison doesn't need a picture, or no emoji shows it well, use none.
```json
"pictures": [{"emoji": "🌀", "at": [-80.0, -2.0], "size": 240, "motion": "spin", "stop_after": 2.5, "delay": 0.3}]
```
| field | what it does |
|---|---|
| `emoji` | one emoji, drawn in full colour (Noto Color Emoji): the comparison's object |
| `at` `[lon,lat]` or `x`,`y` | on the map (moves with the camera) or fixed on screen (default 540, 900). Put it where the real thing is in the story. |
| `size` | height in px (default 260); 200-320 in the hook |
| `motion` | `bob` (default), `spin` (+ `speed` turns/s, `stop_after` s to slow to a halt: the "it stopped working" beat), `shake`, `pulse`, `move` (+ `to` [lon,lat] or [x,y], `move_time`). Pick the motion the comparison describes. |
| `label` | optional short caption under it |
| `delay` | time it to the words |
Keep it out of the caption zone (y < 450), the bottom 250 px and the word card (y ≈ 1300-1500).

## 8. Story style (owner, 2026-11-27): model reel "France and the Netherlands share a land border... in the Caribbean"
The owner liked an 81 s Saint Martin reel and wants every video to feel more like it. Apply on top of sections 0-7.
**Topic and script**
- Hook = a paradox about two famous things: "This is X, and this is Y. They share a border, just not where you think." Show both places, colored and labeled, in the first 2 seconds, then reveal where it really happens.
- One clear story arc: hook paradox, reveal on the map, flashback (how it happened, with dates), the funny legend or human story, then "but here's what really happened" (the real treaty, ruling or number), then today's map. Myth first, fact second, when the topic has a legend.
- Casual, lighthearted narration with short punchy clauses; names and dates the map can show.
**Visuals (all built into the engine)**
- Country / state names as solid colored badges: add `"pill": true` to a highlight (label drawn in the highlight's own color). Use saturated, clearly different colors for the two sides (e.g. blue vs red) and keep them all video.
- History flashbacks on an old paper map: add `"tint": "parchment"` to each scene set in the past (it fades in and out at scene edges). Go back to normal satellite color for "today".
- Characters are **countryballs** (owner, 2026-11-27): flag-coloured balls with cartoon eyes, drawn by the engine (engine/countryballs.py, free). A `pictures` item with `"ball": "FRA"` (ISO-3; ~45 flags built in, see FLAGS) or a custom flag `{"h": ["#hex", ...]}` / `{"v": [...]}` for states, colonies or old flags. Options: `"mood"` happy / angry / sad / shock / smug, `"look"` left / right / ahead (face each other in a standoff), `"say"` a speech bubble ("MINE!", "DEAL?") with `"say_delay"`, and the usual `size` (170-260), `motion` (`bob` = hop, `shake`, `move` + `to`, `spin`), `at` or `x`,`y`, `delay`. They blink on their own. Use 1-3 per scene for the countries the line names, when they act (claim, fight, sign, split, swap); keep each one's position consistent between scenes. Never put a claim in a bubble that the script doesn't make. Flag emojis still work for small mentions. Story objects too: ⚔️ fighting, 📜 treaty, ✍️ signing, 🔪 or ✂️ splitting land, 🍷/🥃 a legend's drinks, 📸 tourists, 🤝 a deal. Every picture now plays a pop sound.
- Something new on screen every 1.5-2.5 s: a badge, a flag, a route, a picture, a stamp. Routes for journeys (who walked or sailed where).
- Camera rule unchanged (section 3 and Channel Settings "Show what the script says"): only go to places the line names.
