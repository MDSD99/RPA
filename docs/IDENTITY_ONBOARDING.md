# MultiRPA müşteri kaydı ve yönetici onayı

İlk kimlik teslimi: 20 Eylül 2026. Güncelleme: 4 Ekim 2026.
Bu belge kimlik, müşteri çalışma alanı, demo/lisans ve yerel işletimi anlatır.
Son tam koşum **790 passed, 2 warnings in 161.33s**; hata, skip veya deselect yok.
İzole PostgreSQL, yerel SMTP ve Chromium kanıtları
[VALIDATION.md](../VALIDATION.md) içinde; tüm ürün kapsamı
[MultiRPA planında](MULTIRPA_PLAN.md) tutulur.

## Uygulanan kapsam ve erişim sınırı

Müşteri portalı tanıtım, kayıt/giriş, e-posta doğrulama, parola yenileme,
hesap/demo/indirme alanı, tenant kapsamlı çalışma alanı ve gerçek yönetici ekranını
sunar. Yerel varsayılan adresi `http://127.0.0.1:8770`; geliştirme konsolu ayrı
`8765` portunda kalır. Onaylı müşteriler yalnız kendi flow/job/schedule/verilerini
okuyabilir. Serbest job/teknik demo oluşturma müşteri API'sinde yoktur.

| Yol | İşlev ve sınır |
| --- | --- |
| `/` | Yerel tanıtım sayfası; production profile root site ayrı statik export'tur |
| `/account` | Kayıt/giriş, doğrulama/onay durumu, explicit demo başlatma, süre/kota ve paket indirme |
| `/verify-email`, `/reset-password` | Süreli, tek kullanımlık fragment token ile doğrulama/parola yenileme |
| `/workspace` | Onaylı müşterinin ortak flow editörü, işler, zamanlamalar ve bağlantı kasası; oturumsuz/onaysız kullanıcı hesaba yönlenir |
| `/admin` | Parola/TOTP ile ayrı yönetici oturumu; hesap kararı, ücretli hak ve cihaz reset |

Her hesap tenant FK ile bağlanır. `0006_execution_tenants` yürütme tablolarını,
idempotency/request anahtarlarını ve parent ilişkilerini tenant kapsamına taşır.
Eski geliştirme kayıtları sabit legacy tenant'a kontrollü backfill edilir;
gerçek müşteri tenant'ı bu kayıtlara erişemez. Worker kayıt sahibini doğrular,
scheduler izni kapalı tenant'ı atlayıp diğer tenant'ları işler.

`0007_entitlements` bir takvim ayı/üç lifetime flow demo, ücretli hak ve cihaz
lisansını; `0008_secret_vault` şifreli bağlantı kasasını ekler. Demo kayıtta/onayda
başlamaz. Kullanıcı başlatınca sunucu UTC saatine göre tek başlangıç kaydedilir;
aynı gün gelecek ay yoksa ayın son günü seçilir. Web/masaüstü ortak üç kimlik
kullanır. Süre sonunda geçmiş açık kalır, yeni çalışma/claim/dış adım engellenir.

Production portalı güvenilir HTTPS origin, bağımsız kalıcı Ed25519 signing key,
ayrı execution process ve güncel şema ister. Legacy geliştirme konsolunun
production engeli korunur. Gerçek DNS/TLS, SMTP sağlayıcısı, ilk admin ve uzak
private PostgreSQL henüz yapılandırılmadı; public yayın yapılmadı.
[Deployment rehberi](DEPLOYMENT.md) gerçek alan adı ve process profili içindir.
4 Ekim'de development DB `0008_secret_vault` revision'ına yükseltildi; dokuz
yürütme tablosunun sayıları korundu. 8765 paneli ve 8770 müşteri portalı managed
process olarak başladı. Rollout `artifacts/multirpa-dev-rollout-2026-10-04.txt`,
izole gerçek backup restore `artifacts/multirpa-backup-restore-2026-10-04.txt`
içinde; bunlar production deployment değildir. 22 Eylül başlangıç kanıtı
tarihsel olarak korunur.

## Hesap ve onay akışı

1. Kayıt, tenant + hesap + e-posta tokenı + şifreli gönderim niyetini tek transaction'da oluşturur.
   Başlangıç durumu `email_verified=false`, `approval=pending`, `suspended=false` olur.
2. Tek kullanımlık bağlantı e-posta adresini doğrular. Doğrulama kendi başına yönetici onayı vermez.
3. Kullanıcı erken giriş yapabilir ve hesabının bekleme durumunu görebilir. Yetki isteyen kullanım
   senaryosu `require_approved` çağrısıyla güncel e-posta, onay ve askıya alma durumunu kontrol eder.
4. Yönetici yalnız doğrulanmış e-posta sahibi hesabı onaylayabilir. Ret, askıya alma ve askıdan çıkarma
   işlemleri gerekçe ister; başarılı kararlar aktör, hedef, eylem, gerekçe, zaman ve correlation ID ile kaydedilir.
5. Ret veya askıya alma bütün mevcut oturumları iptal eder. Askıdan çıkarma eski oturumları canlandırmaz;
   kullanıcının yeniden giriş yapması gerekir. Retten sonra giriş yapan kullanıcı ret durumunu görebilir,
   ancak onay gerektiren işlemi yapamaz.

Kayıt, yeniden doğrulama ve parola yenileme talepleri hesap varlığını açıklamayan ortak yanıt döndürür.
Aynı e-postayla yeniden kayıt mevcut parolayı değiştirmez. E-posta kimlikleri bu ürünün politikası gereği
normalize edilip küçük harfle saklanır; doğrulama sırasında DNS sorgusu yapılmaz.

İlk yönetici yalnız terminal komutuyla oluşturulur. Varsayılan yönetici/parola yoktur; mevcut müşteri
hesabı bu işlemle yöneticiye yükseltilemez. Yönetici kendi hesabında veya başka yönetici hesabında bu
onay ekranından karar veremez. Yönetici ekleme, TOTP kaybı kurtarma ve yönetici yetkisi değiştirme
akışları henüz self-servis olarak uygulanmamıştır.

## Demo, ücretli hak ve cihaz yönetimi

Onaydan sonra müşteri `/account` ekranında **Demo sürümünü başlat** seçer.
Tekrarlı/eşzamanlı başlatma aynı başlangıç/bitişi döndürür. Web'de yeni flow
oluşturma aynı transaction içinde hak ayırır; masaüstü önce opak UUID'yi
merkezde ayırıp geçerli imzalı izin alır. Taslak içerik sunucuya gönderilmez.
Üçüncü kimlikten sonra yeni kimlik reddedilir; aynı flow'un sürümü veya yeniden
çalışması ek hak tüketmez. Başlamış demo tekrar başlatılarak uzatılamaz.

Admin kullanıcı satırındaki lisans yönetiminden son tarihi ve toplam flow
sınırını atar. Tarih gelecekte, kota `1..1000` ve kullanılmış kimlik sayısından
az olamaz. Atama/iptal gerekçelidir; demo tarihi ve geçmiş kimlikler korunur.
İptal veya sona ermiş ücretli plan eski demoya geri dönerek erişim açmaz.
Ödeme alma ve demo tarihini uzatma bu sürümün işlemleri değildir.

Masaüstü yeni cihaz anahtarının sahipliğini sunucunun tek kullanımlık challenge
metnini imzalayarak ispatlar. İlk bağlama atomiktir; ikinci cihaz ve başka
hesapta aynı anahtar kullanımı reddedilir. İzin en fazla 60 saniye ve mevcut
hak bitişine kadardır. Admin cihaz reset'i eski anahtarı emekliye ayırır ve
son verilmiş iznin bitişine kadar yenisini bekletir. Demo/kota sıfırlanmaz.
IP adresi bir bilgisayarı tanımlamaz; donanım attestation/TPM garantisi yoktur.

| İstek | İşlev |
| --- | --- |
| `GET /api/trial` | Hesap/hak durumu, sunucu saati, başlangıç/bitiş ve kullanılan kota |
| `POST /api/trial/start` | Onaylı hesabın tek explicit başlangıcı; `{}` |
| `POST /api/trial/flows` | Masaüstünün `{flow_id}` opak kimlik rezervasyonu |
| `POST /api/license/challenge` | `{public_key,purpose}`; tenant/key/activate veya renew amaçlı challenge |
| `POST /api/license/activate`, `/api/license/renew` | `{challenge_id,signature}`; imzalı süreli cihaz izni |
| `GET /api/admin/tenants/{id}/license` | Ayrı admin audience ile hak/cihaz durumu |
| `POST /api/admin/tenants/{id}/license/grant` | `{expires_at,flow_limit,reason}`; gelecekteki timezone-aware tarih |
| `POST /api/admin/tenants/{id}/license/revoke` | `{reason}`; yeni yürütmeyi kapatma |
| `POST /api/admin/tenants/{id}/device/reset` | `{reason}`; örtüşme olmadan cihaz değişimi |
| `GET /api/downloads`, `/download/{platform}` | E-posta doğrulanmış ve admin onaylı müşteriye pin uyumlu arşiv |

POST'lar ilgili müşteri/admin cookie'si ve `X-CSRF-Token` ister. Flow/kota ve
cihaz geçerliliği UI'den bağımsız servis/DB sınırında denetlenir. Download,
cihaz etkinleştirmesi değildir; dosyayı tekrar indirmek yeni demo hakkı vermez.
Linux geliştirme arşivi stage edildi; Windows kaydı/paketi olmadığında Windows
indirme seçeneği sunulmaz. Production development channel arşivini reddeder.

## Kimlik bilgileri ve oturumlar

| Veri | Uygulanan kural |
| --- | --- |
| Parola | 15–128 karakter, en fazla 512 UTF-8 baytı; kontrol karakteri kabul edilmez. Argon2id, 64 MiB bellek, 3 iterasyon, 2 paralellik, bağımsız salt. |
| E-posta doğrulama | 32 rastgele bayttan URL-güvenli token; 24 saat; tek kullanım. |
| Parola yenileme | Aynı token üretimi; 30 dakika; tek kullanım. Parola değişimi tüm müşteri ve yönetici oturumlarını iptal eder. |
| Müşteri oturumu | Sabit 8 saat; ham token yerine SHA-256 özeti veritabanında. |
| Yönetici oturumu | Ayrı audience ve cookie; sabit 30 dakika; parola yanında altı haneli TOTP zorunlu. |
| TOTP | Sır Fernet ile şifreli; 30 saniyelik sayaç ve bir önceki/sonraki adım toleransı. Kabul edilmiş sayaç yeniden kullanılamaz. |

E-posta token tablolarında da yalnız SHA-256 özetleri bulunur. Ham bağlantı tokenı, gönderim için
geçici olarak şifreli outbox içeriğinde taşınır. Aynı amaç için yeni bağlantı istemek eskisini geçersiz
kılar. Parola yenilemek e-posta doğrulama durumunu veya TOTP gereksinimini değiştirmez.

Süre ve geçerlilik otoritesi PostgreSQL saatidir. Aynı hesap üzerindeki token tüketimi, oturum iptali,
admin MFA ve onay işlemleri hesap kilidiyle sıralanır. Eşzamanlı kayıt, henüz hesap satırı yokken dahi
e-posta temelli transaction advisory lock ve unique constraint ile tekilleştirilir.

HTTPS'te host kapsamlı `__Host-` cookie adları, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`
kullanılır. Yerel HTTP geliştirmesinde Secure olmayan ayrı isimler kullanılır. Oturum tokenı API
JSON yanıtına verilmez; CSRF tokenı oturum/audience ile HMAC üzerinden ilişkilidir. Yönetici kararları
ve çıkış işlemleri, müşteri flow/schedule/kasa yazmaları ile trial/lisans istekleri CSRF kontrolünden geçer. Yazma isteklerinde JSON, özel uygulama başlığı ve kaynak
kontrolü; tüm sayfalarda CSP ve `no-referrer` uygulanır. E-posta bağlantıları tokenı URL fragment'ında
(`/#...` yerine ilgili sayfanın `#token=...` bölümünde) taşır; token sunucu erişim loguna query olarak gitmez.

## Veritabanı ve migration

Kimlik şeması `0004_identity`, ortak istek sınırlama şeması `0005_portal_rate_limits`
migration'ındadır. Güncel head `0008_secret_vault`; yürütme tenant kapsamı 0006,
hak/cihaz kayıtları 0007'de eklenir. Başlangıçta uygulama tablo oluşturmaz;
eksik veya eski şemada portal açılışı reddedilir.

| Tablo | İlişki ve sorumluluk |
| --- | --- |
| `rpa_identity_tenants` | Kurum kimliği. |
| `rpa_identity_accounts` | `tenant_id → tenants.id`; unique normalize e-posta, parola özeti, doğrulama/onay/askı ve yönetici MFA bilgisi. |
| `rpa_identity_email_tokens` | `user_id → accounts.id`; amaç, unique token özeti, son kullanım ve tüketilme zamanı. |
| `rpa_identity_sessions` | `user_id → accounts.id`; unique token özeti, audience, son kullanım ve iptal zamanı. |
| `rpa_identity_email_outbox` | `user_id → accounts.id`; şifreli payload, gönderim durumu, deneme, lease ve claim bilgisi. |
| `rpa_identity_audit` | Hedef ve isteğe bağlı aktör hesaplarına FK; başarılı hesap eylemleri ve karar gerekçeleri. |
| `rpa_portal_rate_limits` | HMAC ile saklanan sayaç anahtarı, deneme sayısı ve pencere sonu. E-posta/IP açık değer olarak saklanmaz. |
| `rpa_entitlements` / `rpa_flow_reservations` | Tenant demo/ücretli hak tarihleri ve silinmeyen ortak flow kimliği kota izi. |
| `rpa_license_devices` / `rpa_device_challenges` | Tenant'a bağlı tek aktif cihaz anahtarı, tek kullanımlık ispat ve son izin bitişi. |
| `rpa_credentials` | Tenant/alias/tür/sürüme bağlı AES-GCM ciphertext ve silme tombstone'u; kasa API'si yalnız metadata döndürür. |

FK'ler `RESTRICT` kullanır; kimlik silme/veri saklama politikası ayrıca tasarlanacaktır. Approval,
audience, token biçimi, süre aralığı ve outbox claim durumu CHECK constraint'lerle de korunur.
Migration downgrade bu yeni tabloları siler; gerçek veri üzerinde geri alma yöntemi sayılmaz.
Güncellemeden önce veritabanı yedeği ve kontrollü geri yükleme yolu hazır olmalıdır.

## E-posta teslimi ve tekrar politikası

HTTP isteği SMTP beklemez. Transaction içindeki şifreli outbox kaydını ayrı gönderici işler.
Gönderici `FOR UPDATE SKIP LOCKED` ile iki dakikalık lease ve rastgele claim tokenı alır, transaction'ı
kapatır, ardından şifreyi çözüp SMTP çağrısını yapar. Sonuç yalnız aynı geçerli claim ile yazılır.
Gönderimden önce tokenın hâlâ kullanılabilir ve hesabın askıda olmadığı kontrol edilir.

Outbox durumları `pending → sending → sent/failed`; geçici hatada gecikmeli `pending` durumuna dönüşür.
SMTP denemeleri beşle sınırlıdır; gecikme 120 saniyeden başlayarak artar. Çöken göndericinin süresi dolan
lease'i başka worker alabilir. Kayıt kimliğinden üretilen `Message-ID` tekrar denemelerde değişmez.

SMTP teslimi ile veritabanı sonucu atomik değildir. SMTP kabulünden sonra süreç çökerse aynı ileti
tekrar gönderilebilir; sabit Message-ID alıcı sistemde kesin tek teslim garantisi vermez. Bu uygulama
sınırlı tekrarlarla *at-least-once* gönderim modelini izler. `sent`, SMTP sunucusunun kabulünü gösterir;
alıcının gelen kutusuna ulaştığını veya okunduğunu göstermez.

Başarıda ve kalıcı sonlandırmada şifreli payload silinir. Süresi dolmuş/değiştirilmiş token gönderilmez;
`TOKEN_INACTIVE` gibi güvenli kodlarla kayıt sonlandırılır. Kalıcı hatadan sonra tekrar bağlantı talebi yeni
token ve yeni outbox kaydı oluşturur. SMTP hata metni, parola veya ham token uygulama yanıtına/loguna yazılmaz.
TLS doğrulaması açık olan STARTTLS/SSL desteklenir; şifresiz SMTP yalnız loopback test hedefinde kabul edilir.

## İstek sınırlama

Sayaçlar PostgreSQL'de atomik tutulur ve kimlik işleminin başarısızlığından bağımsız commit edilir.
Birden fazla portal süreci aynı sınırı paylaşır. Mevcut başlangıç sınırları:

- API istekleri: bağlantı adresi başına 60 saniyede 120.
- Giriş/kayıt/parola değiştirme POST'ları: bağlantı adresi başına 15 dakikada 30.
- Giriş: normalize e-posta başına 15 dakikada 10; müşteri/yönetici aynı e-posta sayacını paylaşır.
- Kayıt/doğrulama tekrarı/parola yenileme talebi: e-posta başına ortak 15 dakikada 5.

Sınır aşımı HTTP 429 döndürür. `mail-worker` geçmiş sayaçları saatlik temizler. Bu temizlik yeni pencerenin
başlaması için gerekli değildir. Development CLI'da proxy başlıkları güvenilir
sayılmaz. Production yalnız `127.0.0.1` üzerindeki nginx hop'una güvenir; nginx
istemcinin forwarding zincirini gerçek bağlantı adresiyle değiştirir. `*` proxy
güveni veya doğrulanmamış CDN zinciri eklenmez; ayrıntılar yayın rehberindedir.

## Sunucuda yerel çalıştırma

Komutları `/opt/rpa` dizininde projenin `.venv` ortamıyla çalıştırın. Veritabanı ayarları mevcut `.env`
içindedir; dosya yalnız sahibine açık olmalıdır. Portal sırları ayrı `.env.portal` dosyasında tutulur.
Dosyalardan sırları terminale basmayın, Git'e eklemeyin veya hata raporuna kopyalamayın.

Yeni geliştirme ortamında `scripts/manage.py start` proje PostgreSQL'ini hazırlar ve migration uygular.
Mevcut çalışan konsolda aynı komut yalnız durumu gösterir. Mevcut yerel DB için yeni migration'ları
onaylı `.env` okuyucusuyla uygulamak gerektiğinde:

```bash
.venv/bin/python - <<'PY'
import subprocess
from scripts.manage import PROJECT_ROOT, read_env, runtime_environment
subprocess.run(
    [str(PROJECT_ROOT / '.venv/bin/python'), '-m', 'alembic', 'upgrade', 'head'],
    env=runtime_environment(read_env(PROJECT_ROOT / '.env')),
    check=True,
)
PY
.venv/bin/python scripts/portal.py init
.venv/bin/python scripts/portal_manage.py start
.venv/bin/python scripts/portal_manage.py status
```

`init`, 0600 izinli dosyayı yalnız ilk kez oluşturur; bağımsız Fernet ve Ed25519 signing
anahtarlarını üretir, mevcut dosyayı değiştirmez ve anahtarları ekrana yazmaz.
`serve`, varsayılan olarak `127.0.0.1:8770` üzerinde ön planda çalışır. `--port` değişirse
`MULTIRPA_PUBLIC_URL` da aynı yerel portu göstermelidir. `portal_manage.py` development
loopback portalını terminal kapandıktan sonra da sürdürür; `start/status/stop`
PID/başlangıç zamanı/cwd/tam argv sahipliğini doğrular. Başka process'e müdahale
etmez ve OS boot servisi kurmaz. Log `logs/multirpa-portal.log`, process kaydı
`.runtime/portal.json` içindedir; sırları/logların ham içeriğini paylaşmayın.
İlk yönetici için başka terminalde:

```bash
.venv/bin/python scripts/portal.py bootstrap-admin
```

Komut e-posta, görünen ad, parola ve doğrulayıcı uygulamaya önceden eklenmiş en az 32 karakterlik Base32
TOTP sırrını ister. Parola ve TOTP sırrı gizli terminal girdisidir. TOTP kurulum QR'ı veya kurtarma kodu
bu komutta üretilmez; doğrulayıcı kaydını güvenli biçimde hazırlamak işletici sorumluluğundadır.

`.env.portal` için izinli alanlar aşağıdadır. Dosya `ANAHTAR=değer` biçimindedir; shell komutu değildir,
değerleri tırnak içine almayın ve aynı anahtarı tekrarlamayın.

| Ayar | Açıklama |
| --- | --- |
| `MULTIRPA_PUBLIC_URL` | Yerelde `http://127.0.0.1:8770`; dış ortam için yapılandırılan güvenilir HTTPS origin. Yol/query içermez. |
| `MULTIRPA_IDENTITY_KEY` | `init` tarafından oluşturulan kalıcı Fernet anahtarı; TOTP/outbox ve domain ayrımlı kasa anahtarı türetimi. |
| `MULTIRPA_LICENSE_SIGNING_KEY` | Kalıcı 32 byte Ed25519 seed, padding içermeyen base64url; production'da zorunlu. Private key pakete gitmez. |
| `MULTIRPA_RELEASE_DIR` | İsteğe bağlı, operatörün yönettiği yayın arşivi/manifest dizini. Origin/public key pin ve production channel doğrulanır. |
| `MULTIRPA_SMTP_HOST` / `MULTIRPA_SMTP_PORT` | Sağlayıcı adresi ve portu; varsayılan port 587. |
| `MULTIRPA_SMTP_FROM` | Doğrulanmış gönderici e-posta adresi. |
| `MULTIRPA_SMTP_SECURITY` | `starttls` (varsayılan), `ssl` veya yalnız yerel test için `local`. |
| `MULTIRPA_SMTP_USERNAME` / `MULTIRPA_SMTP_PASSWORD` | Sağlayıcı gerektiriyorsa ikisi birlikte. |
| `MULTIRPA_SMTP_TIMEOUT` | SMTP işlem zaman aşımı, 1–60 saniye; varsayılan 15. |

SMTP yapılandırıldıktan sonra sınırlı gönderim veya sürekli worker:

```bash
.venv/bin/python scripts/portal.py send-mail --limit 20
.venv/bin/python scripts/portal.py mail-worker
```

İlki en fazla belirtilen sayıda kuyruk kaydını işler; işlenen sayı başarılı teslim sayısı değildir.
Worker ön planda çalışır ve Ctrl+C ile kapanır. Kalıcı servis yöneticisi/restart politikası ayrı dağıtım
adımıdır. SMTP ayarı yoksa portal açılabilir, fakat mail komutları yapılandırma hatasıyla durur;
kayıt sırasında e-postanın gerçekten gönderildiği iddia edilmez.

## Sır saklama ve mevcut doğrulama sınırı

Veritabanı yedeğiyle birlikte Fernet anahtarının erişimi kısıtlı, ayrı ve geri yüklenebilir yedeği gerekir.
Anahtar kaybı yönetici TOTP sırlarının ve bekleyen outbox payload'larının çözülememesine yol açar.
Anahtarı silip `init` çalıştırmak bir rotasyon yöntemi değildir. Çok anahtarlı çözme ve yeniden şifreleme
rotasyonu henüz uygulanmadı; mevcut kayıtları koruyan kontrollü taşıma ve oturum/CSRF etkisi planlanmalıdır.

Gerçek alan adı ve SMTP sağlayıcısı bağlanmamış, dışarıya doğrulama e-postası
gönderilmemiş ve gerçek yönetici hesabı oluşturulmamıştır. İzole yerel SMTP ve
kayıt/doğrulama/admin onayı/demo/müşteri çalışma alanı yolculukları test edildi;
gerçek sağlayıcı teslimi veya public yayın kanıtı değildir. Son tam kayıt
`artifacts/multirpa-full-2026-10-04.txt`; 0001–0008 migration döngüsü ve Alembic
karşılaştırması geçti. Yapılandırılmış mypy kapsamı 44 kaynak dosyasıdır.
Linux geliştirme paketi/runner vardır; Windows build/imza/gerçek kabulü ayrı
[masaüstü rehberinde](DESKTOP.md) izlenir.
