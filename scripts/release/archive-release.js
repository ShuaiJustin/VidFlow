const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..', '..');
const packageJsonPath = path.join(rootDir, 'package.json');
const distOutputDir = path.join(rootDir, 'dist-output');
const backendOutputDir = path.join(rootDir, 'backend', 'dist', 'VidFlow-Backend');
const frontendOutputDir = path.join(rootDir, 'frontend', 'dist');
const releasesDir = path.join(rootDir, 'releases');

function readPackageVersion() {
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
  return String(packageJson.version || '').trim();
}

function normalizeVersion(value) {
  return String(value || '').trim().replace(/^v/i, '');
}

function printUsage() {
  console.log('用法:');
  console.log('  node scripts/release/archive-release.js');
  console.log('  node scripts/release/archive-release.js --version=1.0.0');
}

function parseArgs(argv) {
  const options = {
    version: readPackageVersion()
  };

  for (const arg of argv) {
    if (!arg) {
      continue;
    }

    if (arg === '--help' || arg === '-h') {
      printUsage();
      process.exit(0);
    }

    if (arg.startsWith('--version=')) {
      options.version = normalizeVersion(arg.slice('--version='.length));
      continue;
    }

    if (!arg.startsWith('-') && !options.version) {
      options.version = normalizeVersion(arg);
      continue;
    }

    throw new Error(`未知参数: ${arg}`);
  }

  return options;
}

function assertPathExists(targetPath, description) {
  if (!fs.existsSync(targetPath)) {
    throw new Error(`${description}不存在: ${targetPath}`);
  }
}

function findInstaller(version) {
  const candidates = [
    path.join(distOutputDir, `VidFlow-Setup-${version}-x64.exe`),
    path.join(distOutputDir, `VidFlow Setup ${version}.exe`),
    path.join(distOutputDir, `VidFlow-Setup-${version}.exe`)
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  if (!fs.existsSync(distOutputDir)) {
    throw new Error(`安装包输出目录不存在: ${distOutputDir}`);
  }

  const installers = fs
    .readdirSync(distOutputDir)
    .filter((fileName) => fileName.toLowerCase().endsWith('.exe') && fileName.includes(version));

  if (installers.length === 1) {
    return path.join(distOutputDir, installers[0]);
  }

  throw new Error(`未在 ${distOutputDir} 中找到版本 ${version} 的安装包`);
}

function copyDirectory(sourceDir, targetDir) {
  fs.mkdirSync(path.dirname(targetDir), { recursive: true });
  fs.cpSync(sourceDir, targetDir, { recursive: true, force: true });
}

function formatBytes(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function getDirectorySize(directory) {
  let size = 0;

  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      size += getDirectorySize(entryPath);
    } else if (entry.isFile()) {
      size += fs.statSync(entryPath).size;
    }
  }

  return size;
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const version = normalizeVersion(options.version);

  if (!version) {
    throw new Error('必须提供版本号。');
  }

  const installerPath = findInstaller(version);
  assertPathExists(backendOutputDir, '后端输出目录');
  assertPathExists(frontendOutputDir, '前端输出目录');

  const releaseDir = path.join(releasesDir, `v${version}`);
  const targetBackendDir = path.join(releaseDir, 'VidFlow-Backend');
  const targetFrontendDir = path.join(releaseDir, 'frontend', 'dist');

  console.log('');
  console.log('========================================');
  console.log('VidFlow 发布归档工具');
  console.log('========================================');
  console.log(`版本: ${version}`);
  console.log(`目标目录: ${releaseDir}`);
  console.log('');

  fs.rmSync(releaseDir, { recursive: true, force: true });
  fs.mkdirSync(releaseDir, { recursive: true });

  const installerFileName = path.basename(installerPath);
  const targetInstallerPath = path.join(releaseDir, installerFileName);

  fs.copyFileSync(installerPath, targetInstallerPath);
  copyDirectory(backendOutputDir, targetBackendDir);
  copyDirectory(frontendOutputDir, targetFrontendDir);

  console.log(`[完成] 安装包: ${targetInstallerPath}`);
  console.log(`[完成] 后端: ${targetBackendDir}`);
  console.log(`[完成] 前端: ${targetFrontendDir}`);
  console.log('');
  console.log(`安装包大小: ${formatBytes(fs.statSync(targetInstallerPath).size)}`);
  console.log(`后端大小: ${formatBytes(getDirectorySize(targetBackendDir))}`);
  console.log(`前端大小: ${formatBytes(getDirectorySize(targetFrontendDir))}`);
}

try {
  main();
} catch (error) {
  console.error('');
  console.error(`[错误] ${error.message}`);
  process.exit(1);
}
