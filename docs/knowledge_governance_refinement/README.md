# Đơn giản hóa Knowledge Governance và popup xử lý ngoại lệ

Các thay đổi triển khai trên bản code hiện có; không commit, push hoặc deploy. Không thêm dependency, database hay framework; không gọi paid AI API. Giữ nguyên workflow điều tra, xác nhận resolution, tạo email, tạo Draft và reviewer phê duyệt knowledge.

## Giao diện và luồng duplicate

Đã bỏ dropdown lifecycle, nút lưu lifecycle, nhãn version/lifecycle và các phần xem Version History khỏi Knowledge Article UI. Dữ liệu `version`, `version_history`, `lifecycle_status` và safeguards backend vẫn được giữ; không xóa bài hoặc lịch sử.

Khi tạo bài mới, backend chuẩn hóa `source_system` và `issue_category`, đối chiếu bài active trong kho knowledge, loại chính bài đang xử lý khỏi so sánh. Không đòi title giống nhau. Bài active Draft cũng được tính là potential duplicate; bài expired/superseded bị loại.

Nếu trùng scope, trả HTTP 409 với mã `DUPLICATE_KNOWLEDGE` và tham chiếu bài. Frontend hiện modal **Similar Knowledge Article Found**, chứa title, source/category, source ticket và **View existing article**. **Cancel** đóng modal và không gửi request lưu; **Continue Anyway** gửi lại candidate đã tạo với `review_acknowledged: true`. Nhánh xác nhận không gọi GPT lần thứ hai. Backend tự kiểm tra cả request trực tiếp; không có xác nhận boolean true thì không lưu. Không tự gộp, xóa hay ghi đè.

Mở, đổi title/symptoms hay phê duyệt bài cũ không hiện lại cảnh báo duplicate. Source/category không cho sửa trong workflow hiện có; request PATCH các trường này vẫn bị từ chối. Conflict checks trên nội dung vẫn được giữ. Request lặp lại cùng source resolution trả lại bài đã tồn tại thay vì tạo thêm bài.

## Contract và popup

Giữ `error` dạng chuỗi để tương thích các client và test trước đây; thêm cấu trúc `error_info` thay vì thay kiểu của trường cũ:

```json
{
  "success": false,
  "error": "Could not persist the article update.",
  "error_info": {
    "code": "SAVE_FAILED",
    "title": "Changes Could Not Be Saved",
    "message": "Could not persist the article update.",
    "details": {},
    "retryable": true,
    "blocking": true,
    "next_action": "Your edits remain in the form. Check the current record before retrying."
  }
}
```

Cảnh báo điều tra an toàn trả HTTP 200, `success: true`, `notices: [...]`; vẫn trả investigation và câu hỏi bổ sung. Không tự coi mọi warning là lỗi vận hành.

| Mã | Phát hiện / phản hồi | Popup / hành động |
|---|---|---|
| `DUPLICATE_KNOWLEDGE` | Cùng source/category active; HTTP 409, cần xác nhận | View existing article, Cancel, Continue Anyway |
| `AI_API_FAILURE` | Timeout, connection/HTTP failure, model response rỗng/sai định dạng/incomplete/failed; HTTP 503 | Giải thích ngắn; Retry cho request đã đăng ký callback; Close |
| `MISSING_DOCUMENTATION` | Không có tài liệu active với nội dung điều tra hữu dụng hay captured knowledge phù hợp | Warning; yêu cầu tài liệu/L2, không đề xuất resolution không có căn cứ |
| `INCOMPLETE_TICKET` | Thiếu source, category/issue hoặc short_description/description theo alias schema hiện tại | Danh sách tên trường thực tế còn thiếu và câu hỏi bổ sung; Close |
| `KNOWLEDGE_CONFLICT` | Chỉ dẫn đối nghịch rõ ràng cùng scope/điều kiện | Hai tham chiếu và trích dẫn; Review Sources mở phần nội dung nguồn đã truy xuất trong popup |
| `SAVE_FAILED` | Lỗi persistence; HTTP 500 | Không thông báo thành công; giữ nội dung form; Retry phù hợp và Close |
| `REQUEST_FAILED` | Validation/operational error khác hoặc không xác nhận được response | Thông báo ngắn; Close và Retry khi phù hợp |

Không bắt buộc error code/comment, priority, impact hay urgency mới để điều tra: các trường này không phải điều kiện bắt buộc của luồng retrieval hiện tại. Validation dùng alias thực tế: `source_system/system`, `category/issue_type/issue`, `short_description/description/summary`; trả tên key đang thiếu trong payload/schema.

API adapter giữ nguyên envelope signed browser state của Vercel; normalize kết quả inner route. AI failure không trả success dù có fallback: fallback vẫn được giữ cùng nhãn `deterministic_fallback` và lỗi 503, không trình bày là Live GPT thành công. Backend không gửi stack trace hoặc thông báo disk exception gốc; popup transport error cũng không hiển thị raw exception. Request Retry là thao tác thủ công, chạy lại workflow và cập nhật kết quả, không tự retry.

## File thay đổi

- `agent/api_errors.py` (mới): định nghĩa mã, title, action, details, retryable/blocking và chuyển warning.
- `agent/knowledge_governance.py`: duplicate theo active source/category, metadata nguồn đầy đủ, có tùy chọn bỏ duplicate check khi sửa bài cũ.
- `agent/investigation_validation.py`: required fields theo schema/alias, bỏ mandatory comment; kiểm tra tài liệu có nội dung hữu dụng.
- `mock_api/server.py`: response adapter tương thích API cũ; duplicate gates khi tạo và tránh cảnh báo lại khi sửa; sanitize lỗi browser-state/storage.
- `frontend/src/apiFeedback.js` (mới): ánh xạ lỗi/warning, không bật popup cho thành công thường, lưu callback Retry.
- `frontend/src/FeedbackDialog.jsx` (mới): hai popup dùng styling modal hiện có, view bài cũ/review nguồn/cancel/continue/retry/close.
- `frontend/src/App.jsx`: bỏ lifecycle/history UI, kết nối popup và callback vào investigation/chat/email/resolution/knowledge; giữ fallback provenance và edits khi lỗi.
- `frontend/src/demoApi.js`: ưu tiên structured error message; giữ contract signed state.
- `test/test_knowledge_governance.py`: thêm/cập nhật kiểm thử business flow và error contract.
- `test/test_demo_state.py`: regression browser-state qua cảnh báo + explicit continue mới.
- `frontend/test/apiFeedback.test.js`, `frontend/test/feedbackDialog.test.js` (mới): popup rendering/actions, Retry, warning/success, bảo toàn nội dung khi lỗi.
- Thư mục báo cáo này: logs và checksum; không thay báo cáo kiểm thử lần triển khai trước.

## Kiểm thử thực tế

```sh
.venv/bin/python test/run_offline.py
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

| Kiểm tra | Đạt | Thất bại/lỗi | Bỏ qua |
|---|---:|---:|---:|
| Backend unittest | 174 | 0 | 0 |
| Frontend Node tests | 24 | 0 | 0 |
| Vite build | Thành công | 0 | — |

Backend chạy trong bản sao ứng dụng tạm, `.env` rỗng và socket bên ngoài bị chặn. `isolation.json` xác nhận checksum của 28 JSON gốc trước/sau giống nhau. Frontend render component JSX thực và gọi action callbacks; Retry test kiểm tra URL/body của request thực sự được gửi lại, chỉ sau thao tác retry.

Lần chạy trung gian có 3 assertion cũ không còn đúng với yêu cầu mới: tạo bài browser-state nay cần duplicate confirmation; sửa/phê duyệt bài cũ không được cảnh báo duplicate; conflict warning có thể đi kèm duplicate warning. Đã cập nhật các assertion theo hành vi mới, không bỏ qua test; toàn bộ kết quả cuối ở trên.

Logs: `backend_tests.txt`, `backend_results.json`, `frontend_tests.txt`, `frontend_build.txt`, `isolation.json`.

## Giới hạn

- Conflict detection chỉ nhận một số lệnh đối nghịch rõ ràng bằng tiếng Anh; không phải kiểm tra semantic/đa ngôn ngữ toàn diện. Scope trùng chỉ là potential duplicate, không chứng minh nội dung giống nhau.
- Chưa chạy UAT đầy đủ bằng trình duyệt hoặc GPT thật. Frontend được kiểm tra bằng unit tests, render JSX và callbacks, cộng production build.
- Unsaved form content được giữ trong phiên đang mở khi lỗi; không cam kết tồn tại sau reload/đóng tab.
- Metadata/version/lifecycle API backend vẫn còn để tương thích và bảo vệ retrieval, nhưng không còn điều khiển thủ công trên UI.
- JSON persistence giữ giới hạn prototype và khóa cùng process; không bổ sung transaction giữa nhiều worker. Khi timeout sau save, cần xem record hiện tại trước khi Retry. Idempotency bài dựa trên source resolution như workflow cũ.
