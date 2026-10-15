const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the browser's real shop-plan helpers without starting authentication or polling.
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

const A = { name: "a", stuid: "1001" };
const B = { name: "b", stuid: "1002" };
const C = { name: "c", stuid: "1003" };

function setAccounts(accounts) {
  ui.accounts = accounts;
  vm.runInContext("config = { accounts, shop_exchange: { plans: [] } }", ui);
}

function options(html) {
  return Array.from(html.matchAll(/<option\b([^>]*)>/g), ([, attributes]) => {
    const attr = (name) => attributes.match(new RegExp(`${name}="([^"]*)"`))?.[1];
    return {
      value: attr("value"),
      stuid: attr("data-stuid"),
      selected: /\sselected(?:\s|$)/.test(attributes),
    };
  });
}

function accountSelect(html) {
  const select = html.match(
    /<select data-shop-plan-field="account_index"[^>]*>([\s\S]*?)<\/select>/,
  );
  return options(select[1]);
}

test("a plan resolves its account by stuid after the account list shifted", () => {
  const accounts = [B, C];
  assert.equal(ui.shopPlanAccountIndex({ account_index: 2, account_stuid: C.stuid }, accounts), 1);
  assert.equal(ui.shopPlanAccountIndex({ account_index: 0, account_stuid: A.stuid }, accounts), -1);
  assert.equal(ui.shopPlanAccountIndex({ account_index: 1 }, accounts), 1);
});

test("the plan row selects the plan's own account, not the stale index", () => {
  setAccounts([B, C]);
  const html = ui.shopPlanRow({ goods_id: "g", account_index: 2, account_stuid: C.stuid }, 0);
  const selected = accountSelect(html).filter((option) => option.selected);
  assert.deepEqual(selected.map((option) => option.stuid), [C.stuid]);
});

test("a deleted account shows a placeholder instead of falling back to the first account", () => {
  setAccounts([B, C]);
  const html = ui.shopPlanRow({ goods_id: "g", account_index: 0, account_stuid: A.stuid }, 0);
  const selected = accountSelect(html).filter((option) => option.selected);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].value, "");
  assert.equal(selected[0].stuid, A.stuid);
  assert.match(html, /账号已删除/);
});

function planRow(fields) {
  return {
    querySelectorAll: () =>
      fields.map(({ key, ...field }) => ({ dataset: { shopPlanField: key }, ...field })),
  };
}

function collectPlans(rows) {
  ui.document.querySelectorAll = (selector) => (selector === "[data-shop-plan]" ? rows : []);
  return ui.collectShopExchange().plans;
}

test("collecting reads the account identity from the rendered option", () => {
  // The rows were rendered for [A, B, C]; A was removed afterwards, so the DOM is stale.
  setAccounts([B, C]);
  const plans = collectPlans([
    planRow([
      { key: "goods_id", value: "g-c" },
      { key: "account_index", value: "2", selectedOptions: [{ dataset: { stuid: C.stuid } }] },
    ]),
    planRow([
      { key: "goods_id", value: "g-b" },
      { key: "account_index", value: "1", selectedOptions: [{ dataset: { stuid: B.stuid } }] },
    ]),
  ]);
  // Arrays built inside the VM have a different prototype, so compare plain JSON.
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        plans.map(({ goods_id, account_index, account_stuid }) => [goods_id, account_index, account_stuid]),
      ),
    ),
    [["g-c", 1, C.stuid], ["g-b", 0, B.stuid]],
  );
});

test("collecting keeps a deleted account's binding instead of adopting another account", () => {
  setAccounts([B, C]);
  const [plan] = collectPlans([
    planRow([
      { key: "goods_id", value: "g-a" },
      { key: "account_index", value: "", selectedOptions: [{ dataset: { stuid: A.stuid } }] },
    ]),
  ]);
  assert.equal(plan.account_stuid, A.stuid);
});

test("new plans are bound to the chosen account's stuid", async () => {
  setAccounts([B, C]);
  ui.api = async (url) => (url.includes("device-fp") ? { device_fp: "fp" } : {});
  vm.runInContext("api = globalThis.api", ui);
  const plan = await ui.buildShopPlan({ goods_id: "g", goods_name: "商品" }, 1);
  assert.equal(plan.account_index, 1);
  assert.equal(plan.account_stuid, C.stuid);
});
