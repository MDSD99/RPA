# Sunucu doğrulama kaydı

## Düğüm davranış sürümleri — 13 Ekim 2026, doğrulama bekliyor

Kararlı `(type, type_version)` kataloğu, eski düğümlerin kalıcı v1 anlamı,
arşiv v1/v2 uyumluluğu ve ortak editörde yeni düğümlerin açık v1 kaydı üzerinde
çalışılıyor. Sözleşme ve kalan kabul [FLOW_CONTRACT.md](docs/FLOW_CONTRACT.md)
içinde. 12 Ekim sonucu aşağıdaki aktarım tesliminin tarihsel temelidir; yeni
düğüm sürümü kodunun tam regresyon veya paket doğrulaması olarak kullanılamaz.
13 Ekim sonuçları tamamlandığında bu bölüme ayrı kaydedilecek.

## Akış dosyası aktarımı: tam doğrulama ve Linux paketi — 12 Ekim 2026

5 Ekim'de başlayan aktarım tesliminin son tam koşumu **854 passed, 2 warnings
in 180.78s (0:03:00)**. Hata, skip veya deselect yok. İzole, etiketli ve
silinebilir PostgreSQL üzerinde 0001–0008 upgrade → downgrade → upgrade ve
Alembic check geçti; test container'ı kaldırıldı. Development/production DB
test hedefi yapılmadı. Kayıt: `artifacts/multirpa-full-attempt2-2026-10-12.txt`.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
```

İlk 12 Ekim tam koşumu aynı adlı backend/tarayıcı test modüllerinin pytest
collection çakışması nedeniyle çalışamadı. Tarayıcı modülü
`tests/browser/test_flow_transfer_ui.py` olarak ayrıldı; ardından yukarıdaki
tam koşum geçti. İlk denemenin kaydı `artifacts/multirpa-full-2026-10-12.txt`
olarak korunur; başarısız ilk deneme başarılı sonuç gibi sunulmaz. İki uyarı
Starlette/httpx ve AnyIO kullanım dışı kalma uyarılarıdır.

- **Davranış:** ortak zarf/grafik sınırları, tenant ve onay/CSRF kontrolü,
  süresi dolmuş hesabın kendi akışını dışa aktarması, içe aktarmada yeni UUID ve
  atomik web kotası, yerel taslak/ilk çalışma kotası ayrımı, otomatik iş
  açılmaması ve kaydedilmemiş taslağın korunması doğrulandı. Derin fakat geçerli
  grafik sıkıştırılmış JSON ile dışa aktarılıp tekrar içe alınabildi.
- **Kaynak kontrolü:** Ruff lint, 167 Python dosyasında format, yapılandırılmış
  45 kaynak dosyasında mypy ve pip check geçti. Kayıt:
  `artifacts/multirpa-code-checks-2026-10-12.txt`. Tüm eski kaynakların mypy
  kapsamına alındığı iddia edilmez.
- **Sunucu wheel:** `artifacts/wheels-baseline-2026-10-12/multirpa-0.1.0-py3-none-any.whl`
  130 uygulama/web kaynağıyla byte-for-byte eşleşti; beklenmeyen uygulama,
  environment, backup, runtime veya private signing dosyası yok.
  Kayıt: `artifacts/multirpa-wheel-2026-10-12.txt`.
- **Linux Electron:** 12 Node testi geçti. Gerçek arşivdeki ASAR 34 ortak
  UI/desktop/template/pin dosyasıyla, native runner 27 Python app modülüyle
  eşleşti. Merkezi DB/API paketleri runner içinde yok. Kayıtlar:
  `artifacts/multirpa-desktop-node-2026-10-12.txt` ve
  `artifacts/multirpa-desktop-archive-verify-2026-10-12.txt`.
- **Gerçek paket davranışı:** arşivden açılan Electron dosya önizlemesiyle
  yeni çevrimdışı taslak oluşturdu, tanımı dışa aktardı; iş/dosya etkisi veya
  lisans izni oluşturmadı. Renderer Node/process/ağ ve genel iş IPC sınırları
  korundu. Paketli runner signed permit ile TXT/CSV/XLSX akışını tamamladı.
  Kayıtlar: `artifacts/multirpa-desktop-archive-flow-transfer-smoke-2026-10-12.txt`,
  `artifacts/multirpa-desktop-archive-gui-smoke-2026-10-12.txt` ve
  `artifacts/multirpa-desktop-archive-runner-smoke-2026-10-12.txt`.
- **Hazırlanan indirme:** `MultiRPA-0.1.0-linux-x64.tar.gz`, **310356572 byte**,
  SHA-256 `0cf83d7ed909b9c91deb12d506e4de61b8ddf8f7470cd5b37b87b445eeed7eb4`.
  Stage edilen dosya doğrulanmış arşivle aynı; development kanalında loopback
  origin/public-key pin kullanır. Kayıt:
  `artifacts/multirpa-desktop-staging-2026-10-12.txt`.

9 Ekim hedefli kayıtları tarihsel olarak korunur: backend **102 passed,
2 warnings in 14.94s**, tarayıcı **3 passed in 5.57s**
(`multirpa-flow-transfer-backend-2026-10-09.txt` ve
`multirpa-flow-transfer-browser-final-2026-10-09.txt`, `artifacts/` altında).
Gerçek DNS/SMTP/Windows/production kabulü bu geliştirme kanıtından ayrı kalır.

## Tam doğrulama — 4 Ekim 2026

**790 passed, 2 warnings in 161.33s (0:02:41)**. Başarısız, atlanan veya
seçilmeyen test yok. 0001→0008 upgrade → downgrade base → upgrade ve Alembic
check aynı labelled disposable PostgreSQL'de geçti; container kaldırıldı.
Development/production DB test hedefi yapılmadı.
Kayıt `artifacts/multirpa-full-2026-10-04.txt`.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
```

Tam koşum önceki tenant/demo/device/secret/adapter/customer/browser testleriyle
son desktop protokol/kasa güvenliği, portal process sahipliği, backup restore
sınırı ve onaylı download HTTP regresyonlarını birlikte içerir. İki uyarı
Starlette/httpx ve AnyIO deprecation uyarısıdır. Son Ruff format/lint (163
Python dosyası), yapılandırılmış mypy (44 kaynak) ve pip check de geçti.
Gerçek Windows/public production kabulü bu sonucu kapsamaz.


## Tarihsel geliştirme rollout, paketleme ve işletim — 4 Ekim 2026

Ortam `srv1752189 / /opt/rpa`; kod, bağımlılıklar, PostgreSQL/Chromium ve
Electron build bu sunucuda kaldı. Başka projelerin servisleri değiştirilmedi.
Aşağıdaki kayıt gerçek geliştirme ortamı, izole test/restore ve artifact kanıtıdır;
public DNS/HTTPS yayını veya gerçek Windows kabulü yapılmış sayılmaz.

- **Gerçek yedek geri yükleme:** migration öncesi private custom-format dump
  yeni etiketli PostgreSQL 17 container'ına restore edildi. Baseline revision
  `0005_portal_rate_limits` iken 0006→0007→0008 ileri migration ve Alembic check
  uygulandı. Dokuz execution tablosunun satır sayıları, identifier ve retained
  data SHA-256 özetleri önce/sonra eşleşti; metadata/readiness geçti. Restore
  container'ı kaldırıldı. Canlı development/production restore hedefi yapılmadı.
  Kanıt: `artifacts/multirpa-backup-restore-2026-10-04.txt`.
- **Geliştirme rollout:** yalnız proje konsolu durduruldu; development schema
  `0008_secret_vault` oldu. Dokuz tablo sayısı korundu: jobs 3, transactions 14,
  errors 6, checkpoints 14, audit 58; flows/versions/runs/schedules 0.
  Non-demo aktif job ve processing transaction yoktu. 8765 legacy konsolu ve
  8770 müşteri portalı yeniden başladı. Kanıt:
  `artifacts/multirpa-dev-rollout-2026-10-04.txt`.
- **Portal process yönetimi:** `scripts/portal_manage.py start/status/stop`
  loopback development portalını terminalden bağımsız sürdürür ve yalnız kendi
  PID/start-time/cwd/argv'si doğrulanan process'e sinyal verir. `serve` hâlâ
  ön plandaki alternatif; reboot/system service production profile işidir.
  Canlı `/`, `/account`, `/admin`, logo/favicon HTTP 200; oturumsuz auth/download
  metadata/arşiv istekleri 401, workspace hesabıma yönlendirildi.
- **Son kod denetimi:** Ruff lint/163 Python dosyasında format, yapılandırılmış
  44 kaynak dosyasında mypy ve `pip check` geçti. Kayıt:
  `artifacts/multirpa-code-checks-2026-10-04.txt`. Eski kaynakların tamamının
  mypy kapsamına alındığı iddia edilmez.
- **Sunucu wheel:** `artifacts/wheels/multirpa-0.1.0-py3-none-any.whl` yeniden
  üretildi. Uygulama ve web asset'lerinin 128 kaynağıyla byte-for-byte eşleşti;
  environment/backup/runtime/private signing dosyaları pakette yok. Kayıt:
  `artifacts/multirpa-wheel-2026-10-04.txt`.
- **Desktop ve download:** paketli Linux runner signed permit ile gerçek
  dosya/CSV/Excel akışını tamamladı. Electron GUI ortak editörü açtı; arşiv
  kaynak eşleşmesi doğrulandı. Onaylı müşteri indirme, oturumsuz/onaysız reddi,
  production-channel/origin/public-key pin ve artifact sınırları dahil hedefli
  koşum **17 passed, 2 warnings in 14.30s**. Kayıtlar
  `artifacts/multirpa-desktop-archive-runner-smoke-2026-10-04.txt`,
  `artifacts/multirpa-desktop-archive-equality-2026-10-04.txt`,
  `artifacts/multirpa-desktop-gui-2026-10-04.txt` ve
  `artifacts/multirpa-customer-download-validation-2026-10-04.txt`.
  Final Linux arşivi 310.352.105 byte; SHA-256
  `1860a1c07fd7e63ae00caf6d2fb6e0216285f5f84f2ced4192e95a95ae5e5009`.
  ASAR 32 güncel dosya ve 26 Python bytecode kaynağı eşleşti.
  Canlı portal origin/signing public key, manifest size ve streamed SHA-256
  uyumu `artifacts/multirpa-live-smoke-2026-10-04.txt` ile doğrulandı.
  Stage edilmiş Linux arşivi development channel/loopback pin taşır; genuine
  Windows build, imzalı yükleyici, DPAPI/UIA/kurulum kabulü yoktur.
- **Somut yayın örneği:** generator `https://example.com` ve
  `https://app.example.com` için yeni `artifacts/deployment-example-2026-10-04`
  dizini üretti. Üç non-root unit, SIGTERM/150 saniye kapanış, SMTP environment
  sınırı, local 8770 proxy/forwarding overwrite ve root/app bağlantısı denetlendi.
  `profile.json` deployed=false; sır okunmadı, DNS/servis/proxy değiştirilmedi.
  Gerçek hedefte `nginx -t`/`systemd-analyze verify` yerine geçmez.

Önceki tam 3 Ekim 759-test koşumu aşağıda tarihsel olarak korunur; yukarıdaki
4 Ekim 790-test koşumu o günkü kaynak sürümünün tam sonucudur. Gerçek alan adı/DNS/TLS, SMTP sağlayıcısı/doğrulanmış gönderici, ilk
admin ve private uzak production PostgreSQL sağlanmadı. Ürün kodu/yerel kanıt
ile dış sağlayıcı/Windows/public launch kabulü ayrı izlenir.

## Tenant, demo, cihaz, kasa ve geniş RPA — 3 Ekim 2026

Tam koşum **759 passed, 2 warnings in 162.36s**. Başarısız, atlanan veya
seçilmeyen test yok. Kayıt `artifacts/multirpa-full-2026-10-03.txt`.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/mypy
.venv/bin/python -m pip check
```

- 0001–0008 upgrade → downgrade → upgrade ve Alembic check geçti; test
  container'ı kaldırıldı. Canlı development/production DB test hedefi olmadı.
- Tenant scoped read/write/export-spec/claim ve idempotency ile PostgreSQL
  bileşik FK/unique ihlal reddi, worker'ın gerçek tenant'ı kullanması ve kapalı
  müşteri planının başka tenant'ı durdurmaması doğrulandı.
- Tek takvim ayı başlangıç yarışı, gün-sonu clamp, web/desktop ortak üç lifetime
  kimlik ve dördüncü hak yarışı, silme/reinstall/tekrar başlangıcı ile yenileme
  engeli ve ücretli grant/revoke/geçmiş koruması doğrulandı.
- Challenge/proof replay/amaç/key/tenant bağları, iki cihazın ilk bağlama yarışı,
  tek active DB kuralı, kısa izin/demo sonu ve eski izin bitmeden yeni cihaz
  çalıştırmama/reset/retired-key davranışı doğrulandı.
- Customer workspace API/CSRF ve Chromium yolculuğu demo başlatma/kota/tenant
  ayrımı ile ilerledi. Ortak flow editörü, işler, zamanlamalar ve kasa kullanıldı;
  teknik demo/serbest job endpoint'i müşteri API'sine bağlanmadı.
- Dosya/CSV/XLSX, izinli HTTP ve yerel SMTP gerçek adapter testleri;
  path/DNS/injection/kaynak sınırları ve çözümlenmemiş dış etkiyi tekrar
  göndermeme/reconciliation koruması doğrulandı. Windows UIA yalnız kod/platform
  capability sınırı olarak test edildi; gerçek Windows kontrolü kanıtı değil.
- AES-GCM tenant/alias/tür/sürüm swap/tamper reddi, optimistic version yarışları,
  tombstone, actor/CSRF ve metadata-only API; sır echo'sunu
  değişken/checkpoint/sonuç veya dış etkiye taşımama koruması doğrulandı.
- 3 Ekim Ruff lint/159 dosyada format, yapılandırılmış 44 kaynak dosyasında mypy
  ve pip check geçti. İki uyarı Starlette/httpx ve AnyIO deprecation uyarılarıdır.

20–22 Eylül'deki “tenant/demo/desktop yok” ve eski revision/readiness notları
alttaki **tarihsel teslimlere** aittir; güncel ürün durumunu anlatmaz.

## Tarihsel geliştirme ortamına uygulama — 22 Eylül 2026

20 Eylül'de doğrulanan sürüm bu projeye ait geliştirme ortamına uygulandı.
İşlem öncesi gerçek çalışan iş ve işlemdeki kayıt olmadığı kontrol edildi.
Veritabanının yalnız sahibine açık `before-identity-20260922T191327Z.dump` yedeği
alındı ve arşiv kataloğu doğrulandı; bu, geri yükleme provası yapıldığı anlamına gelmez.

- Mevcut geliştirme konsolu kontrollü durdurulup yeniden başlatıldı.
  PostgreSQL `0005_portal_rate_limits` revision'ına yükseltildi; önce/sonra flow
  kayıt sayısı aynı kaldı. `http://127.0.0.1:8765/api/health` HTTP 200 döndü.
- Portal sır dosyası `.env.portal`, mevcut dosyanın üzerine yazmadan ve sırları
  ekrana vermeden 0600 izinle oluşturuldu. Portal `http://127.0.0.1:8770` üzerinde çalıştırıldı.
- `/`, `/account`, `/admin` ve marka dosyası HTTP 200 döndü; MultiRPA adı doğru,
  çözülmemiş şablon işareti yok. Oturumsuz hesap isteği 401; eski `/api/flows`
  portalda 404 döndü. Müşteri portalı geliştirme otomasyonlarına erişim açmadı.
- Uygulamadaki 92 kaynak dosyası doğrulanan wheel içeriğiyle bayt düzeyinde eşleşti.

Portal ön plandaki süreç olarak çalışıyor; kalıcı servis/otomatik yeniden başlama
kurulmadı. Gerçek yönetici hesabı yok, SMTP sağlayıcısı bağlı değil ve canlı e-posta
gönderilmedi. DNS/TLS veya public yayın yapılmadı; diğer projelerin servisleri değiştirilmedi.
Kayıt: `artifacts/multirpa-dev-smoke-2026-09-22.txt`.

## Tarihsel MultiRPA müşteri kaydı ve yönetici onayı — 20 Eylül 2026

Kayıt güncellemesi: 22 Eylül 2026. Son tam test koşumu 20 Eylül'de tamamlandı:
**525 passed, 2 warnings in 140.53s**. Başarısız, atlanan veya seçilmeyen test yok.
Bu bölüm izole test ve paketleme kanıtıdır. Geliştirme servislerine 22 Eylül'deki
uygulama yukarıda ayrı kaydedildi; dışarıya yayın yapılmadı.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/mypy
.venv/bin/python -m pip check
```

- PostgreSQL 0001–0005 upgrade → downgrade → upgrade ve Alembic şema karşılaştırması geçti.
  Koşumun izole container'ı kaldırıldı; geliştirme/production veritabanı test hedefi yapılmadı.
- Kayıt, tek kullanımlık e-posta doğrulama, parola yenileme, ayrı müşteri/yönetici
  oturumları, TOTP tekrar engeli, onay/ret/askıya alma ve audit kontrolleri geçti.
- Şifreli outbox'tan izole yerel SMTP alıcısına gerçek ileti teslimi; tekrar,
  geçersiz token, claim ve eşzamanlı tüketim senaryoları doğrulandı.
- İki Chromium senaryosu tanıtım → kayıt → doğrulama → yönetici onayı → hesap durumu
  yolculuğunu ve mobil giriş/kurtarma ekranlarını doğruladı. Müşteri/yönetici oturum
  ayrımı korundu; masaüstü ve 390×844 mobil sayfalarda yatay taşma görülmedi.
- Ruff lint ve 115 Python dosyasının format kontrolü geçti. `pyproject.toml` ile
  sınırlandırılmış 15 yeni kaynak dosyasında mypy ve paket bağımlılık kontrolü geçti;
  bu, deponun tamamının tip kontrolünden geçtiği iddiası değildir.
- Wheel paketi kimlik, portal, e-posta, ortak şema denetimi, HTML/statik dosyalar ve
  0004/0005 migration'larını içeriyor. Ortam dosyaları ve sırlar pakete alınmadı.

İlk bütün proje koşumu **454 passed, 4 failed, 67 errors** ile sonuçlanmıştı:
mevcut konsolun readiness denetimi şema revision'ını `0003_flow_schedules` olarak
sabit kabul ediyordu. Portal ve konsol ortak şema denetimine geçirildi; son 525
testlik koşum bu düzeltmeden sonradır. Önceki hata kaydı
`artifacts/multirpa-onboarding-full-2026-09-20.txt` içinde korunur.

Son test kaydı: `artifacts/multirpa-onboarding-full-final-2026-09-20.txt`.
Paketleme kaydı: `artifacts/multirpa-onboarding-wheel-final-2026-09-20.txt`.
Görseller: `artifacts/multirpa-marketing.png`, `artifacts/multirpa-account.png`,
`artifacts/multirpa-admin.png` ve aynı adların `-mobile.png` karşılıkları.
İki uyarı Starlette/httpx ve AnyIO kullanım dışı kalma uyarılarıdır.

Gerçek alan adı/DNS, SMTP sağlayıcısı ve ilk gerçek yönetici henüz yapılandırılmadı.
Kimlik hesapları tenant kaydına bağlıdır; eski flow/job/schedule ve worker verilerinin
kurum ayrımı henüz uygulanmadı. Portal bu yürütme API'lerini sunmaz. Bir takvim ayı/
üç flow demo, cihaz lisansı, ortak editör/yerel runner ve Electron sıradaki işlerdir.
Ayrıntılar [kimlik ve onay rehberinde](docs/IDENTITY_ONBOARDING.md) ve
[MultiRPA planında](docs/MULTIRPA_PLAN.md) tutulur.

## UoW ve servis bileşimi — 19 Eylül 2026

Ortak `DatabaseUnitOfWork`, bağımsız Execution/Flow/Schedule servisleri ve aynı
transaction içinde ortak flow yayını sonrasında tam koşum:
**439 passed, 2 warnings in 115.56s**. Başarısız, atlanan veya seçilmeyen test yok.

12 yeni transaction hata testi; bağlantı alma tekrarını, rollback/kapanışı ve
belirsiz flush/commit sonucunda iş gövdesinin tekrar çalıştırılmamasını doğruladı.
Zamanlama testlerindeki flow işlemleri `FlowService` bağımlılığına taşındı;
mevcut sürüm, atomiklik ve idempotency kabul kontrolleri korundu. PostgreSQL
0001–0003 migration döngüsü ve Alembic şema karşılaştırması geçti; test container'ı kaldırıldı.

Kayıt: `artifacts/multirpa-uow-final-2026-09-19.txt`.

## MultiRPA marka ve ilk mimari dönüşüm — 19 Eylül 2026

HTTP katmanları, ortak doğrulama, marka ve worker yaşam döngüsü değişikliklerinden
sonra tam koşum: **427 passed, 2 warnings in 118.51s**. Başarısız, atlanan veya
seçilmeyen test yok. 18 Eylül'de başarısız olan mobil zamanlama testi bu koşumda geçti.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/python -m pip check
```

- PostgreSQL 0001–0003 migration döngüsü ve Alembic şema karşılaştırması başarılı.
- Ruff lint, 85 Python dosyasının format kontrolü ve paket bağımlılık kontrolü başarılı.
- 12 yeni hata senaryosu; yarım worker/scheduler başlangıcı, yeniden başlatma ve kapanış hatasında kalan kaynakların temizliğini doğruladı.
- Mevcut 31 HTTP endpoint'in yolu/metodu korunuyor; request doğrulaması, Host/Origin, boyut ve güvenlik başlıkları aynı sözleşmede.
- Chromium testleri yeni logolu paneli ve 390×844 zamanlamalar görünümünü doğruladı; JavaScript/CSP/dış kaynak hatası yok.
- MultiRPA wheel paketi üretildi; HTML şablonu, logo/stil ve yeni Python modülleri pakette. Ortam sırları veya artifacts pakete alınmadı.
- Geliştirme paneli kontrollü yeniden başlatıldı; HTTP ana sayfası yeni marka ile sağlıklı yanıt verdi. PostgreSQL verileri korundu.

Kayıt: `artifacts/multirpa-structure-2026-09-19.txt`.
Paketleme kaydı: `artifacts/multirpa-wheel-2026-09-19.txt`.
Görseller: `artifacts/console-desktop.png`, `artifacts/schedules-mobile.png`.
İki uyarı aynı Starlette/httpx ve AnyIO kullanım dışı kalma uyarılarıdır.
UoW/servis bileşimi bu ilk koşumdan sonraki ayrı değişikliktir; sonucu yukarıda kaydedildi.

## Güncel plan öncesi denetim — 18 Eylül 2026

MultiRPA planı hazırlanırken mevcut kod, uygulama değiştirilmeden yeniden test
edildi. Güncel sonuç **414 passed, 1 failed, 2 warnings in 116.61s**.
Bu sonuç aşağıdaki 13 Eylül tarihli tarihsel kaydın yerine güncel durumu gösterir.

```bash
.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
```

- Tüm testler aynı izole PostgreSQL koşumuna dahil edildi; skip veya deselect yok.
- Başarısızlık `tests/browser/test_schedules.py::test_once_schedule_converts_local_time_and_executes_the_saved_flow`, satır 189: **390×844 mobil görünümde yatay taşma**.
- Aynı testin önceki adımlarında tarih dönüşümü, gerçek zamanlanmış iş, tek SUCCESS kayıt, deneme sayısı, sonuç ve sürüm kontrolü geçti.
- 0001–0003 migration upgrade → downgrade → upgrade ve Alembic şema kontrolü geçti.
- Ruff lint ve 70 dosyanın format kontrolü geçti.
- Test container'ı kaldırıldı; geliştirme/production veritabanı test hedefi yapılmadı.
- `scripts/manage.py status` sağlıklı; `/api/health` yanıtı `ok`, veritabanı bağlı, worker'lar etkin.
- İki uyarı Starlette/httpx ve AnyIO kullanım dışı kalma uyarılarıdır.

Kayıt: `artifacts/multirpa-audit-2026-09-18.txt`. Mobil hata, belge tutarsızlıkları
ve ürünleşme eksikleri [MultiRPA planına](docs/MULTIRPA_PLAN.md) eklendi.
Bu denetim yeni kullanıcı/lisans/Electron özelliklerini doğrulamaz; bu özellikler
henüz uygulanmamıştır. Aşağıdaki eski bölümdeki “zamanlama yok” ifadesi ve 0002
referansı tarihsel duruma aittir; mevcut kodda 0003 ve zamanlama vardır.

## Tarihsel kayıt — 13 Eylül 2026

Tarih: **2026-09-13 (UTC)**. Ortam: `srv1752189`, `/opt/rpa`, Python 3.12.3.
Kod, bağımlılıklar, Chromium ve testler bu sunucudadır. Python paketleri `.venv`,
tarayıcı ve proje için çıkarılan işletim sistemi kütüphaneleri `.browser-cache`
içindedir. Sistem paketleri kurulmadı; diğer proje ve production servisleri değiştirilmedi.

## Son gerçek sonuçlar

| Kontrol | Sonuç |
| --- | --- |
| Altyapı, API, repository/service, worker, akış motoru ve işletim | **325 passed, 19 deselected, 2 warnings in 25.63s** |
| Gerçek Chromium: panel, görsel editör ve otomasyon | **19 passed in 69.17s** |
| Toplam farklı test | **344 başarılı; başarısız veya skip yok** |
| Alembic upgrade → downgrade → upgrade | 0001 ve 0002 dahil başarılı; ayrı silinebilir test DB'sinde |
| Alembic check | No new upgrade operations detected. |
| Ruff lint / format | Başarılı; 61 Python dosyası biçim kontrolünden geçti |
| pip check | No broken requirements found. |

İlk komutta seçilmeyen 19 tarayıcı testi ikinci komutla çalıştırılıp geçti. İki
uyarı Starlette TestClient'in httpx ve AnyIO uyumluluk katmanlarının deprecation
uyarılarıdır. Bu sonuçlar testleri atlayarak veya hataları bastırarak elde edilmedi.

```bash
cd /opt/rpa
.venv/bin/python scripts/test_postgres.py -- -o faulthandler_timeout=40
.venv/bin/python scripts/test_postgres.py -- -m browser tests/browser tests/test_flow_browser.py -o faulthandler_timeout=40
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/python -m pip check
```

Hassas değerleri sansürleyen harness çıktıları sunucuda
`artifacts/validation-core.txt` ve `artifacts/validation-browser.txt` içindedir;
artifacts dizini Git dışında tutulur. PostgreSQL 17 image'i
`postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675`.
Her harness çalıştırması kendi etiketli container'ını oluşturdu ve kaldırdı.
Development veya production veritabanı test hedefi yapılmadı.

## Doğrulanan kritik davranışlar

- 12 thread worker 36 kaydı çakışmadan işledi; dört ayrı process eşzamanlı
  sahiplenmede aynı kaydı paylaşmadı.
- Global idempotency aynı business kaydını tek transaction'da tuttu. Başarılı
  işlemler yeniden sahiplenilmedi; çelişen kimlikler reddedildi.
- Retry deneme sınırı, gecikmesi ve tükenen bütçe gerçek PostgreSQL'de doğrulandı.
- Gerçek worker SIGKILL sonrası checkpoint korundu; recovery ve reconciliation
  yeni sahiplik sürümü oluşturdu. Eski worker'ın heartbeat, checkpoint ve sonuç
  yazmaları reddedildi. Belirsiz COMMIT onayı körlemesine yeniden yürütülmedi.
- Kuyrukta karantinadaki işler yeni işlerin alınmasını engellemedi.
- Sekiz eşzamanlı aynı görsel akış çalıştırma isteği **bir job ve bir transaction**
  oluşturdu. Job, girdi, sürüm ve audit kayıtları aynı transaction'da görünür oldu.
- Eşzamanlı akış düzenlemeleri kayıp güncelleme üretmedi; eski sürüm `409` aldı.
  PostgreSQL trigger'ları çalıştırma/sürüm snapshot'ının değiştirilmesini reddetti.
  Akış düzenlendiğinde başlamış işler kendi eski sürüm ve girdilerini korudu.
- Akış motorunda DAG, alan ve boyut doğrulama, türleri koruyan değişkenler,
  koşulun iki dalı, checkpoint'ten devam ve sahiplik kaybı kontrolleri geçti.
- Kaydedilmiş akış → gerçek PostgreSQL lease sahibi worker → Chromium ile HTTPS
  test formunu açma/doldurma/tıklama/metin okuma → DB'de SUCCESS zinciri çalıştı.
  Aynı çalıştırma isteği tekrarlandığında test formuna toplam **bir POST** gitti.
- Yönlendirme testinde izin verilmeyen ikinci test sunucusuna **sıfır istek** gitti.
  Tıklama öncesi sahiplik kaybında **sıfır form gönderimi** yapıldı.
- Gerçek arayüzde adım ekleme, portları bağlama, sürükleyip konum kaydetme,
  değişken/sonuç düzenleme, kaydetme, yeniden açma ve çalıştırma geçti.
- Koşulun Evet/Hayır yolları, yeni sürümle yeni sonuç ve eski çalıştırmanın
  değişmemesi doğrulandı. Otomatik yenileme odaktaki editörü bozmadı.
- Döngü hatası açıklaması, sayfalar arası taslak koruma ve başka sekmenin sürüm
  çakışmasında yerel düzenlemelerin korunması geçti.
- Masaüstü 1440×1000 ve mobil 390×844 ekranları incelendi. Panel ve editörde
  yatay sayfa taşması, JavaScript/CSP hatası veya dış ağ isteği oluşmadı.
- Yönetici komutu başka process'e ait port/PID/başlangıç zamanını reddetti.
  Kapanmış bağlantının TIME_WAIT durumunu çalışan servis saymadan yeniden
  başlatabildi; aktif test listener'ının portunu kullanmayı reddetti.

İlk taramalarda bulunan odak değişiminde kaybolan tıklama, mobil worker kartı
taşması, yönlendirmede hedef sınırı ve TIME_WAIT port kontrolü hataları giderildi.
Eski demo/katalog testlerinin beklentileri yeni görsel motorla uyumlu hale getirildi.
Yukarıdaki sonuçlar düzeltmelerden sonraki çalıştırmalara aittir.

## Çalışan geliştirme kurulumu

Panel **http://127.0.0.1:8765** üzerinde sağlıklı. Readiness, PostgreSQL bağlantısını,
sekiz uygulama tablosunu ve `0002_visual_flows` revision'ını doğruladı. İki demo
ve iki görsel akış worker'ı hazır; `/api/flows`, `/api/workflows` ve editörün
JavaScript/CSS dosyaları başarılı yanıt veriyor.

Projeye ait geliştirme veritabanına yalnızca ileri migration uygulandı. Önceki
demo kayıtları korundu; yeni görsel akış testlerinin verisi geliştirme ortamına
taşınmadı. Kullanıcı **İş akışları → Akış oluştur** veya şablonlarla başlayabilir.
Production migration veya production bağlantı testi yapılmadı.

```bash
.venv/bin/python scripts/manage.py start
.venv/bin/python scripts/manage.py status
```

Kendi bilgisayarınızdan sadece erişim tüneli açın:

```bash
ssh -N -L 8765:127.0.0.1:8765 rpa-sunucu
```

Tarayıcıda `http://127.0.0.1:8765/#workflows` adresini açın. Sunucudaki kod
bilgisayara taşınmaz. Ekran görüntüleri yalnızca test verisi içerir:

- `artifacts/flow-builder-desktop.png`
- `artifacts/flow-builder-mobile.png`
- `artifacts/console-desktop.png`
- `artifacts/console-mobile.png`

## Bilinen kapsam ve gereken yapılandırma

Akış editörü ve manuel çalıştırma hazırdır. Tarayıcı adımları için hedeflerin
`RPA_BROWSER_ALLOWED_HOSTS` ile açıkça izin verilmesi gerekir; mevcut kurulumda
liste boştur. Bu yüzden belirtilmemiş bir dış hedefe otomasyon gönderilmez.

Kimlik doğrulamalı hedefler için secret adaptörü, private hedeflere özel erişim,
zamanlanmış tetikleyiciler, masaüstü/e-posta/dosya düğümleri ve public kullanıcı
girişi bu sürümde yoktur. Production PostgreSQL ağı/TLS/rolleri, backup, izleme
ve gerçek business sonucunun reconciliation kuralları ayrıca sağlanmalıdır.

[Flow Studio rehberi](docs/FLOW_STUDIO.md) node sınırlarını, tarayıcı oturumu
kurtarmayı ve kurulum/işletim komutlarını ayrıntılı açıklar.
