<p align="center">
  <img src="https://github.com/makstymoshenko/web_media_server/blob/main/assets/web_media_server_icon.png" alt="Web Media Server" height="180" width="180">
</p>

# Web Media Server v.0.1.1 [License: MIT]

### Simple Windows 11 (Python) Web Media Server - preview files and media with thumbnails and optional ffmpeg-based conversion.

### 8 Startup modes:

- *Original Destination (1) -> Server from selected folder*<br>
- *Original Destination (2) -> Server from selected folder + meta/tags from audiofiles*<br>
- *Original Destination (3) -> Server from selected folder + logs*<br>
- *Original Destination (4) -> Server from selected folder + logs + meta/tags from audiofiles*<br>
- *Temporary Destination (1) -> Copy/Convert into _temp/*<br>
- *Temporary Destination (2) -> Copy/Convert into _temp/ + meta/tags from audiofiles*<br>
- *Temporary Destination (3) -> Copy/Convert into _temp/ + logs*<br>
- *Temporary Destination (4) -> Copy/Convert into _temp/ + logs + meta/tags from audiofiles*

**(Originally it was implemented as media file access for iPad 1 Safari, but it may also work on other devices that support this)**

⚠️ ffmpeg: bundled ffmpeg-8.0-essentials_build preferred (fallback to system ffmpeg); used for thumbnails, full image JPEG, video MP4, and audio meta-tags extraction.
