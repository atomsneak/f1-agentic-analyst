# Güvenlik politikası

## Kapsam ve tehdit modeli

F1 Agentic Analyst **local-first** çalışacak şekilde tasarlanmıştır: üç yüzey varsayılan
olarak yalnız `127.0.0.1`'i dinler, dışarıya telemetri göndermez ve AI
sağlayıcısını kullanıcı kendisi seçer.

Bu kapsam dışında kalan ve bu deponun sorumluluğunda **olmayan** durumlar:

- Uygulamayı kimlik doğrulaması olmadan internete açmak. Arayüzde oturum/şifre
  katmanı yoktur; dışarıya açarsan bunu bir ters vekil (reverse proxy) ve
  kimlik doğrulama arkasına almak senin sorumluluğundadır.
- Çok kiracılı (multi-tenant) kullanım. Profil tablosu ve arşiv tek kullanıcı
  içindir; aynı örneği birden çok kişiye açmak, o kişilerin birbirinin
  sohbetlerini ve profil listesini görmesi demektir.
- Sağlayıcının kendi tarafındaki veri işleme. Anahtar ve prompt, seçtiğin
  sağlayıcıya gider; oradaki politika senin ile sağlayıcı arasındaki konudur.

## Anahtarların korunması

| Konu | Davranış |
|---|---|
| Saklama | `data/f1dash.db`, dosya modu `0600` (anahtar yazıldıktan sonra uygulanır) |
| Tarayıcıya dönüş | Asla. Yalnız `has_key` ve SHA-256 parmak izi (`sha:29b1d4`) |
| Log'lar | Sağlayıcı hatası `scrub()` ile maskelenir; anahtar hiçbir satıra basılmaz |
| Ortam değişkeni | `F1_AI_KEY` — istersen DB yerine kendi ortamında tut |
| Silme | Profil silinince satır (ve anahtar) veritabanından silinir |

Bir anahtarın sızdığını düşünüyorsan **önce onu sağlayıcı panelinden iptal et**;
sızmış bir anahtar yanmış sayılır, dosyadan silmek yeterli değildir.

## base_url doğrulaması (SSRF)

`base_url` kullanıcı girdisidir ve sunucu o URL'ye istek atar. `ai_config.check_base_url()`
her kaydetme ve "bağlantıyı test et" çağrısında çalışır ve şunları reddeder:

- `http`/`https` dışındaki tüm şemalar (`file://`, `ftp://`, `gopher://`, …)
- bulut meta veri uç noktaları (`169.254.169.254`, `metadata.google.internal`) — her zaman
- link-local, multicast, reserved ve unspecified IP aralıkları — her zaman
- RFC1918 özel ağ adresleri — `F1_AI_ALLOW_PRIVATE=1` verilmedikçe

Loopback (`127.0.0.1`, `localhost`) bilerek serbesttir: yerel Ollama/llama.cpp
çalıştırmak bu projenin olağan kurulumudur.

Kontrol hem URL'deki düz IP'ye hem de hostname'in çözüldüğü adreslere bakılarak
yapılır. DNS rebinding'e karşı tam koruma sağlamaz; bu yüzden arayüzü güvenilmeyen
kullanıcılara açmayın.

## Sır bildirimi

Gerçek bir sızıntı bulduysan lütfen **kamuya açık issue açma**. Depo sahibine
GitHub üzerinden özel mesaj ya da GitHub'ın *Report a vulnerability* (Security →
Advisories) akışıyla bildir. Anahtar sızıntılarında lütfen sızmış değeri mesaja
yazma; hangi dosya/satır olduğunu ve değerin **sha256** özetini yaz.
