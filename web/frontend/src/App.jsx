import { useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Activity, AlertTriangle, ArrowLeft, ArrowRight, BarChart3, BookOpen,
  Bookmark, BookmarkCheck, Bot, BrainCircuit, CheckCircle2, ChevronRight, Clock3, Code2,
  Copy, Check, Download, ExternalLink, Eye, EyeOff, FileText, Filter, FilterX, Flame, Gauge, Globe2, Layers3, Lightbulb,
  ListTree, Menu, MessageCircle, Moon, Radio, RefreshCw, Search, Share2, ShieldCheck, Sparkles, Star, Sun, Target,
  ThumbsUp, TrendingUp, X, Zap, LayoutGrid, List,
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

function cleanMarkdownText(str) {
  if (!str) return ''
  return String(str)
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\\_/g, '_')
    .replace(/#[a-z0-9_]{5,}/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function recordScrollPosition() {
  try {
    sessionStorage.setItem('rr_scroll_pos', String(window.scrollY))
  } catch {}
}

function useRestoreScroll(dependency) {
  useEffect(() => {
    if (!dependency) return
    const savedScroll = sessionStorage.getItem('rr_scroll_pos')
    if (savedScroll) {
      const timer = setTimeout(() => {
        window.scrollTo({ top: Number(savedScroll), behavior: 'smooth' })
        sessionStorage.removeItem('rr_scroll_pos')
      }, 80)
      return () => clearTimeout(timer)
    }
  }, [dependency])
}

function AppLink({ href, children, className = '', onNavigate, onClick, ...props }) {
  const navigate = useNavigate()
  return <Link
    to={href}
    className={className}
    onClick={event => {
      onClick?.(event)
      if (event.defaultPrevented) return
      event.preventDefault()
      navigate(href)
      window.scrollTo({ top: 0, behavior: 'smooth' })
      onNavigate?.()
    }}
    {...props}
  >{children}</Link>
}

function Logo() {
  return <AppLink href="/" className="logo" aria-label="Reddit Radar · Feed">
    <span className="logo-mark"><Activity size={18} /></span>
    <span><b>Reddit</b> Radar</span>
  </AppLink>
}

const NAV_ITEMS = [
  ['feed', '/', 'Feed', Zap],
  ['trends', '/trends', 'Trends', TrendingUp],
  ['social', '/social', 'Social Studio', Share2],
  ['saved', '/saved', 'Đã lưu', Star],
]

function AIBuzzModal({ isOpen, onClose }) {
  const [buzzData, setBuzzData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    getJSON('/api/buzz?period=month')
      .then(setBuzzData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    const onKey = event => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleCopy = () => {
    if (!buzzData?.full_bulletin_text) return
    navigator.clipboard.writeText(buzzData.full_bulletin_text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="modal-content" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Bản tin công nghệ AI Buzz">
      <div className="modal-header">
        <div>
          <span className="eyebrow"><Sparkles size={13} /> AI BUZZ BULLETIN</span>
          <h2>{buzzData?.title || '★ BẢN TIN CÔNG NGHỆ | AI BUZZ'}</h2>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Đóng"><X size={18} /></button>
      </div>

      {loading ? <Loading label="Đang tổng hợp bản tin công nghệ…" /> : (
        <div className="modal-body">
          <div className="buzz-stories-preview">
            {(buzzData?.stories || []).map((s, idx) => (
              <div className="buzz-story-card" key={idx}>
                <span className="buzz-badge">{s.badge}</span>
                <div>
                  <h4>{s.headline}</h4>
                  <p>{s.snippet}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="buzz-raw-text">
            <span className="buzz-label"><Copy size={13} /> NỘI DUNG BẢN TIN ĐỂ ĐĂNG FANPAGE / LINKEDIN:</span>
            <textarea readOnly value={buzzData?.full_bulletin_text || ''} rows={10} />
          </div>
        </div>
      )}

      <div className="modal-footer">
        <button className="button secondary" onClick={onClose}>Đóng</button>
        <button className="button primary" onClick={handleCopy} disabled={!buzzData?.full_bulletin_text}>
          {copied ? <Check size={15} /> : <Copy size={15} />}
          <span>{copied ? 'Đã copy bản tin!' : 'Copy toàn bộ bản tin'}</span>
        </button>
      </div>
    </div>
  </div>
}

function Header({ active, health, savedCount = 0, theme, toggleTheme, onOpenBuzz }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return <header className="topbar">
    <div className="topbar-inner">
      <Logo />
      <nav className="main-nav" aria-label="Điều hướng chính">
        {NAV_ITEMS.map(([key, href, label, Icon]) => (
          <AppLink key={key} href={href} className={active === key ? 'active' : ''}>
            <Icon size={15} />
            {label}
            {key === 'saved' && savedCount > 0 && <span className="nav-badge">{savedCount}</span>}
          </AppLink>
        ))}
      </nav>
      <div className="top-actions">
        <button className="button primary buzz-nav-btn" onClick={onOpenBuzz} title="Mở Bản tin Công nghệ AI Buzz">
          <Sparkles size={14} />
          <span>AI Buzz Bulletin</span>
        </button>

        <button
          className="icon-button theme-toggle"
          aria-label={theme === 'dark' ? "Chuyển giao diện sáng" : "Chuyển giao diện tối"}
          title={theme === 'dark' ? "Giao diện sáng" : "Giao diện tối"}
          onClick={toggleTheme}
        >
          {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>

        <span className={`health-pill ${health?.status || 'unknown'}`}>
          <i />{health?.status === 'healthy' ? 'Pipeline ổn định' : 'Pipeline cần chú ý'}
        </span>
        <button className="icon-button menu-button" aria-label="Mở menu"
          aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)}>
          {menuOpen ? <X size={19} /> : <Menu size={19} />}
        </button>
      </div>
    </div>
    {menuOpen && <nav className="mobile-nav" aria-label="Điều hướng di động">
      {NAV_ITEMS.map(([key, href, label, Icon]) => <AppLink key={key} href={href}
        className={active === key ? 'active' : ''} onNavigate={() => setMenuOpen(false)}>
        <Icon size={17} />{label}
        {key === 'saved' && savedCount > 0 && <span className="nav-badge">{savedCount}</span>}
      </AppLink>)}
    </nav>}
  </header>
}

function Loading({ label = 'Đang đọc dữ liệu…' }) {
  return <div className="state-panel"><span className="loader" /><p>{label}</p></div>
}

function SkeletonGrid({ count = 6 }) {
  return <div className="skeleton-grid" aria-hidden="true">
    {Array.from({ length: count }, (_, i) => (
      <div className="skeleton-card" key={i}>
        <div className="sk-line sk-30" />
        <div className="sk-line sk-95" />
        <div className="sk-line sk-75" />
        <div className="sk-line sk-50" />
      </div>
    ))}
  </div>
}

function ErrorState({ message, retry }) {
  return <div className="state-panel error-state">
    <AlertTriangle size={28} /><h2>Không tải được dữ liệu</h2><p>{message}</p>
    {retry && <button className="button primary" onClick={retry}>Thử lại</button>}
  </div>
}

function ProviderBadge({ provider, model, isAI, compact = false }) {  const local = !isAI || (provider || '').toLowerCase().startsWith('local')
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

function QualityPill({ score }) {
  if (score == null) return null
  const level = score >= 70 ? 'excellent' : score >= 50 ? 'good' : score >= 35 ? 'ok' : 'low'
  return <span className={`quality-pill ${level}`} title={`Chất lượng theo mart dữ liệu: ${Math.round(score)}/100`}>
    <Gauge size={11} /> {Math.round(score)}
  </span>
}

function Metric({ icon: Icon, value, label, tone = '' }) {
  return <article className="metric-card">
    <span className={`metric-icon ${tone}`}><Icon size={18} /></span>
    <div><strong>{value}</strong><small>{label}</small></div>
  </article>
}

function DomainIcon({ domain, size = 18 }) {
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

function computePostValue(item) {
  const score = item.score || item.latest_score || 0
  const comments = item.num_comments || item.comment_count || item.latest_comments || 0
  const trend = item.trend_score || 0
  const hasAI = item.is_ai ? 50 : 0
  return score * 0.4 + comments * 2.5 + trend * 1.2 + hasAI
}

function CardActionsBar({ item, isSaved, toggleSave, isRead, markRead }) {
  const [copied, setCopied] = useState(false)
  const view = analysisView(item)

  const handleCopy = (e) => {
    e.stopPropagation()
    const text = `📌 ${view.title}\n\n${view.summary || ''}\n\nNguồn: ${item.reddit_url || 'Reddit Radar'}`
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return <div className="card-action-bar">
    <button
      className={`card-action-btn ${isSaved ? 'saved' : ''}`}
      onClick={(e) => { e.stopPropagation(); toggleSave(item.post_id) }}
      title={isSaved ? 'Đã lưu (Bấm để bỏ)' : 'Lưu xem sau'}
    >
      <Star size={14} className={isSaved ? 'fill-star' : ''} />
      <span>{isSaved ? 'Đã lưu' : 'Lưu'}</span>
    </button>

    <button className="card-action-btn" onClick={handleCopy} title="Sao chép tóm tắt">
      {copied ? <Check size={14} className="green-icon" /> : <Copy size={14} />}
      <span>{copied ? 'Đã chép' : 'Copy'}</span>
    </button>

    {isRead && <span className="read-tag" title="Bạn đã đọc bài này"><CheckCircle2 size={12} /> Đã xem</span>}
  </div>
}

function AnalysisCard({ item, featured = false, isSaved, toggleSave, isRead, markRead }) {
  const view = analysisView(item)
  const isHotPost = (item.num_comments >= 15 || item.score >= 30 || item.source_stream === 'hot' || item.source_stream === 'both')

  return <article className={`analysis-card ${featured ? 'featured' : ''} ${isRead ? 'read-card' : ''}`}>
    <div className="card-topline">
      <div className="topline-tags">
        <span className="domain-label"><DomainIcon domain={item.domain_id} size={14} />
          {item.domain_name || 'Khác'}
        </span>
        {isHotPost && <span className="hot-pill" title="Bài viết nổi bật đang có lượng thảo luận sôi nổi"><Flame size={12} /> HOT VIRAL</span>}
        <QualityPill score={item.quality_score} />
      </div>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />
    </div>
    <h3>
      <AppLink href={`/post/${item.post_id}`} onClick={() => { recordScrollPosition(); markRead(item.post_id); }}>
        {view.title}
      </AppLink>
    </h3>
    {view.summary && <p className="card-summary">{view.summary}</p>}
    {view.keyPoints[0] && <div className="key-preview">
      <Lightbulb size={15} /><span>{view.keyPoints[0].text}</span>
    </div>}
    <div className="card-meta">
      <span><ThumbsUp size={13} /> {item.score || 0}</span>
      <span><MessageCircle size={13} /> {item.num_comments || item.comment_count || 0}</span>
      <span><Clock3 size={13} /> {relativeTime(item.generated_at || item.created_utc)}</span>
      {item.subreddit && <span className="sub-tag">r/{item.subreddit}</span>}
    </div>
    <CardActionsBar item={item} isSaved={isSaved} toggleSave={toggleSave} isRead={isRead} markRead={markRead} />
  </article>
}

function DigestPanel({ digest, provisional, message }) {
  if (!digest) return <section className="digest-panel provisional-panel">
    <div className="digest-kicker"><Clock3 size={15} /> BRIEFING ĐANG CHỜ PIPELINE</div>
    <h2>Chưa có bản tổng hợp theo chu kỳ</h2>
    <p>{message || 'Các phân tích tốt nhất và tín hiệu thô vẫn được hiển thị bên dưới.'}</p>
    <ProviderBadge provider="none" isAI={false} />
  </section>

  const payload = digest.payload || {}
  const stories = payload.stories || payload.model_updates || []
  const watchlist = payload.watchlist || []
  return <section className={`digest-panel ${provisional ? 'provisional-panel' : ''}`}>
    <div className="digest-head">
      <div>
        <div className="digest-kicker"><Sparkles size={15} /> BRIEFING {digest.period?.toUpperCase()}</div>
        <h2>{digest.title || 'Điểm tin công nghệ AI & Lập trình'}</h2>
      </div>
      <ProviderBadge provider={digest.provider} model={digest.model} isAI={digest.is_ai} />
    </div>
    {digest.summary && <p className="digest-lead">{digest.summary}</p>}
    {stories.length > 0 && <div className="digest-stories">
      {stories.slice(0, 4).map((story, index) => {
        const postId = story.post_id || story.source_post_ids?.[0]
        const content = <><span>{story.change_type || story.category || 'update'}</span>
          <b>{story.headline}</b><small>{story.summary || story.why_it_matters}</small></>
        return postId
          ? <AppLink href={`/post/${postId}`} className="digest-story" key={`${postId}-${index}`} onClick={recordScrollPosition}>{content}<ChevronRight size={15} /></AppLink>
          : <div className="digest-story" key={index}>{content}</div>
      })}
    </div>}
    {watchlist.length > 0 && <div className="watchline"><Gauge size={15} />
      <b>Cần theo dõi:</b> {watchlist.slice(0, 3).map(item => item.topic || item).join(' · ')}
    </div>}
    <footer className="digest-foot">Tạo {dateTime(digest.generated_at)} từ {digest.source_count || 0} nguồn đã crawl</footer>
  </section>
}

function SearchBox({ value, setValue, placeholder }) {
  return <label className="search-box"><Search size={17} />
    <input value={value} onChange={event => setValue(event.target.value)} placeholder={placeholder} />
    {value && <button aria-label="Xóa tìm kiếm" onClick={() => setValue('')}><X size={15} /></button>}
  </label>
}

function DomainChips({ domains, selected, setSelected, total }) {
  return <div className="domain-chips" role="group" aria-label="Lọc theo lĩnh vực">
    <button className={selected === 'all' ? 'active' : ''} onClick={() => setSelected('all')}>
      <BarChart3 size={14} /> Tất cả <b>{total}</b>
    </button>
    {domains.map(domain => <button key={domain.id} className={selected === domain.id ? 'active' : ''}
      onClick={() => setSelected(domain.id)}>
      <DomainIcon domain={domain.id} size={14} /> {domain.name} <b>{domain.count}</b>
    </button>)}
  </div>
}

function getStoredNum(key, defaultVal) {
  try {
    const val = sessionStorage.getItem(key)
    return val !== null ? Number(val) : defaultVal
  } catch {
    return defaultVal
  }
}

function setStoredNum(key, val) {
  try { sessionStorage.setItem(key, String(val)) } catch {}
}

function Pagination({ offset, limit, total, hasMore, onPageChange }) {
  const totalPages = Math.ceil(total / limit) || 1
  const currentPage = Math.floor(offset / limit) + 1

  const pages = []
  const startPage = Math.max(1, currentPage - 2)
  const endPage = Math.min(totalPages, currentPage + 2)
  for (let p = startPage; p <= endPage; p++) {
    pages.push(p)
  }

  return (
    <div className="pagination">
      <button
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        <ArrowLeft size={14} /> Trang trước
      </button>

      <div className="page-numbers">
        {startPage > 1 && (
          <>
            <button className={`page-num ${currentPage === 1 ? 'active' : ''}`} onClick={() => onPageChange(1)}>1</button>
            {startPage > 2 && <span className="page-dots">…</span>}
          </>
        )}
        {pages.map(p => (
          <button
            key={p}
            className={`page-num ${currentPage === p ? 'active' : ''}`}
            onClick={() => onPageChange(p)}
          >
            {p}
          </button>
        ))}
        {endPage < totalPages && (
          <>
            {endPage < totalPages - 1 && <span className="page-dots">…</span>}
            <button className={`page-num ${currentPage === totalPages ? 'active' : ''}`} onClick={() => onPageChange(totalPages)}>{totalPages}</button>
          </>
        )}
      </div>

      <button
        disabled={!hasMore || currentPage >= totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      >
        Trang sau <ArrowRight size={14} />
      </button>
    </div>
  )
}

function parseUrlFeedState(limit, searchParams, pathname) {
  const params = searchParams

  const pageMatch = pathname.match(/^\/page\/(\d+)/)
  const pageFromPath = pageMatch ? parseInt(pageMatch[1], 10) : null

  const page = pageFromPath || parseInt(params.get('page') || '0', 10)
  const viewParam = params.get('view')

  let view = 'hot'
  if (viewParam === 'all' || pathname === '/browse' || pathname === '/knowledge' || pageMatch || page > 1) {
    view = 'all'
  } else if (viewParam === 'hot') {
    view = 'hot'
  } else {
    view = sessionStorage.getItem('rr_feed_subview') || 'hot'
  }

  const offsetFromUrl = page > 1 ? (page - 1) * limit : null
  const offset = offsetFromUrl !== null ? offsetFromUrl : getStoredNum('rr_k_offset', 0)
  const q = params.get('q') || ''
  const domain = params.get('domain') || 'all'

  return { view, page: page || 1, offset, q, domain }
}

// ─────────────────────────────────────────────
// FEED PAGE (merges Today + Knowledge)
// ─────────────────────────────────────────────
function ReadFilterPills({ value, onChange }) {
  return <>
    <button
      className={`toolbar-pill ${value === 'hide' ? 'active' : ''}`}
      onClick={() => onChange(value === 'hide' ? 'all' : 'hide')}
      title="Bỏ bài đã đọc khỏi danh sách"
    >
      <EyeOff size={13} />
      <span>{value === 'hide' ? 'Đang ẩn bài đã đọc' : 'Ẩn bài đã đọc'}</span>
    </button>
    <button
      className={`toolbar-pill ${value === 'only' ? 'active' : ''}`}
      onClick={() => onChange(value === 'only' ? 'all' : 'only')}
      title="Chỉ hiển thị bài bạn đã mở"
    >
      <Eye size={13} />
      <span>{value === 'only' ? 'Chỉ hiện bài đã đọc' : 'Chỉ bài đã đọc'}</span>
    </button>
  </>
}

function FeedPage({ health, savedSet, toggleSave, readSet, markRead }) {
  const limit = 12
  const [searchParams, setSearchParams] = useSearchParams()
  const { pathname } = useLocation()
  const initial = useMemo(() => parseUrlFeedState(limit, searchParams, pathname), [limit, searchParams, pathname])

  const [subView, setSubViewState] = useState(() => initial.view)
  const [query, setQuery] = useState(() => initial.q)
  const [domain, setDomainState] = useState(() => initial.domain)
  const [offset, setOffsetState] = useState(() => initial.offset)
  const [readFilter, setReadFilter] = useState('all')
  const [sortBy, setSortBy] = useState('value')

  const updateFeedUrl = (newView, newOffset, newQuery, newDomain) => {
    const pageNum = Math.floor(newOffset / limit) + 1
    const params = {}
    if (newView === 'all') params.view = 'all'
    if (pageNum > 1) params.page = pageNum
    if (newQuery && newQuery.trim()) params.q = newQuery.trim()
    if (newDomain && newDomain !== 'all') params.domain = newDomain
    setSearchParams(params)
  }

  const setSubView = (val) => {
    setSubViewState(val)
    try { sessionStorage.setItem('rr_feed_subview', val) } catch {}
    updateFeedUrl(val, offset, query, domain)
  }

  const setOffset = (val) => {
    setOffsetState(val)
    setStoredNum('rr_k_offset', val)
    updateFeedUrl(subView, val, query, domain)
  }

  const handlePageChange = (pageNum) => {
    const newOffset = Math.max(0, (pageNum - 1) * limit)
    setOffset(newOffset)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const setDomain = (val) => {
    setDomainState(val)
    setOffset(0)
    updateFeedUrl(subView, 0, query, val)
  }

  const handleSearchChange = (val) => {
    setQuery(val)
    setOffset(0)
    updateFeedUrl(subView, 0, val, domain)
  }

  // Sync local state when the URL changes (navigation, back/forward)
  useEffect(() => {
    const parsed = parseUrlFeedState(limit, searchParams, pathname)
    setSubViewState(parsed.view)
    setQuery(parsed.q)
    setDomainState(parsed.domain)
    setOffsetState(parsed.offset)
  }, [searchParams, pathname])

  // Hot Now data
  const [hotData, setHotData] = useState(null)
  const [hotLoading, setHotLoading] = useState(true)
  const [hotError, setHotError] = useState('')

  // All (knowledge) data
  const [allData, setAllData] = useState(null)
  const [allLoading, setAllLoading] = useState(true)
  const [allError, setAllError] = useState('')

  // Load Hot Now
  const loadHot = () => {
    setHotLoading(true); setHotError('')
    getJSON('/api/today?period=day&limit=25').then(setHotData)
      .catch(err => setHotError(err.message)).finally(() => setHotLoading(false))
  }
  useEffect(loadHot, [])

  // Load All (knowledge)
  const loadAll = () => {
    setAllLoading(true); setAllError('')
    getJSON(withQuery('/api/knowledge/feed', { q: query.trim(), domain, limit, offset }))
      .then(setAllData)
      .catch(err => setAllError(err.message))
      .finally(() => setAllLoading(false))
  }
  useEffect(() => {
    if (subView !== 'all') return
    const timer = window.setTimeout(loadAll, 180)
    return () => window.clearTimeout(timer)
  }, [subView, query, domain, offset])

  useRestoreScroll(hotData || allData)

  const processedHot = useMemo(() => {
    if (!hotData?.highlights) return []
    let list = [...hotData.highlights]
    if (readFilter === 'hide') list = list.filter(i => !readSet.has(i.post_id))
    if (readFilter === 'only') list = list.filter(i => readSet.has(i.post_id))
    list.sort((a, b) => {
      if (sortBy === 'value') return computePostValue(b) - computePostValue(a)
      if (sortBy === 'comments') return (b.num_comments || 0) - (a.num_comments || 0)
      if (sortBy === 'score') return (b.score || 0) - (a.score || 0)
      if (sortBy === 'time') return (b.generated_at || 0) - (a.generated_at || 0)
      return 0
    })
    return list
  }, [hotData, readFilter, sortBy, readSet])

  const filteredAll = useMemo(() => {
    if (!allData?.items) return []
    let list = [...allData.items]
    if (readFilter === 'hide') list = list.filter(i => !readSet.has(i.post_id))
    if (readFilter === 'only') list = list.filter(i => readSet.has(i.post_id))
    list.sort((a, b) => {
      if (sortBy === 'value') return computePostValue(b) - computePostValue(a)
      if (sortBy === 'comments') return (b.num_comments || 0) - (a.num_comments || 0)
      if (sortBy === 'score') return (b.score || 0) - (a.score || 0)
      if (sortBy === 'time') return (b.generated_at || 0) - (a.generated_at || 0)
      return 0
    })
    return list
  }, [allData, readFilter, sortBy, readSet])

  const counts = health?.counts || {}

  return <>
    {/* HERO: compact, with live indicator */}
    <section className="feed-hero">
      <div className="feed-hero-left">
        <div className="feed-live-badge">
          <span className="live-dot" />
          LIVE FEED
          <span className="feed-update-time"><Clock3 size={11} /> Cập nhật {relativeTime(health?.stages?.collector?.last_success_at)}</span>
        </div>
        <h1>Công nghệ đang<br /><em>nói gì hôm nay?</em></h1>
        <div className="feed-stats-row">
          <span><Radio size={13} /> <b>{compactNumber.format(counts.posts || 0)}</b> tín hiệu</span>
          <span><MessageCircle size={13} /> <b>{compactNumber.format(counts.comments || 0)}</b> bình luận</span>
          <span><Bot size={13} /> <b>{fullNumber.format(counts.analyses_ai || 0)}</b> phân tích AI</span>
        </div>
      </div>
    </section>

    {/* FEED SUB-VIEW SWITCHER — prominent horizontal tab bar below hero */}
    <div className="feed-view-tabs" role="tablist" aria-label="Chọn chế độ xem">
      <button
        role="tab"
        aria-selected={subView === 'hot'}
        className={`feed-view-tab ${subView === 'hot' ? 'active hot' : ''}`}
        onClick={() => setSubView('hot')}
      >
        <Flame size={16} />
        <span>Hot Now</span>
        <em>Top 25 bài tốt nhất hôm nay</em>
      </button>
      <button
        role="tab"
        aria-selected={subView === 'all'}
        className={`feed-view-tab ${subView === 'all' ? 'active all' : ''}`}
        onClick={() => setSubView('all')}
      >
        <LayoutGrid size={16} />
        <span>Tất cả bài</span>
        <em>Toàn bộ kho + tìm kiếm</em>
      </button>
    </div>

    {/* DIGEST: always visible on top, full-width */}
    {subView === 'hot' && <DigestPanel digest={hotData?.digest} provisional={hotData?.provisional} message={hotData?.message} />}

    {/* SUB-VIEW: HOT NOW */}
    {subView === 'hot' && <>
      <div className="feed-toolbar">
        <div className="feed-toolbar-left">
          <ReadFilterPills value={readFilter} onChange={setReadFilter} />
          <button className="toolbar-pill" onClick={loadHot} title="Tải lại dữ liệu Hot Now">
            <RefreshCw size={13} />
            <span>Làm mới</span>
          </button>
        </div>
        <div className="feed-toolbar-right">
          <span className="sort-label"><Filter size={12} /> Sắp xếp:</span>
          <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="sort-select">
            <option value="value">⭐ Giá trị cao</option>
            <option value="comments">💬 Nhiều bình luận</option>
            <option value="score">👍 Điểm vote cao</option>
            <option value="time">🕐 Mới nhất</option>
          </select>
        </div>
      </div>

      {hotLoading ? <SkeletonGrid count={6} />
        : hotError ? <ErrorState message={hotError} retry={loadHot} />
        : processedHot.length ? (
          <div className="highlight-grid">
            {processedHot.map((item, index) => (
              <AnalysisCard
                item={item}
                featured={index === 0}
                key={item.post_id}
                isSaved={savedSet.has(item.post_id)}
                toggleSave={toggleSave}
                isRead={readSet.has(item.post_id)}
                markRead={markRead}
              />
            ))}
          </div>
        ) : (
          <div className="empty-panel">
            <Flame size={28} />
            <h3>{readFilter === 'only' ? 'Chưa đọc bài nào' : 'Không có bài hot hôm nay'}</h3>
            <p>{readFilter === 'only' ? 'Mở vài bài là chúng sẽ xuất hiện ở đây.' : 'Thử đổi bộ lọc bài đã đọc.'}</p>
            <button className="button secondary" onClick={() => setReadFilter('all')}>
              <FilterX size={14} /> Đặt lại bộ lọc
            </button>
          </div>
        )}
    </>}

    {/* SUB-VIEW: ALL */}
    {subView === 'all' && <>
      <div className="all-search-bar">
        <SearchBox
          value={query}
          setValue={handleSearchChange}
          placeholder="Tìm bài học AI, DevTools, subreddit…"
        />
      </div>

      <DomainChips
        domains={allData?.domains || []}
        selected={domain}
        setSelected={setDomain}
        total={allData?.total || 0}
      />

      <div className="feed-toolbar">
        <div className="feed-toolbar-left">
          <ReadFilterPills value={readFilter} onChange={setReadFilter} />
        </div>
        <div className="feed-toolbar-right">
          <span className="result-count">Hiển thị <b>{filteredAll.length}</b> kết quả · Trang {Math.floor(offset / limit) + 1}</span>
          <span className="sort-label"><Filter size={12} /> Sắp xếp:</span>
          <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="sort-select">
            <option value="value">⭐ Giá trị cao</option>
            <option value="comments">💬 Nhiều bình luận</option>
            <option value="score">👍 Điểm vote cao</option>
            <option value="time">🕐 Mới nhất</option>
          </select>
        </div>
      </div>

      {allError ? <ErrorState message={allError} retry={loadAll} />
        : allLoading ? <SkeletonGrid count={6} />
        : filteredAll.length ? <>
          <div className="knowledge-grid">
            {filteredAll.map(item => (
              <AnalysisCard
                item={item}
                key={item.post_id}
                isSaved={savedSet.has(item.post_id)}
                toggleSave={toggleSave}
                isRead={readSet.has(item.post_id)}
                markRead={markRead}
              />
            ))}
          </div>
          <Pagination
            offset={offset}
            limit={limit}
            total={allData?.total || 0}
            hasMore={Boolean(allData?.has_more)}
            onPageChange={handlePageChange}
          />
        </> : <div className="empty-panel">
          <Search size={32} />
          <h3>Không tìm thấy kết quả phù hợp</h3>
          <p>Thử từ khóa khác hoặc xóa bộ lọc.</p>
          <button className="button primary" onClick={() => { handleSearchChange(''); setDomain('all'); setReadFilter('all') }}>
            <FilterX size={14} /> Xóa toàn bộ bộ lọc
          </button>
        </div>}
    </>}
  </>
}

// ─────────────────────────────────────────────
// SAVED PAGE
// ─────────────────────────────────────────────
function SavedPage({ savedSet, toggleSave, readSet, markRead }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)

  const loadSaved = () => {
    setLoading(true)
    getJSON('/api/user/bookmarks/details')
      .then(res => setData(res?.items || []))
      .catch(() => setData([]))
      .finally(() => setLoading(false))
  }

  useEffect(loadSaved, [])

  const handleToggleSave = (postId) => {
    toggleSave(postId)
    setData(prev => prev.filter(item => item.post_id !== postId))
  }

  useRestoreScroll(data)

  return <>
    <section className="page-intro saved-intro">
      <div className="intro-copy">
        <span className="eyebrow"><Star size={14} /> PERSONAL BOOKMARKS</span>
        <h1>Bài viết đã lưu ({data.length})</h1>
        <p>Danh sách bài viết bạn đã bấm sao/lưu để đọc lại. Đồng bộ thời gian thực từ SQLite database.</p>
      </div>
    </section>

    {loading ? <SkeletonGrid count={6} />
      : data.length ? (
        <div className="knowledge-grid">
          {data.map(item => (
            <AnalysisCard
              item={item}
              key={item.post_id}
              isSaved={true}
              toggleSave={handleToggleSave}
              isRead={readSet.has(item.post_id)}
              markRead={markRead}
            />
          ))}
        </div>
      ) : (
        <div className="empty-panel">
          <Star size={36} className="empty-star" />
          <h3>Chưa có bài viết nào trong mục Lưu</h3>
          <p>Bấm vào biểu tượng <b>Lưu</b> ở bất kỳ bài viết nào để lưu lại danh sách đọc tại đây.</p>
          <AppLink href="/" className="button primary">Khám phá Feed ngay</AppLink>
        </div>
      )}
  </>
}

// ─────────────────────────────────────────────
// TRENDS PAGE (replaces Radar, now a real dashboard)
// ─────────────────────────────────────────────

function pctDelta(latest, previous) {
  if (!previous || previous <= 0 || !latest) return null
  return Math.round(((latest - previous) / previous) * 100)
}

function TrendsInsights({ items, totalItems, markRead }) {
  const sorted = [...items].sort((a, b) => (b.trend_score ?? 0) - (a.trend_score ?? 0))
  const star = sorted[0]
  const momentum = [...items]
    .sort((a, b) => (b.score_velocity ?? 0) - (a.score_velocity ?? 0))
    .slice(0, 3)
  const totalScore = items.reduce((sum, i) => sum + (i.latest_score || i.score || 0), 0)
  const totalComments = items.reduce((sum, i) => sum + (i.latest_comments || i.num_comments || 0), 0)
  const quality = items.length
    ? Math.round(items.reduce((sum, i) => sum + (i.quality_score || 0), 0) / items.length)
    : 0

  return <>
    <div className="trends-dashboard">
      <div className="trends-chart-panel trends-star-panel">
        <div className="trends-panel-header">
          <Flame size={16} />
          <span>Ngôi sao đang hot</span>
          <small>Top theo trend score</small>
        </div>
        {star ? (
          <AppLink
            className="trends-star-card"
            href={`/post/${star.post_id}`}
            onClick={() => { recordScrollPosition(); markRead(star.post_id) }}
          >
            <div className="trends-star-meta">
              <span className="domain-label"><DomainIcon domain={star.domain_id} size={13} />{star.domain_name || 'Khác'}</span>
              {star.subreddit && <span className="sub-tag">r/{star.subreddit}</span>}
              <QualityPill score={star.quality_score} />
            </div>
            <h3>{star.title || star.summary || star.post_id}</h3>
            <div className="trends-star-stats">
              <span><ThumbsUp size={13} /> {compactNumber.format(star.latest_score || star.score || 0)}
                {pctDelta(star.latest_score, star.previous_score) !== null && (
                  <em className={pctDelta(star.latest_score, star.previous_score) >= 0 ? 'delta-up' : 'delta-down'}>
                    {pctDelta(star.latest_score, star.previous_score) >= 0 ? '▲' : '▼'} {Math.abs(pctDelta(star.latest_score, star.previous_score))}%
                  </em>
                )}
              </span>
              <span><MessageCircle size={13} /> {compactNumber.format(star.latest_comments || star.num_comments || 0)}</span>
              <span><TrendingUp size={13} /> trend {star.trend_score ?? 0}</span>
            </div>
          </AppLink>
        ) : <p className="trends-empty-note">Chưa có tín hiệu trong cửa sổ này.</p>}
      </div>

      <div className="trends-summary-panel trends-momentum-panel">
        <div className="trends-panel-header">
          <Zap size={16} />
          <span>Đang bùng nổ</span>
          <small>Vận tốc upvotes</small>
        </div>
        <div className="trends-momentum-list">
          {momentum.map((item, idx) => (
            <AppLink
              key={item.post_id}
              className="trends-momentum-item"
              href={`/post/${item.post_id}`}
              onClick={() => { recordScrollPosition(); markRead(item.post_id) }}
            >
              <span className="signal-rank">{idx + 1}</span>
              <div className="trends-momentum-body">
                <span className="trends-momentum-title">{item.title || item.summary || item.post_id}</span>
                <span className="trends-momentum-meta">
                  {item.subreddit && <span>r/{item.subreddit}</span>}
                  <span><ThumbsUp size={11} /> {compactNumber.format(item.latest_score || item.score || 0)}</span>
                  <span><MessageCircle size={11} /> {compactNumber.format(item.latest_comments || item.num_comments || 0)}</span>
                  {pctDelta(item.latest_score, item.previous_score) !== null && (
                    <em className={pctDelta(item.latest_score, item.previous_score) >= 0 ? 'delta-up' : 'delta-down'}>
                      {pctDelta(item.latest_score, item.previous_score) >= 0 ? '▲' : '▼'} {Math.abs(pctDelta(item.latest_score, item.previous_score))}%
                    </em>
                  )}
                </span>
              </div>
              <span className="trends-velocity-badge"><TrendingUp size={11} /> {Math.round(item.score_velocity ?? 0)}</span>
            </AppLink>
          ))}
        </div>
      </div>
    </div>

    <div className="trends-stats-strip">
      <div className="trend-stat">
        <b>{totalItems}</b>
        <small>bài trending</small>
      </div>
      <div className="trend-stat">
        <b>{compactNumber.format(totalScore)}</b>
        <small>tổng upvotes</small>
      </div>
      <div className="trend-stat">
        <b>{compactNumber.format(totalComments)}</b>
        <small>tổng bình luận</small>
      </div>
      <div className="trend-stat">
        <b>{quality}<span className="trend-stat-unit">/100</span></b>
        <small>chất lượng trung bình</small>
      </div>
    </div>
  </>
}

function TrendRankedItem({ item, rank, maxTrend, isSaved, toggleSave, isRead, markRead }) {
  const rankClass = rank === 1 ? 'gold' : rank === 2 ? 'silver' : rank === 3 ? 'bronze' : ''
  const trendScore = item.trend_score ?? 0
  const trendPct = maxTrend > 0 ? Math.round((trendScore / maxTrend) * 100) : 0
  const view = analysisView(item)

  return <article className={`analysis-card trend-card ${isRead ? 'read-card' : ''}`}>
    <div className="card-topline">
      <div className="topline-tags">
        <span className={`signal-rank ${rankClass}`}>{String(rank).padStart(2, '0')}</span>
        <span className="domain-label"><DomainIcon domain={item.domain_id} size={14} />
          {item.domain_name || 'Khác'}
        </span>
        {item.subreddit && <span className="sub-tag">r/{item.subreddit}</span>}
        <QualityPill score={item.quality_score} />
      </div>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />
    </div>
    <h3>
      <AppLink href={`/post/${item.post_id}`} onClick={() => { recordScrollPosition(); markRead(item.post_id); }}>
        {view.title}
      </AppLink>
    </h3>
    {view.summary && <p className="card-summary">{view.summary}</p>}
    <div className="trend-score-bar-row">
      <div className="trend-score-track">
        <div
          className="trend-score-fill"
          style={{ width: `${trendPct}%` }}
          title={`Trend score: ${trendScore}`}
        />
      </div>
      <span className="trend-score-label"><TrendingUp size={11} /> {trendScore}</span>
    </div>
    <div className="card-meta">
      <span><ThumbsUp size={13} /> {compactNumber.format(item.latest_score || item.score || 0)}</span>
      <span><MessageCircle size={13} /> {compactNumber.format(item.latest_comments || item.num_comments || 0)}</span>
      <span><Clock3 size={13} /> {relativeTime(item.created_utc)}</span>
    </div>
    <CardActionsBar item={item} isSaved={isSaved} toggleSave={toggleSave} isRead={isRead} markRead={markRead} />
  </article>
}

function TrendsPage({ savedSet, toggleSave, readSet, markRead }) {
  const [period, setPeriod] = useState('day')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('all')
  const [readFilter, setReadFilter] = useState('all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true); setError('')
    getJSON(withQuery('/api/trending', { period, limit: 100 })).then(setData)
      .catch(err => setError(err.message)).finally(() => setLoading(false))
  }
  useEffect(load, [period])
  useRestoreScroll(data)

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('vi')
    return (data?.items || []).filter(item => {
      if (readFilter === 'hide' && readSet.has(item.post_id)) return false
      if (readFilter === 'only' && !readSet.has(item.post_id)) return false
      const matchesDomain = domain === 'all' || item.domain_id === domain
      const text = `${item.title || ''} ${item.subreddit || ''} ${item.domain_name || ''} ${item.analysis?.topic || ''}`.toLocaleLowerCase('vi')
      return matchesDomain && (!normalized || text.includes(normalized))
    })
  }, [data, domain, query, readFilter, readSet])

  const maxTrend = useMemo(() => Math.max(...(filtered.map(i => i.trend_score ?? 0)), 1), [filtered])

  const totalItems = data?.items?.length || 0

  return <>
    {/* TRENDS HERO */}
    <section className="trends-hero">
      <div className="trends-hero-left">
        <span className="eyebrow trends-eyebrow"><TrendingUp size={14} /> TREND INTELLIGENCE</span>
        <h1>Xu hướng đang<br /><em>nổi nhất</em></h1>
        <p>Vận tốc bài viết theo thời gian thực. Xem domain nào đang bùng nổ, bài nào đang leo rank nhanh nhất.</p>
      </div>
      <div className="period-switch-block">
        <span className="period-label">Khoảng thời gian</span>
        <div className="period-switch">
          {PERIODS.map(([value, label]) => (
            <button key={value} className={period === value ? 'active' : ''} onClick={() => { setPeriod(value); setDomain('all') }}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </section>

    {/* DASHBOARD: star story + momentum + summary stats */}
    {!loading && !error && data && (
      <TrendsInsights items={filtered} totalItems={totalItems} markRead={markRead} />
    )}

    {/* SEARCH + DOMAIN FILTER */}
    <div className="trends-filter-row">
      <SearchBox value={query} setValue={setQuery} placeholder="Lọc tiêu đề, subreddit hoặc công nghệ…" />
      <ReadFilterPills value={readFilter} onChange={setReadFilter} />
      <DomainChips domains={data?.domains || []} selected={domain} setSelected={setDomain} total={totalItems} />
    </div>

    <div className="result-line">
      <span>Hiển thị <b>{filtered.length}</b> tín hiệu đang trending</span>
      <span>Cập nhật {relativeTime(data?.generated_at)}</span>
    </div>

    {/* RANKED GRID */}
    {error ? <ErrorState message={error} retry={load} />
      : loading ? <SkeletonGrid count={6} />
      : filtered.length ? (
        <div className="knowledge-grid">
          {filtered.map((item, index) => (
            <TrendRankedItem
              key={item.post_id}
              item={item}
              rank={index + 1}
              maxTrend={maxTrend}
              isSaved={savedSet.has(item.post_id)}
              toggleSave={toggleSave}
              isRead={readSet.has(item.post_id)}
              markRead={markRead}
            />
          ))}
        </div>
      ) : <div className="empty-panel">
        <Search size={32} />
        <h3>Không tìm thấy tín hiệu phù hợp</h3>
        <p>Thử đổi từ khóa, lĩnh vực hoặc mở rộng khoảng thời gian.</p>
        <button className="button primary" onClick={() => { setQuery(''); setDomain('all'); setReadFilter('all') }}>
          <FilterX size={14} /> Đặt lại bộ lọc Trends
        </button>
      </div>}
  </>
}

// ─────────────────────────────────────────────
// SOCIAL STUDIO PAGE
// ─────────────────────────────────────────────
function RoundupStrip({ roundup, hours, setHours }) {
  const [copiedId, setCopiedId] = useState(null)
  const items = roundup?.items || []
  if (!roundup) return null
  if (items.length === 0) {
    return <section className="roundup-section">
      <div className="roundup-heading">
        <span className="eyebrow social-eyebrow"><Zap size={13} /> ROUNDUP THEO GIỜ</span>
        <div className="roundup-switch">
          <button className={hours === 3 ? 'active' : ''} onClick={() => setHours(3)}>3 giờ</button>
          <button className={hours === 24 ? 'active' : ''} onClick={() => setHours(24)}>24 giờ</button>
        </div>
      </div>
      <div className="empty-panel compact-empty">
        <Clock3 size={26} />
        <h3>Chưa có bài roundup trong cửa sổ {hours === 24 ? '24 giờ' : '3 giờ'}</h3>
        <p>Pipeline chạy roundup mỗi giờ; hãy quay lại sau khi đã có đủ tín hiệu.</p>
      </div>
    </section>
  }

  const handleCopy = (item) => {
    navigator.clipboard.writeText(item.full_post_text || '').then(() => {
      setCopiedId(item.cluster_id)
      setTimeout(() => setCopiedId(null), 1800)
    })
  }

  const windowLabel = roundup.windowHours === 24 ? '24 giờ' : `${roundup.windowHours}h`
  return (
    <section className="roundup-section">
      <div className="roundup-heading">
        <span className="eyebrow social-eyebrow"><Zap size={13} /> ROUNDUP THEO GIỜ</span>
        <span className="roundup-caption">
          {roundup.cached ? 'Đã tổng hợp' : 'Tổng hợp mới'} · gom {items.reduce((s, i) => s + (i.n_posts || i.source_post_ids?.length || 1), 0)} bài Reddit
          ({windowLabel} gần nhất) thành {items.length} bài đặc trưng
        </span>
        <div className="roundup-switch">
          <button className={hours === 3 ? 'active' : ''} onClick={() => setHours(3)}>3 giờ</button>
          <button className={hours === 24 ? 'active' : ''} onClick={() => setHours(24)}>24 giờ</button>
        </div>
      </div>
      <div className="roundup-grid">
        {items.map(item => (
          <article className="roundup-card" key={item.cluster_id}>
            <div className="roundup-card-head">
              <span className="roundup-badge"><Zap size={12} /> {item.n_posts || (item.source_post_ids?.length || 1)} bài gộp</span>
              <span className="roundup-stats"><ThumbsUp size={12} /> {item.total_score} · <MessageCircle size={12} /> {item.total_comments}</span>
            </div>
            <h3 className="roundup-title">{item.title}</h3>
            <pre className="roundup-text">{item.full_post_text}</pre>
            {item.source_links?.length > 0 && (
              <div className="roundup-links">
                {item.source_links.map(link => (
                  <a
                    key={link.post_id}
                    className="roundup-chip"
                    href={`/post/${link.post_id}`}
                    target="_blank"
                    rel="noreferrer"
                    title={`Mở bài ${link.post_id} trong tab mới`}
                  >
                    <ExternalLink size={11} />
                    {link.subreddit ? `r/${link.subreddit}` : `Bài ${link.post_id.slice(0, 6)}`}
                  </a>
                ))}
              </div>
            )}
            <button
              className="button primary roundup-copy-btn"
              onClick={() => handleCopy(item)}
              disabled={!item.full_post_text}
            >
              {copiedId === item.cluster_id ? <><Check size={13} /> Đã copy!</> : <><Copy size={13} /> Copy bài Roundup</>}
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}

function SocialStudioPage() {
  const [roundup, setRoundup] = useState(null)
  const [roundupHours, setRoundupHours] = useState(24)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true); setError('')
    getJSON(`/api/social/roundup?hours=${roundupHours}&top=3`)
      .then(d => setRoundup({ ...d, windowHours: roundupHours }))
      .catch(err => { setError(err.message); setRoundup({ items: [], windowHours: roundupHours }) })
      .finally(() => setLoading(false))
  }
  useEffect(load, [roundupHours])

  return <>
    <section className="social-hero">
      <div className="social-hero-left">
        <span className="eyebrow social-eyebrow"><Share2 size={14} /> SOCIAL VIRAL CONTENT STUDIO</span>
        <h1>Bài đăng Social &amp;<br /><em>Bản tin Truyền thông</em></h1>
        <p>Gom các bài Reddit cùng chủ đề trong cửa sổ giờ thành bài đăng Social tổng hợp sẵn sàng xuất bản.</p>
      </div>
    </section>

    <RoundupStrip roundup={roundup} hours={roundupHours} setHours={setRoundupHours} />
    {loading && <Loading label="Đang tổng hợp bài đăng social…" />}
    {!loading && error && <ErrorState message={error} retry={load} />}
  </>
}

// ─────────────────────────────────────────────
// POST DETAIL PAGE (unchanged)
// ─────────────────────────────────────────────
function EvidenceAnalysis({ item }) {
  const view = analysisView(item)
  if (!item.analysis) return <section className="empty-panel"><Clock3 size={26} /><h3>Chưa có phân tích</h3>
    <p>Pipeline sẽ tự xử lý khi bài lọt vào nhóm tín hiệu ưu tiên.</p></section>
  return <section className="evidence-analysis">
    <div className="analysis-heading">
      <div>
        <span className="eyebrow">{view.v2 ? 'POST ANALYSIS V2' : 'V1 COMPATIBILITY'}</span>
        <h2>{view.title}</h2>
        <div className="heading-badge-wrap">
          <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} />
        </div>
      </div>
    </div>
    {!item.is_ai && <div className="local-warning"><AlertTriangle size={17} /><span>
      Đây là trích xuất local, không phải phân tích Gemini/OpenAI và chưa được LLM xác minh.</span></div>}
    {view.author && <article className="analysis-block"><span className="block-title-badge"><Target size={16} /> BÀI GỐC NÓI GÌ</span><p>{cleanMarkdownText(view.author)}</p></article>}
    {view.context && <article className="analysis-block"><span className="block-title-badge"><Layers3 size={16} /> BỐI CẢNH</span><p>{cleanMarkdownText(view.context)}</p></article>}
    {view.summary && <article className="analysis-block verdict-block"><span className="block-title-badge"><Sparkles size={16} /> KẾT LUẬN</span><p>{cleanMarkdownText(view.summary)}</p></article>}
    {view.keyPoints.length > 0 && <article className="analysis-block">
      <span className="block-title-badge"><Lightbulb size={16} /> CLAIM VÀ BẰNG CHỨNG</span>
      <div className="evidence-list">{view.keyPoints.map((point, index) => <div className={`evidence-row ${point.stance || ''}`} key={index}>
        <div className="evidence-claim-head"><span className="claim-bullet">•</span><b>{cleanMarkdownText(point.text)}</b></div>
        {point.evidence && <div className="evidence-quote-box"><p>{cleanMarkdownText(point.evidence)}</p></div>}
        {point.ids.length > 0 && <div className="evidence-citations">{point.ids.map(id => <a href={`#comment-${id}`} key={id} className="citation-pill"><MessageCircle size={11} /> Comment #{id}</a>)}</div>}
      </div>)}</div>
    </article>}
    {view.actions.length > 0 && <article className="analysis-block action-block"><span className="block-title-badge"><CheckCircle2 size={16} /> VIỆC NÊN LÀM</span>
      <ul>{view.actions.map((action, index) => <li key={index}>{cleanMarkdownText(action)}</li>)}</ul></article>}
    {view.resources.length > 0 && <article className="analysis-block"><span className="block-title-badge"><ExternalLink size={16} /> TÀI NGUYÊN TRONG PHÂN TÍCH</span>
      <div className="resource-grid">{view.resources.map((resource, index) => <div className="resource-card" key={index}>
        <div className="resource-topline"><span className="resource-type-tag">{resource.kind || 'resource'}</span>{resource.confidence && <span className="resource-domain-tag">Độ tin cậy: {resource.confidence}</span>}</div>
        <h4 className="resource-card-title">{cleanMarkdownText(resource.name)}</h4>
        {resource.description && <p className="resource-snippet">{cleanMarkdownText(resource.description)}</p>}
        {resource.url && <a href={resource.url} target="_blank" rel="noreferrer" className="resource-link-btn">Mở nguồn <ExternalLink size={12} /></a>}
      </div>)}</div></article>}
    {(view.warnings.length > 0 || view.questions.length > 0) && <div className="two-column-blocks">
      {view.warnings.length > 0 && <article className="analysis-block warning-block"><span className="block-title-badge"><AlertTriangle size={16} /> CẢNH BÁO / PHẢN BIỆN</span><ul>{view.warnings.map((text, index) => <li key={index}>{cleanMarkdownText(text)}</li>)}</ul></article>}
      {view.questions.length > 0 && <article className="analysis-block"><span className="block-title-badge"><MessageCircle size={16} /> CÒN BỎ NGỎ</span><ul>{view.questions.map((text, index) => <li key={index}>{cleanMarkdownText(text)}</li>)}</ul></article>}
    </div>}
    <footer className="method-note">{view.analysis.methodology_note} · Tạo {dateTime(item.generated_at)}</footer>
  </section>
}

function PostDetail({ postId, markRead, isSaved, toggleSave }) {
  const navigate = useNavigate()
  const [post, setPost] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    markRead(postId)
    getJSON(`/api/posts/${encodeURIComponent(postId)}`).then(item => {
      setPost(item)
      document.title = `${item.analysis?.topic || item.title} — Reddit Radar`
    }).catch(err => setError(err.message))
  }, [postId])

  const handleCopy = () => {
    if (!post) return
    const view = analysisView(post)
    const text = `📌 ${view.title}\n\n${view.summary || ''}\n\nNguồn: ${post.reddit_url || 'Reddit Radar'}`
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleExportMarkdown = () => {
    if (!post) return
    setExporting(true)
    getJSON(`/api/posts/${encodeURIComponent(postId)}/export?format=markdown`).then(res => {
      if (res?.markdown) {
        const blob = new Blob([res.markdown], { type: 'text/markdown;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `reddit-radar-${postId}.md`
        a.click()
        URL.revokeObjectURL(url)
      }
    }).catch(err => alert("Không xuất được Markdown: " + err.message))
      .finally(() => setExporting(false))
  }

  if (error) return <ErrorState message={error} />
  if (!post) return <Loading label="Đang mở hồ sơ bằng chứng…" />
  const title = post.analysis?.topic || post.article_title || post.title

  const evidenceIds = new Set([
    ...(post.analysis?.key_points || []).flatMap(point => point.comment_ids || []),
    ...(post.analysis?.opinion_groups || []).flatMap(group => group.comment_ids || []),
  ])

  return <>
    <button className="back-link btn-back-link" onClick={() => window.history.length > 1 ? window.history.back() : navigate('/')}>
      <ArrowLeft size={15} /> Quay lại danh sách
    </button>
    <article className="post-header">
      <div className="card-topline">
        <div className="topline-tags">
          <span className="domain-label"><DomainIcon domain={post.domain_id} size={14} /> {post.domain_name}</span>
          <span className="sub-tag">r/{post.subreddit}</span>
        </div>
        <button
          className={`detail-star-btn ${isSaved ? 'saved' : ''}`}
          onClick={() => toggleSave(postId)}
          title={isSaved ? "Đã lưu (Bấm để bỏ lưu)" : "Lưu bài viết này xem sau"}
        >
          <Star size={15} className={isSaved ? 'fill-star' : ''} />
          <span>{isSaved ? 'Đã lưu' : 'Lưu bài'}</span>
        </button>
      </div>
      <h1>{title}</h1>
      {post.analysis?.topic && post.title !== post.analysis.topic && <p className="original-title">Tiêu đề gốc: {post.title}</p>}
      <div className="post-meta">
        <span className="meta-item"><Clock3 size={14} /> {dateTime(post.created_utc)}</span>
        <span className="meta-item"><ThumbsUp size={14} /> {compactNumber.format(post.score || 0)}</span>
        <span className="meta-item"><MessageCircle size={14} /> {post.num_comments || 0} bình luận</span>
      </div>
      <div className="post-actions">
        {post.reddit_url && <a href={post.reddit_url} target="_blank" rel="noreferrer" className="button primary">Reddit gốc <ExternalLink size={14} /></a>}
        {(post.article_final_url || post.url) && <a href={post.article_final_url || post.url} target="_blank" rel="noreferrer" className="button secondary">Nguồn ngoài <ExternalLink size={14} /></a>}

        <button
          className={`button secondary ${isSaved ? 'saved' : ''}`}
          onClick={() => toggleSave(postId)}
          title={isSaved ? "Bỏ lưu bài" : "Lưu xem sau"}
        >
          <Star size={14} className={isSaved ? 'fill-star' : ''} />
          <span>{isSaved ? 'Đã lưu' : 'Lưu xem sau'}</span>
        </button>

        <button className="button secondary" onClick={handleCopy} title="Sao chép tóm tắt bài viết">
          {copied ? <Check size={14} className="green-icon" /> : <Copy size={14} />}
          <span>{copied ? 'Đã chép tóm tắt' : 'Copy tóm tắt'}</span>
        </button>

        <button className="button secondary" onClick={handleExportMarkdown} disabled={exporting} title="Tải bài viết dạng Markdown">
          <Download size={14} />
          <span>{exporting ? 'Đang xuất...' : 'Export Markdown'}</span>
        </button>
      </div>
    </article>

    <nav className="quick-toc" aria-label="Điều hướng nhanh">
      <span className="toc-label"><ListTree size={14} /> Mục lục nhanh:</span>
      <a href="#sec-analysis">Đúc kết &amp; Bằng chứng</a>
      {(post.extracted_resources || []).length > 0 && <a href="#sec-resources">Tài nguyên ({post.extracted_resources.length})</a>}
      {(post.article_body || post.selftext) && <a href="#sec-source">Nội dung gốc</a>}
      <a href="#sec-comments">Thảo luận ({post.comments?.length || 0})</a>

      <button
        className={`toc-star-btn ${isSaved ? 'saved' : ''}`}
        onClick={() => toggleSave(postId)}
        title={isSaved ? "Đã lưu bài" : "Lưu bài viết này"}
      >
        <Star size={13} className={isSaved ? 'fill-star' : ''} />
        <span>{isSaved ? 'Đã lưu' : 'Lưu bài'}</span>
      </button>
    </nav>

    <div id="sec-analysis">
      <EvidenceAnalysis item={post} />
    </div>

    {(post.extracted_resources || []).length > 0 && <section id="sec-resources" className="source-section"><div className="section-heading compact"><div><span className="eyebrow">EXTRACTED EVIDENCE</span><h2>Liên kết đã bóc tách ({post.extracted_resources.length})</h2></div></div>
      <div className="resource-grid">{post.extracted_resources.map((resource, idx) => {
        const titleText = cleanMarkdownText(resource.title || resource.name || resource.url)
        const domainText = resource.domain ? resource.domain.replace(/^https?:\/\//, '').replace(/\/.*$/, '') : ''
        return <div className="resource-card" key={resource.resource_id || idx}>
          <div className="resource-topline">
            <span className="resource-type-tag">{resource.resource_type || resource.kind || 'link'}</span>
            {domainText && <span className="resource-domain-tag">{domainText}</span>}
          </div>
          <h4 className="resource-card-title">{titleText}</h4>
          {resource.context_snippet && <p className="resource-snippet">{cleanMarkdownText(resource.context_snippet)}</p>}
          {resource.url && <a href={resource.url} target="_blank" rel="noreferrer" className="resource-link-btn">Mở nguồn <ExternalLink size={12} /></a>}
        </div>
      })}</div></section>}

    {(post.article_body || post.selftext) && <section id="sec-source" className="source-section"><div className="section-heading compact"><div><span className="eyebrow">SOURCE TEXT</span><h2>Nội dung gốc</h2></div></div>
      <div className="source-prose">{cleanMarkdownText(post.article_body || post.selftext)}</div></section>}

    <section id="sec-comments" className="source-section comments-section">
      <div className="section-heading compact">
        <div><span className="eyebrow">DISCUSSION EVIDENCE</span><h2>{post.comments?.length || 0} bình luận đã crawl</h2></div>
      </div>
      {post.comments?.length ? (
        <div className="comment-list">
          {post.comments.map(comment => (
            <article id={`comment-${comment.comment_id}`} className={`comment-card ${evidenceIds.has(comment.comment_id) ? 'cited' : ''}`} key={comment.comment_id}>
              <div className="comment-meta">
                <b>u/{comment.author_name || '[deleted]'}</b>
                <span>{comment.score || 0} điểm</span>
                <span>{relativeTime(comment.created_utc)}</span>
                {evidenceIds.has(comment.comment_id) && <em><Sparkles size={11} /> Được trích dẫn</em>}
              </div>
              <p>{cleanMarkdownText(comment.body)}</p>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-panel compact-empty">
          <MessageCircle size={24} />
          <p>Chưa có comment trong database.</p>
        </div>
      )}
    </section>
  </>
}

function Footer({ health }) {
  return <footer className="footer"><div><Logo /><p>Dữ liệu cộng đồng thành tri thức có thể kiểm chứng.</p></div>
    <div><span className={`footer-state ${health?.status || 'unknown'}`}><i /> {health?.status === 'healthy' ? 'Pipeline healthy' : 'Pipeline degraded'}</span>
      <small>Reddit Radar · Asia/Ho_Chi_Minh</small></div></footer>
}

export default function App() {
  return <BrowserRouter><AppShell /></BrowserRouter>
}

function AppShell() {
  const { pathname } = useLocation()
  const [health, setHealth] = useState(null)
  const [buzzOpen, setBuzzOpen] = useState(false)

  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('rr_theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    } catch {
      return 'light'
    }
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('rr_theme', theme) } catch {}
  }, [theme])

  const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark')

  const [savedSet, setSavedSet] = useState(() => {
    try {
      const stored = localStorage.getItem('rr_bookmarks')
      return new Set(stored ? JSON.parse(stored) : [])
    } catch {
      return new Set()
    }
  })

  const [readSet, setReadSet] = useState(() => {
    try {
      const stored = localStorage.getItem('rr_read_posts')
      return new Set(stored ? JSON.parse(stored) : [])
    } catch {
      return new Set()
    }
  })

  useEffect(() => {
    getJSON('/api/user/bookmarks').then(res => {
      if (res?.bookmarks) {
        setSavedSet(prev => {
          const merged = new Set([...prev, ...res.bookmarks])
          try { localStorage.setItem('rr_bookmarks', JSON.stringify([...merged])) } catch {}
          return merged
        })
      }
    }).catch(() => {})

    getJSON('/api/user/read').then(res => {
      if (res?.read) {
        setReadSet(prev => {
          const merged = new Set([...prev, ...res.read])
          try { localStorage.setItem('rr_read_posts', JSON.stringify([...merged])) } catch {}
          return merged
        })
      }
    }).catch(() => {})
  }, [])

  const toggleSave = (postId) => {
    setSavedSet(prev => {
      const next = new Set(prev)
      if (next.has(postId)) next.delete(postId)
      else next.add(postId)
      try { localStorage.setItem('rr_bookmarks', JSON.stringify([...next])) } catch {}
      return next
    })
    fetch(`/api/user/bookmarks/${encodeURIComponent(postId)}`, { method: 'POST' }).catch(() => {})
  }

  const markRead = (postId) => {
    if (!postId) return
    setReadSet(prev => {
      if (prev.has(postId)) return prev
      const next = new Set(prev)
      next.add(postId)
      try { localStorage.setItem('rr_read_posts', JSON.stringify([...next])) } catch {}
      return next
    })
    fetch(`/api/user/read/${encodeURIComponent(postId)}`, { method: 'POST' }).catch(() => {})
  }

  const postMatch = pathname.match(/^\/post\/([^/]+)/)
  const active = postMatch ? 'feed'
    : pathname === '/saved' ? 'saved'
    : pathname === '/social' ? 'social'
    : ['/trends', '/radar', '/signals'].includes(pathname) ? 'trends'
    : 'feed'

  const currentPostId = postMatch ? decodeURIComponent(postMatch[1]) : null

  useEffect(() => {
    getJSON('/api/health').then(setHealth).catch(() => setHealth({ status: 'degraded' }))
  }, [pathname])

  useEffect(() => {
    if (!currentPostId) {
      document.title = 'Reddit Radar — Technology Intelligence'
    }
  }, [currentPostId])

  return <div className="app-shell">
    <Header active={active} health={health} savedCount={savedSet.size} theme={theme} toggleTheme={toggleTheme} onOpenBuzz={() => setBuzzOpen(true)} />
    <main className={`page-shell ${postMatch ? 'detail-shell' : ''}`}>
      <Routes>
        <Route path="/" element={<FeedPage health={health} savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="/trends" element={<TrendsPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="/radar" element={<TrendsPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="/signals" element={<TrendsPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="/social" element={<SocialStudioPage />} />
        <Route path="/saved" element={<SavedPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="/post/:postId" element={<PostDetailRoute markRead={markRead} savedSet={savedSet} toggleSave={toggleSave} />} />
        <Route path="/page/:pageNum" element={<FeedPage health={health} savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
    <Footer health={health} />
    <AIBuzzModal isOpen={buzzOpen} onClose={() => setBuzzOpen(false)} />
  </div>
}

function PostDetailRoute({ markRead, savedSet, toggleSave }) {
  const { postId } = useParams()
  return <PostDetail postId={postId} markRead={markRead} isSaved={savedSet.has(postId)} toggleSave={toggleSave} />
}
