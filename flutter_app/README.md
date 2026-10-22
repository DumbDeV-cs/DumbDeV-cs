# Skyline Weather — Flutter

Skyline Weather is the Flutter/Dart Android app. It includes live city suggestions, device location, animated weather illustrations, hourly and 7-day forecasts, temperature units, and a private on-device vault for real files and folders. Type your vault passcode into the city search field to unlock it; the setup button is available until you create your first passcode.

## Open and run in VS Code

Open this `flutter_app` folder in VS Code. Install the Flutter and Dart extensions if needed, run `flutter pub get`, then launch on an Android emulator or connected phone. The Android runner and Gradle files are included.

To make an APK for installing on a phone, run `flutter build apk --release`. The output is `build/app/outputs/flutter-apk/app-release.apk`. The starter Android setup signs this local APK with the debug key; configure your own release keystore before publishing it on Google Play.

The vault uses Android's app-private documents folder for copied files. Importing a ZIP extracts it into a real folder; importing a folder copies its contents recursively. Nothing is uploaded. The passcode is stored with Android secure storage, hidden while entered, and rate-limited after repeated failures. Screenshots are blocked while the vault is unlocked, and Android backup is disabled. New passcodes need 8 or more characters and must start with a number or symbol so even mistyped passcodes are never sent to city search. Vault files remain app-private but are not individually encrypted; use your device's screen lock and storage encryption.

Weather and city lookup use Open-Meteo. No API key is needed. Location access is requested only when you tap the location button.
