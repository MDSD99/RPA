'use strict';

const FlowUI = (() => {
  const types = {
    start:{title:'Başlangıç',icon:'play',color:'green',description:'Akış girdilerini al',config:{}},
    set:{title:'Değer ata',icon:'queue',color:'blue',description:'Bir değişken oluştur',config:{key:'sonuc',value:'Hazır'}},
    condition:{title:'Koşul',icon:'flow',color:'amber',description:'Sonuca göre dallan',config:{left:'{{girdi}}',operator:'equals',right:'evet'}},
    delay:{title:'Bekle',icon:'clock',color:'violet',description:'Adımlar arasına süre ekle',config:{seconds:1}},
    browser_open:{title:'Sayfa aç',icon:'globe',color:'blue',description:'Tarayıcıda adresi aç',config:{url:'https://example.com'}},
    browser_fill:{title:'Alan doldur',icon:'edit',color:'violet',description:'Form alanına değer yaz',config:{selector:'#alan',value:'{{girdi}}'}},
    browser_click:{title:'Tıkla',icon:'pointer',color:'amber',description:'Sayfadaki öğeye tıkla',config:{selector:'button[type="submit"]'}},
    browser_extract:{title:'Metin oku',icon:'search',color:'blue',description:'Sayfadan değişkene aktar',config:{selector:'h1',key:'baslik'}},
    file_read:{title:'Dosya oku',icon:'search',color:'blue',description:'UTF-8 dosyasını değişkene al',config:{path:'notlar.txt',key:'metin'}},
    file_write:{title:'Dosya yaz',icon:'save',color:'green',description:'Çalışma klasörüne metin kaydet',config:{path:'notlar.txt',value:'{{metin}}'}},
    csv_read:{title:'CSV oku',icon:'grid',color:'blue',description:'CSV satırlarını tablo olarak al',config:{path:'veriler.csv',key:'satirlar'}},
    csv_write:{title:'CSV yaz',icon:'grid',color:'green',description:'Satırları CSV dosyasına kaydet',config:{path:'sonuc.csv',rows:[['Ad','Tutar'],['Örnek',100]]}},
    excel_read:{title:'Excel oku',icon:'grid',color:'blue',description:'XLSX sayfasını tablo olarak al',config:{path:'veriler.xlsx',sheet:'Sayfa1',key:'satirlar'}},
    excel_write:{title:'Excel yaz',icon:'grid',color:'green',description:'Satırları XLSX sayfasına kaydet',config:{path:'sonuc.xlsx',sheet:'Sayfa1',rows:[['Ad','Tutar'],['Örnek',100]]}},
    http_request:{title:'API isteği',icon:'globe',color:'violet',description:'İzinli HTTPS servisine bağlan',config:{url:'https://example.com/api',method:'GET',headers:{Accept:'application/json'},body:null,key:'yanit'}},
    email_send:{title:'E-posta gönder',icon:'arrow',color:'violet',description:'Kasadaki SMTP bağlantısını kullan',config:{secret_ref:'bildirim',to:'ornek@example.com',subject:'İşlem sonucu',body:'{{metin}}'}},
    windows_click:{title:'Windows tıkla',icon:'pointer',color:'amber',description:'Masaüstü UIA öğesine tıkla',config:{window_title:'Hesap Makinesi',auto_id:'num1Button',control_type:'Button'}},
    windows_fill:{title:'Windows doldur',icon:'edit',color:'violet',description:'UIA alanına metin yaz',config:{window_title:'Not Defteri',auto_id:'15',control_type:'Edit',value:'{{metin}}'}},
    windows_extract:{title:'Windows oku',icon:'search',color:'blue',description:'UIA öğesinin metnini al',config:{window_title:'Not Defteri',auto_id:'15',control_type:'Edit',key:'metin'}},
    end:{title:'Bitiş',icon:'check',color:'green',description:'Akış sonucunu döndür',config:{output:{sonuc:'Tamamlandı'}}}
  };
  let draft=null, selected=null, connect=null, zoom=1, opening=0, dirty=false, revision=0, pending=false;
  let templates='blank';
  const drafts=new Map();
  const copy=value=>structuredClone(value);
  const esc=value=>escapeHtml(value);
  const nodeById=id=>draft?.graph.nodes.find(node=>node.id===id);
  const ports=node=>node.type==='end' ? [] : node.type==='condition' ? ['true','false'] : ['next'];
  const portName=port=>({next:'Sonraki adım',true:'Evet',false:'Hayır'})[port];
  const nodeIcon=type=>icon(types[type]?.icon || 'jobs');
  const field=(name,label,value,extra='')=>`<div class="field-group"><label for="flow-${name}">${esc(label)}</label><input id="flow-${name}" data-config="${name}" value="${esc(value)}" ${extra}></div>`;
  const options=(values,current)=>values.map(([value,label])=>`<option value="${esc(value)}" ${value===current?'selected':''}>${esc(label)}</option>`).join('');

  /** @param {unknown} graph @returns {boolean} */
  function supportsGraph(graph) {
    if (!graph || typeof graph !== 'object' || !('nodes' in graph)) return false;
    return Array.isArray(graph.nodes) && graph.nodes.every(node =>
        node && typeof node === 'object' && !Array.isArray(node) &&
        typeof node.type === 'string' && Object.hasOwn(types, node.type) &&
        (!Object.hasOwn(node, 'type_version') || node.type_version === 1));
  }

  function template(kind='blank') {
    const n=(id,type,x,y,config,label)=>({id,type,type_version:1,label:label||types[type].title,position:{x,y},config:copy(config||types[type].config)});
    const e=(source,target,port='next')=>({source,target,port});
    if(kind==='condition') return {name:'Koşullu akış',description:'Girdiye göre iki farklı sonuç üreten başlangıç şablonu.',graph:{nodes:[n('start','start',80,160),n('condition','condition',390,160),n('yes','set',700,60,{key:'sonuc',value:'Onaylandı'},'Olumlu sonuç'),n('no','set',700,310,{key:'sonuc',value:'İncelemeye alındı'},'Diğer sonuç'),n('end','end',1020,160,{output:{sonuc:'{{sonuc}}'}})],edges:[e('start','condition'),e('condition','yes','true'),e('condition','no','false'),e('yes','end'),e('no','end')]}};
    if(kind==='browser') return {name:'Web sayfasından oku',description:'İzin verilen bir sayfanın başlığını oku ve sonucu kaydet.',graph:{nodes:[n('start','start',60,160),n('open','browser_open',350,160),n('read','browser_extract',640,160),n('end','end',930,160,{output:{baslik:'{{baslik}}'}})],edges:[e('start','open'),e('open','read'),e('read','end')]}};
    return {name:'Yeni akış',description:'',graph:{nodes:[n('start','start',100,160),n('end','end',440,160)],edges:[e('start','end')]}};
  }

  function miniGraph(flow) {
    return `<div class="flow-preview" aria-hidden="true"><span class="mini-node green">${icon('play')}</span><i></i><span class="mini-node blue">${icon('queue')}</span><i></i><span class="mini-node amber">${icon('flow')}</span><i></i><span class="mini-node green">${icon('check')}</span><span class="preview-count">${number(flow.node_count)} adım</span></div>`;
  }

  function catalog(data,catalog) {
    const flows=data.items;
    const custom=catalog.items.filter(item=>item.kind==='automation' && item.name!=='visual_flow');
    return `<div class="page-heading flow-catalog-heading"><div><div class="eyebrow">AKIŞ TASARIMCISI</div><h1>İş akışları</h1><p>Bir fikri çalışan otomasyona dönüştür. Adımların kontrolü sende.</p></div><div class="heading-actions"><button class="button" data-flow-action="import">${icon('arrow')}İçe aktar</button><button class="button primary" data-flow-action="new">${icon('plus')}Akış oluştur</button></div></div><section class="flow-hero"><div><span class="hero-label">SEN TASARLA. MULTIRPA ÇALIŞTIRSIN.</span><h2>Tekrarlayan işler,<br>düşündüğün gibi aksın.</h2><p>Adımları bağla, koşulları belirle ve sonuçları takip et.<br>Kod yazmadan, kendi akışınla başla.</p><button class="button" data-flow-action="new" data-template="condition">Koşullu akışla başla ${icon('arrow')}</button></div><div class="hero-flow" aria-hidden="true"><div class="hero-node">${icon('play')}Başlangıç<span class="online-dot"></span></div><div class="hero-line"></div><div class="hero-node decision">${icon('flow')}Koşulu kontrol et</div><div class="hero-branches"><span>Evet</span><span>Hayır</span></div><div class="hero-pair"><div class="hero-node">${icon('check')}Tamamla</div><div class="hero-node">${icon('clock')}Beklet</div></div></div></section><div class="flow-section-heading"><div><h2>Akış kütüphanen <span class="tag">${number(data.total ?? flows.length)}</span></h2><p>Kaydettiğin akışlar ve çalıştırmaya hazır sürümleri.</p></div><span class="live-label"><span class="online-dot"></span>${catalog.workers_enabled?'Çalıştırma motoru etkin':'Worker’lar kapalı'}</span></div><div class="flow-library">${flows.map(flow=>`<article class="saved-flow" data-flow-id="${flow.id}">${miniGraph(flow)}<div class="saved-flow-body"><div class="saved-flow-meta"><span class="tag">Sürüm ${flow.version}</span><span>${date(flow.updated_at)}</span></div><h3><a href="#flows/${flow.id}">${esc(flow.name)}</a></h3><p>${esc(flow.description || 'Kendi adımlarınla hazırlanmış otomasyon.')}</p><a class="button small" href="#flows/${flow.id}">Akışı aç ${icon('arrow')}</a></div></article>`).join('')}<button class="flow-create-card" data-flow-action="new">${icon('plus')}<strong>Yeni bir akış tasarla</strong><span>Boş bir tuvalle başla</span></button></div><div class="flow-section-heading"><div><h2>Hızlı başlangıç</h2><p>Bir şablon seç ve ihtiyacına göre düzenle.</p></div></div><div class="flow-templates">${[['condition','flow','Koşullu akış','Değerlere göre farklı yollardan ilerle.'],['browser','globe','Web sayfasından oku','Bir sayfayı aç ve metnini sonuç olarak al.']].map(([kind,symbol,title,desc])=>`<button class="flow-template" data-flow-action="new" data-template="${kind}"><span class="template-icon">${icon(symbol)}</span><span><strong>${title}</strong><small>${desc}</small></span>${icon('arrow')}</button>`).join('')}<button class="flow-template" data-action="demo" ${catalog.workers_enabled ? '' : 'disabled'}><span class="template-icon">${icon('spark')}</span><span><strong>Altyapı demosu</strong><small>Tekrar deneme ve kesinti kurtarmayı incele.</small></span>${icon('arrow')}</button></div>${custom.length?`<div class="flow-section-heading"><h2>Kayıtlı kod otomasyonları</h2></div><div class="workflow-grid">${custom.map(item=>`<article class="workflow-card" data-workflow-name="${esc(item.name)}"><h2>${esc(item.title)}</h2><p>${esc(item.description)}</p><button class="button" data-action="new-job" data-workflow="${esc(item.name)}">Yeni iş</button></article>`).join('')}</div>`:''}`;
  }

  function remember() {
    if(draft) drafts.set(draft.id||'new',{draft,dirty,selected,revision});
  }
  function leave(route) { if(route!=='flow'){opening++;remember();} }
  async function open(id) {
    const ticket=++opening;
    remember();
    const cached=drafts.get(id);
    if(cached) ({draft,dirty,selected,revision}=cached);
    else if(id==='new') {draft=template(templates);dirty=true;selected='start';revision=0;}
    else {
      main.innerHTML='<div class="initial-loading"><span class="loader"></span>Akış yükleniyor…</div>';
      try { const data=await api(`/api/flows/${id}`);if(ticket!==opening)return;if(!supportsGraph(data.flow.graph))throw new Error('Bu akışın adımları bu MultiRPA sürümüyle desteklenmiyor. Uygulamayı güncelleyin.');draft=data.flow;dirty=false;selected=draft.graph.nodes[0]?.id;revision=0; }
      catch(error){if(ticket===opening){draft=null;selected=null;connect=null;dirty=false;main.innerHTML=`<div class="offline-banner">${esc(error.message)}<a class="button" href="#workflows">Akışlara dön</a></div>`;}return;}
    }
    connect=null;zoom=1;render();loadHistory();
    $('#connection-status').innerHTML='<span class="online-dot"></span>Sistem çalışıyor';
  }
  function markDirty(){dirty=true;revision++;remember();status();}
  function status() {
    const element=$('#flow-save-state');if(element){element.textContent=pending?'Kaydediliyor…':dirty?'Kaydedilmemiş değişiklikler':`Sürüm ${draft.version} · Kaydedildi`;element.classList.toggle('unsaved',dirty);}
    document.querySelectorAll('[data-flow-action="save"],[data-flow-action="run"],[data-flow-action="schedule"]').forEach(b=>b.disabled=pending);
    const exporter=$('[data-flow-action="export"]');if(exporter){exporter.disabled=pending||dirty||!draft?.id;exporter.title=dirty||!draft?.id?'Dışa aktarmak için önce değişiklikleri kaydedin':'Kaydedilmiş sürümü indir; sabit metinler de dosyada yer alır';}
  }
  function render() {
    main.innerHTML=`<div class="editor-heading"><div class="editor-name"><a href="#workflows" class="icon-button" aria-label="Akışlara dön">${icon('back')}</a><div><span class="eyebrow">AKIŞ TASARIMCISI</span><input id="flow-name" aria-label="Akış adı" maxlength="160" value="${esc(draft.name)}"><span id="flow-save-state" role="status"></span></div></div><div class="editor-actions"><button class="button" data-flow-action="export" title="Kaydedilmiş sürümü indir; sabit metinler de dosyada yer alır">${icon('arrow')}Dışa aktar</button><button class="button" data-flow-action="schedule">${icon('calendar')}Zamanla</button><button class="button" data-flow-action="save">${icon('save')}Kaydet</button><button class="button primary" data-flow-action="run">${icon('play')}Çalıştır</button></div></div><div class="editor-description"><input id="flow-description" aria-label="Akış açıklaması" placeholder="Akışın ne yaptığını kısaca açıkla…" maxlength="1000" value="${esc(draft.description)}"><span class="tag" id="flow-node-count">${draft.graph.nodes.length} adım</span></div><div id="flow-validation" class="flow-validation" role="alert" hidden></div><div class="flow-studio"><aside class="node-library" aria-label="Adım kütüphanesi"><div class="studio-panel-heading"><h2>Adımlar</h2><span>EKLE</span></div><p>Akışına bir adım ekle.</p><div class="node-group-label">AKIŞ KONTROLÜ</div>${['set','condition','delay','end'].map(libraryButton).join('')}<div class="node-group-label">TARAYICI</div>${['browser_open','browser_fill','browser_click','browser_extract'].map(libraryButton).join('')}<div class="node-group-label">DOSYA VE TABLO</div>${['file_read','file_write','csv_read','csv_write','excel_read','excel_write'].map(libraryButton).join('')}<div class="node-group-label">API VE E-POSTA</div>${['http_request','email_send'].map(libraryButton).join('')}<div class="node-group-label">WINDOWS MASAÜSTÜ</div>${['windows_click','windows_fill','windows_extract'].map(libraryButton).join('')}<div class="library-tip">${icon('spark')}<p>Bağlamak için adımın çıkışına, ardından hedef adımın girişine tıkla.</p></div></aside><section class="canvas-shell" aria-label="Görsel akış tuvali"><div class="canvas-topline"><span><span class="online-dot"></span>Akış tuvali</span><div class="canvas-tools"><button class="button small" data-flow-action="fit" title="Tüm akışı tuvale sığdır">${icon('search')}Sığdır</button><button class="button small" data-flow-action="layout" title="Adımları otomatik yerleştir">${icon('grid')}Düzenle</button></div></div><div id="flow-viewport" tabindex="0" aria-label="Kaydırılabilir akış alanı"><div id="flow-canvas-space"><div id="flow-canvas"><svg id="flow-edges" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg><div id="flow-nodes"></div></div></div></div><div class="canvas-bottomline"><span id="connection-hint">Adımları sürükle · Çıkışları bağla</span><div class="zoom-controls"><button class="icon-button" data-flow-action="zoom-out" aria-label="Tuvali küçült">−</button><span id="flow-zoom">100%</span><button class="icon-button" data-flow-action="zoom-in" aria-label="Tuvali büyüt">+</button></div></div></section><aside class="node-inspector" aria-label="Adım ayarları" id="node-inspector"></aside></div><section id="flow-run-history"></section>`;
    drawNodes();inspector();status();
  }
  function libraryButton(type){const meta=types[type];return `<button class="library-node" data-flow-action="add" data-type="${type}" aria-label="${meta.title} adımı ekle"><span class="node-symbol ${meta.color}">${nodeIcon(type)}</span><span><strong>${meta.title}</strong><small>${meta.description}</small></span><span class="library-plus">+</span></button>`;}
  function summary(node) {
    const c=node.config;
    if(node.type==='set')return `${c.key} ← ${typeof c.value==='object'?JSON.stringify(c.value):c.value}`;
    if(node.type==='condition')return `${c.left} ${ {equals:'=',not_equals:'≠',contains:'içerir',greater_than:'>'}[c.operator]} ${c.right}`;
    if(node.type==='delay')return `${c.seconds} saniye`;
    if(node.type==='browser_open')return c.url;
    if(node.type==='browser_fill')return `${c.selector} ← ${c.value}`;
    if(node.type==='browser_click')return c.selector;
    if(node.type==='browser_extract')return `${c.selector} → ${c.key}`;
    if(c.path)return c.path+(c.key?` → ${c.key}`:'');
    if(node.type==='http_request')return `${c.method} ${c.url}`;
    if(node.type==='email_send')return `${c.to} · ${c.subject}`;
    if(node.type.startsWith('windows_'))return `${c.window_title} · ${c.auto_id}`;
    return node.type==='start'?'Çalıştırma girdileri':`${Object.keys(c.output||{}).length} sonuç alanı`;
  }
  function drawNodes(){
    const layer=$('#flow-nodes');if(!layer)return;
    layer.innerHTML=draft.graph.nodes.map(node=>`<article class="flow-node ${node.id===selected?'selected':''} ${connect&&node.type!=='start'?'connect-target':''}" data-node="${node.id}">${node.type!=='start'?`<button class="node-input" data-flow-action="target" data-id="${node.id}" aria-label="${esc(node.label)} adımına bağla"></button>`:''}<button class="node-title" data-flow-action="select" data-id="${node.id}" aria-label="${esc(node.label)} adımını düzenle"><span class="node-symbol ${types[node.type].color}">${nodeIcon(node.type)}</span><span><small>${types[node.type].title}</small><strong>${esc(node.label)}</strong></span></button><div class="node-summary">${esc(summary(node))}</div><div class="node-ports">${ports(node).map(port=>`<button data-flow-action="connect" data-id="${node.id}" data-port="${port}" class="node-output ${port} ${connect?.source===node.id&&connect.port===port?'connecting':''}" aria-label="${esc(node.label)}: ${portName(port)} bağla"><span>${port==='next'?'Devam':portName(port)}</span><i></i></button>`).join('')}</div></article>`).join('');
    draft.graph.nodes.forEach(node=>{const element=$(`[data-node="${node.id}"]`);element.style.left=`${node.position.x}px`;element.style.top=`${node.position.y}px`;});
    $('#flow-node-count').textContent=`${draft.graph.nodes.length} adım`;
    resizeCanvas();drawEdges();
  }
  function resizeCanvas(){
    const width=Math.max(1250,...draft.graph.nodes.map(n=>n.position.x+310));
    const height=Math.max(650,...draft.graph.nodes.map(n=>n.position.y+230));
    const canvas=$('#flow-canvas'), space=$('#flow-canvas-space');
    if(!canvas)return;
    canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;canvas.style.transform=`scale(${zoom})`;
    space.style.width=`${width*zoom}px`;space.style.height=`${height*zoom}px`;
    $('#flow-edges').setAttribute('viewBox',`0 0 ${width} ${height}`);
    $('#flow-edges').style.width=`${width}px`;$('#flow-edges').style.height=`${height}px`;
    $('#flow-zoom').textContent=`${Math.round(zoom*100)}%`;
  }
  function drawEdges(){
    const svg=$('#flow-edges');if(!svg)return;
    svg.innerHTML='<defs><marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7f9b8a" stroke="none"/></marker></defs>';
    for(const edge of draft.graph.edges){
      const a=nodeById(edge.source),b=nodeById(edge.target);if(!a||!b)continue;
      const portIndex=ports(a).indexOf(edge.port),startX=a.position.x+220,startY=a.position.y+111+portIndex*30,endX=b.position.x,endY=b.position.y+36;
      const curve=Math.max(55,Math.abs(endX-startX)*.42);
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');
      path.setAttribute('d',`M${startX},${startY} C${startX+curve},${startY} ${endX-curve},${endY} ${endX},${endY}`);path.setAttribute('class',`flow-edge ${edge.port}`);path.setAttribute('marker-end','url(#flow-arrow)');svg.append(path);
    }
  }
  function valueFields(name,label,value){
    const kind=value===null||typeof value==='object'?'json':typeof value;
    return `<div class="field-group"><label for="flow-${name}">${label}</label><div class="typed-value"><select data-value-type="${name}" aria-label="${label} türü">${options([['string','Metin / değişken'],['number','Sayı'],['boolean','Mantıksal'],['json','JSON']],kind)}</select><input id="flow-${name}" data-config="${name}" data-typed="1" value="${esc(kind==='json'?JSON.stringify(value):value)}"></div></div>`;
  }
  function inspector(){
    const target=$('#node-inspector'),node=nodeById(selected);if(!target)return;
    if(!node){target.innerHTML='<div class="inspector-empty">Bir adım seçerek ayarlarını düzenle.</div>';return;}
    const c=node.config;let fields='';
    if(node.type==='start')fields='<div class="inspector-note">Çalıştır düğmesinden giriş değişkenleri ekle. Diğer adımlarda <code>{{girdi}}</code> biçiminde kullan.</div>';
    if(node.type==='set')fields=field('key','Değişken adı',c.key,'maxlength="64"')+valueFields('value','Değer',c.value);
    if(node.type==='condition')fields=valueFields('left','Karşılaştırılan değer',c.left)+`<div class="field-group"><label for="flow-operator">Koşul</label><select id="flow-operator" data-config="operator">${options([['equals','Eşittir'],['not_equals','Eşit değildir'],['contains','İçerir'],['greater_than','Büyüktür']],c.operator)}</select></div>`+valueFields('right','Beklenen değer',c.right);
    if(node.type==='delay')fields=field('seconds','Bekleme süresi (saniye)',c.seconds,'type="number" min="0" max="10" step="0.1"');
    if(node.type==='browser_open')fields=field('url','Sayfa adresi',c.url,'placeholder="https://example.com"')+'<div class="inspector-note">Hedef alan adı sunucudaki izinli sitelere eklenmiş olmalı. HTTPS adresi kullan.</div>';
    if(['browser_fill','browser_click','browser_extract'].includes(node.type))fields=field('selector','Öğe seçicisi (CSS)',c.selector,'placeholder="#alan veya button"')+(node.type==='browser_fill'?field('value','Yazılacak değer',c.value,'maxlength="8192"'):node.type==='browser_extract'?field('key','Sonucu yazacağın değişken',c.key,'maxlength="64"'):'')+'<div class="inspector-note">Bu adımdan önce aynı yolda bir Sayfa aç adımı bulunmalı.</div>';
    if(['file_read','file_write','csv_read','csv_write','excel_read','excel_write'].includes(node.type))fields=field('path','Çalışma klasöründeki dosya',c.path,'placeholder="klasor/dosya.txt" maxlength="240"')+(node.type.startsWith('excel_')?field('sheet','Excel sayfa adı',c.sheet,'maxlength="31"'):'')+(c.key!==undefined?field('key','Sonuç değişkeni',c.key,'maxlength="64"'):'')+(c.value!==undefined?`<div class="field-group"><label for="flow-value">Yazılacak metin</label><textarea id="flow-value" data-config="value" rows="4">${esc(c.value)}</textarea></div>`:'')+(c.rows!==undefined?valueFields('rows','Satırlar (JSON dizi / değişken)',c.rows):'')+'<div class="inspector-note">Dosyalar sana ait çalışma klasöründe kalır. CSV/Excel en fazla 200 satır ve 32 sütun kullanır.</div>';
    if(node.type==='http_request')fields=field('url','API adresi',c.url,'placeholder="https://example.com/api"')+`<div class="field-group"><label for="flow-method">HTTP yöntemi</label><select id="flow-method" data-config="method">${options(['GET','POST','PUT','PATCH','DELETE'].map(x=>[x,x]),c.method)}</select></div>`+valueFields('headers','Genel başlıklar (JSON)',c.headers)+valueFields('body','JSON gövde (GET için null)',c.body)+field('key','Sonuç değişkeni',c.key)+field('secret_ref','Kasa bağlantısı (isteğe bağlı)',c.secret_ref||'')+'<div class="inspector-note">İzinli HTTPS alan adını kullan. Yanıt status/body alanlarıyla kaydedilir; kimlik bilgilerini başlıklara yazma.</div>';
    if(node.type==='email_send')fields=field('secret_ref','SMTP kasa bağlantısı',c.secret_ref)+field('to','Alıcı',c.to)+field('subject','Konu',c.subject)+`<div class="field-group"><label for="flow-body">E-posta metni</label><textarea id="flow-body" data-config="body" rows="4">${esc(c.body)}</textarea></div>`;
    if(node.type.startsWith('windows_'))fields=field('window_title','Pencerenin tam başlığı',c.window_title)+field('auto_id','UI Automation kimliği',c.auto_id)+`<div class="field-group"><label for="flow-control_type">Öğe türü</label><select id="flow-control_type" data-config="control_type">${options(['Button','Edit','Text','Document','ComboBox'].map(x=>[x,x]),c.control_type)}</select></div>`+(c.value!==undefined?field('value','Yazılacak metin',c.value):'')+(c.key!==undefined?field('key','Sonuç değişkeni',c.key):'')+'<div class="inspector-note">Bu adım Windows masaüstü uygulamasında çalışır. Açık pencere ve erişilebilir UIA öğesi gerekir.</div>';
    if(node.type==='browser_fill')fields+=field('secret_ref','Kasa bağlantısı (isteğe bağlı)',c.secret_ref||'')+`<div class="field-group"><label for="flow-secret_field">Kasadan alınan alan</label><select id="flow-secret_field" data-config="secret_field">${options([['username','Kullanıcı adı'],['password','Parola']],c.secret_field||'username')}</select></div><div class="inspector-note">Kasa kullanıyorsan Yazılacak değer alanını boş bırak.</div>`;
    if(node.type==='end')fields=`<div class="output-fields"><label>Çalıştırma sonucu</label>${Object.entries(c.output||{}).map(([key,value],i)=>`<div class="output-row"><input data-output-key="${i}" aria-label="Sonuç alanı ${i+1}" value="${esc(key)}" placeholder="Alan adı"><select data-output-type="${i}" aria-label="Sonuç türü ${i+1}">${options([['string','Metin / değişken'],['number','Sayı'],['boolean','Mantıksal'],['json','JSON']],value===null||typeof value==='object'?'json':typeof value)}</select><input data-output-value="${i}" aria-label="Sonuç değeri ${i+1}" value="${esc(typeof value==='string'?value:JSON.stringify(value))}" placeholder="{{sonuc}}"><button class="icon-button" data-flow-action="remove-output" data-index="${i}" aria-label="Sonuç alanını kaldır">${icon('close')}</button></div>`).join('')}<button class="button small" data-flow-action="add-output">${icon('plus')}Sonuç alanı ekle</button></div>`;
    target.innerHTML=`<div class="studio-panel-heading"><h2>Adım ayarları</h2><span class="node-symbol ${types[node.type].color}">${nodeIcon(node.type)}</span></div><form id="node-settings" data-node-id="${node.id}"><div class="field-group"><label for="node-label">Adım adı</label><input id="node-label" value="${esc(node.label)}" maxlength="100"></div>${fields}<div class="inspector-divider"></div>${ports(node).map(port=>`<div class="field-group"><label for="edge-${port}">${portName(port)}</label><select id="edge-${port}" data-edge="${port}"><option value="">Bağlanmadı</option>${draft.graph.nodes.filter(n=>n.id!==node.id&&n.type!=='start').map(n=>`<option value="${n.id}" ${draft.graph.edges.some(e=>e.source===node.id&&e.port===port&&e.target===n.id)?'selected':''}>${esc(n.label)}</option>`).join('')}</select></div>`).join('')}${node.type!=='start'?`<button type="button" class="button danger small" data-flow-action="delete">${icon('trash')}Adımı kaldır</button>`:''}</form><div class="inspector-footer"><span>DEĞİŞKEN KULLANIMI</span><code>{{degisken_adi}}</code><p>Değerleri girdilerden veya önceki adımlardan al. Şifre ve erişim anahtarı girme.</p></div>`;
  }
  function applyEdge(source,target,port){
    if(source===target||nodeById(target)?.type==='start')return toast('Bu iki adım bağlanamaz.',true);
    draft.graph.edges=draft.graph.edges.filter(e=>!(e.source===source&&e.port===port));
    if(target)draft.graph.edges.push({source,target,port});
    markDirty();connect=null;drawNodes();inspector();$('#connection-hint').textContent='Bağlantı güncellendi';
  }
  function typedInput(element){return parseTyped(element.value,$(`[data-value-type="${element.dataset.config}"]`)?.value);}
  function parseTyped(raw,kind){
    if(kind==='number'){const value=Number(raw);if(!raw.trim()||!Number.isFinite(value))throw new Error('Geçerli bir sayı gir.');return value;}
    if(kind==='boolean'){if(!['true','false'].includes(raw))throw new Error('Mantıksal değer true veya false olmalı.');return raw==='true';}
    if(kind==='json'){try{return JSON.parse(raw);}catch{throw new Error('JSON değerini kontrol et.');}}
    return raw;
  }
  function applySettings(){
    const form=$('#node-settings'),node=nodeById(form?.dataset.nodeId);if(!node)return;
    node.label=$('#node-label').value;
    for(const el of form.querySelectorAll('[data-config]')){
      try{if(el.dataset.config==='secret_ref'&&!el.value.trim()){delete node.config.secret_ref;delete node.config.secret_field;}else if(el.dataset.config!=='secret_field'||node.config.secret_ref){node.config[el.dataset.config]=el.dataset.typed?typedInput(el):el.dataset.config==='seconds'?Number(el.value):el.value;}el.setCustomValidity('');}
      catch(error){el.setCustomValidity(error.message);}
    }
    if(node.type==='end'){
      const output={};form.querySelectorAll('[data-output-key]').forEach(el=>{
        const input=$(`[data-output-value="${el.dataset.outputKey}"]`),kind=$(`[data-output-type="${el.dataset.outputKey}"]`).value;
        el.setCustomValidity(!el.value.trim()?'Sonuç alanına bir ad ver.':Object.hasOwn(output,el.value)?'Sonuç alanları benzersiz olmalı.':'');
        try{const value=parseTyped(input.value,kind);input.setCustomValidity('');Object.defineProperty(output,el.value,{value,writable:true,enumerable:true,configurable:true});}
        catch(error){input.setCustomValidity(error.message);}
      });node.config.output=output;
    }
    markDirty();drawNodes();
  }
  function validate(){
    const form=$('#node-settings');if(form&&!form.reportValidity())return 'Adım ayarlarını kontrol et.';
    if(!draft.name.trim())return 'Akışına bir ad ver.';
    if(!supportsGraph(draft.graph))return 'Bu akışın adımları bu MultiRPA sürümüyle desteklenmiyor. Uygulamayı güncelleyin.';
    const graph=draft.graph,byId=new Map(graph.nodes.map(n=>[n.id,n]));
    if(!graph.nodes.some(n=>n.type==='end'))return 'Akışa en az bir Bitiş adımı ekle.';
    for(const node of graph.nodes){
      if(!node.label.trim())return 'Her adıma bir ad ver.';
      for(const port of ports(node))if(!graph.edges.some(e=>e.source===node.id&&e.port===port))return `“${node.label}” adımının ${portName(port).toLocaleLowerCase('tr')} bağlantısı eksik.`;
    }
    const visiting=new Set(),visited=new Set();let cycle=false;
    const walk=id=>{if(visiting.has(id)){cycle=true;return;}if(visited.has(id))return;visiting.add(id);graph.edges.filter(e=>e.source===id).forEach(e=>{if(byId.has(e.target))walk(e.target);});visiting.delete(id);visited.add(id);};
    walk(graph.nodes.find(n=>n.type==='start')?.id);
    if(cycle)return 'Bağlantılar döngü oluşturuyor. Akış bir Bitiş adımına ilerlemeli.';
    if(visited.size!==graph.nodes.length)return 'Bağlanmamış adımlar var. Her adımı Başlangıçtan ulaşılabilir şekilde bağla.';
    return null;
  }
  function validation(message){const el=$('#flow-validation');if(el){el.hidden=!message;el.textContent=message||'';}}
  async function save(){
    if(pending||!draft)return false;
    const error=validate();if(error){validation(error);return false;}
    validation(null);pending=true;status();
    const before=revision, current=draft, payload={name:draft.name.trim(),description:draft.description,graph:copy(draft.graph)};
    if(draft.id)payload.expected_version=draft.version;
    try{
      const data=await api(draft.id?`/api/flows/${draft.id}`:'/api/flows',payload,draft.id?'PUT':'POST');
      current.id=data.flow.id;current.version=data.flow.version;
      if(draft===current){dirty=revision!==before;remember();drafts.delete('new');if(state.route==='flow'){state.flowId=current.id;history.replaceState(null,'',`#flows/${current.id}`);}status();}
      loadHistory();toast('Akış kaydedildi.');return true;
    }catch(error){validation(error.message+' Akışı başka bir sekmede değiştirdiysen önce güncel sürümü aç.');return false;}
    finally{pending=false;status();}
  }
  function referencedInputs(){
    const assigned=new Set(draft.graph.nodes.filter(n=>typeof n.config.key==='string').map(n=>n.config.key));
    const found=new Set();const json=JSON.stringify(draft.graph.nodes.map(n=>n.config));
    for(const match of json.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g))if(!assigned.has(match[1]))found.add(match[1]);
    return [...found];
  }
  function inputRow(key='',value='',index=0){return `<div class="run-input-row"><input name="input-key" aria-label="Girdi adı ${index+1}" value="${esc(key)}" placeholder="girdi"><select name="input-type" aria-label="Girdi türü ${index+1}">${options([['string','Metin'],['number','Sayı'],['boolean','Mantıksal'],['json','JSON']],'string')}</select><input name="input-value" aria-label="Girdi değeri ${index+1}" value="${esc(value)}" placeholder="Değer"><button type="button" class="icon-button" data-flow-action="remove-input" aria-label="Girdiyi kaldır">${icon('close')}</button></div>`;}
  async function run(){
    if(dirty||!draft.id){if(!await save())return;}
    if(dirty){toast('Kaydetme sırasında akış değişti. Son değişiklikleri kaydedip tekrar çalıştır.',true);return;}
    const inputs=referencedInputs();
    $('#dialog-content').innerHTML=`${dialogHeader('Akışı çalıştır',`${draft.name} · Sürüm ${draft.version}`)}<form id="flow-run-form" data-flow-id="${draft.id}" data-version="${draft.version}" data-request-id="${crypto.randomUUID()}"><div class="dialog-body"><div class="run-info">${icon('play')}<p>Kaydedilen sürüm bir iş olarak çalışır. İlerleme ve sonuçları iş ayrıntılarından takip edebilirsin.</p></div><div class="run-input-heading"><strong>Giriş değişkenleri</strong><button type="button" class="button small" data-flow-action="add-input">${icon('plus')}Girdi ekle</button></div><div id="run-inputs">${inputs.map((key,index)=>inputRow(key,'',index)).join('')}</div><p class="text-small muted">Metin, sayı veya mantıksal değer kullan. Şifre ve erişim anahtarı girme.</p><div class="field-group"><label for="flow-attempts">En fazla deneme</label><input id="flow-attempts" name="max_attempts" type="number" min="1" max="${document.documentElement.dataset.workspace === 'desktop' ? 1 : 100}" value="${document.documentElement.dataset.workspace === 'desktop' ? 1 : 3}" required><small>Belirsiz dış işlemler otomatik tekrarlanmaz; sonuç doğrulaması beklenir.</small></div><div class="form-error" id="flow-run-error" role="alert"></div></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">Vazgeç</button><button class="button primary" type="submit">${icon('play')}Çalıştırmayı başlat</button></div></form>`;
    formDialog.showModal();
  }
  function autoLayout(){
    const graph=draft.graph,levels=new Map();let frontier=graph.nodes.filter(n=>n.type==='start').map(n=>n.id),level=0;
    while(frontier.length&&level<40){const next=[];frontier.forEach(id=>{levels.set(id,Math.max(levels.get(id)||0,level));graph.edges.filter(e=>e.source===id).forEach(e=>next.push(e.target));});frontier=[...new Set(next)];level++;}
    const rows={};graph.nodes.forEach(node=>{const col=Math.min(levels.get(node.id)||0,7),row=rows[col]||0;node.position={x:60+col*290,y:80+row*220};rows[col]=row+1;});markDirty();drawNodes();
  }

  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-flow-action]');if(!button||button.disabled)return;
    event.preventDefault();
    const action=button.dataset.flowAction;
    if(action==='import'){FlowTransferUI.openImport();return;}
    if(action==='new'){templates=button.dataset.template||'blank';remember();drafts.delete('new');if(draft && !draft.id)draft=null;if(location.hash==='#flows/new'){draft=template(templates);dirty=true;selected='start';render();}else location.hash='flows/new';return;}
    if(action==='add-input'){const holder=$('#run-inputs');holder.insertAdjacentHTML('beforeend',inputRow('','',holder.children.length));return;}
    if(action==='remove-input'){button.closest('.run-input-row').remove();return;}
    if(!draft)return;
    if(action==='export'){if(!pending&&!dirty&&draft.id)await FlowTransferUI.exportFlow(draft.id);return;}
    if(action==='save'){await save();return;}
    if(action==='run'){await run();return;}
    if(action==='schedule'){if(dirty||!draft.id){if(!await save())return;}if(dirty){toast('Kaydetme sırasında akış değişti. Son değişiklikleri kaydedip tekrar zamanla.',true);return;}await SchedulerUI.open(copy(draft));return;}
    if(action==='select'){selected=button.dataset.id;connect=null;drawNodes();inspector();return;}
    if(action==='connect'){connect={source:button.dataset.id,port:button.dataset.port};drawNodes();$('#connection-hint').textContent='Hedef adımın soldaki giriş noktasına tıkla';return;}
    if(action==='target'){if(connect)applyEdge(connect.source,button.dataset.id,connect.port);else{selected=button.dataset.id;drawNodes();inspector();}return;}
    if(action==='add'){
      if(draft.graph.nodes.length>=40)return toast('Bir akışta en fazla 40 adım olabilir.',true);
      const type=button.dataset.type,id=`n_${crypto.randomUUID().replaceAll('-','').slice(0,12)}`;
      const base=nodeById(selected);const position={x:Math.min((base?.position.x||100)+290,2200),y:Math.min((base?.position.y||60)+80,1400)};
      draft.graph.nodes.push({id,type,type_version:1,label:types[type].title,position,config:copy(types[type].config)});
      if(base && ports(base).length===1){const edge=draft.graph.edges.find(e=>e.source===base.id);draft.graph.edges=draft.graph.edges.filter(e=>e.source!==base.id);draft.graph.edges.push({source:base.id,target:id,port:'next'});if(edge && type!=='end')draft.graph.edges.push({source:id,target:edge.target,port:type==='condition'?'true':'next'});}
      selected=id;markDirty();drawNodes();inspector();return;
    }
    if(action==='delete'){const node=nodeById(selected);if(!node||node.type==='start')return;draft.graph.nodes=draft.graph.nodes.filter(n=>n.id!==selected);draft.graph.edges=draft.graph.edges.filter(e=>e.source!==selected&&e.target!==selected);selected=draft.graph.nodes[0]?.id;markDirty();drawNodes();inspector();return;}
    if(action==='add-output'){const node=nodeById(selected);if(node?.type==='end'){let i=1;while(Object.hasOwn(node.config.output,`alan_${i}`))i++;node.config.output[`alan_${i}`]='';markDirty();inspector();drawNodes();}return;}
    if(action==='remove-output'){const node=nodeById(selected);delete node.config.output[Object.keys(node.config.output)[Number(button.dataset.index)]];markDirty();inspector();drawNodes();return;}
    if(action==='layout'){autoLayout();return;}
    if(action==='fit'){const viewport=$('#flow-viewport');const width=Math.max(...draft.graph.nodes.map(n=>n.position.x+250));const height=Math.max(...draft.graph.nodes.map(n=>n.position.y+190));zoom=Math.max(.25,Math.min(1,viewport.clientWidth/width,viewport.clientHeight/height));resizeCanvas();viewport.scrollTo(0,0);return;}
    if(action.startsWith('zoom-')){zoom=Math.max(.4,Math.min(1.4,Math.round((zoom+(action==='zoom-in'?.1:-.1))*10)/10));resizeCanvas();}
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='flow-name'){draft.name=event.target.value;markDirty();}
    else if(event.target.id==='flow-description'){draft.description=event.target.value;markDirty();}
    else if(event.target.closest('#node-settings'))applySettings();
  });
  document.addEventListener('change',event=>{
    if(event.target.dataset.edge)applyEdge(selected,event.target.value,event.target.dataset.edge);
    else if(event.target.dataset.outputType!==undefined){const input=$(`[data-output-value="${event.target.dataset.outputType}"]`);input.value=event.target.value==='number'?'0':event.target.value==='boolean'?'true':event.target.value==='json'?'{}':'';applySettings();}
    else if(event.target.dataset.valueType){const input=$(`[data-config="${event.target.dataset.valueType}"]`);input.value=event.target.value==='number'?'0':event.target.value==='boolean'?'true':event.target.value==='json'?'{}':'';applySettings();}
  });
  document.addEventListener('submit',async event=>{
    if(event.target.id==='node-settings'){event.preventDefault();return;}
    if(event.target.id!=='flow-run-form')return;
    event.preventDefault();const form=event.target,button=$('[type="submit"]',form),errorBox=$('#flow-run-error');if(button.disabled)return;button.disabled=true;errorBox.textContent='';
    try{
      const inputs={};for(const row of form.querySelectorAll('.run-input-row')){
        const key=$('[name="input-key"]',row).value.trim(),raw=$('[name="input-value"]',row).value,kind=$('[name="input-type"]',row).value;
        if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key))throw new Error('Girdi adlarında harf, rakam ve alt çizgi kullan; bir harfle başla.');
        if(Object.hasOwn(inputs,key))throw new Error('Girdi adları benzersiz olmalı.');
        let value=raw;if(kind==='number'){value=Number(raw);if(!raw.trim()||!Number.isFinite(value))throw new Error('Sayı girdisini kontrol et.');}else if(kind==='boolean'){if(!['true','false'].includes(raw))throw new Error('Mantıksal girdi true veya false olmalı.');value=raw==='true';}else if(kind==='json'){try{value=JSON.parse(raw);}catch{throw new Error('JSON girdisini kontrol et.');}}
        Object.defineProperty(inputs,key,{value,enumerable:true});
      }
      const result=await api(`/api/flows/${form.dataset.flowId}/runs`,{request_id:form.dataset.requestId,version:Number(form.dataset.version),inputs,max_attempts:Number(form.elements.max_attempts.value)});
      formDialog.close();toast('Akış başlatıldı. Adımlar worker tarafından çalıştırılıyor.');location.hash=`jobs/${result.job.id}`;
    }catch(error){errorBox.textContent=error.message;}finally{button.disabled=false;}
  });
  let drag=null;
  document.addEventListener('pointerdown',event=>{
    const title=event.target.closest('.node-title');if(!title||event.button!==0)return;
    const node=nodeById(title.dataset.id);if(!node)return;
    drag={id:node.id,x:event.clientX,y:event.clientY,origin:copy(node.position),moved:false};
  });
  document.addEventListener('pointermove',event=>{
    if(!drag)return;const dx=(event.clientX-drag.x)/zoom,dy=(event.clientY-drag.y)/zoom;if(Math.abs(dx)+Math.abs(dy)<5&&!drag.moved)return;
    drag.moved=true;const node=nodeById(drag.id);if(!node)return;node.position={x:Math.max(20,Math.min(2200,Math.round(drag.origin.x+dx))),y:Math.max(20,Math.min(1400,Math.round(drag.origin.y+dy)))};
    const el=$(`[data-node="${node.id}"]`);el.style.left=`${node.position.x}px`;el.style.top=`${node.position.y}px`;drawEdges();
  });
  document.addEventListener('pointerup',()=>{if(drag?.moved){markDirty();resizeCanvas();}drag=null;});
  document.addEventListener('pointercancel',()=>{drag=null;});
  document.addEventListener('keydown',event=>{
    if(state.route!=='flow')return;
    if((event.ctrlKey||event.metaKey)&&event.key==='s'){event.preventDefault();save();}
    if(event.key==='Escape'){connect=null;drawNodes();}
  });
  window.addEventListener('beforeunload',event=>{if(dirty||[...drafts.values()].some(d=>d.dirty)){event.preventDefault();event.returnValue='';}});
  function runPanel(run) {
    const spec=run.spec,detail=run.detail,checkpoint=detail?.checkpoints.find(c=>c.checkpoint_key==='flow_state')?.checkpoint_value;
    const executed=checkpoint?.executed||[],current=checkpoint?.next;
    const terminal=detail && ['SUCCESS','FAILED','SKIPPED'].includes(detail.transaction.status);
    return `<section class="panel flow-trace"><div class="panel-head"><div><h2>Akış ilerlemesi <span class="tag">Sürüm ${spec.version}</span></h2><p>${esc(spec.name)} · Bu çalıştırmaya kaydedilen sürüm</p></div><a class="button small" href="#flows/${spec.flow_id}">Akışı düzenle ${icon('arrow')}</a></div><div class="flow-step-trace">${spec.graph.nodes.map(node=>{const completed=executed.includes(node.id);const active=current===node.id&&!terminal;return `<div class="trace-step ${completed?'completed':active?'current':''}"><span class="trace-step-icon">${icon(completed?'check':types[node.type].icon)}</span><div><strong>${esc(node.label)}</strong><small>${completed?'Tamamlandı':active?(detail.transaction.reconciliation_required?'Sonuç doğrulaması bekliyor':'Sıradaki adım'):'Bu çalıştırmada tamamlanmadı'}</small></div></div>`;}).join('')}</div>${detail?.transaction.last_error?`<div class="flow-run-error"><strong>Çalıştırma durumu</strong><span>${esc(errorDescription(detail.transaction.last_error))}</span><code>${esc(detail.transaction.last_error)}</code></div>`:''}</section>`;
  }
  function errorDescription(code){return ({FLOW_BROWSER_NOT_CONFIGURED:'Bu akış için hedef sitenin sunucudaki izinli alan adlarına eklenmesi gerekiyor.',FLOW_VARIABLE_MISSING:'Bir giriş değişkeni eksik. Akıştaki değişken adlarını ve çalıştırma girdilerini kontrol et.',FLOW_BROWSER_HOST_BLOCKED:'Hedef adres bu akışın tarayıcı erişimine açık değil.',FLOW_BROWSER_START_FAILED:'Tarayıcı başlatılamadı. Projeye ait Chromium kurulumunu kontrol et.',FLOW_COMPARISON_TYPE_INVALID:'Koşulda karşılaştırılan değerlerin türleri uyuşmuyor.',FLOW_RUNTIME_EXCEEDED:'Akış izin verilen çalışma süresini aştı.',WORKFLOW_OUTCOME_UNCERTAIN:'Dış sistemdeki sonuç belirsiz. Kayıt ayrıntılarından sonucu doğrulamadan yeniden çalıştırma.'})[code] || 'Akış tamamlanamadı. Hata kodunu ve kayıt ayrıntılarındaki ilerlemeyi incele.';}
  async function loadHistory(){
    const id=draft?.id,target=$('#flow-run-history');if(!id||!target)return;
    try{const data=await api(`/api/flows/${id}/runs?limit=5`);if(draft?.id!==id||!target.isConnected)return;target.innerHTML=`<div class="flow-section-heading"><div><h2>Son çalıştırmalar <span class="tag">${number(data.total)}</span></h2><p>Bu akışın kaydedilmiş sürümleriyle yapılan son işler.</p></div></div><div class="panel">${data.items.length?`<div class="flow-history-list">${data.items.map(run=>`<a href="#jobs/${run.id}" class="flow-history-row"><span class="node-symbol green">${icon('play')}</span><span><strong>Sürüm ${run.version}</strong><small>${date(run.created_at)}</small></span>${badge(run.status)}${icon('arrow')}</a>`).join('')}</div>`:'<p class="inline-note">Henüz çalıştırılmadı. Girdilerini belirleyip Çalıştır düğmesinden başlayabilirsin.</p>'}</div>`;}catch{if(target.isConnected)target.innerHTML='<p class="inline-note">Çalıştırma geçmişi şu anda alınamadı.</p>';}
  }
  return {catalog,open,leave,runPanel,supportsGraph};
})();
