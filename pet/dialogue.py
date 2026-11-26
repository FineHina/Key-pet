"""Local topic/keyword companion responses. No network or generated AI replies."""
from collections import deque

TOPICS = {
    "greeting": {"name": "问候", "category": "default", "fallback": "你好，我在这里。"},
    "encouragement": {"name": "鼓励", "category": "smile", "fallback": "先迈出一小步吧，慢慢来。"},
    "rest_advice": {"name": "休息", "category": "default", "fallback": "累了就休息一下，不用勉强。"},
    "coffee": {"name": "咖啡厅", "category": "default", "fallback": "选个喜欢的饮品，悠闲一会儿吧。"},
    "stars": {"name": "星空", "category": "default", "fallback": "一起看看远方的星光吧。"},
    "tease": {"name": "轻松吐槽", "category": "angry", "fallback": "真是的，偶尔也要认真一点嘛。"},
    "intro": {"name": "认识凯伊", "category": "default", "fallback": "你好，我是凯伊。"},
    "support": {"name": "心情陪伴", "category": "smile", "fallback": "先缓一缓吧，可以和我聊个轻松的话题。"},
}
KEYWORDS = (
    ("intro", ("介绍", "你是谁", "认识", "名字")),
    ("stars", ("星空", "星光", "星星", "宇宙")),
    ("coffee", ("咖啡", "饮料", "喝什么", "味道")),
    ("tease", ("吐槽", "逗我", "玩笑")),
    ("rest_advice", ("累了", "好累", "疲惫", "休息", "睡觉", "困了")),
    ("support", ("难过", "心情不好", "伤心", "不开心", "焦虑")),
    ("encouragement", ("鼓励", "加油", "坚持", "学习", "计划", "目标")),
    ("greeting", ("你好", "早上好", "晚上好", "下午好", "嗨", "hello", "hi")),
)
UNKNOWN = "我现在使用本地话题回应。可以聊问候、鼓励、休息、咖啡、星空或轻松吐槽。"


class DialogueService:
    def __init__(self, library):
        self.library = library
        self.history = deque(maxlen=30)

    def route(self, text):
        text = text.strip().casefold()
        if not text or len(text) > 500:
            raise ValueError("请输入1至500字的内容。")
        for topic, terms in KEYWORDS:
            if any(term in text for term in terms):
                return topic
        return None

    def topic(self, topic):
        if topic not in TOPICS:
            raise ValueError("未知的本地话题。")
        spec = TOPICS[topic]
        entry = self.library.choose(topic, {}, manual=True)
        # Even explicit chat uses curated A/B; archive playback belongs to audition.
        if entry and entry.get("grade", "C") not in ("A", "B"):
            entry = None
        return {"topic": topic, "text": entry["text"] if entry else spec["fallback"],
                "category": spec["category"], "voice": entry}

    def message(self, text):
        topic = self.route(text)
        reply = self.topic(topic) if topic else {"topic": None, "text": UNKNOWN, "category": "default", "voice": None}
        return reply

    def remember(self, user, reply):
        self.history.append({"user": user, "text": reply["text"], "topic": reply.get("topic")})

    def clear(self):
        self.history.clear()
