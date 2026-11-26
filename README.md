# Reels Engine

Renders 9:16 animated-map explainer videos (Real World Explained) from a storyboard + voiceover.
Used every morning by the "Daily Reel engine" scheduled task and by the dashboard's "Create next video" button.

```bash
bash setup.sh                                   # once per fresh workspace
python3 engine/render.py storyboards/x.json build/x.mp4 --audio build/vo.mp3 --preview auto   # check frames
python3 engine/render.py storyboards/x.json build/x.mp4 --audio build/vo.mp3                  # render
bash publish_media.sh build/x.mp4 2026-11-12-x.mp4   # puts it on the media branch, prints the public link
```

- `engine/render.py`: the renderer (Python + cairo + ffmpeg). Map data: Natural Earth (public domain).
- `STORYBOARD_GUIDE.md`: how to write a storyboard. Read it before writing one.
- `storyboards/`: every video's storyboard; `storyboards/examples/` has reference ones.
- `media` branch: finished videos from the last 7 days, so vidIQ can import them from a public link.

## Chat-demo style (everyday-AI account)
`engine/chat.py` renders AI-setup explainers (phone chat + connection-flow diagrams, Apple-style `"look": "clean"`). Read `CHAT_GUIDE.md`; storyboards live in `storyboards/ai/`.
