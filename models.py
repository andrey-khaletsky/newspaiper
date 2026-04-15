"""Article data model for newspaiper."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Article:
    title: str
    subtitle: str = ""
    source_url: str = ""
    source_domain: str = ""
    category: str = ""
    read_time: str = ""
    read_time_minutes: int = 0
    tldr_summary: str = ""
    body: str = ""
    word_count: int = 0
    is_paywalled: bool = False
    fetch_status: str = "pending"  # "ok", "paywalled", "failed", "timeout"
    image_url: str = ""
