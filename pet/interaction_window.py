"""Interaction panel for mood, care, the gift collection and games."""
from sqlite3 import Error as DatabaseError

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .character_art import CharacterArt, brand_icon
from .game_windows import GameWindow, InteractionDialog, RewardWindow
from .interaction import CARE_ACTIONS, GAME_NAMES, GIFTS, MOODS
from .interaction_art import item_icon
from .ui_theme import card, colors, icon, label, scroll_body


class InteractionWindow(InteractionDialog):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("KEI · 互动")
        self.setWindowIcon(brand_icon())
        self.resize(665, 540)
        self.setMinimumSize(510, 390)
        self.game_window = None
        self.reward_windows = {}
        self.selected_gift = None
        self._today_gift = None
        self._pending_packs = None
        self.gift_tiles = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)
        self._build_header(outer)
        self._build_sections(outer)
        footer = QHBoxLayout()
        self.voice_caption = label(role="muted")
        self.voice_caption.hide()
        outer.addWidget(self.voice_caption)
        self.controller.interaction_voice.connect(self._show_voice_caption)
        self.feedback = label(role="muted")
        footer.addWidget(self.feedback, 1)
        close = QPushButton("收起")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.controller.interaction_changed.connect(self.refresh)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._refresh_header)
        self.refresh()

    def _build_header(self, outer):
        surface, box = card(margin=11)
        row = QHBoxLayout()
        self.character = CharacterArt("ready", 74)
        row.addWidget(self.character)
        info = QVBoxLayout()
        info.setSpacing(4)
        info.addWidget(label("KEI 互动", "heading"))
        self.status = label(role="muted")
        info.addWidget(self.status)
        self.affection = QProgressBar()
        self.affection.setAccessibleName("好感度")
        self.affection.setRange(0, 100)
        self.affection.setTextVisible(False)
        self.affection.setFixedHeight(6)
        info.addWidget(self.affection)
        row.addLayout(info, 1)
        self.milestone_replay = QPushButton("试听默契语音")
        self.milestone_replay.hide()
        self.milestone_replay.clicked.connect(self._replay_milestone)
        row.addWidget(self.milestone_replay)
        box.addLayout(row)
        daily = QHBoxLayout()
        self.daily_banner = label(role="muted")
        self.daily_banner.hide()
        daily.addWidget(self.daily_banner, 1)
        self.daily_view = QPushButton("查看礼物")
        self.daily_view.hide()
        self.daily_view.clicked.connect(self._view_daily_gift)
        daily.addWidget(self.daily_view)
        box.addLayout(daily)
        outer.addWidget(surface)

    def _build_sections(self, outer):
        nav = QHBoxLayout()
        self.sections = QStackedWidget()
        self.nav_buttons = QButtonGroup(self)
        for index, (title, build) in enumerate((
            ("喂食与照顾", self._build_care), ("礼物图鉴", self._build_gifts), ("小游戏", self._build_games),
        )):
            tab = QPushButton(title)
            tab.setCheckable(True)
            tab.setProperty("role", "segment")
            self.nav_buttons.addButton(tab, index)
            nav.addWidget(tab, 1)
            page = QWidget()
            page_box = QHBoxLayout(page) if index == 1 else QVBoxLayout(page)
            page_box.setContentsMargins(0, 0, 0, 0)
            body = scroll_body(page_box, 5)
            body.setSpacing(10)
            build(body)
            body.addStretch()
            if index == 1:
                page_box.addWidget(self.gift_detail)
            self.sections.addWidget(page)
        self.nav_buttons.button(0).setChecked(True)
        self.nav_buttons.idClicked.connect(self.sections.setCurrentIndex)
        outer.addLayout(nav)
        outer.addWidget(self.sections, 1)

    def _build_care(self, box):
        grid = QGridLayout()
        help_text = {
            "cake": "草莓蛋糕", "milk_tea_cold": "冰块与珍珠", "milk_tea_hot": "暖手热饮", "rest": "进入放松心情",
        }
        self.care_buttons = {}
        for index, (action, (name, _, _)) in enumerate(CARE_ACTIONS.items()):
            tile, info = card(margin=11)
            button = QPushButton(name)
            button.setMinimumHeight(52)
            button.setIconSize(QSize(44, 44))
            button.setIcon(item_icon(action, 44) if action != "rest" else icon("focus", colors(self)["accent"], 31))
            button.clicked.connect(lambda checked=False, key=action: self._care(key))
            info.addWidget(button)
            info.addWidget(label(help_text[action], "muted"), alignment=Qt.AlignCenter)
            grid.addWidget(tile, index // 2, index % 2)
            self.care_buttons[action] = button
        box.addLayout(grid)

    def _build_gifts(self, box):
        self.gift_buttons = QButtonGroup(self)
        category = None
        for gift_id, gift in GIFTS.items():
            if gift.category != category:
                category = gift.category
                box.addWidget(label(category, "heading"))
                grid = QGridLayout()
                row = 0
                box.addLayout(grid)
            tile = QPushButton()
            tile.setCheckable(True)
            tile.setProperty("role", "segment")
            tile.setIcon(item_icon(gift_id, 31))
            tile.setIconSize(QSize(31, 31))
            tile.setMinimumHeight(62)
            tile.setAccessibleName(gift.name)
            tile.clicked.connect(lambda checked=False, key=gift_id: self._select_gift(key))
            self.gift_buttons.addButton(tile)
            grid.addWidget(tile, row // 2, row % 2)
            row += 1
            self.gift_tiles[gift_id] = tile
        detail, contents = card(margin=11)
        detail.setFixedWidth(177)
        self.gift_icon = QLabel()
        self.gift_icon.setAlignment(Qt.AlignCenter)
        self.gift_icon.setFixedHeight(72)
        contents.addWidget(self.gift_icon)
        self.gift_name = label("选择一件礼物", "heading")
        self.gift_note = label("每天首次启动或完成小游戏可获得礼物。", "muted")
        contents.addWidget(self.gift_name)
        contents.addWidget(self.gift_note)
        contents.addStretch()
        self.give_button = QPushButton("送给 KEI")
        self.give_button.setProperty("role", "primary")
        self.give_button.setEnabled(False)
        self.give_button.clicked.connect(self._give)
        contents.addWidget(self.give_button)
        self.gift_detail = detail

    def _build_games(self, box):
        self.game_buttons = {}
        descriptions = {
            "rps": "鼠标出拳，三回合结算。",
            "number": "猜 1–100 的整数，KEI 会提示偏大还是偏小。",
            "memory": "记住光环次序，完成 3、4、5 步三轮。",
        }
        for kind, title in GAME_NAMES.items():
            tile, info = card(margin=10)
            row = QHBoxLayout()
            words = QVBoxLayout()
            words.addWidget(label(title, "heading"))
            words.addWidget(label(descriptions[kind], "muted"))
            row.addLayout(words, 1)
            button = QPushButton("开始")
            button.setAccessibleName(f"开始{title}")
            button.clicked.connect(lambda checked=False, game=kind: self._open_game(game))
            row.addWidget(button)
            self.game_buttons[kind] = button
            info.addLayout(row)
            box.addWidget(tile)
        self.pending_heading = label("待翻开的礼物牌", "heading")
        box.addWidget(self.pending_heading)
        self.pending_list = QListWidget()
        self.pending_list.setFixedHeight(85)
        self.pending_list.setAccessibleName("待领取的游戏礼物")
        self.pending_list.itemActivated.connect(lambda _: self._open_pending())
        box.addWidget(self.pending_list)
        self.reopen_reward = QPushButton("打开所选礼物牌")
        self.reopen_reward.clicked.connect(self._open_pending)
        box.addWidget(self.reopen_reward, alignment=Qt.AlignRight)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.clock.start()

    def hideEvent(self, event):
        self.clock.stop()
        super().hideEvent(event)

    def set_theme(self, dark):
        super().set_theme(dark)
        self.care_buttons["rest"].setIcon(icon("focus", colors(self)["accent"], 31))
        if self.game_window:
            self.game_window.set_theme(dark)
        for dialog in self.reward_windows.values():
            dialog.set_theme(dark)

    def _refresh_header(self):
        service = self.controller.interaction
        points, stage, mood = service.status()
        text, art = MOODS[mood]
        self.status.setText(f"{stage}  ·  好感度 {points} / 100  ·  {text}")
        self.affection.setValue(points)
        self.milestone_replay.setVisible(points >= 70)
        self.character.set_state(art)
        daily = service.today_gift()
        if daily != self._today_gift:
            self._today_gift = daily
            self.daily_banner.setText(f"今日已入库 · {GIFTS[daily].name}" if daily else "")
            self.daily_banner.setVisible(daily is not None)
            self.daily_view.setVisible(daily is not None)

    def refresh(self):
        self._refresh_header()
        inventory = self.controller.interaction.inventory()
        for gift_id, tile in self.gift_tiles.items():
            count, given = inventory.get(gift_id, (0, 0))
            name = GIFTS[gift_id].name
            tile.setText(f"{name}\n×{count}" + ("  ✓" if given else ""))
            tile.setAccessibleName(f"{name}，持有 {count} 件，已赠送 {given} 次")
        if self.selected_gift:
            gift_id = self.selected_gift
            count, given = inventory.get(gift_id, (0, 0))
            gift = GIFTS[gift_id]
            self.gift_icon.show()
            self.gift_icon.setPixmap(item_icon(gift_id, 68).pixmap(QSize(68, 68), self.devicePixelRatioF()))
            self.gift_name.setText(gift.name)
            self.gift_note.setText(f"{gift.note}\n持有 {count} 件 · 已赠送 {given} 次")
            self.give_button.setEnabled(count > 0)
        else:
            self.gift_icon.hide()
        self._refresh_pending()

    def _refresh_pending(self):
        packs = self.controller.interaction.pending_packs()
        if packs == self._pending_packs:
            return
        self._pending_packs = packs
        current = self.pending_list.currentItem()
        selection = current.data(Qt.UserRole) if current else None
        self.pending_list.clear()
        for row, (identifier, count) in enumerate(packs):
            kind = identifier.split(":")[1]
            item = QListWidgetItem(f"{GAME_NAMES[kind]} · 还有 {count} 张", self.pending_list)
            item.setData(Qt.UserRole, identifier)
            if identifier == selection:
                self.pending_list.setCurrentRow(row)
        if packs and not self.pending_list.currentItem():
            self.pending_list.setCurrentRow(0)
        for widget in (self.pending_heading, self.pending_list, self.reopen_reward):
            widget.setVisible(bool(packs))

    def show_section(self, section):
        index = {"care": 0, "gifts": 1, "games": 2}[section]
        self.nav_buttons.button(index).setChecked(True)
        self.sections.setCurrentIndex(index)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_voice_caption(self, text):
        self.voice_caption.setText(text)
        self.voice_caption.setVisible(bool(text))

    def _replay_milestone(self):
        if not self.controller.replay_interaction_milestone():
            self.feedback.setText("当前暂不能播放纪念语音，请检查声音设置或稍后再试。")

    def _view_daily_gift(self):
        gift_id = self.controller.interaction.today_gift()
        if gift_id is None:
            self.refresh()
            return
        self.show_section("gifts")
        self.gift_tiles[gift_id].setChecked(True)
        self._select_gift(gift_id)
        self.controller.view_interaction_reward()

    def _run(self, method, key):
        try:
            reply, points = method(key)
        except (ValueError, DatabaseError) as error:
            self.feedback.setText(str(error))
            return
        self.feedback.setText(f"{reply}  好感度 +{points}" if points else reply)

    def _care(self, action):
        self._run(self.controller.care_for_key, action)

    def _select_gift(self, gift_id):
        self.selected_gift = gift_id
        self.refresh()

    def _give(self):
        if self.selected_gift:
            self._run(self.controller.give_kei_gift, self.selected_gift)

    def _open_game(self, kind):
        if self.game_window is None:
            dialog = GameWindow(self.controller, kind, self)
            self.game_window = dialog
            dialog.set_theme(bool(self.property("darkTheme")))
            dialog.finished.connect(self._clear_game)
        self.game_window.show()
        self.game_window.raise_()
        self.game_window.activateWindow()

    def _clear_game(self):
        self.game_window = None

    def _open_pending(self):
        item = self.pending_list.currentItem()
        if not item:
            return
        pack = item.data(Qt.UserRole)
        if pack not in self.reward_windows:
            dialog = RewardWindow(self.controller, pack, self)
            dialog.set_theme(bool(self.property("darkTheme")))
            self.reward_windows[pack] = dialog
            dialog.finished.connect(lambda _, key=pack: self.reward_windows.pop(key, None))
        dialog = self.reward_windows[pack]
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.controller.view_interaction_reward(pack)

    def closeEvent(self, event):
        if self.game_window:
            self.game_window.close()
        for dialog in tuple(self.reward_windows.values()):
            dialog.close()
        super().closeEvent(event)
