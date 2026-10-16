"use strict";
const { contextBridge, ipcRenderer } = require("electron");
// The renderer gets operations, never Node, cookies, private keys, or file paths.
contextBridge.exposeInMainWorld(
  "multirpaDesktop",
  Object.freeze({
    request: (path, body, method) =>
      ipcRenderer.invoke("multirpa:request", { path, body, method }),
    account: () => ipcRenderer.invoke("multirpa:account"),
    login: (email, password) =>
      ipcRenderer.invoke("multirpa:login", { email, password }),
    logout: () => ipcRenderer.invoke("multirpa:logout"),
    startTrial: () => ipcRenderer.invoke("multirpa:start-trial"),
    activate: () => ipcRenderer.invoke("multirpa:activate"),
    openAccount: () => ipcRenderer.invoke("multirpa:open-account"),
    openWorkspace: () => ipcRenderer.invoke("multirpa:open-workspace"),
    importFile: () => ipcRenderer.invoke("multirpa:import-file"),
    exportFlow: (flowId) => ipcRenderer.invoke("multirpa:export-flow", flowId),
  }),
);
