[app]

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------
title = SH Download Manager
package.name = shdownloadmanager
package.domain = org.shdownloadmanager

# ---------------------------------------------------------------------------
# Source files
#
# main.py is the Kivy front end.  src/shdm holds the shared download engine,
# settings, grabber and yt-dlp helpers used by both the Windows and the
# Android builds.
# ---------------------------------------------------------------------------
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,txt,ico,json,md
source.exclude_dirs = .git,bin,.buildozer,__pycache__,tests,.github,build,dist,docs,installer,browser_extension,tools
source.exclude_patterns = *.exe,*.pyw,*.apk,*.zip,*.iss,*.ps1,*.log

version = 1.1.0

# ---------------------------------------------------------------------------
# Python requirements
#
# python-for-android installs pure-Python packages with --no-deps, so every
# runtime dependency has to be listed explicitly here.  The Windows-only
# modules (tkinterdnd2, pystray, winreg) are intentionally omitted.
# ---------------------------------------------------------------------------
requirements = python3,kivy,pyjnius,android,openssl,sqlite3,requests,urllib3,certifi,charset-normalizer,idna,yt-dlp

orientation = portrait
fullscreen = 0

# icon.png is generated from assets/icon.ico (512x512).
icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/icon.png

# ---------------------------------------------------------------------------
# Android configuration
# ---------------------------------------------------------------------------
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK

# Share a link from a browser straight into the app.
android.manifest.intent_filters = android_intent_filters.xml
# androidx FileProvider, used by "Open" / "Open Folder" in the completion card.
android.extra_manifest_application_arguments = android_extra_application.xml
android.gradle_dependencies = androidx.core:core:1.13.1
android.add_resources = android_res

android.api = 33
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21
# Build only the 64-bit ARM target to keep CI time reasonable.
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True
android.enable_androidx = True

# ---------------------------------------------------------------------------
# Buildozer
# ---------------------------------------------------------------------------
[buildozer]
log_level = 2
warn_on_root = 1
