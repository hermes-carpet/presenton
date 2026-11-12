const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { execFileSync, spawnSync } = require("node:child_process");

const CRITICAL_PACKAGES = ["@llamaindex/liteparse", "sharp", "zod", "axios", "form-data", "follow-redirects"];
const MARKER = ".presenton-document-lock.sha256";

function criticalVersionsMatch(directory, lock) {
  return Object.entries(lock.packages || {}).every(([relative, entry]) => {
    if (!CRITICAL_PACKAGES.some((name) => relative.endsWith(`node_modules/${name}`))) return true;
    try {
      return JSON.parse(fs.readFileSync(path.join(directory, relative, "package.json"), "utf8")).version === entry.version;
    } catch { return false; }
  });
}

function canLoadRuntime(directory) {
  return spawnSync(process.execPath, ["--input-type=module", "-e", "await import('@llamaindex/liteparse'); await import('sharp');"], {
    cwd: directory, env: process.env, encoding: "utf8",
  }).status === 0;
}

function syncDocumentDependencies({
  root = path.join(__dirname, ".."),
  install = (directory) => execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", [
    "ci", "--include=optional", "--omit=dev", "--prefer-offline", "--no-fund", "--no-audit",
  ], { cwd: directory, stdio: "inherit" }),
  canLoad = canLoadRuntime,
} = {}) {
  const packageBytes = fs.readFileSync(path.join(root, "package.json"));
  const lockBytes = fs.readFileSync(path.join(root, "package-lock.json"));
  const manifest = JSON.parse(packageBytes);
  const lock = JSON.parse(lockBytes);
  const fingerprint = createHash("sha256").update(lockBytes)
    .update(JSON.stringify({ dependencies: manifest.dependencies, overrides: manifest.overrides }))
    .update(`${process.platform}:${process.arch}:${process.versions.modules}`).digest("hex");
  const runtime = path.join(root, "document-extraction-liteparse");
  fs.mkdirSync(runtime, { recursive: true });
  fs.writeFileSync(path.join(runtime, "package.json"), packageBytes);
  fs.writeFileSync(path.join(runtime, "package-lock.json"), lockBytes);
  fs.copyFileSync(path.join(root, "electron/resources/document-extraction/liteparse_runner.mjs"), path.join(runtime, "liteparse_runner.mjs"));

  for (const directory of [root, runtime]) {
    const marker = path.join(directory, "node_modules", MARKER);
    let current = false;
    try { current = fs.readFileSync(marker, "utf8") === fingerprint; } catch {}
    if (current && criticalVersionsMatch(directory, lock) && canLoad(directory)) continue;
    fs.rmSync(marker, { force: true });
    install(directory);
    if (!criticalVersionsMatch(directory, lock) || !canLoad(directory)) {
      throw new Error(`Document dependencies do not match the reviewed lockfile: ${directory}`);
    }
    fs.writeFileSync(marker, fingerprint);
  }
  return fingerprint;
}

if (require.main === module) {
  try { syncDocumentDependencies(); console.log("[document-runtime] Locked dependencies ready."); }
  catch (error) { console.error(`[document-runtime] ${error.message}`); process.exitCode = 1; }
}
module.exports = { syncDocumentDependencies };
