"""English-only speech detection for open / Live grading."""

from __future__ import annotations

from app.services.voice.language import looks_non_english


def test_english_answer_not_flagged() -> None:
    text = (
        "Version A creates one list shared by a and b, so a is b is True. "
        "After a.append(1), both see [1]. Version B creates two lists, so a is b "
        "is False and only a changes. Assignment evaluates the right-hand side "
        "first, which is why a, b = b, a swaps without a temporary."
    )
    assert looks_non_english(text) is False


def test_romanized_japanese_probe_flagged() -> None:
    # Representative of the Live ASR dump that was incorrectly accepted as a finish.
    text = (
        "etto chan chae zai su tan dai chura fai to mo totte mi ni towaku de kon "
        "te na ho dinti zero ai ho din to aro sa imu tin sei fun da ro buru su mon "
        "tere suzai su te bushin isu fain problem isu tatti isu tappuru and ti "
        "tappurus e ah eh imyutaburu es ti weru aro stin sted like e ti e "
        "uwe asa imu taza e ah bigguri su andri sute ringu aro suis "
        "ライク e tei uwe asa imu"
    )
    assert looks_non_english(text) is True


def test_cjk_flagged() -> None:
    assert looks_non_english("タプルはイミュータブルなので代入できません。") is True
