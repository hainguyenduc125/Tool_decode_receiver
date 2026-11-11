# LoRa Console & Decoder

Ứng dụng desktop Windows để xem dữ liệu UART từ mạch thu LoRa và giải mã bản
tin theo parser trong `data_process.cpp`. Ứng dụng hiện chỉ có console LoRa và
kết quả giải mã; không chạy gateway counter, MQTT hay so sánh các nguồn.

## Chạy ứng dụng

```powershell
cd "C:\Users\admin\OneDrive\Desktop\Tool giải mã Lora\Tool_decode_receiver"
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

- Gateway Type0: UID 8 byte, data `18 + 4×N` byte hoặc Info 32 byte; CRC-16/Modbus
  little-endian. Data heartbeat (`SEQ=0`) vẫn được phân loại theo layout Data.
- Type3 TempCameraI2C đang dùng UID 12 byte: camera 26 byte và MCU Info 22 byte,
  checksum XOR-fold. Tool chọn layout bằng cả Modbus, XOR và kiểm tra ngữ nghĩa
  giống Gateway Type0, kể cả khi độ dài hai họ frame trùng nhau.
- Layout Type3 legacy (không có `TYPE`, Info 28 byte) chỉ hiển thị tham khảo;
  kết quả nêu rõ Gateway Type0 từ chối bằng `LENGTH_ERROR`.
- Với frame có CRC hợp lệ nhưng sai số kênh/exponent, tool hiển thị lỗi
  `INVALID_FORMAT`, tương ứng kết quả Gateway.
- UID không theo format nhà máy được báo `INVALID_UUID`; Gateway có thể bỏ qua
  kiểm tra này nếu UID đã được cấu hình trong whitelist (`packet.force`).

Record `L:` có thể là envelope hoặc payload đã đệm, không nhất thiết là raw
frame mà `parse_node_packet()` xử lý.

## Đóng gói Windows `.exe`

Cần Python Windows và kết nối Internet ở lần cài PyInstaller đầu tiên:

```powershell
.\build_exe.cmd
```

File chạy độc lập được tạo tại `dist\LoRaDecoder.exe`. Người dùng cuối không
cần cài Python; bản đóng gói không bao gồm cấu hình người dùng hoặc log.

## Kiểm thử

```powershell
python -m unittest tests.test_lora_frame tests.test_lora_parser
```
