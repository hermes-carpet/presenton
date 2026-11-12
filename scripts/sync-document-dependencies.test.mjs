import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

const { syncDocumentDependencies } = createRequire(import.meta.url)("./sync-document-dependencies.cjs");
const markerName = ".presenton-document-lock.sha256";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-document-lock-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ dependencies: { sharp: "^0.35.5" } }));
  fs.writeFileSync(path.join(root, "package-lock.json"), JSON.stringify({ packages: { "node_modules/sharp": { version: "0.35.5" } } }));
  const runner = path.join(root, "electron/resources/document-extraction");
  fs.mkdirSync(runner, { recursive: true });
  fs.writeFileSync(path.join(runner, "liteparse_runner.mjs"), "// current runner");
  const calls = [];
  const install = (directory) => {
    calls.push(directory);
    const packageDir = path.join(directory, "node_modules/sharp");
    fs.mkdirSync(packageDir, { recursive: true });
    fs.writeFileSync(path.join(packageDir, "package.json"), JSON.stringify({ version: "0.35.5" }));
  };
  return { root, calls, install, canLoad: () => true };
}

test("replaces an old loadable parser volume and reuses the locked result", (t) => {
  const options = fixture(t);
  const runtime = path.join(options.root, "document-extraction-liteparse");
  const old = path.join(runtime, "node_modules/sharp");
  fs.mkdirSync(old, { recursive: true });
  fs.writeFileSync(path.join(old, "package.json"), '{"version":"0.34.5"}');
  syncDocumentDependencies(options);
  assert.deepEqual(options.calls, [options.root, runtime]);
  assert.equal(JSON.parse(fs.readFileSync(path.join(old, "package.json"))).version, "0.35.5");
  assert.equal(fs.readFileSync(path.join(runtime, "liteparse_runner.mjs"), "utf8"), "// current runner");
  options.calls.length = 0;
  syncDocumentDependencies(options);
  assert.deepEqual(options.calls, []);
});

test("repairs downgraded packages even with a current marker", (t) => {
  const options = fixture(t);
  syncDocumentDependencies(options);
  options.calls.length = 0;
  const runtime = path.join(options.root, "document-extraction-liteparse");
  fs.writeFileSync(path.join(runtime, "node_modules/sharp/package.json"), '{"version":"0.34.5"}');
  syncDocumentDependencies(options);
  assert.deepEqual(options.calls, [runtime]);
});

test("failed installation cannot retain a trusted cache marker", (t) => {
  const options = fixture(t);
  syncDocumentDependencies(options);
  options.canLoad = () => false;
  options.install = () => { throw new Error("offline install failed"); };
  assert.throws(() => syncDocumentDependencies(options), /offline install failed/);
  assert.equal(fs.existsSync(path.join(options.root, "node_modules", markerName)), false);
});
