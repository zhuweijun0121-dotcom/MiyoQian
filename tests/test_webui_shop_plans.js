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

function setShopGoods(goods, status = "") {
  ui.goods = goods;
  vm.runInContext(`shopGoods = goods; shopFilterStatus = ${JSON.stringify(status)}; shopCurrentPage = 1`, ui);
}

test("the status filter separates sold-out goods from those exchangeable now", () => {
  setShopGoods([
    { goods_id: "g0", sold_out: true },
    { goods_id: "g1", display_status: "online" },
    { goods_id: "g2", display_status: "always" },
    { goods_id: "g3", display_status: "upcoming" },
  ]);
  const ids = (status) => {
    vm.runInContext(`shopFilterStatus = ${JSON.stringify(status)}`, ui);
    return ui.getFilteredGoods().map((good) => good.goods_id);
  };
  assert.deepEqual(ids(""), ["g0", "g1", "g2", "g3"]);
  assert.deepEqual(ids("sold_out"), ["g0"]);
  assert.deepEqual(ids("available"), ["g1", "g2"]);
});

test("filtered goods keep their index in the full list and short results hide the pager", () => {
  const originalDocument = ui.document;
  const elements = {};
  ui.document = {
    ...originalDocument,
    getElementById: (id) => (elements[id] ??= { innerHTML: "", style: {}, addEventListener() {} }),
  };
  const originalCard = ui.shopGoodCard;
  ui.shopGoodCard = (good, index) => `[${good.goods_id}@${index}]`;
  try {
    const goods = Array.from({ length: 6 }, (_, i) => ({
      goods_id: `g${i}`,
      ...(i % 2 ? { sold_out: true } : { display_status: "online" }),
    }));
    setShopGoods(goods, "available");
    ui.renderShopGoods();
    assert.equal(elements.shopGoods.innerHTML, "[g0@0][g2@2][g4@4]");
    assert.equal(elements.shopPagination.style.display, "none");

    setShopGoods(goods.map((good) => ({ ...good, sold_out: false, display_status: "online" })), "sold_out");
    ui.renderShopGoods();
    assert.match(elements.shopGoods.innerHTML, /当前筛选条件下没有商品/);
  } finally {
    ui.document = originalDocument;
    ui.shopGoodCard = originalCard;
    setShopGoods([]);
  }
});
