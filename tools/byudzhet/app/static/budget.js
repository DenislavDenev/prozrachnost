// The same form parameters feed the page, CSV and JSON API.
(() => {
 const form=document.getElementById('filters');
 if(form){
  const ob=form.elements.oblast,mun=form.elements.municipality;
  const all=[...mun.options].slice(1).map(o=>o.cloneNode(true));
  const limit=(reset=false)=>{const value=reset?'':mun.value;mun.replaceChildren(new Option('Всички',''),...all.filter(o=>!ob.value||o.dataset.oblast===ob.value).map(o=>o.cloneNode(true)));mun.value=[...mun.options].some(o=>o.value===value)?value:'';};
  limit();ob.addEventListener('change',()=>{limit(true);form.requestSubmit();});
  form.querySelectorAll('select').forEach(el=>{if(el!==ob)el.addEventListener('change',()=>form.requestSubmit());});
  document.querySelectorAll('[data-sort]').forEach(b=>b.addEventListener('click',()=>{form.elements.direction.value=form.elements.sort.value===b.dataset.sort&&form.elements.direction.value==='desc'?'asc':'desc';form.elements.sort.value=b.dataset.sort;form.requestSubmit();}));
 }
 document.querySelectorAll('.budget-sort [data-column]').forEach(b=>b.addEventListener('click',()=>{
  const table=b.closest('table'),column=+b.dataset.column,desc=b.dataset.direction!=='desc';b.dataset.direction=desc?'desc':'asc';
  const key=r=>{const cell=r.cells[column],s=(cell.dataset.v??cell.textContent).trim();if(!s||s==='няма данни')return null;const n=Number(s.replaceAll(' ','').replace(',','.')); return Number.isNaN(n)?s:n;};
  const rows=[...table.tBodies[0].rows];rows.sort((a,z)=>{const x=key(a),y=key(z);if(x===null)return 1;if(y===null)return -1;return (typeof x==='number'?x-y:String(x).localeCompare(String(y),'bg'))*(desc?-1:1);});table.tBodies[0].append(...rows);
 }));
})();
