# instagram-mcp

Instagram ma'lumotlarini olish uchun professional MCP server — hech qanday login talab qilmaydi (10 ta tool), authenticated rejimda esa barcha 13 ta tool ishlaydi.

Claude Desktop, Claude Code va boshqa MCP-compatible AI assistantlar bilan to'g'ridan-to'g'ri ishlaydi.

---

## Mundarija

- [Xususiyatlar](#xususiyatlar)
- [Toollar](#toollar)
- [O'rnatish](#ornatish)
- [Sozlash](#sozlash)
- [Autentifikatsiya (Ixtiyoriy)](#autentifikatsiya-ixtiyoriy)
- [Proksi](#proksi)
- [Claude Desktop bilan ulash](#claude-desktop-bilan-ulash)
- [Muhit o'zgaruvchilari](#muhit-ozgaruvchilari)
- [Arxitektura](#arxitektura)
- [Fayl strukturasi](#fayl-strukturasi)

---

## Xususiyatlar

- **13 MCP tool** — profildan tortib keng miqyosli batch scraping gacha
- **10 ta anonim tool** — hech qanday login yoki cookies talab qilmaydi
- **3 ta authenticated tool** — cookies.txt orqali kengaytirilgan ma'lumotlar
- **Kuchli pagination** — GraphQL cursor orqali 200 ta postgacha
- **Batch scraping** — parallel ravishda 500 ta profil, workers soni sozlanadi
- **TTL cache** — bir xil so'rovlar keshlangan, takroriy so'rovlar bir zumda
- **Adaptive rate limiter** — token-bucket + circuit breaker + jitter
- **Proxy rotatsiyasi** — avtomatik health-check, cooldown, fallback
- **curl_cffi impersonation** — Chrome brauzeri sifatida so'rov yuboradi (bot detection bypass)
- **Barcha transport** — STDIO (Claude Desktop) va HTTP (custom integrations)

---

## Toollar

### Anonim toollar (🌐 — login talab qilmaydi)

| # | Tool | Tavsif |
|---|------|--------|
| 1 | `instagram_profile` | Profil ma'lumotlari + so'nggi 12 ta post teglari + akkaunt holati |
| 2 | `instagram_feed_deep` | Chuqur pagination: 200 tagacha post tahlili |
| 3 | `instagram_analyze_engagement` | Engagement rate, kontent turi, eng yaxshi kunlar, top postlar |
| 4 | `instagram_find_collab_network` | Usertag, @mention, co-author, paid partnership xaritasi |
| 5 | `instagram_compare_profiles` | 2-5 akkauntni parallel solishtirish |
| 6 | `instagram_bulk_check` | 20 tagacha akkauntni parallel tekshirish |
| 7 | `instagram_batch_scrape` | 500 tagacha profil, parallel workers, sana filtri |
| 8 | `instagram_server` | Server diagnostika + cache boshqaruvi |
| 9 | `instagram_post` | Bitta post: joylashuv GPS, caption, hashtag, usertag, musiqa |
| 10 | `instagram_post_comments` | Post komentnlari: likes, reply, GIF, til tarkibi |

### Authenticated toollar (🔐 — cookies.txt kerak)

| # | Tool | Tavsif |
|---|------|--------|
| 11 | `instagram_tagged_by` | BOSHQALAR tomonidan tag qilingan postlar (Tagged Tab) |
| 12 | `instagram_reposts` | Akkaunt qayta post qilgan kontentlar (Reposts Tab) |
| 13 | `instagram_reels` | Akkauntning o'z reelslari + **play count** (faqat shu tool) |

> **Muhim:** `play_count` faqat `instagram_reels` tooli orqali olinadi. `instagram_feed_deep` va `instagram_analyze_engagement` reelslarda `view_count=null` qaytaradi — bu Instagram API cheklovi.

---

### Tool tafsilotlari

#### `instagram_profile`
```
Parametrlar:
  username          — Instagram username (@siz)
  include_feed      — so'nggi postlarni ham olib kelish (default: true)
  max_feed_posts    — nechta post (1-12, default: 12)
  check_alive       — akkaunt faolmi? (default: true)
  dead_threshold_days — necha kundan so'ng "o'lik" hisoblanadi (default: 365)
  max_age_days      — faqat shu kundan yangi postlar (default: 4)

Qaytaradi:
  - followers, following, posts count, bio, website, category
  - is_verified, is_business, is_private
  - Hashtags va @mentions (so'nggi 12 ta postdan)
  - last_post_days, is_dead
```

#### `instagram_feed_deep`
```
Parametrlar:
  username          — Instagram username
  max_posts         — nechta post (1-200, default: 50)
  max_age_days      — necha kunlik postlar (1-365)
  since / until     — sana oralig'i filtri (DD.MM.YYYY)

Qaytaradi:
  - Har bir post: shortcode, URL, likes, comments, caption, hashtag, usertag
  - Xronologik tartib, eng yangi birinchi
  - pages_fetched, has_more ko'rsatkichlari
```

#### `instagram_analyze_engagement`
```
Parametrlar:
  username          — Instagram username
  max_posts         — tahlil uchun postlar soni (1-200, default: 50)

Qaytaradi:
  - Engagement rate % (likes+comments / followers * 100)
  - Kontent turi tarkibi: rasm, video, carousel, reel foizlari
  - Eng yaxshi posting kunlari (hafta kunlari bo'yicha)
  - Top 5 post by engagement
  - Top 10 hashtag
  - Avg likes, avg comments, median engagement
```

#### `instagram_find_collab_network`
```
Parametrlar:
  username          — Instagram username
  max_posts         — tahlil qilinadigan postlar (1-200, default: 50)
  min_frequency     — minimum necha marta uchrashi kerak (default: 1)

Qaytaradi:
  - Usertags: rasmlarda tag qilinganlar
  - @mentions: captiondagi eslatmalar
  - Co-authors: birgalikda yaratilgan postlar
  - Paid partnerships: sponsored kontentlar
  - Har bir akkaunt uchun chastotasi
```

#### `instagram_compare_profiles`
```
Parametrlar:
  usernames         — 2-5 ta username ro'yxati

Qaytaradi:
  - Yon-yonma jadval: followers, posts, ER%, so'nggi post
  - Parallel fetch — tez ishlaydi
```

#### `instagram_bulk_check`
```
Parametrlar:
  usernames         — 20 tagacha username
  check_alive       — faollik tekshirish (default: true)

Qaytaradi:
  - Har bir akkaunt: status (active/dead/private/not_found), followers, so'nggi post
```

#### `instagram_batch_scrape`
```
Parametrlar:
  targets           — 500 tagacha username ro'yxati
  since_date        — boshlang'ich sana (DD.MM.YYYY)
  until_date        — tugash sanasi (DD.MM.YYYY)
  max_workers       — parallel workers (1-20, default: 10)
  use_cookies       — authenticated rejim (default: false)
  output_file       — natijani saqlash yo'li (bo'sh = temp fayl)

Qaytaradi:
  - JSON fayl yo'li
  - Muvaffaqiyatli / muvaffaqiyatsiz / skip statistikasi
```

#### `instagram_post`
```
Parametrlar:
  post              — shortcode yoki to'liq URL
                      ('DXjuqH9nDVE' yoki 'https://instagram.com/p/DXjuqH9nDVE/')

Qaytaradi:
  - Likes, comments, views/plays
  - Caption, hashtags, @mentions
  - Joylashuv: nom + GPS koordinatlari + Google Maps link
  - Co-authors, sponsored tags
  - Reel: artist va musiqa nomi
  - Aniq vaqt (taken_at)
```

#### `instagram_post_comments`
```
Parametrlar:
  post              — shortcode yoki to'liq URL (/p/, /reel/, /tv/)
  max_comments      — nechta komment (1-500, default: 100)
  sort_order        — 'popular' (likes bo'yicha) yoki 'recent' (vaqt bo'yicha)

Qaytaradi:
  - Har bir komment: matn, like_count, reply_count, author, vaqt
  - GIF kommentlar alohida ko'rsatiladi
  - has_translation=true — rus yoki boshqa til kommentlar
  - Top 5 komment (likes bo'yicha)
  - Eng ko'p komment qoldirganning to'rttasi
  - Caption ham kiradi (is_caption=true)
  - Auditoriya tili tarkibi %
```

#### `instagram_tagged_by` 🔐
```
Parametrlar:
  username          — Instagram username
  max_posts         — nechta post (1-200, default: 50)

Qaytaradi:
  - BOSHQA akkauntlar ushbu profilni tag qilgan postlar
  - Har bir post: poster username, shortcode, likes, caption excerpt, vaqt
  - Bu "passiv" — biz ular haqida postlar qilingan
```

#### `instagram_reposts` 🔐
```
Parametrlar:
  username          — Instagram username
  max_reposts       — nechta (1-200, default: 50)

Qaytaradi:
  - Bu akkaunt QAYTA POST qilgan kontentlar
  - Har bir repost: original autor, original post URL, likes, caption, vaqt
  - Bu "aktiv" — biz o'zimiz tarqatgan kontent
```

#### `instagram_reels` 🔐
```
Parametrlar:
  username          — Instagram username
  max_reels         — nechta reel (1-200, default: 50)

Qaytaradi:
  - Play count (ASOSIY ko'rsatkich — faqat shu endpoint orqali)
  - Likes, comments, thumbnail, o'lcham, vaqt, is_pinned
  - Top 5 reel (plays bo'yicha)
  - Jami plays, o'rtacha plays/likes/comments
```

#### `instagram_server`
```
Parametrlar:
  action            — 'status' | 'clear_cache' | 'clear_user'
  username          — 'clear_user' uchun username

Qaytaradi (status):
  - Cache hit rate, entries soni, o'lcham
  - Proxy holati: faol, cooldown, ishlamayotgan
  - Rate limiter: joriy RPS, circuit breaker holati
  - Server versiyasi va transport turi
```

---

## O'rnatish

### Talablar

- Python 3.10+
- `uv` paket menejeri (tavsiya etiladi) yoki `pip`

### 1-qadam: Repozitoriyani klonlash

```bash
git clone https://github.com/yourusername/instagram-mcp.git
cd instagram-mcp
```

### 2-qadam: Dependencieslarni o'rnatish

```bash
# uv bilan (tez)
uv sync

# pip bilan
pip install -e .
```

### 3-qadam: Test qilish

```bash
# Server ishlay oladimi?
uv run python -c "from instagram_mcp import create_mcp_server; print('OK')"
```

---

## Sozlash

Barcha sozlamalar muhit o'zgaruvchilari orqali beriladi. Fayllar qo'shimcha config talab qilmaydi — default qiymatlar production uchun yetarli.

### Asosiy muhit o'zgaruvchilari

```bash
# Autentifikatsiya (ixtiyoriy)
INSTAGRAM_MCP_COOKIES=/path/to/cookies.txt

# Proksilar (ixtiyoriy, vergul bilan ajratilgan)
INSTAGRAM_MCP_PROXIES=http://user:pass@host:port,http://host2:port2

# Transport (default: stdio)
INSTAGRAM_MCP_TRANSPORT=http        # HTTP server rejimida
INSTAGRAM_MCP_HOST=0.0.0.0          # HTTP host (default: 0.0.0.0)
INSTAGRAM_MCP_PORT=8000             # HTTP port (default: 8000)

# Cache
INSTAGRAM_MCP_CACHE_DISABLED=1     # Cacheni o'chirish
INSTAGRAM_MCP_CACHE_TTL=600        # Global TTL (sekund)
INSTAGRAM_MCP_CACHE_MAX=1000       # Maksimal kesh yozuvi

# Rate limiting
INSTAGRAM_MCP_RATE_LIMIT_RPS=50.0  # So'rov/sekund
INSTAGRAM_MCP_RATE_LIMIT_BURST=30  # Burst hajmi
```

Barcha o'zgaruvchilar uchun [to'liq ro'yxat](#muhit-ozgaruvchilari) ni ko'ring.

---

## Autentifikatsiya (Ixtiyoriy)

Autentifikatsiya bo'lmasa ham 10 ta tool ishlaydi. `instagram_tagged_by`, `instagram_reposts`, `instagram_reels` toollar uchun cookies kerak.

### Cookies.txt olish

**Usul 1: "Get cookies.txt LOCALLY" extension (Chrome/Firefox)**

1. Chrome Web Store dan ["Get cookies.txt LOCALLY"](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extensionini o'rnating
2. Instagram.com ga kiring (login bo'ling)
3. Extension ikonkasini bosing → "Export" → `cookies.txt` sifatida saqlang

**Usul 2: "EditThisCookie" extension (JSON format)**

1. EditThisCookie extensionini o'rnating
2. Instagram.com ga kiring
3. Extension → Export → JSON ni `cookies.json` sifatida saqlang

### Cookie faylini joylashtirish

```
# Variant 1: MCP server yoniga qo'ying
instagram_mcp/
├── cookies.txt     ← shu yerga
└── ...

# Variant 2: Muhit o'zgaruvchisi orqali
INSTAGRAM_MCP_COOKIES=/home/user/my_cookies.txt

# Variant 3: Yuqori papkaga
MCP/
├── cookies.txt     ← yoki shu yerga
└── instagram_mcp/
```

Server ishlaganda avtomatik topadi va yuklaydi.

### Cookie yangilash

Instagram sessiyasi ~90 kun davomida amal qiladi. Sessiya tugasa:
1. Brauzerda Instagram.com ga qayta kiring
2. Yangi cookies.txt eksport qiling
3. Faylni almashtiring (server restart shart emas — keyingi autentifikatsiyalangan so'rovda avtomatik yangilanadi)

---

## Proksi

Instagram so'rovlarini bir IP dan juda ko'p yuborish 429 xatosiga olib keladi. Proksilar orqali bu cheklovdan o'tish mumkin.

### proxies.txt fayli

```bash
# proxies.txt
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
socks5://user:pass@proxy3.example.com:1080
```

Fayl `instagram_mcp/` yoki `MCP/` papkasida bo'lishi kerak.

### Muhit o'zgaruvchisi orqali

```bash
INSTAGRAM_MCP_PROXIES="http://u:p@h1:8080,http://u:p@h2:8080"
```

### Proksi rotatsiya logikasi

- Har bir so'rov navbatdagi proxyni ishlatadi (round-robin)
- Proxy 5 marta ketma-ket 429 qaytarsa → 30 soniya cooldown
- Barcha proxylar ishlamasa → to'g'ridan-to'g'ri ulanish (fallback)
- Health check 30 soniyada bir marta — tiklanishni avtomatik aniqlaydi

---

## Claude Desktop bilan ulash

`~/.config/claude/claude_desktop_config.json` fayliga qo'shing:

```json
{
  "mcpServers": {
    "instagram": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolut/yo'l/instagram_mcp",
        "run",
        "python",
        "-m",
        "instagram_mcp"
      ],
      "env": {
        "INSTAGRAM_MCP_COOKIES": "/absolut/yo'l/cookies.txt"
      }
    }
  }
}
```

**macOS da yo'l:**
```
/Users/username/.config/claude/claude_desktop_config.json
```

**Windows da yo'l:**
```
C:\Users\username\AppData\Roaming\Claude\claude_desktop_config.json
```

### Claude Code (CLI) bilan ulash

```bash
claude mcp add instagram -- uv --directory /path/to/instagram_mcp run python -m instagram_mcp
```

Yoki `~/.claude/mcp.json` ga qo'lda qo'shing.

---

## Muhit o'zgaruvchilari

| O'zgaruvchi | Default | Tavsif |
|-------------|---------|--------|
| `INSTAGRAM_MCP_COOKIES` | `""` | Cookies fayl yo'li (cookies.txt yoki cookies.json) |
| `INSTAGRAM_MCP_PROXIES` | `""` | Proksi URLlar (vergul bilan) yoki proxies.txt |
| `INSTAGRAM_MCP_TRANSPORT` | `stdio` | `stdio` yoki `http` |
| `INSTAGRAM_MCP_HOST` | `0.0.0.0` | HTTP rejimida server host |
| `INSTAGRAM_MCP_PORT` | `8000` | HTTP rejimida server porti |
| `INSTAGRAM_MCP_APP_ID` | `936619743392459` | Instagram app ID |
| `INSTAGRAM_MCP_IMPERSONATE` | `chrome142` | curl_cffi impersonate target |
| `INSTAGRAM_MCP_TIMEOUT` | `10` | So'rov timeout (sekund) |
| `INSTAGRAM_MCP_MAX_RETRIES` | `3` | Maksimal qayta urinish soni |
| `INSTAGRAM_MCP_MAX_WORKERS` | `12` | Batch operatsiyalar uchun parallellik |
| `INSTAGRAM_MCP_CACHE_DISABLED` | `""` | `1` yoki `true` — cacheni o'chirish |
| `INSTAGRAM_MCP_CACHE_TTL` | `300` | Global cache TTL (sekund) |
| `INSTAGRAM_MCP_CACHE_MAX` | `500` | Maksimal cache yozuvi |
| `INSTAGRAM_MCP_RATE_LIMIT_RPS` | `100.0` | Maksimal so'rov/sekund |
| `INSTAGRAM_MCP_RATE_LIMIT_BURST` | `50` | Burst token hajmi |
| `INSTAGRAM_MCP_RATE_BACKOFF_FACTOR` | `0.7` | 429 da tezlikni kamaytirish koeffitsienti |
| `INSTAGRAM_MCP_RATE_RECOVERY_FACTOR` | `1.15` | Muvaffaqiyatda tezlikni tiklash koeffitsienti |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Circuit ochilishi uchun ketma-ket 429 soni |
| `INSTAGRAM_MCP_CIRCUIT_BREAKER_COOLDOWN` | `60.0` | Circuit ochiq bo'lganda uxlash (sekund) |
| `INSTAGRAM_MCP_PROXY_MAX_FAILS` | `5` | Proxy cooldownga kirishi uchun xato soni |
| `INSTAGRAM_MCP_PROXY_COOLDOWN` | `30` | Proxy cooldown vaqti (sekund) |
| `INSTAGRAM_MCP_PROXY_MAX_COOLDOWN` | `300.0` | Maksimal proxy cooldown (sekund) |
| `INSTAGRAM_MCP_REQUEST_JITTER` | `0.1` | Rate limiter jitter (sekund) |
| `INSTAGRAM_MCP_GRAPHQL_DOC_ID` | `26442143102071041` | Feed pagination doc_id |
| `INSTAGRAM_MCP_MAX_PAGINATION` | `200` | Pagination maksimal post chegarasi |

---

## Arxitektura

```
instagram_mcp/
├── __init__.py         — MCP server factory, lifespan, resources, prompts
├── tools.py            — 13 MCP tool ro'yxatdan o'tkazish
├── client.py           — Barcha Instagram API so'rovlari
├── parser.py           — Raw JSON → strukturali dataclasslar
├── formatter.py        — Markdown output generatori
├── models.py           — Pydantic input modellari + dataclasslar
├── config.py           — Barcha sozlamalar, env var support
├── cache.py            — TTL cache (LRU eviction, async)
├── rate_limiter.py     — Adaptive token-bucket + circuit breaker
├── proxy_manager.py    — Proksi rotatsiya + health check
├── cookie_manager.py   — Cookie yuklash, CSRF token olish
├── exceptions.py       — Typed exception hierarchy
├── agents.py           — Yuqori darajali pipeline agentlar
└── batch_runner.py     — Parallel batch scraping engine
```

### Ma'lumot oqimi

```
MCP Tool (tools.py)
    │
    ├── Pydantic validation (models.py)
    ├── Rate limiter (rate_limiter.py) — token bucket
    │
    ├── Cache check (cache.py) — TTL hit?
    │   ├── HIT  → darhol qaytarish
    │   └── MISS → API so'rov
    │
    ├── Client (client.py)
    │   ├── Proxy tanlash (proxy_manager.py) — round-robin
    │   ├── HTTP so'rov (curl_cffi — Chrome impersonate)
    │   └── Retry (max 3, har biri boshqa proxy)
    │
    ├── Parser (parser.py) — raw JSON → dataclass
    ├── Formatter (formatter.py) — dataclass → Markdown
    └── MCP response (ToolResult)
```

### API endpointlar

| Endpoint | Auth | Tool |
|----------|------|------|
| `GET /api/v1/users/web_profile_info/?username={}` | Yo'q | `instagram_profile`, `instagram_feed_deep`, boshqalar |
| `POST https://www.instagram.com/graphql/query/` | cookies + CSRF | `instagram_tagged_by`, `instagram_reposts`, `instagram_reels` |
| `GET /api/v1/media/{id}/comments/` | Yo'q | `instagram_post_comments` |
| `GET https://www.instagram.com/p/{shortcode}/` | Yo'q | `instagram_post` |

### Cache TTL jadval

| Ma'lumot | TTL |
|----------|-----|
| Profil | 5 daqiqa |
| Feed tags | 2 daqiqa |
| Account status | 10 daqiqa |
| Paginated feed | 3 daqiqa |
| Tagged/reposts/reels | 5 daqiqa |
| Kommentlar | 1 daqiqa |

### GraphQL doc_id lar

| Tool | `fb_api_req_friendly_name` | `doc_id` |
|------|---------------------------|----------|
| `instagram_feed_deep` (anon) | `PolarisProfilePostsTabContentQuery_connection` | `26442143102071041` |
| `instagram_tagged_by` | `PolarisProfileTaggedTabContentQuery_connection` | `26707104818956021` |
| `instagram_reposts` | `PolarisProfileRepostsTabContentRefetchQuery` | `35095888563388407` |
| `instagram_reels` | `PolarisProfileReelsTabContentQuery_connection` | `26292852833730510` |

---

## Fayl strukturasi

```
MCP/
├── instagram_mcp/          — asosiy paket
│   ├── README.md           — shu fayl
│   ├── __init__.py
│   ├── tools.py
│   ├── client.py
│   ├── parser.py
│   ├── formatter.py
│   ├── models.py
│   ├── config.py
│   ├── cache.py
│   ├── rate_limiter.py
│   ├── proxy_manager.py
│   ├── cookie_manager.py
│   ├── exceptions.py
│   ├── agents.py
│   └── batch_runner.py
├── proxies.txt             — proksi URLlar (ixtiyoriy)
├── cookies.txt             — Instagram cookies (ixtiyoriy)
└── pyproject.toml          — paket ta'rifi va dependensiyalar
```

---

## Tez-tez so'raladigan savollar

**Q: Hisob ma'lumotlarim (login/parol) kerakmi?**
A: Yo'q. Anonymous toollar uchun hech narsa kerak emas. Authenticated toollar uchun faqat brauzer cookies yetarli — server login yoki parolni hech qachon ko'rmaydi.

**Q: `play_count` nima uchun boshqa toollarda ko'rinmaydi?**
A: Instagram umumiy feed API sida reellar uchun `view_count=null` qaytaradi. Faqat Reels Tab (`/clips/user/connection/`) endpointi `play_count` ni ochib beradi. Shuning uchun `instagram_reels` alohida tool sifatida mavjud.

**Q: 429 (rate limit) xatosi keldimi?**
A: Proksi sozlang (`proxies.txt` yoki `INSTAGRAM_MCP_PROXIES`). Har bir proksi o'z limitiga ega. Yoki so'rovlar orasida kutib turing — server avtomatik backoff qiladi.

**Q: Batch scraping da natijalar qaerga saqlanadi?**
A: `instagram_batch_scrape` tooli `output_file` parametri berilsa u yerga, aks holda `/tmp/` papkasiga JSON formatda saqlaydi va yo'lini qaytaradi.

**Q: Kommentlar uchun authentication kerakmi?**
A: Yo'q. `instagram_post_comments` to'liq anonim ishlaydi.

---

## Litsenziya

MIT
