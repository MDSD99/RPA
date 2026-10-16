# MultiRPA bağlantı bilgileri kasası

3 Ekim 2026. Kasa HTTP, tarayıcı giriş ve otomasyon SMTP bilgilerini tenant
kapsamında saklar. Flow belgelerinde, çalıştırma girdilerinde ve checkpoint'lerde
parola veya Authorization değeri yerine sabit bir `secret_ref` bulunur.

## Müşteri akışı ve API

Onaylı müşteri bağlantı bilgisine `api_main`, `browser_login`, `mail_main` gibi
küçük harfli bir referans verir. API yalnız şu üç türü kabul eder:

| Tür | Alanlar |
| --- | --- |
| `http` | `authorization` |
| `browser` | `username`, `password` |
| `smtp` | `host`, `port`, `sender`, `security`, `username`, `password` |

Alanlar türle birebir eşleşir; bilinmeyen alanlar, kontrol karakterleri ve
aşırı uzun değerler reddedilir. HTTP Authorization ASCII ve tek satırdır.
SMTP'de SSL/465 veya STARTTLS/587 zorunludur. Local/plain SMTP kasa kaydında
kabul edilmez; yalnız explicit Python test policy'siyle yerel adaptör testleri
yapılabilir.

`GET /api/secrets` yalnız `reference`, `kind`, `version`, `updated_at` döner.
`PUT /api/secrets/{reference}` sır değerlerini alır; yeni kayıt için
`expected_version: null`, düzenleme için görülen sürüm gönderilir. Yanıt yine
yalnız metadata'dır. `DELETE /api/secrets/{reference}` görülen `expected_version`
ile çalışır. Tek bir değeri plaintext okumaya veya dışa aktarmaya yarayan endpoint
yoktur. Yazma ve silme müşteri oturumu ile CSRF gerektirir; tenant ve actor
sunucuda principal'dan türetilir. Yönetici müşterinin hedef parolasını okuyamaz.
Hesap askıya alındığında yeni kullanım ve secret çözümlemesi reddedilir.

## Şifreleme ve sürüm

`rpa_credentials` anahtarı `(tenant_id, reference)`'tır; migration
`0008_secret_vault` tablosunu oluşturur. Secret master key olarak kalıcı
`MULTIRPA_IDENTITY_KEY` kullanılır. HKDF-SHA256 ile kasa için ayrı domain'de 32 byte
AES-GCM anahtarı türetilir; kimlik modülündeki Fernet ciphertext'leriyle aynı
şifreleme bağlamı paylaşılmaz. Her yazım bağımsız 12 byte nonce üretir.
Authenticated data tenant UUID, referans, tür ve sürümü birlikte bağlar.
Ciphertext'i başka tenant'a, alias'a, türe veya sürüme kopyalamak çözümlemeyi
reddettirir. Yanlış anahtar veya ciphertext değişikliği sabit, sır içermeyen
`FLOW_CREDENTIAL_UNAVAILABLE` sonucuna dönüşür.

Tenant/account kilidi yeni alias ve eşzamanlı sürüm yarışlarını sıralar.
Eski sürümle güncelleme veya silme 409 döner. Silme ciphertext'i temizler ve
`deleted_at` tombstone'u bırakır; listede ve runner çözümlemesinde kayıt görünmez.
Aynı alias yeniden oluşturulursa sürüm ilerler; silme/yeniden oluşturma eski
ciphertext'in eski sürüm bağlamında tekrar kullanılmasına izin vermez. En fazla
100 aktif alias tutulur. Store/delete audit kaydı actor UUID, hedef kullanıcı,
alias/tür/sürüm ve correlation UUID içerir; secret değeri içermez.

Bu şifreleme DB yedeğinde secret değerlerini korur; çalışan sunucunun memory'sini
ve master key'i kontrol eden operatöre karşı erişim koruması değildir. Anahtar
ile encrypted database yedeğini ayrı güvenli alanlarda yedekleyin. Anahtar
rotasyonu ciphertext'leri yeniden şifreleme işlemiyle birlikte tasarlanır;
sırf environment değerini yenilemek mevcut kayıtları çözemez hale getirir.

## Runner ve dış sistemler

Runner doğrulanmış tenant için resolver oluşturur. Gerekli secret'ları çalıştırma
başlangıcında belleğe çözer; flow'un içine veya API sonucuna eklemez. Tarayıcı
fill düğümü referansın `username` veya `password` alanını kullanır. HTTP düğümü
Authorization'ı yalnız adaptörün gönderilen request header'ına ekler. SMTP
düğümü kasadaki bağlantıyı kullanır; public alıcı/konu/gövde flow'dan gelir.
Bir sonucu okuyan API/tarayıcı hedefi parolayı echo ederse secret guard bunun
değişken/checkpoint/sonuç veya başka dış etkiye taşınmasını engeller.

Otomasyon mail hedefi `RPA_MAIL_ALLOWED_HOSTS` içinde tam hostname olarak izinli
olmalıdır. DNS sonucundaki tüm adresler public olmalıdır; private/loopback/
link-local veya IPv4-mapped private sonuç tüm isteği reddettirir. Bağlantı bir
doğrulanmış IP'ye sabitlenir; TLS sertifikası asıl hostname ile doğrulanır.
Otomasyon SMTP credentials'ları platformun doğrulama e-postası göndericisinden
ayrıdır. Müşteri SMTP credential kaydı otomatik olarak host policy'ye eklenmez.

Gönderimden önce cancellation/lease denetimi ve kalan runtime'a göre socket
timeout uygulanır. SMTP DATA'nın başarı yanıtı alınmadan bağlantı koparsa mesaj
gönderilmiş olabilir; engine işi belirsiz sonuç olarak durdurur. Checkpoint'teki
effect intent çözülmeden aynı adım tekrar gönderilmez. İleti yollandıktan sonra
süre dolması zaten gerçekleşmiş gönderimi geri almış sayılmaz; operatör sonuç
doğrulama akışını kullanır.

## Kanıt kapsamı

`tests/test_secret_vault.py` gerçek izole PostgreSQL'de tenant/alias/tür/sürüm
swap, ciphertext tamper, sürüm yarışları, tombstone, actor ve API/CSRF davranışını
sınar. `tests/test_mail_automation.py` yalnız test sunucusunda gerçek loopback
SMTP teslimi, DATA ack kaybı ve tekrar engelini; ayrıca private DNS, injection,
protokol/port, timeout ve cancellation sınırlarını sınar. Dış müşteri SMTP'sine
test e-postası gönderilmez. Tam PostgreSQL/migration sonuçları root tarafından
[doğrulama kaydında](../VALIDATION.md) tutulur; gerçek sağlayıcı/Windows kabulü
ayrı işletim adımıdır.
