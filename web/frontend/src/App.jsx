import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowLeft, ArrowRight, BarChart3, BookOpen,
  Bookmark, BookmarkCheck, Bot, BrainCircuit, CheckCircle2, ChevronRight, Clock3, Code2,
  Copy, Check, ExternalLink, Eye, EyeOff, FileText, Filter, Gauge, Globe2, Layers3, Lightbulb,
  Menu, MessageCircle, Radio, Search, ShieldCheck, Sparkles, Star, Target,
  ThumbsUp, TrendingUp, X,
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

function AppLink({ href, children, className = '', onNavigate, onClick, ...props }) {
  return <a href={href} className={className} onClick={event => {
    onClick?.(event)
    if (!event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) {
      event.preventDefault()
      navigate(href)
      onNavigate?.()
    }
  }} {...props}>{children}</a>
}

function Logo() {
  return <AppLink href="/" className="logo" aria-label="Reddit Radar · Hôm nay">
    <span className="logo-mark"><Activity size={18} /></span>
    <span><b>Reddit</b> Radar</span>
  </AppLink>
}

const NAV_ITEMS = [
  ['today', '/', 'Hôm nay', Sparkles],
  ['knowledge', '/knowledge', 'Kho tri thức', BrainCircuit],
  ['saved', '/saved', 'Đã lưu', Star],
  ['radar', '/radar', 'Radar', Radio],
]

function Header({ active, health, savedCount = 0 }) {
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

function ErrorState({ message, retry }) {
  return <div className="state-panel error-state">
    <AlertTriangle size={28} /><h2>Không tải được dữ liệu</h2><p>{message}</p>
    {retry && <button className="button primary" onClick={retry}>Thử lại</button>}
  </div>
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
  return <article className={`analysis-card ${featured ? 'featured' : ''} ${isRead ? 'read-card' : ''}`}>
    <div className="card-topline">
      <span className="domain-label"><DomainIcon domain={item.domain_id} size={14} />
        {item.domain_name || 'Khác'}
      </span>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />
    </div>
    <h3>
      <AppLink href={`/post/${item.post_id}`} onClick={() => markRead(item.post_id)}>
        {view.title}
      </AppLink>
    </h3>
    {view.summary && <p className="card-summary">{view.summary}</p>}
    {view.keyPoints[0] && <div className="key-preview">
      <Lightbulb size={15} /><span>{view.keyPoints[0].text}</span>
    </div>}
    
    <CardActionsBar item={item} isSaved={isSaved} toggleSave={toggleSave} isRead={isRead} markRead={markRead} />

    <footer className="card-footer">
      <span>r/{item.subreddit || '?'}</span>
      <span><ThumbsUp size={13} /> {compactNumber.format(item.score || 0)}</span>
      <span><MessageCircle size={13} /> {compactNumber.format(item.num_comments || item.comment_count || 0)}</span>
      <span>{relativeTime(item.generated_at)}</span>
      <AppLink href={`/post/${item.post_id}`} className="text-link" onClick={() => markRead(item.post_id)}>
        Xem bằng chứng <ArrowRight size={14} />
      </AppLink>
    </footer>
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
        <h2>{payload.title || digest.title || 'Technology intelligence briefing'}</h2>
      </div>
      <ProviderBadge provider={digest.provider} model={digest.model} isAI={digest.is_ai} />
    </div>
    <p className="digest-summary">{payload.executive_summary || digest.executive_summary}</p>
    {message && <div className="digest-notice"><AlertTriangle size={15} /> {message}</div>}
    {stories.length > 0 && <div className="digest-stories">
      {stories.slice(0, 4).map((story, index) => {
        const postId = story.source_post_ids?.[0]
        const content = <><span>{story.change_type || story.category || 'update'}</span>
          <b>{story.headline}</b><small>{story.summary || story.why_it_matters}</small></>
        return postId
          ? <AppLink href={`/post/${postId}`} className="digest-story" key={`${postId}-${index}`}>{content}<ChevronRight size={15} /></AppLink>
          : <div className="digest-story" key={index}>{content}</div>
      })}
    </div>}
    {watchlist.length > 0 && <div className="watchline"><Gauge size={15} />
      <b>Cần theo dõi:</b> {watchlist.slice(0, 3).map(item => item.topic || item).join(' · ')}
    </div>}
    <footer className="digest-foot">Tạo {dateTime(digest.generated_at)} từ {digest.source_count || 0} nguồn đã crawl</footer>
  </section>
}

function SmartFilterToolbar({ minComments, setMinComments, hideRead, setHideRead, sortBy, setSortBy }) {
  return <div className="smart-toolbar">
    <div className="toolbar-left">
      <button
        className={`toolbar-pill ${minComments === 2 ? 'active' : ''}`}
        onClick={() => setMinComments(minComments === 2 ? 0 : 2)}
      >
        <MessageCircle size={13} />
        <span>Ưu tiên bài >2 bình luận</span>
      </button>

      <button
        className={`toolbar-pill ${hideRead ? 'active' : ''}`}
        onClick={() => setHideRead(!hideRead)}
      >
        {hideRead ? <EyeOff size={13} /> : <Eye size={13} />}
        <span>{hideRead ? 'Đang ẩn bài đã đọc' : 'Ẩn bài đã đọc'}</span>
      </button>
    </div>

    <div className="toolbar-right">
      <span className="sort-label"><Filter size={12} /> Sắp xếp:</span>
      <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="sort-select">
        <option value="value">⭐ Giá trị cao (Vote + Comment)</option>
        <option value="comments">💬 Nhiều bình luận nhất</option>
        <option value="score">👍 Điểm vote cao nhất</option>
        <option value="time">🕐 Mới nhất</option>
      </select>
    </div>
  </div>
}

function TodayPage({ health, savedSet, toggleSave, readSet, markRead }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [minComments, setMinComments] = useState(2)
  const [hideRead, setHideRead] = useState(false)
  const [sortBy, setSortBy] = useState('value')

  const load = () => {
    setLoading(true); setError('')
    getJSON('/api/today?period=day&limit=15').then(setData)
      .catch(err => setError(err.message)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const processedHighlights = useMemo(() => {
    if (!data?.highlights) return []
    let list = [...data.highlights]

    // 1. Filter out 0-1 comment posts if minComments active
    if (minComments > 0) {
      list = list.filter(i => (i.num_comments || i.comment_count || 0) >= minComments)
    }

    // 2. Filter out read posts if hideRead active
    if (hideRead) {
      list = list.filter(i => !readSet.has(i.post_id))
    }

    // 3. Sort
    list.sort((a, b) => {
      if (sortBy === 'value') return computePostValue(b) - computePostValue(a)
      if (sortBy === 'comments') return (b.num_comments || 0) - (a.num_comments || 0)
      if (sortBy === 'score') return (b.score || 0) - (a.score || 0)
      if (sortBy === 'time') return (b.generated_at || 0) - (a.generated_at || 0)
      return 0
    })

    return list
  }, [data, minComments, hideRead, sortBy, readSet])

  if (loading) return <Loading label="Đang chuẩn bị bản tin hôm nay…" />
  if (error) return <ErrorState message={error} retry={load} />
  const counts = health?.counts || {}

  return <>
    <section className="today-hero">
      <div className="hero-copy">
        <span className="overline"><i /> TECHNOLOGY INTELLIGENCE · HÔM NAY</span>
        <h1>Điều gì đáng biết,<br /><em>trước khi bạn bắt đầu ngày mới?</em></h1>
        <p>Reddit Radar ưu tiên các bài viết có nhiều lượt thảo luận, xếp hạng theo giá trị thực tế và hỗ trợ lưu bài đọc sau.</p>
        <span className="hero-time"><Clock3 size={15} /> Dữ liệu thu thập {relativeTime(health?.stages?.collector?.last_success_at)}</span>
      </div>
      <div className="hero-metrics">
        <Metric icon={Radio} value={compactNumber.format(counts.posts || 0)} label="tín hiệu đã lưu" tone="mint" />
        <Metric icon={MessageCircle} value={compactNumber.format(counts.comments || 0)} label="bình luận đã đọc" tone="orange" />
        <Metric icon={Bot} value={fullNumber.format(counts.analyses_ai || 0)} label="phân tích LLM" tone="blue" />
        <Metric icon={FileText} value={fullNumber.format(counts.analyses_local || 0)} label="trích xuất local" tone="violet" />
      </div>
    </section>

    <DigestPanel digest={data?.digest} provisional={data?.provisional} message={data?.message} />

    <section className="section-heading">
      <div>
        <span className="eyebrow">ĐỌC TRƯỚC</span>
        <h2>Những phân tích đáng chú ý</h2>
        <p>Xếp hạng theo điểm tương tác, số bình luận thảo luận và bằng chứng thực tế.</p>
      </div>
      <AppLink href="/knowledge" className="button secondary">Mở kho tri thức <ArrowRight size={15} /></AppLink>
    </section>

    <SmartFilterToolbar
      minComments={minComments} setMinComments={setMinComments}
      hideRead={hideRead} setHideRead={setHideRead}
      sortBy={sortBy} setSortBy={setSortBy}
    />

    {processedHighlights.length ? (
      <div className="highlight-grid">
        {processedHighlights.map((item, index) => (
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
        <BrainCircuit size={28} />
        <h3>Không có bài viết phù hợp bộ lọc</h3>
        <p>Thử tắt ẩn bài đã đọc hoặc mở rộng bộ lọc số bình luận.</p>
      </div>
    )}

    <section className="today-signals">
      <div className="section-heading compact">
        <div><span className="eyebrow">RADAR LIVE</span><h2>Tín hiệu đang chuyển động</h2></div>
        <AppLink href="/radar" className="text-link">Xem toàn bộ <ArrowRight size={14} /></AppLink>
      </div>
      <div className="mini-signal-list">
        {(data?.signals || []).slice(0, 6).map((item, index) => (
          <MiniSignal
            item={item}
            rank={index + 1}
            key={item.post_id}
            isSaved={savedSet.has(item.post_id)}
            toggleSave={toggleSave}
            isRead={readSet.has(item.post_id)}
            markRead={markRead}
          />
        ))}
      </div>
    </section>
  </>
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

function KnowledgePage({ savedSet, toggleSave, readSet, markRead }) {
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('all')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [minComments, setMinComments] = useState(2)
  const [hideRead, setHideRead] = useState(false)
  const [sortBy, setSortBy] = useState('value')
  const limit = 12

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true); setError('')
      getJSON(withQuery('/api/knowledge/feed', { q: query.trim(), domain, limit, offset }))
        .then(setData).catch(err => setError(err.message)).finally(() => setLoading(false))
    }, 180)
    return () => window.clearTimeout(timer)
  }, [query, domain, offset])

  useEffect(() => setOffset(0), [query, domain])

  const filteredItems = useMemo(() => {
    if (!data?.items) return []
    let list = [...data.items]

    if (minComments > 0) {
      list = list.filter(i => (i.num_comments || i.comment_count || 0) >= minComments)
    }
    if (hideRead) {
      list = list.filter(i => !readSet.has(i.post_id))
    }

    list.sort((a, b) => {
      if (sortBy === 'value') return computePostValue(b) - computePostValue(a)
      if (sortBy === 'comments') return (b.num_comments || 0) - (a.num_comments || 0)
      if (sortBy === 'score') return (b.score || 0) - (a.score || 0)
      if (sortBy === 'time') return (b.generated_at || 0) - (a.generated_at || 0)
      return 0
    })

    return list
  }, [data, minComments, hideRead, sortBy, readSet])

  return <>
    <section className="page-intro">
      <div><span className="eyebrow">EVIDENCE → INTELLIGENCE</span><h1>Kho tri thức</h1>
        <p>Mỗi bài được làm sạch, ưu tiên bài viết có thảo luận sôi nổi (>2 bình luận). Bạn có thể bấm lưu bài để đọc lại sau.</p></div>
      <SearchBox value={query} setValue={setQuery} placeholder="Tìm chủ đề, subreddit, công nghệ…" />
    </section>

    <DomainChips domains={data?.domains || []} selected={domain} setSelected={setDomain} total={data?.total || 0} />

    <SmartFilterToolbar
      minComments={minComments} setMinComments={setMinComments}
      hideRead={hideRead} setHideRead={setHideRead}
      sortBy={sortBy} setSortBy={setSortBy}
    />

    <div className="result-line">
      <span><b>{filteredItems.length}</b> phân tích phù hợp</span>
      <span>Xếp hạng theo giá trị & bình luận</span>
    </div>

    {error ? <ErrorState message={error} /> : loading ? <Loading label="Đang lọc kho tri thức…" />
      : filteredItems.length ? <>
        <div className="knowledge-grid">
          {filteredItems.map(item => (
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
        <div className="pagination">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><ArrowLeft size={14} /> Trước</button>
          <span>{offset + 1}–{Math.min(offset + data.count, data.total)} / {data.total}</span>
          <button disabled={!data.has_more} onClick={() => setOffset(offset + limit)}>Sau <ArrowRight size={14} /></button>
        </div>
      </> : <div className="empty-panel"><Search size={28} /><h3>Không có kết quả phù hợp</h3><p>Thử đổi từ khóa, lĩnh vực hoặc tắt ẩn bài đã đọc.</p></div>}
  </>
}

function SavedPage({ savedSet, toggleSave, readSet, markRead }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getJSON('/api/knowledge/feed?limit=100').then(res => {
      const items = (res?.items || []).filter(item => savedSet.has(item.post_id))
      setData(items)
    }).finally(() => setLoading(false))
  }, [savedSet])

  return <>
    <section className="page-intro">
      <div>
        <span className="eyebrow">DANH SÁCH CÁ NHÂN</span>
        <h1>Bài viết đã lưu / Xem sau</h1>
        <p>Tất cả các bài viết bạn bấm lưu xem sau hoặc yêu thích sẽ xuất hiện ở đây.</p>
      </div>
    </section>

    {loading ? <Loading label="Đang danh sách bài đã lưu…" />
      : data.length ? (
        <div className="knowledge-grid">
          {data.map(item => (
            <AnalysisCard
              item={item}
              key={item.post_id}
              isSaved={true}
              toggleSave={toggleSave}
              isRead={readSet.has(item.post_id)}
              markRead={markRead}
            />
          ))}
        </div>
      ) : (
        <div className="empty-panel">
          <Star size={32} className="empty-star" />
          <h3>Chưa có bài viết nào được lưu</h3>
          <p>Bấm vào nút <b>Lưu</b> ở góc dưới bài viết bất kỳ để xem lại sau tại đây.</p>
        </div>
      )}
  </>
}

function MiniSignal({ item, rank, isSaved, toggleSave, isRead, markRead }) {
  return <article className={`mini-signal ${isRead ? 'read-card' : ''}`}>
    <span className="signal-rank">{String(rank).padStart(2, '0')}</span>
    <div className="signal-main">
      <div className="card-topline"><span className="domain-label"><DomainIcon domain={item.domain_id} size={13} /> {item.domain_name}</span>
        {item.provider && <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />}</div>
      <h3>
        <AppLink href={`/post/${item.post_id}`} onClick={() => markRead(item.post_id)}>
          {item.analysis?.topic || item.title}
        </AppLink>
      </h3>
      <div className="signal-meta">
        <span>r/{item.subreddit}</span>
        <span><ThumbsUp size={13} /> {compactNumber.format(item.latest_score || item.score || 0)}</span>
        <span><MessageCircle size={13} /> {compactNumber.format(item.latest_comments || item.num_comments || 0)}</span>
        <span>{relativeTime(item.created_utc)}</span>
        <button
          className={`mini-bookmark-btn ${isSaved ? 'saved' : ''}`}
          onClick={() => toggleSave(item.post_id)}
          title={isSaved ? 'Đã lưu' : 'Lưu xem sau'}
        >
          <Star size={12} />
        </button>
      </div>
    </div>
    <div className="trend-box"><TrendingUp size={15} /><b>{item.trend_score ?? '—'}</b><small>trend</small></div>
  </article>
}

function RadarPage({ savedSet, toggleSave, readSet, markRead }) {
  const [period, setPeriod] = useState('day')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [minComments, setMinComments] = useState(2)

  const load = () => {
    setLoading(true); setError('')
    getJSON(withQuery('/api/trending', { period, limit: 100 })).then(setData)
      .catch(err => setError(err.message)).finally(() => setLoading(false))
  }
  useEffect(load, [period])

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('vi')
    return (data?.items || []).filter(item => {
      const matchesDomain = domain === 'all' || item.domain_id === domain
      const comments = item.latest_comments || item.num_comments || 0
      const matchesComments = minComments === 0 || comments >= minComments
      const text = `${item.title || ''} ${item.subreddit || ''} ${item.domain_name || ''} ${item.analysis?.topic || ''}`.toLocaleLowerCase('vi')
      return matchesDomain && matchesComments && (!normalized || text.includes(normalized))
    })
  }, [data, domain, query, minComments])

  return <>
    <section className="page-intro radar-intro">
      <div><span className="eyebrow">RAW SIGNAL EXPLORER</span><h1>Radar</h1>
        <p>Xếp hạng post theo độ mới, tương tác và vận tốc. Tự động ưu tiên bài có thảo luận giá trị.</p></div>
      <SearchBox value={query} setValue={setQuery} placeholder="Lọc tiêu đề hoặc subreddit…" />
    </section>

    <div className="radar-controls">
      <div className="period-switch">{PERIODS.map(([value, label]) => <button key={value}
        className={period === value ? 'active' : ''} onClick={() => { setPeriod(value); setDomain('all') }}>{label}</button>)}</div>
    </div>

    <DomainChips domains={data?.domains || []} selected={domain} setSelected={setDomain} total={data?.items?.length || 0} />

    <div className="smart-toolbar compact-toolbar">
      <button
        className={`toolbar-pill ${minComments === 2 ? 'active' : ''}`}
        onClick={() => setMinComments(minComments === 2 ? 0 : 2)}
      >
        <MessageCircle size={13} />
        <span>Ưu tiên bài >2 bình luận</span>
      </button>
    </div>

    <div className="result-line"><span><b>{filtered.length}</b> tín hiệu</span><span>Dữ liệu tạo {relativeTime(data?.generated_at)}</span></div>

    {error ? <ErrorState message={error} retry={load} /> : loading ? <Loading label="Đang đo vận tốc tín hiệu…" />
      : filtered.length ? (
        <div className="radar-list">
          {filtered.map((item, index) => (
            <MiniSignal
              item={item}
              rank={index + 1}
              key={item.post_id}
              isSaved={savedSet.has(item.post_id)}
              toggleSave={toggleSave}
              isRead={readSet.has(item.post_id)}
              markRead={markRead}
            />
          ))}
        </div>
      ) : <div className="empty-panel"><Search size={28} /><h3>Không tìm thấy tín hiệu</h3><p>Thử đổi từ khóa, lĩnh vực hoặc khoảng thời gian.</p></div>}
  </>
}

function EvidenceAnalysis({ item }) {
  const view = analysisView(item)
  if (!item.analysis) return <section className="empty-panel"><Clock3 size={26} /><h3>Chưa có phân tích</h3>
    <p>Pipeline sẽ tự xử lý khi bài lọt vào nhóm tín hiệu ưu tiên.</p></section>
  return <section className="evidence-analysis">
    <div className="analysis-heading">
      <div><span className="eyebrow">{view.v2 ? 'POST ANALYSIS V2' : 'V1 COMPATIBILITY'}</span><h2>{view.title}</h2></div>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} />
    </div>
    {!item.is_ai && <div className="local-warning"><AlertTriangle size={17} /><span>
      Đây là trích xuất local, không phải phân tích Gemini/OpenAI và chưa được LLM xác minh.</span></div>}
    {view.author && <article className="analysis-block"><span><Target size={16} /> Bài gốc nói gì</span><p>{view.author}</p></article>}
    {view.context && <article className="analysis-block"><span><Layers3 size={16} /> Bối cảnh</span><p>{view.context}</p></article>}
    {view.summary && <article className="analysis-block verdict-block"><span><Sparkles size={16} /> Kết luận</span><p>{view.summary}</p></article>}
    {view.keyPoints.length > 0 && <article className="analysis-block"><span><Lightbulb size={16} /> Claim và bằng chứng</span>
      <div className="evidence-list">{view.keyPoints.map((point, index) => <div className={`evidence-row ${point.stance || ''}`} key={index}>
        <b>{point.text}</b>{point.evidence && <p>{point.evidence}</p>}
        {point.ids.length > 0 && <div>{point.ids.map(id => <a href={`#comment-${id}`} key={id}>#{id}</a>)}</div>}
      </div>)}</div></article>}
    {view.actions.length > 0 && <article className="analysis-block action-block"><span><CheckCircle2 size={16} /> Việc nên làm</span>
      <ul>{view.actions.map((action, index) => <li key={index}>{action}</li>)}</ul></article>}
    {view.resources.length > 0 && <article className="analysis-block"><span><ExternalLink size={16} /> Tài nguyên trong phân tích</span>
      <div className="resource-grid">{view.resources.map((resource, index) => <div className="resource-card" key={index}>
        <small>{resource.kind || 'resource'}{resource.confidence ? ` · ${resource.confidence}` : ''}</small><b>{resource.name}</b>
        <p>{resource.description}</p>{resource.url && <a href={resource.url} target="_blank" rel="noreferrer">Mở nguồn <ExternalLink size={12} /></a>}
      </div>)}</div></article>}
    {(view.warnings.length > 0 || view.questions.length > 0) && <div className="two-column-blocks">
      {view.warnings.length > 0 && <article className="analysis-block warning-block"><span><AlertTriangle size={16} /> Cảnh báo / phản biện</span><ul>{view.warnings.map((text, index) => <li key={index}>{text}</li>)}</ul></article>}
      {view.questions.length > 0 && <article className="analysis-block"><span><MessageCircle size={16} /> Còn bỏ ngỏ</span><ul>{view.questions.map((text, index) => <li key={index}>{text}</li>)}</ul></article>}
    </div>}
    <footer className="method-note">{view.analysis.methodology_note} · Tạo {dateTime(item.generated_at)}</footer>
  </section>
}

function PostDetail({ postId, markRead }) {
  const [post, setPost] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    markRead(postId)
    getJSON(`/api/posts/${encodeURIComponent(postId)}`).then(item => {
      setPost(item)
      document.title = `${item.analysis?.topic || item.title} — Reddit Radar`
    }).catch(err => setError(err.message))
  }, [postId])

  if (error) return <ErrorState message={error} />
  if (!post) return <Loading label="Đang mở hồ sơ bằng chứng…" />
  const title = post.analysis?.topic || post.article_title || post.title
  const evidenceIds = new Set([
    ...(post.analysis?.key_points || []).flatMap(point => point.comment_ids || []),
    ...(post.analysis?.opinion_groups || []).flatMap(group => group.comment_ids || []),
  ])
  return <>
    <AppLink href="/knowledge" className="back-link"><ArrowLeft size={15} /> Kho tri thức</AppLink>
    <article className="post-header">
      <div className="card-topline"><span className="domain-label"><DomainIcon domain={post.domain_id} size={14} /> {post.domain_name}</span><span>r/{post.subreddit}</span></div>
      <h1>{title}</h1>
      {post.analysis?.topic && post.title !== post.analysis.topic && <p className="original-title">Tiêu đề gốc: {post.title}</p>}
      <div className="post-meta"><span><Clock3 size={14} /> {dateTime(post.created_utc)}</span><span><ThumbsUp size={14} /> {compactNumber.format(post.score || 0)}</span>
        <span><MessageCircle size={14} /> {post.num_comments || 0} bình luận</span></div>
      <div className="post-actions">{post.reddit_url && <a href={post.reddit_url} target="_blank" rel="noreferrer" className="button primary">Reddit gốc <ExternalLink size={14} /></a>}
        {(post.article_final_url || post.url) && <a href={post.article_final_url || post.url} target="_blank" rel="noreferrer" className="button secondary">Nguồn ngoài <ExternalLink size={14} /></a>}</div>
    </article>
    <EvidenceAnalysis item={post} />
    {(post.extracted_resources || []).length > 0 && <section className="source-section"><div className="section-heading compact"><div><span className="eyebrow">EXTRACTED EVIDENCE</span><h2>Liên kết đã bóc tách</h2></div></div>
      <div className="resource-grid">{post.extracted_resources.map(resource => <div className="resource-card" key={resource.resource_id}><small>{resource.resource_type} · {resource.domain}</small><b>{resource.title || resource.url}</b>
        <p>{resource.context_snippet}</p><a href={resource.url} target="_blank" rel="noreferrer">Mở nguồn <ExternalLink size={12} /></a></div>)}</div></section>}
    {(post.article_body || post.selftext) && <section className="source-section"><div className="section-heading compact"><div><span className="eyebrow">SOURCE TEXT</span><h2>Nội dung đã lưu</h2></div></div>
      <div className="source-prose">{post.article_body || post.selftext}</div></section>}
    <section className="source-section comments-section"><div className="section-heading compact"><div><span className="eyebrow">DISCUSSION EVIDENCE</span><h2>{post.comments?.length || 0} bình luận đã crawl</h2></div></div>
      {post.comments?.length ? <div className="comment-list">{post.comments.map(comment => <article id={`comment-${comment.comment_id}`}
        className={`comment-card ${evidenceIds.has(comment.comment_id) ? 'cited' : ''}`} key={comment.comment_id}>
        <div className="comment-meta"><b>u/{comment.author_name || '[deleted]'}</b><span>{comment.score || 0} điểm</span><span>{relativeTime(comment.created_utc)}</span>
          {evidenceIds.has(comment.comment_id) && <em><Sparkles size={11} /> Được trích dẫn</em>}</div><p>{comment.body}</p>
      </article>)}</div> : <div className="empty-panel compact-empty"><MessageCircle size={24} /><p>Chưa có comment trong database. Endpoint này không crawl ngầm.</p></div>}
    </section>
  </>
}

function Footer({ health }) {
  return <footer className="footer"><div><Logo /><p>Dữ liệu cộng đồng thành tri thức có thể kiểm chứng.</p></div>
    <div><span className={`footer-state ${health?.status || 'unknown'}`}><i /> {health?.status === 'healthy' ? 'Pipeline healthy' : 'Pipeline degraded'}</span>
      <small>Reddit Radar · Asia/Ho_Chi_Minh</small></div></footer>
}

export default function App() {
  const [path, setPath] = useState(window.location.pathname)
  const [health, setHealth] = useState(null)

  // Local storage + Backend SQLite DB state for bookmarks & read posts
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

  // Initial sync from backend SQLite DB
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

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    getJSON('/api/health').then(setHealth).catch(() => setHealth({ status: 'degraded' }))
  }, [path])

  const postMatch = path.match(/^\/post\/([^/]+)/)
  const active = postMatch ? 'knowledge'
    : path === '/saved' ? 'saved'
    : path === '/knowledge' ? 'knowledge'
    : ['/radar', '/signals'].includes(path) ? 'radar' : 'today'

  return <div className="app-shell">
    <Header active={active} health={health} savedCount={savedSet.size} />
    <main className={`page-shell ${postMatch ? 'detail-shell' : ''}`}>
      {postMatch ? <PostDetail postId={decodeURIComponent(postMatch[1])} markRead={markRead} />
        : active === 'saved' ? <SavedPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />
        : active === 'knowledge' ? <KnowledgePage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />
        : active === 'radar' ? <RadarPage savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />
        : <TodayPage health={health} savedSet={savedSet} toggleSave={toggleSave} readSet={readSet} markRead={markRead} />}
    </main>
    <Footer health={health} />
  </div>
}
