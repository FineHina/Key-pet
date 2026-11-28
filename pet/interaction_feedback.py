"""Presentation cues for successful KEI interactions, keyed to the original voice IDs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    pose: str
    expression: str
    seconds: float
    voices: tuple = ()


CUES = {
    "familiar": Cue("tilt", "smile", 0.85, ("ch0335_battle_recovery_1",)),
    "bonded": Cue("warm_nod", "happy", 1.2, ("ch0335_battle_buffed_1", "ch0335_battle_recovery_1")),
    "bonded_replay": Cue("warm_nod", "smile", 1.2, ("ch0335_growup_4",)),
    "food": Cue("curious", "smile", 1.2, ("ch0335_cafe_monolog_2", "ch0335_battle_recovery_1")),
    "rest": Cue("relax", "default", 2.6, ("ch0335_cafe_monolog_3", "ch0335_carrier_monolog_6")),
    "gift": Cue("receive", "happy", 1.05, ("ch0335_battle_recovery_1", "ch0335_battle_buffed_1")),
    "reward": Cue("nod", "smile", 0.85, ("ch0335_eventmission_get_1_1",)),
    "player_win": Cue("nod", "smile", 0.9, ("ch0335_eventmission_get_1_3",)),
    "kei_win": Cue("proud", "smile", 1.0, ("ch0335_battle_victory_1",)),
    "draw": Cue("tilt", "default", 0.75),
    "number": Cue("hop", "happy", 0.85, ("ch0335_eventmission_get_1_3",)),
    "memory": Cue("halo", "happy", 1.0, ("ch0335_exskill_level_3",)),
}


def milestone(before, after):
    if before < 70 <= after:
        return "bonded", "好感度达到「默契」"
    if before < 30 <= after:
        return "familiar", "好感度达到「熟悉」"
    return None
