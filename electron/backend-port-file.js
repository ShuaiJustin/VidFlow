const fs = require('node:fs');

function removeBackendPortFileAtPath(portFilePath, logger = console) {
  if (!fs.existsSync(portFilePath)) {
    return;
  }

  try {
    fs.unlinkSync(portFilePath);
    logger.log(`Removed stale backend port file: ${portFilePath}`);
  } catch (error) {
    logger.warn(`Failed to remove backend port file ${portFilePath}: ${error.message}`);
  }
}

module.exports = { removeBackendPortFileAtPath };
