# Chat-demo guide (everyday-AI account, @efficiencymaxing)

`engine/chat.py` renders 9:16 explainer videos of AI setups: a phone chat screen (prompt typed, answer streams, key lines highlighted), full-screen title slides, and animated connection-flow diagrams. Rendering costs 0 credits; the only paid step is the vidIQ voiceover (Alice, `Xb7hH8MSUJpSbSDYk0k2`).

```bash
bash setup.sh
python3 engine/chat.py storyboards/ai/<file>.json /tmp/x.mp4 --audio build/vo.mp3 --preview auto        # contact sheet
python3 engine/chat.py storyboards/ai/<file>.json /tmp/x.mp4 --audio build/vo.mp3 --preview 2,9,20.5     # chosen times
python3 engine/chat.py storyboards/ai/<file>.json build/<slug>.mp4 --audio build/vo.mp3                  # render (~2-3 min)
```
Reference storyboard: `storyboards/ai/2026-11-25-medical-bill-3-levels.json`. Copy its structure.

## Video format: 3 levels (owner rule)
1. **Hook**: the efficiency payoff ("from thirty seconds to fully automatic"), as a full-screen slide.
2. **Level 1**, easy, in any free AI chat app: a `card` slide (Level 1 / "Thirty seconds."), then the chat: `image` + `user` prompt, then `ai` answer with `highlight`s.
3. **Stick around**: one short line on its own scene, with a slide teasing Level 3.
4. **Level 2**, a bit of setup: a `card` slide, then a `flow` of 2-3 connected tools plus a `notify` result.
5. **Level 3**, the master version: a `card` slide, then a bigger `flow` (often `"scenes": 2`).
6. **Close**: a save/follow slide.

## Storyboard fields
Top level: `"style": "chat"`, `"look": "clean"` (required: Apple-style), `app_name` ("AI Assistant", generic: never an AI brand's name or look), `outro_hold`, `cover`.

One scene per script paragraph; `vo` = that paragraph exactly (scene timing comes from the voiceover's pauses).

| scene field | what it does |
|---|---|
| `card` `{eyebrow, title:[str or {text, grad:true}], size, sub, icon, y, dur, delay}` | full-screen black slide; `grad` paints a line in the blue to purple gradient (key words only). `dur` seconds, else the whole scene |
| `user` + `user_delay`, `type_cps` | prompt typed in the input bar, then sent as a blue bubble |
| `image` `{icon, label}` | attachment tile sent with the user message (icon: doc, camera...) |
| `ai` + `ai_delay`, `think`, `stream` | typing dots, then the answer streams. Supports `**bold**`, `1. ` and `- ` lists. It must be a real AI answer to the prompt shown |
| `highlight` `[{text, delay}]` | blue underline sweep on that phrase in the latest AI answer |
| `word` `{text, sub, y, delay, hold}` | small dark pill label (y ≈ 600 over the phone) |
| `flow` `{eyebrow, title, subtitle, steps:[{icon, label, sub, delay}], notify:{text, app, delay}, scenes, delay}` | full-screen connection diagram; step `delay` is seconds after the scene starts (it can run into the next scenes with `scenes: 2`). Time each step to the words |
| `zoom`, `zoom_delay`, `focus_y` | gentle push-in on the phone (1.0-1.08) |
| `clear` | start a fresh chat |

Icons: `camera, ai, message, mail, sheet, doc, folder, check, bell, phone`.
`cover` `{icon, eyebrow, title:[...], size, sub, y}`: becomes the first frames and `build/<slug>_cover.png`.

## Check before rendering
Read the contact sheet: captions readable at the top, nothing cut off, the flow steps appear as the voice names them, highlights land on the right words, the notification text fits. Then render and spot-check 4 frames with ffmpeg.

## Honesty
Only show setups that really work with real tools (check the official docs). Say "a tool like Zapier", never promise "free" if a paid key is needed. Example bills/data must be labelled as examples in the caption.
