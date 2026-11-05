/* SH Download Manager browser bridge.
 * Sends a single link to the local desktop app. No remote service is used.
 */

const DEFAULT_PORT = 53991;

async function apiPort() {
  try {
    const stored = await chrome.storage.sync.get({ apiPort: DEFAULT_PORT });
    const port = Number(stored.apiPort);
    return Number.isInteger(port) && port > 0 ? port : DEFAULT_PORT;
  } catch (_) {
    return DEFAULT_PORT;
  }
}

function badge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3000);
}

async function cookiesFor(url) {
  try {
    const list = await chrome.cookies.getAll({ url });
    return list.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (_) {
    return "";
  }
}

async function send(payload) {
  const port = await apiPort();
  const response = await fetch(`http://127.0.0.1:${port}/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, start: true })
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return body;
}

async function formats(url) {
  const port = await apiPort();
  const response = await fetch(`http://127.0.0.1:${port}/formats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url })
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return body.formats || [];
}

async function download(url, referer) {
  if (!url || !/^https?:/i.test(url)) {
    badge("!", "#b42318");
    return;
  }
  try {
    await send({
      url,
      referer: referer || "",
      cookies: await cookiesFor(url)
    });
    badge("OK", "#12805c");
  } catch (error) {
    console.warn("SH Download Manager:", error);
    badge("!", "#b42318");
  }
}

function refreshMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "shdm-download",
      title: "Download with SH Download Manager",
      contexts: ["link", "video", "audio", "image"]
    });
    chrome.contextMenus.create({
      id: "shdm-page",
      title: "Send this page to SH Download Manager",
      contexts: ["page"]
    });
    chrome.contextMenus.create({
      id: "shdm-all",
      title: "Download every link on page",
      contexts: ["page", "all"]
    });
  });
}

chrome.runtime.onInstalled.addListener(refreshMenus);
chrome.runtime.onStartup.addListener(refreshMenus);

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const referer = info.pageUrl || (tab && tab.url) || "";
  if (info.menuItemId === "shdm-page") {
    download(referer, referer); return;
  }
  if (info.menuItemId === "shdm-all") {
    chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: () => Array.from(document.querySelectorAll("a[href],video[src],audio[src],img[src]")).map(el => el.href || el.src || el.getAttribute("src") || "").filter(url => url && url.match(/^https?:\/\//)).filter((v,i,a)=>a.indexOf(v)===i)
    }, (results) => {
      const urls = (results && results[0] && results[0].result) || [];
      urls.forEach(url => download(url, referer));
    });
    return;
  }
  const url = info.linkUrl || info.srcUrl || info.frameUrl || referer;
  const isMedia = info.mediaType === "video" || info.mediaType === "audio";
  if (isMedia && (!url || /^blob:/i.test(url))) {
    download(referer, referer);
    return;
  }
  download(url, referer);
});

chrome.action.onClicked.addListener((tab) => {
  if (tab && tab.url) {
    download(tab.url, tab.url);
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "shdm-test") {
    apiPort()
      .then((port) => fetch(`http://127.0.0.1:${port}/ping`))
      .then((response) => response.json())
      .then((body) => sendResponse({ ok: true, body }))
      .catch((error) => sendResponse({ ok: false, error: "Desktop app not running (start SH Download Manager)" }));
    return true;
  }
  if (message && message.type === "shdm-formats") {
    formats(String(message.url || ""))
      .then((list) => sendResponse({ ok: true, formats: list }))
      .catch((error) => sendResponse({ ok: false, error: "Desktop app not running (start SH Download Manager)" }));
    return true;
  }
  if (message && message.type === "shdm-download") {
    const url = String(message.url || "");
    (async () => {
      await send({
        url,
        format: message.format || "",
        kind: message.kind === "audio" ? "audio" : "video",
        referer: message.referer || "",
        cookies: await cookiesFor(url)
      });
      badge("OK", "#12805c");
    })()
      .then(() => sendResponse({ ok: true }))
      .catch((error) => {
        console.warn("SH Download Manager:", error);
        badge("!", "#b42318");
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  return false;
});
