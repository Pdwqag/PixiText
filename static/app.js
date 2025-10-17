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
