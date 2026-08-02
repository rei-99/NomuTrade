/**
 * STP demo recorder — drives the real UI in installed Edge (no download),
 * records the page as .webm, and captures a screenshot per beat.
 *
 * Prereq: stack running (`./dev.sh sqlite` or uvicorn :8000 + vite :5173),
 * demo trades already seeded (see presentation/demo-guide.md).
 *
 * Run:  node tools/demo-recorder/record-demo.js
 * Out:  tools/demo-recorder/recordings/demo-<ts>.webm + shots/*.png
 */
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.DEMO_BASE_URL || "http://localhost:5173";
const OUT = path.join(__dirname, "recordings");
const SHOTS = path.join(OUT, "shots");
const T0 = Date.now();
const stamp = () => ((Date.now() - T0) / 1000).toFixed(1).padStart(5);
const log = [];

async function beat(page, name, shot = true) {
  const line = `[${stamp()}s] ${name}`;
  console.log(line);
  log.push(line);
  if (shot) await page.screenshot({ path: path.join(SHOTS, `${log.length.toString().padStart(2, "0")}-${name.replace(/[^a-z0-9]+/gi, "_").toLowerCase()}.png`) });
}
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function login(page, email) {
  await page.goto(`${BASE}/login`);
  await page.locator("input[type=email]").pressSequentially(email, { delay: 70 });
  await page.locator("input[type=password]").pressSequentially("demo1234", { delay: 90 });
  await pause(400);
  await page.locator("button[type=submit]").click();
}

async function placeMarketBuy(page, symbol, qty) {
  await page.locator(`.chip-symbol:has-text("${symbol}")`).first().click();
  await pause(1200); // let chart/hero re-anchor to the new symbol
  await page.locator("input.size-input").fill(String(qty));
  await pause(400);
  await page.locator("button.trade-buy").click();
  await pause(700); // confirmation card
  await page.locator(".confirm-card .btn-buy").click();
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1680, height: 1000 },
    recordVideo: { dir: OUT, size: { width: 1680, height: 1000 } },
  });
  const page = await context.newPage();

  // --- Act 1: trader login ---
  await login(page, "trader@demo.nomura");
  await page.waitForSelector(".trading-page", { timeout: 15000 });
  await pause(5000); // tape + chart ticking live
  await beat(page, "login-trader-workspace");

  // --- Act 2: live equity trade on an untouched name (WMT) ---
  await placeMarketBuy(page, "WMT", 25);
  await pause(3500); // fill toast + positions flash
  await beat(page, "buy-wmt-25-filled");

  // --- Act 3: live bond trade on an untouched name (UST2Y) ---
  await page.locator('.scope-seg .seg-btn:has-text("Bonds")').click();
  await pause(800);
  await placeMarketBuy(page, "UST2Y", 1000);
  await pause(3500);
  await beat(page, "buy-ust2y-1000-filled");

  // --- Act 4: portfolio management + risk ---
  await page.locator('nav a:has-text("Portfolios")').click();
  await page.waitForSelector("text=Desk Book 1", { timeout: 10000 });
  await pause(500);
  await page.locator('td:has-text("Desk Book 1")').first().click();
  await pause(4500); // positions, KPIs, VaR/drawdown, allocation
  await beat(page, "portfolio-detail-risk-kpis");

  // --- Act 5: trade blotter — settlement column ---
  await page.locator('nav a:has-text("Trades")').click();
  await pause(4000); // newest fills reach AFFIRMED/SETTLED
  await beat(page, "trades-settlement-column");

  // --- Act 6: sign out, ops view — governance & settlements lane ---
  await page.locator('button:has-text("Sign out")').click();
  await page.waitForSelector("input[type=email]", { timeout: 10000 });
  await login(page, "ops@demo.nomura");
  await page.waitForSelector("text=Governance", { timeout: 15000 });
  await pause(2000);
  await page.locator('nav a:has-text("Governance")').click();
  await pause(4500); // health tiles (mock badges), exceptions, recent settlements
  await beat(page, "ops-governance-settlements");

  await context.close(); // finalizes the video
  await browser.close();

  // Rename the video to something stable and dump the beat log.
  const vids = fs.readdirSync(OUT).filter((f) => f.endsWith(".webm"));
  const newest = vids.map((f) => [f, fs.statSync(path.join(OUT, f)).mtimeMs]).sort((a, b) => b[1] - a[1])[0][0];
  const finalName = `demo-${new Date().toISOString().replace(/[:.]/g, "-")}.webm`;
  fs.renameSync(path.join(OUT, newest), path.join(OUT, finalName));
  fs.writeFileSync(path.join(OUT, `${finalName.replace(/\.webm$/, "")}.beats.txt`), log.join("\n") + "\n");
  console.log(`VIDEO: recordings/${finalName}`);
})().catch((e) => {
  console.error("RECORDER FAILED:", e.message);
  process.exit(1);
});
