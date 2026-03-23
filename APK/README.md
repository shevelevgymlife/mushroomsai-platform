# APK Android wrapper for MushroomsAI

This folder contains an Android app project that wraps the live MushroomsAI web app.

## What it gives you

- Same backend/data as web + Telegram mini app (single source of truth).
- Login works through existing site flows (Google / Telegram) inside WebView.
- Adaptive UI comes from the responsive web frontend you already maintain.
- File upload support (needed for avatar/product/post image forms).

## Build APK locally

1. Install Android Studio (or Android SDK command line tools).
2. Open the `APK/` folder as a project.
3. Let Gradle sync.
4. Build debug APK:
   - Android Studio: Build -> Build APK(s)
   - or CLI: `./gradlew :app:assembleDebug`
5. Output file:
   - `APK/app/build/outputs/apk/debug/app-debug.apk`

## Publish APK for download

Recommended options:

- GitHub Releases: upload `app-debug.apk` (or signed release APK/AAB)
- Or add CI workflow to build APK on every push and attach artifact.

## Important notes

- This project currently points to `https://mushroomsai.ru/dashboard`.
- If domain changes, update `START_URL` in `MainActivity.java`.
- For production store release, add proper app icon, signing config, and Play policies.
