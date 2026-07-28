"""Render the latest Reddit Radar intelligence to JSON and Markdown files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from reddit_crawler.analytics import (
    DOMAIN_META,
    PERIOD_SECONDS,
    database_stats,
    latest_ai_digest,
    trending_posts,
)


def build_report(db_path: str, period: str = "day", limit: int = 20) -> dict[str, Any]:
    if period not in PERIOD_SECONDS:
        raise ValueError(f"period không hợp lệ: {period}")
    generated = dt.datetime.now(dt.timezone.utc)
    return {
        "period": period,
        "generated_at": generated.isoformat(),
        "stats": database_stats(db_path),
        "ai_digest": latest_ai_digest(db_path, period, allow_fallback=False),
        "items": trending_posts(db_path, period=period, limit=limit),
    }


def _source_links(payload: dict[str, Any]) -> dict[str, str]:
    return {
        source.get("post_id"): source.get("url")
        for source in payload.get("sources", [])
        if source.get("post_id") and source.get("url")
    }


def render_markdown(report: dict[str, Any]) -> str:
    period_names = {
        "3h": "3 giờ",
        "day": "ngày",
        "week": "tuần",
        "month": "tháng",
        "year": "năm",
    }
    lines = [
        f"# Reddit Radar — báo cáo {period_names.get(report['period'], report['period'])}",
        "",
        f"Tạo lúc: {report['generated_at']}",
        "",
    ]

    digest = report.get("ai_digest") or {}
    payload = digest.get("payload") or {}
    if digest:
        provider = digest.get("provider") or "unknown"
        model = digest.get("model")
        provider_label = provider + (f" / {model}" if model else "")
        lines.extend([
            "## Briefing",
            "",
            f"**{payload.get('title') or digest.get('title') or 'Technology Radar'}** · "
            f"provider `{provider_label}`",
            "",
            payload.get("executive_summary") or digest.get("executive_summary") or "",
            "",
        ])
        numbers = payload.get("key_numbers") or []
        if numbers:
            lines.extend(["### Số liệu chính", ""])
            for number in numbers:
                lines.append(
                    f"- **{number.get('name') or '?'}:** {number.get('value', 0)} "
                    f"{number.get('unit') or ''} — {number.get('context') or ''}".rstrip()
                )
            lines.append("")

        stories = payload.get("stories") or []
        links = _source_links(payload)
        if stories:
            lines.extend(["### Điều đáng chú ý", ""])
            for story in stories:
                ids = story.get("source_post_ids") or []
                url = next((links[source_id] for source_id in ids if source_id in links), None)
                headline = story.get("headline") or "Không có tiêu đề"
                lines.append(f"#### [{headline}]({url})" if url else f"#### {headline}")
                lines.extend([
                    "",
                    story.get("summary") or "",
                    "",
                    f"**Vì sao đáng quan tâm:** {story.get('why_it_matters') or 'Chưa rõ.'}",
                    "",
                ])

    lines.extend(["## Tín hiệu xếp hạng", ""])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in report.get("items") or []:
        grouped.setdefault(item.get("domain_id") or "other", []).append(item)
    for domain_id, items in grouped.items():
        domain = DOMAIN_META.get(domain_id, DOMAIN_META["other"])
        lines.extend([f"### {domain['name']}", ""])
        for index, item in enumerate(items, 1):
            url = item.get("url") or item.get("reddit_url")
            title = item.get("title") or "Không có tiêu đề"
            heading = f"{index}. [{title}]({url})" if url else f"{index}. {title}"
            lines.append(heading)
            lines.append(
                f"   - trend `{item.get('trend_score', 0)}` · "
                f"{item.get('latest_score', 0)} điểm · "
                f"{item.get('latest_comments', 0)} bình luận"
            )
            analysis = item.get("analysis") or {}
            insight = analysis.get("verdict") or analysis.get("community_consensus")
            if insight:
                lines.append(f"   - Đúc kết: {insight}")
        lines.append("")

    if not report.get("items"):
        lines.extend(["Chưa có tín hiệu trong cửa sổ này.", ""])
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_report(
    report: dict[str, Any], output_dir: str | os.PathLike[str] = "reports",
) -> tuple[Path, Path]:
    root = Path(output_dir) / report["period"]
    root.mkdir(parents=True, exist_ok=True)
    generated = dt.datetime.fromisoformat(report["generated_at"])
    stamp = generated.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    json_path = root / f"{stamp}.json"
    markdown_path = root / f"{stamp}.md"
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown_text = render_markdown(report)
    _atomic_write(json_path, json_text)
    _atomic_write(markdown_path, markdown_text)
    _atomic_write(root / "latest.json", json_text)
    _atomic_write(root / "latest.md", markdown_text)
    return markdown_path, json_path


def create_report(
    db_path: str = "reddit.db",
    period: str = "day",
    *,
    limit: int = 20,
    output_dir: str | os.PathLike[str] = "reports",
) -> dict[str, Any]:
    report = build_report(db_path, period, limit)
    markdown_path, json_path = write_report(report, output_dir)
    return {
        "report": report,
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tạo báo cáo Reddit Radar")
    parser.add_argument("--db", default=os.environ.get("REDDIT_DB_PATH", "reddit.db"))
    parser.add_argument("--period", default="day", choices=PERIOD_SECONDS)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", default=os.environ.get("REPORT_DIR", "reports"))
    args = parser.parse_args(argv)
    result = create_report(args.db, args.period, limit=args.limit, output_dir=args.output)
    print(
        f"Đã tạo {len(result['report']['items'])} mục: "
        f"{result['markdown_path']} + {result['json_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
