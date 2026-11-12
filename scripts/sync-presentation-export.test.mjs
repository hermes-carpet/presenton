import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const {
  getRuntimePlatformArch,
  validateSharpRuntime,
  validateExportRuntime,
} = require("./sync-presentation-export.cjs");

for (const variant of ["linux", "windows-npm-cli", "windows-cmd"]) {
  test(`export install keeps cache paths out of shell arguments (${variant})`, () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-cache & %TEMP% !name! (test)-"));
    try {
      const scripts = path.join(root, "scripts");
      fs.mkdirSync(scripts);
      fs.writeFileSync(path.join(scripts, "run-presentation-export.mjs"), "// fixture");
      fs.writeFileSync(path.join(scripts, "export-permissions.cjs"), "// fixture");
      const npmCli = path.join(root, "npm-cli.js");
      fs.writeFileSync(npmCli, "// fixture");
      const calls = [];
      const module = { exports: {} };
      const env = { CONTROL: "preserved", NPM_CONFIG_CACHE: "inherited-cache", NpM_cOnFiG_cAcHe: "another-cache", ComSpec: "cmd.exe", npm_execpath: variant === "windows-npm-cli" ? npmCli : "" };
      const source = fs.readFileSync(new URL("./sync-presentation-export.cjs", import.meta.url), "utf8");
      vm.runInNewContext(source + "\nmodule.exports.installRuntime = installRuntime;", {
        __dirname: scripts, module, console,
        process: { argv: [], platform: variant === "linux" ? "linux" : "win32", execPath: process.execPath, env },
        require(name) { return name === "child_process" ? { execFileSync(...args) { calls.push(args); } } : require(name); },
      });
      module.exports.installRuntime("v1.0.51", path.join(root, "export-core.tgz"));
      assert.equal(calls.length, 1);
      const [command, args, options] = calls[0];
      assert.equal(options.env.npm_config_cache, path.join(root, ".cache", "presentation-export", "npm"));
      assert.equal(options.env.CONTROL, "preserved");
      assert.deepEqual(Object.keys(options.env).filter((name) => name.toLowerCase() === "npm_config_cache"), ["npm_config_cache"]);
      assert.equal(options.cwd, path.join(root, "presentation-export"));
      assert.equal(args.includes("--cache"), false);
      assert.equal(args.some((arg) => arg.includes(root)), variant === "windows-npm-cli");
      if (variant === "windows-cmd") {
        assert.equal(command, "cmd.exe");
        assert.deepEqual(Array.from(args), ["/d", "/s", "/c", "npm", "install", "--omit=dev", "--ignore-scripts", "--no-package-lock", "--no-fund", "--no-audit"]);
      }
    } finally { fs.rmSync(root, { recursive: true, force: true }); }
  });
}

test("npm reads metacharacter cache paths literally from its environment", { skip: !process.env.npm_execpath }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-npm-env-"));
  try {
    for (const name of ["cache with spaces & symbols", "%TEMP% !name! (cache)"]) {
      const cache = path.join(root, name);
      const result = execFileSync(process.execPath, [process.env.npm_execpath, "config", "get", "cache"], {
        encoding: "utf8", env: { ...process.env, npm_config_cache: cache },
      });
      assert.equal(result.trim(), cache);
      if (process.platform === "win32") {
        const fallback = execFileSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm", "config", "get", "cache"], {
          encoding: "utf8", env: { ...process.env, npm_config_cache: cache },
        });
        assert.equal(fallback.trim(), cache);
      }
    }
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

function writePackage(root, packageName, packageJson) {
  const packageDirectory = path.join(
    root,
    "node_modules",
    ...packageName.split("/"),
  );
  fs.mkdirSync(packageDirectory, { recursive: true });
  fs.writeFileSync(
    path.join(packageDirectory, "package.json"),
    `${JSON.stringify(packageJson)}\n`,
  );
}

function makeSharpRuntime() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-sharp-runtime-"));
  writePackage(root, "sharp", {
    version: "0.35.5",
    optionalDependencies: {
      "@img/sharp-darwin-arm64": "0.35.5",
      "@img/sharp-libvips-darwin-arm64": "1.3.4",
    },
  });
  return root;
}

test("getRuntimePlatformArch distinguishes glibc and musl Linux", () => {
  const glibcReport = {
    getReport: () => ({ header: { glibcVersionRuntime: "2.39" } }),
  };
  const muslReport = { getReport: () => ({ header: {} }) };

  assert.equal(getRuntimePlatformArch("linux", "x64", glibcReport), "linux-x64");
  assert.equal(
    getRuntimePlatformArch("linux", "arm64", muslReport),
    "linuxmusl-arm64",
  );
  assert.equal(getRuntimePlatformArch("darwin", "arm64"), "darwin-arm64");
});

test("validateSharpRuntime rejects a runtime missing the target native package", () => {
  const root = makeSharpRuntime();
  try {
    assert.deepEqual(validateSharpRuntime(root, "darwin-arm64"), {
      ok: false,
      reason: "Missing @img/sharp-darwin-arm64 0.35.5 for darwin-arm64.",
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("validateSharpRuntime rejects native packages from another Sharp release", () => {
  const root = makeSharpRuntime();
  try {
    writePackage(root, "@img/sharp-darwin-arm64", { version: "0.34.5" });
    assert.deepEqual(validateSharpRuntime(root, "darwin-arm64"), {
      ok: false,
      reason: "Expected @img/sharp-darwin-arm64 0.35.5, found 0.34.5.",
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("validateSharpRuntime accepts matching native and libvips packages", () => {
  const root = makeSharpRuntime();
  try {
    writePackage(root, "@img/sharp-darwin-arm64", { version: "0.35.5" });
    writePackage(root, "@img/sharp-libvips-darwin-arm64", { version: "1.3.4" });
    assert.deepEqual(validateSharpRuntime(root, "darwin-arm64"), {
      ok: true,
      sharpVersion: "0.35.5",
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("reusing a pinned export package refreshes its application runner", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-export-runner-"));
  try {
    const runtime = path.join(root, "presentation-export");
    const scripts = path.join(root, "scripts");
    fs.mkdirSync(scripts, { recursive: true });
    fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ presentationExportVersion: "v1.0.34" }));
    fs.copyFileSync(fileURLToPath(new URL("./sync-presentation-export.cjs", import.meta.url)), path.join(scripts, "sync-presentation-export.cjs"));
    fs.copyFileSync(fileURLToPath(new URL("./export-permissions.cjs", import.meta.url)), path.join(scripts, "export-permissions.cjs"));
    const runner = fs.readFileSync(fileURLToPath(new URL("./run-presentation-export.mjs", import.meta.url)), "utf8");
    fs.writeFileSync(path.join(scripts, "run-presentation-export.mjs"), runner);
    writePackage(runtime, "@presenton/export-core", { version: "1.0.34" });
    writePackage(runtime, "katex", { version: "0.18.10" });
    const platform = getRuntimePlatformArch();
    const native = `@img/sharp-${platform}`;
    writePackage(runtime, "sharp", { version: "0.35.5", optionalDependencies: { [native]: "0.35.5" } });
    writePackage(runtime, native, { version: "0.35.5" });
    fs.writeFileSync(path.join(runtime, "runner.mjs"), "// stale runner");
    fs.writeFileSync(path.join(runtime, "presenton-export-version.json"), JSON.stringify({ version: "v1.0.34", package: "presenton-export-core-opensource-1.0.34.tgz" }));
    const output = execFileSync(process.execPath, [path.join(scripts, "sync-presentation-export.cjs")], { encoding: "utf8", env: { ...process.env, EXPORT_RUNTIME_VERSION: "" } });
    assert.match(output, /Using export-core 1\.0\.34/);
    assert.equal(fs.readFileSync(path.join(runtime, "runner.mjs"), "utf8"), runner);
    assert.ok(fs.existsSync(path.join(runtime, "export-permissions.cjs")));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test("export validation rejects coherent vulnerable versions and nested engine copies", () => {
  const root = makeSharpRuntime();
  try {
    writePackage(root, "@img/sharp-darwin-arm64", { version: "0.35.5" });
    writePackage(root, "@img/sharp-libvips-darwin-arm64", { version: "1.3.4" });
    writePackage(root, "@presenton/export-core", { version: "1.0.51" });
    writePackage(root, "katex", { version: "0.16.47" });
    assert.match(validateExportRuntime(root, "darwin-arm64").reason, /KaTeX.*security update/);
    writePackage(root, "katex", { version: "0.18.10" });
    assert.equal(validateExportRuntime(root, "darwin-arm64").ok, true);
    const engine = path.join(root, "node_modules/@presenton/export-core");
    writePackage(engine, "katex", { version: "0.16.47" });
    assert.match(validateExportRuntime(root, "darwin-arm64").reason, /KaTeX.*security update/);
    fs.rmSync(path.join(engine, "node_modules"), { recursive: true });
    writePackage(engine, "sharp", { version: "0.35.3", optionalDependencies: { "@img/sharp-darwin-arm64": "0.35.3" } });
    writePackage(engine, "@img/sharp-darwin-arm64", { version: "0.35.3" });
    assert.match(validateExportRuntime(root, "darwin-arm64").reason, /Sharp.*security update/);
    writePackage(engine, "sharp", { version: "0.35.5-beta.1" });
    assert.equal(validateExportRuntime(root, "darwin-arm64").ok, false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
