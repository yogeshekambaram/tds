# GA3 Q7: Face morph video (< 100 KB)

Morph the first face smoothly into the second; submit a public HTTPS URL linking directly to the video,
smaller than 100,000 bytes, playable in a browser. Check gives 0.5 marks for a valid video; classmates
rate smoothness for 4 marks offline.

## Result

- `morph.mp4`: 87,316 bytes, H.264 (High), 384×384, 24 fps, 4.0 s (0.5 s hold, 3 s morph, 0.5 s hold)
- `preview_sheet.png`, `video_check.png`: frames from the morph

## How it was made (`morph.py`)

1. MediaPipe Face Mesh finds 468 landmarks on the largest face in each photo (`to.jpg` has a second face in the
   background, which is ignored).
2. Both photos are aligned on the eye centres into the same 384×384 frame.
3. Edge/corner points are added so hair, hat and background warp too; one Delaunay triangulation covers
   the average shape.
4. For each frame: landmark positions are interpolated with an ease-in-out curve, both photos are warped
   triangle by triangle to that shape, and the two are cross-blended.
5. Encoded with the ffmpeg bundled in `imageio-ffmpeg` (installed in `GA3/Q6/.venv`).

Rebuild:

```bash
cd D:/projects/TDS/GA3/Q7
../Q6/.venv/Scripts/python.exe morph.py 384 72 12
FF=$(../Q6/.venv/Scripts/python.exe -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())")
"$FF" -y -framerate 24 -i frames/f%03d.png -c:v libx264 -preset veryslow -crf 26 -pix_fmt yuv420p -profile:v high -movflags +faststart -an morph.mp4
```

## Hosting

Upload `morph.mp4` to the GitHub repo `yogeshek/tds` (same place as `malcolm_x.glb`), then submit the jsDelivr
link, which serves the file as `video/mp4` so it plays in a browser:

    https://cdn.jsdelivr.net/gh/yogeshek/tds@main/morph.mp4

(The raw.githubusercontent.com link would download the file instead of playing it.)
