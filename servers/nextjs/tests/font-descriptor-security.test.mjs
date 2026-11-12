import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
let directory, bundle, fonts;

test.before(async () => {
  directory = await mkdtemp(path.join(tmpdir(), "presenton-font-descriptors-"));
  bundle = path.join(directory, "fonts.cjs");
  await build({ entryPoints: [path.resolve("components/slide-editor/text/local-fonts.ts")], outfile: bundle, bundle: true, platform: "node", format: "cjs", logLevel: "silent" });
  fonts = require(bundle);
});
test.after(async () => { await rm(directory, { recursive: true, force: true }); });

test("font descriptors reject unterminated escaped runs without exponential backtracking", () => {
  const probe = spawnSync(process.execPath, ["-e", `
    const {ensureLocalFontsForDescriptors,ensureTemplateFontsForDescriptors}=require(process.argv[1]);
    for(const descriptor of ['400 16px "'+String.raw\`\\!\`.repeat(1000),'400 16px "'+String.raw\`\\!\`.repeat(1000)+'\\nrest']) {
      ensureLocalFontsForDescriptors([descriptor]);
      ensureTemplateFontsForDescriptors([descriptor],[{family:'Control',sourceUrl:'/control.ttf'}]);
    }
  `, bundle], { encoding: "utf8", timeout: 2000 });
  assert.equal(probe.status, 0, String(probe.error || probe.stderr));
});

test("font descriptors bound repeated escaped quote starts after a split family", () => {
  const probe = spawnSync(process.execPath, ["-e", `
    const fonts=require(process.argv[1]);
    const descriptor='400 16px "'+String.fromCharCode(92,34).repeat(100000);
    fonts.ensureLocalFontsForDescriptors([descriptor]);
    fonts.ensureTemplateFontsForDescriptors([descriptor],[{family:'Control',sourceUrl:'/control.ttf'}]);
  `, bundle], { encoding: "utf8", timeout: 2000 });
  assert.equal(probe.status, 0, String(probe.error || probe.stderr));
});

test("template descriptors preserve quoted, escaped and Unicode font families", async () => {
  const previousDocument = globalThis.document;
  const addedStyles = [];
  globalThis.document = {
    querySelector: () => null,
    createElement: () => ({ setAttribute() {}, textContent: "" }),
    head: { appendChild: (style) => addedStyles.push(style) },
  };
  try {
    for (const family of ['Manrope', 'Font "quoted"', 'Path\\Face', '日本語 Font']) {
      const escaped = family.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      const loads = fonts.ensureTemplateFontsForDescriptors([`italic 700 16px "${escaped}"`], [{ family, sourceUrl: "/control.ttf" }]);
      assert.equal(loads.length, 1, family);
      await Promise.all(loads);
    }
    assert.equal(addedStyles.length, 4);
  } finally { globalThis.document = previousDocument; }
});
