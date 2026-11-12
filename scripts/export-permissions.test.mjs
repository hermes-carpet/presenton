import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import test from "node:test";
import permissions from "./export-permissions.cjs";

const { finalizeExportPermissions, exportReaderGid } = permissions;
const posix = { skip: process.platform === "win32" };
const mode = (filename) => fs.statSync(filename).mode & 0o777;

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-export-permissions-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test("exports and nested conversion sidecars retain proxy access without other-user access", posix, (t) => {
  const root = fixture(t);
  fs.chmodSync(root, 0o755);
  const session = path.join(root, "pptx-to-html", "session");
  fs.mkdirSync(path.join(session, "assets"), { recursive: true, mode: 0o755 });
  for (const name of ["index.html", "assets/image.png", "assets/font.woff2"]) fs.writeFileSync(path.join(session, name), name, { mode: 0o644 });
  finalizeExportPermissions(path.join(session, "index.html"), [root], { recursive: true });
  for (const name of ["", "pptx-to-html", "pptx-to-html/session", "pptx-to-html/session/assets"]) assert.equal(mode(path.join(root, name)), 0o750);
  for (const name of ["index.html", "assets/image.png", "assets/font.woff2"]) {
    assert.equal(mode(path.join(session, name)), 0o640);
    assert.equal(fs.statSync(path.join(session, name)).gid, exportReaderGid());
    assert.equal(fs.readFileSync(path.join(session, name), "utf8"), name);
  }
});

test("private and group-writable managed ancestors keep their access contracts", posix, (t) => {
  const root = fixture(t);
  fs.chmodSync(root, 0o2770);
  const file = path.join(root, "exports", "deck.pdf");
  fs.mkdirSync(path.dirname(file)); fs.writeFileSync(file, "pdf");
  const originalMode = fs.statSync(root).mode & 0o7777;
  finalizeExportPermissions(file, [root]);
  assert.equal(fs.statSync(root).mode & 0o7777, originalMode);
  assert.equal(mode(file), 0o640);
  const privateRoot = path.join(root, "private");
  fs.mkdirSync(path.join(privateRoot, "exports"), { recursive: true }); fs.chmodSync(privateRoot, 0o700);
  const privateFile = path.join(privateRoot, "exports", "deck.pdf"); fs.writeFileSync(privateFile, "pdf");
  finalizeExportPermissions(privateFile, [privateRoot]);
  assert.equal(mode(privateRoot), 0o700);
});

test("permission normalization neither follows artifact symlinks nor crosses storage boundaries", posix, (t) => {
  const root = fixture(t);
  const managed = path.join(root, "managed"); fs.mkdirSync(managed);
  const external = path.join(root, "external"); fs.mkdirSync(external); fs.chmodSync(external, 0o755);
  const victim = path.join(external, "secret"); fs.writeFileSync(victim, "secret", { mode: 0o600 });
  fs.symlinkSync(external, path.join(managed, "link"));
  finalizeExportPermissions(managed, [managed], { recursive: true });
  finalizeExportPermissions(path.join(managed, "link"), [managed], { recursive: true });
  assert.equal(mode(victim), 0o600); assert.equal(mode(external), 0o755);
  assert.throws(() => finalizeExportPermissions(victim, [managed]), /outside managed storage/);
});

test("server startup preserves config and recovery data with owner-only modes", posix, (t) => {
  const root = fixture(t);
  const scripts = path.join(root, "scripts"); fs.mkdirSync(scripts);
  for (const name of ["user-config-env.cjs", "presenton-terminal-banner.mjs", "export-permissions.cjs", "run-node-script.mjs"]) fs.copyFileSync(new URL(name, import.meta.url), path.join(scripts, name));
  const data = path.join(root, "data"); fs.mkdirSync(data);
  const primary = path.join(data, "userConfig.json"); const backup = `${primary}.bak`;
  fs.writeFileSync(primary, JSON.stringify({ OPENAI_API_KEY: "old-secret" }), { mode: 0o644 });
  fs.writeFileSync(backup, JSON.stringify({ OPENAI_API_KEY: "recovery-secret" }), { mode: 0o644 });
  const source = fs.readFileSync(new URL("../start.js", import.meta.url), "utf8").split("// Setup node_modules for development")[0];
  const script = path.join(root, "startup.mjs");
  fs.writeFileSync(script, source);
  execFileSync(process.execPath, [script], { env: { ...process.env, APP_DATA_DIRECTORY: data } });
  assert.equal(mode(primary), 0o600); assert.equal(mode(backup), 0o600);
  fs.appendFileSync(script, '\nwriteUserConfig({ OPENAI_API_KEY: "new-secret" });\n');
  execFileSync(process.execPath, [script], { env: { ...process.env, APP_DATA_DIRECTORY: data } });
  assert.equal(mode(primary), 0o600); assert.equal(mode(backup), 0o600);
  assert.equal(JSON.parse(fs.readFileSync(primary)).OPENAI_API_KEY, "new-secret");
  assert.equal(JSON.parse(fs.readFileSync(backup)).OPENAI_API_KEY, "old-secret");
  assert.equal(fs.readdirSync(data).some((name) => name.endsWith(".tmp")), false);
});
