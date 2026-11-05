# SH Download Manager browser extension

This is an optional Manifest V3 extension for Chrome, Edge and other Chromium browsers. It sends links, files and media to the local **SH Download Manager** app.

The extension does **not** send data to a remote server. It only calls `http://127.0.0.1:<port>/add` and `/formats` on this PC.

## Install

1. Start SH Download Manager and make sure **Settings → Integration → Enable local browser integration API** is on. The default port is `53991`.
2. Open the browser's extension page:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `browser_extension` folder.
5. Pin the extension if you want the toolbar button.

You can also open the folder from the app: **Tools → Open browser extension folder**.

## Use

### Video and audio panel

On any page with a `<video>` or `<audio>` player, a small **Download this video** / **Download this audio** button appears over the player. Click it and the panel lists what the app can fetch:

- For YouTube and similar media pages: every quality yt-dlp reports, for example `MP4 file, quality 1080p HD (video + audio)`, `720p`, `480p`, `360p`, `240p`, `144p`, plus `Audio only, MP3 (320 kbps)` and `Audio only, MP3 (128 kbps)` options.
- For a plain embedded file: the single media file.

Pick a row and the download starts in the app. This panel appears only over video and audio elements; ordinary links and images are left alone.

### Links and pages

- Right-click a link, video, audio element or image and choose **Download with SH Download Manager**.
- Right-click a page and choose **Send this page to SH Download Manager**.
- Right-click a page and choose **Download every link on page** to send all links, media sources and images at once.
- Or click the toolbar button to send the current page URL.

The app receives the URL, the referer and the browser cookies for that site. Cookies are needed for downloads that require a signed-in session. They are stored only in the app's local download state.

If the app is not running, or the API is disabled, the extension shows a red `!` badge and the media panel reports the error.

## Options

Open the extension options to change the local API port or test the connection. The port must match the port in **Settings → Integration**.

## Permissions

- `contextMenus` — add the download commands.
- `cookies` — read cookies for the link being downloaded.
- `storage` — remember the API port.
- `scripting` — read every link on a page for **Download every link on page**.
- `<all_urls>` — allow cookies for any site, the on-page media panel and localhost API access.

The content script only adds the dark media button and quality panel. It never modifies page content or reads page data beyond the address of the media element it decorates.

## Notes

- Ordinary files are downloaded by the app's segmented HTTP engine. Media pages are downloaded by yt-dlp, which is bundled with the app. Merging 1080p and above needs ffmpeg on the machine.
- Some sites require a login. Sign in to the site in the browser before downloading; the app will use the cookies captured at the moment you click.
- The app must be running with the API enabled. Start it from the Start menu or the desktop shortcut.
- The app does not bypass DRM, paywalls or site access controls.
