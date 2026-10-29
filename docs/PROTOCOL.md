# Протокол Mac ↔ iPhone v1

## Адреса

`http://MAC_LAN_IP:8765` только в защищённой локальной сети. Парное приложение должно запускаться на телефоне, Mac — в сети. Сервер не является публичным облачным endpoint.

### GET /api/health

Проверка доступности: `{ "service": "PELENG 3D", "protocol": 1, "status": "running" }`.

### POST /api/pair

`Content-Type: application/json`; body:

```json
{"pin":"123456","device_id":"persisted-install-uuid","camera_mode":"back-wide-landscape-640x480-v1"}
```

Ответ `200`: `{"token":"random_session_token","protocol":1,"frame_size":[640,480]}`. Реальный PIN выдаётся на экране Mac; цифры выше — **только описание JSON**, не действующий код. За неверный PIN `403`, при лимите попыток `429`, за попытку другого iPhone занять активное соединение `409`. Токен обновляется при новой паре/перезапуске Mac. В текущем протоколе нет TLS; см. `docs/ARCHITECTURE.md`.

### GET /api/time

Запрос с заголовком `X-Session-Token: TOKEN` возвращает `{"t_receive": 12345.0, "t_send":12345.0001}` — оба времени monotonic Python, секунды. На iPhone t0 и t3 измеряются той же функцией, что и timestamp frame. Offset = ((t1−t0)+(t2−t3))/2; uncertainty = RTT/2. Сначала выбираются лучшие образцы, затем новый запрос каждые четыре секунды.

### POST /api/frame

`Content-Type: image/jpeg`, body — **полный JPEG кадра 640×480**, не base64, максимум 600kB. Заголовки:

- `X-Session-Token` — случайный token после pairing.
- `X-Device-Id` — тот же persistent UUID.
- `X-Camera-Mode` — тот же camera mode.
- `X-Frame-Id` — строго возрастающее целое, сбрасывается при новом pairing.
- `X-Capture-Mono` — iPhone `ProcessInfo.systemUptime`, секунды, момент delegate-callback.
- `X-Clock-Offset` — Mac monotonic − iPhone monotonic, секунды.
- `X-Clock-Uncertainty` — >=0, половина текущего RTT, секунды.

`200 {"received":true}` подтверждает доставку кадра **не 3D-измерение**. Пропущенный по времени кадр всё равно виден в превью iPhone, но не входит в 3D. `401` неверный token, `409` чужой UUID/mode или старый Frame-Id, `413` слишком большой JPEG, `422` неверное изображение/размер.

## Dashboard, только loopback

- `GET /api/dashboard`: все текущие состояния/реальные метрики, IP и PIN (только Mac).
- `GET /api/preview/mac`, `GET /api/preview/iphone`: JPG с реальными цветными маркерами поверх кадра; 204 если нет кадра.
- `POST /api/calibration/capture {}`: найти шахматную доску на свежей синхронной паре и сохранить.
- `POST /api/calibration/run {"square_m":0.025}`: вычислить калибровку, вернуть матрицы и RMS (или 400 с объяснением).
- `POST /api/calibration/reset {}`: очистить калибровочные пары без удаления последнего профиля.
- `POST /api/marker {"lower":[35,80,70],"upper":[90,255,255]}`: HSV пороги.
- `POST /api/record/start {}` и `POST /api/record/stop {}`: начать/завершить запись реальных точек.
- `GET /api/history` и `GET /api/history/FILE.csv`: посмотреть и скачать сохранённые записи.

Единицы: координаты и `baseline_m` — **метры**; `rms`, `reprojection_error_px` — **пиксели**; `sync_error_ms`, `clock_uncertainty_ms` — **миллисекунды**; временные метки — секунды монотонных часов Mac, не Unix UTC.
