"""Mouse-driven games and the reward windows shared with the interaction panel."""
from sqlite3 import Error as DatabaseError
from uuid import uuid4

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QListWidget, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .interaction import GAME_NAMES, GIFTS
from .interaction_art import item_icon
from .mini_games import GAMES, HANDS
from .ui_theme import apply_theme, apply_titlebar, colors, icon, label

SYMBOLS = ("△", "◆", "◎", "✦")
GAME_GUIDES = {
    "rps": "用鼠标出拳。KEI 已选好本轮的手势，等你来揭晓。",
    "number": "KEI 想好了一个 1–100 的整数。试着猜中它。",
    "memory": "观看符号亮起的顺序，再用鼠标按同样的顺序点回去。",
}


class RewardPane(QWidget):
    def __init__(self, controller, pack, summary, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.pack = pack
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        box.addWidget(label(summary, "heading"))
        box.addWidget(label("翻开三张牌即可领取礼物。未翻开的牌会保留在互动面板。", "muted"))
        row = QHBoxLayout()
        self.cards = []
        for slot in range(3):
            button = QPushButton()
            button.setIconSize(QSize(36, 36))
            button.setMinimumHeight(94)
            button.clicked.connect(lambda checked=False, n=slot: self._flip(n))
            self.cards.append(button)
            row.addWidget(button, 1)
        box.addLayout(row)
        self.status = label(role="muted")
        box.addWidget(self.status)
        self.controller.interaction_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        records = dict(self.controller.interaction.reward_cards(self.pack))
        for slot, button in enumerate(self.cards):
            gift = records.get(slot)
            if gift is None:
                button.setText(f"翻开第 {slot + 1} 张")
                button.setIcon(icon("gift", colors(self.window())["accent"], 36))
                button.setEnabled(slot in records)
            else:
                button.setText(f"{GIFTS[gift].name}\n已入库")
                button.setIcon(item_icon(gift, 36))
                button.setEnabled(False)

    def _flip(self, slot):
        try:
            gift = self.controller.reveal_gift(self.pack, slot)
        except (ValueError, DatabaseError) as error:
            self.status.setText(str(error))
            return
        self.status.setText(f"已领取「{GIFTS[gift].name}」。")


class InteractionDialog(QDialog):
    """Modeless companion dialogs do not block the assistant or reminder window."""

    def set_theme(self, dark):
        apply_theme(self, dark)
        apply_titlebar(self)
        for pane in self.findChildren(RewardPane):
            pane.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        available = self.screen().availableGeometry()
        margins = self.windowHandle().frameMargins()
        self.resize(min(self.width(), available.width() - margins.left() - margins.right()),
                    min(self.height(), available.height() - margins.top() - margins.bottom()))
        frame = self.frameGeometry()
        self.move(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
                  max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
        apply_titlebar(self)


class RewardWindow(InteractionDialog):
    def __init__(self, controller, pack, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("KEI · 礼物牌")
        self.resize(510, 310)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 20, 20, 20)
        kind = pack.split(":")[1]
        box.addWidget(RewardPane(controller, pack, f"{GAME_NAMES[kind]} · 完成奖励"))
        close = QPushButton("关闭")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        box.addWidget(close, alignment=Qt.AlignRight)
        box.addStretch()


class GameWindow(InteractionDialog):
    def __init__(self, controller, kind, parent):
        super().__init__(parent)
        self.controller = controller
        self.kind = kind
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"KEI · {GAME_NAMES[kind]}")
        self.resize(535, 410)
        self.setMinimumSize(470, 350)
        self.sequence_timer = QTimer(self)
        self.sequence_timer.setSingleShot(True)
        self.sequence_timer.timeout.connect(self._play_memory_step)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 19, 20, 18)
        layout.setSpacing(11)
        layout.addWidget(label(GAME_NAMES[kind], "title"))
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.voice_caption = label(role="muted")
        self.voice_caption.hide()
        controller.interaction_voice.connect(self._show_voice_caption)
        layout.addWidget(self.voice_caption)
        close = QPushButton("关闭")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        layout.addWidget(close, alignment=Qt.AlignRight)
        self._new_game()

    def _show_voice_caption(self, text):
        if self.model.done or not text:
            self.voice_caption.setText(text)
            self.voice_caption.setVisible(bool(text))

    def _new_game(self):
        for index in range(self.pages.count() - 1, -1, -1):
            page = self.pages.widget(index)
            self.pages.removeWidget(page)
            page.deleteLater()
        self.model = GAMES[self.kind]()
        self._show_voice_caption("")
        self.match_id = uuid4().hex
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addWidget(label(GAME_GUIDES[self.kind], "muted"))
        self.status = label(role="heading")
        box.addWidget(self.status)
        setups = {"rps": self._setup_rps, "number": self._setup_number, "memory": self._setup_memory}
        setups[self.kind](box)
        self.retry_reward = QPushButton("重试结算")
        self.retry_reward.hide()
        self.retry_reward.clicked.connect(self._finish)
        box.addWidget(self.retry_reward)
        self.pages.addWidget(body)
        self.pages.setCurrentWidget(body)
        if self.kind == "number":
            self.number_input.setFocus()

    def _setup_rps(self, box):
        self.status.setText("第 1 / 3 回合")
        row = QHBoxLayout()
        self.hands = []
        for number, text in enumerate(HANDS):
            button = QPushButton(text)
            button.setMinimumHeight(76)
            button.clicked.connect(lambda checked=False, hand=number: self._pick_hand(hand))
            row.addWidget(button, 1)
            self.hands.append(button)
        box.addLayout(row)
        self.advance_round = QPushButton("下一轮")
        self.advance_round.setProperty("role", "primary")
        self.advance_round.hide()
        self.advance_round.clicked.connect(self._next_hand)
        box.addWidget(self.advance_round, alignment=Qt.AlignRight)
        box.addStretch()

    def _pick_hand(self, hand):
        if not self.model.awaiting_pick:
            return
        move, outcome = self.model.play(hand)
        self.status.setText(f"第 {len(self.model.rounds)} / 3 回合 · 你出 {HANDS[hand]}，KEI 出 {move} · {outcome}")
        for button in self.hands:
            button.setEnabled(False)
        if self.model.done:
            self._finish()
        else:
            self.advance_round.show()

    def _next_hand(self):
        self.model.next_round()
        self.advance_round.hide()
        self.status.setText(f"第 {len(self.model.rounds) + 1} / 3 回合")
        for button in self.hands:
            button.setEnabled(True)

    def _setup_number(self, box):
        self.status.setText("等你猜第一个数字")
        row = QHBoxLayout()
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("1–100")
        self.number_input.setAccessibleName("猜测数字")
        self.number_input.setMaxLength(3)
        self.number_input.returnPressed.connect(self._guess)
        row.addWidget(self.number_input, 1)
        self.submit_guess = QPushButton("猜这个数")
        self.submit_guess.setProperty("role", "primary")
        self.submit_guess.setAutoDefault(False)
        self.submit_guess.clicked.connect(self._guess)
        row.addWidget(self.submit_guess)
        box.addLayout(row)
        self.history = QListWidget()
        self.history.setAccessibleName("猜测记录")
        box.addWidget(self.history, 1)

    def _guess(self):
        if self.model.done:
            return
        raw = self.number_input.text().strip()
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 100:
            self.status.setText("请输入 1–100 之间的整数。")
            self.number_input.selectAll()
            return
        guess = int(raw)
        repeated = guess in self.model.guesses
        result = self.model.guess(guess)
        suffix = "（已猜过）" if repeated else ""
        self.status.setText(f"{guess} · {result}{suffix}")
        if not repeated:
            self.history.addItem(f"{guess} · {result}")
            self.history.scrollToBottom()
        if self.model.done:
            self.submit_guess.setEnabled(False)
            self._finish()
        else:
            self.number_input.clear()
            self.number_input.setFocus()

    def _setup_memory(self, box):
        self.status.setText("第 1 / 3 轮 · 先看 3 步")
        self._accept_symbols = False
        self._pulse_index = 0
        self._highlight = None
        self.memory_display = label("等待播放", "heading")
        self.memory_display.setAlignment(Qt.AlignCenter)
        box.addWidget(self.memory_display)
        row = QHBoxLayout()
        self.symbol_buttons = []
        for index, symbol in enumerate(SYMBOLS):
            button = QPushButton(symbol)
            button.setAccessibleName(("三角", "菱形", "圆环", "四角星")[index])
            button.setMinimumHeight(64)
            button.setProperty("role", "memoryTile")
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, n=index: self._pick_symbol(n))
            row.addWidget(button, 1)
            self.symbol_buttons.append(button)
        box.addLayout(row)
        self.replay_sequence = QPushButton("观看顺序")
        self.replay_sequence.setProperty("role", "primary")
        self.replay_sequence.clicked.connect(self._show_sequence)
        box.addWidget(self.replay_sequence, alignment=Qt.AlignRight)
        box.addStretch()

    def _show_sequence(self):
        self.sequence_timer.stop()
        self.model.replay()
        self._accept_symbols = False
        self._pulse_index = 0
        self._highlight = None
        self.replay_sequence.setEnabled(False)
        for button in self.symbol_buttons:
            button.setEnabled(False)
        self.status.setText(f"第 {self.model.round_index + 1} / 3 轮 · 观看顺序")
        self._play_memory_step()

    def _play_memory_step(self):
        if self._highlight is not None:
            button = self.symbol_buttons[self._highlight]
            button.setEnabled(False)
            button.setProperty("lit", False)
            button.style().unpolish(button)
            button.style().polish(button)
            self._highlight = None
            self.memory_display.setText("·")
            self.sequence_timer.start(250)
        elif self._pulse_index < len(self.model.sequence):
            index = self.model.sequence[self._pulse_index]
            self._pulse_index += 1
            self._highlight = index
            button = self.symbol_buttons[index]
            button.setEnabled(True)
            button.setProperty("lit", True)
            button.style().unpolish(button)
            button.style().polish(button)
            self.memory_display.setText(f"{self._pulse_index} / {len(self.model.sequence)}  ·  {SYMBOLS[index]}")
            self.sequence_timer.start(650)
        else:
            self._accept_symbols = True
            self.memory_display.setText("轮到你了")
            self.replay_sequence.setText("重看顺序")
            self.replay_sequence.setEnabled(True)
            for button in self.symbol_buttons:
                button.setEnabled(True)

    def _pick_symbol(self, index):
        if not self._accept_symbols:
            return
        outcome = self.model.pick(index)
        if outcome == "correct":
            self.memory_display.setText(f"已输入 {self.model.position} / {len(self.model.sequence)}")
            return
        self._accept_symbols = False
        for button in self.symbol_buttons:
            button.setEnabled(False)
        if outcome == "done":
            self.replay_sequence.setEnabled(False)
            self._finish()
        elif outcome == "retry":
            self.memory_display.setText("顺序有一点不同，重看这一轮再试。")
        else:
            self.status.setText(f"第 {self.model.round_index + 1} / 3 轮 · {len(self.model.sequence)} 步")
            self.memory_display.setText("这一轮通过，准备看下一轮吧。")
            self.replay_sequence.setText("观看下一轮")

    def _finish(self):
        if not self.model.done:
            return
        self.sequence_timer.stop()
        outcome = self.model.outcome() if self.kind == "rps" else self.kind
        summary = self.model.summary()
        try:
            pack, points = self.controller.finish_interaction_game(
                self.kind, self.match_id, outcome=outcome, summary=summary)
        except (ValueError, DatabaseError) as error:
            self.status.setText(str(error))
            self.retry_reward.show()
            return
        result = QWidget()
        box = QVBoxLayout(result)
        box.setContentsMargins(0, 0, 0, 0)
        if points:
            summary += f" · 好感度 +{points}"
        if self.kind == "rps":
            box.addWidget(label(self.status.text(), "muted"))
        box.addWidget(RewardPane(self.controller, pack, summary))
        box.addStretch()
        again = QPushButton("再玩一场")
        again.clicked.connect(self._new_game)
        box.addWidget(again, alignment=Qt.AlignRight)
        self.pages.addWidget(result)
        self.pages.setCurrentWidget(result)

    def done(self, result):
        self.sequence_timer.stop()
        super().done(result)
