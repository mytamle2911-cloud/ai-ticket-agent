# Suggested Contact Time — sửa tìm ngày liên hệ tiếp theo

## Nguyên nhân và Git history

Đã đối chiếu `suggest_contact_time()` trong `agent/customer_communication.py` tại `ac2fa65` (checkpoint trước triển khai), `a295056` (chuẩn bị Vercel), `c3f3d1e` (Knowledge Governance). AST của hàm giống nhau ở cả ba commit, SHA-256 rút gọn `c57d19e8a04f119e`. Không tìm thấy thay đổi logic scheduler do Knowledge Governance.

Logic cũ chỉ duyệt khoảng từ 00:00 đến 24:00 của một ngày được chọn theo múi giờ consultant; mặc định là hôm nay. Nếu không còn đủ slot 30 phút, trả `No future 30-minute slot remains...` ngay, không thử ngày sau. Với giờ L2 09:00–18:00 tại Việt Nam, từ 17:30:01 trở đi không còn slot đầy đủ trong ngày. Hiện tượng trước đây có giờ và sau đó không có giờ phù hợp với giới hạn phụ thuộc thời điểm chạy này; không phải bằng chứng DST bị tính sai. Logic cũ cũng chưa bỏ cuối tuần.

## Thay đổi

- Backend tìm slot tương lai đầu tiên trong ngày được chọn và các ngày làm việc sau đó. Ngày chọn đã qua được giữ ở `selected_date` để tương thích, nhưng bắt đầu tìm từ hôm nay tại Việt Nam; ngày slot thực tế nằm trong `suggested_slot`.
- Giữ giờ làm việc trong JSON, slot đúng 30 phút và căn giờ bắt đầu vào phút 00/30 theo giờ consultant. Kiểm tra toàn bộ slot nằm trong giờ làm việc của cả hai bên.
- Dùng UTC để duyệt các thời điểm thực và IANA `ZoneInfo` để chuyển `Asia/Ho_Chi_Minh` / `Europe/Berlin`; không hardcode giờ hoặc offset mùa hè/mùa đông.
- Chỉ dùng thứ Hai–thứ Sáu khi không có cấu hình `working_days`. Nếu được cung cấp, mỗi bên có thể cấu hình danh sách weekday 0=Monday đến 6=Sunday. Cả ngày địa phương của customer lẫn consultant đều được kiểm tra.
- Phạm vi tìm mặc định 14 **ngày lịch**, tính cả ngày bắt đầu. Có thể truyền `search_horizon_days` hoặc tùy chọn `consultant.contact_search_horizon_days` (1–366). Không sửa JSON cấu hình hiện có. Nếu hết phạm vi mà không có slot hợp lệ, trả `available: false`, không bịa lịch và nêu phạm vi đã tìm.
- Response giữ trường cũ, bổ sung ngày/giờ kết thúc và UTC offset tương ứng cho từng bên. UI giữ class/styling cũ, hiện thứ/ngày, khoảng giờ và nhãn Việt Nam/Munich cùng offset.
- Backend GET customer communication và POST email draft dùng cùng scheduler. Email nhận slot có sẵn làm context; LLM không quyết định giờ hiển thị trên UI. Vẫn ghi rõ đây là đề xuất, không phải lịch đã đặt.

Ví dụ được tái hiện trong test với clock cố định: thứ Sáu 17/07/2026 lúc 17:31 ở Việt Nam → thứ Hai 20/07/2026, 14:00–14:30 Việt Nam (UTC+07:00), 09:00–09:30 Munich (UTC+02:00). Đây là output tính từ clock/config test, không phải giờ hardcode trong scheduler.

## File thay đổi trong phần sửa scheduler

- `agent/customer_communication.py`: tìm nhiều ngày, lịch làm việc/horizon, kiểm tra slot đầy đủ, dữ liệu end time/date/offset.
- `frontend/src/customerCommunication.js`: formatter cho ngày địa phương, time range và nhãn múi giờ, không phụ thuộc browser timezone.
- `frontend/src/App.jsx`: hiển thị hai khoảng giờ/ngày từ backend trong card cũ.
- `test/test_customer_communication.py`: cập nhật expectation cũ khi hết giờ hôm nay; thêm 12 ca regression scheduling và route/email integration.
- `frontend/src/customerCommunication.test.js`: thêm 3 ca date/time/offset/host-timezone.
- `docs/contact_time_fix/`: báo cáo, logs và checksum.

Các thay đổi Knowledge Governance/exception handling chưa commit từ yêu cầu trước vẫn được giữ nguyên; không ghi đè chúng.

## Kiểm thử thực tế

```sh
.venv/bin/python test/run_offline.py
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

| Kiểm tra | Đạt | Thất bại/lỗi | Bỏ qua |
|---|---:|---:|---:|
| Backend unittest, toàn bộ suite hiện tại | 186 | 0 | 0 |
| Frontend Node tests, toàn bộ suite | 27 | 0 | 0 |
| Vite production build | Thành công | 0 | — |
| git diff --check | Sạch | 0 | — |

Coverage mới: overlap còn trong hôm nay; slot cuối đủ 30 phút và cutoff sau một giây; hết giờ hôm nay → ngày mai; thứ Sáu/cuối tuần → thứ Hai; chuyển DST Đức tháng 3/tháng 10; không overlap và hết horizon; horizon cấu hình; clock server UTC/Los Angeles khác ngày Việt Nam; selected date quá khứ/tương lai; các ngày địa phương khác nhau; email mock và route nhận slot backend giống nhau; closing time có giây; UI hiển thị đúng hai ngày/offset bất kể host Honolulu/Tokyo/UTC.

Backend chạy trong bản sao tạm, chặn socket ngoài và mock AI. `isolation.json` xác nhận 28 JSON gốc không đổi. Không gọi GPT thật, không commit, push hoặc deploy.

## Giới hạn

Chưa tích hợp ngày nghỉ lễ hoặc lịch bận của từng người vì không có dữ liệu/cấu hình đó. Giờ làm việc qua đêm vẫn bị từ chối như logic cũ. Không có booking/calendar mutation. Chưa chạy UAT trình duyệt thủ công; frontend được xác minh bằng test formatter và production build, backend bằng unit/integration tests.
