# Web Media Server v.0.1.1 [License: MIT]
## (Windows 11 Media Server To iPad 1 Safari)

### Windows 11 (Python) Web Media Server serving files and media with thumbnails, image rotation, and optional ffmpeg-based conversion.

### 8 Startup modes:

- *Original Destination (1) -> Server From Selected Folder*<br>
- *Original Destination (2) -> Server From Selected Folder + Meta/Tags From Audiofiles*<br>
- *Original Destination (3) -> Server From Selected Folder + Logs*<br>
- *Original Destination (4) -> Server From Selected Folder + Logs + Meta/Tags From Audiofiles*<br>
- *Temporary Destination (1) -> Copy/Convert Into _temp/*<br>
- *Temporary Destination (2) -> Copy/Convert Into _temp/ + Meta/Tags From Audiofiles*<br>
- *Temporary Destination (3) -> Copy/Convert Into _temp/ + Logs*<br>
- *Temporary Destination (4) -> Copy/Convert Into _temp/ + Logs + Meta/Tags From Audiofiles*

⚠️ ffmpeg: bundled ffmpeg-8.0-essentials_build preferred (fallback To System ffmpeg); used for thumbnails, full image JPEG, video MP4, and audio meta-tags extraction.
