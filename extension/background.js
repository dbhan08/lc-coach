// Service worker. Two jobs:
//   1. Make the toolbar action click open the side panel (instead of a popup).
//   2. Nothing else for now — sidepanel.js talks to content.js directly via
//      chrome.tabs.sendMessage, so we don't need a relay.

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error("[lc-coach] setPanelBehavior failed:", err));
});
