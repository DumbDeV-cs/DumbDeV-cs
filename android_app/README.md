# Android build

This folder is the Kivy Android port; the desktop version remains in the parent folder.

It includes city suggestions, Open-Meteo forecasts, GPS location with IP-based approximate fallback, animated weather art, an animated hourly chart, rain chances, a seven-day temperature range display, and a vault that copies files into app-private storage. Android uses its system picker for individual files; import folders as ZIP archives with **Folder ZIP**. The encrypted vault index tracks local copies; the copied file contents themselves are ordinary local files.

## Debug APK

Use Linux or WSL2 with Ubuntu and install Buildozer's current prerequisites (Python, Java, Git, Android SDK/NDK). Keep the project in the WSL Linux filesystem, not under `/mnt/c`.

1. `cd` into this folder in Linux/WSL.
2. Install Buildozer and its Linux prerequisites from the official Buildozer installation guide.
3. Run `buildozer android debug`. The first build downloads the Android SDK/NDK; it may take several minutes. The APK is written to `bin/`.
4. Install the debug APK on a device and check GPS permission, city search, and vault file picking.

## Release

Create a private signing key, configure signing only in your local build environment, and run `buildozer android release`. Keep the signing key and passwords out of source control. Verify the APK signature before sharing. This project targets API 36; Google Play requires API 36 or higher for new submissions from August 31, 2026. Play distribution uses an Android App Bundle, so build an AAB for Play rather than the sideload APK.

This Codex workspace runs on Windows and does not have the Linux Android toolchain installed, so it can prepare and syntax-check the project but cannot produce a signed release APK here.
