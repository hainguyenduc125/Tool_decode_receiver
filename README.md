# Gateway LoRa Message Counter

Tool desktop (Python + PyQt5) độc lập dùng trong **factory test / kiểm tra đường
truyền** LoRa:

```
LoRa Node → LoRa Receiver (Type1) → Gateway (ESP32-S3) → MQTT Broker → Server
```

Tool đọc **Serial (USB gateway)** để đếm packet LoRa gateway nhận, đồng thời
**subscribe MQTT** để đếm message server nhận của **đúng gateway đang test**, rồi
so sánh: Missing / Delivery rate / Duplicate → PASS / FAIL.

> **Sơ đồ đấu nối (quan trọng — tránh hiểu nhầm):**
>
> ```
> LoRa Node ──LoRa P2P──► LoRa Receiver (Type1) ──UART (trên board)──► Gateway (ESP32-S3)
>                                                                        │
>                                                       USB CDC (COM)    ▼
>                                                                     Laptop (tool này)
> ```
>
> - **LoRa Receiver KHÔNG cắm vào laptop.** Nó gắn **trực tiếp lên Gateway qua
>   UART** (chân `LORA_RX` 38 / `LORA_TX` 39, xem `src/main.h`), do Gateway quản
>   lý và reset (`LORA_RST` GPIO 3).
> - **Laptop nối với Gateway** bằng **USB serial (COM)**, tốc độ 115200 8N1.
>   Gateway in toàn bộ log (gồm cả log packet LoRa nhận được) ra COM này.
> - Vì vậy tool gọi Serial port là **Gateway (COM)** — nó đọc **log của Gateway**,
>   không phải "đọc thẳng receiver". Mọi sự kiện `[LORA] ...` trên COM đều là do
>   Gateway log lại khi nó nhận từ Receiver qua UART.

> Tool KHÔNG sửa firmware, KHÔNG tạo API/format mới, KHÔNG fake dữ liệu. Mọi
> format lấy từ source firmware gateway (`src/` trong repo).

---

## 1. Cài đặt & chạy

```powershell
cd tools\gateway_message_counter
python -m pip install -r requirements.txt
python main.py
# hoặc
.\run.ps1
```

Requirements: `PyQt5`, `pyserial`, `paho-mqtt`, `requests`.

---

## 2. Cách dùng nhanh (bench)

1. Nối **USB gateway** vào PC (Serial = USB CDC 115200), gateway nối mạng
   (Ethernet/WiFi) tới broker, đã chạy publish kiểu `node/<uuid>/data`.
2. **Chọn COM thủ công**: bấm `Detect COM` (hoặc gõ tên cổng trực tiếp vào ô COM,
   dùng `serial.tools.list_ports` — có fallback đọc registry Windows nếu pyserial
   trả rỗng), chọn cổng gateway rồi `Connect`. Tool đọc log Serial của gateway qua
   COM này để đếm Rx.
3. **Gateway UUID** tự động điền (không cần nhập tay):
   - **Cách nhanh nhất:** bấm **Reboot GW** → gateway boot lại và in block
     **DEVICE INFO** (UUID / Model / Series / MAC) ngay trên Serial → tool bắt
     block này và tự điền UUID + S/N + MAC + Model (nhãn nguồn `serial boot`),
     **không cần chờ IP/HTTP hay bản tin [INFO] 60s**.
   - **HTTP fallback:** nếu lỡ block DEVICE INFO (COM bận / connect trễ), tool bắt
     dòng log IP (`[ OK ] WIFI/ETHERNET CONNECTED ...`) rồi gọi
     `GET http://<ip>/api/status` → điền các ô còn thiếu (nhãn nguồn
     `HTTP sau reboot`). Tool poll lại mỗi 2 s trong ~30 s để bù trường hợp lỡ IP.
   - **Tự động thụ động:** bản tin `[INFO]` 60s trên Serial (chỉ in khi MQTT đã
     kết nối) cũng được trích tự động để điền UUID (nhãn nguồn `[INFO] 60s`).
   - **Thứ tự ưu tiên nguồn:** serial boot > HTTP > [INFO] 60s — nguồn nào lấy
     được UUID trước sẽ được giữ nhãn; các nguồn sau chỉ bổ sung ô còn thiếu.
   - **Receiver UID:** dòng `[LORA] MODULE INFO` khi module LoRa reset (sau reboot)
     tự điền **Receiver UID** kèm `FW / FREQ / BW / TXP` — không phải chờ packet.
   - Nếu vẫn trống, nhập tay 16 hex vào ô **Gateway UUID** (dùng để lọc
     `gateway_id` khi đếm MQTT).
4. Tab **MQTT**: nhập host/port/user/pass/topic (đã nạp từ `config.json`), bấm
   **Connect MQTT**. Khi có UUID, ô Filter hiện `ACTIVE (uuid=...)`.
5. Nhập **Target messages** (vd 1000) / **duration**, bấm **START TEST**.
   - Nếu đã chọn COM cho **mạch thu riêng (COM 2)** mà chưa bấm Connect, tool **tự
     kết nối COM 2 ngay khi START** để đối chiếu song song gateway ↔ mạch thu riêng.
   - Nếu chưa chọn COM 2, test vẫn chạy bình thường nhưng khối **SO SÁNH NGUỒN
     THU** sẽ là **N/A** (không đo được nguồn đối chiếu độc lập).
6. Khi đủ target (hoặc hết giờ) tool chờ **settle** 5 s cho MQTT về kịp rồi tự
   chấm **PASS/FAIL** và ghi kết quả ra `logs/<session>/`.

> **Lưu ý về mô hình radio:**
> - **UUID gateway** = `device_uuid` (efuse MAC hex hoa, thường 11 ký tự — vd MAC
>   `04:F9:19:9E:13:9C` → UUID `4F9199E139C`) xuất hiện trong block boot
>   **DEVICE INFO** trên Serial, trong bản tin `[INFO]` 60s và ở `GET /api/status`.
>   Tool tự lấy từ serial boot (nhanh nhất) hoặc HTTP / `[INFO]` (fallback); ô
>   UUID vẫn để sửa tay nếu cần.
> - Các data frame của node trên MQTT mang trường `gateway_id` = UUID của gateway
>   phát bản tin đó, và `receiver_id` = UID mạch LoRa gắn trên gateway. Vì **nhiều
>   gateway có thể cùng nghe 1 node**, cùng một data frame sẽ xuất hiện với nhiều
>   `gateway_id` khác nhau trên cùng topic. Cờ `filter_by_gateway_uuid` dùng UUID
>   test để chỉ đếm data đúng của gateway đang test.
> - Mục tiêu so khớp chỉ dựa trên cặp `(node, seq)` giữa Rx (Serial) và MQTT tương
>   ứng; **không** cần match theo `receiver_id` (UID mạch LoRa).

---

## 2b. UI: tìm 1 node & đối chiếu 3 nguồn (search bar)

Cửa sổ chính chia **cột cấu hình trái (cuộn được)** + **vùng chính bên phải**
(tabbed — thoáng hơn khi dùng lâu). Từ tab **Message Log** có **2 ô tìm kiếm**:

- **Tìm node (UID)** — gõ **UID node** (một phần cũng được, vd `4F9199E139C`) →
  Message Log lọc chỉ còn bản tin của node đó, đồng thời bảng **So sánh node
  (3 nguồn)** (tab riêng) ráp từng `Seq` thành 3 cụm cột: **LORA** (gateway serial)
  | **COM2** (mạch thu riêng) | **MQTT** (server) — mỗi cụm có Time / RSSI-SNR /
  dấu **✓**. Đây chính là cách đối chiếu bản tin sau khi giải mã: **cùng
  (UID node, Seq)** mà gateway LoRa có / mạch thu riêng (COM 2) có / MQTT có —
  thiếu ở nguồn nào thấy ngay.
- **Tìm gateway (UID)** *(bên dưới ô node)* — gõ **UID gateway** (một phần cũng
  được) để **lọc bản tin MQTT do đúng gateway đó bắn** khi có **nhiều gateway
  cùng đẩy bản tin** lên broker nhưng ở khoảng cách khác nhau. Tool lọc theo
  `gateway_uuid` trên từng dòng (lấy từ `gateway_id` trong payload MQTT); dòng
  không gắn gateway (vd COM2, payload hệ cũ) luôn giữ lại để không phá đối chiếu
  3 nguồn. Hai ô áp dụng **đồng thời** (AND): hiện dòng khớp cả node lẫn gateway.
- Bỏ trống cả 2 ô → về chế độ hiển thị tất cả.
- Node mới (UID 16 hex, MQTT mang 24 hex do server lắp đuôi `FC8B3004`) vẫn tìm
  được vì tool so cả 2 dạng (UID tự nhiên + đã bóc suffix 8 hex) — cùng quy ước
  với khối đối chiếu nguồn (COM2 ↔ gateway) bên dưới.

### Lọc bản tin rác (CRC lỗi / unparsed)

Checkbox **“Lọc bản tin rác (CRC lỗi / unparsed)”** (dòng 3 của search bar,
nhớ trong `config.json` → `ui.filter_garbage`, mặc định `true`):

- Bỏ khỏi **Message Log + file CSV** mọi frame LoRa **KHÔNG hợp lệ**: `CRC_ERROR`,
  `UNPARSED`, `INCOMPLETE` — cả ở nguồn **gateway (LORA)** lẫn **mạch thu riêng
  (COM2)**, vì cả 2 cùng đi qua `SerialLogParser` nên kind giống nhau.
- Các frame này **vẫn được đếm riêng** ở tab Counters (`rx_crc_error`) để theo dõi
  chất lượng đường RF — chỉ lọc **hiển thị/đối chiếu**, không “giấu” lỗi.
- Tắt checkbox → hiện lại tất cả (tiện debug nhiễu).

> Đối chiếu 2 nguồn **luôn** lọc rác bất kể checkbox: cả `add_second_source`
> (COM2) và `add_gateway_event` (gateway) chỉ nhận frame `DATA` + `crc_ok` +
> `seq > 0`. Nhờ vậy 1 frame CRC sai (vẫn đọc được uid/seq) **không** sinh `extra`
> giả làm sai verdict.

Bảng **So sánh mạch thu riêng (COM 2) ↔ Gateway (live)** nằm ngay dòng trạng thái
node trên panel chính: per-node số data frame COM2 nhận, gateway nhận, Missing và
% khớp — cập nhật live trong lúc START TEST và giữ nguyên tới khi CLEAR (thay cho
chỉ xem qua popup). Nút **So sánh nguồn (COM2 ↔ GW)** (cột trái) vẫn mở popup chi
tiết + verdict PASS/FAIL theo ngưỡng `pass.*`.

---

## 3. Ý nghĩa con số

- **LoRa Receiver (Rx)** = số **data frame** (seq > 0, CRC OK) gateway log trên
  Serial — mỗi packet = 1 block log `[LORA] RECEIVER ID: ...` + `[LORA] RSSI/...`,
  không phải "1 dòng = 1 packet".
- **MQTT Server** = số message topic `node/<uuid>/data` của **gateway đang test**
  (lọc theo `gateway_id` trong payload). Info frame (seq=0), error frame, retained,
  message của gateway khác → KHÔNG tính, hiển thị ở dòng diag.
- **Missing** = số KEY `(node, seq)` receiver nhận nhưng MQTT không thấy.
- **Extension** đối chiếu theo **KEY `(UID node, seq)`** — vì `seq` là DUY NHẤT
  trên mỗi UID node nên 1 key = 1 bản tin logic. Các metric matched/missing/extra
  và **Delivery rate** đều tính theo **SỐ KEY**, không theo số lần nhận.
- **Matched** = số KEY `(node, seq)` có ở cả 2 nguồn.
- **Extra (MQTT)** = số KEY chỉ có trên MQTT (re-publish/echo) — không tính missing.
- **Delivery rate** = matched_keys / rx_keys × 100 (rx_keys = 0 → N/A). Nhờ mẫu số
  là số KEY nên tỷ lệ **không thể vượt 100%** dù có echo/repeat.
- **Duplicate** = `(node, seq)` xuất hiện > 1 lần (MQTT: re-publish/replay; RX: echo).

Ví dụ: Rx 1000 key, MQTT 998 key có 998 key trùng → Missing 2 → FAIL
(theo `pass.max_missing = 0`).

> ⚠️ Trước đây delivery rate tính `MQTT / Rx × 100` theo **số bản tin**, nên khi
> gateway nhận 1 seq nhiều lần (echo/repeat) tỷ lệ có thể vượt 100% (vd 292%).
> Nay đã chuyển sang tính theo KEY để phản ánh đúng tỷ lệ **bản tin logic**.

---

## 4. config.json

| Nhóm | Ý nghĩa |
|---|---|
| `mqtt.host/port/user/pass` | Broker (mặc định 45.117.170.179:1885) |
| `mqtt.subscribe_topics` | Topic subscribe — mặc định `node/+/data`, `node/+/log` (topic xem bản tin node theo vận hành) |
| `mqtt.filter_by_gateway_uuid` | `true`: chỉ đếm message có `gateway_id` == UUID test |
| `mqtt.count_retained` | `false`: bỏ qua retained (tránh đếm lại message cũ) |
| `serial.baudrate` | 115200 (USB CDC gateway) |
| `gateway_http` | bật/tắt + timeout gọi `/api/status` |
| `test.*` | target mặc định, duration mặc định (giây), settle (giây) |
| `pass.max_missing` | số missing tối đa được chấp nhận (0 = nghiêm ngặt) |
| `pass.min_delivery_rate_percent` | delivery rate tối thiểu (100.0) |
| `ui.*` | giới hạn dòng raw log trên UI, số dòng bảng message, `filter_garbage` (lọc rác CRC lỗi/unparsed khỏi Message Log + CSV) |

PASS ⇔ `missing ≤ max_missing` **và** `delivery_rate ≥ min_delivery_rate_percent`
**và** không có `extra` trên MQTT (extra = key chỉ có ở MQTT).
Nếu cần dung sai 1 message trong 1000 → sửa `pass` trong `config.json`
(không hard-code trong code).

---

## 5. Session & export

Mỗi lần `START` tạo `logs/<YYYYMMDD_HHMMSS_<uuid>>/`:

- `uart_raw.log`, `mqtt_raw.log` — log thô đầy đủ trong test.
- `messages.csv` — từng message: `pc_time, source, gateway_uuid, node_uuid,
  sequence, rssi, snr, kind, topic, payload`.
- `summary.json` — kết quả + diagnostics.
- `result.txt` — block TEST RESULT.

Nút **Save Log** / **Export CSV** lưu ra thư mục/chọn file tuỳ ý. Raw log trên
UI giới hạn (mặc định 5000 dòng) để không lag — dữ liệu đầy đủ luôn nằm trong
file session.

### Export **kết quả đối chiếu** (nút `Export đối chiếu CSV`)

Xuất **1 file CSV phẳng** gom toàn bộ kết quả đối chiếu của 1 session (hoặc dữ
liệu COM2/gateway đã thu kể cả khi chưa chạy test). File tự mô tả bằng cột
`section`:

| `section` | Nội dung |
|---|---|
| `flow` | Tóm tắt từng luồng — `GATEWAY_MQTT` (radio ↔ MQTT) và `COM2_GATEWAY` (mạch thu riêng ↔ gateway): `result` (PASS/FAIL/N/A), `rx_keys`, `matched`, `missing`, `extra`, `delivery_rate`. Luồng không có dữ liệu sẽ **không** ghi dòng. |
| `node` | Per-node của từng luồng (`flow` phân biệt) — số key, matched, missing, extra, % khớp. |
| `message` | Per-`(UID, seq)`: `in_com2`/`in_gateway`/`in_mqtt` (0/1), thời điểm mỗi nguồn, RSSI-SNR, và `result` — `OK` / `MISSING_GATEWAY` / `EXTRA_GATEWAY` / `MISSING_MQTT` / `EXTRA_MQTT`. |

- UID được **chuẩn hoá về UID gốc 8 byte** (bóc đuôi `FC8B3004` của node mới)
  nên cùng 1 key so khớp được giữa 3 nguồn.
- Chỉ lấy row `DATA` — frame rác (CRC lỗi/unparsed) **không** vào CSV (xem mục 2b).
- File ghi UTF-8 **có BOM** (`utf-8-sig`) để Excel mở đúng tiếng Việt.
- 22 cột cố định (xem `core/compare_export.py :: FIELDNAMES`) — ô không dùng để trống.

---

## 6. Quy ước firmware đã xác thực (đừng "đoán" lại)

| Mục | Giá trị |
|---|---|
| Marker 1 packet nhận | `[LORA]    RECEIVER ID: <16hex> \| TIME: ...` rồi `[LORA]    RSSI: ... \| DATA: <hex>` |
| Topic xem bản tin node | `node/<uuid>/data` (data — ĐẾM) và `node/<uuid>/log` (log/status node — hiển thị + diag, KHÔNG đếm) |
| Topic data hệ mới | `node/<sensor_id>/data` |
| Payload data hệ mới | `gateway_id, receiver_id, sensor_id, rssi, snr, timestamp, seq, mcu_temp, ...` |
| Error frame | `node/<id>/data`, có `error`, **không** có `seq` |
| Info (legacy) | `node/<id>/info` nếu còn xuất hiện — diag, không đếm |
| Gateway UUID | `device_uuid` = efuse MAC hex hoa (16 hex) |
| Data frame Type3 | `[UID8][SEQ2 LE][MCU2][VDD2][CH1][float N×4][CRC16 Modbus 2 LE]`, dài `17+4N` |
| Node cũ | frame 22/26/42 byte, CRC XOR |
| Duplicate | phát hiện qua cặp `(gateway_id?, sensor_id, seq)` |

**Giới hạn firmware (không phải lỗi tool):**
- Hệ cũ verbose (topic `node_send`) KHÔNG có `gateway_id` → tool chỉ đếm theo
  `(UUID, SEQ)` khi operator tắt `filter_by_gateway_uuid` (chế độ COUNT-ONLY).
- Info frame Type3 28 byte hiện bị `parse_node_packet` gateway xem là length
  error → tool cũng xếp "unparsed", không tính.
- MAC không in trên Serial — lấy qua `/api/status` khi link up.
- Giờ gateway (`TIME:`) rỗng tới khi NTP sync; correlation dùng giờ PC.
- Queue LoRa/decoded trong gateway đầy → firmware drop âm thầm (không log) —
  diag sẽ không thấy, cần xem firmware nếu Rx thấp bất thường.

---

## 7. Troubleshooting (đọc dòng diag dưới bảng node)

- `sensor_not_allowed > 0` → node bị whitelist/blacklist gateway chặn (log
  `[FAIL] SENSOR IS NOT ALLOWED`).
- `rx_crc_error > 0` → frame nhận được nhưng CRC sai → không có message MQTT data.
- `mqtt_other_gateway > 0` → broker có message node khác gateway — bị lọc đúng.
- `mqtt_no_uuid > 0` → chưa có UUID test nên MQTT chưa đếm (điền UUID).
- `mqtt_no_gw_id > 0` → topic `node/#` nhưng payload thiếu `gateway_id`.
- `mqtt_retained_ignored > 0` → retained bị bỏ qua (đúng thiết kế).
- `gw_publish_ok` (dòng `[ OK ] MQTT PUBLISHED` trên Serial) < MQTT Server →
  gateway gửi nhưng broker/subscribe không nhận → kiểm tra mạng/topic.

---

## 8. Checklist test thật

1. Gateway chạy firmware publish `node/<uuid>/data` (new_system), nối USB + mạng.
2. Node Type3 cấu hình `send_interval` nhỏ (vd 5–10 s) hoặc kích hoạt cảnh báo để
   phát liên tục; để gần gateway.
3. Tool: Connect Serial → HTTP lấy UUID → Connect MQTT → START target 1000.
4. Kỳ vọng PASS: Rx == MQTT, missing 0, dup 0.
5. Chèn lỗi để kiểm tra bắt lỗi: che RF (Rx giảm), rút mạng MQTT giữa chừng
   (missing/dup tăng), bật gateway khác cùng broker (filter chặn `other_gw`).

## 9. Dev / test nhanh

```powershell
python -m unittest discover -s tests -t .   # parser/CRC/matcher — không cần hardware
```

Smoke test end-to-end (không hardware) được dùng lúc phát triển: nạp log serial +
MQTT giả lập vào `MainWindow` offscreen và kiểm tra kết quả PASS/FAIL.
