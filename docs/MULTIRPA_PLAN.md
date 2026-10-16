# MultiRPA — mevcut durum ve uygulama planı

İlk inceleme: 18 Eylül 2026. Güncelleme: 13 Ekim 2026.
Proje: `/opt/rpa`, sunucu: `srv1752189`.

Kullanıcı planın uygulanmasını ve gerekli mevcut kodun temiz mimariye
dönüştürülmesini onayladı; uygulama başladı. Ürün adı **MultiRPA**.
Yeni kod ile değiştirilen mevcut kod aynı [mühendislik standardına](ENGINEERING_STANDARDS.md)
tabidir. Tamamlanan altyapı değişiklikleri ile henüz yapılmayan ürün özellikleri
bu belgede ayrı izlenir; planın tamamı uygulanmış değildir.

9 Ekim GENRPA referans incelemesi ve öncelikli yetenek açıkları
[GENRPA_GAP_ANALYSIS.md](GENRPA_GAP_ANALYSIS.md) içinde kaynaklarıyla kayıtlıdır.
Bu inceleme yeni özelliklerin tamamlandığı anlamına gelmez; uygulama sırası
bölüm 19'da izlenir.

## 1. Mevcut durum

| Alan | Bugünkü durum | Kanıt / sınır |
| --- | --- | --- |
| Geliştirme paneli | Ayrı loopback paneli var | `scripts/manage.py status`, `/api/health`; müşteri yayını değil |
| PostgreSQL altyapısı | Uygulandı ve doğrulandı | Alembic 0001–0008; tenant, demo/kota/cihaz ve şifreli bağlantı kasası |
| İş yürütme | Uygulandı ve doğrulandı | Kuyruk, claim/lease/fencing, retry, checkpoint/reconciliation; tenant ve güncel hak kontrolü |
| Görsel akış editörü | Web/masaüstünde ortak | Aynı kaynaklarla akış, sürüm, giriş, geçmiş ve bağlantı kasası arayüzü |
| Akış dosyası aktarımı | Sürümlü JSON içe/dışa aktarma | Ortak doğrulama, önizleme ve yeni kimlik; otomatik eşitleme veya çalıştırma yapmaz |
| Tarayıcı otomasyonu | Temel kapsam var | Açma, doldurma, tıklama, metin okuma ve kasadan giriş bilgisi; izinli HTTPS hedefleri |
| Akış dili | 20 düğüm türü | DAG, 2–40 adım, toplam 120 saniye; desteklenmeyen runner erken reddeder; döngü yok |
| Düğüm davranış sürümü | Uygulama ve doğrulama sürüyor | Eski alansız düğüm kalıcı v1; açık `type_version`, değişmez katalog ve arşiv v1/v2 uyumluluğu; bölüm 20 |
| Zamanlama | Web'de uygulanmış | Tek sefer/sabit aralık, sürüm sabitleme ve tekrar gönderim koruması; Electron yerel scheduler sunmaz |
| Mobil görünüm | Düzeltildi ve doğrulandı | 390×844 kimlik, admin, zamanlama ve müşteri çalışma alanı testleri |
| Takvim tipi zamanlama | Gelecek faz | Cron, haftanın günleri ve yerel saat dilimine göre tekrar yok |
| Teknik demo | Geliştirme panelinde var | Başarı/retry/kurtarma örnekleri; müşteri ürün denemesi ayrı hak modelidir |
| MultiRPA kimliği ve logo | Web ve Electron'da uygulandı | SVG, favicon, PNG/ICO simge, sayfa/sekme/paket ve indirme adları |
| Mevcut kodun katmanlara ayrılması | Uygulandı | Router/models/middleware/runtime, UoW ve servis bileşimi; aynı kurallar yeni ve değişen eski kodda |
| Tanıtım sitesi ve uygulama alt alan adı | Yerel sayfalar ve yayın profili hazır | Ayrı root statik site/app HTTPS proxy profili; gerçek DNS/TLS/yayın yapılmadı |
| Kayıt, giriş, e-posta doğrulama | Servis/API/yerel SMTP ve tarayıcı yolculuğu doğrulandı | Argon2id, oturum, tek kullanımlık bağlantı, parola yenileme, şifreli outbox; gerçek mail sağlayıcısı bağlanacak |
| Gerçek yönetici ekranı | Onay ve lisans/cihaz işlemleri uygulandı | TOTP, onay/ret/askı, süre/kota, gerekçeli ücretli hak/iptal ve cihaz reset audit'i; gerçek ilk admin kurulmadı |
| Müşteriler arasında veri ayrımı | Uygulandı ve doğrulandı | 0006 kontrollü legacy backfill; scoped service/repository ve tenant bileşik FK/unique; worker/scheduler gerçek tenant'ı kullanır |
| 1 aylık / 3 işlik deneme | Uygulandı ve doğrulandı | Tek explicit başlangıç, sunucu takvim ayı, ortak üç lifetime flow UUID; silme/yeniden kurulum sıfırlamaz |
| Masaüstü uygulaması | Linux geliştirme paketi ve gerçek yerel runner var | SQLite/JSON-lines, ortak motor/editör; dosya/CSV/XLSX paketli smoke geçti. Windows build ve gerçek kabul bekliyor |
| Cihaz lisansı | Uygulandı ve doğrulandı | Ed25519 challenge/proof, tek aktif cihaz, en fazla 60 saniye izin ve örtüşmeyen admin cihaz değişimi; TPM garantisi yok |
| İlk geniş RPA kataloğu | Dosya/CSV/Excel/HTTP/e-posta uygulandı | İzinli hedef, özel çalışma alanı, effect intent ve belirsiz sonuç koruması; üç Windows UIA düğümü kodlandı, gerçek Windows kabulü bekliyor |
| Bağlantı bilgileri kasası | Uygulandı ve doğrulandı | 0008 AES-GCM; tenant/alias/tür/sürüm bağı, metadata yanıtı, tombstone ve sırların çıktıya sızmasını engelleyen guard |
| Müşteriye açık production | Profil hazır; yayın tamamlanmadı | Portal HTTPS/ayrı signing key ve external worker ister; üç non-root servis/nginx örneği var. Gerçek altyapı ve kabul eksik |

Kod referansları: `app/database/models.py`, `app/web/api.py`,
`app/workflows/flow_engine.py`, `app/workflows/browser_actions.py`,
`app/workers/runtime.py`, `app/workers/scheduler.py`, `app/web/static/`,
`docs/SCHEDULING.md`.

`VALIDATION.md` tarihsel sonuçlarla birlikte 12 Ekim aktarım teslimi tam koşumunu
içerir: **854 passed, 2 warnings in 180.78s**; hata/skip/deselect yok. Bu koşum
13 Ekim düğüm sürümü değişikliklerini kapsamaz; yeni sonuç ayrıca kaydedilecek.
Güncel şema head'i
`0008_secret_vault`; 0006 tenant, 0007 demo/cihaz ve 0008 sır kasası test döngüsü geçti.
Test kaydı gerçek müşteri hedeflerinde veya Windows üzerinde doğrulama
yapıldığı anlamına gelmez.

18 Eylül başlangıç koşumunda **414 test geçti, 1 test başarısız** oldu.
Zamanlamalar sayfasındaki mobil taşma 19 Eylül'de düzeltildi ve sonraki tam
koşumlarda geçti. Başlangıç ölçümü bölüm 14'te, önceki teslim bölüm 16'da,
4 Ekim teslimi ve kalan yayın kabulü bölüm 17'de, 12 Ekim'de doğrulaması kapanan
akış aktarımı bölüm 18'de, yeni düğüm sözleşmesi işi bölüm 20'dedir.

## 2. Kesin gereksinimler ve önerilen kararlar

Kesin gereksinimler: MultiRPA markası, tanıtım sitesi → uygulama alt alan adı,
kayıt/giriş, e-posta doğrulaması, admin onayı, kullanıcı başlatmalı bir aylık
deneme, üç iş sınırı, tek cihaz lisansı, masaüstünde akış oluşturma ve süre
dolunca çalıştırmanın kapanması.

| Karar | Planın önerisi | Durum |
| --- | --- | --- |
| “3 iş” anlamı | Demo boyunca toplam 3 akış kimliği; tekrar çalıştırma serbest; silme hak açmaz | Uygulandı |
| “1 ay” anlamı | 1 takvim ayı; başlangıç sunucu UTC saati, sonraki ayda aynı gün yoksa ayın son günü | Uygulandı |
| Masaüstü bağlantısı | İş verisi yerelde; merkezi sunucuya yalnız hesap eşleştirme ve lisans/hak kontrolü | Uygulandı |
| Lisans kapsamı | Onaylı müşteri hesabına 1 lisans ve 1 aktif masaüstü cihazı | Uygulandı |
| Hesap ile kurum ilişkisi | İlk sürümde tek sahip; çok üyeli kurum yönetimi ayrı faz | Tek sahip uygulandı |
| İlk müşteri masaüstü hedefi | Windows; Linux build/GUI/runner geliştirme kanıtı mevcut | Windows kabulü bekliyor |
| Ücretli devam | Admin bitiş/kota atar ve iptal eder; ödeme sağlayıcısı ayrı iş paketi | Manuel hak uygulandı |
| Ağ kesintisi | Güncel çevrimiçi izin gerekir; editör/veri incelemesi açık kalır | Uygulandı |

Kullanıcının “yap, doğru olmayan yeri değiştiririz” talebi üzerine bu değerler
başlangıç politikasıdır. Sonraki ürün düzeltmeleri merkezi politika ve kabul
testlerine birlikte yansıtılır; farklı ekranlara ayrı iş kuralları yazılmaz.

## 3. Marka ve logo

Logo yönü: birden çok akışın birleşmesini anlatan, düğüm ve bağlantılardan oluşan
yalın bir **M** simgesi; yanında okunaklı **MultiRPA** yazısı. Koyu lacivert temel,
turkuaz vurgu; açık/koyu zemin ve tek renk varyantları. Küçük boyutta okunur SVG
ana dosya ve favicon ile masaüstü PNG/ICO çıktıları hazırdır; ICNS/macOS sonraki kapsamdır.
Bağlantılı M simgesi, yatay açık/koyu logo, tek renk simge ve favicon hazırlandı;
mevcut panele, tanıtım, müşteri ve yönetici ekranlarına uygulandı. Electron
paketinde PNG/ICO simgeler hazırdır; macOS/ICNS bu teslimin kapsamı değildir.

Ürün adı `app/core/branding.py` üzerinden sunucu ve HTML'e aktarılır; istemci
sekme başlığını bu değerden alır. Markanın uygulandığı yüzler: tanıtım sitesi,
uygulama menüsü, sekme başlığı, favicon, giriş/kayıt/onay ekranları, admin paneli,
e-postalar, demo/lisans ekranları, Electron başlığı/simgesi, indirilen dosya adları,
README ve geliştirici erişim etiketleri. Windows yükleyici adı/logo ayarları hazırdır;
gerçek imzalı Windows yükleyicisi üretme/kabul adımı bekliyor.

Müşteriye görünen eski konsol/yönetim ürün adları kaldırıldı; ad/logo web ve
Electron'da MultiRPA olarak uygulanıyor.
RPA bir teknik terim olarak kalabilir. Mevcut veritabanı tablo adları, migration
kimlikleri ve çalışan servis kimlikleri yalnız marka değişsin diye kırılmayacak;
gerekli teknik yeniden adlandırmalar uyumluluk planıyla yapılacak.

## 4. Web yüzleri ve kullanıcı yolculuğu

Alan adı henüz verilmedi. Aşağıdakiler sahip olunan alan adı seçildiğinde
uygulanacak şablonlardır; alan adının alınmış olduğu varsayılmaz:

- `https://<alan-adı>`: ürün, yetenekler, kullanım örnekleri, demo ve giriş bağlantısı.
- `https://app.<alan-adı>`: kayıt, giriş, müşteri paneli, Flow Studio, lisans/cihaz ve indirme.
- `https://app.<alan-adı>/admin`: yalnız yetkili yönetici; TOTP ve ayrı audience/cookie. Ayrı admin hostname'i bu profile eklenmedi.
- Backend/lisans API: uygulamanın güvenli API katmanı; PostgreSQL internete veya istemcilere açılmaz.

Müşteri akışı:

```text
Tanıtım sitesi → Uygulamaya git → Kayıt / Giriş
Kayıt → E-postayı doğrula → Yönetici onayı bekleniyor
Admin onayı → Demoyu başlat → 1 ay / 3 akış
Süre sonu → Demo tamamlandı → Lisans talebi / admin tarafından etkinleştirme
```

Kayıt olan kişi doğrulama ve bekleme ekranına erişebilir; iki koşul tamamlanmadan
iş akışlarına, verilere, runner'a ve indirme/etkinleştirme haklarına erişemez.
Admin onay bekleyen hesapları görebilir; demo süresi kayıt veya onay anında işlemez.
Başlatma isteği atomiktir: çift tıklama ve eşzamanlı istekler tek başlangıç üretir.

E-posta doğrulama bağlantısı tek kullanımlı ve sürelidir. Tekrar gönderme,
süre dolması, teslim edilememe ve parola sıfırlama akışları uygulandı. Kuyruklu
gönderim/sınırlı tekrar/outbox durumu vardır; sağlayıcı webhook teslim bildirimi
bu sürümde yoktur. Gerçek mail sağlayıcısında teslim kabulü ayrı yapılmalıdır.
Gönderici alan adı için SPF/DKIM/DMARC kurulacak; gerçek dış posta kutusunda test
yapılmadan “e-posta tamamlandı” denmeyecek. Bağlantılar sabit güvenilir origin'den
üretilecek; token'lar ham olarak veritabanında veya loglarda tutulmayacak.
[OWASP e-posta doğrulama rehberi](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html).

## 5. Gerçek admin paneli

Admin ekranı backend yetkileriyle korunur; menüyü gizlemek erişim kontrolü sayılmaz.
İlk admin güvenilir kurulum komutuyla oluşturulacak; herkese açık admin kaydı olmayacak.

- Kullanıcı/kurum listesi; e-posta doğrulama ve onay durumu.
- Onayla, gerekçeyle reddet, askıya al, yeniden etkinleştir.
- Demo başlangıcı/bitişi, kullanılan 3 akış hakkı ve lisans durumu.
- Cihaz etkinleştirme, son lisans kontrolü, lisans iptali ve cihaz değişim onayı.
- Ücretli lisans bitişi/kotası atama ve iptal uygulandı; demo tarihini değiştiren uzatma bu sürümde yok.
- Onay, ret, rol, süre ve cihaz değişikliklerinin yönetici denetim geçmişi.

Hesap askıya alındığında oturumlar ve yeni çalıştırma izinleri iptal edilir;
kuyruk ve zamanlayıcı da bunu uygular. Mevcut iş durumu geçişleri için kullanılan
audit tablosundan bağımsız olarak kullanıcı/lisans yönetimini de kapsayan audit
uygulandı. Admin API'si müşterinin hedef sistem parolalarını okuyamaz.

## 6. Demo süresi ve ortak kota

Sunucuda kalıcı `trial_started_at`, `trial_ends_at`, ücretli plan/haklar ve akış
kota kayıtları tutulur; API bitişi `expires_at` olarak döndürür. Web/masaüstü
aynı demo ve kotayı kullanır; toplam üç kimliktir. Web/API/scheduler/worker
güncel hakkı, yerel runner merkezi signed izni uygular.

Kota akış kimliği üzerindedir: webde yeni akış oluşturma veya içe aktarma
yeni hak tüketir. Masaüstünde kaydetme/içe aktarma yerel taslak oluşturur;
merkezi hak ilk çalıştırmadan önce ayrılır. Mevcut akışın sürümlerini düzenlemek
ve tekrar çalıştırmak yeni hak tüketmez. Silinen akışın kota izi korunur. Eşzamanlı web/desktop
isteklerinde son hak yalnız bir isteğe verilir. Aynı tenant/flow UUID tekrarında
yeni hak tüketilmez; imzalı cihaz izni ayrılmış opak flow kimliklerini kapsar.
Web/masaüstü arasında sürümlü JSON dosyasıyla kullanıcı denetiminde aktarım
vardır; otomatik içerik eşitlemesi yoktur. İçe aktarılan kopya yeni kimlik alır
ve çalıştırılabilir hâle gelirken kendi kota hakkını kullanır.
Bir kullanıcının mevcut bir akışı tamamen farklı bir işe dönüştürmesi teknik
olarak “aynı iş” diye kesin ayırt edilemez; üç sınırı üç akış kimliğidir.

Bağlantı yokken yeni akış/içe aktarma yalnız yerel taslak olabilir; çalıştırılabilir
akışa dönüşmeden önce merkezi kota hakkı ayrılması zorunludur.

Masaüstü verileri yerelde tutulsa bile ortak kota için sunucuya yalnız opak akış
kimliği/kota hakkı kaydı gider; akış içeriği, hedef parolalar ve işlenen belgeler
gönderilmez. Bu da lisans/hak trafiğinin parçasıdır. Hiçbir ek istek kabul
edilmiyorsa çevrimiçi ortak kota modeli yeniden kararlaştırılmalıdır.

Süre dolduğunda “Demo tamamlandı” ekranı gösterilir. Yeni manuel/zamanlanmış iş,
kuyruktaki işin sahiplenilmesi, yeniden deneme ve yeni dış etkili adım engellenir.
Devam eden dış sistem çağrısı geri alınmış varsayılmaz; süre sınırında güvenli
kontrol noktasında durur, gerekiyorsa sonuç doğrulamaya düşer. Kullanıcı verisi
silinmez. Onaylı hesabın kendi kaydedilmiş akışını incelemesi/dışa aktarması
süre dolduğunda da açıktır; webde yeni kayıt/içe aktarma ve çalıştırma engellenir.
Masaüstünde yerel taslak düzenleme ve aktarım çevrimdışı da sürdürülebilir.

Yeniden kurulum, çıkış/giriş, farklı saat ayarı veya tekrar indirme başlangıç ve
bitişi sıfırlamaz. Hesap silip açma/yeni e-posta ile yeni kişi gibi görünme riskini
tek başına cihaz/IP çözmez; admin müşteri/kurum geçmişini inceleyerek tekrar demo
verilmesini kontrol eder. Saklama/silme politikası ayrıca açıkça tanımlanır.

## 7. Tek bilgisayar lisansı

İndirme anındaki IP bir bilgisayar kimliği değildir: aynı ofiste birden çok
bilgisayar aynı dış IP'yi kullanabilir. Bu nedenle IP alanı ancak isteğe bağlı
kurumsal ağ izin listesi olacak. Dosyanın başka yere kopyalanmasını engellemek
yerine o cihazda etkinleştirme ve çalıştırma reddedilecek.
[IETF — IP adresi paylaşımının etkileri](https://www.rfc-editor.org/info/rfc6269/).

1. Kullanıcı masaüstünde web hesabıyla giriş yapar; sunucu tek kullanımlık cihaz challenge'ı üretir.
2. Masaüstünde cihaz anahtarı üretilip Electron safeStorage ile korunur; TPM/attestation sonraki güvence fazıdır.
3. Sunucu hesabın doğrulama/onay/demo hakkını kontrol eder; ilk cihaz bağlamasını atomik yapar.
4. Bir lisansa yalnız bir aktif cihaz atanır; sonraki cihaz kendi başına üzerine yazamaz.
5. Sunucu cihazın anahtar sahipliğini sınamayla doğrular ve süreli, imzalı izin verir.
6. Aynı bilgisayarda yeniden kurulum kalan süreyi kullanır; anahtar kaybolmuşsa admin kurtarması gerekir.
7. Cihaz değişimi admin işlemidir; eski cihazın yenilemesi kapatılır. Verilmiş son iznin süresi dolmadan yeni cihazın izni başlamaz; demo tarihi değişmez.

IP, MAC adresi veya yalnız bir donanım parmak izi yeterli temel değildir. Mevcut cihaz anahtarı
Electron `safeStorage` ile korunur; güvenli secret store yoksa aktivasyon
reddedilir. TPM/attestation ayrıca uygulanmalıdır. Electron `safeStorage` sır saklamaya yardım eder;
tek başına kopyalanamaz cihaz anahtarı veya TPM garantisi sağlamaz.
[Microsoft TPM](https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/tpm-fundamentals),
[Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage).

“Hayatında tek kez istek atsın, tamamen çevrimdışı kalsın ve süre/cihaz denetimi
asla aşılamasın” birlikte garanti edilemez. Öneri: açılışta ve her yeni çalıştırma
öncesinde çevrimiçi lisans kontrolü; uzun işlerde en çok 60 saniyelik imzalı izin
ve mevcut Electron main'de 25 saniyede bir yenileme. İzin süresi demo bitişini aşamaz. Adımlar geçerli
izni yerelde kontrol eder; her adımda ağ isteği gerekmez. Yenileme başarısızsa
mevcut izin dolduğunda yeni dış etkili adım başlamaz. Bu kısa pencere dışında
çevrimdışı demo çalıştırması sunulmaz. Admin iptali en geç mevcut 60 saniyelik
iznin sonunda uygulanır. Uyku/uyanma, yeniden başlatma ve saat geri alma
doğrulama gerektirir. Değiştirilmiş yerel yazılıma karşı mutlak kopya koruması
vaat edilmez.

## 8. Electron ve ortak RPA mimarisi

Mevcut Python/FastAPI/PostgreSQL çekirdeği korunup geliştirildi. Electron ortak
web editörünü kullanır; otomasyonu özel Python child runner yürütür. Müşteri
paketine merkezi PostgreSQL bağlantı bilgisi veya private signing key verilmez.

```text
Tanıtım sitesi → Müşteri uygulaması → Backend → Merkezi PostgreSQL
                         ↓             ↓
                   Web akış editörü   Sunucu worker / scheduler

Electron → Aynı editör bileşenleri → Yerel runner → Windows / tarayıcı / dosyalar
    ↓                                  ↓
Lisans API                         Yerel çalışma deposu
```

Ortak sözleşme: sürümlü akış şeması, değişkenler, düğüm kataloğu, hata türleri,
çalıştırma olayları ve yetenek bildirimi. Düğüm “sunucu / masaüstü / her ikisi”
yetenekleriyle preflight edilir; desteklemeyen runner ilk etkiden önce reddeder.
Motorun yerel context/saklama uyarlaması SQLite ile uygulandı;
aynı kodun Electron'a paketlenmesi tek başına yeterli değildir. Merkezi ürün
veritabanı PostgreSQL kalır; yerel çalışma deposu merkezi lisans otoritesi değildir.

Yerel modda web ile otomatik akış/veri eşitleme yapılmaz. Sürümlü JSON
içe/dışa aktarma arayüzü kullanıcının seçtiği akış tanımını taşır; kasa sırları,
iş geçmişi, yerel belgeler veya lisans kaydı dosyaya eklenmez. Eşitleme veya webden masaüstü iş başlatma seçilirse ayrıca görev kanalı,
cihaz çevrimiçi durumu, izinler ve veri aktarımı tasarlanır; “yalnız lisans trafiği”
vaadi o mod için değişir. Kullanıcının akışının hedef sitelere/API'lere yaptığı
istekler, MultiRPA sunucusuna gönderilen trafikten ayrıdır.

Electron arayüzü sandbox/context isolation ile çalışır; dosya ve otomasyon
işlemleri dar, doğrulanmış IPC üzerinden ayrı runner'a gider. Paket ve güncelleme
yetki kontrolleri ve sırların işletim sistemi kasasında saklanması uygulandı.
Windows paket/güncelleme imzası ve geri alınabilir production sürüm dağıtımı
gerçek build/altyapı kabulü gerektirir.
[Electron güvenlik rehberi](https://www.electronjs.org/docs/latest/tutorial/security).

İlk sürüm Windows'ta kullanıcı oturumu açıkken çalışan RPA'yı hedefler. Kilitli
ekran, RDP kopması ve gözetimsiz çalışma ayrı runner/oturum politikası ve gerçek
Windows testleri gerektirir; Electron bunları kendiliğinden çözmez.

## 9. RPA yetenekleri ve kapsam sırası

| Aşama | Yetenekler | Tamamlanma kanıtı |
| --- | --- | --- |
| Çekirdeği genişlet | Döngü, alt akış, kontrollü hata yakalama ve uzun iş checkpoint'i gelecekte; mevcut adım/etki izin kontrolü uygulandı | Sürümlü akışlar ve tekrar/iptal senaryoları |
| İlk müşteri paketi | Dosya/CSV/Excel, HTTP API/JSON, e-posta ve sır kasası uygulandı; klasörlerin bağımsız yönetimi yok | İzole gerçek dosya/XLSX/HTTPS/SMTP testleri ve paketli Linux runner; gerçek müşteri hedefi kabulü bekliyor |
| Windows masaüstü | UIA click/fill/extract kodlandı; uygulama açma, klavye ve kilitli/gözetimsiz oturum sonraki kapsam | Gerçek Windows uygulaması ve imzalı yükleyici kabulü henüz yok |
| Sonraki adaptörler | PDF/OCR, veritabanı, SFTP, ERP/SAP ve hedefe özel bağlayıcılar | Seçilen müşteri senaryosuna göre ayrı kabul testleri |
| İleri işletim | Takvim/cron, saat dilimi, gözetimsiz runner, webhook ve çoklu runner kapasitesi | Saat/durum/yeniden başlatma ve yük testleri |

Mevcut 40 adım/120 saniye/DAG sınırları gerçek süreçlere göre yeniden tasarlanacak;
yalnız sınırları kaldırmak yeterli değildir. Uzun işler, döngü içi checkpoint,
iptal, retry ve dış sistemde işlemin gerçekten yapıldığını doğrulama birlikte ele
alınacak. Düğüm kataloğu genişletilebilir olacak; tüm sektör bağlayıcıları ilk
sürümde hazırmış gibi sunulmayacak.

## 10. Veri modeli ve sunucu kuralları

Uygulanan kavramlar: kullanıcılar, kurumlar, ayrı customer/admin oturumları, e-posta doğrulama
ve parola sıfırlama token'ları, onay kararları, demo/hak kayıtları, akış kota
kayıtları, lisanslar, cihazlar, etkinleştirmeler, süreli izinler, yönetim audit'i
ve e-posta gönderim kuyruğu.

E-posta durumu, admin onayı, hesap durumu ve demo/lisans durumu ayrı tutulacak.
Parolalar uygun parola hash'iyle saklanacak; güvenli oturum cookie'leri, CSRF
koruması, giriş/doğrulama denemelerine hız sınırı ve admin MFA uygulanacak.
Admin ve müşteri oturumlarının alt alan adları arasında yanlış paylaşılması
engellenecek; çıkış, parola değişimi ve askıya alma oturum iptalini kapsayacak.
Her akış, sürüm, job, transaction, schedule, checkpoint ve hata ilgili kuruma
bağlanacak. Müşteri kimliği istemcinin serbest alanından değil doğrulanmış
oturumdan alınacak. Listeleme, detay, güncelleme, export, worker ve scheduler
dahil her erişimde kurum sınırı uygulanacak; bileşik FK/unique kuralları bunu
destekleyecek. Mevcut global idempotency anahtarları kurum kapsamına taşınacak.

Mevcut örnek/veriler sahipliği belirlenmiş geliştirme kurumuna kontrollü migration
ile aktarılacak; bilinmeyen kayıtlar müşterilere açılmayacak. Migration öncesi
backup, staging denemesi ve geri dönüş planı hazırlanacak. Production erişimi
mevcut development korumasını basitçe kaldırarak açılmayacak.

## 11. Onaylanan uygulama sırası ve kabul kapıları

Bu tablo ilk kabul hedeflerini korur; güncel tamamlanma durumu bölüm 1, 17 ve 18'dedir.

| Sıra | Teslimat | Kabul ölçütü |
| --- | --- | --- |
| 0 | Bu durum raporu ve ürün kararları | Kota, ay hesabı, cihaz ve çevrimdışı politika net |
| 1 | MultiRPA logo paketi, tutarlı marka ve mevcut mobil taşma düzeltmesi | Tüm müşteri yüzlerinde yeni ad/logo; eski ad taraması ve mobil görünüm testi temiz |
| 2 | Kimlik, kurum izolasyonu ve e-posta | Kayıt → gerçek e-posta → doğrulama; başka müşteriye erişim reddi |
| 3 | Admin paneli ve onay kapısı | Onaysız/askıdaki hesap doğrudan API ve worker yoluyla da çalışamaz |
| 4 | Demo ve lisans/hak servisi | Tek başlangıç, atomik 3 hak, ortak bitiş ve süre sonu engeli |
| 5 | Tanıtım sitesi, app/admin adresleri ve staging | HTTPS, yönlendirme, oturum ve mail akışı gerçek ortamda çalışır |
| 6 | Ortak editör/runner sözleşmesi ve Electron | Windows kurulum, tek cihaz aktivasyonu ve yerel akış çalıştırma |
| 7 | İlk geniş RPA düğüm paketi | Dosya/Excel/e-posta/API ve masaüstü örnekleri uçtan uca çalışır |
| 8 | Pilot ve production yayını | İzleme, yedek geri yükleme, lisans süre sonu ve yükseltme testleri geçer |
| 9 | İleri adaptörler ve ölçek | Önceliklendirilen müşteri senaryoları ayrı kabul edilir |

Staging dışında müşteriye açma, tüm kimlik/onay/tenant/lisans kontrolleri
tamamlandıktan sonra yapılacak. Test ortamında kısa ömürlü deneme kullanarak süre
sonu sınanacak; gerçek bir ayın geçmesi beklenmeyecek.

## 12. Kritik kabul senaryoları

- Doğrulanmamış veya admin onaysız hesap login olsa bile ürünü çalıştıramaz.
- Doğrulama bağlantısının süresi/tek kullanımlılığı ve mail tekrar gönderimi doğru çalışır.
- İki eşzamanlı demo başlatma tek başlangıç/bitiş üretir.
- Aynı anda web ve masaüstünden 4. akış oluşturma kotayı aşamaz; doğrudan API de reddeder.
- Akış silme, yeniden indirme/kurma, hesap oturumu yenileme ve saat değişimi demo süresini sıfırlamaz.
- Aynı lisansa iki cihaz eşzamanlı bağlanamaz; paket/izin dosyası kopyası başka cihazı yetkilendirmez.
- Aynı IP'deki iki bilgisayar tek bilgisayar sayılmaz; aynı bilgisayarın ağ değişimi cihaz kimliğini değiştirmez.
- Admin cihaz değişiminde eski cihazın izni tanımlanan kısa yenileme sınırı içinde geçersizleşir.
- Demo bitişi/askıya alma; manuel API, kuyruk, zamanlama, retry ve açık masaüstü runner'ında uygulanır.
- Devam eden dış etkili işlem zorla tekrarlanmaz; belirsiz sonuç doğrulamaya bırakılır.
- Müşteri A, kimlik tahminiyle B'nin akışını/işini/sırrını göremez veya çalıştıramaz.
- Web ve masaüstü aynı akış şemasını açar; desteklenmeyen adımlar çalıştırma öncesi açıklanır.
- Sırlar, lisans anahtarları, cihaz özel anahtarları ve iş verileri loglara veya istemci paketine sızmaz.
- Windows yeniden başlatma, uyku/uyanma, kilitli ekran ve ağ kesintisi açık durumlarla yönetilir.
- İmzalı kurulum/güncelleme, veri koruma ve önceki sürümden geçiş test edilir.
- Yedekten geri dönüş ayrı ortamda denenir; lisans ve execution geçmişi tutarlı kalır.

## 13. Yayın öncesi gerekli dış bilgiler

Kullanılacak alan adı ve DNS yönetimi; e-posta gönderim sağlayıcısı ve gönderen
adres; ilk admin hesabı; Windows sürüm hedefi ve imza sertifikası; production
PostgreSQL/private ağ, sır yönetimi, backup ve izleme sağlayıcısı. Gerçek RPA
örnekleri için hedef uygulamalar, gerekli test hesapları ve işlem yetkileri.
Bu bilgiler sır olarak kod veya bu belgeye yazılmayacak.

## 14. Başlangıç doğrulaması — 18 Eylül 2026

Koşum komutu: `.venv/bin/python scripts/test_postgres.py -- -m '' -o faulthandler_timeout=40`.
Kayıt: `artifacts/multirpa-audit-2026-09-18.txt`. Bu koşum kendi silinebilir
PostgreSQL container'ını kullanır; geliştirme veya production DB'si test hedefi
değildir.

- **414 passed, 1 failed, 2 warnings in 116.61s**; testler atlanmadı.
- Başarısız test: `tests/browser/test_schedules.py::test_once_schedule_converts_local_time_and_executes_the_saved_flow`, satır 189.
- Hata: 390×844 görünümde `document.documentElement.scrollWidth` / `document.body.scrollWidth` pencereyi aşıyor.
- Aynı testin önceki kontrollerinde tarih dönüşümü, gerçek çalıştırma, tek SUCCESS kayıt, bir deneme, beklenen çıktı ve sürüm doğrulaması geçti.
- Alembic 0001–0003 upgrade → downgrade → upgrade ve `alembic check` geçti.
- Test container'ı koşum sonunda kaldırıldı.
- Ruff lint ve 70 dosyada format kontrolü geçti.
- İki uyarı Starlette/httpx ve AnyIO kullanım dışı kalma uyarıları; bağımlılık bakımında ele alınacak.

Bu bölüm 18 Eylül başlangıç ölçümüdür. Mobil hata planın 1. fazına eklendi;
19 ve 20 Eylül uygulama sonuçları aşağıdaki bölümlerde ve [VALIDATION.md](../VALIDATION.md)
içinde ayrıca tutulur.

## 15. Kod kalitesi ve mevcut kodun dönüşümü — onaylanan ek kapsam

Kullanıcının talebi: proje temiz, sürdürülebilir ve geniş RPA kapsamına uygun
yazılacak; mevcut kodun yazım/mimari biçimi de gerektiğinde değiştirilecek.
Bu gereksinim yalnız yeni ekranlar için değildir. Ayrıntılı kurallar
[ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md) içinde tanımlandı ve
`AGENTS.md` üzerinden sonraki geliştirmelere bağlandı.

| İş paketi | Yapılacak değişiklik | Kabul kanıtı |
| --- | --- | --- |
| HTTP katmanını ayır | Composition, istek modelleri, router, middleware, hata ve runtime ayrımı | Mevcut 31 endpoint, hata kodları ve güvenlik davranışı korunur |
| Ortak doğrulamayı çıkar | JSON/metin/UUID/sayı kuralları core'da; motor service özel fonksiyonunu import etmez | Payload/sır/Unicode/boyut testleri aynı sonuçları üretir |
| Kaynak ömrünü sağlamlaştır | Kısmi worker başlangıcı, başlangıç hatası ve birden fazla kapanış hatasında kaynaklar kapatılır | Hata enjekte edilen lifecycle testleri |
| UoW ve servis bileşimi | Flow→Execution ve Schedule→Flow mirası yerine açık işlem/saklama bağımlılıkları | Atomiklik, belirsiz commit, idempotency ve retry davranışı korunur |
| Tipli uygulama sözleşmeleri | Domain değerleri, servis giriş/çıkışları ve HTTP yanıt modelleri | Yeni modüllerde tip kontrolü ve API sözleşme testleri |
| Frontend modülleri | Ortak API istemcisi, sunum yardımcıları, ayrı özellik durumları; okunabilir kaynak ve ortak editör paketi | Taslak/sürüm çakışması, kayıt/çalıştırma ve dar ekran testleri |
| Sürümlü düğüm yapısı | Düğüm kataloğu, açık giriş/çıkış, yetenek/iptal/süre/yan etki sözleşmeleri | Eski flow sürümleri çalışır, desteklenmeyen runner erken reddeder |
| Ürün güvenliği | Tenant, auth/onay, demo/kota ve cihaz lisansı ayrı kurallar; tüm yürütme yollarında uygulanır | Çapraz müşteri, kota yarışı, ikinci cihaz, süre sonu testleri |
| Üretim işletimi | Ayrı API/worker/scheduler süreçleri, yapılandırma, sır yönetimi, izleme ve yedek geri dönüşü | Pilot gerçek ortam kabulü |

Bu sırada tüm sistemi bir kerede yeniden yazmak veya gereksiz mikroservisler
oluşturmak hedef değildir. Çalışan davranış korunur, ölçülebilir sorun düzeltilir,
ilgili test ve belge aynı değişiklikte güncellenir. Sadece dosya sayısının artması
veya test sayısı, kalite ve tamamlanma kanıtı sayılmaz.

19 Eylül ilk uygulama: MultiRPA SVG marka paketi/panel adları, modüler HTTP
katmanı, typed servis bağımlılığı, ortak core doğrulayıcıları, mobil başlık
yerleşimi ve kaynak ömrü düzeltmeleri tamamlandı. **427 test geçti**, hata/skip
yok; lint, 85 dosyada format, bağımlılık ve wheel paketleme kontrolü başarılı.
Geliştirme paneli bu sürüme alındı. Ayrıntılı kanıt [VALIDATION.md](../VALIDATION.md).
İkinci refactor olarak ortak `DatabaseUnitOfWork`, bağımsız Execution/Flow/Schedule
servisleri, aynı transaction içinde ortak flow yayını, saf flow sözleşmesi ve
iş durumu geçiş bileşeni kodlandı. Transaction hata sınırlarını doğrulayan
12 ek testle birlikte tam regresyon **439 passed, 2 warnings in 115.56s** olarak
tamamlandı. Eski zamanlama testlerinin flow işlemleri doğru FlowService bağımlılığına
taşındı; kabul kontrolleri korunuyor.

## 16. Tarihsel kimlik ve onay uygulaması — 20 Eylül 2026

Tanıtım, müşteri kayıt/giriş/doğrulama/parola yenileme ve gerçek yönetici onay
ekranları ayrı `create_portal_app` girişinde kodlandı. Hesapların tenant ilişkisi,
ayrı müşteri/yönetici oturumları, yönetici TOTP ve tekrar engeli, onay/ret/askıya
alma audit'i, PostgreSQL tabanlı istek sınırlama ve SMTP outbox teslimi uygulandı.
İşletim ve veri modeli [IDENTITY_ONBOARDING.md](IDENTITY_ONBOARDING.md) içinde.

İlk hedefli koşum giriş çerezi tarih biçiminde hata buldu; PostgreSQL UTC saat
dilimi HTTP cookie için `datetime.UTC` biçimine çevrildi. Son hedefli koşum:
**75 passed, 2 warnings in 17.72s**. Ayrı yerel SMTP alıcısına gerçek teslim,
tek kullanımlık doğrulama, onay/oturum iptali, eşzamanlı tüketim ve güvenlik
sınırları doğrulandı. Son tam koşuma sekiz terminal/sır dosyası testi de dahil edildi.
Son tam koşum **525 passed, 2 warnings in 140.53s**: hata, skip veya deselect yok.
İki Chromium senaryosu müşteri kaydı/doğrulaması, yönetici TOTP girişi/onayı, ayrı
oturumlar ve mobil giriş/kurtarma sayfalarını doğruladı. Tanıtım, müşteri ve admin
ekranlarının masaüstü/mobil görselleri alındı; yatay taşma yok. Migration 0001–0005
döngüsü ve Alembic şema karşılaştırması, Ruff lint/115 dosyada format, 15 yeni kaynak
dosyasında mypy, bağımlılık ve wheel paketleme denetimi geçti.

İlk tam koşumda eski konsolun `0003` revision'ına sabitlenmiş readiness kontrolü
uyumsuzluk üretti. Ortak şema denetimiyle düzeltildi; son 525 testlik sonuç bu
düzeltmeyi içeriyor. Testler 20 Eylül'de tamamlandı; kayıt 22 Eylül'de güncellendi.
Kanıt: `artifacts/multirpa-onboarding-full-final-2026-09-20.txt` ve
[VALIDATION.md](../VALIDATION.md).

22 Eylül'de doğrulanan sürüm geliştirme ortamına uygulandı: migration head `0005`,
konsol `127.0.0.1:8765`, ayrı portal `127.0.0.1:8770`. Yerel HTTP denetimleri geçti;
portalın kalıcı servis kurulumu yapılmadı. Kayıt:
`artifacts/multirpa-dev-smoke-2026-09-22.txt`.

Bu tarihsel teslim sırasında yürütme tenant izolasyonu ve ürün denemesi henüz yoktu.
Aşağıdaki sınırlar 20–22 Eylül durumuna aittir; güncel uygulama bölüm 17'dedir.
Onaylı hesap dahi bu portalda eski geliştirme kayıtlarını okuyamaz/çalıştıramaz.
Gerçek alan adı, SMTP sağlayıcısı ve ilk admin bilgileri henüz yapılandırılmadı;
yerel test gerçek müşteriye teslim ve public yayın kanıtı sayılmaz.

Sonraki iş sırası: mevcut flow/job/schedule/worker verilerinin tenant kapsamına
taşınması → tek başlangıçlı bir takvim ayı/üç flow demo ve tüm çalıştırma yollarında
hak kontrolü → cihaz lisansı → gerçek alan adı/e-posta ile staging → ortak editör,
yerel runner ve Electron → Windows otomasyonu ve geniş düğüm paketi → pilot/yayın.

## 17. Tenant, demo, cihaz, ortak masaüstü ve ilk RPA paketi — 4 Ekim 2026

4 Ekim son tam koşumu **790 passed, 2 warnings in 161.33s** ile tamamlandı;
testler atlanmadı veya seçilerek dışarıda bırakılmadı. Kayıt
`artifacts/multirpa-full-2026-10-04.txt`. Önceki 3 Ekim **759 passed** koşumu
`artifacts/multirpa-full-2026-10-03.txt` içinde tarihsel olarak korunur. İzole gerçek PostgreSQL'de 0001–0008
upgrade → downgrade → upgrade ve Alembic model/şema karşılaştırması geçti.
3 Ekim'de Ruff lint ve 159 kaynak dosyasının format kontrolü geçti; 4 Ekim son
kaynak kontrolünde sayı 163 oldu. Yapılandırılmış 44 kaynak dosyasında mypy ve
Python bağımlılık denetimi de geçti. Eski kaynakların tamamının
mypy kapsamına alındığı iddia edilmez.

### Tamamlanan uygulama

- **Kurum ayrımı:** 0006 tüm yürütme tablolarına tenant sahibi ekler. Eski kayıtlar
  sabit, müşterilere açılmayan geliştirme tenant'ına taşınır. Tenant bileşik
  FK/unique kısıtları çapraz ilişkiyi de engeller. Müşteri principal'ı servis
  kapsamını belirler; worker locked kayıt sahibini kullanır. Bir müşterinin
  süresi dolunca scheduler diğer müşterilerin planlarını işlemeye devam eder.
- **Demo ve hak:** 0007 tek seferlik explicit takvim ayı başlangıcı, ortak üç
  lifetime flow kimliği, eşzamanlı son hak yarışı ve ücretli devam kayıtlarını
  içerir. Başlatma tekrarı, silme ve reinstall geçmişi değiştirmez. API,
  flow/schedule yayını, claim, heartbeat/checkpoint ve dış adım kontrolünde
  güncel onay/askı/süre denetlenir. Süre dolduğunda geçmiş okunabilir; yeni
  çalıştırma engellenir.
- **Gerçek müşteri/admin ekranı:** `/account` demo başlangıç/bitiş/kota ve
  indirmeyi, `/workspace` tenant'a ait ortak akış editörü/işler/zamanlamalar/
  bağlantı kasasını sunar. Teknik demo ve serbest job oluşturma müşteri API'sinde
  yoktur. Admin gerekçeyle ücretli bitiş/kota atar, iptal eder ve cihaz değişimi
  yapar; TOTP/ayrı oturum/CSRF ve yönetim audit'i korunur.
- **Cihaz lisansı:** amaç/tenant/key bağlı, süreli ve tek kullanımlık challenge
  Ed25519 ile ispatlanır. Bir aktif cihaz DB unique kuralıyla da korunur.
  Sunucu en fazla 60 saniyelik, hak bitişini aşmayan izin imzalar. Admin reset
  önceki iznin bitişine kadar yeni cihazı bekletir; emekli cihaz anahtarı geri
  bağlanamaz. IP cihaz kimliği değildir; TPM ve mutlak kopya koruması sunulmaz.
- **Geniş ilk düğüm paketi:** dokuz temel düğüme dosya oku/yaz, CSV oku/yaz,
  Excel oku/yaz, HTTP request, e-posta gönder ve üç Windows UIA türü eklendi.
  Toplam 20 tür vardır. Dosya kökü tenant/yerel hesap alanına sınırlı; HTTP,
  browser ve SMTP hedefleri operator izinli ve private ağlara kapalıdır.
  Dış etkiler intent/checkpoint ve belirsiz sonuç karantinası kullanır.
  Desteklenmeyen Windows/adapter akışı ilk etkiden önce reddedilir.
- **Sır kasası:** 0008 AES-GCM ciphertext'i tenant, alias, tür ve sürüme bağlar.
  HTTP/browser/SMTP bağlantı bilgileri yalnız yazılır; API metadata döndürür.
  Silme tombstone ve optimistic version kuralıyla yapılır. Çözülmüş sırların
  değişken/checkpoint/sonuç veya başka dış etkiye aktarılması guard ile engellenir.
  İşletim sınırı [kasa rehberinde](SECRET_VAULT.md) kayıtlıdır.
- **Electron ve yerel yürütme:** web ile aynı renderer, dar IPC ve özel Python
  JSON-lines child process kullanılır; dinleyen local HTTP portu yoktur.
  SQLite/sabit sürüm/checkpoint cihazda kalır; merkezi trafik auth/lisans/opak
  kota kimlikleriyle sınırlıdır. `safeStorage` güvensiz plaintext fallback'i
  reddeder. Paketli Linux runner imzalı izinle gerçek TXT/CSV/XLSX akışını
  tamamladı; Electron GUI ortak editörü açtı. Linux development arşivi
  `.runtime/releases/MultiRPA-0.1.0-linux-x64.tar.gz` (310.352.105 byte) olarak
  stage edildi; SHA-256
  `1860a1c07fd7e63ae00caf6d2fb6e0216285f5f84f2ced4192e95a95ae5e5009`.
  ASAR'daki 32 güncel kaynak ve runner'daki 26 Python bytecode kaynağı eşleşti;
  gerçek extracted runner smoke tekrar geçti. Yalnız onaylı müşteri indirebilir.
  Loopback origin/public key
  pin'li bu arşiv production müşteri paketi değildir.
- **Geliştirme işletimi:** `scripts/portal_manage.py start/status/stop` portalı
  terminalden bağımsız sürdürür; PID/start-time/cwd/argv sahipliği doğrulanır.
  4 Ekim development rollout'unda dokuz execution tablo sayısı korunarak head
  0008 uygulandı ve 8765/8770 managed process'leri açıldı. Dump yeni disposable
  PostgreSQL'de gerçekten restore edildi; identifier/retained-data hash'leri
  migration öncesi/sonrası eşleşti. Production restore kanıtı değildir.
- **Yayın hazırlığı:** portal production'da HTTPS origin, ayrı kalıcı signing
  key ve ayrı execution process ister. Generator sır okumadan root statik
  site/app proxy ve non-root API/execution/mail unit'lerini üretir. 4 Ekim'de
  yeni örnek `artifacts/deployment-example-2026-10-04` içinde üretildi; üç unit,
  SMTP sır kapsamı, SIGTERM/150 saniye kapanış, local proxy ve forwarding
  overwrite kontrolü geçti. Profil `deployed: false`; servis kurulmadı.

### Yayını tamamlamak için kalan kabul

1. Sahip olunan gerçek alan adı/DNS/TLS, mail sağlayıcısı ve doğrulanmış gönderen,
   ilk admin kimliği ile private uzak PostgreSQL/runtime-migration rolleri ve
   secret dağıtımı sağlanmalı. [Yayın rehberi](DEPLOYMENT.md) uygulanıp gerçek
   root → app → doğrulama maili → admin onayı → demo yolculuğu sınanmalı.
2. Windows build ortamında gerçek Python runner ve NSIS yükleyicisi üretilmeli,
   imza sertifikasıyla imzalanmalı. Kurulum/kaldırma/reinstall, ikinci cihaz,
   reset, DPAPI, ağ kesintisi, saat/uyku ve gerçek UIA hedefi kabulü yapılmalı.
   Linux build veya unit testi bu kabulün yerine geçmez; hazır komutlar
   [masaüstü rehberinde](DESKTOP.md) bulunmaktadır.
3. Geliştirme dump'ının yeni izole PostgreSQL'e gerçek restore'u ve 0005→0008
   migration provası geçti; dokuz execution tablosunun sayı/kimlik/içerik
   özetleri korundu. Development DB de 0008'e alınıp 8765/8770 yeniden açıldı.
   Kanıtlar [VALIDATION.md](../VALIDATION.md) içindedir. Gerçek production
   backup/secret key kurtarma, izleme ve restart/yükseltme kabulü ayrıca yapılmalı.

Bunlar uygulanmış ürünü gerçek ortama bağlama ve platform kabul adımlarıdır.
Ödeme sağlayıcısı, çok üyeli kurumlar, cron/saat dilimi, döngü/alt akış/uzun iş,
OCR/PDF/ERP/SAP/SFTP ve gözetimsiz runner ayrı gelecek fazdır. Bu geniş RPA
ürününü yalnız tarayıcı düğümleriyle sınırlamaz; her yeni capability kendi hedef
sistem ve tekrar/iptal/sonuç doğrulama kabulüyle eklenir.


## 18. Sürümlü akış dosyası aktarımı — 5 Ekim 2026

Başlangıç: 5 Ekim; tam doğrulama ve Linux paketi: 12 Ekim 2026.

Web ve Electron ortak editörüne akış tanımını dosyayla taşıma eklendi. Kaydedilmiş, değişiklik beklemeyen akış **Dışa aktar** ile alınır. **İçe aktar** dosyayı boyut/şema/grafik sınırlarıyla doğrular, ad ve adım sayısını gösterir; kullanıcının ayrı oluşturma işlemi yeni kimlik ve ilk sürümü üretir. Var olan akış ezilmez, iş veya zamanlama kendiliğinden açılmaz.

Web içe aktarması tenant ve aktif demo/lisans sınırında normal oluşturma işlemiyle atomik kota ayırır. Masaüstü içe aktarması yerel taslak olarak saklanır; merkezi kota ve cihaz izni ilk çalıştırmadan önce alınır. Onaylı müşteri süre bittikten sonra da kendi kaydedilmiş akışını dışa aktarabilir; başka müşterinin kimliğiyle veri okunamaz.

Dosya, akışa yazılmış sabit değerleri ve kasa referans adlarını içerir. Kasa parolaları, oturum/cihaz/lisans bilgileri, çalıştırma geçmişi ve belgeler taşınmaz. İçerik otomatik olarak tüm kişisel/veri niteliğindeki metinlerden arındırılmış sayılmaz. Hedef kurulumda kasa bağlantıları ve operatörün izinli hedef ayarları ayrıca hazırlanır.

Aktarım kullanım ve API sözleşmesi [Flow Studio rehberinde](FLOW_STUDIO.md),
12 Ekim doğrulama kayıtları [VALIDATION.md](../VALIDATION.md) içinde tutulur.
Tam koşum **854 passed, 2 warnings in 180.78s**; paketli Electron'da dosya
önizleme, yeni çevrimdışı taslak ve dışa aktarma geçti. Linux arşivinde 34 ASAR
kaynağı ve 27 native Python modülü, sunucu wheel'inde 130 kaynak eşleşti.
Bu aşama gerçek DNS/SMTP/Windows ve production kabulünün yerine geçmez.

## 19. GENRPA referans incelemesinden çıkan işler — 9 Ekim 2026

[Kaynaklı yetenek karşılaştırması](GENRPA_GAP_ANALYSIS.md) resmî GENRPA belgeleri
ile mevcut MultiRPA kodunu karşılaştırır. Kullanıcı kuyruğu ile mevcut yürütme
kuyruğu; ayrıntılı kurumsal roller ile mevcut admin/müşteri yetkileri ayrı ele
alınır. Akış içe/dışa aktarımı mevcut olduğundan eksik özellik olarak sayılmaz.

| Sıra | Yapılacak iş | Kabul / bağımlılık |
| --- | --- | --- |
| 0 | Aktarım tesliminin Electron paketi ve tam doğrulaması 12 Ekim'de kapandı | 854 test, gerçek paket aktarımı ve kaynak eşleşmesi geçti; Windows ve gerçek yayın kabulleri açık |
| 1a | Sürümlü düğüm sözleşmesi: uygulama/doğrulama sürüyor | Eski grafik ve özetleri korunur; sürüm/runner desteği ilk dış etkiden önce denetlenir; bölüm 20 |
| 1b | Döngü, alt akış, hata dalları ve uzun işler: yapılacak | Döngü içi checkpoint, alt akış sürümü, tüm çağrılan kimliklerin kotası, iptal/lisans bitişi ve belirsiz dış sonuç koruması birlikte doğrulanır |
| 2 | Görsel element seçici, debugger ve Windows eylemleri | Gerçek hedefte adımlama; Windows UIA/DPAPI ve imzalı kurulum kabulü |
| 3 | Excel/DataTable, mail okuma/ekler, PDF/OCR, müşteri DB'si ve dosya aktarımı | Ortak adaptör sözleşmesi, büyük veri sınırları, kasa, retry/iptal ve gerçek hedef kabulü |
| 4 | Kullanıcı kuyrukları, cron/saat dilimi, olay tetikleyicileri, çok üyeli kurum ve ayrıntılı roller | Kurum/yetki ayrımı ve eşzamanlılık; masaüstü yerel zamanlama ayrıca tasarlanır |
| 5 | SAP/ERP, AI ve hazır uygulama bağlantıları; operasyon/tasarruf raporları | Doğrulanmış müşteri senaryosu, ölçülebilir çıktı ve maliyet/hata politikası |
| Ayrı ürün kararı | Merkezi müşteri robot filosu ve uzaktan görev dağıtımı | İş verisini yerelde tutan lisans trafiğiyle sınırlı mevcut mod korunur; merkezi yönetim ayrı seçilebilir mod gerektirir |

Otomatik kalıcı taslak, proje/klasör düzeni, toplu paket ve bağımlılık taşıma da
geliştirici deneyimi kapsamına alındı. Genel recorder, SFTP ve SSO yararlı
geliştirme adaylarıdır; araştırmada GENRPA desteği doğrulanmış gibi sunulmaz.
Yeni ve değiştirilen mevcut kod aynı mühendislik standardını izler; bu yetenekler
mevcut tenant/lisans/kuyruk güvencelerini koruyan artımlı dönüşümle eklenir.

## 20. Düğüm davranış sürümü ve katalog — 13 Ekim 2026

Durum: uygulama ve doğrulama sürüyor. 12 Ekim sonuçları yeni kodun tamamlanma
kanıtı olarak kullanılmaz. Ayrıntılı ortak sözleşme ve sonraki döngü/alt akış
kabul tasarımı [FLOW_CONTRACT.md](FLOW_CONTRACT.md) içindedir.

- Tür ve davranış sürümü değişmez katalogda birlikte çözülür. Mevcut 20 tür
  v1'dir; eksik `type_version` kalıcı v1 anlamını taşır. Açık alan yalnız tam
  sayı `1` kabul eder; bilinmeyen sürümler çalıştırma öncesinde reddedilir.
- Eski grafiğe doğrulama sırasında alan eklenmez; özetler, sabit çalışma
  girdileri ve checkpoint uyumluluğu korunur. Yeni editör düğümleri açık v1
  yazar; eski akışların toplu veya örtük dönüşümü yapılmaz.
- Aktarım v1 yalnız eski alansız düğümleri taşır; v2 açık sürümlü veya karma
  düğümleri kabul eder. Dışa aktarma grafiği değiştirmeden uygun zarfı seçer.
- Akış kayıt revizyonu, düğüm davranış sürümü ve arşiv biçim sürümü ayrı
  kavramlardır. Şema desteği, runner'ın gerçek yeteneği ve Windows gibi
  platformların saha kabulü ayrı izlenir.
- **Döngü, alt akış, hata dalları ve uzun işler henüz yoktur.** DAG ve
  40 düğüm/120 saniye sınırları korunur. Sonraki tasarım iterasyon/iç içelik
  bütçesi, genel deadline, iterasyon başına checkpoint adresi, tenant/UUID/sabit
  alt akış revizyonu, tüm çağrı kimliklerinde kota ve browser reconciliation
  kurallarını birlikte kapsar.

Bu çalışma mevcut motor/aktarımı artımlı olarak dönüştürür; tenant, demo,
lisans, idempotency ve claim/lease/fencing güvenceleri aynı kalır. Yeni tam
test ve paket sonuçları [doğrulama kaydına](../VALIDATION.md) eklendiğinde bu
bölümün durumu güncellenecek.
