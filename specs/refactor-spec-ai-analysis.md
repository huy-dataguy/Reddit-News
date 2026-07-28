# Reddit Radar — Spec Refactor: AI Analysis, Prompt & UX

> Mục tiêu: nâng chất lượng đầu ra AI (post analysis, digest, longread) và cách
> hiển thị lên đúng "cảm giác" mà bạn thấy ưng ý ở các bản tóm tắt Reddit trong
> hội thoại này — tức là: đọc 2 phút hiểu hết, có tri thức dùng ngay, có tài
> nguyên đã lọc/kiểm chứng, có cảnh báo rủi ro, không lan man, không PR giả.

---

## 1. Chẩn đoán vấn đề hiện tại

Đối chiếu `PostAnalysis` hiện tại (mục 10.1 trong tài liệu gốc) với các bản tóm
tắt bạn thấy ưng ý ở trên, khoảng cách nằm ở đây:

| Vấn đề hiện tại | Pattern đã chứng minh hiệu quả (trong hội thoại) |
|---|---|
| `community_consensus` là 1 đoạn văn dài, dễ sa vào diễn giải chung chung ("cộng đồng đồng thuận rằng...") | Chia nhỏ theo **luận điểm cụ thể**, mỗi luận điểm có bằng chứng (số upvote, ai nói) |
| `suggestions` liệt kê tool nhưng không phân biệt "đã kiểm chứng" vs "chỉ được nhắc tên" | Luôn có cột/nhãn trạng thái: đã xác minh tồn tại / chưa xác minh / có dấu hiệu spam |
| Không có mục cảnh báo/rủi ro tách riêng | Luôn có mục "⚠️ Cảnh báo / lưu ý quan trọng" tách biệt, không trộn vào phần khen |
| `learning_points` chung chung, không phân biệt "áp dụng ngay được" vs "cần thêm ngữ cảnh" | Luôn kết ở dạng **hành động cụ thể**, có thể copy dùng luôn (mẫu prompt, câu lệnh, số liệu) |
| Không có bước phân biệt nguồn tin đáng tin (bài gốc) vs nguồn tự PR/spam trong comment | Có bước lọc: nếu 1 bình luận giống quảng cáo/spam → gắn nhãn rõ, không trộn lẫn vào "giải pháp được đề xuất" |
| Không phân tách rõ "phần OP tự nói" vs "phần rút ra từ toàn bộ thảo luận" | Tách 2 lớp: tóm tắt bài gốc → rồi tổng hợp comment riêng, không hòa lẫn |
| UI hiện hiển thị y nguyên field JSON → cứng, giống form dữ liệu | UI cần trình bày như bài đọc (đọc mượt), không như bảng tra cứu |

**Kết luận cốt lõi**: vấn đề không nằm ở kiến trúc pipeline (đã ổn), mà ở **tầng
prompt + schema + cách hiển thị**. Đây là 3 thứ cần refactor, không cần đụng vào
crawl/storage/analytics.

---

## 2. Nguyên tắc thiết kế mới (áp dụng cho mọi prompt AI trong hệ thống)

Đúc kết từ các bản phân tích bạn thấy hiệu quả trong hội thoại này:

1. **Tách 3 lớp rõ ràng, không hòa vào nhau**:
   - Lớp 1: Tóm tắt bài gốc (OP nói gì) — khách quan, ngắn.
   - Lớp 2: Tổng hợp từ cộng đồng (comment) — theo luận điểm, có bằng chứng.
   - Lớp 3: Nhận xét/đánh giá tổng hợp — góc nhìn phân tích, được tách biệt và
     gắn nhãn rõ là "nhận xét", không lẫn vào sự thật khách quan.

2. **Không có câu nói suông**. Mọi câu trong `community_consensus` /
   `learning_points` phải kèm được ít nhất 1 trong: số liệu (upvote/score),
   trích dẫn diễn giải (không quote nguyên văn dài), hoặc link nguồn.

3. **Resource luôn có nhãn tin cậy**. Mọi resource trong `suggestions` /
   `fact_extracted_resource` phải có field `confidence`:
   - `verified` — đã kiểm tra domain/URL còn tồn tại, hợp lệ.
   - `unverified` — chỉ trích từ comment, chưa kiểm tra.
   - `suspicious` — giống spam/self-promo (nhiều dấu hiệu: tài khoản mới, chỉ
     post 1 link, không kèm giải thích, ngôn ngữ quảng cáo).

4. **Luôn có mục cảnh báo tách riêng** (`warnings`), không gộp vào đâu khác.
   Bao gồm: rủi ro kỹ thuật, tranh cãi chưa ngã ngũ, thông tin có thể đã cũ,
   pháp lý/bản quyền nếu có nhắc tới.

5. **Actionability trước, diễn giải sau**. Ưu tiên trình bày phần "làm được gì
   ngay" lên đầu, phần diễn giải bối cảnh/ngữ cảnh xuống dưới hoặc thu gọn
   (collapsible) trên UI.

6. **Không markdown-hoá quá đà trong payload** — text field nên là văn xuôi
   mạch lạc, còn cấu trúc (list, bảng) nằm ở schema (array of objects), để UI
   tự render đẹp thay vì nhúng markdown thô vào JSON.

---

## 3. Schema mới: `PostAnalysisV2`

Thay thế `PostAnalysis` hiện tại. Giữ tương thích ngược bằng cách thêm bảng
mới `ai_post_analysis_v2`, không sửa bảng cũ (an toàn migrate, có thể rollback).

```python
class ResourceItem(BaseModel):
    name: str
    kind: Literal["github_repo", "arxiv_paper", "tech_blog", "tool", "doc", "other"]
    url: str
    description: str              # 1 câu, cụ thể, không PR
    confidence: Literal["verified", "unverified", "suspicious"]
    source_comment_id: str | None
    note: str | None              # vd: "đã kiểm tra repo còn tồn tại, 200+ stars"

class OpinionPoint(BaseModel):
    claim: str                    # luận điểm cụ thể, 1 câu
    evidence: str                 # bằng chứng: "u/xxx, 450 upvote" hoặc trích dẫn diễn giải
    stance: Literal["support", "counter", "caveat"]
    comment_ids: list[str]

class PostAnalysisV2(BaseModel):
    language: Literal["vi"] = "vi"
    source_post_id: str

    # LỚP 1 — Tóm tắt khách quan bài gốc
    topic: str                        # tiêu đề súc tích
    author_summary: str                # 2-3 câu: OP thực sự nói/hỏi/chia sẻ gì
    context: str                       # bối cảnh kỹ thuật cần biết để hiểu bài

    # LỚP 2 — Tổng hợp cộng đồng, theo luận điểm (KHÔNG viết thành 1 đoạn văn dài)
    key_points: list[OpinionPoint]     # 3-6 luận điểm chính, mỗi cái có bằng chứng
    resources: list[ResourceItem]      # tool/repo/link được cộng đồng đề cập

    # LỚP 3 — Actionable + cảnh báo, tách biệt hoàn toàn
    action_items: list[str]            # cụ thể, copy-dùng-ngay được (mẫu prompt, lệnh, số)
    warnings: list[str]                # rủi ro, tranh cãi, thông tin có thể lỗi thời
    open_questions: list[str]          # câu hỏi cộng đồng chưa trả lời được

    # Nhận xét tổng hợp — LUÔN gắn nhãn rõ đây là góc nhìn phân tích, không phải sự thật
    verdict: str                       # 2-4 câu: đánh giá tổng thể, có được không, rủi ro gì

    methodology_note: str
    generated_at: datetime
```

**Điểm khác biệt cốt lõi so với schema cũ**: `community_consensus` (1 blob text)
→ `key_points` (array có cấu trúc, mỗi item tự đứng được). Điều này ép LLM phải
cụ thể hoá thay vì viết văn chung chung, và cho phép UI render từng luận điểm
thành 1 khối riêng — đúng cảm giác "đọc 2 phút hiểu hết" bạn thấy hiệu quả.

---

## 4. Prompt mới: Post Analysis

Thay thế `POST_ANALYSIS_INSTRUCTIONS`. Có annotate lý do từng đoạn để dễ tinh
chỉnh sau này.

```
Bạn là một biên tập viên công nghệ dày dạn, chuyên đọc thảo luận kỹ thuật trên
Reddit và viết lại thành tri thức thực dụng bằng tiếng Việt cho người đang bận,
muốn hiểu nhanh và áp dụng được ngay — không phải để lướt cho vui.

NGUYÊN TẮC BẮT BUỘC:

1. Tách rõ 3 lớp, không trộn lẫn:
   - "author_summary"/"context": CHỈ tóm tắt khách quan bài gốc, không chêm ý kiến.
   - "key_points": CHỈ tổng hợp từ comment, mỗi luận điểm phải có bằng chứng
     (ai nói, bao nhiêu upvote, hoặc trích dẫn diễn giải ngắn). KHÔNG viết
     chung chung kiểu "nhiều người đồng ý rằng...". Nếu chỉ 1-2 người nói,
     ghi rõ đó là ý kiến thiểu số, không thổi phồng thành đồng thuận.
   - "verdict": ĐÂY LÀ NHẬN ĐỊNH PHÂN TÍCH của bạn, phải tách biệt và không
     được lẫn vào 2 phần trên như thể đó là sự thật khách quan.

2. Với mọi resource (tool/repo/link) nhắc tới trong "resources":
   - Đánh giá "confidence": verified (bạn chắc chắn URL/domain hợp lệ và tồn tại)
     / unverified (chỉ trích từ comment, chưa kiểm chứng được) / suspicious
     (có dấu hiệu spam: tài khoản có vẻ mới, chỉ thả link không giải thích,
     giọng văn quảng cáo, hoặc lặp lại y hệt ở nhiều thread khác).
   - KHÔNG bịa URL. Nếu không chắc URL chính xác, để "url": null và ghi rõ
     trong "note".

3. "warnings" là bắt buộc phải điền nếu có BẤT KỲ dấu hiệu nào sau: tranh cãi
   chưa ngã ngũ trong comment, thông tin có thể đã lỗi thời (mốc thời gian, số
   liệu do 1 người tự nói không kiểm chứng), rủi ro bảo mật/pháp lý được nhắc
   tới, cảnh báo từ chính cộng đồng về 1 giải pháp nào đó.

4. "action_items" phải là hành động CỤ THỂ, không phải bài học trừu tượng.
   Sai: "Nên viết test đầy đủ."
   Đúng: "Viết test ngay từ ticket đầu tiên, đừng đợi tới khi > 1000 dòng code."
   Nếu bài viết có mẫu prompt/câu lệnh/cấu hình cụ thể → trích lại (paraphrase,
   không quote nguyên văn dài) để người đọc dùng được ngay.

5. Giọng văn: thẳng, súc tích, không PR, không màu mè. Câu ngắn. Không dùng
   markdown thô trong text field (không **, không #, không bullet trong string
   — cấu trúc để ở schema, không ở text).

6. Nếu bài viết/comment không đủ thông tin cho 1 field nào đó (vd không có
   resource nào được nhắc), trả về mảng rỗng, KHÔNG bịa thêm cho đủ.

INPUT: bài viết gốc (title, selftext, article_body nếu có) + tối đa 120 comment
top-score kèm điểm, độ sâu, tác giả.

OUTPUT: đúng schema PostAnalysisV2, toàn bộ text bằng tiếng Việt tự nhiên.
```

---

## 5. Prompt mới: AI Digest

Giữ nguyên phần "chỉ dùng dữ liệu trong INPUT_SOURCES, không tự thêm sự kiện"
(đây là nguyên tắc chống hallucination đã đúng), nhưng thêm:

```
Thêm vào SYSTEM_INSTRUCTIONS hiện tại:

- Với mỗi "story" trong output, thêm field "confidence_reason": 1 câu giải
  thích VÌ SAO tin này đáng tin ở mức độ đó (vd: "được xác nhận qua nhiều
  nguồn độc lập" / "chỉ có 1 nguồn Reddit, chưa có bài báo chính thức").
- KHÔNG liệt kê tin chỉ vì nó có nhiều upvote nếu nội dung không thực sự có
  thông tin mới — thà digest ngắn còn hơn nhồi tin nhạt để đủ số lượng.
- Mỗi "story" phải trả lời được câu hỏi "so what?" trong field
  "why_it_matters" — không chỉ là mô tả sự kiện mà phải nói tác động cụ thể
  tới người đọc (developer, founder, người dùng cuối...).
```

---

## 6. UX / Dashboard — trình bày như bài đọc, không như bảng dữ liệu

### 6.1 Knowledge Detail (thay thế 4-tab hiện tại)

Thay vì 4 tab rời rạc (Tổng hợp / Quan điểm / Đề xuất / Tranh luận), gộp thành
**1 luồng đọc dọc, theo đúng thứ tự ưu tiên đọc**:

```
┌─────────────────────────────────────────┐
│ [Domain badge]  [Nguồn: r/xxx · 2h ago] │
│                                           │
│ ## {topic}                               │
│                                           │
│ {author_summary}                         │
│                                           │
│ ── Điểm mấu chốt ──                       │
│ • {key_point 1}  [👍 450 · u/xxx]        │
│ • {key_point 2}  [👍 120 · u/yyy]        │
│   ...                                     │
│                                           │
│ ── ⚠️ Cần lưu ý ──  (chỉ hiện nếu có)     │
│ • {warning 1}                             │
│                                           │
│ ── Áp dụng ngay ──                        │
│ ✓ {action_item 1}                         │
│ ✓ {action_item 2}                         │
│                                           │
│ ── Tài nguyên ──                          │
│ [verified ✓] {tool name} → {url}         │
│ [chưa xác minh ⚠] {tool name}            │
│                                           │
│ ── Nhận xét ──  (đóng khung riêng, khác  │
│    màu nền, để phân biệt rõ đây là góc   │
│    nhìn phân tích chứ không phải sự thật) │
│ {verdict}                                 │
│                                           │
│ [Câu hỏi còn bỏ ngỏ] (collapsed, click   │
│  mới mở, vì đây là phần ít giá trị nhất) │
└─────────────────────────────────────────┘
```

**Lý do đổi**: 4 tab buộc người đọc phải tự lắp ghép ý nghĩa; luồng dọc theo
đúng thứ tự ưu tiên (mấu chốt → cảnh báo → hành động → tài nguyên → nhận xét)
là thứ tự đã chứng minh hiệu quả trong các bản tóm tắt bạn thấy ưng ý ở trên.

### 6.2 Knowledge Card (danh sách) — rút gọn, không nhồi field

Card hiện có thể đang cố nhét quá nhiều field. Card chỉ nên có:
- Topic (1 dòng)
- 1 key_point nổi bật nhất (không phải cả đoạn consensus)
- Badge cảnh báo nếu `warnings` không rỗng (icon ⚠ nhỏ, không cần text)
- Badge số resource đã verified

### 6.3 Resource Hub — thêm filter theo confidence

Thêm filter 3 nút: **Đã xác minh / Chưa xác minh / Ẩn nghi ngờ spam** (mặc
định ẩn `suspicious` khỏi feed chính, có toggle "hiện cả nghi ngờ spam").

### 6.4 Digest / Signals sidebar

Thêm `confidence_reason` hiển thị dạng tooltip nhỏ cạnh mỗi story, không chiếm
chỗ chính — giữ digest gọn như hiện tại nhưng cho phép hover để thấy độ tin cậy.

---

## 7. Kế hoạch refactor (ticket hoá, theo tinh thần workflow ở phần trước)

Áp dụng đúng pattern "ticket nhỏ + guardrail + verify thủ công" đã tổng hợp ở
các bài trước trong hội thoại này:

| Ticket | Nội dung | Allowed areas | Không đụng vào |
|---|---|---|---|
| T01 | Thêm bảng `ai_post_analysis_v2` + Pydantic model `PostAnalysisV2` | `reddit_crawler/llm.py`, `schema.sql` | Không sửa bảng `ai_post_analysis` cũ |
| T02 | Viết prompt mới (mục 4), test trên 10 post mẫu, so sánh output cũ/mới | `reddit_crawler/llm.py` | Không đụng crawl/storage |
| T03 | Thêm bước "resource confidence check" (verify URL còn sống — HEAD request hoặc domain check đơn giản) | `reddit_crawler/resources.py` | Không đổi logic extract URL hiện có |
| T04 | Cập nhật prompt digest (mục 5) | `reddit_crawler/llm.py` | Không đổi provider fallback chain |
| T05 | API: thêm endpoint `/api/knowledge/{post_id}` trả `PostAnalysisV2`, giữ endpoint cũ song song | `web/app.py` | Không xoá endpoint cũ (để rollback) |
| T06 | Frontend: component `KnowledgeDetailV2` theo layout mục 6.1 | `web/frontend/src/App.jsx` (tách file riêng nếu được, vd `KnowledgeDetail.jsx`) | Không sửa Signals/Resources/Entities tab |
| T07 | Frontend: rút gọn Knowledge Card theo mục 6.2 | `web/frontend/src/App.jsx` | Không đổi routing |
| T08 | A/B thủ công: chạy song song v1/v2 trên cùng 10-20 post, tự đọc so sánh, quyết định có thay hẳn không | - | - |
| T09 | Nếu đạt: migrate `analyze-top`/`analyze-post` CLI sang dùng v2 mặc định, xoá code v1 sau 2 tuần ổn định | `cli.py`, `reddit_crawler/llm.py` | - |

**Manual verification cho mỗi ticket**: đọc thử output của ít nhất 5 post khác
domain nhau (AI, security, tooling...), tự hỏi 3 câu:
1. Đọc xong có biết ngay "làm gì với thông tin này" không?
2. Có câu nào chung chung, vô nghĩa, có thể xoá mà không mất gì không?
3. Có tool/link nào bị nhắc mà không rõ tin được không?

---

## 8. Rủi ro cần lưu ý khi triển khai

- **Chi phí token tăng**: schema mới yêu cầu LLM suy luận cụ thể hơn (đánh giá
  confidence, tách luận điểm) → có thể tốn output token hơn. Nên test cost
  trên vài chục post trước khi bật đại trà qua cron.
- **Verify resource URL tốn thêm 1 vòng gọi mạng** (HEAD request) — cần
  timeout ngắn + cache kết quả theo domain để tránh crawl chậm lại.
- **Đừng xoá schema/endpoint cũ ngay** — giữ song song ít nhất 1-2 tuần để có
  đường lùi nếu chất lượng v2 không như kỳ vọng trên dữ liệu thật (khác với
  10 post test).
