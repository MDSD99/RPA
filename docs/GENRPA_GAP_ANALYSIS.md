# GENRPA referansıyla MultiRPA yetenek ve eksik analizi

İnceleme tarihi: 9 Ekim 2026. MultiRPA kaynak kodu: `/opt/rpa`.

GENRPA tarafında resmî ürün belgeleri ve sürüm notları, MultiRPA tarafında
mevcut uygulama kodu ve kabul kayıtları incelendi. GENRPA kurulumu üzerinde
uygulamalı performans veya güvenilirlik testi yapılmadı. Belgelenen bir özellik,
bütün müşteri ortamlarında çalıştığına dair bağımsız kabul kanıtı değildir.
MultiRPA'da kodlanmış özellik ile dağıtımı ve gerçek platform kabulü tamamlanmış
özellik de aynı kabul edilmedi.

En büyük farklar akış dilinin kapsamı, otomasyon geliştirme araçları, Windows
işletimi ve hazır uygulama bağlantılarıdır. Kimlik, lisans ve yürütme altyapısı
mevcut; bunların yeniden sıfırdan yapılması gerekmiyor.

## Kaynaklı karşılaştırma

| Alan | GENRPA'da belgelenen yetenek | MultiRPA'daki durum ve yapılacak iş |
| --- | --- | --- |
| Döngü ve alt akış | DataTable üzerinde [Loop](https://docs.genrpa.com/genstudio/uygulama-butonlari/home/loop), giriş/çıkış parametreleriyle tekrar kullanılabilir [Function](https://docs.genrpa.com/aktiviteler/favorite-activities/function). | **Eksik.** 20 düğüm türü var; grafik döngüsüz, en fazla 40 düğüm ve 120 saniye. Döngü, alt akış çağrısında sürüm sabitleme ve uzun iş yürütmesi birlikte tasarlanmalı; mevcut akış çalıştırmaları zaten sürüme bağlı. GENRPA'nın bu belgelerinden sınırsız çalışma veya her tür döngü desteği çıkarılmıyor. |
| Akış içinde hata yönetimi | Seçili aktivitelerde teknik/iş hatası yakalama ve hata sonrası işlem tanımlama: [Error Handling](https://docs.genrpa.com/aktiviteler/favorite-activities/error-handling). | **Kısmi.** Worker retry, hata kaydı ve belirsiz sonucu doğrulama var. Kullanıcının editörde kuracağı try/catch/finally ve hata dalı yok. Yeni hata yakalama, belirsiz dış işlemi kendiliğinden tekrarlatmamalı. |
| Geliştirme araçları | Web Picker ve breakpoint listesi: [sürüm notları](https://www.genrpa.com/tr/newrelease). | **Eksik.** Tarayıcıda aç/doldur/tıkla/metin al mevcut; CSS seçicisi elle giriliyor. Görsel element seçici, breakpoint, adımlama ve değişken izleme eklenmeli. Genel ekran hareket kaydedicisinin GENRPA kapsamı ayrıca doğrulanmalı. |
| Windows robotu | Gözetimli ve gözetimsiz robot çalışma modeli: [bileşenler](https://docs.genrpa.com/genrpa-baslarken). | **Kısmi ve kabul bekliyor.** Electron/yerel runner ve üç UIA eylemi kodda var. İmzalı Windows yükleyicisi ve gerçek hedef uygulama kabulü yok. Uygulama başlatma, klavye, oturum yönetimi ve gözetimsiz çalışma ayrı işler. |
| Excel ve veri tabloları | Excel aralığını WHERE koşuluyla DataTable olarak alma: [Read Range as Database](https://docs.genrpa.com/aktiviteler/excel/database/read-range-as-database); daha geniş etkinlikler [katalogda](https://docs.genrpa.com/aktiviteler). | **Kısmi.** CSV/XLSX satır okuma-yazma var. Dosya adaptöründe 200 satır, 32 sütun ve 8 KiB sonuç sınırı bulunuyor. Hücre/range, filtre/join/gruplama, biçimlendirme, pivot ve formül/makro desteği ayrı geliştirilmeli; büyük veri için parçalı işleme gerekli. |
| E-posta | Mesaj seçme, ek indirme ve okundu işaretleme: [Outlook](https://docs.genrpa.com/aktiviteler/outlook/messages/download-attachments-of-mail-messages); IMAP/OAuth2 ayarları [Mail Trigger](https://docs.genrpa.com/gen-control-room/process-management/triggers.md) içinde belgeleniyor. | **Kısmi.** Otomasyon SMTP ile yalnız alıcı/konu/metin gönderiyor. Mail okuma, ekler, klasör işlemleri, OAuth yenileme ve Outlook bağlantısı yok. Platformun hesap doğrulama e-postası bu otomasyon yeteneklerinin yerine geçmez. |
| PDF ve OCR | Taranmış PDF'den OCR ile metin çıkarma: [PDF Image To Text](https://docs.genrpa.com/aktiviteler/pdf/pdf-image-to-text). | **Eksik.** PDF/OCR düğümü yok. Metin/tablo çıkarma, sayfa işlemleri ve taranmış belgeler için güven puanı ile kontrol yolu geliştirilmeli. |
| İş akışından veritabanı erişimi | Bağlantıda sorgu çalıştırma: [MS SQL Exec Query](https://docs.genrpa.com/aktiviteler/database/ms-sql/exec-query). | **Eksik.** Ürünün PostgreSQL kullanması, müşterinin veritabanına sorgu atan bir akış düğümü olduğu anlamına gelmez. Parametreli sorgu, bağlantı kasası, sonuç sınırı ve işlem politikası olan adaptör gerekli. |
| SAP | SAP GUI grid hücrelerine erişim ve tablo çıkarma: [SAP GUI Grid View Operation](https://docs.genrpa.com/aktiviteler/sap-automation/sap-gui-grid-view-operation). | **Eksik.** Genel Windows UIA kodu SAP bağlayıcısı sayılmaz. İlk gerçek SAP senaryosu ve erişilebilir test ortamı üzerinden ayrı adaptör/kabul hazırlanmalı. |
| Zamanlama ve olaylar | Cron/takvim ve makine seçimi: [Schedules](https://docs.genrpa.com/gen-control-room/process-management/schedules); dosya ve gelen mail olayları: [Triggers](https://docs.genrpa.com/gen-control-room/process-management/triggers.md). | **Kısmi.** Sunucuda tek sefer/sabit aralık var. Cron, yerel saat dilimi, iş takvimi, dosya/mail tetikleyicisi ve Electron yerel zamanlayıcısı yok. |
| İş verisi kuyrukları | İş anahtarı, durum, öncelik ve işlem ayrıntısı: [Queue Database](https://docs.genrpa.com/gen-control-room/process-management/queue-database). | **Kısmi.** Dayanıklı job/transaction kuyruğu, claim, retry ve checkpoint mevcut. Kullanıcının tanımlayıp akıştan veri ekleyeceği/çekeceği adlandırılmış kuyruklar ve ilgili editör düğümleri yok. |
| Merkezi robot yönetimi | Robotların merkezi yönetimi [bileşenlerde](https://docs.genrpa.com/genrpa-baslarken), makineye görev ataması [zamanlamada](https://docs.genrpa.com/gen-control-room/process-management/schedules) belgeleniyor. | **Ürün kararı gerekli.** Sunucu worker havuzu var; müşteri bilgisayarlarından oluşan robot filosu, kapasiteye göre görev dağıtımı ve uzaktan çalıştırma yok. Yerel iş verisini sunucuya göndermeme gereksinimi korunmalı; merkezi yönetim ancak ayrı seçilebilir mod olarak ele alınmalı. |
| Kurumsal roller | İşlem ve süreç bazında okuma/yazma/çalıştırma/silme/export izinleri: [Roles](https://docs.genrpa.com/gen-control-room/settings/roles.md). | **Kısmi.** Müşteri/admin ayrımı, kurum izolasyonu, onay ve admin TOTP var. Çok üyeli kurum, geliştirici/operatör/izleyici rolleri ve proje bazlı izinler eksik. GENRPA SSO/SAML/LDAP desteği bu incelemede doğrulanmadı. |
| Operasyon ve tasarruf raporları | Robot kullanım/başarı eğilimleri: [Robot Performance](https://docs.genrpa.com/gen-control-room/dashboards/robot-performance-and-utilization); maliyet tasarrufu: [Robot Cost Savings](https://docs.genrpa.com/gen-control-room/dashboards/robot-cost-savings). | **Kısmi.** İş geçmişi, hata, checkpoint, audit ve sayaçlar var. Robot kapasitesi, süre/başarı eğilimleri, iş alarmı ve hesaplama varsayımları açık tasarruf raporları eksik. |
| Yapay zekâ ve hazır SaaS bağlantıları | ChatGPT görsel işlemleri, MCP ve Teams/Slack/Jira aktiviteleri [sürüm notlarında](https://www.genrpa.com/tr/newrelease) belirtiliyor. | **Eksik.** Genel HTTP isteği var; hazır AI/MCP veya bu uygulamalara özel düğüm yok. Gereken bağlantı, kimlik yenileme, yapılandırılmış çıktı, maliyet ve hata politikası birlikte sağlanmalı. |
| Büyük proje düzeni | Otomatik kayıt, klasörler, toplu dışa aktarım, proje araması ve UiPath geçiş aracı [sürüm notlarında](https://www.genrpa.com/tr/newrelease) belirtiliyor. | **Kısmi.** Tek akışın sürümlü `.multirpa.json` içe/dışa aktarımı artık kodda mevcut. Otomatik kalıcı taslak, proje araması, klasör/toplu paket, bağımlılık taşıma ve başka üreticiden dönüşüm yok. Yeni aktarım kodunun güncel dağıtım paketi/kapsamlı kabulü ayrıca izleniyor. |

## Eksik diye tekrar yapılmayacak mevcut temel

- E-posta doğrulama, admin onayı, askıya alma, admin TOTP ve denetim kaydı.
- Açık başlangıçlı bir takvim aylık deneme, ortak üç kalıcı akış kimliği kotası.
- Tek aktif cihaz, imzalı kısa süreli çalışma izni ve süre sonunda yeni işlem engeli.
- Kurum izolasyonu, şifreli bağlantı kasası ve akış sürümleri.
- Sunucu kuyruğu, worker, retry, sahiplik/izin süresi kontrolü, checkpoint ve belirsiz sonucun doğrulanması.
- Web/Electron ortak editörü; dosya, CSV/XLSX, HTTP, SMTP ve temel tarayıcı eylemleri.
- Web ve masaüstünde sürümlü akış dosyası içe/dışa aktarma kodu.

Kod kanıtları: [motor](../app/workflows/flow_engine.py),
[adaptör sözleşmesi](../app/workflows/automation_contract.py),
[dosya/Excel sınırları](../app/adapters/automation_files.py),
[tarayıcı](../app/workflows/browser_actions.py),
[worker](../app/workers/runtime.py), [kimlik](../app/identity/models.py),
[akış aktarımı](../app/workflows/flow_transfer.py),
[masaüstü runner](../app/desktop/runner.py).

## Uygulama sırası ve kabul ölçütleri

Bu sıra mühendislik değerlendirmesidir; GENRPA'nın ürün yol haritası değildir.
Mevcut işlerin kod tarzı da [mühendislik standardına](ENGINEERING_STANDARDS.md)
göre değiştirilen bölümle birlikte dönüştürülecek; mevcut sistem toptan yeniden
yazılmayacak.

1. **Mevcut teslimi kapat.** Akış aktarımının güncel Electron paketi ve ilgili
   tam doğrulama kaydı tamamlanmalı. Gerçek DNS/TLS/mail/ilk admin ve Windows
   kurulum kabulü açık kalemdir. Bunlar araştırma yapıldı diye tamamlanmış sayılmaz.
2. **Akış motorunu genişlet.** Önce sürümlü düğüm sözleşmesi; sonra sınırlandırılmış
   foreach/döngü, alt akış, hata dalı ve uzun iş. Döngü içi checkpoint, sürümü
   sabit alt akış çağrısı, iptal ve lisans bitişi kabulde gösterilmeli. Dış sistemde
   yapılmış olabilecek işlem doğrulanmadan yeniden gönderilmemeli. Yalnız
   40 düğüm/120 saniye limitlerini artırmak bu işi tamamlamaz.
3. **Geliştirici ve masaüstü deneyimini tamamla.** Görsel element seçici, adımlama,
   breakpoint/değişken izleme; gerçek Windows uygulamasında başlat/tıkla/doldur/oku
   senaryosu ve imzalı kurulum. Recorder ayrı bir geliştirme önerisidir.
4. **Günlük iş adaptörlerini genişlet.** Excel/DataTable → mail okuma/ekler →
   PDF/OCR → müşteri veritabanı ve dosya aktarımı. Her adaptör ortak giriş/çıkış,
   kasa, süre/iptal ve belirsiz sonuç sözleşmesini kullanmalı. Büyük dosya/veri
   işlemleri mevcut küçük JSON değişken alanına sınırsız yüklenmemeli.
5. **Kurumsal işletimi ekle.** Kullanıcı kuyrukları, cron/saat dilimi, dosya/mail
   tetikleyicileri ve ayrıntılı roller. Merkezi filo yönetimi ayrı çalışma modu
   kararıdır; mevcut lisans trafiğiyle sınırlı masaüstü modunu değiştirmez.
6. **Müşteri senaryosuna göre SAP ve AI ekle.** Gerçek hedef sistemde tekrar,
   hata/iptal ve sonuç doğrulama kabulü gerekir. Temel motor/adaptörler
   tamamlanmadan yalnız çok sayıda düğüm eklemek ürün olgunluğu sayılmaz.

## Açık sınırlar ve doğrulanmayan başlıklar

- GENRPA'nın genel hareket kaydedicisi, SFTP, SSO/SAML/LDAP ve her kilitli
  Windows/RDP oturumundaki davranışı bu incelemede doğrulanmadı. Bunlar
  GENRPA'da kesin var diye listelenmiyor. SFTP ve SSO MultiRPA için ayrıca
  değerlendirilebilir.
- Python/PostgreSQL ile .NET/MSSQL seçimi tek başına yetenek açığı değildir.
- İş verisinin cihazda kalması ve sunucuya yalnız hesap/lisans/kota trafiği
  gitmesi mevcut ürün kararıdır. Uzaktan görev ve merkezi masaüstü logu eklemek
  bu veri akışını değiştirir; otomatik kapsam genişlemesi sayılmaz.
- Gerçek Windows/UIA/DPAPI kabulü, imzalı yükleyici ve production domain/mail
  kurulumu hâlâ açık. Linux'ta çalışan paket bunların kabul kanıtı değildir.
- Bu inceleme kod ve doküman denetimidir; yeni tam test koşumu veya GENRPA
  hız/ölçek karşılaştırması yapılmadı. Önceki test kanıtları [VALIDATION.md](../VALIDATION.md)
  içinde tarihleriyle tutulur.

Güncel ana plan: [MULTIRPA_PLAN.md](MULTIRPA_PLAN.md).
