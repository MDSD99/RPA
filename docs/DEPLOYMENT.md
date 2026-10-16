# MultiRPA production yayın profili

3 Ekim 2026. Bu rehber kodun gerçek alan adı, private PostgreSQL ve SMTP ile
yayınlanması için incelenebilir dosyalar üretir. Bu sunucuda nginx/systemd
servisleri kurulmadı; DNS, sertifika, gerçek SMTP ve ilk müşteri yöneticisi
sağlanmadan internet yayını yapılmış sayılmaz.

## Dosyaları hazırlama

Gerçek ve size ait iki HTTPS adresini belirleyin. Tanıtım sitesi ana alan adında,
müşteri ve yönetici alanı uygulama alt alan adında `/account`, `/workspace`,
`/admin` üzerinden çalışır. Yönetici oturumu ayrı cookie, kısa süre ve TOTP kullanır.
Aşağıdaki adresler yalnız belgeleme örneğidir:

```bash
cd /opt/rpa
.venv/bin/python scripts/deployment.py \
  --root-url https://example.com \
  --app-url https://app.example.com \
  --output artifacts/deployment-example
```

Üretilen dizinde `nginx.conf`, üç systemd unit dosyası, gerçek sır içermeyen
`database.env.example`, `portal.env.example`, `mail.env.example`, ayrı statik
tanıtım sitesi ve `profile.json` bulunur. Generator çalışan servis değiştirmez,
environment veya veritabanı sırrı okumaz. Var olan çıktı dizinini değiştirmeyi
reddeder. `profile.json` içindeki `deployed: false` bunun yayın komutu olmadığını
gösterir. İkinci çıktı için yeni bir dizin kullanın.

Varsayılan kurulum yolu `/opt/multirpa/current`, ayar yolu `/etc/multirpa`, statik
site `/var/www/multirpa`, servis hesabı `multirpa`, private API portu `8770`.
Gerekirse generator seçenekleriyle bu yolları açıkça değiştirin. Geliştirme
ortamının `/opt/rpa/.env` ve `.env.portal` dosyaları production'a taşınmaz.

## Altyapı ve sırlar

DNS kayıtları gerçek yayın sunucusuna gitmelidir. Her iki hostname için geçerli
sertifika alın; üretilen nginx dosyası sertifikaları
`/etc/letsencrypt/live/<hostname>/fullchain.pem` ve `privkey.pem` altında bekler.
Sertifika yenileme ve nginx reload mekanizmasını altyapı yöneticisi kurar.
Profil başka sitelerin `default_server` ayarını değiştirmez.

Private ağdaki ayrı PostgreSQL sunucusunda veritabanını ve runtime hesabını
oluşturun. Runtime rolü superuser, createdb veya createrole olmamalı; tablo/schema
sahibi olmamalı. Uygulama bunu bağlantı açarken de denetler. Ayrı migration rolü
şema sahibidir. PostgreSQL internete açılmaz; ağ kuralı yalnız MultiRPA sunucusunu
kabul eder. `DB_SSLMODE=verify-full` ve doğru sunucu hostname'i/CA kullanılır.
Runtime için ilgili schema'da USAGE, uygulama tablolarında SELECT/INSERT/UPDATE/DELETE
ve gerekli sequence hakları verilir; migration sonrasında default privileges de
aynı kapsamı korur. Production ayarları loopback veritabanını kabul etmez.

Üç örnek dosyayı ayar dizinindeki `.env` uzantılı gerçek dosyalara dönüştürün.
Dosyalar `root:root`, `0600`; dizin root tarafından yönetilir. systemd manager
EnvironmentFile değerlerini okur ve seçilmiş non-root servise verir. Ayar
dosyalarını shell ile source etmeyin; shell genişlemesi sırları veya kontrol
karakterlerini değiştirebilir. systemd'nin EnvironmentFile sözdizimine uygun
tek satırlı değerler kullanın. Yer tutucuların tümünü gerçek değerlerle değiştirin.

`MULTIRPA_IDENTITY_KEY` kalıcı Fernet anahtarıdır; TOTP/outbox şifrelerini çözer.
`MULTIRPA_LICENSE_SIGNING_KEY` bağımsız, kalıcı, 32 byte Ed25519 seed'in padding
içermeyen base64url biçimidir. Production bu anahtarı zorunlu kılar. Anahtarları
secret manager ile üretip güvenli yedekleyin; loga veya komut çıktısına yazmayın.
Kimlik anahtarını gelişigüzel yenilemek mevcut şifreli verileri okunamaz yapar.
Lisans anahtarı değişimi masaüstü paketlerindeki pin güncellemesini de gerektirir.

SMTP dosyası yalnız mail servisine yüklenir; API ve execution process'i SMTP
parolasını almaz. `starttls` veya `ssl` kullanılır. SPF/DKIM/DMARC ve gönderici
alan adı doğrulaması sağlayıcınızda tamamlanır. Production ayarlarında
`MULTIRPA_PUBLIC_URL` gerçek `https://app.<alan-adı>` köküdür; doğrulama ve parola
yenileme bağlantıları bu adrese çıkar.

Servis hesabı root değildir; application/venv ve `.browser-cache` dosyaları
release hazırlanırken yüklenir, servis tarafından değiştirilmez. Tarayıcı
adımları gerekiyorsa project-local Chromium ve kitaplıkları hedef Linux ortamına
uygun hazırlanmalıdır; `scripts/setup_browser.py` Ubuntu 24.04 ile sınırlıdır.
Onaylı hedef hostname'leri `RPA_BROWSER_ALLOWED_HOSTS` içinde açıkça tanımlayın.
Boş liste tarayıcı ağ hedeflerini kapatır. HTTP ve otomasyon e-posta hedefleri
aynı biçimde `RPA_HTTP_ALLOWED_HOSTS`, `RPA_MAIL_ALLOWED_HOSTS` ile belirlenir.
`MULTIRPA_WORKSPACE_ROOT=/var/lib/multirpa/workspaces` altında her tenant UUID'si
için ayrı dosya alanı kullanılır; systemd yalnız bu state alanında yazmaya izin
verir. Rastgele hedef/özel ağ erişimi açılmaz.

## Process sınırı ve proxy

| Servis | İşlev | Sır dosyaları |
| --- | --- | --- |
| `multirpa-api` | HTTPS proxy arkasında müşteri/admin API; embedded worker kapalı | database, portal |
| `multirpa-execution` | Worker pool ve tek scheduler; tenant/hak kontrolü her adımda | database, portal |
| `multirpa-mail` | Şifreli outbox gönderimi ve request-limit temizliği | database, portal, mail |

Unit dosyaları `python -m scripts.portal serve|execution-worker|mail-worker`
komutlarını kullanır. API production'da `127.0.0.1` üzerinde kalır, Uvicorn
forwarding bilgisine yalnız `127.0.0.1` proxy'sinden güvenilir. nginx
`X-Forwarded-For` değerini gerçek bağlantı adresiyle değiştirir; istemcinin
gönderdiği zinciri eklemez. HTTPS şeması da proxy tarafından belirlenir.
Bu sayede Origin kontrolü ve gerçek istemciye göre istek sınırlaması çalışır.
Başka bir CDN/proxy eklenecekse onun güven zinciri ve gerçek IP politikası ayrıca
kurulur. Bu profile doğrulamadan `*` proxy güveni eklenmez.
[nginx proxy başlıkları](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
ve [Uvicorn server ayarları](https://www.uvicorn.org/settings/) davranışlarının
üzerine bu uygulama sınırı uygulanır.

nginx yalnız root site statiklerini ve app proxy'sini yayınlar; PostgreSQL veya
8765 geliştirme paneline route yoktur. TLS 1.2/1.3, HSTS, body sınırı ve uygulama
CSP başlıkları kullanılır. HSTS `includeSubDomains` içermez; henüz kurulmamış
başka alt alan adlarına politika dayatılmaz. Access log kapalıdır; token taşıyan
URL'lerin nginx hata kayıtlarına girmemesi için bu iki server'da error log da
kapatılmıştır. Uygulama güvenli domain hata kodlarını ve process durumunu journal'a
yazar; teşhiste ham token/credential dökümleri alınmaz.

Servisler SIGTERM/SIGINT ile güvenli kapanır. Scheduler yeni dispatch'i durdurur,
ardından worker cleanup ve engine kapanır. Unit stop süresi 150 saniyedir.
Belirsiz dış sonuç önce reconciliation'a alınır; restart sonrası otomatik olarak
yapılmış sayılmaz. Unit'lerde NoNewPrivileges, read-only sistem görünümü, private
temp dizini ve kısıtlı capabilities vardır. Bu sınırlar hedef sistem eylemlerinin
idempotency veya masaüstü TPM garantisi değildir.

## Kurulum sırası ve kabul

Altyapı yöneticisi yalnız bu projeye ait yolları/account'u oluşturur; release'i
kurar, statik export'u `/var/www/multirpa` altına ve unit'leri systemd dizinine
kopyalar. nginx dosyası mevcut diğer siteleri koruyacak şekilde eklenir. Önce
`nginx -t` ve `systemd-analyze verify` uygulanır. Ardından profile ait unit'ler
etkinleştirilir; generator bunları otomatik yapmaz.

Migration'ı runtime rolüyle veya API başlangıcında çalıştırmayın. Ayrı migration
credentials'ını kontrollü bir komut ortamına sağlayıp `RPA_ALLOW_PRODUCTION_MIGRATIONS=yes`
ile `.venv/bin/alembic upgrade head` çalıştırın; environment sırlarını veya tam
DSN'i göstermeyin. Sonra migration izni ve migration credentials ortamdan çıkarılır.
API şema hazır değilse başlamayı reddeder. Release/deploy sırasında yedek alın;
veri taşıyan migration için ileri düzeltme veya doğrulanmış restore kullanın.

İlk yöneticiyi güvenilir terminal oturumunda production environment ile
`.venv/bin/python -m scripts.portal bootstrap-admin` komutuyla kurun. Parola ve
TOTP gizli girdidir; genel admin kayıt endpoint'i yoktur. Root site → app linki,
kayıt → gerçek mail doğrulama → TOTP admin onayı → kullanıcı demo başlatma →
web/masaüstü ortak üç flow → expiry/cihaz değişimi yolculuğunu gerçek adreslerde
sınayın. E-posta sağlayıcısı ve Windows kabulü tamamlanmadan bunları tamamlandı
olarak işaretlemeyin. HTTPS cevaplarında Secure/HttpOnly/__Host- cookie ve HSTS'i,
çapraz tenant isteklerin reddini, admin oturum ayrımını ve rate-limit IP'sini
doğrulayın. Veritabanı portu ve geliştirme paneli internetten erişilememelidir.

## Yedek ve restore

Günde en az bir encrypted/off-site yedek; 7 günlük, 4 haftalık, 12 aylık saklama
başlangıç işletim politikasıdır. Değişim hacmine göre PostgreSQL WAL/PITR eklenir.
RPO/RTO hedeflerini ölçülen restore süresiyle belirleyin. Dump, secret key yedeği
ve masaüstü imza/pin release kaydını birlikte saklayın; veritabanı yedeği tek
başına Fernet anahtarını geri getirmez.

Operatörün private `pg_service.conf` dosyası remote host, dbname, ayrı backup rolü,
verify-full ve CA bilgilerini içerir. Parola ayrı `0600` passfile içindedir;
argv veya DSN içinde taşınmaz. Örnek komutlar:

```bash
umask 077
PGSERVICEFILE=/etc/multirpa/pg_service.conf \
PGPASSFILE=/etc/multirpa/backup.pgpass \
PGSERVICE=multirpa-backup \
pg_dump --format=custom --file=/var/backups/multirpa/pre-release.dump
pg_restore --list /var/backups/multirpa/pre-release.dump
```

Arşiv listesi kontrolü restore kanıtı değildir. En az aylık izole bir PostgreSQL
veritabanına restore yapın. Restore hedefini production'dan ayrı service profili
ile tanımlayın, dump'ı `pg_restore --no-owner --no-acl --exit-on-error` ile yükleyin,
migration revision'ını ve temel tenant/flow/job sayımlarını karşılaştırın.
Outbox/worker/scheduler süreçlerini restore hedefinde açık bırakmayın; eski mail
ve dış eylemlerin yanlışlıkla tekrarlanmasını engelleyin. Yeni worker başlatılmadan
önce restore edilen devam eden işler kontrollü reconciliation incelemesinden
geçer. Backup/restore ayrı rolleri application runtime rolüyle aynı değildir.
[PostgreSQL dump/restore](https://www.postgresql.org/docs/current/backup-dump.html)
ve [private passfile](https://www.postgresql.org/docs/current/libpq-pgpass.html)
mekanizmaları kullanılır.
