# RPA çalışma ortamı

- Proje kökü sunucuda /opt/rpa dizinidir.
- Kullanıcının açık talebi: kod, bağımlılıklar, testler ve çalıştırma işlemleri bu sunucuda yapılır.
- Proje kodunu Mac veya başka bir yerel makineye taşıma; sunucudaki dosyaları doğrudan düzenle.
- Veritabanı gereksinimleri için DATABASE_ARCHITECTURE.md dosyasını oku.
- Production veritabanı PostgreSQL olacak; bağlantı sırlarını environment variable veya secret yönetiminden al.
- Kimlik bilgilerini, bağlantı dizelerini ve hassas verileri çıktılara veya Git'e yazma.
- Sunucudaki diğer projeler ve çalışan servisler bu projenin kapsamı dışındadır.
- Bu kurulum yalnızca geliştirme ortamını hazırlamıştır; RPA iş akışının kapsamını kullanıcının talebinden belirle.

## MultiRPA geliştirme standardı

- Ürünün müşteri yüzündeki adı MultiRPA'dır; yeni modüllerde ayrı ürün adı türetme.
- Güncel kapsam ve aşamalar için `docs/MULTIRPA_PLAN.md`, kod yazım ve dönüşüm kuralları için `docs/ENGINEERING_STANDARDS.md` okunur.
- Kullanıcının talebi gereği standartlar yalnız yeni kod için değildir: değiştirilen mevcut modüller de ilgili sorumluluk sınırına taşınır; gerekli dönüşüm plana ve tamamlanma kaydına eklenir.
- API, kullanım senaryosu, domain, saklama ve dış sistem adaptörlerinin sorumluluklarını ayır. Çalışan davranışı koruyan artımlı dönüşüm yap; yalnız biçim tercihi için tüm sistemi yeniden yazma.
- Yetki, tenant, demo/kota ve lisans kontrolleri yalnız arayüzde bırakılamaz. Tamamlanmamış özellikler çalışan veya production hazır diye sunulmaz.
- Flow sürümü, idempotency, claim/lease/fencing, checkpoint ve belirsiz sonuç doğrulama güvenceleri refactor sırasında korunur; ilgili testler izole ortamda çalıştırılır.
