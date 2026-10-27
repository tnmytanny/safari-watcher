const ids = ["date", "at", "gates", "shift", "armed"];
const $ = (id) => document.getElementById(id);
const msg = (t) => ($("msg").textContent = t);

chrome.storage.local.get("cfg").then(({ cfg = {} }) => {
  for (const id of ids) if (cfg[id] !== undefined) $(id)[id === "armed" ? "checked" : "value"] = cfg[id];
});
for (const id of ids) $(id).addEventListener("change", () => {
  const cfg = Object.fromEntries(ids.map((k) => [k, k === "armed" ? $(k).checked : $(k).value]));
  // a new arm starts a fresh attempt in every tab
  chrome.storage.local.set({ cfg: { ...cfg, armId: cfg.armed ? Date.now() : 0 } }).then(() => msg("Saved"));
});

async function send(type) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    msg(await chrome.tabs.sendMessage(tab.id, { type }));
  } catch {
    msg("Open a booking page on mponline / mahaforest first");
  }
}
$("save").onclick = () => send("save");
$("fill").onclick = () => send("fill");
$("clear").onclick = async () => {
  const all = await chrome.storage.local.get(null);
  await chrome.storage.local.remove(Object.keys(all).filter((k) => k.startsWith("form:")));
  msg("Saved forms cleared");
};
