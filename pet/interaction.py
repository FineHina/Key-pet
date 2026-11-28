"""Local interaction rules and durable gift delivery."""
from dataclasses import dataclass
from datetime import datetime
import random
import time
from uuid import uuid4


@dataclass(frozen=True)
class Gift:
    name: str
    category: str
    note: str


GIFTS = {
    "perfume": Gift("星雾香水", "化妆品", "淡紫玻璃瓶，带小小的星轨装饰。"),
    "lipstick": Gift("莓色唇膏", "化妆品", "柔和的莓粉色，配浅紫色管身。"),
    "blush": Gift("星砂腮红", "化妆品", "带星形扣的便携腮红盒。"),
    "handheld": Gift("横屏掌机", "游戏设备及介质", "适合一起玩一会儿的口袋游戏机。"),
    "retro": Gift("复古竖屏掌机", "游戏设备及介质", "薄荷色小屏搭配复古按键。"),
    "cartridge": Gift("游戏卡带", "游戏设备及介质", "淡紫色外壳上印着一颗星星。"),
    "disc": Gift("游戏光盘", "游戏设备及介质", "在光下会泛出浅浅的彩色光泽。"),
}

CARE_ACTIONS = {
    "cake": ("送蛋糕", "happy", "蛋糕收到啦。一起休息一会儿吧。"),
    "milk_tea_cold": ("送冷奶茶", "happy", "冰奶茶，谢谢你。"),
    "milk_tea_hot": ("送热奶茶", "happy", "热奶茶刚刚好。谢谢。"),
    "rest": ("休息一下", "relaxed", "好，就安静休息一会儿。"),
}

MOODS = {
    "calm": ("平静", "ready"),
    "happy": ("愉快", "success"),
    "relaxed": ("放松", "rest"),
    "surprised": ("惊喜", "complete"),
    "quiet": ("想安静", "quiet"),
}

GAME_NAMES = {"rps": "KEI 猜拳", "number": "KEI 猜数字", "memory": "光环记忆"}
SCORES = {"touch": (1, 3), "focus": (2, 2), "care": (2, 2), "game": (2, 2)}


class InteractionService:
    def __init__(self, storage, wall_clock=time.time, rng=None):
        self.storage = storage
        self.wall_clock = wall_clock
        self.rng = rng if rng is not None else random.Random()

    @staticmethod
    def _date(now):
        return datetime.fromtimestamp(now).date().isoformat()

    def status(self):
        row = self.storage.conn.execute("SELECT * FROM companion_state WHERE id = 1").fetchone()
        points = row["affection"]
        mood = row["mood"] if row["mood_until"] > self.wall_clock() else "calm"
        stage = "默契" if points >= 70 else "熟悉" if points >= 30 else "初识"
        return points, stage, mood

    def _set_mood(self, mood, now, seconds):
        self.storage.conn.execute("UPDATE companion_state SET mood = ?, mood_until = ? WHERE id = 1",
                                  (mood, now + seconds))

    def _score(self, category, event_key, now):
        conn = self.storage.conn
        if conn.execute("SELECT 1 FROM companion_scores WHERE event_key = ?", (event_key,)).fetchone():
            return 0
        value, limit = SCORES[category]
        day = self._date(now)
        earned = conn.execute(
            """SELECT COUNT(*) FROM companion_scores
                WHERE local_date = ? AND category = ? AND points > 0""", (day, category)).fetchone()[0]
        current = conn.execute("SELECT affection FROM companion_state WHERE id = 1").fetchone()[0]
        points = min(value, 100 - current) if earned < limit else 0
        # Completed focus sessions and games retain an idempotent record even at the daily cap.
        if points or category in ("focus", "game"):
            conn.execute("INSERT INTO companion_scores VALUES (?, ?, ?, ?, ?)",
                         (event_key, day, category, points, now))
        if points:
            conn.execute("UPDATE companion_state SET affection = affection + ? WHERE id = 1", (points,))
        return points

    def touch(self):
        now = self.wall_clock()
        with self.storage.transaction() as conn:
            last = conn.execute(
                "SELECT MAX(occurred_at) FROM companion_scores WHERE category = 'touch'").fetchone()[0]
            if last is None or now - last >= 300:
                return self._score("touch", f"touch:{uuid4().hex}", now)
            return 0

    def ask_for_quiet(self):
        with self.storage.transaction():
            self._set_mood("quiet", self.wall_clock(), 30)

    def focus_completed(self, session_id):
        with self.storage.transaction() as conn:
            session = conn.execute(
                "SELECT kind, state, duration, completed_at FROM focus_sessions WHERE id = ?",
                (session_id,)).fetchone()
            if (session and session["kind"] == "focus" and session["state"] == "completed"
                    and session["duration"] >= 600):
                return self._score("focus", f"focus:{session_id}", session["completed_at"])
            return 0

    def care(self, action):
        if action not in CARE_ACTIONS:
            raise ValueError("无法识别该照顾动作。")
        _, mood, reply = CARE_ACTIONS[action]
        now = self.wall_clock()
        with self.storage.transaction():
            self._set_mood(mood, now, 900)
            points = self._score("care", f"care:{uuid4().hex}", now)
        return reply, points

    def inventory(self):
        return {row["gift_id"]: (row["quantity"], row["given"])
                for row in self.storage.conn.execute("SELECT * FROM gift_inventory")}

    def _add_gift(self, gift_id):
        self.storage.conn.execute(
            """INSERT INTO gift_inventory (gift_id, quantity) VALUES (?, 1)
                ON CONFLICT(gift_id) DO UPDATE SET quantity = quantity + 1""", (gift_id,))

    def give(self, gift_id):
        if gift_id not in GIFTS:
            raise ValueError("无法识别该礼物。")
        now = self.wall_clock()
        with self.storage.transaction() as conn:
            row = conn.execute(
                """UPDATE gift_inventory SET quantity = quantity - 1, given = given + 1
                    WHERE gift_id = ? AND quantity > 0""", (gift_id,))
            if not row.rowcount:
                raise ValueError("这件礼物暂未入库。")
            self._set_mood("surprised", now, 1200)
            points = self._score("care", f"gift:{uuid4().hex}", now)
            _, stage, _ = self.status()
        name = GIFTS[gift_id].name
        reply = {
            "初识": f"「{name}」收到啦，谢谢你。",
            "熟悉": f"谢谢你挑了「{name}」，我收下了。",
            "默契": f"「{name}」收到了，我会好好收起来。",
        }[stage]
        return reply, points

    def grant_daily_gift(self):
        now = self.wall_clock()
        pack = f"daily:{self._date(now)}"
        with self.storage.transaction() as conn:
            if conn.execute("SELECT 1 FROM gift_rewards WHERE pack = ?", (pack,)).fetchone():
                return None
            gift = self.rng.choice(tuple(GIFTS))
            conn.execute("INSERT INTO gift_rewards VALUES (?, 0, ?, ?, 1)", (pack, gift, now))
            self._add_gift(gift)
        return gift

    def today_gift(self):
        pack = f"daily:{self._date(self.wall_clock())}"
        row = self.storage.conn.execute("SELECT gift_id FROM gift_rewards WHERE pack = ?",
                                        (pack,)).fetchone()
        return row["gift_id"] if row else None

    def finish_game(self, game, match_id):
        pack, points, _ = self.settle_game(game, match_id)
        return pack, points

    def settle_game(self, game, match_id):
        if game not in GAME_NAMES:
            raise ValueError("无法识别该小游戏。")
        pack = f"game:{game}:{match_id}"
        now = self.wall_clock()
        with self.storage.transaction() as conn:
            if conn.execute("SELECT 1 FROM gift_rewards WHERE pack = ?", (pack,)).fetchone():
                return pack, 0, False
            gifts = self.rng.choices(tuple(GIFTS), k=3)
            conn.executemany("INSERT INTO gift_rewards VALUES (?, ?, ?, ?, 0)",
                             [(pack, slot, gift, now) for slot, gift in enumerate(gifts)])
            points = self._score("game", pack, now)
            self._set_mood("happy", now, 900)
        return pack, points, True

    def reward_cards(self, pack):
        return [(row["slot"], row["gift_id"] if row["claimed"] else None)
                for row in self.storage.conn.execute(
                    "SELECT slot, gift_id, claimed FROM gift_rewards WHERE pack = ? ORDER BY slot", (pack,))]

    def pending_packs(self):
        return [(row["pack"], row["remaining"]) for row in self.storage.conn.execute(
            """SELECT pack, COUNT(*) AS remaining FROM gift_rewards
                WHERE claimed = 0 GROUP BY pack ORDER BY MIN(created_at), pack""")]

    def reveal(self, pack, slot):
        if not pack.startswith("game:"):
            raise ValueError("这份礼物已经自动发放。")
        with self.storage.transaction() as conn:
            row = conn.execute("SELECT gift_id, claimed FROM gift_rewards WHERE pack = ? AND slot = ?",
                               (pack, slot)).fetchone()
            if row is None:
                raise ValueError("找不到这张礼物牌。")
            if not row["claimed"]:
                conn.execute("UPDATE gift_rewards SET claimed = 1 WHERE pack = ? AND slot = ?", (pack, slot))
                self._add_gift(row["gift_id"])
        return row["gift_id"]
