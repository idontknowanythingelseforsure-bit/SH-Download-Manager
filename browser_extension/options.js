const DEFAULT_PORT = 53991;
const portInput = document.getElementById("port");
const status = document.getElementById("status");

chrome.storage.sync.get({ apiPort: DEFAULT_PORT }).then((stored) => {
  portInput.value = stored.apiPort;
});

document.getElementById("save").addEventListener("click", async () => {
  const port = Number(portInput.value);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    status.textContent = "Enter a port between 1024 and 65535.";
    status.className = "bad";
    return;
  }
  await chrome.storage.sync.set({ apiPort: port });
  status.textContent = `Saved port ${port}.`;
  status.className = "good";
});

document.getElementById("test").addEventListener("click", () => {
  status.textContent = "Testing…";
  status.className = "";
  chrome.runtime.sendMessage({ type: "shdm-test" }, (reply) => {
    if (reply && reply.ok) {
      const name = (reply.body && reply.body.name) || "SH Download Manager";
      status.textContent = `Connected to ${name}.`;
      status.className = "good";
    } else {
      status.textContent = "Could not connect. Is the app running and the API enabled?";
      status.className = "bad";
    }
  });
});
