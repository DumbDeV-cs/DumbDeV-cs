[app]
title = Stealth Weather Pro
package.name = stealthweatherpro
package.domain = org.codexweather
source.dir = .
source.include_exts = py,png,jpg,kv,json
version = 2.0.0
icon.filename = assets/icon.png
presplash.filename = assets/presplash.png
requirements = python3,kivy,requests,cryptography,plyer,androidstorage4kivy
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_COARSE_LOCATION,ACCESS_FINE_LOCATION
android.api = 36
android.minapi = 23
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.enable_androidx = True

[buildozer]
log_level = 2
warn_on_root = 1
