// Run with Electron against the packaged resources directory, not with Node.
const assert = require('node:assert/strict');
const path = require('node:path');
const { app, nativeImage, Tray } = require('electron');

app.whenReady().then(() => {
  const resources = path.resolve(process.argv[2]);
  const { loadTrayIcon } = require(path.join(resources, 'app/electron/desktop-icons.js'));
  const image = loadTrayIcon(nativeImage, path.join(resources, 'icons'));
  assert.equal(image.isEmpty(), false);
  if (process.platform === 'darwin') {
    assert.deepEqual(image.getSize(), { width: 18, height: 18 });
    assert.equal(image.isTemplateImage(), true);
    assert.ok(image.getScaleFactors().includes(2), 'Retina tray representation is missing');
    const dockIcon = nativeImage.createFromPath(path.join(resources, 'icons/icon.png'));
    assert.equal(dockIcon.isEmpty(), false);
    assert.ok(dockIcon.getSize().width >= 256, 'Dock icon must retain application resolution');
    app.dock.setIcon(dockIcon);
  }
  const tray = new Tray(image);
  console.log(JSON.stringify({ platform: process.platform, tray: image.getSize(), scales: image.getScaleFactors(), template: image.isTemplateImage() }));
  tray.destroy();
  app.quit();
}).catch((error) => {
  console.error(error);
  app.exit(1);
});
