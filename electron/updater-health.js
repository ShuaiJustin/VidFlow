async function confirmHealthyUpdate({ updater, mainWindow, windowReady, backendReady, backendPort, checkBackendHealth, isWindowReady = () => windowReady, isBackendReady = () => backendReady }) {
  if (!updater || !windowReady || !backendReady || !backendPort || !mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed() || mainWindow.webContents.isLoadingMainFrame?.()) return false;
  if (!(await checkBackendHealth(backendPort))) return false;
  if (!isWindowReady() || !isBackendReady() || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed() || mainWindow.webContents.isLoadingMainFrame?.()) return false;
  return updater.confirmUpdateHealth();
}

module.exports = { confirmHealthyUpdate };
