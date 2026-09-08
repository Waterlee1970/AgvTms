const path = 'c:/codex/AgvTms-2.4/frontend-clean/node_modules/playwright';
const { chromium } = require(path);

(async () => {
  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox', '--disable-gpu-sandbox']
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const consoleMsgs = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') consoleMsgs.push(`[${m.type()}] ${m.text().slice(0, 300)}`); });
  page.on('pageerror', e => consoleMsgs.push(`[pageerror] ${String(e).slice(0, 300)}`));
  page.on('requestfailed', r => consoleMsgs.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));

  try {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle', timeout: 60000 });
    // 打开 3D 数字孪生菜单
    const menuItem = page.locator('.ant-menu-item', { hasText: '3D 数字孪生' }).first();
    await menuItem.click({ timeout: 15000 });
    await page.waitForTimeout(4000);

    // 读取统计卡 + 3D HUD
    const statsBefore = await page.locator('.ant-statistic-title').allInnerTexts().catch(() => []);
    const hudBefore = await page.locator('text=AGVs:').allInnerTexts().catch(() => []);
    console.log('STATS_CARDS:', JSON.stringify(statsBefore));

    await page.screenshot({ path: 'c:/codex/AgvTms-2.4/frontend-clean/dt_before.png', fullPage: false });

    // 点击 启动模拟
    const startBtn = page.locator('button', { hasText: '启动模拟' }).first();
    const btnVisible = await startBtn.isVisible().catch(() => false);
    console.log('START_BTN_VISIBLE:', btnVisible);
    if (btnVisible) await startBtn.click();
    await page.waitForTimeout(6000);

    await page.screenshot({ path: 'c:/codex/AgvTms-2.4/frontend-clean/dt_after.png', fullPage: false });

    const hudAfter = await page.locator('div', { hasText: '3D Digital Twin' }).allInnerTexts().catch(() => []);
    const pauseText = await page.locator('button', { hasText: '暂停' }).count();
    console.log('HUD_AFTER:', JSON.stringify(hudAfter));
    console.log('PAUSE_BTN_COUNT:', pauseText);
    console.log('CONSOLE_ERRORS:', JSON.stringify(consoleMsgs, null, 2));
  } catch (err) {
    console.log('SCRIPT_ERROR:', String(err).slice(0, 500));
    console.log('CONSOLE_ERRORS_SO_FAR:', JSON.stringify(consoleMsgs, null, 2));
    await page.screenshot({ path: 'c:/codex/AgvTms-2.4/frontend-clean/dt_error.png' }).catch(() => {});
  } finally {
    await browser.close();
  }
})();
