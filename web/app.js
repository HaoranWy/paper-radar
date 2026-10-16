'use strict';
const D=window.PAPER_DATA, $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const url=s=>{try{let u=new URL(s);return ['https:','http:'].includes(u.protocol)?u.href:''}catch{return ''}};
const views={all:['论文动态','先看与你的研究相关的，再看来源。'],preprint:['最新预印本','快速发现新方向；未经同行评审的条目不作为正式录用证据。'],published:['已录用 / 已发表','由公开已录用会集或正式出版记录支持；文献类型不明时单独标注。'],strict:['严选对比方法','正式录用 / 发表 + 研究论文 + 有官方开源代码核验依据；不是复现成功保证。'],saved:['我的收藏','收藏仅保存在当前浏览器，清理浏览器数据会丢失；可导出备份。']};
const state={view:'all',topic:'',limit:25};
let saved;
try{saved=new Set(JSON.parse(localStorage.getItem('paper-radar-saved-v1')||'[]'))}catch{saved=new Set()}
const preview=D.meta.mode==='preview';
const formal=p=>['published','accepted'].includes(p.status);
const strict=p=>formal(p)&&p.document_type==='research_article'&&p.code_status==='verified';
function dateLabel(s){
 if(!s)return '未提供';
 if(/^\d{4}$/.test(s))return s+'（仅年）';
 if(/^\d{4}-\d{2}$/.test(s))return s+'（仅年月）';
 return String(s).slice(0,10);
}
function timeLabel(s){
 if(!s)return '尚未成功抓取';
 if(s.length<=10)return dateLabel(s);
 const d=new Date(s);if(isNaN(d))return s;
 try{return new Intl.DateTimeFormat('zh-CN',{timeZone:D.meta.timezone,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}catch{return String(s).slice(0,16)}
}
function timestamp(s){if(!s)return 0;const t=Date.parse(s);return Number.isNaN(t)?0:t}
function notify(message){$('toast').textContent=message;$('toast').style.display='block';clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').style.display='none',2600)}
function storeSaved(){try{localStorage.setItem('paper-radar-saved-v1',JSON.stringify([...saved]))}catch{notify('浏览器不允许持久保存；当前会话仍可收藏。')}}
function badge(text,kind=''){return `<span class="badge ${esc(kind)}">${esc(text)}</span>`}
function compactVenue(p){
 let v=p.venue||p.sources.join(' / '), y=(p.published_at||'').slice(0,4);
 for(const [pattern,name] of [[/international conference on machine learning/i,'ICML'],[/neural information processing/i,'NeurIPS'],[/computational linguistics/i,'ACL'],[/artificial intelligence and statistics/i,'AISTATS']]){
  if(pattern.test(v))return name+(y?' '+y:'');
 }
 return v.length>85?v.slice(0,82)+'…':v;
}
function selectedPapers(){
 const needle=$('search').value.trim().toLocaleLowerCase(), source=$('source').value;
 const days=$('period').value, dateField=$('date-field').value;
 let rows=D.papers.filter(p=>{
  if(state.view==='preprint'&&p.status!=='preprint')return false;
  if(state.view==='published'&&!formal(p))return false;
  if(state.view==='strict'&&!strict(p))return false;
  if(state.view==='saved'&&!saved.has(p.id))return false;
  if(state.topic&&!p.topics.includes(state.topic))return false;
  if(source){let [type,...parts]=source.split(':');let value=parts.join(':');if(!(type==='family'?p.families:p.sources).includes(value))return false}
  if(needle&&!([p.title,p.authors.join(' '),p.abstract,p.venue,...p.matched_terms].join(' ').toLocaleLowerCase().includes(needle)))return false;
  if(days!=='all'){
   let d=p[dateField];
   // Unknown day precision must not silently masquerade as a specific recent day.
   if(!d||d.length<10)return false;
   let t=timestamp(d), now=Date.now();
   if(!t||t<now-Number(days)*86400000||t>now+86400000)return false;
  }
  return true;
 });
 const sort=$('sort').value;
 rows.sort((a,b)=>sort==='new'?timestamp(b.record_changed_at)-timestamp(a.record_changed_at):sort==='published'?timestamp(b.published_at)-timestamp(a.published_at):(b.score-a.score||timestamp(b.record_changed_at)-timestamp(a.record_changed_at)));
 return rows;
}
function renderCard(p){
 const saveText=saved.has(p.id)?'取消收藏':'收藏论文';
 const status=p.status==='preprint'?'预印本':p.status==='accepted'?'已录用':'已发表';
 const shortAbstract=p.abstract?(p.abstract.length>240?p.abstract.slice(0,240)+'…':p.abstract):'来源未提供摘要。可打开原文查看；本条目的相关性可能仅由标题命中。';
 const code=p.code_status==='verified'?badge('官方代码有核验依据','verified'):p.code_links.length?badge('代码链接待核验'):'';
 const links=[['来源原文 ↗',p.url],['PDF ↗',p.pdf_url],...(p.code_links.length?[['代码 ↗',p.code_links[0]]]:[]),...(p.code_evidence?[['核验依据 ↗',p.code_evidence]]:[])].filter(x=>url(x[1]));
 const abstract=p.abstract.length>240?`<div>${esc(shortAbstract)}</div><details><summary>展开来源摘要</summary><div class="abstract-full">${esc(p.abstract)}</div><small>${esc(p.abstract_note)}</small></details>`:`<div>${esc(shortAbstract)}</div>${p.abstract_note&&preview?`<small>${esc(p.abstract_note)}</small>`:''}`;
 return `<article class="paper" data-id="${esc(p.id)}">
  <button class="save ${saved.has(p.id)?'saved':''}" data-save="${esc(p.id)}" aria-label="${saveText}" aria-pressed="${saved.has(p.id)}" title="${saveText}">${saved.has(p.id)?'★':'☆'}</button>
  <div class="paper-head">${badge(compactVenue(p))}${badge(status,p.status==='preprint'?'preprint':'formal')}${p.document_type==='unspecified'?badge('文献类型待确认'):''}${p.document_type==='review'?badge('综述'):''}${code}<span class="score" title="关键词匹配分，不代表质量、创新性或统计概率">匹配分 ${Number(p.score)||0}</span></div>
  <h3><a href="${esc(url(p.url))}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a></h3>
  <div class="authors">${esc(p.authors.slice(0,6).join(' · '))}${p.authors.length>6?' · 等':''}</div>
  <div class="abstract">${abstract}</div>
  <div class="match"><strong>相关线索</strong><span>${esc(p.matched_terms.slice(0,7).join(' / ')||'请核查主题匹配')} ${p.matched_terms.length>7?'…':''}</span></div>
  <div class="paper-bottom"><div class="paper-meta"><span>${p.status==='preprint'?'预印本提交':'来源发表日期'} ${esc(dateLabel(p.published_at))}</span><span>${preview?'样例收录':'首次收录'} ${esc(dateLabel(p.first_seen_at))}</span>${p.preprint_at&&formal(p)?`<span>预印本首发 ${esc(dateLabel(p.preprint_at))}</span>`:''}</div><div class="paper-links">${links.map(([name,link])=>`<a href="${esc(url(link))}" target="_blank" rel="noopener noreferrer">${name}</a>`).join('')}</div></div>
 </article>`;
}
function render(){
 const rows=selectedPapers();
 $('papers').innerHTML=rows.length?rows.slice(0,state.limit).map(renderCard).join(''):`<div class="empty"><strong>这里暂时没有匹配的论文。</strong><p>${state.view==='strict'?'尚未满足正式发表、研究论文和官方代码核验三个条件；不会用未核验记录填充。':'尝试扩大时间范围，或清除主题与来源筛选。'}</p><button class="button" id="clear-filters">清除筛选</button></div>`;
 $('result-count').textContent=`${rows.length} 篇匹配 · 显示 ${Math.min(rows.length,state.limit)} 篇`;
 $('view-title').textContent=views[state.view][0];$('view-help').textContent=views[state.view][1];
 $('breadcrumb-view').textContent=state.view==='all'?'全部论文':views[state.view][0];
 $('nav-saved').textContent=D.papers.filter(p=>saved.has(p.id)).length;
 $('load-more').style.display=rows.length>state.limit?'inline-block':'none';
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
 document.querySelectorAll('[data-topic]').forEach(b=>b.classList.toggle('active',b.dataset.topic===state.topic));
 const clear=$('clear-filters');if(clear)clear.onclick=()=>{state.topic='';$('search').value='';$('source').value='';$('period').value='all';render()};
}
function showHealth(){
 const labels={ok:'本次完成',error:'抓取失败',partial:'部分完成',unavailable:'待公开 / 待核对',untested:'尚未在线实测'};
 $('health-list').innerHTML=Object.entries(D.meta.sources).map(([name,s])=>`<div class="health-row"><strong>${esc(name)}</strong><span class="health-status ${esc(s.status)}">${esc(labels[s.status]||s.status)}</span><small>最近完整成功：${esc(timeLabel(s.last_success_at))} · 本次读取 ${Number(s.fetched)||0} 条元数据 · 本次新增 ${Number(s.added)||0} 条相关记录<br>${esc(s.note||'无额外说明')}</small></div>`).join('')||'<p>尚未运行采集程序。</p>';
 $('health-dialog').showModal();
}
$('health-button').onclick=showHealth;$('mode-badge').onclick=showHealth;$('close-health').onclick=()=>$('health-dialog').close();
$('health-dialog').addEventListener('click',event=>{if(event.target===$('health-dialog')){let r=$('health-dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('health-dialog').close()}});
document.addEventListener('click',event=>{
 const save=event.target.closest('[data-save]');if(save){let id=save.dataset.save;saved.has(id)?saved.delete(id):saved.add(id);storeSaved();render();return}
 const view=event.target.closest('[data-view]');if(view){state.view=view.dataset.view;state.limit=25;render();return}
 const topic=event.target.closest('[data-topic]');if(topic){state.topic=topic.dataset.topic;state.limit=25;render()}
});
for(const id of ['source','period','sort','date-field'])$(id).onchange=()=>{state.limit=25;render()};
$('search').oninput=()=>{state.limit=25;render()};$('load-more').onclick=()=>{state.limit+=25;render()};
$('export').onclick=()=>{
 const rows=selectedPapers();const payload={exported_at:new Date().toISOString(),mode:D.meta.mode,papers:rows,bookmarked_ids:[...saved]};
 const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'});const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=`paper-radar-${new Date().toISOString().slice(0,10)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);notify(`已导出 ${rows.length} 篇论文`);
};
$('side-topics').innerHTML=D.topics.map(t=>`<button class="side-topic" data-topic="${esc(t.id)}">${esc(t.label)}</button>`).join('');
$('topic-chips').innerHTML=`<button class="chip active" data-topic="">全部主题</button>`+D.topics.map(t=>`<button class="chip" data-topic="${esc(t.id)}">${esc(t.label)}</button>`).join('');
const families=['arXiv','Nature','Science','会议'];
$('source').innerHTML='<option value="">全部来源</option><optgroup label="来源系列">'+families.map(f=>`<option value="family:${esc(f)}">${esc(f==='Nature'||f==='Science'?f+' 系列':f)}</option>`).join('')+'</optgroup><optgroup label="具体来源">'+[...new Set(D.papers.flatMap(p=>p.sources))].sort().map(s=>`<option value="source:${esc(s)}">${esc(s)}</option>`).join('')+'</optgroup>';
$('subtitle').textContent=D.meta.subtitle;$('timezone').textContent=D.meta.timezone;
$('nav-all').textContent=D.papers.length;$('stat-total').textContent=D.papers.length;
$('stat-new').textContent=preview?'—':D.meta.last_run.added??'—';
$('stat-published').textContent=D.papers.filter(formal).length;$('stat-code').textContent=D.papers.filter(p=>p.code_status==='verified').length;
$('updated').textContent=timeLabel(preview?D.meta.last_run.checked_at:D.meta.last_run.finished_at);$('update-label').textContent=preview?'人工样例核对日期':'最近采集运行结束';
const health=Object.values(D.meta.sources),bad=health.filter(s=>s.status==='error').length,partial=health.filter(s=>s.status==='partial').length;
$('mode-badge').textContent=preview?'离线样例 · 来源状态 ↗':`来源状态 ${bad?'⚠':'↗'}`;
if(preview){$('notice').textContent='当前是离线预览：仅展示少量已核对来源的真实论文样例，不是今日全量抓取结果。每日任务尚未部署；示例中文说明为人工整理，实时采集保留来源摘要。'}
else if(!D.meta.last_run.finished_at){$('notice').textContent='尚未采集数据。请运行 python run.py 或启用 GitHub Actions；重新打开网页不会触发抓取。'}
else if(Date.now()-timestamp(D.meta.last_run.finished_at)>36*3600000){$('notice').classList.add('error');$('notice').textContent='超过 36 小时没有新的采集运行记录。页面仍显示上次数据，请检查定时任务。'}
else if(bad||partial){$('notice').classList.add('error');$('notice').textContent=`最近运行有 ${bad} 个来源失败、${partial} 个来源部分完成。已保留旧数据，请查看来源状态；不能视为全量更新成功。`}
else{$('notice').classList.add('live');$('notice').textContent='已完成最近一次采集。公开会集尚未发布的来源会单独标注；每日检查不代表每日都有新论文。'}
$('footer-meta').textContent=`页面构建 ${timeLabel(D.meta.generated_at)} · ${D.meta.display_truncated?'当前网页已截断，更多历史记录在数据文件中':'所有摘要和状态均附来源链接'} · 不自动下载付费全文`;
render();
