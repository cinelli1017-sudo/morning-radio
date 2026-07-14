# -*- coding: utf-8 -*-
"""
Claude APIを使って、収集したニュースからラジオ番組の台本を作るモジュール。

【やっていること】
1. 収集したニュース一覧をプロンプト(Claudeへの指示文)に埋め込む
2. 「2人のパーソナリティの掛け合い」の台本をJSON形式で作るよう依頼する
3. 返ってきたJSONを、[{"speaker": "male", "text": "..."}, ...] のリストにして返す

音声合成(synthesize.py)がこのリストをそのまま読み上げるため、
Claudeには output_config のJSONスキーマ指定を使って「必ずこの形のJSONで返す」
ことを保証させている(自由文で返されるとプログラムで処理できないため)。
"""

from datetime import datetime
import json
import os
from zoneinfo import ZoneInfo

import anthropic

# 台本の形をJSONスキーマで定義する。
# これをAPIに渡すと、Claudeの出力が必ずこの形のJSONになることが保証される
SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # male=メインMC(男性) / female=アシスタント(女性)
                    "speaker": {"type": "string", "enum": ["male", "female"]},
                    "text": {"type": "string"},
                    # 新しいニュース(話題)に切り替わる最初のセリフだけtrue。
                    # 音声合成時、trueのセリフの直前にジングル(チャイム音)が入る
                    "new_topic": {"type": "boolean"},
                },
                "required": ["speaker", "text", "new_topic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """あなたはプロの放送作家です。朝のラジオ番組の台本を書きます。

## リスナー像
生産設備メーカーで購買(調達)を担当している会社員。通勤中にiPhoneで聴く。

## 番組の構成
1. オープニング: 番組名と今日の日付を伝え、軽い挨拶と天気の話題などはせず、今日のトピックの予告をする
2. 本編: ジャンルごとにニュースを紹介。1つのニュースにつき、事実の紹介→2人のコメントの掛け合い
3. 締め: 今日の要点を一言でまとめ、リスナーの仕事を応援して締める

## パーソナリティ
- male(ケイタ): メインMC。落ち着いた進行役。ニュースの事実を分かりやすく伝える
- female(ナナミ): アシスタント。明るく、鋭い質問や補足で話を広げる

## new_topic フラグのルール
- 新しいニュース(話題)に切り替わる最初のセリフ、および締めセクションの最初のセリフは new_topic を true にする
- それ以外のセリフ(オープニング含む)はすべて false にする
- trueのセリフの直前には自動でチャイム音が入るため、セリフ側でも「続いては〜」など話題の切り替わりが分かる言い方で始める

## 書き方のルール
- 話し言葉で自然に。書き言葉(「〜である」等)は使わない
- 購買担当者の視点(納期・価格・調達リスク・仕入先への影響)へのひとことを、関連するニュースに添える
- 専門用語は簡単な言い換えを添える
- 数字や社名は聞き取りやすいように読む(例: 「1,234億円」→「およそ1200億円」)
- 音声合成で読み上げるため、記号(※、→、【】など)や英略語の羅列は避ける
- 1つのセリフは長くても3文程度。テンポよく交互に話す
"""


def _get_client() -> anthropic.Anthropic:
    """APIキーを環境変数から読み込んでクライアントを作る。"""
    # ローカルでは .env の claude_API_KEY、GitHub ActionsではSecretsの CLAUDE_API_KEY を使う
    api_key = os.environ.get("claude_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "APIキーが見つかりません。.env に claude_API_KEY=... を設定するか、"
            "GitHub Secrets に CLAUDE_API_KEY を登録してください。"
        )
    return anthropic.Anthropic(api_key=api_key)


def generate_script(news_text: str, claude_config: dict, program_config: dict) -> list[dict]:
    """
    ニュース一覧のテキストから、掛け合い形式の台本を生成する。

    戻り値: [{"speaker": "male" or "female", "text": "セリフ"}, ...]
    """
    today = datetime.now(ZoneInfo("Asia/Tokyo"))
    # 日本語の曜日(weekday()は月曜=0で返るため、この並び順にしている)
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    target_minutes = program_config.get("target_minutes", 7)
    # 音声合成の読み上げ速度はおよそ1分330文字(実測ベース)
    target_chars = target_minutes * 330

    user_prompt = f"""今日は{today.year}年{today.month}月{today.day}日({weekday_ja}曜日)です。
番組名は「{program_config.get('title', 'モーニングラジオ')}」です。

以下は昨日から今朝にかけてのニュース一覧です。この中から重要なもの・リスナーに役立つものを
6〜8本選んで、ラジオ台本を作ってください。

長さの指定(重要):
- セリフの合計は{target_chars}文字以上にしてください(読み上げ約{target_minutes}分の番組です)
- 1つのニュースにつき4〜6往復の掛け合いで、背景や具体例まで掘り下げてください

{news_text}"""

    client = _get_client()
    response = client.messages.create(
        model=claude_config.get("model", "claude-sonnet-5"),
        max_tokens=claude_config.get("max_tokens", 16000),
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        # JSONスキーマを指定して、出力が必ず台本の形のJSONになるようにする
        output_config={"format": {"type": "json_schema", "schema": SCRIPT_SCHEMA}},
    )

    # スキーマ指定をしているので、textブロックの中身は必ず正しいJSONになっている
    text = next(block.text for block in response.content if block.type == "text")
    script_lines = json.loads(text)["lines"]

    total_chars = sum(len(line["text"]) for line in script_lines)
    print(f"[generate_script] 台本を生成しました: {len(script_lines)}セリフ / 約{total_chars}文字")
    print(
        f"[generate_script] トークン使用量: 入力{response.usage.input_tokens} / "
        f"出力{response.usage.output_tokens}"
    )

    return script_lines
