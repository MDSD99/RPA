# Akış zamanlama

Güncelleme: 4 Ekim 2026. Bu özellik sunucu web çalışma alanındadır; Electron
yerel scheduler sunmaz. Güncel tenant/hak sınırı aşağıda açıklanır.

Kaydedilmiş bir MultiRPA Akış Tasarımcısı sürümü, tek sefer veya sabit aralıklarla otomatik
çalıştırılabilir. Zamanlamalar PostgreSQL'de tutulur. Panel açıkken zamanlayıcı
vadesi gelen planlardan gerçek job/transaction oluşturur; görsel akış worker'ları
bu kayıtları sahiplenir. Tarayıcı sekmesinin açık kalması gerekmez; sunucudaki
panel process'i veya bağımsız zamanlayıcı ve worker process'leri açık olmalıdır.

## Panelden kullanma

Onaylı müşteri demoyu `/account` üzerinden başlattıktan sonra `/workspace#schedules`
yolunu kullanır. Geliştirme panelinin teknik demo/verileri müşteri tenant'ına
açılmaz. Müşteri yalnız kendi akışı/sürümü ve zamanlamasıyla işlem yapabilir.

1. **İş akışları** ekranında akışınızı oluşturup kaydedin.
2. **Zamanlamalar** ekranından yeni zamanlama oluşturun; akışın kaydedilmiş sürümünü seçin.
3. Ad, ilk çalışma zamanı, tek sefer/tekrar türü, giriş değişkenleri ve deneme
   sınırını belirleyin. Tekrarlı çalışmada aralık `60..2678400` saniye olmalıdır.
4. Kaydedilen zamanlamanın sonraki tarihini ve son iş bağlantısını listeden izleyin.
5. **Duraklat**, yalnızca gelecekteki çalıştırmaları durdurur. Oluşturulmuş işi
   iptal etmek gerekiyorsa iş ayrıntısındaki iptal işlemini ayrıca kullanın.

Zamanlama bir sürüme sabitlenir. Akışı sonradan düzenlemek mevcut zamanlamanın
sürümünü değiştirmez; yeni sürümü zamanlamayı düzenleyerek seçin. Tamamlanmış tek
seferlik planın tekrar çalışması için düzenleyip yeni bir gelecek tarih belirleyin.

## Zaman ve tekrar davranışı

API tarihleri ISO 8601 biçiminde ve açık saat dilimiyle kabul eder; örneğin
`2030-01-15T09:00:00+03:00`. Veritabanı ve API çıktısı UTC kullanır. Saat dilimi
içermeyen tarihler reddedilir. Yeni oluşturma ve düzenleme sırasında ilk tarih
veritabanı saatinden ileride olmalıdır.

Tekrarlı plan ilk tarihe göre sabit geçen süre aralıklarıyla ilerler. `86400`
saniye, 24 saat demektir; yaz/kış saati değişikliklerine göre takvim gününün aynı
yerel saatini takip etmez. Cron, haftanın günleri ve saat dilimine bağlı takvim
kuralları bu sürümde bulunmaz.

Sunucu bir süre kapalı kalırsa kaçırılan her aralık için ayrı iş üretilmez. En son
vadesi geçmiş aralık için bir iş oluşturulur, önceki aralıklar `missed_runs`
sayacına eklenir ve sonraki tarih ilk ileri aralığa taşınır. Tek seferlik planın
tarihi geçmişse sunucu açıldığında bir kez gönderilir.

Aynı zamanlamanın önceki işi `PENDING` veya `RUNNING` ise yenisi gönderilmez.
Önceki işin sonucu doğrulama bekleyen kaydı varsa iş iptal edilmiş olsa bile yeni
çalıştırma durur. Sonucu iş ayrıntısından doğruladıktan sonra zamanlayıcı planı
tekrar değerlendirebilir. Bu sınır aynı zamanlamaya aittir; aynı akışa ait ayrı
zamanlamalar ve elle başlatılan işler birbirinden bağımsızdır.

## Atomiklik ve işletim

`0003_flow_schedules` migration'ı `rpa_flow_schedules` tablosunu ekler. Akış ve sürüm
bileşik FK ile `rpa_flow_versions` tablosuna, son iş `rpa_jobs` tablosuna bağlıdır.
`0006_execution_tenants` zamanlama, sürüm ve son iş ilişkilerine tenant bileşik
FK ve tenant içinde unique request kapsamı ekler. Planın türü, aralık sınırı,
girdi boyutu, deneme sayısı ve durum koşulları hem
service hem PostgreSQL constraint'leri ile korunur.

Vadesi gelen planlar `FOR UPDATE SKIP LOCKED` ile sahiplenilir. İş, transaction,
sabit sürüm bağlantısı, sonraki tarih ve audit kaydı tek DB transaction'ında
commit edilir. Birden fazla zamanlayıcı aynı veritabanında çalışabilir; aynı
vade iki kez yayınlanmaz. İş kimliği plan UUID'si, revision ve vade zamanından
üretilir. Bağlantı commit yanıtından önce kesilirse sonraki kontrol kalıcı durumu
okur; dış işlemleri doğrudan tekrar çalıştırmaz.

Oluşturma API'sindeki tenant kapsamında `request_id` tekrarında ilk yapılandırma aynıysa aynı
zamanlama döner; farklı yapılandırma `409` hatasıdır. Düzenleme, duraklatma ve
devam ettirme işlemleri `expected_revision` ister. Zamanlayıcının gönderimi de
revision artırır; eski ekrandan yapılan değişiklik güncel kaydı sessizce ezmez.

Her zamanlayıcı varsayılan olarak saniyede bir kontrol eder, bir turda en fazla
20 plan gönderir. Panelin listesinde gösterilen zamanlayıcı sayaçları o process'in
ömrüne aittir; planın `run_count` ve `missed_runs` sayaçları kalıcıdır. Zamanlayıcı
hata ayrıntılarını ve giriş verilerini loglamaz. Durdurma yeni bir kontrol turunu
engeller; başlamış atomik gönderim tamamlanabilir. Thread en fazla altı saniye
beklenir; bloklanmış bir DB çağrısı henüz dönmediyse snapshot durumu gerçek
çalışma durumunu korur.

## Kurum ve güncel hak kontrolü

Oluşturma/düzenleme/devam ve vadesi gelen yayında güncel e-posta doğrulama,
admin onayı, askı ve demo/ücretli hak bitişi denetlenir. Flow'un lifetime kota
kimliği yeniden çalıştırmada ek hak tüketmez. Vadesi gelen ama izni kapanmış
müşterinin planı duraklatılır; scheduler diğer müşterilerin planlarını işlemeye
devam eder. Hak yeniden verilince kullanıcı uygun gelecek vade/revision ile
planını devam ettirir; kapalı aralıklar otomatik toplu replay edilmez.

Yayın transaction'ında hak dolması başka tenant'ın işini rollback ettirmez.
Yayınlanan eski bir job dahi hak kapandıktan sonra claim/dış adım üzerinden
çalışamaz. Gönderilmiş bir dış çağrı geri alınmış sayılmaz; effect intent ve
reconciliation işletimi sürer. Bu kurallar doğrudan API/worker kullanımında da
uygulanır; yalnız arayüzde zamanlama düğmesini kapatmaya dayanmaz.

## Kurulum ve komutlar

Mevcut projede yeni bir bağımlılık gerekmez. Sunucuda `/opt/rpa` içinde yalnızca
RPA process'ini yenileyin; `start` doğrulanmış development veritabanını migration
head sürümüne getirir. PostgreSQL veya diğer projelerin servisleri durdurulmaz:

```bash
hostname
pwd
.venv/bin/python scripts/manage.py stop
.venv/bin/python scripts/manage.py start
.venv/bin/python scripts/manage.py status
```

Beklenen ortam `srv1752189 / /opt/rpa` olmalıdır. Production migration bu
geliştirme kurulumunun kapsamında değildir. Hazır panel için SSH tünelinizden
`http://127.0.0.1:8765/#schedules` adresini açın.

Müşteri portalını geliştirmede `scripts/portal_manage.py start/status/stop`
yönetir; `scripts/portal.py serve` ön plandaki karşılığıdır. Production'da
API'ye embedded worker eklenmez; `scripts/portal.py execution-worker` tek process
içinde worker havuzu ve scheduler'ı başlatır. Ayrı non-root systemd profili
[yayın rehberinde](DEPLOYMENT.md) tanımlıdır.

Panelden bağımsız process'ler için `APP_ENV` ve `DB_*` değerleri önceden yetkili
environment/secret yöneticisinden sağlanmalıdır. Komutlar `.env` dosyasını
kendiliğinden yüklemez; bağlantı dizeleri komut satırına yazılmaz:

```bash
# Bir seferde en fazla 20 planı kuyruğa alıp çıkar:
.venv/bin/python scripts/scheduler.py --once
# Sürekli zamanlayıcı; akış yürütmez, iş kuyruğuna gönderir:
.venv/bin/python scripts/scheduler.py --poll-seconds 1
# Ayrı terminal/process; kaydedilmiş görsel akışları yürütür:
.venv/bin/python scripts/worker.py --workers 2 --lease-seconds 30
```

`--poll-seconds` sonlu sayıdır ve `0.01..60` aralığı DB bağlantısından önce
doğrulanır. `0.01` yalnızca hızlı test/özel işletim ihtiyaçları içindir; varsayılan
bir saniyedir. Panel kendi zamanlayıcısını yalnızca worker'lar açık ve
`visual_flow` kayıtlıyken başlatır. Bağımsız zamanlayıcı için uygun worker'ı ayrıca
çalıştırın. Production DB bağlantısı mevcut merkezi veritabanı güvenlik kurallarına
tabidir; komutlar migration uygulamaz.

## API

Geliştirme panelinde yazma istekleri yerel erişim sınırını; müşteri portalında
onaylı cookie/CSRF ve tenant scope sınırını kullanır. Her iki yüz JSON gövde ve
`X-RPA-Request: 1` gerekir. `Origin` varsa panelin protokol/host/port'u ile aynı
olmalıdır. Kimlik bilgilerini giriş değişkenlerine yazmayın; girdiler JSON nesnesi
ve en fazla 12 KiB olabilir. Deneme sınırı ilk denemeyi içerir; `1..100` aralığıdır.

| İstek | Sonuç |
| --- | --- |
| `GET /api/schedules?limit=100&offset=0&flow_id={uuid}` | `{items,total,limit,offset,scheduler}`; `flow_id` isteğe bağlı |
| `POST /api/schedules` | Oluşturma alanları; `201 {schedule}` |
| `GET /api/schedules/{id}` | `{schedule}` |
| `PUT /api/schedules/{id}` | Tam yapılandırma ve `expected_revision`; `{schedule}` |
| `POST /api/schedules/{id}/pause` | `{expected_revision}`; `{schedule}` |
| `POST /api/schedules/{id}/resume` | `{expected_revision}`; `{schedule}` |

Örnek oluşturma gövdesi; UUID ve giriş değerleri sahtedir. Gerçek, kaydedilmiş bir
akış/sürüm UUID'si ve işlem başına yeni request UUID'si kullanılmalıdır:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000101",
  "name": "Örnek zamanlama",
  "flow_id": "00000000-0000-4000-8000-000000000102",
  "version": 1,
  "kind": "interval",
  "next_run_at": "2030-01-15T09:00:00+03:00",
  "interval_seconds": 3600,
  "inputs": {"mesaj": "Örnek"},
  "max_attempts": 3,
  "enabled": true
}
```

Tek sefer için `kind="once"`, `interval_seconds=null` kullanın. Düzenlemede
`request_id` yerine güncel `expected_revision` gönderin. `state`, `enabled`,
`next_run_at`, `last_job_id`, `last_job_status`, `run_count` ve `missed_runs`
alanları durum/izleme için döner. Başlık, saat veya girdi hataları kullanıcı
verilerini yansıtmayan kontrollü `422` yanıtları üretir.

## Doğrulama

Aşağıdaki komut her çalıştırmada ayrı, geçici PostgreSQL oluşturur; development
ve production veritabanları test hedefi değildir. Migration
`upgrade → downgrade → upgrade → alembic check` döngüsü yalnızca bu test DB'sinde
çalışır ve test sonunda container kaldırılır:

```bash
.venv/bin/python scripts/test_postgres.py -- tests/test_schedules_postgres.py tests/test_schedule_api.py tests/test_scheduler_runtime.py
.venv/bin/python scripts/test_postgres.py -- -m browser tests/browser/test_schedules.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

İlk zamanlama tesliminde API/runtime hedef doğrulamasında 35 test geçti: gerçek PostgreSQL'de iki
zamanlayıcıdan tek gönderim, panelden otomatik worker yürütmesi, revision
çakışmaları, duraklat/devam et, idempotency, giriş sınırları ve bloklanan thread
durdurma davranışı dahil. Son toplu test sayıları ve sonuçları
[VALIDATION.md](../VALIDATION.md) dosyasında tutulur.

3 Ekim tam koşumuna tenant'lar arası plan/sürüm/request ayrımı, PostgreSQL
bileşik FK reddi, kapalı müşteri planının diğer müşterileri durdurmaması ve
yayın sırasında expiry testi dahil edildi. 4 Ekim son tam koşumu
**790 passed, 2 warnings in 161.33s**; kayıt
`artifacts/multirpa-full-2026-10-04.txt`. Önceki 3 Ekim 759-test sonucu
doğrulama kaydında korunur. Gerçek production zamanlama/yayın kabulü
ve cron/yerel takvim desteği bu test sonucu ile tamamlanmış sayılmaz.
