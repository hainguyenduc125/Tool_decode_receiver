# LoRa Console & Decoder

Ứng dụng desktop Windows để xem dữ liệu UART từ mạch thu LoRa và giải mã bản
tin theo parser trong `data_process.cpp`. Ứng dụng không chạy gateway counter,
MQTT hay so sánh các nguồn.

## Chạy ứng dụng

```powershell
cd "C:\Users\admin\OneDrive\Desktop\Tool giải mã Lora\Tool_decode_receiver"
python -m pip install -r requirements.txt
python main.py
```

Hoặc chạy `.\run.ps1`. Chọn cổng COM của mạch thu, baudrate (mặc định 115200)
rồi bấm **Kết nối**. Cửa sổ chính chia thành ba cột: **Bản tin nhận được**,
**Giải mã thủ công** và **Kết quả trực tiếp**. Tab **Bản tin** liệt kê từng
record UART; tab **Console UART thô** giữ nguyên luồng UART.

Chọn một hàng trong bảng (hoặc dùng phím ↑/↓) để nạp và giải mã ngay record đó.
Trong tab console thô, nhấp vào một dòng nhận dạng được để giải mã dòng ấy.
Kết quả giải mã do thao tác chọn hiển thị ở cột giữa; bản tin live mới nhất
hiển thị riêng ở cột phải. Nếu đang chọn một hàng hoặc đã cuộn lên trong bảng,
bản tin mới không làm thay đổi lựa chọn và vị trí cuộn.

Ô **Lọc UID node** so khớp UID chính xác, không phân biệt hoa thường; với record
`L:`, UID được lấy từ trường **Receiver UID**. Nhóm bộ lọc **SEQ** gồm **Chẵn**,
**Lẻ** và **Không SEQ**, lọc đồng thời bảng, kết quả trực tiếp và thống kê.
Các số cạnh checkbox là số record trong phạm vi UID hiện tại; luôn phải chọn ít
nhất một luồng. Frame không có SEQ, Info, record `L:` và heartbeat (nếu được cấu
hình) thuộc nhóm **Không SEQ**.
Bảng giữ tối đa số dòng trong `config.json` → `ui.table_max_rows` (mặc định
50.000); **Xóa console** xóa cả bảng, console, kết quả và dữ liệu thống kê.

Khi nhập UID hợp lệ, panel **Thống kê node** hiển thị RSSI theo thời gian riêng
cho luồng **Chẵn** và **Lẻ**, histogram RSSI (bin 5 dBm), số liệu từng luồng và
ghép cặp Chẵn–Lẻ trong cửa sổ thời gian có thể chỉnh. Nếu thiếu `pyqtgraph`,
phần số liệu vẫn hoạt động và panel báo rằng biểu đồ không khả dụng. Có thể đặt
lại mốc bằng nút **Đặt lại mốc thống kê**; bản tin tiếp theo trở thành SEQ đầu
tiên của từng luồng.

Tỉ lệ nhận được tính riêng theo UID và parity từ SEQ: mỗi luồng tăng 2 mỗi bản
tin, nên `Tổng (kỳ vọng) = (SEQ mới nhất − SEQ đầu tiên) / 2 + 1` trong từng
đoạn; khi SEQ quay vòng hoặc node reset, các đoạn được cộng riêng. `Đã nhận` là
số SEQ duy nhất đã nhận, `Mất = Tổng − Đã nhận`, và `Tỉ lệ = Đã nhận / Tổng ×
100%`. Gói đến trễ có thể lấp vào SEQ bị mất; gói CRC sai chỉ được đếm riêng.
Nhóm **Không SEQ** chỉ hiển thị số đã nhận, không tính tổng/mất/tỉ lệ.

## Giải mã thủ công

Ô nhập trong cột **Giải mã thủ công** nhận một frame HEX, một dòng UART, hoặc
nhiều record, mỗi dòng một bản tin:

- HEX thuần hoặc `+EVT:RXP2P:<rssi>:<snr>:<hex>`.
- Dòng receiver `[RX] <length> | <rssi> | <snr> | <hex>`; RSSI/SNR dạng thập
  phân được giữ lại khi hiển thị.
- `D:` record có 19 trường: tool dựng lại frame 42 byte, kiểm tra CRC XOR và
  giải mã 4 cảm biến theo công thức của firmware.
- `L:` record: hiển thị receiver, type và payload; nếu payload không đúng layout
  frame trong firmware, tool sẽ báo không nhận diện thay vì báo CRC hợp lệ.
- Log gateway có trường `DATA:` cũng có thể dán để giải mã.

## Các layout hỗ trợ

- Gateway Type0: UID 8 byte, data `18 + 4×N` byte hoặc Info 32 byte; CRC-16/Modbus
  little-endian. TYPE được hiển thị đúng theo byte trong frame; Data heartbeat
  (`SEQ=0`) vẫn được phân loại theo layout Data.
- Type3 TempCameraI2C đang dùng UID 12 byte: camera 26 byte và MCU Info 22 byte,
  checksum XOR-fold. Tool chọn layout bằng cả Modbus, XOR và kiểm tra ngữ nghĩa
  giống Gateway Type0, kể cả khi độ dài hai họ frame trùng nhau.
- Type3 TempCamera 26 byte được nhận diện là `Type3`; Type6 Energy 42 byte được
  nhận diện qua marker sensor 8 (điện áp) / 9 (dòng điện) như Gateway Type0.
  Giá trị Type6 được hiển thị thêm theo pha, kèm đơn vị V/A.
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
python -m unittest discover
```
