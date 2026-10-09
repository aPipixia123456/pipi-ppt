import { test, expect } from "@playwright/test";

test("authorize, generate, edit, export and sign out", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "使用 pipiapi 登录" }).click();
  await page.getByRole("link", { name: "同意授权" }).click();
  await expect(
    page.getByRole("heading", { name: "今天，想讲一个什么故事？" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../output/playwright/studio.png",
    fullPage: true,
  });
  await page.getByLabel("演示主题").fill("绿色能源产品的市场介绍与发布计划");
  await page.getByRole("spinbutton", { name: "页数" }).fill("5");
  await page.getByRole("button", { name: "生成演示大纲" }).click();
  await expect(page.getByLabel("大纲 1", { exact: true })).toBeVisible();
  await page
    .getByLabel("大纲 1", { exact: true })
    .fill("绿色能源的新篇章 — 产品定位与发布目标");
  await page.getByRole("button", { name: "确认大纲并生成" }).click();
  await expect(page.getByRole("button", { name: "导出 PPTX" })).toBeEnabled({
    timeout: 60000,
  });
  await expect(page.getByRole("button", { name: "新增页" })).toBeVisible();
  await page.screenshot({
    path: "../../output/playwright/editor.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "导出 PPTX" }).click();
  await page.getByRole("button", { name: "生成任务" }).click();
  await expect(page.getByRole("link", { name: "下载" })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载" }).click();
  const exported = await download;
  expect(exported.suggestedFilename()).toBe("presentation.pptx");
  await exported.saveAs("../../output/playwright/pipi-ppt-demo.pptx");
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "使用 pipiapi 登录" }),
  ).toBeVisible();
});

test("built-in template preview and language switch", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "使用 pipiapi 登录" }).click();
  await page.getByRole("link", { name: "同意授权" }).click();
  await page.getByRole("button", { name: "模板中心", exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).first().click();
  await expect(page.getByRole("dialog", { name: "模板预览" })).toBeVisible();
  await page.getByRole("button", { name: "使用这个模板" }).click();
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "What story will you tell today?" }),
  ).toBeVisible();
});

test("lost submit response reuses the task and late polling preserves edits", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: "使用 pipiapi 登录" }).click();
  await page.getByRole("link", { name: "同意授权" }).click();
  await page.getByLabel("演示主题").fill("网络重试与编辑保护验收");
  await page.getByRole("spinbutton", { name: "页数" }).fill("5");
  const before = (await (await page.request.get("/api/jobs")).json()).length;
  const keys: string[] = [];
  await page.route("**/api/decks", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push(route.request().headers()["idempotency-key"]);
    const response = await route.fetch();
    if (keys.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "生成演示大纲" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "生成演示大纲" }).click();
  const outline = page.getByLabel("大纲 1", { exact: true });
  await expect(outline).toBeEnabled();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect((await (await page.request.get("/api/jobs")).json()).length).toBe(
    before + 1,
  );
  const edited = "这段编辑必须保留，不能被稍晚到达的刷新覆盖";
  let handled = false;
  const latePoll = new Promise<void>((resolve, reject) => {
    void page.route("**/api/decks/*", async (route) => {
      if (route.request().method() !== "GET" || handled)
        return route.continue();
      handled = true;
      try {
        const response = await route.fetch();
        await outline.fill(edited);
        await route.fulfill({ response });
        resolve();
      } catch (error) {
        reject(error);
      }
    });
  });
  await latePoll;
  await expect(outline).toHaveValue(edited);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByRole("button", { name: "我的作品", exact: true }).click();
});

test("administrator saves multiline model lists and controls template availability", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: "使用 pipiapi 登录" }).click();
  await page.getByRole("link", { name: "同意授权" }).click();
  await page.getByRole("button", { name: "站点管理", exact: true }).click();
  const models = page.getByLabel("已验证的文字/视觉模型，每行一个");
  await models.fill("fixture-text-vision\nfixture-extra");
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/admin") &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "保存配置" }).click();
  expect((await saved).ok()).toBeTruthy();
  const config = await (await page.request.get("/api/admin")).json();
  expect(config.policy.text_models).toEqual([
    "fixture-text-vision",
    "fixture-extra",
  ]);
  const disabled = page.waitForResponse((response) =>
    response.url().includes("/admin/templates/executive?enabled=false"),
  );
  await page.getByLabel("工作汇报 / Reports").uncheck();
  expect((await disabled).ok()).toBeTruthy();
  await page.getByRole("button", { name: "模板中心", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工作汇报", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "站点管理", exact: true }).click();
  await page.getByLabel("工作汇报 / Reports").check();
  await models.fill("fixture-text-vision");
  await page.getByRole("button", { name: "保存配置" }).click();
});
