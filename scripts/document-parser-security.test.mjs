import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const fromParser = createRequire(require.resolve("@llamaindex/liteparse/package.json"));
const fromAxios = createRequire(fromParser.resolve("axios"));
const { http } = fromAxios("follow-redirects");

test("OCR HTTP dependencies strip array-form credentials across origins", async () => {
  let received;
  let original;
  const destination = createServer((request, response) => {
    received = request.headers;
    response.end("ok");
  });
  destination.listen(0);
  await once(destination, "listening");
  const source = createServer((request, response) => {
    original = request.headers;
    response.writeHead(302, { location: `http://localhost:${destination.address().port}/result` });
    response.end();
  });
  source.listen(0, "127.0.0.1");
  await once(source, "listening");
  try {
    const status = await new Promise((resolve, reject) => {
      http.get({
        hostname: "127.0.0.1",
        port: source.address().port,
        path: "/ocr",
        headers: [
          "Host", `127.0.0.1:${source.address().port}`,
          "Authorization", "Bearer test-canary",
          "Cookie", "canary=test",
        ],
      }, (response) => {
        response.resume();
        response.on("end", () => resolve(response.statusCode));
        response.on("error", reject);
      }).on("error", reject);
    });
    assert.equal(status, 200);
    assert.equal(original.authorization, "Bearer test-canary");
    assert.equal(original.cookie, "canary=test");
    assert.equal(received.authorization, undefined);
    assert.equal(received.cookie, undefined);
  } finally {
    await Promise.all([new Promise((resolve) => source.close(resolve)), new Promise((resolve) => destination.close(resolve))]);
  }
});
