/**
 * 词库专项：8 个词库都要能加载，声明的词数要和实际条数一致，
 * 每条数据都得有单词 + 中文释义（幼儿园启蒙的释义还必须是"干净"的：
 * 不带词性前缀、不夹英文、不带括号噪声 —— 那些是词典原文的坑）
 */
const { chromium } = require('playwright');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const BASE = 'file:///' + (ROOT + '/apps/word-hub/index.html').replace(/ /g, '%20');

const R = [];
const check = (n, ok, d = '') => { R.push({ n, ok: !!ok, d: String(d) }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  — ' + d : ''}`); };
const section = s => console.log(`\n----- ${s} -----`);

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message).slice(0, 140)));
  await page.route('**/*.woff2', r => r.abort());
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForFunction(() => typeof LISTS === 'object' && document.querySelectorAll('#listCards .list-card').length >= 8,
    null, { timeout: 40000 });

  section('词库清单');
  const meta = await page.evaluate(() => Object.keys(LISTS).map(k => ({
    k, name: LISTS[k].name, file: LISTS[k].file, total: LISTS[k].total })));
  check('一共 8 个词库，新增的两个排在最前（由易到难）',
    meta.length === 8 && meta[0].k === 'kids' && meta[1].k === 'primary',
    meta.map(m => m.k).join(' / '));
  check('新词库就在应用里（首页能看到词库卡片）',
    await page.evaluate(() => document.querySelectorAll('#listCards .list-card').length === 8),
    await page.evaluate(() => document.querySelectorAll('#listCards .list-card').length));

  section('逐个加载：声明的词数 = 实际条数');
  const loaded = await page.evaluate(async () => {
    const out = {};
    for (const k of Object.keys(LISTS)) {
      await selectList(k);                                  // 应用自己的加载逻辑（含 <script> 注入）
      const d = S.data || [];
      out[k] = {
        len: d.length, want: LISTS[k].total,
        noZh: d.filter(e => !e.t || !String(e.t).trim()).length,
        noW: d.filter(e => !e.w).length,
        sample: (d[0] || {}).w,
      };
    }
    return out;
  });
  for (const [k, v] of Object.entries(loaded)) {
    check(`${k}（${meta.find(m => m.k === k).name}）：${v.len} 词，与声明一致`,
      v.len > 0 && v.len === v.want, `实际 ${v.len} / 声明 ${v.want}，样例 ${v.sample}`);
  }
  const badZh = Object.entries(loaded).filter(([, v]) => v.noZh).map(([k, v]) => `${k}:${v.noZh}`);
  const badW = Object.entries(loaded).filter(([, v]) => v.noW).map(([k, v]) => `${k}:${v.noW}`);
  check('所有词库的每条数据都有单词与中文释义', badZh.length === 0 && badW.length === 0,
    `缺释义 ${badZh.join(',') || '无'}；缺单词 ${badW.join(',') || '无'}`);

  section('幼儿园启蒙的释义要"干净"');
  const kids = await page.evaluate(async () => {
    await selectList('kids');
    const d = S.data || [];
    const dirty = d.filter(e => /[A-Za-z()=<]/.test(e.t));       // 夹英文/括号噪声
    const pos = d.filter(e => /^(n|v|vt|vi|adj|adv|prep|pron|num|conj|int|art|aux)\./.test(e.t));
    const tooLong = d.filter(e => e.t.length > 16);
    const noPhon = d.filter(e => !e.p);
    return { len: d.length, dirty: dirty.map(e => `${e.w}=${e.t}`).slice(0, 4), nDirty: dirty.length,
             nPos: pos.length, nLong: tooLong.length, nNoPhon: noPhon.length,
             sample: d.slice(0, 3).map(e => `${e.w}/${e.p}/${e.t}`) };
  });
  check('释义里不夹英文、不带括号噪声', kids.nDirty === 0, kids.dirty.join(' | '));
  check('释义不带 "n." "vt." 这类词性前缀（幼儿园不需要）', kids.nPos === 0, `有 ${kids.nPos} 条带前缀`);
  check('释义简短（≤16 字）', kids.nLong === 0, `有 ${kids.nLong} 条过长`);
  check('绝大多数词都有音标', kids.nNoPhon <= kids.len * 0.05, `缺音标 ${kids.nNoPhon}/${kids.len}`);
  console.log('   样例:', kids.sample.join(' ; '));

  section('例句覆盖');
  const sent = await page.evaluate(async () => {
    const out = {};
    for (const k of ['kids', 'primary']) {
      await selectList(k);
      const d = S.data || [];
      out[k] = d.filter(e => window.ECDICT_SENT && window.ECDICT_SENT[e.w.toLowerCase()]).length;
    }
    return out;
  });
  check('小学词库例句覆盖 ≥ 90%', sent.primary >= 811 * 0.9, `${sent.primary}/811`);
  check('幼儿园词库例句覆盖 ≥ 70%', sent.kids >= 394 * 0.7, `${sent.kids}/394`);

  check('整场无页面 JS 报错', errs.length === 0, errs.join(' | ') || '无');

  console.log(`\n================ 词库专项 汇总：通过 ${R.filter(x => x.ok).length}/${R.length} ================`);
  await browser.close();
  process.exit(R.every(x => x.ok) ? 0 : 1);
})().catch(e => { console.error('测试异常：', e); process.exit(1); });
