// Open CREST's ArcGIS dashboard in the real ArcGIS Dashboards app, against the local stand-in for ArcGIS
// Online that tools/dashboard_preview.py serves, and take screenshots. Nothing is published.
//
//   python tools/dashboard_preview.py --site <built CREST site> &
//   node tools/dashboard_preview.js [--out preview.png] [--wait 20000] [--port 8765] [--width 1600] [--height 950]
//        [--steps '[{"click":"text=Floods"},{"wait":3000},{"shot":"floods.png"}]'] [--log rest|all] [--text]
//
// The browser loads the Dashboards app from www.arcgis.com; requests for the preview's items, for its
// feature services and for the CREST pages the dashboard embeds are answered by the stand-in.
// Steps: {"click": selector} {"clickAt": [x, y]} {"type": text} {"wait": ms} {"shot": file} {"select": [selector, text]}
//        {"frameText": true} (prints each frame's text) {"eval": "js"}. --scale 2 doubles the screenshots' pixels.
// Needs Node with Playwright and its Chromium; HTTPS_PROXY is used if set.
const { chromium } = require('playwright');

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i < 0 ? fallback : (process.argv[i + 1] === undefined || process.argv[i + 1].startsWith('--') ? true : process.argv[i + 1]);
}

(async () => {
  const port = +arg('port', 8765), local = 'http://127.0.0.1:' + port;
  const ids = await (await fetch(local + '/ids')).json();
  const out = arg('out', 'dashboard_preview.png'), wait = +arg('wait', 20000), log = arg('log', '');
  const steps = JSON.parse(arg('steps', '[]'));
  const browser = await chromium.launch(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {});
  const ctx = await browser.newContext({ viewport: { width: +arg('width', 1600), height: +arg('height', 950) },
    deviceScaleFactor: +arg('scale', 1), timezoneId: arg('tz', 'America/New_York'), locale: 'en-US' });
  const page = await ctx.newPage();

  async function forward(route, path) {
    const req = route.request(), cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' };
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    try {
      const post = req.method() === 'POST';
      const res = await fetch(local + path, { method: req.method(), body: post ? req.postData() : undefined,
        headers: post ? { 'content-type': 'application/x-www-form-urlencoded' } : {} });
      const body = Buffer.from(await res.arrayBuffer());
      if (log === 'all' || (log === 'rest' && /FeatureServer/.test(path))) {
        console.log('LOCAL', req.method(), decodeURIComponent(path).slice(0, 700) + (post ? ' BODY ' + decodeURIComponent(req.postData() || '').slice(0, 500) : ''),
          '=>', res.status, body.toString().slice(0, 160));
      }
      return route.fulfill({ status: res.status, headers: Object.assign({ 'content-type': res.headers.get('content-type') || 'application/json' }, cors), body });
    } catch (e) {
      console.log('FORWARD FAILED', path.slice(0, 120), e.message);
      return route.abort();
    }
  }
  const mine = new Set(ids.items), names = new Set(ids.names);
  await ctx.route(/\/sharing\/rest\/content\/items\/[0-9a-f]{32}/, route => {
    const u = new URL(route.request().url());
    const m = u.pathname.match(/\/sharing\/rest\/content\/items\/([0-9a-f]{32})/);
    return m && mine.has(m[1]) ? forward(route, u.pathname.slice(u.pathname.indexOf('/sharing/rest/')) + u.search) : route.continue();
  });
  await ctx.route(u => u.href.startsWith(ids.services + '/'), route => {
    const u = new URL(route.request().url());
    const name = (u.pathname.split('/services/')[1] || '').split('/')[0].toLowerCase();
    return names.has(name) ? forward(route, u.pathname + u.search) : route.continue();
  });
  await ctx.route(u => u.href.startsWith(ids.base), route => {
    const u = new URL(route.request().url());
    return forward(route, '/site/' + u.href.slice(ids.base.length).replace(/^\//, ''));
  });

  const failed = {};
  page.on('requestfailed', r => { const h = new URL(r.url()).host; failed[h] = (failed[h] || 0) + 1; if (failed[h] < 4) console.log('FAILED', r.url().slice(0, 160), r.failure() && r.failure().errorText); });
  page.on('response', async r => {
    const u = r.url();
    if (r.status() >= 400) console.log('HTTP', r.status(), u.slice(0, 220));
    if (log && /\/rest\//.test(u) && !u.startsWith(ids.services)) {
      let t = ''; try { t = (await r.text()).slice(0, 300); } catch (e) {}
      if (/"error"/.test(t) || log === 'all') console.log('REST', u.slice(0, 220), '=>', t.replace(/\s+/g, ' ').slice(0, 200));
    }
  });
  page.on('console', m => { if (m.type() === 'error' || arg('console', false)) console.log('CONSOLE', m.type(), m.text().slice(0, 400)); });
  page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 400), (e.stack || '').split('\n').slice(1, 7).join(' <- ').slice(0, 900)));

  await page.goto(arg('url', ids.open), { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(e => console.log('GOTO', e.message));
  await page.waitForTimeout(wait);
  await page.screenshot({ path: out });
  for (const s of steps) {
    try {
      if (s.click) await page.click(s.click, { timeout: 8000 });
      if (s.clickAt) await page.mouse.click(s.clickAt[0], s.clickAt[1]);
      if (s.type) await page.keyboard.type(s.type, { delay: 40 });
      if (s.select) { await page.click(s.select[0], { timeout: 8000 }); await page.waitForTimeout(800); await page.click('text=' + s.select[1], { timeout: 8000 }); }
      if (s.eval) console.log('EVAL', JSON.stringify(await page.evaluate(s.eval)));
      if (s.wait) await page.waitForTimeout(s.wait);
      if (s.shot) await page.screenshot({ path: s.shot });
      if (s.frameText) for (const f of page.frames()) console.log('FRAME', f.url().slice(0, 140), '|', (await f.evaluate(() => document.body ? document.body.innerText : '').catch(() => '')).replace(/\n+/g, ' | ').slice(0, 400));
    } catch (e) { console.log('STEP FAILED', JSON.stringify(s), e.message.split('\n')[0]); }
  }
  if (arg('text', false)) console.log('TEXT', (await page.evaluate(() => document.body.innerText)).replace(/\n+/g, ' | ').slice(0, 6000));
  console.log('failed', JSON.stringify(failed), 'title', await page.title());
  await browser.close();
})();
