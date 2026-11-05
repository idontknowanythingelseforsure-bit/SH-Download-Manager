# IDM analysis and clean-room notes

This document records the static analysis of the locally installed Internet Download Manager (IDM) that informed the **feature set** of SH Download Manager. No IDM code, resources, icons, strings, protocol handlers or licensing mechanisms were copied.

## Scope

- Target: `I:\Projects\REA\Internet Download Manager\IDMan.exe` and the files installed beside it.
- Method: static inspection only — PE metadata, imports, resources, strings and registry configuration.
- Explicitly out of scope: activation, trial reset, serial checks, license bypass, patching, network interception of IDM servers, and redistribution of IDM files.
- No IDM process was automated or instrumented for this work.

## Identity observed

| Property | Value |
| --- | --- |
| Product version | 6.43.16.2 |
| Binary type | 32-bit Windows GUI executable (PE32) |
| Subsystem / manifest | Windows GUI, Common Controls 6, `asInvoker`, DPI-aware |
| Signature | Valid Tonec Inc. Authenticode signature |
| SHA-256 | `3B5DC9CE53EB31933EFFA3FD973BEC068A78E0F8C1D15A919B081F0C4D9325BA` |

## Installed components inspected

- `IDMan.exe` — main GUI and download engine host.
- `IDMMsgHost.exe` — helper process used by browser integration.
- `idmwfp*.sys`, `idmtdi*.sys` — network filter drivers.
- Browser extension packages (CRX/XPI) and native-messaging manifests.
- Shell extension DLLs and language/toolbar resources.
- Installer/uninstaller metadata and default registry configuration.

## Evidence from the binary

### Imports and runtime

`IDMan.exe` imports from `WS2_32.dll`, `WININET.dll`, OLE/COM libraries, `SHELL32.dll` and `COMCTL32.dll`. This is consistent with a classic Win32 download client that uses WinINET/Winsock for HTTP and HTTPS, COM for shell integration, and Common Controls for the UI.

### UI resources

Static resource enumeration found:

- 105 dialog resources.
- 174 string-table resources.
- 37 icon groups.

This indicates a large, dialog-driven desktop UI with extensive localization, rather than a thin wrapper around a browser.

### Download protocol clues

Strings and configuration keys in the binary show the building blocks of a segmented HTTP downloader:

- `Range: bytes=0-`
- `Content-Range`
- `ETag` and `If-Range`
- `Last-Modified` / `If-Modified-Since`
- `Content-Disposition`
- Cookie database locations for Chrome/Edge/Firefox profiles
- User-Agent and referer handling
- Retry, timeout and connection-count settings

These observations support the following functional model:

1. Probe the URL with a range request.
2. Read the total length from `Content-Range` (or `Content-Length`).
3. Split the file into byte ranges and request them concurrently.
4. Write each range at its absolute offset in a partial file.
5. Persist per-range progress so an interrupted transfer can resume.
6. Validate the remote file with ETag/Last-Modified before resuming.
7. Fall back to a single stream when ranges are unavailable.

SH Download Manager implements this model independently, using Python's `requests` and its own scheduler, segment planner, rate limiter, state file and UI.

### Queue, scheduler and integration clues

Registry keys and strings referenced by the installation include:

- `maxMC` — maximum simultaneous connections/downloads.
- `MonitorUrlClipboard` — clipboard URL monitoring.
- `TrayIcon` — minimize-to-tray behavior.
- `Queue` and `Scheduler` — queued and scheduled transfers.

The browser integration is split between a browser extension and a local helper process. IDM also registers shell integration and a URL protocol handler.

## Feature mapping

The table shows how the observed feature set was reimplemented from scratch.

| Observed IDM capability | SH Download Manager implementation |
| --- | --- |
| Segmented HTTP/HTTPS transfer | `engine.py`: range probe, segment planner, worker pool |
| Dynamic part splitting | Idle workers split the largest remaining segment |
| Resume after interruption | JSON state with per-segment byte positions |
| ETag / Last-Modified validation | Probe stores validators; a changed file restarts cleanly |
| Retry on transient failures | Bounded exponential backoff for probe and segment requests |
| Servers without ranges | Automatic single-stream fallback |
| Browser cookie/referer use | Add dialog advanced headers + extension-provided cookies |
| Queue and parallel limit | Scheduler with `max_concurrent` |
| Speed limit | Global token-bucket limiter shared by all workers |
| Category folders | Extension-based category mapping and configurable folders |
| Clipboard monitoring | Optional clipboard poller, disabled by default |
| Tray / minimize behavior | Start-minimized setting and a compact status bar |
| Browser integration | Localhost API plus a Manifest V3 Chrome/Edge extension |
| URL protocol handler | `shdm://` handler registered under `HKCU\Software\Classes` |
| Media grabber | HTML parser, media-source extraction and parallel probing |

## What was intentionally not reproduced

- IDM's visual design, icons, dialogs, string tables and branding.
- IDM's licensing, activation, trial or update mechanisms.
- IDM's proprietary network filter drivers or browser helper binaries.
- IDM's exact scheduling policy, segment heuristics or server-specific workarounds.
- DRM circumvention, paywall bypass or access-control bypass.
- HLS/DASH stream reconstruction, FTP, BitTorrent or video muxing.

## Implementation differences

SH Download Manager is a Python/Tkinter application packaged as a single Windows executable. It uses ordinary HTTP range requests and does not install kernel drivers. This keeps the implementation auditable and portable, at the cost of some IDM-specific integration:

- No native browser messaging host; the extension talks to `127.0.0.1` instead.
- No shell context-menu extension in this release.
- No multi-threaded disk cache or proprietary connection reuse layer.
- Stream manifests are downloaded as files, not reconstructed into playable media.

## Engine test coverage

`tests/test_engine.py` starts a local HTTP server and verifies real data integrity:

- 6 MiB multipart download with SHA-256 comparison.
- `Content-Disposition` file naming and redirect following.
- Servers that ignore `Range` (single-stream fallback).
- Pause/resume with byte-for-byte SHA-256 comparison.
- Retry after a `503` response.
- Duplicate URL detection.

## Clean-room statement

The analysis above was performed on a local installation for interoperability and feature research. SH Download Manager was written as a new codebase. It contains no IDM binaries, resources, decompiled code or extracted assets. Internet Download Manager is a trademark of its owner; this project is not affiliated with or endorsed by that owner.
