const $ = id => document.getElementById(id);
const keyStore = 'lightpanda_goods_web_api_key';
const apiUrlStore = 'lightpanda_goods_web_api_url';
const ruleStore = 'goods_filter_rules_v1';
const salesRuleStore = 'goods_sales_rule_v1';
const names = {filter:'商品过滤', sales:'销量查询', settings:'设置'};
const descriptions = {filter:'按规则筛选商品，查看每个批次的结果。',sales:'基于已拼显示统计销量，按批次查看完整明细。',settings:'管理过滤规则、销量阈值和 API 连接。'};
const state = {key:localStorage.getItem(keyStore)||'', apiUrl:localStorage.getItem(apiUrlStore)||'', authenticated:false, statusBusy:false, querying:false, queryingType:null, page:'filter', filterView:'eligible', batches:{filter:[],sales:[]}, detail:{filter:null,sales:null}, rules:{sales:20,price:500}, salesThreshold:10, modalDismissed:false};

function textNode(tag, value, className='') { const e=document.createElement(tag); e.textContent=String(value ?? ''); if(className)e.className=className; return e; }
function message(type, value) { $(type+'Message').textContent=value||''; }
function detailMessage(data) { const d=data?.detail; return typeof d==='string'?d:d?.message||'请求失败'; }
function inputIds(type) { return [...new Set($(type+'Ids').value.trim().split(/[\s,;，；]+/).filter(Boolean))]; }
function rawInputIds(type) { return $(type+'Ids').value.trim().split(/[\s,;，；]+/).filter(Boolean); }
function removeChineseLines(type) {
  const field=$(type+'Ids'),lines=field.value.split(/\r?\n/),kept=lines.filter(line=>!/\p{Script=Han}/u.test(line));
  const removed=lines.length-kept.length;
  if(removed){field.value=kept.join('\n');refreshInputStats(type);}
  return removed;
}
function refreshInputStats(type) { const raw=rawInputIds(type),unique=new Set(raw);$(type+'RawCount').textContent=raw.length;$(type+'UniqueCount').textContent=unique.size;$(type+'DuplicateCount').textContent=raw.length-unique.size;$(type+'Count').textContent=`${unique.size} 个 ID`; }
function formatPrice(goods) { const raw=goods?.priceYuan ?? (typeof goods?.price==='number'?goods.price/100:null); const n=Number(raw); return raw!==null&&Number.isFinite(n)?`¥${n.toFixed(2)}`:'—'; }
function numberOrDash(value) { return value===null||value===undefined||value===''?'—':String(value); }
function localDate(value) { try {return new Date(value).toLocaleString('zh-CN',{hour12:false});}catch{return value||'';} }
function validLink(value) { try {const u=new URL(value); return ['http:','https:'].includes(u.protocol)?u.href:null;}catch{return null;} }
function normalizeApiUrl(value) {
  const raw=value.trim();if(!raw)return '';
  let url;try{url=new URL(raw);}catch{throw new Error('API 地址格式不正确');}
  if(!['http:','https:'].includes(url.protocol)||!url.hostname||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('API 地址须为 http(s)://主机[:端口]，不能包含路径、参数或账号');
  return url.origin;
}
function defaultApiUrl() {const url=new URL(location.href);url.port='8766';return url.origin;}
function updateAdminLink() { $('adminLink').href=(state.apiUrl||defaultApiUrl())+'/admin'; }
function getRules() { try { const saved=JSON.parse(localStorage.getItem(ruleStore)||'{}'); const sales=Number(saved.sales),price=Number(saved.price); if(Number.isInteger(sales)&&sales>=0&&Number.isFinite(price)&&price>=0)return {sales,price}; }catch{} return {sales:20,price:500}; }
function updateRuleUI() { $('salesThreshold').value=state.rules.sales; $('priceThreshold').value=state.rules.price; $('ruleChip').textContent=`销量 > ${state.rules.sales} 或 价格 > ¥${state.rules.price}`; }
function getSalesThreshold() { const raw=localStorage.getItem(salesRuleStore);const value=Number(raw); return raw!==null&&Number.isInteger(value)&&value>=0?value:10; }
function updateSalesRuleUI() { $('querySalesThreshold').value=state.salesThreshold; $('salesRuleText').textContent=`所有批次按“已拼显示”统计：> ${state.salesThreshold} 已出，≤ ${state.salesThreshold} 或空未出，库存为 0 售罄。`;$('salesSettingsSaved').textContent=`当前规则：销量 > ${state.salesThreshold} 算已出`; }

function setPage(page) {
  if(!names[page])return;
  state.page=page;
  document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id==='page-'+page));
  $('crumb').textContent=names[page]; $('pageTitle').textContent=names[page]; $('pageDescription').textContent=descriptions[page];
  history.replaceState(null,'','#'+page);
}
function showWorkspace(type){$(type+'Workspace').classList.remove('detail-open');$(type+'Detail').hidden=true;}
function setConnection(label, tone='') {
  $('authBadge').textContent=label; $('authBadge').className='pill '+tone;
  $('statusDot').className='status-dot '+tone; $('connectionText').textContent=label;
  $('loginAction').hidden=false;
  $('logoutAction').hidden=false;
  $('logoutAction').disabled=!state.authenticated;
  for(const type of ['filter','sales']) $(type+'Run').disabled=!state.authenticated||state.querying;
}
async function callApi(path,options={}) {
  if(!state.key)throw new Error('请先在设置中填写 API Key');
  const response=await fetch(path,{...options,headers:{'X-API-Key':state.key,...(state.apiUrl?{'X-API-Base-URL':state.apiUrl}:{}),...options.headers}});
  let data;try{data=await response.json();}catch{throw new Error('服务器返回了无法读取的内容');}
  if(!response.ok){const error=new Error(detailMessage(data));error.status=response.status;throw error;}
  return data;
}
function updateShows(shows) {
  for(const type of ['filter','sales']) {
    const select=$(type+'Show'),old=select.value;
    select.replaceChildren();
    if(!shows?.length){select.append(new Option('当前无可编辑直播场次',''));continue;}
    for(const show of shows)select.append(new Option(`${show.title||show.showId} · ${show.time||''}`,show.showId));
    if([...select.options].some(o=>o.value===old))select.value=old;
  }
}
async function refreshStatus() {
  if(state.statusBusy||state.querying||!state.key)return;
  state.statusBusy=true;
  try {
    const data=await callApi('/api/status');
    state.authenticated=!!data.authenticated;
    setConnection(state.authenticated?'商家已登录':'等待商家扫码',state.authenticated?'ok':'warn');
    if(state.authenticated){$('loginModal').classList.add('hidden');state.modalDismissed=false;updateShows(data.shows||[]);}
    else {updateShows([]);if(!state.modalDismissed)$('loginModal').classList.remove('hidden');setQr(data.qrImage);}
  } catch(error) {
    state.authenticated=false;setConnection(error.status===401?'API Key 无效':'API 连接失败','warn');
    message('key',error.message);
  } finally {state.statusBusy=false;}
}
function setQr(image) { $('qrImage').hidden=!image; $('qrLoading').hidden=!!image; if(image)$('qrImage').src=image;else $('qrLoading').textContent='正在获取二维码…'; }
async function refreshQr() { $('refreshQr').disabled=true;try {const data=await callApi('/api/login',{method:'POST'});setQr(data.qrImage);await refreshStatus();}catch(error){$('qrLoading').hidden=false;$('qrLoading').textContent=error.message;message('key',error.message);}finally{$('refreshQr').disabled=false;} }
async function openLogin() {if(!state.key){setPage('settings');message('key','请先填写 API Key');return;}state.modalDismissed=false;$('loginModal').classList.remove('hidden');setQr(null);await refreshQr();}
async function logoutMerchant() {const button=$('logoutAction');button.disabled=true;try {await callApi('/api/logout',{method:'POST'});state.authenticated=false;state.modalDismissed=true;$('loginModal').classList.add('hidden');updateShows([]);setConnection('已退出登录','warn');message('key','商家会话已退出。');}catch(error){message('key',error.message);}finally{button.disabled=false;}}

function classification(result) {
  const goods=new Map((result.goodsList||[]).map(g=>[String(g.goodsId),g]));
  const failures=new Map((result.failures||[]).map(f=>[String(f.goodsId),f]));
  const ids=result.requestedGoodsIds?.map(String)||[...goods.keys(),...failures.keys()];
  return ids.map((id,index)=>({id,index:index+1,goods:goods.get(id)||null,failure:failures.get(id)||null}));
}
function displayedSales(goods) {
  const tip=String(goods?.soldQuantityTip??'').trim();
  if(!tip)return {value:null,tip:'',invalid:false};
  const match=tip.replace(/[\s,]/g,'').match(/(?:已拼|已抢|已售)(\d+(?:\.\d+)?)(万|亿)?/);
  if(!match)return {value:null,tip,invalid:true};
  const multiplier=match[2]==='亿'?100000000:match[2]==='万'?10000:1;
  const value=Math.trunc(Number(match[1])*multiplier);
  return Number.isFinite(value)?{value,tip,invalid:false}:{value:null,tip,invalid:true};
}
function salesStatus(item, threshold=10) {
  const g=item.goods;
  if(item.failure||!g)return {label:'未知',tone:'unknown',key:'unknown'};
  if(typeof g.stock==='number'&&g.stock<=0)return {label:'售罄',tone:'soldout',key:'sold_out'};
  const sales=displayedSales(g);
  if(sales.invalid)return {label:'未知',tone:'unknown',key:'unknown'};
  if(sales.value!==null&&sales.value>threshold)return {label:'已出',tone:'',key:'sold'};
  return {label:'未出',tone:'unsold',key:'unsold'};
}
function filterGroups(items) {
  const groups={eligible:[],overSales:[],overPrice:[],unknown:[]};
  for(const item of items){
    const g=item.goods;
    if(!g||item.failure){groups.unknown.push(item);continue;}
    const sales=displayedSales(g),sold=!sales.invalid;
    const price=typeof g.price==='number'&&Number.isFinite(g.price);
    const overSales=sold&&sales.value!==null&&sales.value>state.rules.sales;
    const overPrice=price&&g.price/100>state.rules.price;
    if(overSales)groups.overSales.push(item);
    if(overPrice)groups.overPrice.push(item);
    if(!overSales&&!overPrice){if(sold&&price)groups.eligible.push(item);else groups.unknown.push(item);}
  }
  return groups;
}
function addStat(container,label,value,highlight=false){const card=textNode('div','',`stat${highlight?' highlight':''}`);card.append(textNode('span',label),textNode('strong',value));container.append(card);}
function renderOverview(type){
  const host=$(type+'Overview');host.replaceChildren();
  const batch=state.detail[type]?.batch||state.batches[type][0];
  if(!batch){for(const label of type==='filter'?['总商品数量','符合条件','超销 / 超价','待核实']:['总商品数量','已出','未知','出销率'])addStat(host,label,'—');return;}
  if(type==='sales'){
    addStat(host,'总商品数量',batch.total);addStat(host,'已出',batch.sold,true);addStat(host,'未知',batch.unknown);addStat(host,'出销率',batch.total?`${(batch.sold/batch.total*100).toFixed(2)}%`:'0.00%');
  }else if(state.detail.filter){
    const items=classification(state.detail.filter.result),groups=filterGroups(items);
    addStat(host,'总商品数量',items.length);addStat(host,'符合条件',groups.eligible.length,true);addStat(host,'超销 / 超价',new Set([...groups.overSales,...groups.overPrice].map(item=>item.id)).size);addStat(host,'待核实',groups.unknown.length);
  }else{addStat(host,'总商品数量',batch.total);for(const label of ['符合条件','超销 / 超价','待核实'])addStat(host,label,'—');}
}
function batchPlaceholder(type,value){const host=$(type+'Batches');host.replaceChildren();const row=document.createElement('tr'),cell=textNode('td',value,'empty-small');cell.colSpan=type==='sales'?10:6;row.append(cell);host.append(row);}
function renderFilterBatchRow(batch){
  const row=document.createElement('tr');row.className=`batch-data-row${state.detail.filter?.batch.id===batch.id?' selected':''}`;
  const first=document.createElement('td'),identity=textNode('div','','batch-identity');identity.append(textNode('strong',batch.name),textNode('small',`批次 #${batch.id}`));first.append(identity);row.append(first);
  addCell(row,batch.total,'numeric');addCell(row,batch.show_id||'未指定');addCell(row,localDate(batch.created_at));
  const status=document.createElement('td');status.append(textNode('span','已完成','status-pill complete'));row.append(status);
  const action=document.createElement('td'),actions=textNode('div','','row-actions'),open=textNode('button','查看详情 ›','table-action'),remove=textNode('button','删除','table-action delete-action');open.onclick=()=>openBatch('filter',batch.id);remove.onclick=()=>deleteBatch('filter',batch);actions.append(open,remove);action.append(actions);row.append(action);
  row.onclick=event=>{if(!event.target.closest('button'))openBatch('filter',batch.id);};return row;
}
function renderSalesBatchRow(batch){
  const row=document.createElement('tr');row.className=`batch-data-row${state.detail.sales?.batch.id===batch.id?' selected':''}`;
  const first=document.createElement('td'),identity=textNode('div','','batch-identity');identity.append(textNode('strong',batch.name),textNode('small',`${localDate(batch.created_at)} · ${batch.show_id||'未指定场次'}`));first.append(identity);row.append(first);
  addCell(row,batch.total,'numeric');
  const progress=document.createElement('td'),progressWrap=textNode('div','','progress-cell');progressWrap.append(textNode('span',`${batch.total}/${batch.total}`));const track=textNode('div','','progress-track'),fill=textNode('span','','progress-fill');track.append(fill);progressWrap.append(track);progress.append(progressWrap);row.append(progress);
  addCell(row,batch.sold,'numeric positive');addCell(row,batch.unsold,'numeric');addCell(row,batch.sold_out,'numeric');addCell(row,batch.unknown,'numeric');
  addCell(row,batch.total?`${(batch.sold/batch.total*100).toFixed(2)}%`:'0.00%','numeric rate');
  const status=document.createElement('td');status.append(textNode('span','已完成','status-pill complete'));row.append(status);
  const action=document.createElement('td'),actions=textNode('div','','row-actions'),button=textNode('button','查看详情 ↗','table-action'),remove=textNode('button','删除','table-action delete-action');button.onclick=()=>openBatch('sales',batch.id);remove.onclick=()=>deleteBatch('sales',batch);actions.append(button,remove);action.append(actions);row.append(action);
  row.onclick=event=>{if(!event.target.closest('button'))openBatch('sales',batch.id);};return row;
}
function renderBatchList(type) {
  const host=$(type+'Batches');host.replaceChildren();
  const batches=state.batches[type];
  if(type==='sales'){
    if(state.queryingType==='sales'){
      const row=document.createElement('tr');row.className='batch-data-row pending';const first=textNode('td','','batch-identity');first.append(textNode('strong',$('salesName').value.trim()||'正在查询的新批次'),textNode('small','正在从接口获取商品信息'));row.append(first);
      const cell=textNode('td','查询中…','pending-cell');cell.colSpan=7;row.append(cell);const status=document.createElement('td');status.append(textNode('span','进行中','status-pill working'));row.append(status,textNode('td','请稍候'));host.append(row);
    }
    for(const batch of batches)host.append(renderSalesBatchRow(batch));
    if(!host.children.length)batchPlaceholder(type,'暂无销量批次。左侧提交商品 ID 后会显示在这里。');
    return;
  }
  if(!batches.length){batchPlaceholder(type,'暂无过滤批次。左侧提交商品 ID 后会显示在这里。');return;}
  for(const batch of batches)host.append(renderFilterBatchRow(batch));
}
async function deleteBatch(type,batch){
  if(!confirm(`确定删除“${batch.name}”批次？删除后无法恢复。`))return;
  try{
    await callApi('/api/batches/'+batch.id,{method:'DELETE'});
    if(state.detail[type]?.batch.id===batch.id){state.detail[type]=null;showWorkspace(type);}
    state.batches[type]=state.batches[type].filter(item=>item.id!==batch.id);
    renderBatchList(type);renderOverview(type);message(type,'已删除批次。');
  }catch(error){message(type,error.message);}
}
async function clearAllBatches(type){
  if(!confirm('确定清空商品过滤和销量查询的所有批次？此操作无法恢复。'))return;
  try{
    const data=await callApi('/api/batches',{method:'DELETE'});
    for(const taskType of ['filter','sales']){state.detail[taskType]=null;state.batches[taskType]=[];showWorkspace(taskType);renderBatchList(taskType);renderOverview(taskType);}
    message(type,`已清空 ${data.deleted} 个批次。`);
  }catch(error){message(type,error.message);}
}
async function loadBatches(type) {
  if(!state.key)return;
  try { const data=await callApi('/api/batches?taskType='+type);state.batches[type]=data.batches||[];renderBatchList(type);renderOverview(type);if(!state.detail[type]&&state.batches[type].length)await openBatch(type,state.batches[type][0].id,false); }
  catch(error){batchPlaceholder(type,error.message);renderOverview(type);}
}
async function syncSalesThreshold(){
  if(!state.key)return null;
  return callApi('/api/sales-threshold',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({salesThreshold:state.salesThreshold})});
}
async function openBatch(type,id,scroll=true) {
  try { const data=await callApi('/api/batches/'+id);if(data.batch.task_type!==type)throw new Error('批次类型不匹配');state.detail[type]=data;renderBatchList(type);renderDetail(type);renderOverview(type);$(type+'Workspace').classList.add('detail-open');if(scroll)$(type+'Detail').scrollIntoView({behavior:'smooth',block:'start'}); }
  catch(error){message(type,error.message);}
}
function renderDetail(type) {
  const data=state.detail[type];if(!data)return;
  const {batch,result}=data,items=classification(result),stats=$(type+'Stats');
  $(type+'Detail').hidden=false;$(type+'DetailTitle').replaceChildren(textNode('span','3','step-number'),document.createTextNode(batch.name));
  $(type+'DetailMeta').textContent=`${localDate(batch.created_at)} · 场次 ${batch.show_id||'未指定'} · 成功 ${result.successCount??0} · 失败 ${result.failureCount??0}${type==='sales'?` · 已出阈值 > ${batch.sales_threshold??10}`:''}`;
  stats.replaceChildren();
  if(type==='sales'){
    const threshold=batch.sales_threshold??10;
    addStat(stats,'总商品数量',batch.total);addStat(stats,`已出（销量 > ${threshold}）`,batch.sold,true);addStat(stats,`未出（≤ ${threshold} / 空）`,batch.unsold);addStat(stats,'售罄',batch.sold_out);addStat(stats,'未知',batch.unknown);addStat(stats,'出销率',batch.total?`${(batch.sold/batch.total*100).toFixed(2)}%`:'0.00%');
    renderRows('sales',items);
  } else {
    const groups=filterGroups(items),excluded=new Set([...groups.overSales,...groups.overPrice].map(x=>x.id));
    addStat(stats,'总商品数量',items.length);addStat(stats,'符合条件',groups.eligible.length,true);addStat(stats,'已过滤',excluded.size);addStat(stats,'待核实',groups.unknown.length);
    document.querySelectorAll('#filterTabs .tab').forEach(tab=>{tab.classList.toggle('active',tab.dataset.view===state.filterView);tab.querySelector('span').textContent=groups[tab.dataset.view].length;});
    renderRows('filter',groups[state.filterView]);
  }
}
function addCell(row,value,className=''){const cell=textNode('td',value,className);row.append(cell);return cell;}
function appendProduct(row,item) {
  const cell=document.createElement('td'),wrap=textNode('div','','product-cell'),g=item.goods;
  const image=validLink(g?.image);
  if(image){const img=document.createElement('img');img.src=image;img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';img.className='product-image';img.onerror=()=>img.replaceWith(textNode('span','无图','product-image-fallback'));wrap.append(img);}
  else wrap.append(textNode('span','无图','product-image-fallback'));
  const desc=textNode('div','','product-text');desc.append(textNode('strong',g?.title||'商品信息未返回'));wrap.append(desc);cell.append(wrap);row.append(cell);
}
function productLink(item) {return /^\d{10,20}$/.test(item.id)?`https://mobile.yangkeduo.com/goods.html?goods_id=${encodeURIComponent(item.id)}`:validLink(item.goods?.goodsLink);}
function appendLink(row,item) {const cell=document.createElement('td'),url=productLink(item);if(url){const a=textNode('a','打开商品 ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';cell.append(a);}else cell.textContent='—';row.append(cell);}
function rawRow(item,colspan) {
  const row=document.createElement('tr');row.className='raw-row';row.hidden=true;const cell=document.createElement('td');cell.colSpan=colspan;
  const dl=document.createElement('dl');
  const fields=[['商品 ID',item.id],['已拼显示',item.goods?.soldQuantityTip],['价格显示',item.goods?.priceTip],['库存显示',item.goods?.stockTip],['失败原因',item.failure?.reason||item.goods?.reason],['数据来源',state.detail[state.page]?.result?.source]];
  for(const [label,value] of fields){const div=document.createElement('div');div.append(textNode('dt',label),textNode('dd',numberOrDash(value)));dl.append(div);}
  const caption=textNode('div','接口返回的完整字段','eyebrow');const pre=textNode('pre',JSON.stringify(item.goods||item.failure||{goodsId:item.id},null,2));cell.append(dl,caption,pre);row.append(cell);return row;
}
function renderRows(type,items){
  const host=$(type+'Rows');host.replaceChildren();if(type==='filter')$('filterEmpty').hidden=items.length>0;
  for(const item of items){
    const row=document.createElement('tr');addCell(row,item.index);addCell(row,item.id,'numeric');appendProduct(row,item);
    const sales=displayedSales(item.goods),salesCell=addCell(row,'','sales-cell');
    salesCell.append(textNode('strong',numberOrDash(sales.value),'sales-value'));
    if(sales.tip)salesCell.append(textNode('small',sales.tip,'sales-source'));
    addCell(row,item.goods?formatPrice(item.goods):'—','numeric');
    if(type==='filter')addCell(row,numberOrDash(item.goods?.stock));
    const status=type==='sales'?salesStatus(item,state.detail.sales?.batch.sales_threshold??10):item.failure||!item.goods?{label:'查询失败',tone:'unknown'}:state.filterView==='eligible'?{label:'符合条件',tone:''}:state.filterView==='overSales'?{label:'超销',tone:'over'}:state.filterView==='overPrice'?{label:'超价',tone:'over'}:{label:'待核实',tone:'unknown'};
    const statusCell=document.createElement('td');statusCell.append(textNode('span',status.label,'status '+status.tone));row.append(statusCell);appendLink(row,item);
    const button=textNode('button','查看字段','detail-toggle'),cell=document.createElement('td'),raw=rawRow(item,type==='filter'?9:8);button.onclick=()=>{raw.hidden=!raw.hidden;button.textContent=raw.hidden?'查看字段':'收起字段';};cell.append(button);row.append(cell);host.append(row,raw);
  }
}

async function runQuery(type){
  const removed=removeChineseLines(type),ids=inputIds(type),show=$(type+'Show').value;
  const cleaned=removed?`已移除 ${removed} 行含中文的内容。`:'';
  if(!state.key){message(type,'请先在设置中填写 API Key');setPage('settings');return;}
  if(!state.authenticated){message(type,'商家尚未登录，请先扫码登录');$('loginModal').classList.remove('hidden');return;}
  if(!show){message(type,'当前没有可编辑的直播场次');return;}
  if(!ids.length){message(type,`${cleaned}请至少输入 1 个商品 ID`);return;}
  const invalid=ids.find(id=>!/^\d{10,20}$/.test(id));if(invalid){message(type,`${cleaned}商品 ID 格式不正确：${invalid}`);return;}
  state.querying=true;state.queryingType=type;setConnection('正在查询…','warn');message(type,`${cleaned}正在请求接口，商品较多时需要稍候…`);if(type==='sales')renderBatchList('sales');
  try {
    const result=await callApi('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goodsIds:ids,showId:show,batchName:$(type+'Name').value.trim(),taskType:type,salesThreshold:type==='sales'?state.salesThreshold:10})});
    state.queryingType=null;message(type,`${cleaned}查询完成，共处理 ${result.batchCount||1} 批，已保存为${names[type]}批次。`);await loadBatches(type);await openBatch(type,result.batchId);
  } catch(error){message(type,error.message);if(error.status===409){state.authenticated=false;state.modalDismissed=false;$('loginModal').classList.remove('hidden');refreshQr();}if(error.status===401){state.authenticated=false;}}
  finally {state.querying=false;state.queryingType=null;if(type==='sales')renderBatchList('sales');setConnection(state.authenticated?'商家已登录':'请检查连接',state.authenticated?'ok':'warn');}
}
async function exportXlsx(type){
  const data=state.detail[type];if(!data)return;
  const params=new URLSearchParams();
  if(type==='filter'){params.set('view',state.filterView);params.set('filterSales',String(state.rules.sales));params.set('filterPrice',String(state.rules.price));}
  try{
    const response=await fetch(`/api/batches/${data.batch.id}/export?${params}`,{headers:{'X-API-Key':state.key,...(state.apiUrl?{'X-API-Base-URL':state.apiUrl}:{})}});
    if(!response.ok){const error=await response.json();throw new Error(detailMessage(error));}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    const encodedName=response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    link.href=url;link.download=encodedName?decodeURIComponent(encodedName):`${names[type]}_${data.batch.id}.xlsx`;link.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
  }catch(error){message(type,error.message);}
}
async function copyFilterIds(view){
  const data=state.detail.filter;if(!data)return;
  const ids=filterGroups(classification(data.result))[view].map(item=>item.id);
  try{
    const value=ids.join('\n');
    if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(value);
    else{const field=document.createElement('textarea');field.value=value;field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();const copied=document.execCommand('copy');field.remove();if(!copied)throw new Error('复制失败');}
    $('copyFeedback').textContent=`已复制 ${ids.length} 个商品 ID`;
  }
  catch{$('copyFeedback').textContent='复制失败，请检查浏览器剪贴板权限';}
}

function initialize(){
  state.rules=getRules();state.salesThreshold=getSalesThreshold();updateRuleUI();updateSalesRuleUI();$('apiKey').value=state.key;
  try{state.apiUrl=normalizeApiUrl(state.apiUrl);}catch{state.apiUrl='';localStorage.removeItem(apiUrlStore);}
  $('apiUrl').value=state.apiUrl;$('apiUrl').placeholder=`留空使用服务器默认（${defaultApiUrl()}）`;updateAdminLink();
  document.querySelectorAll('.nav-item').forEach(el=>el.onclick=()=>setPage(el.dataset.page));
  $('openConnection').onclick=()=>setPage('settings');
  $('loginAction').onclick=openLogin;
  $('logoutAction').onclick=logoutMerchant;
  for(const type of ['filter','sales']){
    $(type+'Ids').addEventListener('input',()=>refreshInputStats(type));
    $(type+'Dedupe').onclick=()=>{$(type+'Ids').value=inputIds(type).join('\n');refreshInputStats(type);};
    $(type+'Clear').onclick=()=>{$(type+'Ids').value='';refreshInputStats(type);message(type,'');};
    $(type+'File').onchange=async()=>{const file=$(type+'File').files?.[0];if(!file)return;const content=await file.text();$(type+'Ids').value=[$(type+'Ids').value,content].filter(Boolean).join('\n');$(type+'File').value='';refreshInputStats(type);};
    $(type+'Run').onclick=()=>runQuery(type);$(type+'Reload').onclick=()=>loadBatches(type);$(type+'ClearAll').onclick=()=>clearAllBatches(type);$(type+'Export').onclick=()=>exportXlsx(type);
    $(type+'Back').onclick=()=>showWorkspace(type);
    $(type+'DeleteCurrent').onclick=()=>{const batch=state.detail[type]?.batch;if(batch)deleteBatch(type,batch);};
    refreshInputStats(type);
    renderOverview(type);
  }
  document.querySelectorAll('#filterTabs .tab').forEach(el=>el.onclick=()=>{state.filterView=el.dataset.view;renderDetail('filter');});
  $('copyEligible').onclick=()=>copyFilterIds('eligible');$('copyOverSales').onclick=()=>copyFilterIds('overSales');$('copyOverPrice').onclick=()=>copyFilterIds('overPrice');
  $('saveSettings').onclick=()=>{const sales=Number($('salesThreshold').value),price=Number($('priceThreshold').value);if(!Number.isInteger(sales)||sales<0||!Number.isFinite(price)||price<0){$('settingsSaved').textContent='请输入有效的非负阈值';return;}state.rules={sales,price};localStorage.setItem(ruleStore,JSON.stringify(state.rules));updateRuleUI();$('settingsSaved').textContent='规则已保存';renderDetail('filter');renderOverview('filter');};
  $('saveSalesSettings').onclick=async()=>{const raw=$('querySalesThreshold').value,threshold=Number(raw);if(raw===''||!Number.isInteger(threshold)||threshold<0){$('salesSettingsSaved').textContent='请输入有效的非负整数';return;}state.salesThreshold=threshold;localStorage.setItem(salesRuleStore,String(threshold));updateSalesRuleUI();$('salesSettingsSaved').textContent='正在更新已有销量批次…';try{const result=await syncSalesThreshold();state.detail.sales=null;showWorkspace('sales');await loadBatches('sales');$('salesSettingsSaved').textContent=result?`已保存：销量 > ${threshold} 算已出，已重算 ${result.updated} 个批次`:`已保存：销量 > ${threshold} 算已出；连接 API 后更新已有批次`;}catch(error){$('salesSettingsSaved').textContent=`规则已保存在浏览器；已有批次更新失败：${error.message}`;}};
  $('saveKey').onclick=async()=>{const key=$('apiKey').value.trim();if(!key){message('key','请填写 API Key');return;}let apiUrl;try{apiUrl=normalizeApiUrl($('apiUrl').value);}catch(error){message('key',error.message);return;}state.key=key;state.apiUrl=apiUrl;localStorage.setItem(keyStore,key);if(apiUrl)localStorage.setItem(apiUrlStore,apiUrl);else localStorage.removeItem(apiUrlStore);updateAdminLink();state.authenticated=false;setConnection('正在连接…','warn');updateShows([]);for(const type of ['filter','sales']){state.batches[type]=[];state.detail[type]=null;batchPlaceholder(type,'正在读取批次…');showWorkspace(type);renderOverview(type);}message('key','');await refreshStatus();await loadBatches('filter');try{await syncSalesThreshold();}catch(error){$('salesSettingsSaved').textContent=`已有销量批次更新失败：${error.message}`;}await loadBatches('sales');};
  $('clearKey').onclick=()=>{localStorage.removeItem(keyStore);localStorage.removeItem(apiUrlStore);state.key='';state.apiUrl='';state.authenticated=false;$('apiKey').value='';$('apiUrl').value='';updateAdminLink();setConnection('未连接');updateShows([]);$('loginModal').classList.add('hidden');for(const type of ['filter','sales']){state.batches[type]=[];state.detail[type]=null;batchPlaceholder(type,'连接 API 后显示批次');showWorkspace(type);renderOverview(type);}message('key','已清除浏览器中的 API 地址和 Key');};
  $('closeModal').onclick=()=>{state.modalDismissed=true;$('loginModal').classList.add('hidden');};$('refreshQr').onclick=refreshQr;$('checkLogin').onclick=refreshStatus;
  setPage(names[location.hash.slice(1)]?location.hash.slice(1):'filter');
  setConnection(state.key?'正在连接…':'未连接',state.key?'warn':'');
  if(state.key){refreshStatus();loadBatches('filter');syncSalesThreshold().catch(error=>{$('salesSettingsSaved').textContent=`已有销量批次更新失败：${error.message}`;}).finally(()=>loadBatches('sales'));}
  setInterval(()=>{if(!document.hidden&&state.key)refreshStatus();},10000);
}
initialize();
