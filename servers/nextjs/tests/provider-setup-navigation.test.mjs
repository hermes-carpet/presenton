import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

const compiled = await build({
  entryPoints: ["lib/provider-setup-navigation.ts"], bundle: true,
  platform: "node", format: "esm", write: false, logLevel: "silent",
});
const { providerSetupNavigationAction: action } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString("base64")}`
);
const base = "https://slides.example/settings";

for (const href of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " ja\tva\nscript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "http://[invalid"]) {
  for (const target of [null, "_blank"]) {
    test(`blocks executable or malformed navigation ${JSON.stringify(href)} target=${target}`, () => {
      assert.equal(action(href, target, base), "block");
    });
  }
}
test("preserves setup warning for ordinary navigation and allows documentation tabs", () => {
  for (const href of ["/dashboard", "https://docs.example/setup", "../upload", "#section"]) {
    assert.equal(action(href, null, base), "warn");
    assert.equal(action(href, "_blank", base), "ignore");
  }
  assert.equal(action(null, null, base), "ignore");
  assert.equal(action("", null, base), "ignore");
  assert.equal(action("#", null, base), "ignore");
});
