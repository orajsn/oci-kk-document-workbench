// Optional browser regression. Requires Node.js, Playwright Chromium and a local Python 3.11+.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { once } = require('node:events');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const repo = path.resolve(__dirname, '..');
const cards = path.join(repo, 'demo', 'day-one');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1');
  await once(probe, 'listening');
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  return port;
}

async function waitUntilReady(url, server) {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (server.exitCode !== null) throw Error('Local practice server exited');
    try { if ((await fetch(url + '/api/health')).ok) return; } catch {}
    await pause(100);
  }
  throw Error('Local practice server did not become ready');
}

async function assertStable(page) {
  const sizes = await page.evaluate(() => new Promise(resolve => {
    const image = document.querySelector('#original-image');
    const sizes = [];
    function sample() {
      const box = image.getBoundingClientRect();
      sizes.push([box.width, box.height].join('/'));
      if (sizes.length === 65) resolve(sizes.slice(5));
      else requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  }));
  assert.equal(new Set(sizes).size, 1, 'Fit mode changed size while idle');
}

async function main() {
  const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'kk-viewer-test-'));
  const port = await freePort();
  const base = 'http://127.0.0.1:' + port;
  const server = spawn(process.env.KK_TEST_PYTHON || 'python', [
    'run_local.py', '--port', String(port), '--data-dir', dataDir,
    '--reference-dir', path.join(cards, 'reader')
  ], { cwd: repo, windowsHide: true, stdio: 'ignore' });
  let browser;
  try {
    await waitUntilReady(base, server);
    browser = await chromium.launch({
      headless: true, executablePath: process.env.KK_CHROMIUM_EXECUTABLE || undefined,
      ignoreDefaultArgs: ['--hide-scrollbars'],
      args: ['--disable-features=OverlayScrollbar,FluentOverlayScrollbar']
    });
    const page = await browser.newPage({ viewport: { width: 1175, height: 1087 } });
    await page.goto(base);
    await page.locator('#new-upload').click();
    await page.locator('#processing-mode').selectOption('reference');
    await page.locator('#upload-file').setInputFiles(path.join(cards, 'kartu-a.png'));
    await page.waitForFunction(() => document.querySelector('.batch-thumb')?.naturalWidth > 0);
    const thumb = await page.locator('.batch-thumb').elementHandle();
    await page.locator('#case-id').fill('PREVIEW-TEST');
    await page.locator('#upload-file').setInputFiles(path.join(cards, 'kartu-b.png'));
    assert(await thumb.evaluate(node => node === document.querySelector('.batch-thumb')));
    await page.locator('.batch-remove').nth(1).click();
    assert(await thumb.evaluate(node => node === document.querySelector('.batch-thumb')));
    await page.locator('#submit-upload').click();
    await page.locator('#view-uploaded-documents').click();
    await page.route(/\/api\/jobs\/[^/]+\/image$/, route => route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1200"><rect width="1600" height="1200" fill="white"/></svg>'
    }));
    await page.locator('#job-list button[data-job-id]').first().click();
    await page.waitForFunction(() => document.querySelector('#original-image').naturalWidth === 1600);
    // Fractional CSS dimensions and non-overlay scrollbars previously caused a fit/resize loop.
    await page.evaluate(() => {
      document.documentElement.style.zoom = '90%';
      const viewport = document.getElementById('image-viewport');
      viewport.style.width = '484.75px';
      viewport.style.height = '565.75px';
    });
    for (const value of ['100', '135', '140']) {
      await page.locator('#contrast-control').fill(value);
      await assertStable(page);
    }
    for (let i = 0; i < 5; i++) await page.locator('#zoom-in').click();
    assert(await page.locator('#image-viewport').evaluate(el => el.scrollWidth > el.clientWidth));
    await page.locator('#fit-image').click();
    await assertStable(page);
    console.log('PASS: stable upload thumbnails, fit, contrast and manual zoom.');
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {
      const stopped = once(server, 'exit');
      server.kill();
      await stopped;
    }
    if (path.dirname(dataDir) === os.tmpdir() && path.basename(dataDir).startsWith('kk-viewer-test-')) {
      await fs.rm(dataDir, { recursive: true, force: true });
    }
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
