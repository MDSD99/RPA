'use strict';

const SchedulerUI = (() => {
  const esc=value=>escapeHtml(value);
  let opening=0,selection=0;
  const zone=()=>Intl.DateTimeFormat().resolvedOptions().timeZone || 'Yerel saat';
  function localInput(value){
    const d=new Date(value),pad=number=>String(number).padStart(2,'0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function futureDefault(){return localInput(Date.now()+5*60*1000);}
  function fullDate(value){return value?new Intl.DateTimeFormat('tr-TR',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)):'—';}
  function cadence(item){
    if(item.kind==='once')return 'Tek sefer';
    const seconds=item.interval_seconds;
    return seconds%86400===0?`${seconds/86400*24} saat aralıkla`:seconds%3600===0?`${seconds/3600} saat aralıkla`:seconds%60===0?`${seconds/60} dakika aralıkla`:`${seconds} saniye aralıkla`;
  }
  function scheduleState(item){return !item.next_run_at?'completed':!item.enabled?'paused':'enabled';}
  function scheduleBadge(item){const status=scheduleState(item);return `<span data-schedule-status="${status}" class="status-badge ${status==='enabled'?'status-idle':status==='paused'?'status-pending':'status-completed'}">${{enabled:'Etkin',paused:'Duraklatıldı',completed:'Tetiklendi'}[status]}</span>`;}
  function runtimeState(snapshot){
    if(!snapshot?.enabled)return {label:'Planlayıcı kapalı',ready:false,description:'Zamanlamalar kaydedilir; planlayıcı çalışırken zamanı gelen işler kuyruğa alınır.'};
    if(snapshot.state==='error')return {label:'Planlayıcı bağlantı hatası',ready:false,description:'Zamanlamalar veritabanında korunuyor. Bağlantı düzeldiğinde zamanı geçen tetiklemeler kontrol edilecek.'};
    if(['stopped','stopping'].includes(snapshot.state))return {label:'Planlayıcı durdu',ready:false,description:'Zamanlamalar veritabanında korunuyor. Yeni tetiklemeler için planlayıcı çalışmalı.'};
    return {label:'Planlayıcı çalışıyor',ready:true,description:snapshot.last_tick?`Son kontrol ${fullDate(snapshot.last_tick)}`:'İlk kontrol bekleniyor'};
  }
  function page(data){
    const runtime=runtimeState(data.scheduler);
    return `<div class="page-heading"><div><div class="eyebrow">OTOMASYON TAKVİMİ</div><h1>Zamanlamalar</h1><p>Akışlarının ne zaman çalışacağını belirle. Sonuçları tek yerden takip et.</p></div><button class="button primary" data-schedule-action="new">${icon('plus')}Zamanlama oluştur</button></div><section class="schedule-overview"><div class="schedule-overview-icon">${icon('calendar')}</div><div class="schedule-overview-copy"><span class="eyebrow">ZAMANINDA, KONTROLLÜ</span><h2>Sen planla.<br>Akışın zamanı gelince çalışsın.</h2><p>Bir tarih seç veya tekrar aralığı belirle.<br>Önceki iş bitmeden aynı zamanlama yeni bir iş başlatmaz.</p></div><div class="scheduler-status ${runtime.ready?'ready':''}"><span class="scheduler-status-dot"></span><strong>${esc(runtime.label)}</strong><small>${esc(runtime.description)}</small><span class="schedule-zone">${icon('globe')}${esc(zone())}</span></div></section><div class="schedule-list-heading"><h2>Kayıtlı zamanlamalar <span class="tag">${number(data.total)}</span></h2><button class="button small" data-action="refresh">${icon('refresh')}Yenile</button></div>${data.items.length?`<div class="schedule-list">${data.items.map(card).join('')}</div>${pagination(data.total)}`:`<section class="panel schedule-empty"><span class="empty-icon">${icon('calendar')}</span><h2>İlk zamanlamanı oluştur</h2><p>Kaydettiğin bir akışı seç, girdilerini ve çalışma zamanını belirle.</p><button class="button primary" data-schedule-action="new">${icon('plus')}Zamanlama oluştur</button><a href="#workflows" class="button ghost small">Akış kütüphanesine git ${icon('arrow')}</a></section>`}<p class="schedule-policy">${icon('check')}Kaçırılan tekrarlar tek çalıştırmada birleştirilir. Duraklatmak gelecekteki tetiklemeleri durdurur; başlamış işi iptal etmez.</p>`;
  }
  function card(item){
    const status=scheduleState(item),next=item.next_run_at?new Date(item.next_run_at):null;
    return `<article class="schedule-card" data-schedule-id="${item.id}"><div class="schedule-date-tile"><span>${next?new Intl.DateTimeFormat('tr-TR',{month:'short'}).format(next):'BİTTİ'}</span><strong>${next?next.getDate():icon('check')}</strong><small>${next?new Intl.DateTimeFormat('tr-TR',{hour:'2-digit',minute:'2-digit'}).format(next):'Tek sefer'}</small></div><div class="schedule-card-main"><div class="schedule-card-title"><h3>${esc(item.name)}</h3>${scheduleBadge(item)}</div><a class="schedule-flow" href="#flows/${item.flow_id}">${icon('flow')}${esc(item.flow_name || 'Görsel akış')}<span>Sürüm ${item.version}</span></a><div class="schedule-card-details"><span>${icon('refresh')}${cadence(item)}</span><span>${icon('play')}${number(item.run_count)} çalıştırma</span>${item.missed_runs?`<span title="Kaçırılan tekrarlar için ayrı işler açılmaz">${icon('clock')}${number(item.missed_runs)} tekrar birleştirildi</span>`:''}</div><p class="schedule-next">${status==='completed'?'Bu tek seferlik zamanlama tetiklendi.':`${status==='paused'?'Planlanan':'Sonraki'} tetikleme: ${fullDate(item.next_run_at)}`}${item.last_run_at?`<span>Son tetikleme: ${fullDate(item.last_run_at)}</span>`:''}</p>${item.last_job_id?`<a href="#jobs/${item.last_job_id}" class="schedule-last-job" data-last-job="${item.last_job_id}">Son işi aç ${icon('arrow')}</a>`:''}</div><div class="schedule-card-actions"><button class="button small" data-schedule-action="edit" data-id="${item.id}">${icon('edit')}Düzenle</button>${status!=='completed'?`<button class="button small ${status==='paused'?'primary':''}" data-schedule-action="${status==='paused'?'resume':'pause'}" data-id="${item.id}" data-revision="${item.revision}">${icon(status==='paused'?'play':'pause')}${status==='paused'?'Devam ettir':'Duraklat'}</button>`:''}</div></article>`;
  }
  function inputRow(key='',value='',index=0){
    const kind=value===null||typeof value==='object'?'json':typeof value;
    return `<div class="run-input-row"><input name="input-key" aria-label="Girdi adı ${index+1}" placeholder="girdi" value="${esc(key)}"><select name="input-type" aria-label="Girdi türü ${index+1}">${[['string','Metin'],['number','Sayı'],['boolean','Mantıksal'],['json','JSON']].map(([type,label])=>`<option value="${type}" ${type===kind?'selected':''}>${label}</option>`).join('')}</select><input name="input-value" aria-label="Girdi değeri ${index+1}" placeholder="Değer" value="${esc(kind==='string'?value:JSON.stringify(value))}"><button type="button" class="icon-button" data-schedule-action="remove-input" aria-label="Girdiyi kaldır">${icon('close')}</button></div>`;
  }
  function references(graph){
    const assigned=new Set(graph.nodes.filter(n=>['set','browser_extract'].includes(n.type)).map(n=>n.config.key)),keys=new Set();
    for(const match of JSON.stringify(graph.nodes.map(n=>n.config)).matchAll(/\{\{\s*([A-Za-z][A-Za-z0-9_]*)(?:\.[^}]+)?\s*\}\}/g))if(!assigned.has(match[1]))keys.add(match[1]);
    return [...keys];
  }
  async function open(flow=null,id=null){
    const ticket=++opening,originalHash=location.hash;
    let schedule=null,flows=[];
    try{
      if(id){schedule=(await api(`/api/schedules/${id}`)).schedule;flow=(await api(`/api/flows/${schedule.flow_id}`)).flow;}
      if(!flow){flows=(await api('/api/flows?limit=500')).items;if(flows.length)flow=(await api(`/api/flows/${flows[0].id}`)).flow;}
      if(ticket!==opening||location.hash!==originalHash)return;
      if(!flow){$('#dialog-content').innerHTML=`${dialogHeader('Önce bir akış oluştur','Zamanlama, kaydettiğin akış sürümünü çalıştırır.')}<div class="dialog-body"><p class="text-small muted">Henüz kaydedilmiş bir akış bulunmuyor.</p><a href="#workflows" class="button primary">Akış kütüphanesini aç ${icon('arrow')}</a></div>`;formDialog.showModal();return;}
      const version=schedule?.version||flow.version,selectedFlowId=flow.id;
      const inputValues=schedule?Object.entries(schedule.inputs):references(flow.graph).map(key=>[key,'']);
      const flowChoices=flows.length?`<select name="flow_id" id="schedule-flow">${flows.map(item=>`<option value="${item.id}" ${item.id===selectedFlowId?'selected':''}>${esc(item.name)}</option>`).join('')}</select>`:`<input id="schedule-flow" value="${esc(schedule?.flow_name || flow.name)}" readonly><input name="flow_id" type="hidden" value="${selectedFlowId}">`;
      $('#dialog-content').innerHTML=`${dialogHeader(schedule?'Zamanlamayı düzenle':'Zamanlama oluştur','Kaydedilmiş akış sürümünün ne zaman çalışacağını belirle.')}<form id="schedule-form" data-id="${schedule?.id||''}" data-request-id="${crypto.randomUUID()}" data-revision="${schedule?.revision||''}" data-flow-id="${selectedFlowId}"><div class="dialog-body"><div class="field-group"><label for="schedule-name">Zamanlama adı</label><input id="schedule-name" name="name" required maxlength="160" value="${esc(schedule?.name||flow.name)}" autocomplete="off"></div><div class="schedule-form-grid"><div class="field-group"><label for="schedule-flow">İş akışı</label>${flowChoices}</div><div class="field-group"><label for="schedule-version">Akış sürümü</label><select id="schedule-version" name="version">${[...new Set([version,flow.version])].map(value=>`<option value="${value}" ${value===version?'selected':''}>Sürüm ${value}${value===flow.version?' · Güncel':''}</option>`).join('')}</select></div></div><div class="schedule-version-note">${icon('flow')}<span>Akışı sonradan düzenlesen de bu zamanlama seçtiğin sürümü kullanır.</span></div><div class="schedule-form-grid"><div class="field-group"><label for="schedule-kind">Çalıştırma düzeni</label><select id="schedule-kind" name="kind"><option value="once" ${schedule?.kind!=='interval'?'selected':''}>Tek sefer</option><option value="interval" ${schedule?.kind==='interval'?'selected':''}>Belirli aralıklarla</option></select></div><div class="field-group" id="schedule-interval-field" ${schedule?.kind!=='interval'?'hidden':''}><label for="schedule-interval">Tekrar aralığı (saniye)</label><input id="schedule-interval" name="interval_seconds" type="number" min="60" max="2678400" step="1" value="${schedule?.interval_seconds||3600}"><small>En az 60 saniye. 1 saat = 3600 saniye.</small></div></div><div class="field-group"><label for="schedule-date">${schedule?'Sonraki':'İlk'} çalıştırma tarihi ve saati</label><input id="schedule-date" name="next_run_at" type="datetime-local" required value="${schedule?.next_run_at?localInput(schedule.next_run_at):futureDefault()}"><small>Saat dilimi: <strong>${esc(zone())}</strong>. Seçim UTC olarak kaydedilir.</small><span class="schedule-date-preview" id="schedule-date-preview"></span></div><div class="run-input-heading"><strong>Giriş değişkenleri</strong><button type="button" class="button small" data-schedule-action="add-input">${icon('plus')}Girdi ekle</button></div><div id="schedule-inputs">${inputValues.map(([key,value],i)=>inputRow(key,value,i)).join('')}</div><p class="text-small muted">Her tetiklemede bu girdiler kullanılır. Şifre veya erişim anahtarı girme.</p><div class="schedule-form-grid"><div class="field-group"><label for="schedule-attempts">En fazla deneme</label><input id="schedule-attempts" name="max_attempts" type="number" min="1" max="100" value="${schedule?.max_attempts||3}" required></div><label class="schedule-enabled"><input name="enabled" type="checkbox" ${schedule?.enabled!==false?'checked':''}><span>Kaydettikten sonra etkin</span></label></div><div class="schedule-policy-note">Önceki iş bitmeden yeni iş başlatılmaz. Kaçırılan tekrarlar tek çalıştırmada birleştirilir.</div><div id="schedule-error" class="form-error" role="alert"></div></div><div class="dialog-footer"><button type="button" class="button" data-action="close-dialog">Vazgeç</button><button type="submit" class="button primary">${icon('calendar')}Zamanlamayı kaydet</button></div></form>`;
      formDialog.showModal();preview();
    }catch(error){toast(error.message,true);}
  }
  function preview(){
    const input=$('#schedule-date'),element=$('#schedule-date-preview');if(!input||!element)return;
    const value=new Date(input.value);element.textContent=Number.isFinite(value.valueOf())?`UTC: ${value.toISOString().slice(0,16).replace('T',' ')} · ${$('#schedule-kind').value==='once'?'bir kez':`ardından ${$('#schedule-interval').value} saniyede bir`}`:'';
  }
  function parseInputs(form){
    const inputs={};for(const row of form.querySelectorAll('.run-input-row')){
      const key=$('[name="input-key"]',row).value.trim(),raw=$('[name="input-value"]',row).value,type=$('[name="input-type"]',row).value;
      if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key))throw new Error('Girdi adları harfle başlamalı; harf, rakam ve alt çizgi içerebilir.');
      if(Object.hasOwn(inputs,key))throw new Error('Girdi adları benzersiz olmalı.');
      let value=raw;
      if(type==='number'){value=Number(raw);if(!raw.trim()||!Number.isFinite(value))throw new Error('Girdi için geçerli bir sayı gir.');}
      if(type==='boolean'){if(!['true','false'].includes(raw))throw new Error('Mantıksal girdi true veya false olmalı.');value=raw==='true';}
      if(type==='json'){try{value=JSON.parse(raw);}catch{throw new Error('JSON girdisini kontrol et.');}}
      Object.defineProperty(inputs,key,{value,enumerable:true});
    }
    return inputs;
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-schedule-action]');if(!button||button.disabled)return;event.preventDefault();
    const action=button.dataset.scheduleAction;
    if(action==='new'){button.disabled=true;try{await open();}finally{button.disabled=false;}return;}
    if(action==='edit'){button.disabled=true;try{await open(null,button.dataset.id);}finally{button.disabled=false;}return;}
    if(action==='add-input'){const holder=$('#schedule-inputs');holder.insertAdjacentHTML('beforeend',inputRow('','',holder.children.length));return;}
    if(action==='remove-input'){button.closest('.run-input-row').remove();return;}
    if(['pause','resume'].includes(action)){
      button.disabled=true;
      try{await api(`/api/schedules/${button.dataset.id}/${action}`,{expected_revision:Number(button.dataset.revision)});toast(action==='pause'?'Gelecekteki tetiklemeler duraklatıldı.':'Zamanlama yeniden etkin.');await refresh();}
      catch(error){toast(error.message,true);await refresh();}
      finally{button.disabled=false;}
    }
  });
  document.addEventListener('change',async event=>{
    if(event.target.id==='schedule-kind'){const field=$('#schedule-interval-field');field.hidden=event.target.value!=='interval';preview();}
    if(event.target.id==='schedule-flow'&&event.target.tagName==='SELECT'){
      const ticket=++selection,form=$('#schedule-form'),submit=$('[type="submit"]',form),flowId=event.target.value;submit.disabled=true;form.dataset.flowId='';form.dataset.flowLoading='1';
      try{const flow=(await api(`/api/flows/${flowId}`)).flow;if(ticket!==selection||!form.isConnected)return;form.dataset.flowId=flow.id;form.dataset.flowLoading='';$('#schedule-version').innerHTML=`<option value="${flow.version}">Sürüm ${flow.version} · Güncel</option>`;$('#schedule-inputs').innerHTML=references(flow.graph).map((key,i)=>inputRow(key,'',i)).join('');$('#schedule-error').textContent='';}
      catch(error){if(form.isConnected&&ticket===selection)$('#schedule-error').textContent=error.message+' Akışı tekrar seçip yüklenmesini bekle.';}
      finally{if(form.isConnected&&ticket===selection)submit.disabled=!form.dataset.flowId;}
    }
  });
  document.addEventListener('input',event=>{if(['schedule-date','schedule-interval'].includes(event.target.id))preview();});
  document.addEventListener('submit',async event=>{
    if(event.target.id!=='schedule-form')return;event.preventDefault();
    const form=event.target,submit=$('[type="submit"]',form),errorBox=$('#schedule-error');if(submit.disabled)return;submit.disabled=true;errorBox.textContent='';
    try{
      if(!form.dataset.flowId||form.dataset.flowLoading)throw new Error('Seçtiğin akış henüz yüklenmedi.');
      if(form.elements.flow_id?.value!==form.dataset.flowId)throw new Error('Seçtiğin akış değişti. Akışı tekrar seç.');
      const values=Object.fromEntries(new FormData(form)),when=new Date(values.next_run_at);
      if(!Number.isFinite(when.valueOf())||when.valueOf()<=Date.now())throw new Error('Gelecekte bir tarih ve saat seç.');
      // datetime-local values in a skipped daylight-saving hour must not silently move.
      if(localInput(when)!==values.next_run_at)throw new Error('Bu yerel saat saat diliminde geçerli değil. Farklı bir saat seç.');
      const body={name:values.name.trim(),flow_id:form.dataset.flowId,version:Number(values.version),kind:values.kind,next_run_at:when.toISOString(),interval_seconds:values.kind==='interval'?Number(values.interval_seconds):null,inputs:parseInputs(form),max_attempts:Number(values.max_attempts),enabled:form.elements.enabled.checked};
      if(form.dataset.id)body.expected_revision=Number(form.dataset.revision);else body.request_id=form.dataset.requestId;
      await api(form.dataset.id?`/api/schedules/${form.dataset.id}`:'/api/schedules',body,form.dataset.id?'PUT':'POST');
      toast('Zamanlama kaydedildi.');if(form.isConnected && $('#schedule-form')===form && formDialog.open){formDialog.close();if(state.route==='schedules')await refresh();else location.hash='schedules';}
    }catch(error){errorBox.textContent=error.message;}
    finally{submit.disabled=!form.dataset.flowId;}
  });
  return {page,open};
})();
