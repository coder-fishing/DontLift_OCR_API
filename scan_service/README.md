# Scan API cho DontLift (bản thử local, VND)

Service tắt MKL-DNN để tránh lỗi `ConvertPirAttribute2RuntimeAttribute` đã tái hiện với PaddlePaddle CPU hiện tại. Cấu hình này có thể làm OCR chậm hơn; cần đo lại trước khi deploy.

Chạy từ thư mục `D:\PaddleOCR`, dùng môi trường `.venv` đã cài PaddleOCR.

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\scan_service\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn scan_service.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Mở http://127.0.0.1:8000/docs, chọn **POST /scan → Try it out**, chọn ảnh ở trường `image`, rồi Execute. Server chỉ sẵn sàng sau khi nạp model; lần đầu có thể tải model qua mạng. Model được giữ trong RAM, nhưng lượt suy luận đầu vẫn có thể chậm hơn các lượt sau. Không bật nhiều worker hoặc reload khi đo tốc độ.

Hoặc gọi bằng PowerShell (dùng curl.exe, không phải alias curl):

```powershell
curl.exe -X POST http://127.0.0.1:8000/scan -F "image=@samples/bill.png"
```

`GET /health` trả trạng thái ready/busy. Tối đa một tác vụ xử lý; request đồng thời nhận 503 và Retry-After. Ảnh JPEG/PNG/WEBP tối đa 10 MB, 25 triệu pixel, được thu nhỏ còn tối đa 2200 pixel mỗi chiều. Không lưu ảnh/JSON OCR xuống ổ đĩa bởi code service; upload multipart có thể được framework đệm vào file tạm.

Response có `amount` khi quy tắc chọn được rõ, `suggested_amount` để người dùng xem lại, `needs_confirmation`, `evidence`, `warnings`, `timings_ms`. Không có nhãn tổng hỗ trợ thì số tiền null. Điểm quy tắc chưa được hiệu chỉnh thành xác suất; mọi số tiền cần người dùng xác nhận. Chưa xử lý tách nhiều hóa đơn trong một ảnh, chưa hỗ trợ ngoại tệ.

## Express gọi service

Express kiểm tra đăng nhập/premium trước, chuyển ảnh bằng multipart field `image` tới `/scan`, nhận JSON rồi trả cho DontLift. Không tự động retry request scan nếu chưa có cơ chế chống xử lý trùng. Chọn timeout dựa trên thời gian OCR đo thực tế.

Bật khóa trước khi cho backend khác truy cập:

```powershell
$env:SCAN_API_KEY = "thay-bang-khoa-ngau-nhien-dai"
.\.venv\Scripts\python.exe -m uvicorn scan_service.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Express gửi header `X-API-Key`. Không đưa khóa này vào app client. Không cấu hình khóa nghĩa là chế độ thử local không xác thực. Khi public cần HTTPS và giới hạn kích thước request tại proxy (kiểm tra 10 MB trong endpoint diễn ra sau bước parse multipart). Đây chưa phải cấu hình deploy Render; môi trường mới cần cài cả PaddleOCR/PaddlePaddle tương thích và đủ RAM. `OCR_LANG` mặc định `vi`, `OCR_DEVICE` mặc định `cpu`.
