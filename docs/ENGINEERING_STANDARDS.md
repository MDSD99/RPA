# MultiRPA mühendislik standardı ve artımlı dönüşüm planı

İlk tarih: 19 Eylül 2026. Güncelleme: 13 Ekim 2026.
Durum: geliştirme standardı; aşağıdaki hedeflerin tamamı henüz uygulanmış değildir.
Ürün kapsamı ve başlangıç kararları [MultiRPA planında](MULTIRPA_PLAN.md) tutulur.
Bu belge yeni kodun kurallarını, mevcut kodun dönüşüm sırasını ve tamamlanma kanıtlarını tanımlar.
Temel kısıtlar [AGENTS.md](../AGENTS.md) ve [veritabanı mimarisinden](../DATABASE_ARCHITECTURE.md) gelir.
Amaç; açık sorumluluklar, güvenilir yürütme, test edilebilir değişiklikler ve sürdürülebilir bakım sağlamaktır.

## 1. Korunacak temel ve kanıtlanan iyileştirme alanları

- Python/FastAPI/SQLAlchemy/Alembic/PostgreSQL temeli korunur; sunucu tek ürün içinde modüllere ayrılır.
- API, worker ve scheduler gerektiğinde ayrı süreçlerde çalışır; bu tek başına mikroservis ayrımı değildir.
- Mikroservis veya yeni framework ancak ölçülmüş ölçek, izolasyon veya dağıtım ihtiyacıyla değerlendirilir.
- Mevcut claim, lease, fencing, checkpoint, reconciliation ve audit davranışları dönüşüm sırasında korunur.
- `app/web/api.py` uygulama birleştirme noktası olarak sadeleştirildi; HTTP sınırı, modeller, rotalar, hata eşleme ve runtime ayrı modüllerde.
- `flow_engine.py` dahil tüketicilerin servis özel doğrulayıcılarına bağımlılığı ortak core doğrulama katmanına taşındı.
- `FlowService(ExecutionService)` ve `ScheduleService(FlowService)` mirası kaldırıldı; servisler bağımsız ve açık UoW bileşenini kullanıyor.
- Manuel/zamanlanmış flow yayını aynı transaction içinde ortak `publish_flow_run` bileşeninden geçiyor; dosya taşımak tek başına başarı değildir.
- Bu UoW/servis dönüşümü 19 Eylül'de **439 passed, 2 warnings in 115.56s** tam koşumuyla doğrulandı; transaction hata testleri ve mevcut PostgreSQL/Chromium davranışları geçti.
- Kimlik/portal/e-posta teslimi 20 Eylül'de **525 passed, 2 warnings in 140.53s** tam koşumuyla doğrulandı. Bu sonuç tarihsel kimlik teslimidir. Tenant yürütme, demo/cihaz ve ilk geniş adapter paketi 3 Ekim'de **759 passed, 2 warnings in 162.36s** tam koşumuyla doğrulandı; Linux paketli yerel runner da çalıştı. Gerçek Windows/yayın kabulü bekliyor.
- Baştan toplu yeniden yazım yapılmaz; her adım geri alınabilir, incelenebilir ve çalışan davranışı korur.

## 2. Modül sınırları ve hedef yerleşim

Aşağıdaki yerleşim hedeftir; boş klasör ve kullanılmayan arayüz üretme zorunluluğu değildir.

```text
app/
  core/              # Ayarlar, saat, güvenli hata ve ortak değer tipleri
  domain/            # Kimlikler, yürütme geçişleri, lisans ve kota kuralları
  ports/             # Gerçek alternatifleri bulunan saklama/runner/iletişim sözleşmeleri
  services/          # Kullanım senaryoları, yetkilendirme ve işlem koordinasyonu
  database/          # PostgreSQL modelleri, repository, DatabaseUnitOfWork ve Alembic
  web/
    api.py           # Mevcut create_app girişini koruyan uygulama birleştirme noktası
    schemas.py       # HTTP giriş modelleri; yanıt modelleri ayrı gelişim adımı
    routes/          # operations, flows, schedules, system; sonra auth, admin, licenses
    middleware.py    # Origin/host/body sınırı ve güvenlik başlıkları
    dependencies.py  # Principal, tenant ve servis bağımlılıklarının çözümü
    runtime.py       # Başlama, kapanma, typed servis grubu ve kaynak ömrü
    errors.py        # Domain hatalarının HTTP karşılığı
    responses.py     # Ortak, sır sızdırmayan yanıt zarfları
  workflows/         # Sürümlü flow sözleşmesi, doğrulayıcı, motor ve düğüm kayıtları
  adapters/          # Browser, HTTP, dosya, e-posta, Windows ve yerel saklama
  workers/           # Claim, yürütme, heartbeat, scheduler ve recovery döngüleri
  identity/          # Hesap, ayrı oturum, doğrulama, TOTP ve admin onayı
  entitlements/      # Takvim aylık demo, ortak kota ve cihaz ispatı/izinleri
  secrets/           # Tenant/alias/tür/sürüme bağlı authenticated encryption
  desktop/           # Dar JSON-lines, yerel SQLite ve ortak motor uyarlaması
desktop/             # Electron main/preload, ortak renderer ve platform build komutları
```

Bağımlılıklar dış katmanlardan kullanım senaryolarına ve domain kurallarına akar.
Domain FastAPI, SQLAlchemy, Electron, tarayıcı veya dosya sistemini import etmez.
Repository iş kuralını belirlemez; servis SQL veya HTTP yanıtı üretmez; router doğrudan repository çağırmaz.
Arayüzler gerçek değişim sınırları için yazılır; her sınıfa karşılık boş bir soyut sınıf eklenmez.

## 3. API, arayüz ve yaşam döngüsü

- Router yalnız giriş doğrulama, principal/tenant çözümü, servis çağrısı ve yanıt eşlemesi yapar.
- Giriş ve yanıt şemaları ayrıdır; ORM nesnesi veya iç cihaz/lisans bilgisi otomatik olarak dışarı verilmez.
- Domain hata kodları kararlıdır; Türkçe kullanıcı metni sunum katmanında eşlenir, ham exception gönderilmez.
- Middleware mevcut host/origin/boyut korumalarını korur; public yayın için auth/CSRF politikası ayrıca eklenir.
- `create_app`, bağımlılıkları birleştirir; modül import etmek bağlantı açmaz, thread başlatmaz veya migration yapmaz.
- FastAPI lifespan kaynakları açıp kapatır; üretimde API worker/scheduler süreçlerini örtük çoğaltmaz.
- Her süreç kendi bağlantı havuzunu kullanır; kapanış yeni iş kabulünü durdurup güvenli kontrol noktasını bekler.
- Editörün flow durumu, API istemcisi ve ekran bileşenleri ayrılır; web ve Electron aynı editör paketini kullanır.
- Ortak editör Electron API'lerini doğrudan çağırmaz; dosya ve runner erişimi tanımlı köprüden geçer.
- Yeni frontend kodu tipli sözleşmelerle yazılır; mevcut JavaScript davranışı korunarak parça parça dönüştürülür.

Router ve lifecycle ayrımı [FastAPI modüler uygulama](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
ve [lifespan](https://fastapi.tiangolo.com/advanced/events/) mekanizmalarıyla uygulanır.

## 4. Tipler, doğrulama ve değişmez yürütme girdileri

- Yeni/değişen domain ve servis fonksiyonlarının parametreleri ile dönüşleri açıkça tiplenir.
- `TenantId`, `FlowId`, `RunId`, `DeviceId`, `Claim` ve sonuç modelleri birbirinin yerine rastgele string taşımaz.
- `Any` sınır adaptörleriyle sınırlanır; dış veri doğrulanıp domain tipine dönüştürülmeden çalıştırılmaz.
- Genel JSON boyut/derinlik denetimi ortak modülde; iş kuralı doğrulaması ilgili domain içinde tutulur.
- Çalıştırma; sabit flow sürümü, düğüm sürümleri, giriş kopyası ve açık hak bağlamına bağlanır.
- Claim ve checkpoint anlık görüntüleri değişmez değerlerdir; paylaşılan mutable dict yürütme otoritesi olmaz.
- Veritabanındaki yürütme durumu meşru geçişlerle güncellenir; “değişmez” sözü durumun hiç değişmemesi değildir.
- UTC iş zamanı ile monotonik süre ölçümü ayrılır; lisans bitişinin otoritesi istemci saati olamaz.
- Sır değerleri flow JSON içine yazılmaz; izin denetimli kasa referansları çalıştırma anında çözülür.

## 5. Repository, service ve Unit of Work

- Bir kullanım senaryosunun ilişkili kayıt değişiklikleri bir Unit of Work (UoW) içinde commit edilir.
- UoW session ömrünü, rollback'i ve güvenli hata çevirisini yönetir; repository bağımsız commit etmez.
- Session thread/task arasında paylaşılmaz; servis nesnesi açık session yerine UoW fabrikası alır.
- Mevcut `_unit_of_work` davranışı çıkarılmadan önce bağlantı alma/commit hata senaryoları sabitlenir.
- Retry yalnız yeniden denemenin güvenli olduğu sınırda uygulanır; belirsiz commit körlemesine tekrarlanmaz.
- Tarayıcı, e-posta, dosya veya dış API işlemi veritabanı kilidi açıkken bekletilmez.
- Önce niyet/claim kaydı, sonra dış işlem, sonra fencing kontrollü sonuç kaydı kullanılır.
- E-posta ve bildirim için aynı transaction'da outbox kaydı; bağımsız gönderici ve yinelenmeye dayanıklı tüketim kurulur.
- Outbox dış hizmette kesin tek teslim garantisi vermez; sağlayıcı sonucu ve tekrar politikası ayrıca ele alınır.
- Kalıcı kısıtlar PostgreSQL FK/unique/check ve uygun kilitlerle desteklenir; uygulama kontrolü tek engel değildir.

Session ömrü [SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
kilit davranışları [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html) ile doğrulanır.

## 6. Kurum sınırı, kimlik ve lisans

- Tenant doğrulanmış principal'dan çıkarılır; istemcinin serbest `tenant_id` alanı yetki kabul edilmez.
- Repository işlemleri tenant bağlamını zorunlu alır; get/list/update/export ve background işler aynı sınırı taşır.
- İdempotency anahtarı en az tenant + işlem türü + business key kapsamındadır; global çakışma oluşturmaz.
- Tenant içeren bileşik FK/unique kuralları çapraz kurum ilişkilendirmelerini veritabanında da sınırlar.
- Auth/üyelik/onay ile demo/kota/cihaz lisansı ayrı domain kurallarıdır; execution motoruna HTTP auth gömülmez.
- Uygulama servisi çalışma iznini üretir; worker ve yerel runner dış etkili adımdan önce bu izni uygular.
- Başlangıç politikası: bir takvim ayı, web/masaüstünde ortak üç flow kimliği, bir aktif masaüstü cihazı.
- Başlangıç atomik ve tek seferliktir; silinen flow hak açmaz, yeniden kurulum süreyi yenilemez.
- Cihaz değişiminde eski izin sona ermeden/iptali doğrulanmadan yeni cihaz çalıştırılmaz; örtüşme engellenir.
- Kullanıcı/admin eylemleri actor, hedef, gerekçe, sonuç ve correlation kimliğiyle denetlenebilir kayda alınır.

## 7. Ortak flow sözleşmesi ve düğüm kataloğu

- Taşınabilir akış dosyası sürümlü bir zarf taşır; tanınmayan sürüm veya alan reddedilir. Düğüm davranışı kararlı `(type, type_version)` ile çözülür. 13 Ekim uygulamasının doğrulaması ayrıca izlenir; ayrıntı [FLOW_CONTRACT.md](FLOW_CONTRACT.md) içinde.
- `type_version` alanının yokluğu kalıcı v1'dir; doğrulama eski grafiği alan ekleyerek normalize etmez. Yeni davranış aynı sürümün anlamını değiştirmez; desteklenmeyen sürüm erken reddedilir.
- Akış kayıt revizyonu, düğüm davranış sürümü ve arşiv `schema_version` ayrı tutulur. Arşiv v1 yalnız eski düğümleri, v2 açık sürümlü/karma düğümleri taşır; dışa aktarma uygun zarfı kaynak grafiği değiştirmeden seçer.
- Katalog değişmez kayıtlar sunar; metadata kopyasının değişmesi motor davranışını değiştirmez. Kaynak şemasında bir türün tanınması, runner yeteneği ve gerçek platform kabulü ayrı kanıtlardır.
- Şema/JSON Schema ve uyumluluk örnekleri tek kaynak olur; Python ve editör tipleri buradan türetilir/doğrulanır.
- Çalıştırma öncesinde yapı, değişken tipleri, bağlantılar, sır referansları ve runner yetenekleri doğrulanır.
- Düğüm sözleşmesi giriş/çıkış şeması, gereken yetenek, süre sınırı, iptal ve yan etki davranışını bildirir.
- Düğüm sonucu başarı, güvenli tekrar, kalıcı hata veya belirsiz dış sonuç olarak açıkça sınıflanır.
- Katalog yalnız izinli kayıtları çalıştırır; flow girdisinden keyfi Python/JavaScript eval veya import yapılmaz.
- Saf dönüşüm/koşul düğümleri ağ veya masaüstü erişmez; dış işlemler açık adapter sözleşmesi kullanır.
- Browser/HTTP/dosya/Excel/e-posta/Windows adaptörleri aynı sözleşmeye uyar; hata biçimleri motora sızmaz.
- Runner yetenek bildirimi platform ve sürümü kapsar; desteklenmeyen flow kısmen çalıştırılmadan reddedilir.
- Döngü/alt akış eklenirken adım bütçesi, checkpoint adresleme ve sürüm sabitleme birlikte tasarlanır.
- Mevcut DAG/40 adım/120 saniye sınırları yalnız eşdeğer kaynak ve güvenlik kontrolü kurulduğunda değişir.

## 8. İptal, lease, tekrar ve belirsiz sonuç

- Her claim artan fencing token taşır; heartbeat, checkpoint ve sonuç yazımı owner/token/süre koşulunu denetler.
- Lease kaybeden worker yeni dış işlem başlatamaz; eski token ile gelen geç sonuç kalıcı durumu ezemez.
- Fencing veritabanını korur; dış sistem token desteklemiyorsa o sistemde tek işlem garantisi yaratmaz.
- Hedef destekliyorsa aynı business idempotency anahtarı iletilir; deneme başına rastgele anahtar üretilmez.
- İptal, lisans bitişi ve askıya alma yeni dış etkili adımı durdurur; zaten gönderilmiş çağrıyı geri almış saymaz.
- Adaptörler zaman aşımı ve iptal sinyali alır; güvenli olmayan anlık process kill varsayılan iptal yolu olmaz.
- Crash veya belirsiz yanıt sonrasında iş reconciliation durumuna alınır; hedef sonuç doğrulanmadan tekrar yapılmaz.
- Reconciliation sonucunda “yapıldı”, “yapılmadı” veya “belirsiz” ayrımı ve yetkili manuel çözüm kaydı tutulur.
- Retry gecikmesi/bütçesi ile worker adaleti tanımlanır; sürekli hata alan iş tüm kuyruğu bloke etmez.
- Log/ölçümler run, step ve güvenli correlation kimlikleri taşır; ham belgeler ve sırlar varsayılan kayıt değildir.

## 9. Masaüstü ve güvenlik sınırları

- Merkezi production kayıtlarının otoritesi private ağdaki PostgreSQL'dir; masaüstüne DB bağlantı sırrı verilmez.
- Yerel runner depolaması bir port üzerinden ayrılır; uygulaması SQLite olsa bile merkezi lisans otoritesi olmaz.
- Bu seçim PostgreSQL üretim gereksinimini değiştirmez; yerel ve merkezi adapter için aynı uygunluk testleri yazılır.
- Yerel iş içeriği cihazda kalır; sunucuya hesap/cihaz ve opak flow kota kimlikleriyle sınırlı hak trafiği gider.
- Ağ yokken yeni kota ayrılmaz; taslak düzenlenebilir, kota ve güncel izin alınmadan çalıştırılamaz.
- Lisans izninin yenileme aralığı/sonu açık politikadır; demo bitişini aşmaz, süresi dolunca yeni dış işlem durur.
- Electron renderer sandbox/context isolation kullanır; dar preload/IPC sözleşmesi kaynağı ve girdiyi doğrular.
- Yerel runner işletim sistemi yetkileri sınırlıdır; uzak sayfaya Node veya sınırsız dosya/shell köprüsü verilmez.
- Browser/API adaptörlerinde izinli hedef, yönlendirme, özel ağ erişimi ve dosya yolu politikaları açık tanımlanır.
- Pakete gömülü sır kullanılmaz; imzalı yükleyici/güncelleme ve cihaz anahtarı koruması Windows'ta doğrulanır.

## 10. Bağımlılık ve veri uyumluluğu

- Dağıtılan Python ve masaüstü bağımlılıkları kilit dosyasıyla tekrarlanabilir olur; güncellemeler küçük ve testlidir.
- Bir adapter için eklenen ağır bağımlılık bütün sunucu süreçlerine zorunlu yüklenmez; ihtiyaç bazlı paketlenir.
- Şema yalnız Alembic ile değişir; başlangıçta otomatik schema yaratma/değiştirme production'a taşınmaz.
- Migration sırası genişlet → veriyi taşı/doğrula → tüketicileri geçir → eski alanı kaldır biçimindedir.
- Eski worker/desktop sürümünün okuyabildiği aralık açık tutulur; uyumsuz sürüm anlaşılır güncelleme yanıtı alır.
- Kayıtlı flow ve checkpoint formatı sessizce değiştirilmez; sürüm dönüştürme ve eski örnek testleri gerekir.
- API/import uyumluluk geçişleri süreli olur; geçici wrapper'ın kaldırılma koşulu dönüşüm kaydında belirtilir.
- Veri kaybettiren downgrade varsayılmaz; backup/restore veya ileri düzeltme stratejisi değişiklikle birlikte yazılır.

## 11. Test ve inceleme kapıları

- Saf domain kuralları enjekte edilen saat/kimlik üreticiyle deterministik test edilir; bekleyerek zaman geçirmekten kaçınılır.
- Eşzamanlılık testleri bariyer/olaylarla yarış üretir; gerçek PostgreSQL kilit/FK/commit davranışı mock ile kanıtlanmaz.
- İzole test PostgreSQL'i kullanılır; çalışan geliştirme ve production veritabanları test hedefi değildir.
- HTTP sözleşme, tenant ihlali, kota yarışı, stale claim ve belirsiz dış sonuç kritik regresyon senaryolarıdır.
- API/refactor değişikliğinde mevcut UI Chromium akışları da korunur; snapshot tek başına iş doğruluğu kanıtı sayılmaz.
- Masaüstü kabulü gerçek Windows kurulum, uyku, yeniden başlatma, ağ kesintisi ve cihaz değişimi testinden geçer.
- Windows otomasyonu Linux'ta derleme/test geçti diye tamamlanmış işaretlenmez; kilitli ekran sınırı ayrıca sınanır.
- Ruff lint/format mevcut tabandır; type checker yeni/dönüştürülen modüllerde başlar, kapsam artarken hata bütçesi büyütülmez.
- Testler kullanıcının gözlediği davranış ve değişmez kuralları sınar; özel fonksiyonun satırlarını tekrar etmez.
- Kontrol çıktısı komut, ortam, geçen/atlanmış testler ve bilinen sınırları içerir; test sayısı tek kalite ölçütü değildir.

12 Ekim aktarım teslimindeki tip kontrolü `pyproject.toml` ile seçilen 45 kaynak dosyasını kapsar:
kimlik/portal, müşteri servis bileşimi ve HTTP sınırı, demo/cihaz, secret vault,
masaüstü runner, dosya/HTTP/mail adapter'ları, automation contract ve ortak
outbox/rate-limit/schema modülleri. Parametre/dönüş tipleri ve generic kapsamı
zorunludur; bu, bütün eski kaynakların mypy kapsamına alındığı iddiası değildir.
3 Ekim tesliminde Ruff lint ve 159 Python kaynak dosyasının format kontrolü
geçti. 4 Ekim kaynak denetiminde Ruff format kapsamı 163 dosya ve mypy 44
kaynaktı; 12 Ekim aktarım tesliminde Ruff format 167 dosyada ve mypy 45
kaynakta geçti. Bu tarihsel sayılar 13 Ekim düğüm sürümü kodunun doğrulaması
değildir. Yeni değişikliklerde son kapsam/sayı ve tekrar koşumu
[VALIDATION.md](../VALIDATION.md) içinde kaydedilir.

## 12. Mevcut kodu dönüştürme sırası

1. Mevcut API/flow/worker/scheduler davranışını ve kritik güvenlik hatalarını testlerle referans olarak sabitle.
2. `api.py` içinden schemas, middleware, routes ve runtime'ı davranış değiştirmeden ayır; create_app girişini koru. Uygulandı; ilk 427 testlik ve sonraki tam koşumlarla doğrulandı.
3. Ortak doğrulayıcıları servis özel fonksiyonlarından çıkar; import bağımlılıklarını ve hata kodlarını koru.
4. UoW ve repository bağımlılıklarını açık yap; yardımcı paylaşımı için servis mirasını bileşime dönüştür. Uygulandı; 12 yeni transaction hata testi dahil 439 testlik tam koşumla doğrulandı.
5. Tenant/kimlik/onay/lisans modüllerini eklerken her sorgu ve arka plan işinin kapsamını birlikte geçir. Uygulandı; gerçek PostgreSQL çapraz tenant kısıtları, quota/device yarışları ve hak kaybı yürütme testleri geçti.
6. Flow sözleşmesi ve düğüm kataloğunu genişlet; dokuz temel tür aynı davranışla korunarak toplam 20 tür ve adapter preflight/effect intent eklendi. Düğüm başına davranış sürümü 13 Ekim uygulama/doğrulama aşamasındadır; döngü/alt akış ve uzun iş bütçesi gelecek fazdır.
7. Ortak editör ve yerel saklama/runner sınırını çıkar. Electron/dar IPC/SQLite ve paketli Linux runner uygulandı; Windows UIA kodlandı, gerçek Windows build/imza/kabulü bekliyor.
8. Her adımda ilişkili rehberi, migration/uyumluluk notunu ve kabul kanıtını güncelle; geçici köprüleri temizle.

## 13. Bir değişikliğin tamamlanma koşulları

- Problem, kapsam ve kullanıcıya görünen sonuç nettir; tamamlanmamış kabiliyet tamamlandı diye sunulmaz.
- İlgili modül sınırı, tipler, yetki/kota kuralları, hata/iptal yolu ve kaynak ömrü uygulanmıştır.
- Değişen davranışı kanıtlayan uygun testler, lint/format ve ilgili tip kontrolü geçmiştir.
- Şema/flow/API uyumluluğu ile kurtarma yolu açıklanmış; sırlar ve hassas payload'lar çıktılara sızmamıştır.
- Yerel veya Windows kabulü gerekiyorsa gerçek ortam kanıtı vardır; eksik dış doğrulama açıkça kaydedilmiştir.
- Marka tutarlıdır; kullanıcı akışı çalışır, ilgili plan/mimari/işletim belgesi son davranışı anlatır.

## 14. Uygulanan güvenlik ve yayın sınırı — 4 Ekim 2026

4 Ekim tam koşumu **790 passed, 2 warnings in 161.33s**; hata/skip/deselect yok.
0001–0008 test migration döngüsü ve Alembic check geçti; izole container kaldırıldı.
Kanıt `artifacts/multirpa-full-2026-10-04.txt`.

Tenant principal'dan türetilir; request tarafında `system=True` seçimi yoktur.
Trusted worker/scheduler sistem scope ile kayıt bulur, locked row'daki tenant
ile yeniden daraltır. Eski geliştirme verisi sabit legacy tenant'a backfill
edilir; bu özel uyumluluk yolu müşteri hesapları için erişim bypass'ı değildir.
DB tarafında tenant bileşik FK/unique ve idempotency kapsamı korunur.

Demo başlangıcı ve yeni flow rezervasyonu aynı tenant/account kilidiyle sıralanır.
Demo geçmişi ve lifetime flow izleri silinmez. Hak kaybı yayın/claim/adım
sınırında uygulanır; bitmiş süreden sonra gönderilmiş bir etkinin geri alındığı
varsayılmaz. Cihaz değişimi son imzalı izin bitişine kadar yenisini bekletir.
Electron/runner lease'i ayrı doğrular; signed süre monotonik saatle sınırlanır.

Kasa ciphertext'i authenticated data ile tenant/alias/tür/sürüme bağlanır;
metadata API'si dışında plaintext okuma yolu bulunmaz. Desktop güvenli OS
secret store yoksa aktivasyonu reddeder; plaintext fallback yoktur. Master
key/çalışan process kontrolü veya değiştirilmiş yerel binary için mutlak
sır/kopya koruması iddia edilmez.

Production portalı artık blanket ortam engeli yerine doğrulanmış HTTPS origin,
ayrı signing key, current schema, private uzak least-privilege PostgreSQL ve
ayrı execution process ister. `scripts/deployment.py` üç non-root process ve
root/app nginx profilini yeni bir artifact dizinine üretir; generator
publication veya servis mutasyonu yapmaz. Legacy konsol development'ta kalır.
Gerçek DNS/SMTP/Windows kabulü ve production restore/izleme dış doğrulama kapılarıdır.


## 15. Akış aktarımı ve mevcut kodun dönüşümü — 5 Ekim 2026

Tam doğrulama ve Linux paketi: 12 Ekim 2026. **854 passed, 2 warnings in
180.78s**; gerçek paketli Electron aktarımı, 34 ASAR/27 native kaynak eşleşmesi
ve 130 kaynaklı sunucu wheel'i doğrulandı. Bunlar o günkü aktarım tesliminin
kanıtıdır; 13 Ekim yeni düğüm sürümü doğrulaması ayrıca kaydedilir.

- Taşınabilir akış zarfının doğrulaması web ve yerel runner tarafından kullanılan ortak domain sözleşmesinde tutulur; arayüz tek güvenlik kapısı değildir.
- API yalnız oturumdan türetilmiş tenant içinde okur. Süresi dolmuş fakat onaylı hesap kendi kaydedilmiş tanımını dışa aktarabilir; yazma hakkı bu nedenle açılmaz.
- Web içe aktarması normal oluşturma kullanım senaryosunu kullanır; yeni UUID/sürüm 1 ve kota tek transaction içinde oluşur. Kaynak kimliği veya lisans kaydı taşınmaz.
- Masaüstü aktarımı yerel düzenleme yetkisidir; çalıştırmadan önce merkezi kota ve signed cihaz izni zorunluluğu korunur.
- Yeni dosya seçimi, önizleme ve dışa aktarma davranışı editörün grafik/çalıştırma kodundan ayrı modülde tutulur. Paylaşılan editör işletim sistemi dosya yoluna erişmez; Electron main yalnız doğrulanmış akış kimliğiyle yerel runner üzerinden içerik alır.
- Dosya/grafik boyutu, derinlik, sonlu sayılar ve Unicode denetlenir; prototip anahtarları reddedilir. Tanımın içine yazılmış sabit metinler taşınır; sır kasasının plaintext içeriği veya yürütme geçmişi aktarım nesnesine dahil edilmez.
- İçe aktarma hiçbir işi veya zamanlamayı başlatmaz. Dosyayı seçmek veya oluşturma isteğini göndermeden pencereyi kapatmak kayıt üretmez. Oluşturma isteği gönderildikten sonra pencereyi kapatmak devam eden işlemi iptal etmez; geç gelen yanıt kullanıcının geçtiği sayfayı veya yeni taslağını değiştirmez.

## 16. Düğüm sürümü dönüşümü — 13 Ekim 2026

Uygulama ve doğrulama sürüyor. Ortak motor ile aktarım katmanı, eski grafiğin
temsiline ve özetine dokunmadan açık davranış sürümünü çözmek üzere
dönüştürülüyor. Editör kataloğu, şablonlar ve Electron dosya köprüsü aynı
sözleşmeyi izler. Girişte `true`/`1.0` gibi Python'da `1` ile eşit sayılabilen
değerler sürüm kabul edilmez; sürüm alanı tam sayı olarak doğrulanır.

Sabit çalışma ve checkpoint içerikleri topluca güncellenmez. Katalog sorgusu
platform kabulü veya lisans yetkisi vermez; desteklenmeyen davranış/runner
akışı ilk dış etkiden önce reddeder. Linux doğrulaması Windows UIA/DPAPI ve
imzalı yükleyici kabulüne dönüştürülmez.

Döngü/alt akış sonraki aşamadır. Sadece grafik doğrulamasının döngü yasağını
kaldırmak yeterli değildir: iterasyon ve iç içelik bütçesi, tek genel deadline,
iterasyon/çağrı yoluna bağlı checkpoint, tenant/UUID/sabit sürümlü alt akış,
tüm çağrı kimliklerine kota ve browser reconciliation birlikte tasarlanır.
Tamamlanma kaydı [VALIDATION.md](../VALIDATION.md), ayrıntılı kabul
[FLOW_CONTRACT.md](FLOW_CONTRACT.md) içindedir.
