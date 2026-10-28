/* 学习bot · 聊天导师
 *
 * 有两种模式：
 *   api  —— 走 /api/chat 代理（key 在服务端 .env），真模型讲解
 *   stub —— 本地模拟：从资料检索片段拼回答，界面会标出来
 *
 * 接口：CHAT.probe() / CHAT.reply(goal, reading, history, text) -> {text, action?}
 */
const CHAT = (() => {
  let mode = 'stub';
  let info = null;

  const clip = (s, n = 130) => (String(s).length <= n ? String(s) : String(s).slice(0, n) + '…');
  const has = (re, s) => re.test(s);

  async function probe() {
    try {
      const r = await fetch('api/health', { cache: 'no-store' });
      info = await r.json();
      mode = info && info.ok ? 'api' : 'stub';
    } catch (e) {
      mode = 'stub';
      info = null;
    }
    return mode;
  }

  function pick(rd, text) {
    const words = text.split(/[，。？?！!、\s]+/).filter((w) => w.length >= 2);
    const list = rd.excerpts || [];
    let best = list[0] || null;
    let top = -1;
    for (const e of list) {
      let s = 0;
      for (const w of words) if (e.text.includes(w)) s += 2;
      if (s > top) { top = s; best = e; }
    }
    return best;
  }

  /* 本地模拟：只贴资料 + 反问，不假装讲解 */
  function stub(goal, rd, text) {
    const ex = pick(rd, text);
    const cp = (rd.points && rd.points.length ? rd.points : goal.cp) || [];
    const first = cp[0] || '能用自己的话把这个目标讲一遍';

    if (has(/考什么|重点|考点|会不会考/, text)) {
      return {
        text: '这一节要能回答：\n' + cp.map((c, i) => `${i + 1}. ${c}`).join('\n')
          + '\n\n先挑一条你最有把握的说给我听，我挑毛病。',
      };
    }

    if (!ex) {
      return {
        text: `资料里没检索到跟「${clip(text, 20)}」直接相关的段落。\n`
          + '换个说法？或者先问我「这一节考什么」，我按验收点给你拆。',
      };
    }

    const quote = `资料原文：「${clip(ex.text)}」（${ex.src} · ${ex.anchor}）`;

    if (has(/例子|举例|举个/, text)) {
      const s = (ex.text.split(/[。；]/).find((x) => /例|比如/.test(x)) || '').trim();
      return {
        text: s
          ? `${quote}\n\n里面提到：${clip(s, 90)}\n\n你能照着这个例子再编一个自己的版本吗？`
          : `${quote}\n\n这段没现成例子。你给个具体场景，我们拿它套一遍。`,
      };
    }

    return {
      text: `（本地模拟回复，不是真讲解）\n${quote}\n\n我能对上的是上面这段。\n`
        + `你要的是「讲清楚」还是「举个例子」？说明之后我更好往那个方向答。\n\n`
        + `聊得差不多了就说「出题」。`,
    };
  }

  async function reply(goal, rd, history, text) {
    const t = (text || '').trim();

    if (/出题|考我|测一下|测验|来套题/.test(t) && t.length <= 14) {
      return { action: 'quiz', text: '好，开始小测：3 题，答对 2 道才算过。' };
    }

    if (mode === 'api') {
      try {
        const r = await fetch('api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            goal: { title: goal.title, ch: goal.ch, min: goal.min, cp: goal.cp || [] },
            reading: rd,
            history: history.filter((m) => !m.pending).slice(-8),
            text: t,
          }),
        });
        const j = await r.json();
        if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
        return { text: j.text, sources: j.sources || [] };
      } catch (e) {
        const s = stub(goal, rd, t);
        return { text: `（模型没回上来：${e.message}）\n\n先用资料检索顶着：\n\n${s.text}` };
      }
    }

    return stub(goal, rd, t);
  }

  return { probe, reply, get mode() { return mode; }, get info() { return info; } };
})();
