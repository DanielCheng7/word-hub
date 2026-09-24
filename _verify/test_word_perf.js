/* 窗口交互性能改造的专项验证
 *
 * 桌面壳（pywebview）在这个沙箱会话里起不来渲染进程，所以这里用真实 Edge 打开
 * index.html?desktop=1，并在页面脚本运行前打桩一个 window.pywebview.api，
 * 把「拖窗 / 缩放 / 三个按钮」这条链路的调用次数、参数、时序全部照原样量出来。
 *
 * 重点量：拖动是否节流（合帧）、松手/取消后是否还会继续发 move（卡住的拖动）、
 *         交互期间是否关掉了毛玻璃、按钮命中区够不够大。
 */
const { chromium } = require('playwright');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const URLX = 'file:///' + path.join(ROOT, 'apps/word-hub/index.html').replace(/\\/g, '/') + '?desktop=1';

const RES = [];
function check(name, ok, detail = '') {
  RES.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const page = await (await browser.newContext({ viewport: { width: 1345, height: 874 } })).newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message).slice(0, 120)));

  // 打桩：页面脚本运行前就把 pywebview.api 放好，并记录每一次调用
  await page.addInitScript(() => {
    window.__calls = [];
    const rec = name => (...a) => { window.__calls.push({ name, args: a, t: performance.now() }); return true; };
    window.__lat = (n) => {
      const want = { minimize: 'minimize', zoom: 'zoom', close: 'close', beginDrag: 'begin_drag', dragMove: 'drag_to', endDrag: 'end_drag' }[n];
      return window.__calls.filter(c => c.name === want).length;
    };
    // 懒加载 api：桌面栏绑定时会读 window.pywebview.api
    const api = {};
    ['close', 'minimize', 'zoom', 'begin_resize', 'update_resize', 'end_resize', 'begin_drag', 'drag_to', 'end_drag']
      .forEach(k => { api[k] = rec(k); });
    window.pywebview = { api };
  });

  await page.goto(URLX, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForFunction(() => document.querySelectorAll('#listCards .list-card').length === Object.keys(LISTS).length, null, { timeout: 40000 });
  // 等桌面栏绑定完成（页面里有个 setInterval 兜底，最多 100ms 一轮）
  await page.waitForFunction(() => document.body.classList.contains('desktop'), null, { timeout: 20000 });
  await page.waitForTimeout(600);

  /* ---------------- 一、内置拖动标记必须已经摘掉 ---------------- */
  const dom = await page.evaluate(() => ({
    hasClass: document.querySelector('.macdrag').classList.contains('pywebview-drag-region'),
    selectorHit: document.querySelectorAll('.pywebview-drag-region').length,
    barBox: (() => { const r = document.querySelector('.macdrag').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })(),
  }));
  check('拖动区已摘掉 pywebview-drag-region（内置的拖动处理器不会再挂上）',
    !dom.hasClass && dom.selectorHit === 0, JSON.stringify(dom));

  /* ---------------- 二、三颗按钮的命中区 ---------------- */
  const hit = await page.evaluate(() => {
    const out = {};
    for (const a of ['min', 'zoom', 'close']) {
      const el = document.querySelector(`.lt-${a}`);
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el, '::after');
      // ::after 的 inset:-6px → 命中区 = 圆点尺寸 + 12
      out[a] = {
        dot: [Math.round(r.width), Math.round(r.height)],
        afterInset: cs.inset,
        gap: getComputedStyle(el.parentElement).gap,
      };
    }
    return out;
  });
  check('三颗按钮圆点仍是 12px，但命中区靠伪元素扩到 24×24',
    hit.min.dot[0] === 12 && /-6px/.test(hit.min.afterInset) && /-6px/.test(hit.close.afterInset),
    JSON.stringify(hit));
  check('按钮间距 12px，相邻命中区正好相接不重叠', hit.min.gap === '12px', hit.min.gap);

  /* ---------------- 三、拖动：节流（合帧）+ 参数正确 ---------------- */
  await page.evaluate(() => { window.__calls.length = 0; });
  // 用合成的 PointerEvent 精确控制 screenX/screenY
  const dragStat = await page.evaluate(async () => {
    const bar = document.querySelector('.macdrag');
    const fire = (type, sx, sy) => bar.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 7, pointerType: 'mouse',
      button: 0, buttons: type === 'pointerup' ? 0 : 1,
      clientX: 300, clientY: 19, screenX: sx, screenY: sy,
    }));
    // 真实鼠标的移动频率远高于屏幕帧率（高回报率鼠标一帧能来好几个事件），
    // 所以这里一帧里连发 10 个位置，验证「一帧最多发一次 drag_to、且发的是最新位置」
    const PER_FRAME = 10, FRAMES = 6, N = PER_FRAME * FRAMES;
    let k = 0;
    fire('pointerdown', 1000, 800);
    for (let f = 0; f < FRAMES; f++) {
      for (let i = 0; i < PER_FRAME; i++) { k++; fire('pointermove', 1000 - k * 3, 800 + k * 2); }
      await new Promise(r => requestAnimationFrame(r));
    }
    await new Promise(r => requestAnimationFrame(r));   // 等最后一帧的合帧回调落下来再量
    const during = window.__calls.filter(c => c.name === 'drag_to').length;
    // v1.30 起页面**不再往 Python 传任何坐标**（位置/位移都由后端用
    // GetWindowRect + GetCursorPos 现读）→ 每次 drag_to 都必须是零参数，
    // 这样"CSS 像素 vs 物理像素"那类换算错误就没有藏身之处了。
    const argCounts = window.__calls.filter(c => c.name === 'drag_to').map(c => c.args.length);
    const beginArgCounts = window.__calls.filter(c => c.name === 'begin_drag').map(c => c.args.length);
    const interactingDuring = document.body.classList.contains('win-interacting');
    const blurDuring = getComputedStyle(document.querySelector('#macbar')).backdropFilter;
    const begin = window.__calls.filter(c => c.name === 'begin_drag').length;
    fire('pointerup', 1000 - N * 3, 800 + N * 2);
    await new Promise(r => setTimeout(r, 60));
    const endAfterUp = window.__calls.filter(c => c.name === 'end_drag').length;

    // 松手之后再晃鼠标：绝不能再发 move（"卡住的拖动"）
    window.__calls.length = 0;
    for (let i = 0; i < 20; i++) fire('pointermove', 500 + i * 9, 400 + i * 7);
    await new Promise(r => setTimeout(r, 150));
    const afterUp = window.__calls.filter(c => c.name === 'drag_to').length;

    return {
      begin, endAfterUp, moves: N, during, afterUp, argCounts, beginArgCounts,
      interactingDuring, blurDuring,
      interactingAfter: document.body.classList.contains('win-interacting'),
    };
  });
  // end_drag 是在 pointerup 时调的，上面清过 __calls，单独再量一次
  const endInfo = await page.evaluate(() => {
    const bar = document.querySelector('.macdrag');
    window.__calls.length = 0;
    const fire = (type, sx, sy) => bar.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 9, pointerType: 'mouse',
      button: 0, buttons: type === 'pointerup' ? 0 : 1, clientX: 300, clientY: 19, screenX: sx, screenY: sy,
    }));
    const begArg = [];
    fire('pointerdown', 700, 700);
    fire('pointerup', 700, 700);
    return { begin: window.__calls.filter(c => c.name === 'begin_drag').length,
             end: window.__calls.filter(c => c.name === 'end_drag').length };
  });

  check('按下只发一次 begin_drag', dragStat.begin === 1, `begin=${dragStat.begin}`);
  check('60 次指针移动（每帧 10 个）只发了约等于帧数的 drag_to（rAF 合帧生效）',
    dragStat.during > 0 && dragStat.during <= 12, `move ${dragStat.during} 次 / 指针 ${dragStat.moves} 次`);
  check('begin_drag / drag_to 都不带参数（页面不再传坐标，换算错误无处藏身）',
    dragStat.argCounts.length > 0 && dragStat.argCounts.every(n => n === 0)
    && dragStat.beginArgCounts.every(n => n === 0),
    `drag_to 参数个数 ${JSON.stringify(dragStat.argCounts)} / begin_drag ${JSON.stringify(dragStat.beginArgCounts)}`);
  check('松手时正好发一次 end_drag', dragStat.endAfterUp === 1, `end=${dragStat.endAfterUp}`);
  check('交互期间给 body 加了 win-interacting', dragStat.interactingDuring === true);
  check('交互期间 #macbar 的 backdrop-filter 已关闭（不再每帧重算全宽高斯模糊）',
    dragStat.blurDuring === 'none', dragStat.blurDuring);
  check('松手后 win-interacting 已移除', dragStat.interactingAfter === false);
  check('松手后再晃鼠标不再发任何 drag_to（没有"卡住的拖动"）',
    dragStat.afterUp === 0, `afterUp=${dragStat.afterUp}`);
  check('一次完整的按下/松手恰好一对 begin_drag / end_drag',
    endInfo.begin === 1 && endInfo.end === 1, JSON.stringify(endInfo));

  /* ---------------- 四、pointercancel / 失焦 也能收尾 ---------------- */
  const cancelInfo = await page.evaluate(async () => {
    const bar = document.querySelector('.macdrag');
    const fire = (type, sx, sy, pid = 11) => bar.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: pid, pointerType: 'mouse',
      button: 0, buttons: 1, clientX: 300, clientY: 19, screenX: sx, screenY: sy,
    }));
    window.__calls.length = 0;
    fire('pointerdown', 100, 100);
    fire('pointercancel', 100, 100);
    await new Promise(r => setTimeout(r, 60));
    const afterCancel = { end: window.__calls.filter(c => c.name === 'end_drag').length,
                          interacting: document.body.classList.contains('win-interacting') };
    // 取消之后再移动，不该有 move
    window.__calls.length = 0;
    for (let i = 0; i < 10; i++) fire('pointermove', 200 + i, 200 + i);
    await new Promise(r => setTimeout(r, 100));
    afterCancel.movesAfter = window.__calls.filter(c => c.name === 'drag_to').length;
    return afterCancel;
  });
  check('pointercancel 会收尾（end_drag + 去掉 win-interacting）且之后不再发 move',
    cancelInfo.end === 1 && cancelInfo.interacting === false && cancelInfo.movesAfter === 0,
    JSON.stringify(cancelInfo));

  /* ---------------- 五、三个按钮点下去真的会调到 Python ---------------- */
  const btn = await page.evaluate(async () => {
    const out = {};
    window.__calls.length = 0;
    for (const a of ['min', 'zoom', 'close']) {
      document.querySelector(`.lt-${a}`).click();
      await new Promise(r => setTimeout(r, 30));
    }
    out.calls = window.__calls.map(c => c.name);
    return out;
  });
  check('最小化 / 最大化 / 关闭 三个按钮点击都会调到 Python 侧',
    ['minimize', 'zoom', 'close'].every(n => btn.calls.includes(n)), JSON.stringify(btn.calls));

  /* ---------------- 六、CDN 字体不再阻塞首屏 ---------------- */
  const links = await page.evaluate(() => [...document.querySelectorAll('link[rel="stylesheet"]')].map(l => ({
    href: (l.getAttribute('href') || '').slice(0, 46),
    media: l.getAttribute('media'),
    onload: l.getAttribute('onload') || '',
  })));
  check('三个 CDN 字体样式表都是非阻塞加载（media=print + onload 切 all）',
    links.length === 3 && links.every(l => /media/.test(l.onload) && (l.media === 'print' || l.media === 'all')),
    JSON.stringify(links.map(l => l.media)));

  /* ---------------- 七、真实鼠标事件也能走通拖动 ---------------- */
  const real = await page.evaluate(() => { window.__calls.length = 0; return true; });
  const box = await page.evaluate(() => {
    const r = document.querySelector('.macdrag').getBoundingClientRect();
    return { x: Math.round(r.x + 260), y: Math.round(r.y + r.height / 2) };
  });
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  for (let i = 1; i <= 12; i++) { await page.mouse.move(box.x - i * 4, box.y + i * 2); await sleep(16); }
  await page.mouse.up();
  await sleep(120);
  const realCalls = await page.evaluate(() => ({
    begin: window.__calls.filter(c => c.name === 'begin_drag').length,
    move: window.__calls.filter(c => c.name === 'drag_to').length,
    end: window.__calls.filter(c => c.name === 'end_drag').length,
  }));
  check('真实鼠标拖动同样走通（begin/drag_to/end 各就位）',
    realCalls.begin === 1 && realCalls.move >= 1 && realCalls.move <= 12 && realCalls.end === 1,
    JSON.stringify(realCalls));

  /* ---------------- 七点五、双击窗口栏 = 缩放（不能被指针捕获吞掉） ---------------- */
  const dbl = await page.evaluate(() => { window.__calls.length = 0; return true; });
  const barBox = await page.evaluate(() => {
    const r = document.querySelector('.macdrag').getBoundingClientRect();
    return { x: Math.round(r.x + 420), y: Math.round(r.y + r.height / 2) };
  });
  await page.mouse.click(barBox.x, barBox.y, { clickCount: 2, delay: 40 });
  await sleep(200);
  const dblCalls = await page.evaluate(() => window.__calls.filter(c => c.name === 'zoom').length);
  check('双击窗口栏只触发一次缩放（指针捕获没把 dblclick 吞掉，也没重复触发）',
    dblCalls === 1, `zoom 调用 ${dblCalls} 次`);

  /* ---------------- 八、缩放链路仍然可用 ---------------- */
  const rz = await page.evaluate(async () => {
    window.__calls.length = 0;
    const el = document.querySelector('.rz-e');
    const fire = (type, sx, sy) => el.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 21, pointerType: 'mouse',
      button: 0, buttons: 1, clientX: 1340, clientY: 400, screenX: sx, screenY: sy,
    }));
    fire('pointerdown', 2000, 600);
    for (let i = 0; i < 20; i++) { fire('pointermove', 2000 + i * 4, 600); await new Promise(r => requestAnimationFrame(r)); }
    const during = window.__calls.filter(c => c.name === 'update_resize').length;
    fire('pointerup', 2080, 600);
    await new Promise(r => setTimeout(r, 60));
    return {
      begin: window.__calls.filter(c => c.name === 'begin_resize').length,
      during,
      end: window.__calls.filter(c => c.name === 'end_resize').length,
      interacting: document.body.classList.contains('win-interacting'),
    };
  });
  check('边缘缩放：一次 begin / 合帧后的 update / 一次 end，且收尾清掉 win-interacting',
    rz.begin === 1 && rz.during > 0 && rz.during <= 20 && rz.end === 1 && rz.interacting === false,
    JSON.stringify(rz));

  /* ================= 顶部能拖动（修 bug） =================
     之前 #rzbox(950) 压在 #macbar(900) 上面，点窗口最顶端会触发"缩放窗口"（看着像刷新），拖动直接废掉 */
  const dragTop = await page.evaluate(async () => {
    const hasTopZone = !!document.querySelector('.rz-n')
                    || !!document.querySelector('.rz-nw')
                    || !!document.querySelector('.rz-ne');
    const zBar = parseInt(getComputedStyle(document.getElementById('macbar')).zIndex, 10);
    const zRz = parseInt(getComputedStyle(document.getElementById('rzbox')).zIndex, 10);
    window.__calls.length = 0;
    const bar = document.querySelector('.macdrag');
    const b = bar.getBoundingClientRect();
    bar.dispatchEvent(new PointerEvent('pointerdown', {
      button: 0, pointerId: 7, bubbles: true,
      clientX: b.left + 60, clientY: b.top + 2, screenX: 400, screenY: 60,      // 故意点最顶端 2px 处
    }));
    await new Promise(r => setTimeout(r, 60));
    const names = window.__calls.map(c => c.name);
    bar.dispatchEvent(new PointerEvent('pointerup', { button: 0, pointerId: 7, bubbles: true }));
    return { hasTopZone, zBar, zRz, names };
  });
  check('顶部不再有缩放热区，且标题栏层级高于热区层（不会变成缩放窗口）',
    dragTop.hasTopZone === false && dragTop.zBar > dragTop.zRz, JSON.stringify(dragTop));
  check('点/拖窗口最顶端走 begin_drag，不是 begin_resize（修"点顶层就刷新、拖不动"）',
    dragTop.names.includes('begin_drag') && !dragTop.names.includes('begin_resize'),
    JSON.stringify(dragTop.names));

  /* ================= 拉伸之后还能不能正常拖动（真鼠标） =================
     甲方反馈"拉伸完之后又不能正常移动窗口位置了" —— 怀疑缩放手势没干净收尾：
     .rz 上的指针捕获没释放 / body.rz-dragging 没摘 → 标题栏收不到 pointerdown。
     ⚠️ 必须用真实鼠标（page.mouse）：合成事件测不出"被捕获/被遮挡"这类问题。 */
  const rzBox = await page.evaluate(() => {
    const r = document.querySelector('.rz-e').getBoundingClientRect();
    const b = document.querySelector('.macdrag').getBoundingClientRect();
    return { rz: { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) },
             bar: { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) } };
  });
  await page.evaluate(() => { window.__calls.length = 0; });
  await page.mouse.move(rzBox.rz.x, rzBox.rz.y);
  await page.mouse.down();
  await page.mouse.move(rzBox.rz.x + 40, rzBox.rz.y, { steps: 6 });
  await page.mouse.up();
  await new Promise(r => setTimeout(r, 120));
  const afterResize = await page.evaluate(() => ({
    rzDragging: document.body.classList.contains('rz-dragging'),
    winInteracting: document.body.classList.contains('win-interacting'),
    names: window.__calls.map(c => c.name),
  }));
  check('拉伸手势收尾干净：rz-dragging / win-interacting 都摘掉了，且发出了 end_resize',
    afterResize.rzDragging === false && afterResize.winInteracting === false
    && afterResize.names.includes('end_resize') && afterResize.names.includes('begin_resize'),
    JSON.stringify(afterResize));

  await page.evaluate(() => { window.__calls.length = 0; });
  await page.mouse.move(rzBox.bar.x, rzBox.bar.y);
  await page.mouse.down();
  await page.mouse.move(rzBox.bar.x + 60, rzBox.bar.y + 30, { steps: 6 });
  await page.mouse.up();
  await new Promise(r => setTimeout(r, 120));
  const afterDrag = await page.evaluate(() => window.__calls.map(c => c.name));
  const dragMoves = afterDrag.filter(n => n === 'drag_to').length;
  check('拉伸之后拖标题栏仍然走 begin_drag / drag_to（没被卡住）',
    afterDrag.includes('begin_drag') && dragMoves > 0,
    JSON.stringify(afterDrag.slice(0, 14)) + `  drag_to×${dragMoves}`);

  console.log('\n页面 JS 报错:', errs.length ? errs.join(' | ') : '无');
  console.log(`\n================ 窗口交互性能 汇总：通过 ${RES.filter(Boolean).length}/${RES.length} ================`);
  await browser.close();
  process.exit(RES.every(Boolean) ? 0 : 1);
})().catch(e => { console.error('测试异常：', e); process.exit(1); });
