# Knowledge governance và exception handling — kết quả triển khai

## Phạm vi

Giữ nguyên Flask, React/Vite, dữ liệu JSON và giao diện/workflow hiện có. Không thêm dependency, database hoặc agent. Không gọi API AI; không commit, push hoặc deploy. Các JSON gốc, bao gồm 25 ticket và KB, không thay đổi (checksum trước/sau trong `isolation.json`).

Các khả năng đã có và được giữ lại: xác nhận rõ ràng trước cập nhật trạng thái; đối chiếu trạng thái hiện tại để chống cập nhật cũ; chỉ tạo knowledge từ resolution được consultant xác nhận; Draft → phê duyệt bởi reviewer → Approved; chống tạo lại bài cùng resolution; truy xuất theo nguồn/category; lỗi AI có provenance fallback riêng.

## Tính năng

| Yêu cầu | Triển khai | Phạm vi / giới hạn |
|---|---|---|
| KG01 | Hoàn thành | Chuẩn hóa title/source/category; HTTP 409 cảnh báo và tham chiếu bài hiện có; xem bài trong modal hiện có; tiếp tục chỉ sau thao tác review. Không xóa/gộp. |
| KG02 | Hoàn thành ở mức kiểm tra nhẹ | Nhận diện enable/disable, grant/revoke và một số lệnh phủ định có cùng đối tượng/điều kiện; hiển thị hai nguồn và trích dẫn. Khác resolution hoặc khác điều kiện không tự động là xung đột. Không phải bộ kiểm tra ngữ nghĩa toàn diện. |
| KG03 | Hoàn thành | Bắt đầu v1, mỗi thay đổi thực tế tăng version và giữ snapshot trước đó; expected_version để phát hiện bản chỉnh sửa cũ; không cắt history âm thầm khi đạt giới hạn 100 snapshot. |
| KG04 | Hoàn thành | active/expired/superseded; bài cũ thiếu metadata mặc định active/v1; chỉ active + Approved được truy xuất. GET bài/lịch sử vẫn cho phép review; history không đi vào retrieval/prompt. Product docs cũng áp dụng lọc active. |
| EX01 | Hoàn thành trong các pipeline hiện có | Timeout/lỗi/response incomplete hoặc failed không được đánh dấu live GPT thành công; chat dùng fallback có nhãn và lỗi; email/knowledge draft lỗi không được lưu. Retry investigation/chat là thao tác thủ công; fetch giới hạn 60 giây. |
| EX02 | Hoàn thành | Không có tài liệu active phù hợp → cảnh báo, HUMAN_SUPPORT, yêu cầu L2/bổ sung bằng chứng; không sinh resolution kỹ thuật không có căn cứ. |
| EX03 | Hoàn thành | Kiểm tra source, issue type/category, mô tả và error/symptoms. Thiếu thông tin → chỉ rõ trường và câu hỏi; không kết luận cause/resolution; nhánh UI và prefill cũng bị chặn. |
| EX04 | Hoàn thành trong phạm vi KG02 | Knowledge được truy xuất có chỉ dẫn đối nghịch → cảnh báo + trích dẫn, giữ dữ liệu bằng chứng cho review, chặn đề xuất kết luận và gọi GPT. Cần L2 xử lý nguồn xung đột trước khi điều tra lại. |
| EX05 | Hoàn thành với giới hạn prototype | Lỗi ghi trả lỗi, không toast thành công; form React giữ nội dung khi request thất bại. JSON được ghi qua file tạm + atomic replace; dữ liệu demo-state được copy trước chỉnh sửa. |

Lỗi/thiếu bằng chứng dùng `deterministic_guardrail`, không gắn nhãn là phản hồi GPT. Status update và consultant confirmation không được bỏ qua. Bài Approved vẫn không chỉnh sửa nội dung trực tiếp; có thể thay đổi lifecycle, còn sửa nội dung và approval tiếp tục theo workflow Draft hiện có.

## Các file thay đổi

- `agent/knowledge_governance.py` (mới): chuẩn hóa, duplicate/conflict, version và metadata validation.
- `agent/investigation_validation.py` (mới): kiểm tra bằng chứng tối thiểu, cảnh báo và chặn kết luận thiếu căn cứ.
- `agent/llm_agent.py`: active retrieval, áp dụng kiểm tra sau scenario guidance, từ chối model status thất bại/incomplete.
- `agent/chat_agent.py`: phản hồi guardrail, kiểm tra model status, loại history khỏi context.
- `agent/captured_knowledge.py`: kiểm tra active và loại version history khỏi retrieval.
- `agent/knowledge_capture.py`: bỏ KB inactive khỏi seed; từ chối draft incomplete/failed.
- `agent/customer_communication.py`: từ chối email draft incomplete/failed.
- `agent/demo_state.py`: tương thích metadata mới và state cũ.
- `mock_api/server.py`: review gates, article GET, version/lifecycle PATCH, atomic JSON write, khóa ghi trong cùng process, lọc retrieval.
- `frontend/src/App.jsx`: cảnh báo/review/xem nguồn, metadata/history/lifecycle trong modal cũ, retry và evidence guard.
- `frontend/src/resolutionPrefill.js`: không điền sẵn cause/resolution khi bằng chứng bị chặn.
- `test/test_knowledge_governance.py` (mới): 20 ca tập trung cho governance và exception.
- `test/test_agent.py`, `test/test_knowledge_reuse.py`: sửa fixture fake client để patch `_get_client`, độc lập API key thực; không nới kiểm tra xác thực production.
- `frontend/test/resolutionPrefill.test.js`: regression cho prefill khi có cảnh báo.
- `test/run_offline.py` (mới): chạy toàn bộ Python tests trên bản sao tạm, `.env` rỗng, loại biến cấu hình AI/deployment và chặn socket bên ngoài.
- Thư mục báo cáo này: logs, kết quả và checksum.

## Kiểm thử thực tế

Các lệnh cuối đã chạy từ repository root:

```sh
.venv/bin/python test/run_offline.py
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

| Kiểm tra | Đạt | Thất bại/lỗi | Bỏ qua |
|---|---:|---:|---:|
| Python unittest: 146 regression + 20 governance/exception | 166 | 0 | 0 |
| Node test: 16 regression + 1 prefill guard | 17 | 0 | 0 |
| Vite production build | Thành công | 0 | — |
| git diff --check | Sạch | 0 | — |

Các ca mới kiểm tra: duplicate + review trước tạo; chỉ dẫn đối nghịch + trích dẫn; tránh false conflict khác điều kiện/nguồn; version/history/stale write; loại inactive và giữ GET/history; save failure/atomic replace failure; thiếu tài liệu/thông tin/source; conflict trong native investigation/chat; timeout/connection/incomplete/failed; reject generation incomplete; approval không tự động; browser state compatibility; không đưa version history vào AI.

Một lần chạy trung gian có 1 lỗi trong test mới do truyền `investigation` bằng positional argument cho hàm email nhận keyword-only. Đã sửa fixture, chạy lại toàn bộ; kết quả cuối ở trên. Các lần chạy thành công trước đó không thay thế cho kết quả cuối.

Logs: `python_execution.txt`, `python_results.json`, `frontend_tests.txt`, `frontend_build.txt`. `isolation.json` lưu checksum trước/sau của tất cả JSON gốc; kết quả `original_json_unchanged: true`. Không gọi paid API; runner chặn network sockets và toàn bộ client AI trong test liên quan đều được mock.

## Giới hạn cần ghi nhận

- Conflict detection chỉ là kiểm tra rule-based tiếng Anh có chủ đích; chưa bao phủ mâu thuẫn ngữ nghĩa, đa ngôn ngữ hoặc điều kiện phức tạp. L2 vẫn phải đọc nguồn; hệ thống không chọn bài đúng.
- Chưa chạy UAT trình duyệt thủ công và chưa gọi GPT thật. Frontend có unit test và build verification, không phải bằng chứng hoàn tất tương tác trình duyệt.
- Unsaved edits được giữ trong state của form đang mở khi lỗi request; không cam kết tồn tại sau refresh/đóng trang.
- Lịch sử tối đa 100 snapshot/bài; đạt giới hạn sẽ từ chối thay đổi và yêu cầu export/review thay vì xóa lịch sử.
- Khóa ghi bảo vệ cùng process; JSON/local storage không thay thế database transaction giữa nhiều worker. Browser demo state giữ cơ chế signed snapshot hiện có.
- Timeout phía trình duyệt có thể xảy ra sau khi server đã nhận request. Cần xem lại trạng thái bài/version trước khi thử lưu lại; ứng dụng không tự retry hay thông báo lưu thành công khi chưa có xác nhận.
