"""Encontra vídeos "outliers": muitas views em relação ao tamanho do canal.

Um vídeo com 500 mil views num canal de 10 mil inscritos indica demanda real
pelo tema, não só força do canal. É o sinal principal para escolher nicho e temas.

Custo de cota da YouTube Data API (10.000 unidades/dia grátis):
search.list = 100 por palavra-chave; videos.list e channels.list = 1 por lote de 50.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from .config import DATA_DIR, require_env


@dataclass
class Outlier:
    keyword: str
    video_id: str
    title: str
    channel_title: str
    published_at: str
    views: int
    subscribers: int
    duration_s: int
    score: float

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def outlier_score(views: int, subscribers: int) -> float:
    """Views por inscrito. Canais com menos de 1.000 inscritos contam como 1.000
    para não inflar o score de canais minúsculos."""
    return round(views / max(subscribers, 1000), 2)


def parse_duration(iso: str) -> int:
    """Converte duração ISO 8601 (PT1H2M3S) em segundos."""
    total, num = 0, ""
    units = {"H": 3600, "M": 60, "S": 1}
    for ch in iso.removeprefix("P").replace("T", ""):
        if ch.isdigit():
            num += ch
        elif ch in units:
            total += int(num or 0) * units[ch]
            num = ""
        elif ch == "D":
            total += int(num or 0) * 86400
            num = ""
    return total


def _chunks(items: list[str], size: int = 50):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def find_outliers(
    keywords: list[str],
    days: int = 90,
    region: str = "BR",
    language: str = "pt",
    max_subscribers: int = 200_000,
    min_score: float = 3.0,
    long_only: bool = True,
) -> list[Outlier]:
    yt = build("youtube", "v3", developerKey=require_env("YOUTUBE_API_KEY"))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    found: dict[str, tuple[str, dict]] = {}
    for kw in keywords:
        resp = yt.search().list(
            q=kw, part="snippet", type="video", order="viewCount", maxResults=50,
            publishedAfter=since, regionCode=region, relevanceLanguage=language,
        ).execute()
        for item in resp.get("items", []):
            found.setdefault(item["id"]["videoId"], (kw, item["snippet"]))

    videos: dict[str, dict] = {}
    for batch in _chunks(list(found)):
        resp = yt.videos().list(part="statistics,contentDetails", id=",".join(batch)).execute()
        videos.update({v["id"]: v for v in resp.get("items", [])})

    channel_ids = list({s["channelId"] for _, s in found.values()})
    subs: dict[str, int] = {}
    for batch in _chunks(channel_ids):
        resp = yt.channels().list(part="statistics", id=",".join(batch)).execute()
        for c in resp.get("items", []):
            stats = c["statistics"]
            # Canais que escondem inscritos ficam de fora (não dá para calcular).
            if not stats.get("hiddenSubscriberCount"):
                subs[c["id"]] = int(stats.get("subscriberCount", 0))

    results = []
    for vid, (kw, snip) in found.items():
        v = videos.get(vid)
        sub = subs.get(snip["channelId"])
        if not v or sub is None or sub > max_subscribers:
            continue
        duration = parse_duration(v["contentDetails"]["duration"])
        if long_only and duration < 180:
            continue
        views = int(v["statistics"].get("viewCount", 0))
        score = outlier_score(views, sub)
        if score >= min_score:
            results.append(Outlier(kw, vid, snip["title"], snip["channelTitle"],
                                   snip["publishedAt"][:10], views, sub, duration, score))
    return sorted(results, key=lambda o: o.score, reverse=True)


def save_csv(outliers: list[Outlier]) -> str:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"outliers-{datetime.now():%Y%m%d-%H%M}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        fields = list(Outlier.__dataclass_fields__) + ["url"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for o in outliers:
            w.writerow({**asdict(o), "url": o.url})
    return str(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--keywords", nargs="+", required=True)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--region", default="BR")
    p.add_argument("--language", default="pt")
    p.add_argument("--max-subscribers", type=int, default=200_000)
    p.add_argument("--min-score", type=float, default=3.0)
    p.add_argument("--include-shorts", action="store_true")
    a = p.parse_args()

    outliers = find_outliers(a.keywords, a.days, a.region, a.language,
                             a.max_subscribers, a.min_score, not a.include_shorts)
    for o in outliers[:20]:
        print(f"{o.score:>7.1f}x  {o.views:>10,} views  {o.subscribers:>8,} insc.  {o.title[:70]}")
    print(f"\n{len(outliers)} outliers salvos em {save_csv(outliers)}")


if __name__ == "__main__":
    main()
