import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowLeft, ArrowRight, BarChart3, BookOpen,
  Bot, BrainCircuit, CheckCircle2, ChevronRight, Clock3, Code2,
  ExternalLink, FileText, Gauge, Globe2, Layers3, Lightbulb,
  Menu, MessageCircle, Radio, Search, ShieldCheck, Sparkles, Target,
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
  return <AppLink href="/" className="logo" aria-label="Reddit Radar · Hôm nay">
    <span className="logo-mark"><Activity size={18} /></span>
    <span><b>Reddit</b> Radar</span>
  </AppLink>
}

const NAV_ITEMS = [
  ['today', '/', 'Hôm nay', Sparkles],
  ['knowledge', '/knowledge', 'Kho tri thức', BrainCircuit],
  ['radar', '/radar', 'Radar', Radio],
]

function Header({ active, health }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return <header className="topbar">
    <div className="topbar-inner">
      <Logo />
      <nav className="main-nav" aria-label="Điều hướng chính">
        {NAV_ITEMS.map(([key, href, label, Icon]) => <AppLink
          key={key} href={href} className={active === key ? 'active' : ''}
        ><Icon size={15} />{label}</AppLink>)}
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

function AnalysisCard({ item, featured = false }) {
  const view = analysisView(item)
  return <article className={`analysis-card ${featured ? 'featured' : ''}`}>
    <div className="card-topline">
      <span className="domain-label"><DomainIcon domain={item.domain_id} size={14} />
        {item.domain_name || 'Khác'}
      </span>
      <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />
    </div>
    <h3><AppLink href={`/post/${item.post_id}`}>{view.title}</AppLink></h3>
    {view.summary && <p className="card-summary">{view.summary}</p>}
    {view.keyPoints[0] && <div className="key-preview">
      <Lightbulb size={15} /><span>{view.keyPoints[0].text}</span>
    </div>}
    <footer className="card-footer">
      <span>r/{item.subreddit || '?'}</span>
      <span><ThumbsUp size={13} /> {compactNumber.format(item.score || 0)}</span>
      <span><MessageCircle size={13} /> {compactNumber.format(item.num_comments || item.comment_count || 0)}</span>
      <span>{relativeTime(item.generated_at)}</span>
      <AppLink href={`/post/${item.post_id}`} className="text-link">Xem bằng chứng <ArrowRight size={14} /></AppLink>
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

function TodayPage({ health }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = () => {
    setLoading(true); setError('')
    getJSON('/api/today?period=day&limit=8').then(setData)
      .catch(err => setError(err.message)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  if (loading) return <Loading label="Đang chuẩn bị bản tin hôm nay…" />
  if (error) return <ErrorState message={error} retry={load} />
  const counts = health?.counts || {}
  return <>
    <section className="today-hero">
      <div className="hero-copy">
        <span className="overline"><i /> TECHNOLOGY INTELLIGENCE · HÔM NAY</span>
        <h1>Điều gì đáng biết,<br /><em>trước khi bạn bắt đầu ngày mới?</em></h1>
        <p>Reddit Radar theo dõi thảo luận, thu thập bằng chứng và đưa phần đáng đọc nhất lên trước.</p>
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
      <div><span className="eyebrow">ĐỌC TRƯỚC</span><h2>Những phân tích đáng chú ý</h2>
        <p>Ưu tiên kết quả có LLM và bằng chứng đã crawl; bản local luôn được ghi nhãn riêng.</p></div>
      <AppLink href="/knowledge" className="button secondary">Mở kho tri thức <ArrowRight size={15} /></AppLink>
    </section>
    {data?.highlights?.length
      ? <div className="highlight-grid">{data.highlights.map((item, index) => <AnalysisCard
        item={item} featured={index === 0} key={item.post_id} />)}</div>
      : <div className="empty-panel"><BrainCircuit size={28} /><h3>Pipeline chưa có phân tích</h3><p>Các tín hiệu thô vẫn có ở Radar.</p></div>}
    <section className="today-signals">
      <div className="section-heading compact"><div><span className="eyebrow">RADAR LIVE</span><h2>Tín hiệu đang chuyển động</h2></div>
        <AppLink href="/radar" className="text-link">Xem toàn bộ <ArrowRight size={14} /></AppLink></div>
      <div className="mini-signal-list">{(data?.signals || []).slice(0, 6).map((item, index) => <MiniSignal
        item={item} rank={index + 1} key={item.post_id} />)}</div>
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

function KnowledgePage() {
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('all')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
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

  return <>
    <section className="page-intro">
      <div><span className="eyebrow">EVIDENCE → INTELLIGENCE</span><h1>Kho tri thức</h1>
        <p>Mỗi bài chỉ xuất hiện một lần: kết quả LLM được ưu tiên, sau đó mới tới bản local; trong cùng nhóm, V2 đứng trước V1. Mở bài để kiểm tra claim và nguồn bình luận.</p></div>
      <SearchBox value={query} setValue={setQuery} placeholder="Tìm chủ đề, subreddit, công nghệ…" />
    </section>
    <DomainChips domains={data?.domains || []} selected={domain} setSelected={setDomain} total={data?.total || 0} />
    <div className="result-line"><span><b>{data?.total || 0}</b> bản phân tích phù hợp</span>
      <span>LLM được xếp trước · local vẫn truy cập được</span></div>
    {error ? <ErrorState message={error} /> : loading ? <Loading label="Đang lọc kho tri thức…" />
      : data?.items?.length ? <>
        <div className="knowledge-grid">{data.items.map(item => <AnalysisCard item={item} key={item.post_id} />)}</div>
        <div className="pagination">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><ArrowLeft size={14} /> Trước</button>
          <span>{offset + 1}–{Math.min(offset + data.count, data.total)} / {data.total}</span>
          <button disabled={!data.has_more} onClick={() => setOffset(offset + limit)}>Sau <ArrowRight size={14} /></button>
        </div>
      </> : <div className="empty-panel"><Search size={28} /><h3>Không có kết quả phù hợp</h3><p>Thử từ khóa hoặc lĩnh vực khác.</p></div>}
  </>
}

function MiniSignal({ item, rank }) {
  return <article className="mini-signal">
    <span className="signal-rank">{String(rank).padStart(2, '0')}</span>
    <div className="signal-main">
      <div className="card-topline"><span className="domain-label"><DomainIcon domain={item.domain_id} size={13} /> {item.domain_name}</span>
        {item.provider && <ProviderBadge provider={item.provider} model={item.model} isAI={item.is_ai} compact />}</div>
      <h3><AppLink href={`/post/${item.post_id}`}>{item.analysis?.topic || item.title}</AppLink></h3>
      <div className="signal-meta"><span>r/{item.subreddit}</span><span><ThumbsUp size={13} /> {compactNumber.format(item.latest_score || item.score || 0)}</span>
        <span><MessageCircle size={13} /> {compactNumber.format(item.latest_comments || item.num_comments || 0)}</span>
        <span>{relativeTime(item.created_utc)}</span></div>
    </div>
    <div className="trend-box"><TrendingUp size={15} /><b>{item.trend_score ?? '—'}</b><small>trend</small></div>
  </article>
}

function RadarPage() {
  const [period, setPeriod] = useState('day')
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
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
      const text = `${item.title || ''} ${item.subreddit || ''} ${item.domain_name || ''} ${item.analysis?.topic || ''}`.toLocaleLowerCase('vi')
      return matchesDomain && (!normalized || text.includes(normalized))
    })
  }, [data, domain, query])

  return <>
    <section className="page-intro radar-intro">
      <div><span className="eyebrow">RAW SIGNAL EXPLORER</span><h1>Radar</h1>
        <p>Xếp hạng post theo độ mới, tương tác và vận tốc. Đây là bề mặt kiểm chứng, không phải bản tin mặc định.</p></div>
      <SearchBox value={query} setValue={setQuery} placeholder="Lọc tiêu đề hoặc subreddit…" />
    </section>
    <div className="radar-controls">
      <div className="period-switch">{PERIODS.map(([value, label]) => <button key={value}
        className={period === value ? 'active' : ''} onClick={() => { setPeriod(value); setDomain('all') }}>{label}</button>)}</div>
    </div>
    <DomainChips domains={data?.domains || []} selected={domain} setSelected={setDomain} total={data?.items?.length || 0} />
    <div className="result-line"><span><b>{filtered.length}</b> tín hiệu</span><span>Dữ liệu tạo {relativeTime(data?.generated_at)}</span></div>
    {error ? <ErrorState message={error} retry={load} /> : loading ? <Loading label="Đang đo vận tốc tín hiệu…" />
      : filtered.length ? <div className="radar-list">{filtered.map((item, index) => <MiniSignal item={item} rank={index + 1} key={item.post_id} />)}</div>
      : <div className="empty-panel"><Search size={28} /><h3>Không tìm thấy tín hiệu</h3><p>Thử đổi từ khóa, lĩnh vực hoặc khoảng thời gian.</p></div>}
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

function PostDetail({ postId }) {
  const [post, setPost] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
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
    : path === '/knowledge' ? 'knowledge'
      : ['/radar', '/signals'].includes(path) ? 'radar' : 'today'
  return <div className="app-shell">
    <Header active={active} health={health} />
    <main className={`page-shell ${postMatch ? 'detail-shell' : ''}`}>
      {postMatch ? <PostDetail postId={decodeURIComponent(postMatch[1])} />
        : active === 'knowledge' ? <KnowledgePage />
          : active === 'radar' ? <RadarPage /> : <TodayPage health={health} />}
    </main>
    <Footer health={health} />
  </div>
}
