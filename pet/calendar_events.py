"""Local-date Easter eggs with durable attempted/skipped daily claims."""
from datetime import datetime
import time

EVENTS = {
    "newyear": {"name": "元旦彩蛋", "topic": "season_newyear", "category": "happy", "text": "新年快乐！"},
    "halloween": {"name": "万圣节彩蛋", "topic": "season_halloween", "category": "smile", "text": "今天是万圣节，聊聊糖果吧。"},
    "xmas": {"name": "圣诞节彩蛋", "topic": "season_xmas", "category": "smile", "text": "圣诞快乐，今天聊聊礼物吧。"},
    "birthday": {"name": "生日彩蛋", "topic": "season_birthday", "category": "happy", "text": "生日快乐！"},
}
DATES = {"01-01": "newyear", "10-31": "halloween", "12-25": "xmas"}


class CalendarEvents:
    def __init__(self, storage, settings, wall_clock=time.time):
        self.storage = storage
        self.settings = settings
        self.wall_clock = wall_clock

    def today(self):
        local = datetime.fromtimestamp(self.wall_clock())
        month_day = local.strftime("%m-%d")
        key = "birthday" if self.settings.get("birthday_mmdd") == month_day else DATES.get(month_day)
        if key is None:
            return None
        return dict(EVENTS[key], event_key=key, date=local.strftime("%Y-%m-%d"))

    def claim(self, blocked=False):
        event = self.today()
        if event is None:
            return None
        skipped = blocked or not self.settings.get("calendar_events_enabled", True)
        if not self.storage.claim_calendar_event(event["date"], event["event_key"],
                                                 "skipped" if skipped else "triggered", self.wall_clock()):
            return None
        return None if skipped else event
