# Discord Bot

Giveaway（抽選）とTicket（問い合わせ管理）を統合したDiscord Botです。起動ファイルは `bot.py` です。

## 機能

### Giveaway
- `/giveaway create`：リアクション参加型の抽選を作成
- `/giveaway end`：抽選を手動終了
- `/giveaway reroll`：終了済み抽選を再抽選（過去の当選者は除外）
- 🎉リアクションで参加し、外すと辞退
- 締切時に参加者を取得してランダム抽選
- `data/giveaways.json` に保存し、再起動後に締切タイマーを復元

### Ticket
- `/ticket-panel`：チケット作成パネルを設置
- ボタンで問い合わせチャンネルを作成
- 作成者とモデレーターロールのみ閲覧可能
- `/ticket-close` またはチケット内ボタンでクローズ
- 再起動後もパネルとボタンが動作

## 必要環境
- Python 3.10以上
- discord.py 2.x
- python-dotenv

## セットアップ

```bash
git clone https://github.com/walnut1210/Discord-bot.git
cd Discord-bot
python -m venv .venv
```

Windowsでは `.venv\\Scripts\\activate`、macOS/Linuxでは `source .venv/bin/activate` で仮想環境を有効化します。

```bash
python -m pip install -r requirements.txt
```

`.env.example` を `.env` にコピーし、次の値を設定してください。

| 変数 | 説明 |
| --- | --- |
| `DISCORD_TOKEN` | Discord Bot Token |
| `MOD_ROLE_ID` | チケット対応ロールのID |
| `TICKET_CATEGORY_ID` | チケット作成先カテゴリID |
| `GIVEAWAY_EMOJI` | 参加リアクション（任意、初期値 🎉） |
| `GUILD_ID` | コマンド登録先サーバーID（任意） |

Bot TokenをGitHubへアップロードしないでください。

## Discord側の設定
[Discord Developer Portal](https://discord.com/developers/applications) でBotを作成し、`bot` と `applications.commands` のScopeでサーバーに招待します。

必要な権限：View Channels、Send Messages、Embed Links、Add Reactions、Read Message History、Manage Channels。Botのロールはモデレーターロールより上位に配置してください。

Privileged Gateway Intents（Server Members、Message Content、Presence）は不要です。

## 起動

```bash
python bot.py
```

## コマンド
- `/giveaway create prize:賞品名 duration:分 winners:人数 channel:チャンネル`
- `/giveaway end message_id:メッセージID`
- `/giveaway reroll message_id:メッセージID`：終了済み抽選を再抽選
- `/ticket-panel`
- `/ticket-close`

## 保存データ
Giveawayの情報は `data/giveaways.json` に保存されます。ホスティング環境では `data/` が再起動後も保持される永続ストレージを使用してください。チケットを閉じるとチャンネルは5秒後に削除されます。

## License
[MIT License](LICENSE)
