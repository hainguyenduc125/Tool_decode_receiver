# Ngữ cảnh: Tách 2 luồng đối chiếu bản tin trong gateway_message_counter

> File ghi chú ngữ cảnh (context) — lược sử và trạng thái hiện tại của tool
> `tools/gateway_message_counter`, tập trung vào việc **tách 2 luồng đối chiếu**.
> Cập nhật: 2026-09-08.

## 1. Vai trò tool

Tool desktop (Python + PyQt5) dùng trong **factory test đường truyền LoRa**:

```
LoRa Node ──LoRa P2P──► LoRa Receiver (Type1) ──UART──► Gateway (ESP32-S3) ──► MQTT Broker
                             ▲                                            │
                     COM 2 (mạch thu riêng)                     COM chính (USB CDC) ──► Laptop/tool
```

- Đọc **Serial gateway** (COM chính) để đếm packet LoRa gateway nhận.
- **Subscribe MQTT** để đếm message server nhận của đúng gateway đang test.
- **COM 2** là một mạch thu LoRa riêng cắm thẳng vào PC, dùng làm **nguồn tham
  chiếu độc lập** (nghe cùng node ở cùng thời điểm với gateway).

## 2. Hai luồng đối chiếu (đã tách)

| Luồng | So sánh | Module | Verdict |
|---|---|---|---|
| 1 | Mạch thu riêng (COM 2) ↔ Gateway (log COM chính) | `core/source_comparator.py` + `ui/serial_source_widget.py` | PASS/FAIL/N/A — hiện dialog + lưu vào session |
| 2 | Gateway (log COM chính) ↔ MQTT Server | `core/message_counter.py` + `core/message_matcher.py` + `core/test_result.py` | PASS/FAIL — tự chấm khi kết thúc test |

- Dữ liệu **gateway COM** là nguồn dùng chung cho cả 2 luồng (receiver Rx của
  luồng 2, và phía "gateway" của luồng 1) — nhưng được đưa vào 2 consumer độc
  lập nên không trộn số liệu.
- **MQTT không** đi vào luồng 1; **COM 2 không** đi vào luồng 2.
- So khớp luồng 1 theo cặp `(UID gốc 8 byte, seq)`; luồng 2 theo cặp
  `(node_id chuẩn hoá 24 hex, seq)`. Đuôi chuyển đổi node mới lấy từ config
  `node.new_node_suffix` (mặc định `FC8B3004`).
- Vì `seq` **duy nhất trên mỗi UID node**, mọi metric (matched / missing / extra
  / delivery rate) đều đếm theo **SỐ KEY** cặp đó, không theo số lần nhận
  (tránh tỷ lệ > 100% khi gateway nhận echo/repeat). Duplicate chỉ dùng để phát
  hiện bản tin lặp (re-publish), không cộng vào tỷ lệ.

## 3. Vấn đề phát hiện khi kiểm tra (2026-09-08)

1. **Bug chặn luồng 1**: `QMessageBox` dùng trong `on_compare_sources` nhưng
   thiếu import → bấm "So sánh nguồn" sẽ `NameError`.
2. **Cửa sổ thu không đồng bộ**: COM 2 ghi vào comparator liên tục, trong khi
   gateway chỉ ghi khi `_counting and not _rx_frozen` → mở COM 2 trước START /
   để sau freeze sinh **missing giả**; 2 luồng không cùng mốc thời gian.
3. **START test mới không reset comparator** (chỉ CLEAR reset) → chạy test liên
   tiếp không CLEAR gộp cả số liệu test trước.
4. **Luồng 1 không lưu / không verdict**: kết quả chỉ hiện dialog tay, không
   vào `summary.json`/`result.txt`; data COM 2 không vào CSV; không có
   PASS/FAIL riêng (khác luồng 2 tự chấm + lưu đầy đủ).
5. (Kèm theo) `closeEvent` không stop worker của widget COM 2 → nguy cơ treo khi
   thoát app.

## 4. Các sửa đã áp dụng

### `models/message.py`
- Thêm hằng `SRC_REF = "REF"` (nguồn mạch thu riêng COM 2) bên cạnh
  `SRC_LORA`/`SRC_MQTT`.

### `ui/main_window.py`
- Thêm `QMessageBox` vào import PyQt5 (fix bug #1).
- Thêm `_ref_row()` (dòng CSV/table cho nguồn COM 2, `source=REF`,
  `topic=serial2`) và callback `_on_ref_event()` → đẩy data frame COM 2 vào
  table/CSV giống gateway (không vào `MessageCounter`).
- Lifecycle (fix #2, #3):
  - `on_start_test`: `comparator.reset()` + `src_widget.set_recording(True)`.
  - `_begin_settle` (freeze): `src_widget.set_recording(False)` — cả 2 nguồn
    radio dừng cùng mốc.
  - `on_clear`: `set_recording(False)` + `comparator.reset()` (giữ nguyên như
    trước).
- Luồng 1 có verdict + lưu session (fix #4):
  - `_source_compare_verdict()`: PASS/FAIL/N/A theo ngưỡng `pass` trong config
    (`max_missing`, `min_delivery_rate_percent`). N/A khi COM 2 không thu được
    data frame nào.
  - `_source_compare_section()`: tính + format khối đối chiếu; trả `None` nếu
    không có data frame nguồn nào trong cửa sổ test.
  - `_finalize`: gắn kết quả vào `s.extra["source_compare"]` (ghi `summary.json`)
    và nối khối text vào `result.txt` / tab Kết quả.
  - `on_compare_sources` (dialog): hiện kèm verdict + reason.
- `closeEvent`: stop `src_widget` worker (fix #5).

### `ui/serial_source_widget.py`
- Thêm `on_rx_event` callback + cờ `_recording` + phương thức `set_recording()`.
- `_on_line`: luôn hiển thị UID/packet lên widget; **chỉ thu vào comparator và
  callback khi `_recording`** (trong cửa sổ test).
- Tắt `_recording` khi disconnect / `stop()`.

## 5. Hành vi mong đợi sau fix

- Bấm "So sánh nguồn": dialog có verdict + reason (không còn `NameError`).
- Kết thúc 1 test (target/duration/stop): tab **Kết quả** và `result.txt` có 2
  khối riêng — TEST RESULT (gateway ↔ MQTT) và SO SÁNH NGUỒN THU (COM 2 ↔
  gateway). `summary.json` có thêm `extra.source_compare`.
- Nếu test chạy mà **không kết nối COM 2**: khối đối chiếu nguồn vẫn xuất hiện
  với verdict **N/A** kèm lý do ("Không có data frame nào từ mạch thu riêng")
  — phân biệt rõ "không đo được" với "FAIL".
- Dữ liệu ngoài cửa sổ test (trước START / sau freeze) không còn lọt vào so
  sánh → hết missing giả do lệch mốc thời gian.

## 6. Kiểm tra

- `python -m py_compile ui\main_window.py ui\serial_source_widget.py models\message.py`
- `python -m unittest discover -s tests -t .` → **54 tests OK** (không cần phần
  cứng).
- Cần chạy tay `python main.py` với phần cứng thật để nghiệm thu UI: kết nối COM
  chính + COM 2 + MQTT, START test, xem 2 khối kết quả.
