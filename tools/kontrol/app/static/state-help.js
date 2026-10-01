(() => {
 const node=document.getElementById('state-help'); if(!node)return;
 const data=JSON.parse(node.textContent),pop=document.createElement('div');
 pop.id='state-popover';pop.className='state-popover';pop.setAttribute('role','tooltip');pop.hidden=true;
 document.body.append(pop);let owner=null,timer;
 const hide=()=>{clearTimeout(timer);if(owner){owner.removeAttribute('aria-describedby');owner.setAttribute('aria-expanded','false');}owner=null;pop.hidden=true;};
 const place=()=>{if(!owner)return;const a=owner.getBoundingClientRect(),p=pop.getBoundingClientRect();const top=a.bottom+8+p.height<=innerHeight?a.bottom+8:Math.max(8,a.top-p.height-8);pop.style.left=Math.max(8,Math.min(a.left,document.documentElement.clientWidth-p.width-8))+'px';pop.style.top=top+'px';};
 const show=b=>{clearTimeout(timer);const id=b.dataset.help,h=id.startsWith('row-')?data.rows[+id.slice(4)]:data.columns[id];if(!h)return;if(owner&&owner!==b)hide();owner=b;pop.replaceChildren();
  const title=document.createElement('strong');title.textContent=h.title;pop.append(title);
  for(const text of [h.parent?'Категория: '+h.parent:null,h.text,h.example,...(h.checks||[])]){if(!text)continue;const p=document.createElement('p');p.textContent=text;pop.append(p);}
  const link=document.createElement('a');link.textContent='Оригинален източник ↗';link.href=h.source||'/sources';link.rel='noopener';pop.append(link);
  const close=document.createElement('button');close.type='button';close.className='help-close';close.textContent='Затвори';close.addEventListener('click',hide);pop.append(close);
  pop.hidden=false;b.setAttribute('aria-describedby',pop.id);b.setAttribute('aria-expanded','true');place();
 };
 const delay=()=>{clearTimeout(timer);timer=setTimeout(hide,180);};
 document.querySelectorAll('.help-trigger').forEach(b=>{b.setAttribute('aria-expanded','false');b.addEventListener('mouseenter',()=>show(b));b.addEventListener('mouseleave',delay);b.addEventListener('focus',()=>show(b));b.addEventListener('click',()=>show(b));b.addEventListener('blur',e=>{if(!pop.contains(e.relatedTarget))delay();});});
 pop.addEventListener('mouseenter',()=>clearTimeout(timer));pop.addEventListener('mouseleave',delay);pop.addEventListener('focusin',()=>clearTimeout(timer));pop.addEventListener('focusout',e=>{if(!pop.contains(e.relatedTarget)&&e.relatedTarget!==owner)delay();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});document.addEventListener('pointerdown',e=>{if(!pop.contains(e.target)&&e.target!==owner&&!e.target.closest('.help-trigger'))hide();});
 addEventListener('resize',place);addEventListener('scroll',()=>{if(owner)place();},true);
})();
