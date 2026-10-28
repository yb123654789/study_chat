/* 学习bot · 今日任务（预览版）
 * 数据来自 data/plan.json（tools/export_web.py 导出的真实排程）
 *        data/quiz.json（按 prompts/plan_v1.md 手写的示例题）
 * 状态存 localStorage，刷新不丢；正式版换成 Supabase。
 */
const S = {
  plan: null,
  quiz: {},
  reading: {},
  today: null,        // 当天那一行
  cap: 120,
  results: {},        // "2026-10-24|g01" -> {passed, score}
  tab: 'today',
  quizState: null,    // 当前小测
  chats: {},          // "2026-10-24|g01" -> [{role,text}]
  chatTask: null,
  baseKey: 'studybot.v1',
};

const $ = (id) => document.getElementById(id);
const WD = '日一二三四五六';

/* ---------------- 工具 ---------------- */
function ymd(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function parseYmd(s) {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function diffDays(a, b) {
  return Math.round((parseYmd(b) - parseYmd(a)) / 86400000);
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function save() {
  try {
    localStorage.setItem(S.baseKey, JSON.stringify({ cap: S.cap, results: S.results, chats: S.chats }));
  } catch (e) {}
}
function load() {
  try {
    const raw = localStorage.getItem(S.baseKey);
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.cap) S.cap = d.cap;
    if (d.results) S.results = d.results;
    if (d.chats) S.chats = d.chats;
  } catch (e) {}
}
function toast(msg) {
  const el = document.createElement('div');
  el.textContent = msg;
  el.style.cssText =
    'position:fixed;left:50%;bottom:96px;transform:translateX(-50%);z-index:99;' +
    'background:rgba(20,22,28,.92);color:#fff;font-size:13px;padding:9px 14px;border-radius:10px;' +
    'max-width:88vw;text-align:center;animation:fade .16s ease';
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ---------------- 数据 ---------------- */
async function boot() {
  load();
  const [plan, quiz, reading] = await Promise.all([
    fetch('data/plan.json').then((r) => r.json()),
    fetch('data/quiz.json').then((r) => r.json()).catch(() => ({})),
    fetch('data/reading.json').then((r) => r.json()).catch(() => ({})),
  ]);
  S.plan = plan;
  S.quiz = quiz;
  S.reading = reading;
  S.today = pickToday(plan.days);
  await CHAT.probe();          // 判断有没有真模型；没有就退回本地模拟

  // 深链：推送点进来要直接到某个任务/某天，不用自己找
  const q = new URLSearchParams(location.search);
  if (q.get('tab') === 'plan') S.tab = 'plan';
  render();
  bind();
  if (q.get('task')) openTask(q.get('task'));
  if (q.get('quiz')) startQuiz(q.get('quiz'));
  if ('serviceWorker' in navigator && !q.has('nosw')) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}

/* 今天没有任务（比如刚过零点、或计划已过期）就取最近一个有任务的未来日 */
function pickToday(days) {
  const key = ymd(new Date());
  const exact = days.find((d) => d.date === key);
  if (exact) return exact;
  return days.find((d) => d.date > key && d.tasks.length) || days[0];
}

function resultsOf(day, task) {
  return S.results[`${day.date}|${task.id}`];
}
function learnedMinutes(day) {
  return day.tasks.reduce((n, t) => n + (resultsOf(day, t)?.passed ? t.min : 0), 0);
}
function doneCount(day) {
  return day.tasks.filter((t) => resultsOf(day, t)?.passed).length;
}
function taskMinutes(day) {
  return day.tasks.reduce((n, t) => n + t.min, 0);
}

/* ---------------- 渲染 ---------------- */
function render() {
  const d = S.today;
  const left = diffDays(ymd(new Date()), S.plan.exam_date);
  $('daysLeft').textContent = left > 0 ? left : 0;
  $('examDate').textContent = S.plan.exam_date.replace(/-/g, '.');
  $('doneCount').textContent = `${doneCount(d)}/${d.tasks.length}`;
  $('learnedMin').textContent = learnedMinutes(d);
  $('capMin').textContent = S.cap;

  $('view').innerHTML = S.tab === 'today' ? viewToday(d) : viewPlan();
  document.querySelectorAll('.tabs button').forEach((b) => {
    b.classList.toggle('on', b.dataset.tab === S.tab);
  });
}

function viewToday(day) {
  const total = taskMinutes(day);
  const over = total > S.cap;
  const allDone = day.tasks.length > 0 && doneCount(day) === day.tasks.length;
  return `
    <div class="cap-row">
      <span class="lab">今天能学</span>
      ${[60, 120, 180].map((m) => `<button class="chip ${S.cap === m ? 'on' : ''}" data-cap="${m}">${m / 60} 小时</button>`).join('')}
    </div>
    ${over ? `<div class="hint">今天排了 ${total} 分钟，超过你选的 ${S.cap} 分钟。按规则会把排不下的顺延到明天，并说明牺牲了什么。</div>` : ''}
    <div class="sect-title">今天 · ${day.date} 周${WD[parseYmd(day.date).getDay()]}${day.kind === '复习日' ? ' · 复习日' : ''}</div>
    ${day.tasks.length ? day.tasks.map((t, i) => taskCard(day, t, i)).join('') : `<div class="empty">今天没排任务（缓冲日）</div>`}
    ${allDone ? `<div class="hint" style="background:var(--ok-soft);color:var(--ok)">今天的 ${day.tasks.length} 个目标都过了。明天 07:00 见。</div>` : ''}
  `;
}

function taskCard(day, t, i) {
  const r = resultsOf(day, t);
  const cls = ['card'];
  if (t.p === 'core') cls.push('core');
  if (r?.passed) cls.push('passed');
  else if (r) cls.push('failed');
  const tag = t.p === 'core'
    ? '<span class="tag">核心</span>'
    : `<span class="tag sup">${t.p === 'support' ? '辅助' : '可选'}</span>`;
  const state = r?.passed
    ? '<span class="tag state-ok">已通过</span>'
    : r ? '<span class="tag state-bad">未通过 · 明天补</span>' : '';
  const btn = r?.passed
    ? `<button class="btn ghost" data-task="${t.id}">再看一眼</button>`
    : `<button class="btn" data-task="${t.id}">${r ? '再考一次' : '开始学习'}</button>`;
  return `
    <article class="${cls.join(' ')}">
      <div class="card-head">
        ${tag}${state}
        <span class="ch">${esc(t.ch)}</span>
      </div>
      <h3>${esc(t.title)}</h3>
      <div class="card-foot">
        <span class="min">${t.min} 分钟 · 含小测</span>
        ${btn}
      </div>
    </article>`;
}

function viewPlan() {
  const key = ymd(new Date());
  const days = S.plan.days.filter((d) => d.date >= key).slice(0, 21);
  const mark = { learn: '学', review: '复', exam: '考' };
  return `<div class="sect-title">接下来 3 周</div>` + days.map((d) => `
    <div class="day ${d.date === key ? 'today' : ''}">
      <div class="day-head">
        <b>${d.date.slice(5)}</b><span class="wd">周${WD[parseYmd(d.date).getDay()]}</span>
        <span class="kind">${d.kind}</span>
      </div>
      ${d.tasks.length ? d.tasks.map((t) => `
        <div class="item">
          <span class="k">${mark[t.kind] || '学'}</span>
          <span class="t">${esc(t.title)}</span>
          <span class="m">${t.min}′</span>
        </div>`).join('') : `<div class="item"><span class="t" style="color:var(--muted)">缓冲，不排任务</span></div>`}
    </div>`).join('');
}

/* ---------------- 弹层 ---------------- */
function openSheet(html, cls) {
  const body = $('sheetBody').parentElement;
  body.classList.toggle('chat', cls === 'chat');
  $('sheetBody').innerHTML = html;
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeSheet() {
  $('sheet').hidden = true;
  $('sheetBody').innerHTML = '';
  $('sheetBody').parentElement.classList.remove('chat');
  document.body.style.overflow = '';
  S.quizState = null;
  S.chatTask = null;
}

const taskById = (id) => S.today.tasks.find((x) => x.id === id);

function chatKey(id) {
  return `${S.today.date}|${id}`;
}

/* 点开任务 = 进聊天窗口，不是看资料 */
function openTask(taskId) {
  const rd = S.reading[taskId] || {};
  const key = chatKey(taskId);
  if (!S.chats[key]) {
    S.chats[key] = [{
      role: 'bot',
      text: (rd.lead ? rd.lead + '\n\n' : '')
        + '我是这一节的带练。你想先弄明白哪一点？\n'
        + '可以直接问，也可以点下面的按钮。\n'
        + '聊到你觉得能讲明白了，跟我说「出题」。',
    }];
    save();
  }
  S.chatTask = taskId;
  renderChat();
}

function renderChat() {
  const t = taskById(S.chatTask);
  const log = S.chats[chatKey(t.id)];
  const r = resultsOf(S.today, t);
  const hasQuiz = Array.isArray(S.quiz[t.id]) && S.quiz[t.id].length === 3;
  openSheet(`
    <div class="chat-head">
      <div class="chat-title">${esc(t.title)}</div>
      <div class="chat-sub">${esc(t.ch)} · ${t.min} 分钟${r?.passed ? ' · 已通过' : ''}</div>
    </div>
    <div class="chat-log" id="chatLog">
      ${log.map((m) => `<div class="msg ${m.role === 'me' ? 'me' : 'bot'}">
        <div class="bubble">${esc(m.text)}</div>
        ${(m.sources && m.sources.length) ? `<div class="refs">${m.sources.map((s) => esc(s.src.replace(/\.txt$/, '')) + ' · ' + esc(s.anchor)).join('<br>')}</div>` : ''}
      </div>`).join('')}
    </div>
    <div class="chat-bar">
      <div class="chips">
        <button data-say="举个例子">举个例子</button>
        <button data-say="这一节考什么">考什么</button>
        <button class="primary" data-say="出题">出题 →</button>
      </div>
      <div class="typing">
        <textarea id="chatBox" rows="1" placeholder="问点什么，或者说「出题」"></textarea>
        <button class="btn send" id="sendBtn">发送</button>
      </div>
      <div class="chat-note">${
        CHAT.mode === 'api'
          ? '真模型 · deepseek-flash'
          : '本地模拟回复，不是真讲解（没配 key）'
      }${hasQuiz ? '' : '；这套题还没生成'}</div>
    </div>
  `, 'chat');

  const box = $('chatBox');
  box.focus();
  box.addEventListener('input', () => {
    box.style.height = 'auto';
    box.style.height = Math.min(96, box.scrollHeight) + 'px';
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat(box.value);
    }
  });
  $('sendBtn').addEventListener('click', () => sendChat(box.value));

  const logEl = $('chatLog');
  logEl.scrollTop = logEl.scrollHeight;
}

function sendChat(raw) {
  const text = (raw || '').trim();
  if (!text || !S.chatTask) return;
  const t = taskById(S.chatTask);
  const key = chatKey(t.id);
  const log = S.chats[key];
  log.push({ role: 'me', text });
  save();
  renderChat();

  // 等模型时先放一个“正在想”的占位气泡，不写进状态
  const logEl = $('chatLog');
  if (logEl) {
    const d = document.createElement('div');
    d.className = 'msg bot';
    d.id = 'pendingMsg';
    d.innerHTML = '<div class="bubble thinking">正在想…</div>';
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  }

  CHAT.reply(t, S.reading[t.id] || {}, log, text).then((out) => {
    log.push({ role: 'bot', text: out.text, sources: out.sources || [] });
    save();
    renderChat();
    if (out.action === 'quiz') setTimeout(() => startQuiz(t.id), 450);
  }).catch((e) => {
    log.push({ role: 'bot', text: `（出错了：${e.message}）` });
    save();
    renderChat();
  });
}

/* ---------------- 小测 ---------------- */
function startQuiz(taskId) {
  const qs = S.quiz[taskId];
  S.quizState = { taskId, qs, i: 0, picks: new Array(qs.length).fill(null), texts: new Array(qs.length).fill('') };
  renderQuiz();
}

function renderQuiz() {
  const { qs, i, picks, texts } = S.quizState;
  const q = qs[i];
  const last = i === qs.length - 1;
  const ready = q.type === 'choice' ? picks[i] !== null : texts[i].trim().length >= 5;
  openSheet(`
    <div class="progress"><i style="width:${((i) / qs.length) * 100}%"></i></div>
    <div class="qno">第 ${i + 1} / ${qs.length} 题 · ${q.type === 'choice' ? '单选' : '简答'}</div>
    <div class="qstem">${esc(q.stem)}</div>
    ${q.type === 'choice'
      ? q.options.map((o, j) => `
        <button class="opt ${picks[i] === j ? 'on' : ''}" data-opt="${j}">
          <b>${'ABCD'[j]}</b><span>${esc(o)}</span>
        </button>`).join('')
      : `<textarea id="ans" placeholder="用两三句话写清楚思路，别只写结论">${esc(texts[i])}</textarea>
         <div class="note">预览版：简答按关键词判定（命中「${q.keys.join('」「')}」），正式版由模型按要点批改。</div>`}
    <div class="sheet-foot">
      ${i > 0 ? '<button class="btn ghost" data-prev>上一题</button>' : '<button class="btn ghost" data-close>退出</button>'}
      <button class="btn wide" data-next ${ready ? '' : 'disabled style="opacity:.4"'}>${last ? '交卷' : '下一题'}</button>
    </div>
  `);
  if (q.type !== 'choice') {
    const ta = $('ans');
    ta.addEventListener('input', () => {
      texts[i] = ta.value;
      const btn = document.querySelector('[data-next]');
      const ok = ta.value.trim().length >= 5;
      btn.disabled = !ok;
      btn.style.opacity = ok ? '1' : '.4';
    });
  }
}

function gradeShort(q, text) {
  const hit = q.keys.filter((k) => text.includes(k));
  return { pass: hit.length >= 1 && text.trim().length >= 15, hit, need: q.keys };
}

function submitQuiz() {
  const { taskId, qs, picks, texts } = S.quizState;
  const rows = qs.map((q, i) => {
    if (q.type === 'choice') {
      const ok = picks[i] === q.answer;
      return { ok, stem: q.stem, why: ok ? '选对了' : `正确答案是 ${'ABCD'[q.answer]}：${q.options[q.answer]}` };
    }
    const g = gradeShort(q, texts[i]);
    return {
      ok: g.pass,
      stem: q.stem,
      why: g.pass
        ? `命中要点：${g.hit.join('、')}`
        : `要写到的要点：${q.rubric.join('；')}`,
    };
  });
  const score = rows.filter((r) => r.ok).length;
  const passed = score >= 2;
  const key = `${S.today.date}|${taskId}`;
  S.results[key] = { passed, score, at: Date.now() };
  save();
  renderResult(rows, score, passed, taskId);
}

function renderResult(rows, score, passed, taskId) {
  openSheet(`
    <div class="result-icon">${passed ? '✅' : '❌'}</div>
    <h2 style="margin-bottom:2px">${passed ? '通过' : '没过，明天补考'}</h2>
    <div class="sub">${score}/3 题正确 · 规则是答对 ≥2 才过${passed ? '' : '，这一项不算完成'}</div>
    ${rows.map((r) => `
      <div class="res-row">
        <span class="mark ${r.ok ? 'ok' : 'bad'}">${r.ok ? '✓' : '✕'}</span>
        <span>${esc(r.stem)}<div class="why">${esc(r.why)}</div></span>
      </div>`).join('')}
    <div class="sheet-foot">
      <button class="btn ghost wide" data-close>回到今天</button>
      ${passed ? '' : `<button class="btn wide" data-quiz="${taskId}">再考一次</button>`}
    </div>
    ${passed ? '' : '<div class="note">换题是为了防背答案，不是为了刷到过。同一目标 24 小时内最多重出 2 次；连续两次换题都错，就回去重学，不再出新题。</div>'}
  `);
}

/* ---------------- 事件 ---------------- */
function bind() {
  $('view').addEventListener('click', (e) => {
    const cap = e.target.closest('[data-cap]');
    if (cap) { S.cap = Number(cap.dataset.cap); save(); render(); return; }
    const task = e.target.closest('[data-task]');
    if (task) openTask(task.dataset.task);
  });

  document.querySelectorAll('.tabs button').forEach((b) => {
    b.addEventListener('click', () => { S.tab = b.dataset.tab; render(); });
  });

  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) { closeSheet(); render(); return; }

    const say = e.target.closest('[data-say]');
    if (say) { sendChat(say.dataset.say); return; }

    const quizBtn = e.target.closest('[data-quiz]');
    if (quizBtn) { startQuiz(quizBtn.dataset.quiz); return; }

    const opt = e.target.closest('[data-opt]');
    if (opt) {
      S.quizState.picks[S.quizState.i] = Number(opt.dataset.opt);
      renderQuiz();
      return;
    }
    if (e.target.closest('[data-next]')) {
      const { i, qs } = S.quizState;
      if (i === qs.length - 1) submitQuiz();
      else { S.quizState.i++; renderQuiz(); }
      return;
    }
    if (e.target.closest('[data-prev]')) {
      S.quizState.i = Math.max(0, S.quizState.i - 1);
      renderQuiz();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('sheet').hidden) { closeSheet(); render(); }
  });
}

boot();
