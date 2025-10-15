
(function(){
  function setActiveByCenter(){
    const mid = window.innerHeight/2;
    let best=null, bestDist=Infinity;
    document.querySelectorAll('.page').forEach(sec=>{
      const r = sec.getBoundingClientRect();
      const d = Math.abs((r.top + r.height/2) - mid);
      if (d < bestDist){ bestDist=d; best=sec; }
    });
    if (best){
      const idx = best.dataset.index;
      // pager highlight
      document.querySelectorAll('.bottom-pager .page-number').forEach(a=>{
        a.classList.toggle('active', a.dataset.page===idx);
      });
      // update URL ?p=idx (without page reload)
      const url = new URL(window.location.href);
      if (url.searchParams.get('p') !== String(idx)) {
        url.searchParams.set('p', idx);
        history.replaceState(null, '', url.toString());
      }
    }
  }
  function gotoByHashOrQuery(){
    // hash (#N or #page-N) takes precedence, else use ?p=N
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
      if (el){ el.scrollIntoView({ behavior: 'instant' in window ? 'instant' : 'smooth', block: 'start' }); }
    }
  }
  window.addEventListener('scroll', ()=>{ window.requestAnimationFrame(setActiveByCenter); }, { passive:true });
  window.addEventListener('hashchange', gotoByHashOrQuery);
  document.addEventListener('DOMContentLoaded', ()=>{ setActiveByCenter(); gotoByHashOrQuery(); });
})();


/* inserter */
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
