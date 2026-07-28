# Token ẩn danh vs App đăng ký + Kỹ thuật chống phát hiện

> Ghi lại để đọc lại. Bối cảnh dự án: **cá nhân, phi thương mại**, mục tiêu chính là
> để tự nắm tin nhanh **và để học crawl / reverse engineering**.

## 1. "Ẩn danh" nghĩa là gì (đừng hiểu nhầm)

"Ẩn danh" **không phải** là "không có token". Nó **vẫn là một OAuth token thật** —
loại *userless / installed_client* — chỉ khác:
- không gắn với tài khoản người dùng nào,
- (khi dùng public client-id) không gắn với app đăng ký của bạn.

Vì vậy Reddit xếp nó vào **nhóm hạn mức thấp + theo dõi theo IP**. Đăng ký app riêng
cho bạn **gấp ~10 lần throughput** và ổn định hơn hẳn.

## 2. Bảng so sánh (số đo thực tế + tài liệu xác nhận)

| Tiêu chí | Token ẩn danh (installed_client, public client-id) | App bạn đăng ký |
|---|---|---|
| **Hạn mức** | ~100 req / 10 phút ≈ **10 req/phút** (đốt 5 request thấy `used+remaining=100`, cửa sổ reset ~600s) | **100 req/phút** (~1000 / 10 phút) |
| **Theo dõi** | theo **IP** — nhiều token cùng IP vẫn bị gộp | theo **client-id** của bạn |
| **Ổn định** | dễ bị siết/chặn khi crawl nặng (thread lớn bung `morechildren` là chạm trần ngay) | ổn định |
| **Rủi ro** | vùng xám ToS | hợp lệ nếu được duyệt |

→ Ẩn danh tiện để **vọc/thử nghiệm**; chạy đều thì **đăng ký app** hơn hẳn về hiệu năng.

## 3. Điểm pháp lý (đã điều chỉnh cho bối cảnh cá nhân)

Cuối 2025 + **Responsible Builder Policy (3/2026)**: token OAuth qua form duyệt thủ công;
các hạn chế **thương mại / khai thác dữ liệu / feed vào AI-LLM** cần được chấp thuận.

- Vì dự án của bạn là **cá nhân, phi thương mại, dùng riêng** → phần lớn ràng buộc
  "thương mại/AI" **ít áp lực hơn**.
- Nhưng **vẫn phải**: tôn trọng rate limit, không đập hạ tầng, không phát tán lại
  dữ liệu người dùng, tuân robots.txt/ToS ở mức cơ bản. "Cá nhân" giảm rủi ro pháp lý,
  **không** phải giấy phép làm mọi thứ.

## 4. Repo GitHub liên quan

| Repo | Vì sao đáng xem |
|---|---|
| [RedditWarp](https://redditwarp.readthedocs.io/en/latest/user-guide/the-client.html) | Thư viện Python hỗ trợ đúng `installed_client` grant (userless token) — cách làm "chuẩn". |
| [reddit-archive/reddit — Wiki OAuth2](https://github.com/reddit-archive/reddit/wiki/oauth2) | Nguồn chính thức mô tả grant `installed_client` (base64 client-id + mật khẩu rỗng). |
| [not-an-aardvark/reddit-oauth-helper](https://github.com/not-an-aardvark/reddit-oauth-helper) | Script tạo token nhanh. |
| [proxidize/reddit-scraper](https://github.com/proxidize/reddit-scraper) | Repo về "chống phát hiện": 2 chế độ, xoay proxy, giải captcha. Xem để hiểu, đọc kỹ cảnh báo. |



Repo GitHub liên quan (đúng chủ đề bạn hỏi)

- RedditWarp — thư viện Python hỗ trợ đúng installed_client grant mà tool của mình đang dùng (userless token). Đáng đọc để thấy cách làm "chuẩn".
- reddit-archive/reddit — Wiki OAuth2 — nguồn gốc chính thức mô tả grant installed_client (base64 client-id + mật khẩu rỗng), tức thứ mình reverse-engineer ra.
- not-an-aardvark/reddit-oauth-helper — script tạo token nhanh.
- proxidize/reddit-scraper — đây chính là repo về "chống phát hiện": kiến trúc 2 chế độ, xoay proxy, giải captcha, phát hiện kích thước job. Xem để hiểu bức tranh, nhưng đọc kỹ phần cảnh báo bên dưới.

## 5. "Kỹ thuật chống phát hiện" — hai nhóm

**Nhóm A — vệ sinh crawl hợp lệ (nên làm; tool đã có phần lớn):**
- Đọc header `x-ratelimit-*`, tự ngủ chờ; exponential backoff cho 429/5xx.
- Xoay `device_id` để lấy token mới khi cần; throttle có nhịp (không bắn liên tục).
- User-Agent mô tả rõ ràng, đúng định dạng.
- Cache + tránh gọi lại (tầng raw JSONL đã có).

**Nhóm B — né hệ thống chống bot (proxy pool, giả TLS fingerprint, giải captcha):**
Đây là để "cãi" lớp bảo vệ của Reddit. **Với mục tiêu HỌC**: nên hiểu chúng hoạt động
ra sao (rất giá trị về kiến thức). **Với việc chạy thật**: hạn chế dùng — vi phạm ToS,
dễ bị revoke/ban, không bền. Học để *hiểu phòng thủ*, không để *tấn công dịch vụ*.

## 6. Khuyến nghị theo giai đoạn

1. **Bây giờ (vọc/thử):** token ẩn danh — đủ để hoàn thiện pipeline & star schema.
2. **Tích lũy dữ liệu đều:** đăng ký 1–2 app tại reddit.com/prefs/apps → 100 req/phút.
   Chỉ cần đổi `REDDIT_CLIENT_ID`, không sửa code.
3. **Lịch sử (3 tháng / 1 năm):** đừng cào ngược qua API (trần 1000). Nạp dump
   **Arctic Shift / PullPush** rồi merge vào cùng star schema.
4. **Cần lớn hơn 100 req/phút:** nhiều client-id đăng ký hợp lệ + chờ duyệt, không spoofing.

## Nguồn
- [Reddit Data API Wiki — Reddit Help](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- [Reddit API Limits — Data365](https://data365.co/blog/reddit-api-limits)
- [Reddit API Rate Limits 2026 — PainOnSocial](https://painonsocial.com/blog/reddit-api-rate-limits-guide)
