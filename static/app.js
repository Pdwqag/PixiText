// --- 1) BFCache/戻る進む対策を強化（Chrome/Firefox/Safari 全対応） ---
function refreshCSS() {
  document.querySelectorAll('link[rel="stylesheet"][href*="style.css"]').forEach(link => {
    const u = new URL(link.href, location.href);
    u.searchParams.set('t', Date.now().toString()); // 使い捨てバスター
    link.href = u.toString();
  });
}

window.addEventListener('pageshow', (e) => {
  // Safari は e.persisted、Chrome/Firefox は Navigation Timing で検出
  const nav = performance.getEntriesByType('navigation')[0];
  const isBF = (e.persisted === true) || (nav && nav.type === 'back_forward');
  if (isBF) refreshCSS();
});

// --- 2) プレビューのページ番号ハイライト & 位置同期 ---
(function(){
  let scheduled = false;
  function setActiveByCenter(){
    scheduled = false;
    const mid = window.innerHeight/2;
    let best=null, bestDist=Infinity;
    document.querySelectorAll('.page').forEach(sec=>{
      const r = sec.getBoundingClientRect();
      const d = Math.abs((r.top + r.height/2) - mid);
      if (d < bestDist){ bestDist=d; best=sec; }
    });
    if (best){
      const idx = String(best.dataset.index);
      const nums = document.querySelectorAll('.bottom-pager .page-number');
      if (nums.length) {
        nums.forEach(a => a.classList.toggle('active', a.dataset.page === idx));
      }
      // URL の ?p= を同期（リロードなし）
      const url = new URL(window.location.href);
      if (url.searchParams.get('p') !== idx) {
        url.searchParams.set('p', idx);
        history.replaceState(null, '', url.toString());
      }
    }
  }
  function schedule(){ if (!scheduled){ scheduled = true; requestAnimationFrame(setActiveByCenter); } }

  function gotoByHashOrQuery(){
    const h = location.hash;
    let m = h.match(/^#(\d+)$/) || h.match(/^#page-(\d+)$/);
    let target = null;
    if (m) target = m[1];
    else {
      const url = new URL(window.location.href);
      const p = url.searchParams.get('p');
      if (p) target = p;
    }
    if (target){
      const el = document.getElementById('page-' + target) || document.getElementById(target);
      if (el){
        // behavior: 'instant' は仕様外。確実に即時スクロール。
        el.scrollIntoView({ behavior: 'auto', block: 'start' });
      }
    }
  }

  window.addEventListener('scroll', schedule, { passive:true });
  window.addEventListener('hashchange', gotoByHashOrQuery);
  document.addEventListener('DOMContentLoaded', () => { setActiveByCenter(); gotoByHashOrQuery(); });
})();

// --- 3) タグ挿入（そのままでOK） ---
(function(){
  function insertAtCursor(el, text){
    const [start, end] = [el.selectionStart, el.selectionEnd];
    const before = el.value.slice(0, start);
    const after  = el.value.slice(end);
    el.value = before + text + after;
    const pos = start + text.length;
    el.selectionStart = el.selectionEnd = pos;
    el.focus();
  }
  document.addEventListener('click', function(e){
    const btn = e.target.closest('button.ins');
    if (!btn) return;
    const ta = document.getElementById('text');
    if (!ta) return;
    insertAtCursor(ta, btn.dataset.insert);
  });
})();

async function callPreviewAPI(body){
  const resp = await fetch('/api/preview_page', {
    method: 'POST',
    body,
    credentials: 'same-origin',
  });

  const data = await resp.json().catch(() => ({}));
  return { resp, data };
}

// --- 4) プレビュー送信前にエラーチェックしてトースト表示 ---
(()=>{
  document.addEventListener('submit', async (e)=>{
    const form = e.target;
    const submitter = e.submitter || document.activeElement;
    const action = (submitter && submitter.formAction) || form.getAttribute('action') || '';
    const isPreview = submitter && submitter.dataset.ajaxPreview !== undefined;

    if (!isPreview || !/\/preview(\b|$)/.test(action)) return;

    e.preventDefault();

    const fd = new FormData(form);
    if (submitter && submitter.name) {
      fd.append(submitter.name, submitter.value || '');
    }

    try {
      const { resp, data } = await callPreviewAPI(fd);

      if (resp.ok && data.success) {
        const target = new URL(action, location.href);
        target.searchParams.set('p', data.p || 1);
        window.location.href = target.toString();
        return;
      }

      const msg = data && data.message ? data.message : 'プレビューに失敗しました。';
      window.showToast && window.showToast(msg);
    } catch (err) {
      window.showToast && window.showToast('プレビュー処理中にエラーが発生しました。');
      console.error(err);
    }
  });
})();

// --- 5) トップページに簡易プレビューカードを表示 ---
(()=>{
  const panel = document.querySelector('[data-preview-panel]');
  if (!panel) return;

  const box = panel.querySelector('[data-preview-box]');
  const empty = panel.querySelector('[data-preview-empty]');
  const textArea = panel.querySelector('[data-preview-text]');
  const counter = panel.querySelector('[data-preview-counter]');
  const prev = panel.querySelector('[data-preview-prev]');
  const next = panel.querySelector('[data-preview-next]');
  const refresh = panel.querySelector('[data-preview-refresh]');
  const textInput = document.getElementById('text');
  const modeSelect = document.querySelector('select[name="writing_mode"]');

  let current = 1;
  let total = 1;

  function toggleEmpty(showEmpty){
    if (!box || !empty) return;
    box.hidden = showEmpty;
    empty.hidden = !showEmpty;
  }

  async function loadPreview(page = 1){
    const textValue = textInput ? textInput.value.trim() : '';
    if (!textValue) {
      toggleEmpty(true);
      return;
    }

    const fd = new FormData();
    fd.append('text', textValue);
    fd.append('writing_mode', modeSelect ? modeSelect.value : 'horizontal');
    fd.append('p', page);

    try {
      const { resp, data } = await callPreviewAPI(fd);

      if (resp.ok && data.success) {
        current = data.p || 1;
        total = data.total || 1;
        if (textArea) textArea.value = data.page_text || '';
        if (counter) counter.textContent = `${current} / ${total}`;
        if (prev) prev.disabled = current <= 1;
        if (next) next.disabled = current >= total;
        toggleEmpty(false);
        return;
      }

      const msg = (data && data.message) ? data.message : 'プレビューの取得に失敗しました。';
      window.showToast && window.showToast(msg);
      toggleEmpty(true);
    } catch (err) {
      window.showToast && window.showToast('プレビュー取得中にエラーが発生しました。');
      console.error(err);
      toggleEmpty(true);
    }
  }

  prev && prev.addEventListener('click', (e)=>{
    e.preventDefault();
    if (current <= 1) return;
    loadPreview(current - 1);
  });

  next && next.addEventListener('click', (e)=>{
    e.preventDefault();
    if (current >= total) return;
    loadPreview(current + 1);
  });

  refresh && refresh.addEventListener('click', (e)=>{
    e.preventDefault();
    loadPreview(1);
  });

  if (textInput) {
    textInput.addEventListener('change', ()=> loadPreview(1));
  }
  if (modeSelect) {
    modeSelect.addEventListener('change', ()=> loadPreview(current));
  }

  document.addEventListener('DOMContentLoaded', ()=> loadPreview(1));
})();
