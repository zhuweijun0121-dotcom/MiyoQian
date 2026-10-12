const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the browser's real form helpers without starting authentication or polling.
const source = fs.readFileSync(
  path.join(__dirname, "../miyouqian/webui/app.js"),
  "utf8",
);
assert.match(source, /\ncheckAuthAndInit\(\);\s*$/);
const ui = vm.createContext({
  document: {
    querySelectorAll: () => [],
    getElementById: () => ({ addEventListener() {} }),
    addEventListener() {},
  },
});
vm.runInContext(source.replace(/\ncheckAuthAndInit\(\);\s*$/, "\n"), ui);

function collect(html, enabled = true) {
  const fields = Array.from(html.matchAll(/<input\b([^>]*)>/g), ([, attributes]) => {
    const attr = (name) => attributes.match(new RegExp(`${name}="([^"]*)"`))?.[1];
    return {
      dataset: { pushField: attr("data-push-field") },
      type: attr("type"),
      tagName: "INPUT",
      value: attr("value") || "",
      checked: /\schecked(?:\s|$)/.test(attributes),
    };
  });
  return ui.cleanPushChannel(ui.collectPushChannel({
    dataset: { pushProvider: "wecombot" },
    querySelector: () => ({ checked: enabled }),
    querySelectorAll: () => fields,
  }));
}

test("existing WeCom channels default to Markdown and show an unchecked toggle", () => {
  const channel = { provider: "wecombot", enable: true, webhook: "https://example.invalid/bot" };
  assert.equal(ui.cleanPushChannel(channel).plain_text, false);
  const html = ui.pushChannelFields("wecombot", channel);
  assert.match(html, /纯文本消息（兼容微信）/);
  assert.equal(collect(html).plain_text, false);
});

for (const plainText of [true, false]) {
  test(`WeCom plain_text=${plainText} survives editing, saving and reloading`, () => {
    const channel = {
      provider: "wecombot", enable: true,
      webhook: "https://example.invalid/bot", plain_text: plainText,
    };
    const saved = collect(ui.pushChannelFields("wecombot", channel));
    assert.equal(saved.plain_text, plainText);
    assert.equal(saved.webhook, channel.webhook);
    const reloaded = JSON.parse(JSON.stringify(saved));
    assert.equal(collect(ui.pushChannelFields("wecombot", reloaded)).plain_text, plainText);
    // A subsequent unrelated save collects string-valued hidden fields.
    assert.equal(collect(ui.pushChannelHiddenFields(reloaded)).plain_text, plainText);
  });

  test(`disabling a configured channel retains plain_text=${plainText}`, () => {
    const channel = {
      provider: "wecombot", enable: false,
      webhook: "https://example.invalid/bot", plain_text: plainText,
    };
    const saved = collect(ui.pushChannelHiddenFields(channel), false);
    assert.equal(saved.enable, false);
    assert.equal(saved.plain_text, plainText);
    assert.equal(ui.shouldSavePushChannel(saved), true);
  });
}

test("unchecking the plain-text toggle restores Markdown", () => {
  const html = ui.pushChannelFields("wecombot", {
    provider: "wecombot", enable: true,
    webhook: "https://example.invalid/bot", plain_text: true,
  });
  const saved = collect(html.replace(/\schecked(?=\s*\/?>)/g, ""));
  assert.equal(saved.plain_text, false);
  assert.equal(collect(ui.pushChannelHiddenFields(saved)).plain_text, false);
});

test("the default false flag does not retain an empty disabled channel", () => {
  const saved = collect(ui.pushChannelFields("wecombot", ui.emptyPushChannel("wecombot")), false);
  assert.equal(saved.plain_text, false);
  assert.equal(ui.shouldSavePushChannel(saved), false);
});

test("an explicitly enabled plain-text preference is retained before adding a webhook", () => {
  const saved = collect(ui.pushChannelFields("wecombot", {
    provider: "wecombot", enable: false, plain_text: true,
  }), false);
  assert.equal(saved.plain_text, true);
  assert.equal(ui.shouldSavePushChannel(saved), true);
});
