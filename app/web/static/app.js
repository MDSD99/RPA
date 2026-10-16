'use strict';

const productName = document.documentElement.dataset.productName;
const workspaceMode = document.documentElement.dataset.workspace || 'development';

const $ = (selector, root = document) => root.querySelector(selector);
const main = $('#main');
const formDialog = $('#form-dialog');
const transactionDialog = $('#transaction-dialog');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const number = value => Number(value || 0).toLocaleString('tr-TR');
const labels = {PENDING:'Bekliyor',RUNNING:'Çalışıyor',COMPLETED:'Tamamlandı',PARTIALLY_COMPLETED:'Kısmen tamamlandı',FAILED:'Başarısız',CANCELLED:'İptal edildi',PROCESSING:'İşleniyor',SUCCESS:'Başarılı',SKIPPED:'Atlandı',RETRY:'Tekrar bekliyor',idle:'Hazır',processing:'Çalışıyor',recovering:'Kontrol ediyor',stopped:'Durduruldu',error:'Bağlantı hatası'};
const badge = (status, review = false) => `<span class="status-badge status-${review ? 'review' : String(status).toLowerCase()}">${review ? 'İnceleme bekliyor' : escapeHtml(labels[status] || status)}</span>`;
const workflow = value => escapeHtml(state.catalog?.items.find(item => item.name === value)?.title || (value === 'infrastructure_demo' ? 'Altyapı demosu' : value));
const date = value => value ? new Intl.DateTimeFormat('tr-TR',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '—';
const ago = value => { if (!value) return 'Henüz çalışmadı'; const secs = Math.max(0,Math.floor((Date.now()-Date.parse(value))/1000)); return secs < 10 ? 'Az önce' : secs < 60 ? `${secs} sn önce` : secs < 3600 ? `${Math.floor(secs/60)} dk önce` : date(value); };
const state = {route:'overview',jobId:null,filter:'',query:'',review:false,page:0,limit:10,loading:false,sequence:0,overview:null,catalog:null,currentJob:null,transaction:null};

async function api(path, body, method) {
  if (window.multirpaTransport) return window.multirpaTransport.request(path, body, method);
  const response = await fetch(path,{method:method || (body === undefined ? 'GET' : 'POST'),headers:body === undefined ? {} : {'Content-Type':'application/json','X-RPA-Request':'1'},body:body === undefined ? undefined : JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  let data;
  try { data = await response.json(); } catch { throw new Error('Sunucudan geçerli yanıt alınamadı. Bağlantıyı kontrol et.'); }
  if (!response.ok) throw new Error(data.error?.message || 'İşlem tamamlanamadı. Durumu yenileyip tekrar dene.');
  return data;
}

function toast(message, error = false) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' error' : ''}`;
  element.innerHTML = icon(error ? 'alert' : 'check') + `<span>${escapeHtml(message)}</span>`;
  $('#toasts').append(element);
  setTimeout(() => element.remove(),6000);
}

function heading(title, description, actions = true) {
  actions = actions && workspaceMode === 'development';
  return `<div class="page-heading"><div><div class="eyebrow">${escapeHtml(productName)}</div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div>${actions ? `<div class="heading-actions"><button class="button" data-action="demo">${icon('play')}Demo başlat</button><button class="button primary" data-action="new-job">${icon('plus')}Yeni iş</button></div>` : ''}</div>`;
}

function empty(title, description, action, buttonText, iconName = 'jobs') {
  return `<div class="empty-state"><div class="empty-icon">${icon(iconName)}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p>${action ? `<button class="button primary" data-action="${action}">${icon('plus')}${escapeHtml(buttonText)}</button>` : ''}</div>`;
}

function stats(counts) {
  const terminal = counts.success + counts.failed + counts.skipped;
  const successRate = terminal ? `${Math.round(counts.success / terminal * 100)}%` : '—';
  const cards = [
    ['Toplam işlem',number(counts.transactions),'jobs',`${number(counts.jobs)} iş içerisinde`,'featured'],
    ['Aktif işler',number(counts.running_jobs),'play',`${number(counts.processing)} kayıt şu anda işleniyor`,''],
    ['Başarı oranı',successRate,'check',terminal ? `${number(counts.success)} / ${number(terminal)} tamamlanan işlem` : 'Tamamlanan işlemler üzerinden',''],
    ['İnceleme bekliyor',number(counts.reconciliation_required),'alert',counts.reconciliation_required ? 'Devam etmek için sonuç doğrulaması gerekli' : 'Müdahale bekleyen kayıt yok',counts.reconciliation_required ? 'alert' : '']
  ];
  return `<div class="stat-grid">${cards.map(([label,value,symbol,foot,cls]) => `<div class="stat-card ${cls}"><div class="stat-top"><span>${label}</span><span class="stat-icon">${icon(symbol)}</span></div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`).join('')}</div>`;
}

function workerTitle(worker) {
  const match=worker.worker_id.match(/^(demo|workflow)-worker-(?:.*-)?([0-9]+)$/);
  return match ? `${match[1] === 'demo' ? 'Demo' : 'Akış'} worker ${match[2]}` : worker.worker_id;
}

function workerWorkflowNames(worker) {
  return Array.isArray(worker.workflow_names) ? worker.workflow_names : ['infrastructure_demo'];
}

function workerScope(workers) {
  if (state.catalog?.workers_enabled === false) return 'Bu uygulamanın worker’ları kapalı.';
  if (!workers.length) return 'Bu sunucuda çalışan worker bulunmuyor.';
  const names=[...new Set(workers.flatMap(workerWorkflowNames))];
  if (!names.length) return 'Worker’lara henüz bir iş akışı atanmamış.';
  return names.every(name => name === 'infrastructure_demo') ? 'Yalnızca altyapı demo işlerini çalıştırır' : `Atanan iş akışları: ${names.map(workflow).join(', ')}`;
}

function workflowsPage(catalog) {
  const automations=catalog.items.filter(item => item.kind === 'automation');
  const availability=catalog.workers_enabled ? 'Worker’lar etkin' : 'Worker’lar kapalı';
  return `${heading('İş akışları','Kayıtlı akışları incele ve çalıştıracağın işi oluştur.',false)}<div class="catalog-summary"><div><span class="tag">${number(catalog.items.length)} kayıtlı akış</span><span class="tag">${escapeHtml(availability)}</span></div><button class="button small" data-action="refresh">${icon('refresh')}Yenile</button></div>${!automations.length ? `<div class="callout">${icon('jobs')}<div><strong>Henüz uygulamaya özel otomasyon kayıtlı değil.</strong> Altyapı demosuyla kayıt işleme, tekrar ve kurtarmayı deneyebilirsin. Gerçek uygulama adımları için o akışın otomasyonu sisteme bağlanmalı.</div></div>` : ''}${!catalog.workers_enabled ? `<div class="callout">${icon('alert')}<div><strong>Bu uygulamanın worker’ları kapalı.</strong> İş ve kayıt oluşturabilirsin; bu uygulamadaki worker’lar etkinleştirilene kadar kayıtları işlemez. Demo çalıştırma kapalıdır.</div></div>` : ''}<div class="workflow-grid">${catalog.items.map(item => `<article class="workflow-card" data-workflow-name="${escapeHtml(item.name)}"><div class="workflow-card-head"><div class="worker-symbol">${icon(item.kind === 'demo' ? 'spark' : 'jobs')}</div><span class="status-badge ${item.kind === 'demo' ? 'status-pending' : 'status-idle'}">${item.kind === 'demo' ? 'Altyapı demosu' : 'Kayıtlı otomasyon'}</span></div><h2>${escapeHtml(item.title)}</h2><code class="workflow-code">${escapeHtml(item.name)}</code><p>${escapeHtml(item.description)}</p>${item.kind === 'demo' ? '<p class="workflow-note">Dış bir uygulamada gerçek işlem yapmaz.</p>' : '<p class="workflow-note">Kayıtları bu akışın kayıtlı otomasyonu işler. İş adımlarını ve gereken kayıtları akış tanımına göre hazırla.</p>'}<div class="workflow-card-footer"><span class="text-small muted">${item.workers_enabled ? 'Worker’lar etkin' : 'Worker’lar kapalı'}</span><button class="button ${item.kind === 'demo' ? '' : 'primary'}" data-action="${item.kind === 'demo' ? 'demo' : 'new-job'}" data-workflow="${escapeHtml(item.name)}" ${item.kind === 'demo' && !item.workers_enabled ? 'disabled' : ''}>${icon(item.kind === 'demo' ? 'play' : 'plus')}${item.kind === 'demo' ? 'Demo başlat' : 'Yeni iş'}</button></div></article>`).join('')}</div>`;
}

function jobWorkflowNotice(job) {
  if (job.workflow_name === 'visual_flow') return `<div class="flow-run-notice">${icon('spark')}<div><strong>Görsel akış çalıştırması</strong><span>Kaydedilen sürüm üzerinden çalışır. Adım ilerlemesi ve sonuç, kayıt ayrıntılarındadır.</span></div><a class="button small" href="#workflows">Akışlar ${icon('arrow')}</a></div>`;
  const registered=state.catalog?.items.find(item => item.name === job.workflow_name && item.registered);
  if (!registered) return `<div class="callout">${icon('jobs')}<div>Bu iş kuyruğa kaydedildi. Çalışması için bu akışa özel otomasyon worker’ının bağlanması gerekiyor. Bu uygulamada bu akış kayıtlı değil.</div></div>`;
  if (!registered.workers_enabled) return `<div class="callout">${icon('alert')}<div>Bu iş akışı kayıtlı; bu uygulamanın worker’ları kapalı. Kayıtların işlenmesi için worker’ların etkinleştirilmesi gerekiyor.</div></div>`;
  return '';
}

function workerMini(workers) {
  return `<section class="panel"><div class="panel-head"><div class="panel-title-group"><h2>Worker'lar</h2><span class="tag">${workers.length} worker</span></div><a href="#workers" class="button ghost small">Tümü ${icon('arrow')}</a></div><div class="worker-mini-list">${workers.length ? workers.map(w => `<div class="worker-mini"><div class="worker-symbol">${icon('worker')}</div><div><div class="worker-name" title="${escapeHtml(w.worker_id)}">${escapeHtml(workerTitle(w))}</div><div class="worker-caption">${number(w.processed_count)} işlem tamamlandı</div></div>${badge(w.state)}</div>`).join('') : '<p class="inline-note">Çalışan worker bulunmuyor.</p>'}</div><div class="worker-panel-footer"><span class="online-dot${state.catalog?.workers_enabled === false || !workers.length ? ' offline' : ''}"></span>${workerScope(workers)}</div></section>`;
}

function distribution(counts) {
  const groups = [['Başarılı',counts.success,'#8ba66c'],['Bekleyen',counts.pending,'#d6dec3'],['İşlenen',counts.processing,'#477a59'],['Tekrar',counts.retry,'#cfb374'],['Başarısız',counts.failed,'#ce8f7b'],['Atlanan',counts.skipped,'#e6e9df']];
  return `<section class="panel"><div class="panel-head"><div><h2>İşlem dağılımı</h2><p>Tüm işler · güncel durum</p></div><span class="tag">Canlı veri</span></div><div class="distribution-body"><div class="donut" id="distribution-chart" role="img" aria-label="İşlem durumlarının dağılımı"><div class="donut-center"><strong>${number(counts.transactions)}</strong><small>toplam işlem</small></div></div><div class="distribution-legend">${groups.map(([label,value,color]) => `<div class="legend-row"><span class="legend-dot" data-color="${color}"></span><div class="legend-text">${label}<strong>${number(value)}</strong></div></div>`).join('')}</div></div></section>`;
}

function paintCharts() {
  document.querySelectorAll('[data-color]').forEach(node => node.style.background = node.dataset.color);
  const chart = $('#distribution-chart');
  if (!chart || !state.overview?.counts.transactions) return;
  const c = state.overview.counts;
  const pieces = [[c.success,'#8ba66c'],[c.pending,'#d6dec3'],[c.processing,'#477a59'],[c.retry,'#cfb374'],[c.failed,'#ce8f7b'],[c.skipped,'#e6e9df']];
  let cursor = 0;
  chart.style.background = `conic-gradient(${pieces.map(([value,color]) => { const start=cursor; cursor += value / c.transactions * 100; return `${color} ${start}% ${cursor}%`; }).join(',')})`;
}

function jobTable(items) {
  if (!items.length) return workspaceMode === 'development'
    ? empty('Henüz bir iş yok','Demo başlatarak ilk işlemlerini takip et veya kendi iş kuyruğunu oluştur.','demo','İlk demoyu başlat')
    : empty('Henüz bir çalıştırma yok','İş akışları ekranında bir akış oluşturup çalıştırabilirsiniz.');
  return `<div class="table-wrap"><table class="jobs-table"><thead><tr><th>İş adı</th><th>Durum</th><th>İlerleme</th><th>Oluşturulma</th><th><span class="muted">Detay</span></th></tr></thead><tbody>${items.map(j => { const completed=j.success_count+j.failed_count+j.skipped_count; return `<tr><td><div class="job-cell"><div class="job-symbol">${icon('jobs')}</div><div><a class="job-title" href="#jobs/${j.id}" title="${escapeHtml(j.job_id)}">${escapeHtml(j.job_id)}</a><span class="job-caption">${workflow(j.workflow_name)}</span></div></div></td><td>${badge(j.status)}</td><td><div class="progress-cell"><progress value="${completed}" max="${j.total_records || 1}" aria-label="${completed} / ${j.total_records} kayıt tamamlandı"></progress><span>${completed}/${j.total_records}</span></div></td><td class="text-small">${date(j.created_at)}</td><td><a class="icon-button" aria-label="${escapeHtml(j.job_id)} işini aç" href="#jobs/${j.id}">${icon('arrow')}</a></td></tr>`; }).join('')}</tbody></table></div>`;
}

function attention(counts) {
  return counts.reconciliation_required ? `<div class="callout">${icon('alert')}<div><strong>${number(counts.reconciliation_required)} kayıt inceleme bekliyor.</strong> Sonucu doğruladıktan sonra işleme devam edebilirsin.</div><a href="#queue?review=1" class="button small">Kayıtları incele ${icon('arrow')}</a></div>` : '';
}

function overviewPage(overview, jobs) {
  const c=overview.counts;
  return `${heading('Operasyon merkezi','İşlerini takip et. İstisnalara zamanında müdahale et.')}${stats(c)}${attention(c)}<div class="overview-grid">${distribution(c)}${workerMini(overview.workers)}</div><section class="panel"><div class="panel-head"><div class="panel-title-group"><h2>Son işler</h2><span class="tag">${number(c.jobs)} iş</span></div><a class="button ghost small" href="#jobs">Tüm işleri gör ${icon('arrow')}</a></div>${jobTable(jobs.items)}<div class="table-footer"><span>Veriler sunucudan canlı olarak güncellenir</span><span>Son ${Math.min(jobs.items.length,6)} iş</span></div></section>`;
}

function toolbar(type) {
  const options = type === 'jobs' ? ['PENDING','RUNNING','COMPLETED','PARTIALLY_COMPLETED','FAILED','CANCELLED'] : ['PENDING','PROCESSING','SUCCESS','FAILED','RETRY','SKIPPED'];
  return `<div class="toolbar"><div class="search-field">${icon('search')}<input id="table-search" type="search" placeholder="${type === 'jobs' ? 'İş adı veya akış ara…' : 'Kayıt veya işlem anahtarı ara…'}" aria-label="${type === 'jobs' ? 'İş ara' : 'Kayıt ara'}" value="${escapeHtml(state.query)}" maxlength="256"></div>${type !== 'jobs' ? `<button class="button small ${state.review ? 'primary' : ''}" data-action="review-filter">${icon('alert')}İnceleme bekleyen</button>` : ''}<select id="status-filter" aria-label="Duruma göre filtrele"><option value="">Tüm durumlar</option>${options.map(s => `<option value="${s}"${state.filter === s ? ' selected' : ''}>${labels[s]}</option>`).join('')}</select><button class="icon-button" data-action="refresh" aria-label="Listeyi yenile">${icon('refresh')}</button></div>`;
}

function pagination(total) {
  const start=total ? state.page*state.limit+1 : 0;
  const end=Math.min((state.page+1)*state.limit,total);
  return `<div class="table-footer"><span>${number(total)} kayıttan ${number(start)}–${number(end)}</span><div class="pagination"><button class="button small" data-action="previous" ${state.page === 0 ? 'disabled' : ''}>${icon('back')}Önceki</button><span class="page-number">${state.page+1} / ${Math.max(1,Math.ceil(total/state.limit))}</span><button class="button small" data-action="next" ${end >= total ? 'disabled' : ''}>Sonraki ${icon('arrow')}</button></div></div>`;
}

function transactionTable(items) {
  if (!items.length) return empty('Burada bekleyen kayıt yok','Filtreyi değiştirebilir veya bir işe yeni kayıt ekleyebilirsin.',null,null,'queue');
  return `<div class="table-wrap"><table class="transactions-table"><thead><tr><th>Kayıt</th><th>Durum</th><th>Deneme</th><th>Worker</th><th>Güncelleme</th><th>Detay</th></tr></thead><tbody>${items.map(t => `<tr><td><button class="job-title" data-action="transaction" data-id="${t.id}" type="button">${escapeHtml(t.record_id)}</button><span class="job-caption">${workflow(t.workflow_name)}</span></td><td>${badge(t.status,t.reconciliation_required)}</td><td>${t.attempt_count} <span class="muted">/ ${t.max_attempts}</span></td><td class="text-small">${escapeHtml(t.owner_id || '—')}</td><td class="text-small">${date(t.updated_at)}</td><td><button class="icon-button" data-action="transaction" data-id="${t.id}" aria-label="${escapeHtml(t.record_id)} kaydını incele">${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div>`;
}

function jobDetailPage(job, transactions) {
  const active=['PENDING','RUNNING'].includes(job.status);
  const done=job.success_count+job.failed_count+job.skipped_count;
  return `<button class="back-button" data-action="back-jobs">${icon('back')}Tüm işler</button><div class="page-heading"><div><div class="eyebrow">${workflow(job.workflow_name)}</div><h1 class="wrap">${escapeHtml(state.flowRun?.spec.name || job.job_id)}</h1><p>${date(job.created_at)} tarihinde oluşturuldu</p></div><div class="record-toolbar">${job.status === 'PENDING' ? '<button class="button primary" data-action="start">'+icon('play')+'İşi başlat</button>' : ''}${job.status === 'RUNNING' ? `<button class="button" data-action="recover">${icon('refresh')}Kurtarma kontrolü</button>${done === job.total_records ? '<button class="button primary" data-action="finalize">'+icon('check')+'İşi tamamla</button>' : ''}` : ''}${active ? '<button class="button danger" data-action="cancel">İptal et</button>' : ''}</div></div><div class="panel detail-summary"><div><span>İş durumu</span>${badge(job.status)}</div><div><span>Tamamlanan / toplam</span><strong>${done} / ${job.total_records}</strong></div><div><span>Başarılı / başarısız</span><strong>${job.success_count} / ${job.failed_count}</strong></div><div><span>Son güncelleme</span><strong>${date(job.updated_at)}</strong></div></div>${jobWorkflowNotice(job)}${state.flowRun ? FlowUI.runPanel(state.flowRun) : ''}<section class="panel"><div class="panel-head"><h2>İş kayıtları <span class="tag">${job.total_records}</span></h2>${active && job.workflow_name !== 'visual_flow' ? '<button class="button small" data-action="add-record">'+icon('plus')+'Kayıt ekle</button>' : ''}</div>${toolbar('transactions')}${transactionTable(transactions.items)}${pagination(transactions.total)}</section>`;
}

function workersPage(workers) {
  return `${heading("Worker'lar",'Çalışan worker’ları ve son bağlantı durumlarını izle.',false)}<div class="workers-grid">${workers.map(w => `<article class="worker-card"><div class="worker-card-head"><div class="worker-symbol">${icon('worker')}</div>${badge(w.state)}</div><h2 title="${escapeHtml(w.worker_id)}">${escapeHtml(workerTitle(w))}</h2><p>${workerWorkflowNames(w).map(workflow).join(', ') || 'Atanmış iş akışı yok'} · bu sunucuda</p><div class="worker-state-line text-small muted">${icon('clock')}Son bağlantı: ${ago(w.last_heartbeat)}</div>${w.transaction_id ? `<button class="button small" data-action="transaction" data-id="${w.transaction_id}">Aktif kaydı aç ${icon('arrow')}</button>` : ''}<div class="worker-metrics"><div><span>Tamamlanan</span><strong>${number(w.processed_count)}</strong></div><div><span>Hata / tekrar</span><strong>${number(w.error_count)}</strong></div></div></article>`).join('')}</div><p class="inline-note">${workerScope(workers)}. Sayaçlar uygulamanın bu çalışma oturumuna aittir; işlem geçmişi veritabanında korunur.</p>`;
}

function params() {
  const p=new URLSearchParams({limit:state.limit,offset:state.page*state.limit});
  if (state.filter) p.set('status',state.filter);
  if (state.query) p.set('q',state.query);
  if (state.review && state.route !== 'jobs') p.set('reconciliation_required','true');
  return p;
}

async function loadJobPageData() {
  const result=await Promise.all([api(`/api/jobs/${state.jobId}`),api(`/api/jobs/${state.jobId}/transactions?${params()}`)]);
  if(result[0].job.workflow_name === 'visual_flow') {
    const [spec,records]=await Promise.all([api(`/api/flows/runs/by-job/${result[0].job.id}`),api(`/api/jobs/${result[0].job.id}/transactions?limit=1`)]);
    const detail=records.items.length ? await api(`/api/transactions/${records.items[0].id}`) : null;
    result.push({spec,detail});
  }
  return result;
}

async function refresh(silent = false) {
  if (state.route === 'flow') { if (!silent) await FlowUI.open(state.flowId); return; }
  const sequence=++state.sequence;
  state.loading=true;
  try {
    const overviewPromise=api('/api/overview');
    const catalogPromise=api('/api/workflows').catch(error => {
      error.catalogUnavailable=true;
      error.message=`İş akışı kataloğu alınamadı. ${error.message}`;
      throw error;
    });
    let content;
    if (state.route === 'overview') {
      const [overview,catalog,jobs]=await Promise.all([overviewPromise,catalogPromise,api('/api/jobs?limit=6')]);
      if (sequence !== state.sequence) return;
      state.overview=overview;state.catalog=catalog;content=overviewPage(overview,jobs);
    } else {
      const request=state.route === 'job' ? loadJobPageData() : state.route === 'jobs' ? api(`/api/jobs?${params()}`) : state.route === 'queue' ? api(`/api/transactions?${params()}`) : state.route === 'workflows' ? api('/api/flows') : state.route === 'schedules' ? api(`/api/schedules?${params()}`) : api('/api/workers');
      const [overview,catalog,data]=await Promise.all([overviewPromise,catalogPromise,request]);
      if (sequence !== state.sequence) return;
      state.overview=overview;state.catalog=catalog;
      if (state.route === 'job') { state.currentJob=data[0].job; state.flowRun=data[2] || null; content=jobDetailPage(data[0].job,data[1]); }
      else if (state.route === 'workflows') content=FlowUI.catalog(data,catalog);
      else if (state.route === 'schedules') content=SchedulerUI.page(data);
      else if (state.route === 'jobs') content=`${heading('İşler','Tüm işlerini, ilerlemelerini ve sonuçlarını takip et.')}<section class="panel"><div class="panel-head"><h2>İş listesi <span class="tag">${number(data.total)}</span></h2></div>${toolbar('jobs')}${jobTable(data.items)}${pagination(data.total)}</section>`;
      else if (state.route === 'queue') content=`${heading('İşlem kuyruğu','Her kaydın durumunu gör. Gerekli olduğunda güvenle müdahale et.',false)}${attention(overview.counts)}<section class="panel"><div class="panel-head"><h2>Tüm kayıtlar <span class="tag">${number(data.total)}</span></h2></div>${toolbar('transactions')}${transactionTable(data.items)}${pagination(data.total)}</section>`;
      else content=workersPage(data.items);
    }
    if (sequence !== state.sequence) return;
    const focusedId=document.activeElement?.id;
    const selection=document.activeElement?.selectionStart;
    main.innerHTML=content;
    if (focusedId === 'table-search') { const input=$('#table-search'); input?.focus(); input?.setSelectionRange(selection,selection); }
    paintCharts();
    $('#nav-jobs').textContent=number(state.overview.counts.jobs);
    $('#connection-status').innerHTML='<span class="online-dot"></span>Sistem çalışıyor';
    $('#server-dot').style.background='#8ac079';
    $('#last-updated').textContent=`Son güncelleme ${new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;
  } catch (error) {
    if (sequence !== state.sequence) return;
    if (error.catalogUnavailable) state.catalog=null;
    $('#connection-status').innerHTML='<span class="online-dot offline"></span>Bağlantı yok';
    $('#server-dot').style.background='#caa56c';
    if (!silent || error.catalogUnavailable) main.innerHTML=`${heading('Bağlantı kurulamadı','Sunucu bağlantısını kontrol edip yeniden dene.',false)}<div class="offline-banner">${escapeHtml(error.message)}<button class="button small" data-action="refresh">${icon('refresh')}Yeniden dene</button></div>`;
  } finally { if (sequence === state.sequence) state.loading=false; }
}

function navigate() {
  clearTimeout(searchTimer);
  formDialog.close();
  transactionDialog.close();
  const hash=location.hash.slice(1) || (workspaceMode === 'development' ? 'overview' : 'workflows');
  const [path,query]=hash.split('?');
  const parts=path.split('/');
  state.route=parts[0] === 'flows' && (parts[1] === 'new' || /^[a-f0-9-]{36}$/i.test(parts[1] || '')) ? 'flow' : parts[0] === 'jobs' && /^[a-f0-9-]{36}$/i.test(parts[1] || '') ? 'job' : ['overview','workflows','schedules','jobs','queue','workers'].includes(parts[0]) ? parts[0] : 'overview';
  state.jobId=state.route === 'job' ? parts[1] : null;
  state.flowId=state.route === 'flow' ? parts[1] : null;
  main.classList.toggle('flow-main',state.route === 'flow');
  FlowUI.leave(state.route);
  state.page=0;state.filter='';state.query='';state.review=new URLSearchParams(query).get('review') === '1';
  $('#breadcrumb').textContent={overview:'Genel bakış',workflows:'İş akışları',schedules:'Zamanlamalar',jobs:'İşler',queue:'İşlem kuyruğu',workers:"Worker'lar",job:'İş ayrıntıları',flow:'Akış tasarımcısı'}[state.route];
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active',a.dataset.nav === (state.route === 'job' ? 'jobs' : state.route === 'flow' ? 'workflows' : state.route)));
  $('#sidebar').classList.remove('open');$('#menu-button').setAttribute('aria-expanded','false');
  document.title=`${$('#breadcrumb').textContent} · ${productName}`;
  window.scrollTo(0,0);refresh();
}

function dialogHeader(title,description,transaction = false) {
  return `<div class="dialog-header"><div><h2 id="${transaction ? 'transaction-title' : 'dialog-title'}">${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div><button class="icon-button" data-action="${transaction ? 'close-transaction' : 'close-dialog'}" aria-label="Pencereyi kapat">${icon('close')}</button></div>`;
}

function showForm(type, selectedWorkflow = '') {
  if (workspaceMode !== 'development' && ['demo','new-job','add-record'].includes(type)) return;
  if (type === 'new-job' && selectedWorkflow === 'visual_flow') { location.hash='workflows';return; }
  if (type === 'demo' && state.catalog?.workers_enabled === false) return toast('Bu uygulamanın worker’ları kapalı. Demo için worker’ları etkinleştir.',true);
  let title,description,fields,submit;
  const requestId=crypto.randomUUID();
  if (type === 'demo') {
    title='Altyapıyı çalışırken gör';description='Bir senaryo seç. Worker’lar demo kayıtlarını gerçek işlem altyapısında çalıştırsın.';submit='Demoyu çalıştır';
    fields=`<div class="scenario-grid"><label class="scenario-option"><input type="radio" name="scenario" value="success" checked><span><strong>Başarılı işlem</strong><small>Kayıtlar işlenir, ilerleme kaydedilir ve iş tamamlanır.</small></span></label><label class="scenario-option"><input type="radio" name="scenario" value="retry"><span><strong>Kontrollü tekrar</strong><small>İlk deneme başarısız olur; kayıt ikinci denemede tamamlanır.</small></span></label><label class="scenario-option"><input type="radio" name="scenario" value="recovery"><span><strong>Kesinti ve kurtarma</strong><small>Sahiplik süresi dolan kayıt incelemeye alınır. Devam kararını sen verirsin.</small></span></label></div><div class="field-group"><label for="record-count">Kayıt sayısı</label><input id="record-count" name="record_count" type="number" min="1" max="20" value="8" required><small>Demo, dış bir siteye bağlanmaz veya gerçek business işlemi yapmaz.</small></div>`;
  } else if (type === 'new-job') {
    title='Yeni iş oluştur';description='İşini tanımla, kayıtlarını ekle ve kuyruğunu yönet.';submit='İşi oluştur';
    fields=`<div class="field-group"><label for="job-name">İş adı / çalıştırma kimliği</label><input id="job-name" name="job_id" required maxlength="128" placeholder="Örn. günlük-aktarım-001" autocomplete="off"><small>Her çalıştırma için benzersiz bir kimlik kullan.</small></div><div class="field-group"><label for="workflow-name">İş akışı adı</label><input id="workflow-name" name="workflow_name" required maxlength="128" placeholder="Örn. belge_aktarimi" autocomplete="off" list="workflow-choices" value="${escapeHtml(selectedWorkflow)}"><datalist id="workflow-choices">${(state.catalog?.items || []).filter(item => item.kind === 'automation' && item.registered).map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.title)}</option>`).join('')}</datalist><small>Kayıtlı bir akış seçebilir veya ayrı bir worker için akış adını yazabilirsin. Kayıtlı olmayan akışları bu uygulamanın worker’ları çalıştırmaz. Altyapıyı denemek için Demo başlat seçeneğini kullan.</small></div>`;
  } else if (type === 'add-record') {
    title='İşe kayıt ekle';description=state.currentJob.job_id;submit='Kaydı ekle';
    fields=`<div class="field-group"><label for="record-id">Kayıt kimliği</label><input id="record-id" name="record_id" required maxlength="256" placeholder="Örn. kayıt-001" autocomplete="off"></div><div class="field-group"><label for="idempotency-key">Tekil işlem anahtarı</label><input id="idempotency-key" name="idempotency_key" required maxlength="256" placeholder="kaynak:kayıt:işlem" autocomplete="off"><small>Aynı business işlemi için her denemede aynı anahtarı kullan. Bu anahtar yinelenen kayıtları engeller.</small></div><div class="field-group"><label for="external-reference">Dış referans (isteğe bağlı)</label><input id="external-reference" name="external_reference" maxlength="256" placeholder="Örn. hedef-sistem-kayıt-no" autocomplete="off"><small>Hedef sistemdeki kaydı bulmak için referansını ekle.</small></div><div class="field-group"><label for="max-attempts">En fazla deneme sayısı</label><input id="max-attempts" name="max_attempts" type="number" min="1" max="100" value="3" required></div>`;
  } else {
    title='İş iptal edilsin mi?';description=state.currentJob.job_id;submit='Evet, işi iptal et';
    fields='<p class="inline-note">Bekleyen kayıtlar atlanır. İşlenmekte olan kayıtlar için sahiplik iptal edilir; sonucu belirsiz işlemler incelemeye alınır. Bu iş yeniden başlatılamaz.</p>';
  }
  $('#dialog-content').innerHTML=`${dialogHeader(title,description)}<form id="action-form" data-type="${type}" data-job-id="${state.jobId || ''}" data-request-id="${requestId}"><div class="dialog-body">${fields}<div class="form-error" id="form-error" role="alert"></div><div class="form-actions"><button type="button" class="button" data-action="close-dialog">Vazgeç</button><button type="submit" class="button ${type === 'cancel' ? 'danger' : 'primary'}">${escapeHtml(submit)} ${icon('arrow')}</button></div></div></form>`;
  formDialog.showModal();
}

const auditLabels={TRANSACTION_CREATED:'Kayıt kuyruğa eklendi',TRANSACTION_CLAIMED:'Worker kaydı sahiplendi',TRANSACTION_SUCCEEDED:'İşlem başarıyla tamamlandı',TRANSACTION_ERROR:'İşlem hata veya tekrar durumuna geçti',TRANSACTION_RECOVERY_REQUIRED:'Sahiplik süresi doldu; inceleme gerekiyor',TRANSACTION_RECONCILED_SUCCEEDED:'Sonuç doğrulandı: başarılı',TRANSACTION_RECONCILED_NOT_APPLIED:'Sonuç doğrulandı: işlem gerçekleşmedi',TRANSACTION_JOB_CANCELLED:'İş iptal edildi',TRANSACTION_SKIPPED:'Kayıt atlandı'};

async function openTransaction(id) {
  try {
    const data=await api(`/api/transactions/${id}`);
    state.transaction=data.transaction;
    const t=data.transaction;
    const recovery=t.reconciliation_required ? `<div class="reconcile-box"><h3>Bu kaydın sonucu doğrulanmalı</h3><p>${t.workflow_name === 'infrastructure_demo' ? 'Bu bir kesinti demosu. Dış sistemde işlem yapılmadı. Yeniden denemeye izin vererek kaydın kaldığı yerden tamamlanmasını izleyebilirsin.' : 'Hedef sistemde bu işlemin gerçekleşip gerçekleşmediğini kontrol et. Sonuç belirsizse kaydı inceleme durumunda bırak.'}</p><label class="check-label"><input type="checkbox" id="reconcile-confirm">${t.workflow_name === 'infrastructure_demo' ? 'Demo kaydı için sonucu doğruladım.' : 'Hedef sistemde işlem sonucunu doğruladım.'}</label><div class="reconcile-actions"><button class="button primary small" data-action="reconcile" data-outcome="not_applied" disabled>Gerçekleşmedi · devam et</button><button class="button small" data-action="reconcile" data-outcome="succeeded" disabled>Gerçekleşti · başarılı say</button></div><div class="form-error" id="reconcile-error" role="alert"></div></div>` : '';
    $('#transaction-content').innerHTML=`${dialogHeader('Kayıt ayrıntıları',t.record_id,true)}<div class="dialog-body"><div class="detail-meta"><div><span>Durum</span>${badge(t.status,t.reconciliation_required)}</div><div><span>Deneme</span><strong>${t.attempt_count} / ${t.max_attempts}</strong></div><div><span>Worker</span><strong>${escapeHtml(t.owner_id || '—')}</strong></div><div><span>Sahiplik sürümü</span><strong>${t.fencing_token}</strong></div><div><span>Lease bitişi</span><strong>${date(t.lease_expires_at)}</strong></div><div><span>Güncelleme</span><strong>${date(t.updated_at)}</strong></div></div>${recovery}<section class="detail-section"><h3>Kayıt bağlantıları</h3><div class="transaction-links"><div><span class="text-small muted">Dış referans</span><strong class="wrap external-reference">${escapeHtml(t.external_reference || '—')}</strong></div><a class="button small" href="#jobs/${t.job_id}" data-action="open-transaction-job" data-job-id="${t.job_id}">Kayıtlı olduğu işi aç ${icon('arrow')}</a></div></section><section class="detail-section"><h3>Tekil işlem anahtarı</h3><div class="code-block">${escapeHtml(t.idempotency_key)}</div></section><section class="detail-section"><h3>Kayıtlı ilerleme</h3>${data.checkpoints.length ? `<div class="checkpoints-list">${data.checkpoints.map(c => `<div><div class="checkpoint-head">${escapeHtml(c.checkpoint_key)}<span>${date(c.updated_at)}</span></div><pre class="code-block">${escapeHtml(JSON.stringify(c.checkpoint_value,null,2))}</pre></div>`).join('')}</div>` : '<p class="text-small muted">Henüz checkpoint kaydedilmedi.</p>'}</section>${Object.keys(t.result_data || {}).length ? `<section class="detail-section"><h3>İşlem sonucu</h3><pre class="code-block">${escapeHtml(JSON.stringify(t.result_data,null,2))}</pre></section>` : ''}${data.errors.length ? `<section class="detail-section"><h3>Hata geçmişi</h3>${data.errors.map(e => `<div class="code-block">${escapeHtml(e.error_type)} · ${date(e.created_at)}</div>`).join('')}</section>` : ''}<section class="detail-section"><h3>İşlem geçmişi</h3><ol class="timeline">${data.audit.map(a => `<li>${escapeHtml(auditLabels[a.action] || a.action)}<time>${date(a.created_at)}</time></li>`).join('')}</ol></section></div>`;
    if (!transactionDialog.open) transactionDialog.showModal();
  } catch(error) { toast(error.message,true); }
}

document.addEventListener('click',async event => {
  const button=event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const action=button.dataset.action;
  if (['demo','new-job','add-record','cancel'].includes(action)) return showForm(action,button.dataset.workflow || '');
  if (action === 'close-dialog') return formDialog.close();
  if (action === 'close-transaction') return transactionDialog.close();
  if (action === 'open-transaction-job') { transactionDialog.close();location.hash=`jobs/${button.dataset.jobId}`;return; }
  if (action === 'refresh') return refresh();
  if (action === 'back-jobs') { location.hash='jobs';return; }
  if (action === 'review-filter') { state.review=!state.review;state.page=0;return refresh(); }
  if (action === 'next' || action === 'previous') { state.page+=action === 'next' ? 1 : -1;return refresh(); }
  if (action === 'transaction') return openTransaction(button.dataset.id);
  button.disabled=true;
  try {
    if (action === 'reconcile') {
      if (!$('#reconcile-confirm')?.checked) return;
      await api(`/api/transactions/${state.transaction.id}/reconcile`,{expected_fencing_token:state.transaction.fencing_token,outcome:button.dataset.outcome});
      toast('İşlem sonucu kaydedildi.');transactionDialog.close();await refresh();
    } else if (['start','finalize','recover'].includes(action)) {
      const result=await api(`/api/jobs/${state.jobId}/${action}`,{});
      toast(action === 'recover' ? `${result.count} kayıt kurtarma kontrolünden geçti.` : action === 'start' ? 'İş başlatıldı.' : 'İş tamamlandı.');await refresh();
    }
  } catch(error) { if (action === 'reconcile') $('#reconcile-error').textContent=error.message; else toast(error.message,true); }
  finally { button.disabled=false; }
});

document.addEventListener('submit',async event => {
  if (event.target.id !== 'action-form') return;
  event.preventDefault();
  const form=event.target;
  const submit=$('[type="submit"]',form);
  if (submit.disabled) return;
  const values=Object.fromEntries(new FormData(form));
  submit.disabled=true;$('#form-error').textContent='';
  try {
    const type=form.dataset.type;
    let result;
    if (type === 'demo') result=await api('/api/demo-jobs',{scenario:values.scenario,record_count:Number(values.record_count),request_id:form.dataset.requestId});
    if (type === 'new-job') result=await api('/api/jobs',{job_id:values.job_id.trim(),workflow_name:values.workflow_name.trim()});
    if (type === 'add-record') result=await api(`/api/jobs/${form.dataset.jobId}/transactions`,{record_id:values.record_id.trim(),idempotency_key:values.idempotency_key.trim(),max_attempts:Number(values.max_attempts),...(values.external_reference.trim() ? {external_reference:values.external_reference.trim()} : {})});
    if (type === 'cancel') result=await api(`/api/jobs/${form.dataset.jobId}/cancel`,{});
    formDialog.close();
    if (type === 'add-record' && result.transaction.job_id !== form.dataset.jobId) {
      toast('Bu kayıt başka bir işte zaten mevcut. Kayıtlı olduğu iş açıldı.');
      location.hash=`jobs/${result.transaction.job_id}`;
      return;
    }
    toast({demo:'Demo başlatıldı. Worker’lar kayıtları işliyor.','new-job':'Yeni iş oluşturuldu.','add-record':'Kayıt kuyruğa eklendi.',cancel:'İş iptal edildi.'}[type]);
    if (type === 'demo' || type === 'new-job') location.hash=`jobs/${result.job.id}`;
    else await refresh();
  } catch(error) { $('#form-error').textContent=error.message; }
  finally { submit.disabled=false; }
});

let searchTimer;
document.addEventListener('input',event => {
  if (event.target.id === 'table-search') { state.query=event.target.value;state.page=0;state.sequence++;state.loading=false;clearTimeout(searchTimer);searchTimer=setTimeout(() => refresh(),350); }
});
document.addEventListener('change',event => {
  if (event.target.id === 'status-filter') { state.filter=event.target.value;state.page=0;refresh(); }
  if (event.target.id === 'reconcile-confirm') document.querySelectorAll('[data-action="reconcile"]').forEach(button => button.disabled=!event.target.checked);
});
$('#menu-button').addEventListener('click',() => { const isOpen=$('#sidebar').classList.toggle('open');$('#menu-button').setAttribute('aria-expanded',String(isOpen)); });
document.addEventListener('click',event => { if (innerWidth <= 720 && !event.target.closest('#sidebar') && !event.target.closest('#menu-button')) { $('#sidebar').classList.remove('open');$('#menu-button').setAttribute('aria-expanded','false'); } });
window.addEventListener('hashchange',navigate);
setInterval(() => { if (!document.hidden && !state.loading && !formDialog.open && !transactionDialog.open && !document.activeElement?.matches('input,select,textarea')) refresh(true); },3000);
navigate();
