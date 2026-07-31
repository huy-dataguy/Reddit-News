import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowLeft, ArrowRight, BarChart3, BookOpen,
  Bot, BrainCircuit, CheckCircle2, ChevronRight, Clock3, Code2,
  Copy, ExternalLink, FileText, Filter, Gauge, Globe2, Layers3, Lightbulb,
  Menu, MessageCircle, Radio, Search, ShieldCheck, Sparkles, Target,
  ThumbsUp, TrendingUp, X, Check
} from 'lucide-react'
import { getJSON, withQuery } from './api'

const PERIODS = [
  ['3h', '3 giờ'], ['day', '24 giờ'], ['week', '7 ngày'],
  ['month', '30 ngày'], ['year', '1 năm'],
]

const DOMAIN_ICONS = {
  ai_models: BrainCircuit, ai_ml: BrainCircuit, developer_tools: Code2,
  devtools: Code2, cybersecurity: ShieldCheck, security: ShieldCheck,
  infrastructure: Layers3, infra: Layers3, science: BookOpen,
  business_policy: Globe2, business: Globe2, other: Radio,
}

const compactNumber = new Intl.NumberFormat('vi-VN', {
  notation: 'compact', maximumFractionDigits: 1,
})
const fullNumber = new Intl.NumberFormat('vi-VN')

function relativeTime(timestamp) {
  if (!timestamp) return 'chưa rõ thời gian'
  const seconds = Math.max(0, Date.now() / 1000 - timestamp)
  if (seconds < 60) return 'vừa xong'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút trước`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} giờ trước`
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} ngày trước`
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'medium' }).format(timestamp * 1000)
}

function dateTime(timestamp) {
  if (!timestamp) return '—'
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Ho_Chi_Minh',
  }).format(timestamp * 1000)
}

function navigate(path) {
  if (window.location.pathname === path) return
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function AppLink({ href, children, className = '', onNavigate, ...props }) {
  return <a href={href} className={className} onClick={event => {
    if (!event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) {
      event.preventDefault()
      navigate(href)
      onNavigate?.()
    }
  }} {...props}>{children}</a>
}

function Logo() {
  return <AppLink href="/" className="logo" aria-label="Reddit Radar · Feed">
    <span className="logo-mark"><Activity size={18} /></span>
    <span><b>Reddit</b> Radar</span>
  </AppLink>
}

function ProviderBadge({ provider, model, isAI, compact = false }) {
  const local = !isAI || (provider || '').toLowerCase().startsWith('local')
  if (!provider || provider === 'none') {
    return <span className="provider-badge pending"><Clock3 size={12} /> Chưa có briefing</span>
  }
  if (local) {
    return <span className="provider-badge local"><FileText size={12} />
      {compact ? 'Local' : 'Trích xuất local · chưa xác minh'}
    </span>
  }
  return <span className="provider-badge ai"><Bot size={12} />
    {provider}{model ? ` · ${model}` : ''}
  </span>
}

function DomainIcon({ domain, size = 16 }) {
  const Icon = DOMAIN_ICONS[domain] || Radio
  return <Icon size={size} />
}

function analysisView(item) {
  const analysis = item?.analysis || {}
  const v2 = item?.analysis_version === 'v2' || Boolean(analysis.author_summary)
  const keyPoints = v2
    ? (analysis.key_points || []).map(point => ({
      text: point.claim, evidence: point.evidence, ids: point.comment_ids || [], stance: point.stance,
    }))
    : (analysis.learning_points || []).map(text => ({ text, ids: [] }))
  return {
    analysis,
    v2,
    title: analysis.topic || item?.title || 'Chưa có tiêu đề',
    summary: analysis.verdict || analysis.community_consensus || analysis.author_summary
      || analysis.author_goal || item?.summary || '',
    context: analysis.context || analysis.problem_context || '',
    author: analysis.author_summary || analysis.author_goal || '',
    keyPoints,
    actions: analysis.action_items || [],
    resources: analysis.resources || analysis.suggestions || [],
    warnings: analysis.warnings || analysis.disagreements || [],
    questions: analysis.open_questions || analysis.unanswered_questions || [],
  }
}

function FeedStoryCard({ item }) {
  const view = analysisView(item)
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    const textToCopy = `📌 ${view.title}\n\n${view.summary || ''}\n\nNguồn: ${item.reddit_url || 'Reddit Radar'}`
    navigator.clipboard.writeText(textToCopy).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return <article className="feed-story-card">
    <div className="card-topline">
      <span className="domain-label">
        <DomainIcon domain={item.domain_id} size={14} />
        {item.domain_name || 'Công nghệ'}
      </span>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />
    </div>

    <h2 className="story-title">
      <AppLink href={`/post/${item.post_id}`}>{view.title}</AppLink>
    </h2>

    {view.summary && <p className="story-summary">{view.summary}</p>}

    {view.keyPoints[0] && (
      <div className="evidence-preview-box">
        <Lightbulb size={15} className="evidence-icon" />
        <span><b>Ghi nhận:</b> {view.keyPoints[0].text}</span>
      </div>
    )}

    <div className="story-actions-bar">
      <div className="story-meta">
        <span className="sub-tag">r/{item.subreddit || '?'}</span>
        <span><ThumbsUp size={13} /> {compactNumber.format(item.score || 0)}</span>
        <span><MessageCircle size={13} /> {compactNumber.format(item.num_comments || 0)}</span>
        <span className="time-tag">{relativeTime(item.generated_at || item.created_utc)}</span>
      </div>

      <div className="card-btn-group">
        <button className="btn-icon-text" onClick={handleCopy} title="Sao chép tóm tắt">
          {copied ? <Check size={14} className="success-icon" /> : <Copy size={14} />}
          <span>{copied ? 'Đã chép' : 'Copy'}</span>
        </button>
        <AppLink href={`/post/${item.post_id}`} className="btn-icon-text primary">
          <span>Bằng chứng & Nguồn</span>
          <ArrowRight size={14} />
        </AppLink>
      </div>
    </div>
  </article>
}

function PinnedDigestBanner({ digest, provisional, message }) {
  const [dismissed, setDismissed] = useState(false)
  if (!digest || dismissed) return null

  const payload = digest.payload || {}
  const stories = payload.stories || payload.model_updates || []

  return <div className="pinned-digest-banner">
    <div className="digest-banner-header">
      <div className="digest-kicker">
        <Sparkles size={15} />
        <span>TỔNG HỢP NHANH {digest.period?.toUpperCase() || 'HÔM NAY'}</span>
      </div>
      <button className="close-banner-btn" onClick={() => setDismissed(true)} title="Ẩn bản tin">
        <X size={15} />
      </button>
    </div>

    <h3>{payload.title || digest.title || 'Công nghệ & AI Radar Briefing'}</h3>
    <p className="digest-executive-summary">{payload.executive_summary || digest.executive_summary}</p>

    {stories.length > 0 && (
      <div className="digest-bullet-list">
        {stories.slice(0, 3).map((story, idx) => (
          <div key={idx} className="digest-bullet-item">
            <span className="bullet-dot" />
            <span><b>{story.headline}:</b> {story.summary || story.why_it_matters}</span>
          </div>
        ))}
      </div>
    )}
  </div>
}

function SingleFeedPage({ health, query, setQuery }) {
  const [period, setPeriod] = useState('day')
  const [domain, setDomain] = useState('all')
  const [tier, setTier] = useState('all')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const limit = 15

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true); setError('')
      getJSON(withQuery('/api/feed', { period, domain, q: query.trim(), tier, limit, offset }))
        .then(setData)
        .catch(err => setError(err.message))
        .finally(() => setLoading(false))
    }, 150)
    return () => window.clearTimeout(timer)
  }, [period, domain, query, tier, offset])

  useEffect(() => setOffset(0), [period, domain, query, tier])

  const counts = health?.counts || {}

  return <div className="three-column-layout">
    {/* LEFT RAIL — NAVIGATION & FILTERS */}
    <aside className="left-rail">
      <div className="rail-box sticky-box">
        <div className="rail-heading">BẢNG TIN</div>
        <nav className="rail-menu">
          <button className={tier === 'all' ? 'active' : ''} onClick={() => setTier('all')}>
            <Sparkles size={16} />
            <span>Tất cả dòng tin</span>
          </button>
          <button className={tier === 'knowledge' ? 'active' : ''} onClick={() => setTier('knowledge')}>
            <BrainCircuit size={16} />
            <span>Đúc kết AI</span>
          </button>
          <button className={tier === 'signal' ? 'active' : ''} onClick={() => setTier('signal')}>
            <Radio size={16} />
            <span>Tín hiệu Hot</span>
          </button>
        </nav>

        <div className="rail-heading margin-top">KHUNG THỜI GIAN</div>
        <div className="period-pills">
          {PERIODS.map(([val, label]) => (
            <button key={val} className={period === val ? 'active' : ''} onClick={() => setPeriod(val)}>
              {label}
            </button>
          ))}
        </div>

        <div className="rail-heading margin-top">CHỦ ĐỀ</div>
        <div className="domain-rail-list">
          <button className={domain === 'all' ? 'active' : ''} onClick={() => setDomain('all')}>
            <BarChart3 size={14} />
            <span>Tất cả</span>
            <small>{data?.total || 0}</small>
          </button>
          {(data?.domains || []).map(d => (
            <button key={d.id} className={domain === d.id ? 'active' : ''} onClick={() => setDomain(d.id)}>
              <DomainIcon domain={d.id} size={14} />
              <span>{d.name}</span>
              <small>{d.count}</small>
            </button>
          ))}
        </div>
      </div>
    </aside>

    {/* CENTER FEED — MAIN CONTENT */}
    <main className="center-feed">
      <PinnedDigestBanner digest={data?.digest} />

      <div className="feed-header-line">
        <span className="feed-count-badge">
          <b>{data?.total || 0}</b> bài viết & tín hiệu
        </span>
        <span className="mode-indicator">
          Tự động lọc trùng · Tinh chế Gold Mart
        </span>
      </div>

      {error ? (
        <div className="state-panel error-state">
          <AlertTriangle size={28} />
          <h2>Không tải được dữ liệu</h2>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="state-panel">
          <span className="loader" />
          <p>Đang tinh chế bảng tin thông minh…</p>
        </div>
      ) : data?.items?.length ? (
        <>
          <div className="feed-cards-list">
            {data.items.map(item => (
              <FeedStoryCard item={item} key={item.post_id} />
            ))}
          </div>

          {/* PAGINATION */}
          <div className="pagination">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              <ArrowLeft size={14} /> Trang trước
            </button>
            <span>{offset + 1}–{Math.min(offset + limit, data.total)} / {data.total}</span>
            <button disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>
              Trang sau <ArrowRight size={14} />
            </button>
          </div>
        </>
      ) : (
        <div className="empty-panel">
          <Search size={28} />
          <h3>Không tìm thấy kết quả phù hợp</h3>
          <p>Thử đổi từ khóa hoặc bộ lọc chủ đề khác.</p>
        </div>
      )}
    </main>

    {/* RIGHT RAIL — HOT SIDEBAR */}
    <aside className="right-rail">
      <div className="rail-box sticky-box">
        <div className="rail-heading">
          <TrendingUp size={15} />
          <span>ĐANG NÓNG</span>
        </div>
        <div className="hot-stories-list">
          {(data?.top_trends || []).map((trend, idx) => (
            <AppLink href={`/post/${trend.post_id}`} key={trend.post_id} className="hot-story-item">
              <span className="hot-rank">{idx + 1}</span>
              <div className="hot-story-content">
                <span className="hot-title">{trend.title}</span>
                <span className="hot-sub">r/{trend.subreddit} · {compactNumber.format(trend.score || 0)} điểm</span>
              </div>
            </AppLink>
          ))}
        </div>

        <div className="rail-heading margin-top">THỐNG KÊ PIPELINE</div>
        <div className="pipeline-stats">
          <div className="stat-row">
            <span>Tín hiệu đã lưu</span>
            <b>{compactNumber.format(counts.posts || 0)}</b>
          </div>
          <div className="stat-row">
            <span>Bình luận đã đọc</span>
            <b>{compactNumber.format(counts.comments || 0)}</b>
          </div>
          <div className="stat-row">
            <span>Đúc kết AI</span>
            <b>{fullNumber.format(counts.analyses_ai || 0)}</b>
          </div>
          <div className="stat-row">
            <span>Trạng thái Tầng Serving</span>
            <b className="mode-tag">Gold Mart</b>
          </div>
        </div>
      </div>
    </aside>
  </div>
}

function PostDetail({ postId }) {
  const [post, setPost] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    getJSON(`/api/posts/${encodeURIComponent(postId)}`).then(item => {
      setPost(item)
      document.title = `${item.analysis?.topic || item.title} — Reddit Radar`
    }).catch(err => setError(err.message))
  }, [postId])

  if (error) return <div className="state-panel error-state"><AlertTriangle size={28} /><p>{error}</p></div>
  if (!post) return <div className="state-panel"><span className="loader" /><p>Đang mở hồ sơ bằng chứng…</p></div>
  const title = post.analysis?.topic || post.article_title || post.title
  const view = analysisView(post)

  return <div className="post-detail-container">
    <AppLink href="/" className="back-link"><ArrowLeft size={15} /> Quay lại Bảng tin</AppLink>
    
    <article className="post-header-card">
      <div className="card-topline">
        <span className="domain-label"><DomainIcon domain={post.domain_id} size={14} /> {post.domain_name}</span>
        <span className="sub-tag">r/{post.subreddit}</span>
      </div>
      <h1>{title}</h1>
      {post.analysis?.topic && post.title !== post.analysis.topic && <p className="original-title">Tiêu đề gốc: {post.title}</p>}
      
      <div className="post-meta">
        <span><Clock3 size={14} /> {dateTime(post.created_utc)}</span>
        <span><ThumbsUp size={14} /> {compactNumber.format(post.score || 0)} điểm</span>
        <span><MessageCircle size={14} /> {post.num_comments || 0} bình luận</span>
      </div>

      <div className="post-actions-buttons">
        {post.reddit_url && <a href={post.reddit_url} target="_blank" rel="noreferrer" className="btn-action primary">Reddit gốc <ExternalLink size={14} /></a>}
        {(post.article_final_url || post.url) && <a href={post.article_final_url || post.url} target="_blank" rel="noreferrer" className="btn-action secondary">Nguồn báo <ExternalLink size={14} /></a>}
      </div>
    </article>

    {/* ANALYSIS DETAILS */}
    {view.summary && <section className="detail-section">
      <h3><Sparkles size={16} /> Tóm tắt & Kết luận</h3>
      <p className="summary-prose">{view.summary}</p>
    </section>}

    {view.keyPoints.length > 0 && <section className="detail-section">
      <h3><Lightbulb size={16} /> Các điểm ghi nhận & Bằng chứng</h3>
      <div className="evidence-detail-list">
        {view.keyPoints.map((pt, i) => (
          <div key={i} className="evidence-detail-item">
            <b>{pt.text}</b>
            {pt.evidence && <p>{pt.evidence}</p>}
          </div>
        ))}
      </div>
    </section>}

    {/* SOURCE BODY */}
    {(post.article_body || post.selftext) && <section className="detail-section">
      <h3><FileText size={16} /> Nội dung bài viết</h3>
      <div className="source-prose">{post.article_body || post.selftext}</div>
    </section>}
  </div>
}

export default function App() {
  const [path, setPath] = useState(window.location.pathname)
  const [health, setHealth] = useState(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    getJSON('/api/health').then(setHealth).catch(() => setHealth({ status: 'degraded' }))
  }, [path])

  const postMatch = path.match(/^\/post\/([^/]+)/)

  return <div className="app-container">
    {/* HEADER */}
    <header className="main-header">
      <div className="header-inner">
        <Logo />
        <div className="search-bar">
          <Search size={16} className="search-icon" />
          <input
            type="text"
            placeholder="Tìm kiếm tin tức, subreddit, công nghệ…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query && <button onClick={() => setQuery('')} className="clear-btn"><X size={14} /></button>}
        </div>

        <div className="header-right">
          <span className={`health-pill ${health?.status || 'unknown'}`}>
            <i /> {health?.status === 'healthy' ? 'Pipeline OK' : 'Pipeline chú ý'}
          </span>
        </div>
      </div>
    </header>

    {/* MAIN CONTENT */}
    <div className="main-content-wrapper">
      {postMatch ? (
        <PostDetail postId={decodeURIComponent(postMatch[1])} />
      ) : (
        <SingleFeedPage health={health} query={query} setQuery={setQuery} />
      )}
    </div>
  </div>
}
