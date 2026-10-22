const path = require('path');

function loadTrayIcon(nativeImage, iconsDirectory, platform = process.platform) {
  const filename = platform === 'darwin'
    ? 'tray-iconTemplate.png'
    : platform === 'win32' ? 'tray-icon.ico' : 'tray-icon.png';
  const image = nativeImage.createFromPath(path.join(iconsDirectory, filename));
  if (image.isEmpty()) {
    throw new Error(`Unable to load tray icon: ${filename}`);
  }
  if (platform === 'darwin') {
    image.setTemplateImage(true);
  }
  return image;
}

module.exports = { loadTrayIcon };
