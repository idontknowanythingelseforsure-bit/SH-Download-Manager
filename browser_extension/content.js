/* SH Download Manager - on-page media panel.
 *
 * Shows a "Download this video/audio" button over <video> and <audio>
 * elements only, like IDM. Clicking it lists the qualities that the desktop
 * app can produce (through yt-dlp) and sends the chosen one there.
 *
 * Everything is namespaced with the shdm- prefix so page styles are untouched.
 */

(() => {
  if (window.__shdmMediaPanel) {
    return;
  }
  window.__shdmMediaPanel = true;

  const MEDIA_HOSTS = [
    "youtube.com", "youtu.be", "music.youtube.com", "m.youtube.com",
    "vimeo.com", "dailymotion.com", "soundcloud.com", "bandcamp.com",
    "facebook.com", "fb.watch", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "twitch.tv", "reddit.com", "redd.it",
    "bilibili.com", "rumble.com", "odysee.com", "streamable.com",
    "ok.ru", "vk.com", "bitchute.com", "lbry.tv"
  ];

  const state = {
    panel: null,
    panelFor: null,
    panelAnchor: null,
    panelHideTimer: 0,
    buttons: new Map(),          // media element -> button
    lastHref: location.href,
    busy: false
  };

  function mediaUrl() {
    return location.href;
  }

  function isMediaPage(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, "").toLowerCase();
      return MEDIA_HOSTS.some((h) => host === h || host.endsWith("." + h));
    } catch (_) {
      return false;
    }
  }

  function formatSize(bytes) {
    if (!bytes || bytes <= 0) {
      return "";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
  }

  function sendMessage(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (reply) => {
          const error = chrome.runtime.lastError;
          if (error) {
            resolve({ ok: false, error: String(error.message || error) });
          } else {
            resolve(reply || { ok: false, error: "No response" });
          }
        });
      } catch (error) {
        resolve({ ok: false, error: String(error) });
      }
    });
  }

  // ---------- styling ----------

  const style = document.createElement("style");
  style.textContent = `
    .shdm-media-btn {
      position: fixed;
      z-index: 2147483400;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 5px 9px;
      border: 1px solid #3c3c3c;
      border-radius: 4px;
      background: #1e1e1e;
      color: #e0e0e0;
      font: 12px/1.2 "Segoe UI", Arial, sans-serif;
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(0,0,0,.5);
      opacity: 0;
      pointer-events: none;
      transition: opacity .12s ease;
    }
    .shdm-media-btn.shdm-visible {
      opacity: .92;
      pointer-events: auto;
    }
    .shdm-media-btn:hover {
      opacity: 1;
      border-color: #007acc;
      background: #252526;
    }
    .shdm-media-btn b {
      color: #4fc3f7;
      font-weight: 600;
    }
    .shdm-panel {
      position: fixed;
      z-index: 2147483500;
      width: 310px;
      max-height: 340px;
      overflow-y: auto;
      padding: 6px 0;
      border: 1px solid #3c3c3c;
      border-radius: 5px;
      background: #1e1e1e;
      color: #e0e0e0;
      font: 12px/1.3 "Segoe UI", Arial, sans-serif;
      box-shadow: 0 8px 26px rgba(0,0,0,.65);
    }
    .shdm-panel-head {
      padding: 4px 12px 8px;
      color: #9d9d9d;
      font-size: 11px;
      border-bottom: 1px solid #2f2f2f;
      margin-bottom: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .shdm-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      cursor: pointer;
      white-space: nowrap;
    }
    .shdm-row:hover {
      background: #007acc;
      color: #fff;
    }
    .shdm-row .shdm-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #4fc3f7;
      flex: none;
    }
    .shdm-row.shdm-audio .shdm-dot {
      background: #ce9178;
    }
    .shdm-row .shdm-label {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .shdm-row .shdm-size {
      color: #9d9d9d;
      font-size: 11px;
    }
    .shdm-row:hover .shdm-size {
      color: #e8e8e8;
    }
    .shdm-empty {
      padding: 10px 12px;
      color: #9d9d9d;
    }
  `;
  (document.head || document.documentElement).appendChild(style);

  // ---------- panel ----------

  function hidePanel() {
    if (state.panel) {
      state.panel.remove();
      state.panel = null;
      state.panelFor = null;
      state.panelAnchor = null;
    }
  }

  function buildPanel(anchor, title, owner) {
    hidePanel();
    const panel = document.createElement("div");
    panel.className = "shdm-panel";

    const head = document.createElement("div");
    head.className = "shdm-panel-head";
    head.textContent = title || "Download this media";
    panel.appendChild(head);

    document.documentElement.appendChild(panel);
    state.panel = panel;
    state.panelFor = owner || anchor;
    state.panelAnchor = anchor;
    placePanel(anchor);
    return panel;
  }

  function placePanel(anchor) {
    const panel = state.panel;
    if (!panel) {
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const width = panel.offsetWidth || 310;
    const height = panel.offsetHeight || 200;
    // Align the panel's left edge under the button (image request: "bring it under it")
    let left = rect.left;
    if (left + width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - width - 8);
    }
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - height - 6);
    }
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(top)}px`;
  }

  function addRow(panel, { label, size, audio, onClick }) {
    const row = document.createElement("div");
    row.className = "shdm-row" + (audio ? " shdm-audio" : "");
    const dot = document.createElement("span");
    dot.className = "shdm-dot";
    const text = document.createElement("span");
    text.className = "shdm-label";
    text.textContent = label;
    row.append(dot, text);
    if (size) {
      const sizeEl = document.createElement("span");
      sizeEl.className = "shdm-size";
      sizeEl.textContent = size;
      row.appendChild(sizeEl);
    }
    row.addEventListener("click", onClick);
    panel.appendChild(row);
    return row;
  }

  function addDone(panel, message) {
    const done = document.createElement("div");
    done.className = "shdm-empty";
    done.textContent = message;
    panel.appendChild(done);
  }

  async function openPanel(element) {
    clearTimeout(state.panelHideTimer);
    if (state.panel && state.panelFor === element) {
      hidePanel();
      return;
    }
    const pageUrl = mediaUrl();
    const src = element.currentSrc || element.src || "";
    const tag = element.tagName.toLowerCase();
    const anchor = state.buttons.get(element) || element;
    const panel = buildPanel(anchor, isMediaPage(pageUrl) ? document.title : pageUrl, element);

    const loading = document.createElement("div");
    loading.className = "shdm-empty";
    loading.textContent = "Looking up available qualities...";
    panel.appendChild(loading);

    if (isMediaPage(pageUrl)) {
      const reply = await sendMessage({ type: "shdm-formats", url: pageUrl });
      loading.remove();
      if (!state.panel || state.panel !== panel) {
        return;
      }
      if (!reply.ok) {
        addDone(panel, `Not available: ${reply.error || "desktop app is not running"}`);
        return;
      }
      const formats = reply.formats || [];
      if (!formats.length) {
        addDone(panel, "No downloadable formats found.");
        return;
      }
      formats.forEach((item) => {
        addRow(panel, {
          label: item.label || item.selector,
          size: formatSize(item.size),
          audio: item.kind === "audio",
          onClick: () => startDownload(pageUrl, item, panel)
        });
      });
      placePanel(anchor);
      return;
    }

    loading.remove();
    if (/^https?:/i.test(src)) {
      addRow(panel, {
        label: `Download ${tag === "audio" ? "audio" : "video"} file`,
        size: "",
        audio: tag === "audio",
        onClick: () => startDownload(src, null, panel)
      });
    } else {
      addDone(panel, "This player uses a stream that the app cannot fetch directly.");
    }
    placePanel(anchor);
  }

  async function startDownload(url, item, panel) {
    if (state.busy) {
      return;
    }
    state.busy = true;
    const reply = await sendMessage({
      type: "shdm-download",
      url,
      format: item ? item.selector : "",
      kind: item ? item.kind : "video",
      referer: location.href
    });
    state.busy = false;
    if (!state.panel || state.panel !== panel) {
      return;
    }
    panel.textContent = "";
    const head = document.createElement("div");
    head.className = "shdm-panel-head";
    head.textContent = reply.ok ? "Added to SH Download Manager" : "Could not add download";
    panel.appendChild(head);
    addDone(panel, reply.ok ? "Check the app window for progress." : String(reply.error || ""));
    placePanel(state.panelAnchor || panel);
  }

  // ---------- overlay buttons ----------

  function ensureButton(element) {
    if (!element.isConnected) {
      return;
    }
    let button = state.buttons.get(element);
    if (!button) {
      button = document.createElement("div");
      button.className = "shdm-media-btn";
      button.innerHTML = "<b>&#8681;</b><span>Download this media</span>";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openPanel(element);
      });
      document.documentElement.appendChild(button);
      state.buttons.set(element, button);
    }
    return button;
  }

  function positionButton(element, button) {
    const rect = element.getBoundingClientRect();
    const visible = rect.width >= 220 && rect.height >= 100 &&
      rect.bottom > 0 && rect.top < window.innerHeight &&
      getComputedStyle(element).visibility !== "hidden";
    if (!visible) {
      button.classList.remove("shdm-visible");
      return;
    }
    button.classList.add("shdm-visible");
    const label = element.tagName.toLowerCase() === "audio" ? "Download this audio" : "Download this video";
    const span = button.querySelector("span");
    if (span && span.textContent !== label) {
      span.textContent = label;
    }
    button.style.left = `${Math.round(Math.max(8, rect.left + 8))}px`;
    button.style.top = `${Math.round(Math.min(window.innerHeight - 34, rect.top + 8))}px`;
  }

  function scan() {
    const media = Array.from(document.querySelectorAll("video, audio"));
    const seen = new Set(media);
    media.forEach((element) => {
      const button = ensureButton(element);
      if (button) {
        positionButton(element, button);
      }
    });
    state.buttons.forEach((button, element) => {
      if (!seen.has(element) || !element.isConnected) {
        button.remove();
        state.buttons.delete(element);
        if (state.panelFor === element) {
          hidePanel();
        }
      }
    });
  }

  let scheduled = false;
  function scheduleScan() {
    if (scheduled) {
      return;
    }
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      scan();
    });
  }

  function reposition() {
    state.buttons.forEach((button, element) => {
      if (element.isConnected) {
        positionButton(element, button);
      }
    });
    if (state.panel && state.panelAnchor && state.panelAnchor.isConnected) {
      placePanel(state.panelAnchor);
    }
  }

  window.addEventListener("scroll", reposition, true);
  window.addEventListener("resize", reposition, true);

  // The panel is click-to-open and stays open until the user closes it,
  // clicks elsewhere, or presses Escape (no hover auto-hide).
  document.addEventListener("click", (event) => {
    if (!state.panel) {
      return;
    }
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (state.panel.contains(target)) {
      return;
    }
    if (target.closest(".shdm-media-btn")) {
      return;                       // let the button toggle itself
    }
    hidePanel();
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.panel) {
      hidePanel();
    }
  }, true);

  new MutationObserver(scheduleScan).observe(document.documentElement, {
    childList: true,
    subtree: true
  });

  // YouTube and other single-page apps swap the player without reloading.
  setInterval(() => {
    if (location.href !== state.lastHref) {
      state.lastHref = location.href;
      hidePanel();
      scheduleScan();
    }
  }, 1200);

  document.addEventListener("yt-navigate-finish", () => {
    hidePanel();
    scheduleScan();
  }, true);

  scan();
  setTimeout(scan, 800);
})();
