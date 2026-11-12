import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, copyFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runNodeScript } from "./run-node-script.mjs";

test("startup Node helpers preserve literal paths, arguments, environment and exit errors", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "presenton node & scripts "));
  try {
    const script = path.join(directory, "sync & helper; script.cjs");
    const output = path.join(directory, "output.json");
    writeFileSync(script, 'require("node:fs").writeFileSync(process.argv[2], JSON.stringify({args: process.argv.slice(3), cwd: process.cwd(), control: process.env.PRESENTON_NODE_HELPER_CONTROL}));');
    const args = ["spaces in an argument", "$(echo unsafe)", "one;two", "a&b"];
    await runNodeScript(script, [output, ...args], { cwd: directory, stdio: "ignore", env: { ...process.env, PRESENTON_NODE_HELPER_CONTROL: "control" } });
    const result = JSON.parse(readFileSync(output, "utf8"));
    assert.deepEqual(result.args, args);
    assert.equal(result.control, "control");
    assert.equal(realpathSync(result.cwd), realpathSync(directory));
    const failing = path.join(directory, "failure.cjs");
    writeFileSync(failing, "process.exit(7);");
    await assert.rejects(runNodeScript(failing, [], { stdio: "ignore" }), /exited with code: 7/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("export credentials are scoped to the two desktop origins without installing a hostname cookie", () => {
  const directory = mkdtempSync(path.join(tmpdir(), "presenton-desktop-export-"));
  try {
    const packageDirectory = path.join(directory, "node_modules", "@presenton", "export-core");
    mkdirSync(packageDirectory, { recursive: true });
    writeFileSync(path.join(packageDirectory, "package.json"), JSON.stringify({ type: "module", exports: "./index.mjs" }));
    writeFileSync(path.join(packageDirectory, "index.mjs"), "export async function runTask(task, options) { return JSON.stringify({ task, options }); }");
    const runner = path.join(directory, "runner.mjs");
    copyFileSync(fileURLToPath(new URL("./run-presentation-export.mjs", import.meta.url)), runner);
    copyFileSync(fileURLToPath(new URL("./export-permissions.cjs", import.meta.url)), path.join(directory, "export-permissions.cjs"));
    const origins = ["http://127.0.0.1:40001", "http://127.0.0.1:40000"];
    const token = "a".repeat(64);
    const taskFile = path.join(directory, "task.json");
    writeFileSync(taskFile, JSON.stringify({ type: "export", url: "https://external.example/presentation", cookieHeader: `presenton_desktop_session=${token}` }));
    execFileSync(process.execPath, [runner, taskFile], { env: {
      ...process.env, PRESENTON_ELECTRON: "true", PRESENTON_DESKTOP_TOKEN: token,
      PRESENTON_DESKTOP_ORIGINS: origins.join(","),
    } });
    const response = JSON.parse(readFileSync(path.join(directory, "task.response.json"), "utf8"));
    const { task, options } = JSON.parse(response.data);
    assert.equal("cookieHeader" in task, false);
    assert.deepEqual(options.urlConfigs, origins.map((url) => ({
      url, match: "origin", headers: { "x-presenton-desktop-token": token },
    })));
    assert.equal(options.urlConfigs.some((config) => "cookies" in config), false);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
