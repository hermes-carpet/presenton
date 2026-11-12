const fs = require("node:fs");
const path = require("node:path");

function exportReaderGid() {
  if (typeof process.getgid !== "function") return undefined;
  if (process.getuid() === 0) {
    try {
      const groups = fs.readFileSync("/etc/group", "utf8").split("\n").map((line) => line.split(":"));
      for (const name of ["www-data", "nginx"]) {
        const group = groups.find((entry) => entry[0] === name && /^\d+$/.test(entry[2] || ""));
        if (group) return Number(group[2]);
      }
    } catch { /* Keep the runtime's group when no system group database exists. */ }
  }
  return process.getgid();
}

function permissions(filename, directory, gid, legacy = false) {
  try {
    const stat = fs.lstatSync(filename);
    if (stat.isSymbolicLink()) return;
    // A restricted process may write into an existing shared-volume directory
    // without owning that directory. Keep migration best-effort for it only.
    legacy ||= directory && typeof process.getuid === "function"
      && process.getuid() !== 0 && stat.uid !== process.getuid();
    if (gid !== undefined && stat.gid !== gid) fs.chownSync(filename, stat.uid, gid);
    const mode = directory
      ? (legacy ? stat.mode & 0o7770 : 0o750 | (stat.mode & 0o3000))
      : 0o640;
    fs.chmodSync(filename, mode);
  } catch (error) {
    if (legacy && ["EPERM", "EACCES"].includes(error.code)) return;
    throw error;
  }
}

function finalizeExportPermissions(outputPath, roots = [], { recursive = false, legacy = false } = {}) {
  if (fs.lstatSync(outputPath).isSymbolicLink()) return;
  const resolved = fs.realpathSync(outputPath);
  const directory = fs.statSync(resolved).isDirectory() ? resolved : path.dirname(resolved);
  const configured = roots.filter(Boolean);
  const candidates = configured.map((root) => fs.existsSync(root) ? fs.realpathSync(root) : path.resolve(root)).filter((root) => {
    const relative = path.relative(root, directory);
    return !path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`);
  });
  if (configured.length && !candidates.length) throw new Error("Export artifact is outside managed storage");
  const boundary = candidates.sort((a, b) => b.length - a.length)[0] || directory;
  const gid = exportReaderGid();
  for (let current = directory; ; current = path.dirname(current)) {
    permissions(current, true, gid, true);
    if (current === boundary || path.dirname(current) === current) break;
  }
  function visit(current) {
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) return;
    permissions(current, stat.isDirectory(), gid, legacy);
    if (stat.isDirectory()) for (const entry of fs.readdirSync(current)) visit(path.join(current, entry));
  }
  if (recursive) visit(directory);
  else {
    permissions(directory, true, gid, legacy);
    if (fs.statSync(resolved).isFile()) permissions(resolved, false, gid, legacy);
  }
}

module.exports = { exportReaderGid, finalizeExportPermissions };
