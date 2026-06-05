# snapchat-uploader

Скрипт для массового создания **Snapchat SALES** кампаний и загрузки видеокреативов.

Владелец: Stas Alekseenko (stasalex@gmail.com)

---

## Быстрый старт

```bash
cd snapchat-uploader
pip install -r requirements.txt
cp .env.example .env            # вставить SNAP_CLIENT_ID, SNAP_CLIENT_SECRET
python get_token.py             # получить SNAP_REFRESH_TOKEN → запишется в .env автоматически
# положить client_secrets.json (Google OAuth) в корень папки

python run.py mimika_v21                  # загрузить до 10 креативов
python run.py mimika_v21 --limit 3        # загрузить 3 (для теста)
python run.py mimika_v21 --dry-run        # проверить конфиг без загрузки
```

---

## Аутентификация Snapchat

OAuth2 Authorization Code flow:

1. Зарегистрировать OAuth App в **Snap Business Manager** → Business Details → OAuth Apps
   - Указать `redirect_uri` (любой HTTPS URL, которым владеешь — например лендинг)
2. Запустить `python get_token.py` — откроет браузер, попросит вставить URL после редиректа
3. `SNAP_REFRESH_TOKEN` запишется в `.env` автоматически

Токен доступа живёт **60 минут** — обновляется автоматически через refresh_token. Refresh token не истекает.

---

## Как работает

1. Читает базовые имена из `creatives/{funnel}.txt`
2. Из `state/{funnel}/uploaded.json` фильтрует уже загруженные
3. Берёт до `batch_size` (10) файлов
4. Ищет файлы на Drive по токенам названия: `CRTV-154-1_MIDEF_ED` → запрос `title contains 'CRTV-154-1' and title contains 'MIDEF' and title contains 'ED'` → фильтр 9x16 или без маркера
5. Создаёт **CBO кампанию** с бюджетом на уровне кампании (Smart Budget)
6. Создаёт **2 Ad Squad** — первые ⌈N/2⌉ файлов в AS1, остальные в AS2
7. Для каждого файла: скачивает → `upload_media` → поллит до READY → `create_creative` → `create_ad`
8. Сохраняет в state, удаляет локальный файл сразу после загрузки

---

## Структура проекта

```
snapchat-uploader/
├── run.py                  ← точка входа
├── get_token.py            ← получение OAuth refresh_token
├── requirements.txt
├── .env.example
├── configs/                ← один yaml = одна воронка
│   └── mimika_v21.yaml
├── creatives/              ← списки базовых имён (одно на строку)
│   └── mimika_v21.txt
├── state/                  ← не в git
│   └── {funnel}/
│       ├── uploaded.json   ← {basename: {ad_id, campaign_id, ...}}
│       └── last_run.log
└── core/
    ├── auth.py             ← OAuth2: получение/обновление access_token
    ├── config.py           ← FunnelConfig + загрузчик yaml
    ├── api.py              ← Snapchat API v1: media, creative, campaign, ad_squad, ad
    ├── gdrive.py           ← Google Drive: поиск по токенам названия файла, скачивание
    └── uploader.py         ← главный цикл
```

---

## Конфиг воронки (YAML)

| Поле | Описание |
|------|----------|
| `ad_account_id` | ID рекламного аккаунта Snapchat |
| `pixel_id` | UUID пикселя (из Snap Ads Manager → Pixels) |
| `profile_id` | UUID публичного профиля (из URL в профиле: `/profiles/{id}/`) |
| `gdrive_root_folder_id` | ID корневой папки Drive с креативами |
| `campaign_name_template` | Шаблон имени кампании (дата `_DDMMYY_HHMMSS` добавляется автоматически) |
| `ad_url` | URL лендинга (поддерживаются макросы `{{campaign.name}}`, `{{adSet.name}}` и др.) |
| `headline` | Текст под брендом (до 34 символов) |
| `brand_name` | Название бренда на объявлении |
| `call_to_action` | Кнопка (например `MORE`, `SHOP_NOW`, `SIGN_UP`) |
| `campaign_budget_usd` | Бюджет кампании $/день (CBO Smart Budget, распределяется между адсетами автоматически) |
| `target_cost_usd` | Bid cap за конверсию (USD) |
| `optimization_goal` | Событие пикселя: `PIXEL_PURCHASE`, `PIXEL_SIGNUP` и др. |
| `countries` | ISO коды стран (lowercase). Несколько примеров закомментированы в YAML |
| `smart_targeting` | Авторасширение аудитории (`SMART_TARGETING`) |
| `languages` | ISO коды языков: `en`, `de`, `fr` и др. |
| `min_age` | Минимальный возраст (18 — требование DSA для EU/UK) |
| `batch_size` | Файлов за один прогон (макс 10, делятся на 2 адсета) |

---

## Snapchat API v1 — важные детали

- Base URL: `https://adsapi.snapchat.com/v1/`
- Auth: `Authorization: Bearer {token}`
- Бюджет: micro-currency ($1 = 1 000 000)
- CBO: `pacing_level: CAMPAIGN` + `shared_properties` обязательны при budget на уровне кампании
- `shared_ad_squad_bid_strategy`: только `AUTOBID` или `LOWEST_COST_WITH_MAX_BID` (не `TARGET_COST`)
- Media upload: 2 шага — POST создание + POST загрузка файла → poll до `media_status: READY`
- Creative type: `WEB_VIEW` (не `SNAP_AD`)
- Ad type: `REMOTE_WEBPAGE` (не `SNAP_AD` и не `WEB_VIEW`)
- Demographics: `min_age`, `languages` и `operation: INCLUDE` — в ОДНОМ объекте
- Гео: **обязательно** хотя бы одна страна в CBO кампании
- Язык English: `id = "en"`
- EU/UK DSA: `PIXEL_PURCHASE` + EU/UK страны требуют `min_age >= 18`
- Поддерживаемые URL-макросы: `{{campaign.name}}`, `{{campaign.id}}`, `{{adSet.name}}`, `{{adSet.id}}`, `{{creative.name}}`, `{{site_source_name}}`
- `profile_id`: берётся из URL профиля: `https://profile.snapchat.com/{org_id}/profiles/{profile_id}/`

### Статусы объектов

| Объект | Статус | Почему |
|--------|--------|--------|
| Кампания | PAUSED | Включается вручную после проверки |
| Ad Squad | ACTIVE | Кампания PAUSED — показов нет |
| Объявление | ACTIVE | Кампания PAUSED — показов нет |

---

## Аккаунты

| Аккаунт | ad_account_id |
|---------|---------------|
| CM MENTALGROWTH LTD | `e8efd7df-56a7-494d-9ff0-5e190dd98ef6` |

Пиксель Face Yoga: `73936160-1977-47e5-80cf-e32974be3fef`
Profile ID: `f8eec728-3cc7-42be-a514-eeb81fe7c5a6`

---

## Добавить новую воронку

```bash
cp configs/mimika_v21.yaml configs/new_funnel.yaml
# Заменить: name, pixel_id, profile_id, gdrive_root_folder_id,
#           campaign_name_template, ad_url, headline, brand_name

touch creatives/new_funnel.txt
# Добавить базовые имена файлов (одно на строку)

python run.py new_funnel --dry-run
python run.py new_funnel
```
