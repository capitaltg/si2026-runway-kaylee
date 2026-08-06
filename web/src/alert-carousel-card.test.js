import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { build } from "esbuild";
import React from "react";

async function loadAlertCarouselCard() {
  const sourceUrl = new URL("./components/AlertCarouselCard.jsx", import.meta.url);
  const source = await readFile(sourceUrl, "utf8");
  const result = await build({
    stdin: {
      contents: source,
      loader: "jsx",
      resolveDir: new URL("./components/", import.meta.url).pathname,
      sourcefile: "AlertCarouselCard.jsx",
    },
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
  });
  const bundled = result.outputFiles[0].text;
  return import(`data:text/javascript;base64,${Buffer.from(bundled).toString("base64")}`);
}

test("keeps the pager outside the changing keyed alert root so its focused control survives", async () => {
  const { default: AlertCarouselCard } = await loadAlertCarouselCard();
  const renderCard = (key) =>
    AlertCarouselCard({
      index: key === "first" ? 0 : 1,
      total: 2,
      onPrevious() {},
      onNext() {},
      children: React.createElement("div", { key, style: {} }, key),
    });

  const first = renderCard("first");
  const second = renderCard("second");

  assert.equal(first.key, second.key);
  assert.equal(first.type, second.type);
  assert.equal(React.Children.toArray(first.props.children)[1].key, ".$alert-carousel-controls");
});
