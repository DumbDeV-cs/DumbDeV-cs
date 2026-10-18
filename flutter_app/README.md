# Stealth Weather Pro — Flutter

This is the Flutter/Dart Android version of the weather app. It includes live city search suggestions, device location, an animated weather illustration, hourly and 7-day forecasts, temperature units, and a private on-device vault for real files and folders.

## Open and run in VS Code

Open this `flutter_app` folder in VS Code. Install the Flutter and Dart extensions if needed, run `flutter pub get`, then launch on an Android emulator or connected phone. The Android runner and Gradle files are included.

To make an APK for installing on a phone, run `flutter build apk --release`. The output is `build/app/outputs/flutter-apk/app-release.apk`. The starter Android setup signs this local APK with the debug key; configure your own release keystore before publishing it on Google Play.

The vault uses Android's app-private documents folder for copied files. Importing a ZIP extracts it into a real folder; importing a folder copies its contents recursively. Nothing is uploaded. A vault code is stored in Android secure storage and the vault file tree stays within the app sandbox.

Weather and city lookup use Open-Meteo. No API key is needed. Location access is requested only when you tap the location button.
