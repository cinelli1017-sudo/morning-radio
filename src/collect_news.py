# -*- coding: utf-8 -*-
"""
RSSフィードからニュースを集めるモジュール。

【やっていること】
1. config.yaml に登録されたRSSフィードを、requestsライブラリで取得する
2. feedparserライブラリで、RSSの中身(記事のタイトル・概要・日時)を取り出す
3. 「過去〇時間以内」の記事だけに絞り込み、ジャンルごとにまとめて返す

1つのフィードが取得に失敗しても、他のフィードだけで番組を作れるように、
エラーはログに出すだけで処理は止めない方針にしている。
(毎朝の自動実行なので「何かが少し欠けても放送は続ける」ことを優先)
"""

import calendar
from datetime import datetime, timedelta, timezone
import html
import re

import feedparser
import requests

# 一部のサイトはBotらしいアクセスを拒否するため、ブラウザ風のUser-Agentを名乗る
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT_SECONDS = 20

# 概要文が長すぎるとClaudeに渡す文章が膨らむため、この文字数で切る
SUMMARY_MAX_CHARS = 200


def _strip_html_tags(text: str) -> str:
    """概要文に混ざっているHTMLタグ(<a>など)や実体参照(&amp;など)を取り除く。"""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # 改行や連続する空白を1つのスペースにまとめて読みやすくする
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _entry_published_utc(entry) -> datetime | None:
    """
    記事の公開日時をUTC(世界標準時)のdatetimeで返す。

    feedparserは、RSSの形式ごとに違う日付の書き方を
    published_parsed / updated_parsed というフィールドに変換してくれる。
    どちらも無い記事は日時不明としてNoneを返す。
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    # feedparserの日時はUTCのstruct_timeなので、UTC専用の変換関数で秒数にしてから
    # datetimeへ直す(time.mktimeはPC のタイムゾーンで解釈してしまうため使わない)
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def _fetch_feed_entries(url: str) -> list:
    """1つのRSSフィードを取得して、記事のリストを返す。失敗したら例外を投げる。"""
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    # feedparserにはURLを直接渡すこともできるが、タイムアウトを指定できないため、
    # requestsで取得した中身(バイト列)を渡す形にしている
    feed = feedparser.parse(response.content)
    return feed.entries


def collect_news(news_config: dict) -> dict[str, list[dict]]:
    """
    config.yamlのnews設定に従ってニュースを収集する。

    戻り値は「ジャンル名 → 記事リスト」の辞書。記事は以下の形:
        {"title": "...", "summary": "...", "source": "NHK 経済ニュース", "link": "..."}
    """
    hours = news_config.get("hours", 26)
    max_items = news_config.get("max_items_per_genre", 8)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    news_by_genre: dict[str, list[dict]] = {}

    for feed_conf in news_config.get("feeds", []):
        genre = feed_conf["genre"]
        name = feed_conf["name"]
        url = feed_conf["url"]

        try:
            entries = _fetch_feed_entries(url)
        except Exception as error:
            # 1つのフィードの失敗で全体を止めない(他のジャンルで番組は作れる)
            print(f"[collect_news] 警告: {name} の取得に失敗しました: {error}")
            continue

        added = 0
        for entry in entries:
            published = _entry_published_utc(entry)
            # 日時が分からない記事は「古いかもしれない」ので載せない
            if published is None or published < cutoff:
                continue

            title = _strip_html_tags(entry.get("title", "")).strip()
            summary = _strip_html_tags(entry.get("summary", ""))[:SUMMARY_MAX_CHARS]
            if not title:
                continue

            news_by_genre.setdefault(genre, []).append(
                {
                    "title": title,
                    "summary": summary,
                    "source": name,
                    "link": entry.get("link", ""),
                }
            )
            added += 1

        print(f"[collect_news] {name}: 過去{hours}時間の記事を {added} 件取得")

    # 同じジャンルに複数フィードがある場合に記事が多くなりすぎるため、上限で切る
    for genre in news_by_genre:
        news_by_genre[genre] = news_by_genre[genre][:max_items]

    return news_by_genre


def format_news_for_prompt(news_by_genre: dict[str, list[dict]]) -> str:
    """収集したニュースを、Claudeへのプロンプトに貼り付けやすいテキストに整形する。"""
    lines = []
    for genre, items in news_by_genre.items():
        lines.append(f"■ ジャンル: {genre}")
        for item in items:
            lines.append(f"- {item['title']}（出典: {item['source']}）")
            if item["summary"]:
                lines.append(f"  概要: {item['summary']}")
        lines.append("")
    return "\n".join(lines).strip()
