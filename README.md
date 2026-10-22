# Skyline Weather

A desktop weather dashboard with ranked city suggestions, approximate current-location weather, animated weather illustrations, a temperature trend chart with a moving highlight, rain-chance indicators, and an encrypted vault. Imported files and folders are copied as real items into `~/.wx_pro/saved_items`; encrypted metadata tracks the saved copies. Create file/folder actions make real items there too. Removing a vault entry removes its managed copy.

## Run

1. Install Python 3.10 or newer.
2. Run `pip install -r requirements.txt`.
3. Run `python main.py`.

Weather and city search use Open-Meteo. The current-location button uses an IP-based estimate from ipapi.co (not device GPS). Requests run only after you search or press the location button. Set `WX_PRO_HOME` to choose another local storage directory.

## Android / Flutter

The Flutter mobile app is in [flutter_app](flutter_app/README.md). In VS Code, open the `flutter_app` folder (or run `cd flutter_app` from this repository root) before running `flutter pub get`, `flutter run`, or `flutter build apk --release`. The app has animated weather graphics, city autocomplete, device GPS, hourly and 7-day forecasts, unit switching, and an on-device file vault that stores actual file and folder copies.

The earlier Kivy experiment remains in [android_app](android_app/README.md).
