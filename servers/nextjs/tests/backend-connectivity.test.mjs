import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { build } from "esbuild";

let connectivity;
let temporaryDirectory;

test.before(async () => {
  temporaryDirectory = await mkdtemp(
    path.join(tmpdir(), "presenton-backend-connectivity-"),
  );
  const entryFile = path.join(temporaryDirectory, "entry.ts");
  const outputFile = path.join(temporaryDirectory, "bundle.mjs");
  await writeFile(
    entryFile,
    [
      `export { assertBackendReachable, BackendConnectionError, normalizeBackendAssetUrls, resolveBackendAssetUrl, getApiUrl, getFastAPIUrl } from ${JSON.stringify(path.resolve("utils/api.ts"))};`,
      `export { clearOllamaModelsCache, isOllamaModelAvailable } from ${JSON.stringify(path.resolve("utils/providerUtils.ts"))};`,
    ].join("\n"),
  );
  await build({
    entryPoints: [entryFile],
    outfile: outputFile,
    bundle: true,
    platform: "node",
    format: "esm",
    tsconfig: path.resolve("tsconfig.json"),
    logLevel: "silent",
  });
  connectivity = await import(
    `${pathToFileURL(outputFile).href}?cache=${Date.now()}`
  );
});

test.after(async () => {
  if (temporaryDirectory) {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});

test("query parameters cannot redirect authenticated API requests to an unconfigured origin", async () => {
  const oldWindow = globalThis.window;
  const oldFetch = globalThis.fetch;
  try {
    for (const electron of [false, true]) {
      for (const origin of ["https://collector.example", "https://backend.example.evil.test", "https://backend.example@collector.example", "http://backend.example", "https://backend.example:8443"]) {
        globalThis.window = {
          location: { origin: "https://presenton.example", search: `?fastapiUrl=${encodeURIComponent(origin)}` },
          env: { NEXT_PUBLIC_FAST_API: "https://backend.example" },
          ...(electron ? { electron: {} } : {}),
        };
        const calls = [];
        globalThis.fetch = async (url) => {
          calls.push(url);
          return Response.json({ configured: true });
        };
        await connectivity.assertBackendReachable();
        assert.equal(connectivity.getFastAPIUrl(), electron ? "https://backend.example" : "https://presenton.example");
        assert.deepEqual(calls, [electron ? "https://backend.example/api/v1/auth/status" : "/api/v1/auth/status"]);
      }
    }
  } finally { globalThis.window = oldWindow; globalThis.fetch = oldFetch; }
});

test("trusted backend queries preserve Electron, export and custom backend connections", () => {
  const oldWindow = globalThis.window;
  try {
    for (const backend of ["http://127.0.0.1:40000", "http://localhost:5001", "https://custom-backend.example"]) {
      for (const electron of [false, true]) {
        globalThis.window = {
          location: { origin: "http://127.0.0.1:40001", search: `?fastapiUrl=${encodeURIComponent(backend + "/")}` },
          env: { NEXT_PUBLIC_FAST_API: backend },
          ...(electron ? { electron: {} } : {}),
        };
        assert.equal(connectivity.getApiUrl("/api/v1/ppt/presentation"), `${backend}/api/v1/ppt/presentation`);
      }
    }
    globalThis.window = { location: { origin: "https://presenton.example", search: "" }, env: { NEXT_PUBLIC_FAST_API: "http://fastapi:8000" } };
    assert.equal(connectivity.getApiUrl("/api/v1/auth/status"), "/api/v1/auth/status");
    globalThis.window.location.search = "?fastapiUrl=https%3A%2F%2Fpresenton.example";
    assert.equal(connectivity.getApiUrl("/api/v1/auth/status"), "https://presenton.example/api/v1/auth/status");
  } finally { globalThis.window = oldWindow; }
});

test("Next.js public font URLs are not rewritten as backend assets", () => {
  const fontUrl =
    "/vendor/fonts/sans_serif/poppins/Poppins-Regular.ttf";

  assert.equal(connectivity.resolveBackendAssetUrl(fontUrl), fontUrl);
});

test("font URLs remain unchanged when normalizing a template response", () => {
  const fontUrl =
    "/vendor/fonts/sans_serif/poppins/Poppins-Regular.ttf";
  const response = {
    fonts: {
      Poppins: fontUrl,
    },
  };

  assert.deepEqual(connectivity.normalizeBackendAssetUrls(response), response);
});

test("backend reachability accepts a JSON health response", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ configured: true, authenticated: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

  try {
    await connectivity.assertBackendReachable();
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("backend reachability turns an HTML frontend response into a clear connection error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("<!DOCTYPE html><title>Not Found</title>", {
      status: 404,
      headers: { "Content-Type": "text/html" },
    });

  try {
    await assert.rejects(
      connectivity.assertBackendReachable(),
      (error) => {
        assert.ok(error instanceof connectivity.BackendConnectionError);
        assert.doesNotMatch(error.message, /<!DOCTYPE|Unexpected token/);
        assert.match(error.message, /FastAPI backend/i);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("backend reachability turns a failed fetch into a clear connection error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };

  try {
    await assert.rejects(
      connectivity.assertBackendReachable(),
      connectivity.BackendConnectionError,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Ollama availability only returns false for a valid model list", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify([{ name: "llama3.2:3b" }]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

  try {
    connectivity.clearOllamaModelsCache();
    assert.equal(
      await connectivity.isOllamaModelAvailable(
        "missing-model",
        "http://ollama.test:11434",
      ),
      false,
    );
  } finally {
    connectivity.clearOllamaModelsCache();
    globalThis.fetch = originalFetch;
  }
});

test("Ollama availability does not classify HTML as a missing model", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("<!DOCTYPE html><title>Not Found</title>", {
      status: 404,
      headers: { "Content-Type": "text/html" },
    });

  try {
    connectivity.clearOllamaModelsCache();
    await assert.rejects(
      connectivity.isOllamaModelAvailable(
        "llama3.2:3b",
        "http://ollama-html.test:11434",
      ),
      connectivity.BackendConnectionError,
    );
  } finally {
    connectivity.clearOllamaModelsCache();
    globalThis.fetch = originalFetch;
  }
});
