# LoRa Console & Decoder

Ứng dụng desktop Windows để xem dữ liệu UART từ mạch thu LoRa và giải mã bản
tin theo parser trong `data_process.cpp`. Ứng dụng hiện chỉ có console LoRa và
kết quả giải mã; không chạy gateway counter, MQTT hay so sánh các nguồn.

## Chạy ứng dụng

```powershell
cd "C:\Users\admin\OneDrive\Desktop\Tool giải mã Lora\gateway_message_counter"
python -m pip install -r requirements.txt
python main.py
```

Hoặc chạy `.\run.ps1`. Chọn cổng COM của mạch thu, baudrate (mặc định 115200)
rồi bấm **Kết nối**. Console sẽ hiển thị dữ liệu UART; frame nhận dạng được sẽ
tự xuất hiện ở **Kết quả giải mã**.

Nhập UID vào ô **Lọc UID node** để chỉ hiện kết quả giải mã của node đó. Bộ lọc
áp dụng cho cả bản tin UART trực tiếp và dữ liệu dán để giải mã; để trống ô này
để xem tất cả UID. Console UART thô vẫn ghi lại toàn bộ dữ liệu nhận được.

## Giải mã thủ công

Tab nhập ở dưới cửa sổ nhận một frame HEX, một dòng UART, hoặc nhiều record mỗi
dòng:

- HEX thuần hoặc `+EVT:RXP2P:<rssi>:<snr>:<hex>`.
- `D:` record có 19 trường: tool dựng lại frame 42 byte, kiểm tra CRC XOR và
  giải mã 4 cảm biến theo công thức của firmware.
- `L:` record: hiển thị receiver, type và payload; nếu payload không đúng layout
  frame trong firmware, tool sẽ báo không nhận diện thay vì báo CRC hợp lệ.
- Log gateway có trường `DATA:` cũng có thể dán để giải mã.

## Các layout hỗ trợ

- Type3 firmware hiện tại: UID 8 byte, SEQ, TYPE, MCU temperature/VDD,
  channel và các mẫu float; CRC-16/Modbus little-endian.
- Type3 legacy: layout không có TYPE và Info frame 28 byte.
- Node cũ: frame 22/26/42 byte, gồm MCU info, camera hoặc 4 cảm biến; checksum
  XOR-fold theo firmware.

Lưu ý: Info frame Type3 28 byte được hiển thị để tham khảo; firmware hiện tại
không nhận diện độ dài này. Record `L:` có thể là envelope hoặc payload đã đệm,
không nhất thiết là raw frame mà `parse_node_packet()` xử lý.

## Kiểm thử

```powershell
python -m unittest tests.test_lora_frame tests.test_lora_parser
```
