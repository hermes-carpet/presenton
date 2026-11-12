import crypto from "crypto";
import fs from "fs";
import path from "path";

function assertTrustedParents(directory: string, uid: number | undefined): void {
  if (uid === undefined) return; // Windows roots must come from per-user app data.
  const parents: Array<{ directory: string; stat: fs.Stats }> = [];
  for (let current = directory; ; current = path.dirname(current)) {
    const stat = fs.statSync(current);
    parents.push({ directory: current, stat });
    if (path.dirname(current) === current) break;
  }
  let privateBoundary = false;
  for (const { directory: current, stat } of parents.reverse()) {
    if (!stat.isDirectory() || (stat.uid !== uid && stat.uid !== 0)) {
      throw new Error(`Untrusted application directory: ${current}`);
    }
    // Sticky system temp roots protect an owned child against other users.
    if (!privateBoundary && (stat.mode & 0o022) !== 0 && !(stat.uid === 0 && (stat.mode & 0o1000) !== 0)) {
      throw new Error(`Application directory has a writable parent: ${current}`);
    }
    if (stat.uid === uid && (stat.mode & 0o077) === 0) privateBoundary = true;
  }
}

export function ensurePrivateDirectory(directory: string): string {
  const uid = typeof process.getuid === "function" ? process.getuid() : undefined;
  const absolute = path.resolve(directory);
  let existing = path.dirname(absolute);
  while (!fs.existsSync(existing)) {
    const next = path.dirname(existing);
    if (next === existing) throw new Error(`Application storage parent is unavailable: ${directory}`);
    existing = next;
  }
  const parent = fs.realpathSync(existing);
  assertTrustedParents(parent, uid);
  const candidate = path.resolve(parent, path.relative(existing, absolute));
  fs.mkdirSync(candidate, { recursive: true, mode: 0o700 });
  const entry = fs.lstatSync(candidate);
  if (uid !== undefined && entry.uid !== uid) {
    throw new Error(`Application directory belongs to another user: ${candidate}`);
  }
  if (uid === undefined && entry.isSymbolicLink()) {
    throw new Error(`Application directory must not be a link: ${candidate}`);
  }
  const resolved = fs.realpathSync(candidate);
  const stat = fs.statSync(resolved);
  if (!stat.isDirectory() || (uid !== undefined && stat.uid !== uid)) {
    throw new Error(`Invalid private application directory: ${resolved}`);
  }
  assertTrustedParents(path.dirname(resolved), uid);
  if (uid !== undefined) fs.chmodSync(resolved, 0o700);
  fs.accessSync(resolved, fs.constants.W_OK);
  return resolved;
}

export function writePrivateFileSync(filename: string, content: string | Uint8Array): void {
  const directory = ensurePrivateDirectory(path.dirname(filename));
  const target = path.join(directory, path.basename(filename));
  const temporary = path.join(directory, `.${path.basename(filename)}.${crypto.randomUUID()}.tmp`);
  try {
    const descriptor = fs.openSync(temporary, "wx", 0o600);
    try {
      fs.writeFileSync(descriptor, content);
      fs.fsyncSync(descriptor);
    } finally { fs.closeSync(descriptor); }
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, { force: true }); }
}
