"""Rules for the three small games. Windows own presentation and reveal timing."""
import random


HANDS = ("石头", "剪刀", "布")


class RockPaperScissors:
    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random.Random()
        self.rounds = []
        self.opponent = self.rng.randrange(len(HANDS))
        self.awaiting_pick = True

    @property
    def done(self):
        return len(self.rounds) == 3

    def play(self, hand):
        if not self.awaiting_pick or hand not in range(len(HANDS)):
            raise ValueError("本轮已经揭晓。")
        outcome = (self.opponent - hand) % 3
        self.rounds.append(outcome)
        self.awaiting_pick = False
        return HANDS[self.opponent], ("平局", "你赢了", "KEI 赢了")[outcome]

    def next_round(self):
        if self.done or self.awaiting_pick:
            return
        self.opponent = self.rng.randrange(len(HANDS))
        self.awaiting_pick = True

    def outcome(self):
        user, key = self.rounds.count(1), self.rounds.count(2)
        return "draw" if user == key else "player_win" if user > key else "kei_win"

    def summary(self):
        result = {"draw": "平局", "player_win": "你赢了", "kei_win": "KEI 赢了"}[self.outcome()]
        return f"{result} · 你 {self.rounds.count(1)} 胜，KEI {self.rounds.count(2)} 胜"


class GuessNumber:
    def __init__(self, rng=None):
        self.secret = (rng if rng is not None else random.Random()).randint(1, 100)
        self.guesses = {}

    @property
    def done(self):
        return self.secret in self.guesses

    def guess(self, value):
        if self.done:
            raise ValueError("这局已经猜中。")
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError("请输入 1–100 之间的整数。")
        if value not in self.guesses:
            self.guesses[value] = ("猜小了" if value < self.secret else
                                   "猜大了" if value > self.secret else "猜中了")
        return self.guesses[value]

    def summary(self):
        return f"猜中了 {self.secret} · 共尝试 {len(self.guesses)} 个数字"


class HaloMemory:
    LENGTHS = (3, 4, 5)

    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random.Random()
        self.round_index = 0
        self.position = 0
        self.sequence = self._sequence()

    def _sequence(self):
        return tuple(self.rng.choices(range(4), k=self.LENGTHS[self.round_index]))

    @property
    def done(self):
        return self.round_index == len(self.LENGTHS)

    def pick(self, symbol):
        if self.done or symbol not in range(4):
            raise ValueError("当前无法输入该符号。")
        if symbol != self.sequence[self.position]:
            self.position = 0
            return "retry"
        self.position += 1
        if self.position < len(self.sequence):
            return "correct"
        self.position = 0
        self.round_index += 1
        if self.done:
            return "done"
        self.sequence = self._sequence()
        return "next"

    def replay(self):
        self.position = 0

    def summary(self):
        return "三轮光环序列全部复现成功"


GAMES = {"rps": RockPaperScissors, "number": GuessNumber, "memory": HaloMemory}
