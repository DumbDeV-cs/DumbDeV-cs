# Stealth Weather Pro

A desktop weather dashboard with city search suggestions, approximate current-location weather, interactive weather graphics, and an encrypted private vault for file and folder paths.

## Run

1. Install Python 3.10 or newer.
2. Run `pip install -r requirements.txt`.
3. Run `python main.py`.

Weather data and city search use Open-Meteo. The current-location button uses an IP-based estimate from ipapi.co (not device GPS), then requests a forecast from Open-Meteo. The app makes those network requests only when you search or press the location button. Vault entries store paths and notes; file contents are not read. The vault can also create a new empty folder or `.txt` file in a folder you select and then bookmark it.

The app creates `~/.wx_pro` for its local settings and encrypted vault. Set `WX_PRO_HOME` to choose another storage directory.
