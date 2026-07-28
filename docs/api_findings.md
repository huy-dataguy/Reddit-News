# Reddit API — Ghi chú reverse-engineering

Tổng hợp những gì đã kiểm chứng thực tế (ngày 2026-07-07) khi vọc Reddit.

## 1. Bức tranh tổng thể các "cửa" vào dữ liệu Reddit

| Cửa | Ví dụ | Trạng thái thực tế | Ghi chú |
|-----|-------|--------------------|---------|
| `.json` public | `reddit.com/r/x/hot.json` | ❌ **403 + trang challenge** | Bị chặn theo IP/UA (datacenter). Đây chính là `js_challenge` bạn thấy trong URL. |
| `old.reddit.com/.json` | `old.reddit.com/r/x/hot.json` | ❌ 403 | Cùng lớp bảo vệ. |
| `oauth.reddit.com` **không token** | `oauth.reddit.com/r/x/hot` | ❌ 403 | Bắt buộc phải có Bearer token. |
| **`oauth.reddit.com` + token ẩn danh** | ✅ | ✅ **200 JSON sạch** | **Đây là đường đi chính của tool này.** |
| `gateway.reddit.com/desktopapi` | API nội bộ của web app | ⚠️ 301 redirect | Còn tồn tại nhưng cần header/cookie web; kém ổn định hơn OAuth. |
| GraphQL (`gql.reddit.com`) | API app di động | ⚠️ | Mạnh nhưng schema hay đổi, cần token; chưa cần tới ở giai đoạn này. |

**Kết luận:** không cần "hack" gì phức tạp. Đường sạch nhất, ổn định nhất, đúng cấu
trúc chính thức là **lấy OAuth token ẩn danh rồi gọi `oauth.reddit.com`**.

## 2. Lấy token ẩn danh (installed_client) — mấu chốt

Reddit cho phép client cài đặt (app chính thức) lấy token **không cần đăng nhập,
không cần client-secret**, chỉ cần một `device_id` ngẫu nhiên:

```
POST https://www.reddit.com/api/v1/access_token
Authorization: Basic base64("<client_id>:")      # mật khẩu rỗng
Content-Type: application/x-www-form-urlencoded

grant_type=https://oauth.reddit.com/grants/installed_client
device_id=<uuid ngẫu nhiên>
```

Trả về:
```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400, "scope": "*" }
```

Token sống ~24h. Sau đó gọi API:
```
GET https://oauth.reddit.com/r/technology/hot?limit=100&raw_json=1
Authorization: Bearer eyJ...
User-Agent: <UA mô tả rõ ràng>
```

> ⚠️ **ToS & production:** dùng public client-id ẩn danh tiện cho việc thử nghiệm,
> nhưng để chạy dài hạn/đúng luật, hãy đăng ký app riêng tại
> <https://www.reddit.com/prefs/apps> (loại *installed* hoặc *script*). Bạn sẽ có
> client-id riêng, hạn mức 100 QPM ổn định, và tuân thủ **Reddit Data API Terms**.

## 3. Các endpoint đã kiểm chứng (đều 200)

| Mục đích | Endpoint |
|----------|----------|
| Listing post | `/r/{sub}/{hot,new,top,rising,controversial}?t=&limit=100&after=` |
| Post + cây comment | `/r/{sub}/comments/{id}?sort=top&depth=N&limit=500` |
| Bung comment ẩn | `POST /api/morechildren` (`link_id`, `children`=csv id, tối đa 100/lần) |
| Thông tin user | `/user/{name}/about` |
| Metadata subreddit | `/r/{sub}/about` |
| Tìm subreddit | `/subreddits/search?q=` |
| Search post trong sub | `/r/{sub}/search?q=&sort=top&t=year&restrict_sr=1` |

`raw_json=1` để Reddit **không** HTML-escape (`&amp;`, `&lt;`) trong text.

## 4. Cấu trúc dữ liệu Reddit ("thing")

Mọi đối tượng có một `kind` (fullname prefix) + `data`:

| kind | prefix | là gì |
|------|--------|-------|
| `t1` | `t1_` | comment |
| `t2` | `t2_` | user (account) |
| `t3` | `t3_` | post (link/submission) |
| `t5` | `t5_` | subreddit |
| `more`| — | nút "xem thêm N comment" (chứa danh sách id để gọi `morechildren`) |

Listing bọc trong `{ "kind":"Listing", "data":{ "children":[...], "after":"t3_..", "before":null } }`.
`after` là con trỏ phân trang.

Response của `/comments/{id}` là **mảng 2 phần tử**: `[0]` = Listing chứa post,
`[1]` = Listing chứa cây comment (mỗi comment có `replies` lồng nhau, hoặc node `more`).

## 5. Rate limit (đo thực tế)

Mỗi response có header:
```
x-ratelimit-remaining: 94.0     # còn lại trong cửa sổ
x-ratelimit-used: 6
x-ratelimit-reset: 368          # giây tới khi reset
```
- App đăng ký chính thức (OAuth): **100 request/phút**.
- Token ẩn danh: hạn mức thấp hơn và dễ bị siết — khi crawl thread lớn (bung
  `morechildren` nhiều lần) sẽ chạm trần nhanh. Tool tự phát hiện và ngủ chờ reset.

## 6. Vấn đề DỮ LIỆU LỊCH SỬ (rất quan trọng cho mục tiêu "lấy 3 tháng / 1 năm")

**Giới hạn cứng:** mỗi listing chỉ trả về **tối đa ~1000 item**, dù phân trang.
Nghĩa là **KHÔNG thể** duyệt ngược toàn bộ lịch sử một sub lớn chỉ bằng
`/new` hay `/top`. Đây là giới hạn của Reddit, không phải của tool.

Chiến lược thực tế:

1. **Đi tới (hiện tại → tương lai):** chạy crawler định kỳ (cron) trên `/new`,
   lưu tăng dần (incremental). Đây là cách bền vững nhất để "sở hữu" dữ liệu.
2. **Lách một phần bằng search theo cửa sổ thời gian:** chia nhỏ query
   (`/r/{sub}/search?q=...&sort=new&t=...`) theo từ khóa/khoảng ngày để vượt mốc 1000.
3. **Backfill lịch sử — dùng nguồn dump ngoài** (đây mới là cách lấy 1 năm về trước):
   - **Arctic Shift** — kế thừa tinh thần Pushshift: có API + dump hàng tháng.
     <https://github.com/ArthurHeitmann/arctic_shift>
   - **PullPush.io** — API kiểu Pushshift, full-text search lịch sử. <https://pullpush.io>
   - **Academic Torrents** — dump submissions + comments theo tháng (rất lớn, phù hợp
     nạp một lần rồi merge với dữ liệu crawl mới).

> Khuyến nghị: **crawl OAuth cho dữ liệu mới (mục 1)** + **nạp dump Arctic Shift cho
> lịch sử (mục 3)**, rồi hợp nhất vào cùng star schema. Đừng cố cào ngược 1 năm qua
> API chính thức — bất khả thi vì trần 1000.
