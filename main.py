import os
from flask import Flask
from threading import Thread
import sqlite3
import discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks
from dotenv import load_dotenv

# =====================
# 設定
# =====================

load_dotenv()

TOKEN = os.getenv("TOKEN")

# 本番用
INACTIVE_DAYS = 30
WARNING_GRACE_DAYS = 1

# テスト時は下記を使う
# INACTIVE_DAYS = 0
# WARNING_GRACE_DAYS = 0

DB_NAME = "activity.db"

JST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =====================
# DB初期化
# =====================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            last_activity TEXT,
            warning_sent TEXT
        )
    """)

    conn.commit()
    conn.close()


def update_last_activity(user_id, username):
    now = datetime.now(JST).isoformat()

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO user_activity (user_id, username, last_activity, warning_sent)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            last_activity = excluded.last_activity,
            warning_sent = NULL
    """, (user_id, username, now))

    conn.commit()
    conn.close()


def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, username, last_activity, warning_sent
        FROM user_activity
    """)

    users = cur.fetchall()
    conn.close()

    return users


def set_warning_sent(user_id):
    now = datetime.now(JST).isoformat()

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        UPDATE user_activity
        SET warning_sent = ?
        WHERE user_id = ?
    """, (now, user_id))

    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM user_activity
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()
    conn.close()


# =====================
# BOT起動時
# =====================

@bot.event
async def on_ready():
    init_db()

    print(f"BOT起動完了: {bot.user}")

    if not check_inactive_users.is_running():
        check_inactive_users.start()


# =====================
# メッセージ監視
# =====================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    update_last_activity(
        message.author.id,
        str(message.author)
    )

    await bot.process_commands(message)


# =====================
# 参加時にDB登録
# =====================

@bot.event
async def on_member_join(member):
    if member.bot:
        return

    update_last_activity(
        member.id,
        str(member)
    )


# =====================
# 非アクティブ確認
# =====================

@tasks.loop(hours=24)
async def check_inactive_users():
    print("非アクティブユーザー確認開始")

    now = datetime.now(JST)
    users = get_all_users()

    for guild in bot.guilds:
        for user_id, username, last_activity, warning_sent in users:
            member = guild.get_member(user_id)

            if member is None:
                continue

            if member.bot:
                continue

            # 管理者は除外
            if member.guild_permissions.administrator:
                continue

            last_activity_dt = datetime.fromisoformat(last_activity)
            inactive_period = now - last_activity_dt

            # 警告済みの場合
            if warning_sent:
                warning_sent_dt = datetime.fromisoformat(warning_sent)
                grace_period = now - warning_sent_dt

                if grace_period >= timedelta(days=WARNING_GRACE_DAYS):
                    try:
                        await member.kick(reason="30日以上活動なし・警告後も活動なし")
                        delete_user(user_id)
                        print(f"Kick実行: {username}")
                    except Exception as e:
                        print(f"Kick失敗: {username} / {e}")

                continue

            # 30日以上活動なしなら警告
            if inactive_period >= timedelta(days=INACTIVE_DAYS):
                try:
                    await member.send(
                        "サーバー内で30日以上活動が確認できていません。\n"
                        "翌日までにメッセージ投稿などの活動が確認できない場合、"
                        "サーバーから除外される可能性があります。"
                    )

                    set_warning_sent(user_id)
                    print(f"警告送信: {username}")

                except Exception as e:
                    print(f"DM送信失敗: {username} / {e}")


# =====================
# 手動確認コマンド
# =====================

@bot.command()
async def check(ctx):
    await check_inactive_users()
    await ctx.send("非アクティブユーザー確認を実行しました。")
@bot.command()
async def inactive(ctx):
    now = datetime.now(JST)
    users = get_all_users()

    inactive_users = []

    for user_id, username, last_activity, warning_sent in users:
        try:
            last_activity_dt = datetime.fromisoformat(last_activity)
            inactive_days = (now - last_activity_dt).days
        except (TypeError, ValueError):
            continue

        if inactive_days < INACTIVE_DAYS:
            continue

        member = ctx.guild.get_member(user_id)

        if member is None:
            continue

        if member.bot:
            continue

        if member.guild_permissions.administrator:
            continue

        status = "⚠️ 警告済み" if warning_sent else "未警告"

        inactive_users.append(
            (inactive_days, member.display_name, status)
        )

    if not inactive_users:
        await ctx.send(f"現在、{INACTIVE_DAYS}日以上未活動のユーザーはいません。")
        return

    inactive_users.sort(reverse=True)

    message = f"📋 {INACTIVE_DAYS}日以上未活動のユーザー\n\n"

    for days, name, status in inactive_users:
        message += f"・{name}：{days}日（{status}）\n"

    await ctx.send(message)

# =====================
# BOT起動
# =====================
app = Flask(__name__)

@app.route("/")
def home():
    return "Discord Bot Running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )


if TOKEN is None:
    print("TOKENが設定されていません。.envを確認してください。")
else:
    web_thread = Thread(target=run_web)
    web_thread.start()

    bot.run(TOKEN)