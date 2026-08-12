"""The drift record has to be able to name what drifted.

WHAT THIS IS PROTECTING

A session writes the propagation configuration it began under, then re-checks it after every
response. If the configuration moves mid-session, the manifest written at `begin` describes
something that stopped being true — and the whole point of INV-P8 is that a sweep cell can
name the configuration that produced its numbers.

THE BUG THIS FILE EXISTS FOR

The hash digested the WHOLE manifest; the diff walked only `manifest["factors"]`. So a change
landing anywhere else produced a drift entry that said, in effect, "something changed and I
cannot tell you what" — the exact non-actionable record `_note_manifest_drift`'s own docstring
rejects.

It was not hypothetical. Replacing a bank mid-session re-derives its graph, which moves
`policy.prerequisite_edges` from nine to zero and rewrites `policy.bank_policy`. None of that
lives under `factors`, so the one operation most likely to change propagation underneath a
live candidate was the one the record could not describe.

The fix is to derive both from `comparable_manifest`, so a field the hash notices is a field
the diff can name.
"""

from __future__ import annotations

from cat_engine.engine.services.competency_graph.manifest import (
    comparable_manifest,
    manifest_diff,
    manifest_hash,
)


def manifest(**over) -> dict:
    base = {
        "bank_id": "DA",
        "code_revision": "abc123",
        "master_switch": True,
        "shadow_mode": True,
        "factors": {"upward_decay": 0.7, "maximum_propagation_depth": 4},
        "policy": {"prerequisite_edges": 9, "inert_because": {"deployment": 9}},
    }
    base.update(over)
    return base


class TestTheDiffAndTheHashCoverTheSameThing:
    """The property whose absence was the bug."""

    def test_every_field_that_moves_the_hash_can_be_named(self):
        """Field by field, across the whole manifest — not only under `factors`."""
        before = manifest()
        for field, value in (
            ("bank_id", "AIE"),
            ("master_switch", False),
            ("shadow_mode", False),
            ("factors", {"upward_decay": 0.3}),
            ("policy", {"prerequisite_edges": 0}),
        ):
            after = manifest(**{field: value})
            assert manifest_hash(before) != manifest_hash(after), field
            named = manifest_diff(before, after)
            assert named, f"{field} moved the hash and the diff named nothing"
            assert any(entry.split(".")[0] == field for entry in named), (
                f"{field} moved but the diff reported {named}"
            )

    def test_a_field_the_hash_ignores_is_not_reported_as_drift(self):
        """`code_revision` cannot change within a process. Reporting it would be noise."""
        before = manifest()
        after = manifest(code_revision="deadbeef")
        assert manifest_hash(before) == manifest_hash(after)
        assert manifest_diff(before, after) == []

    def test_an_unchanged_manifest_reports_nothing(self):
        assert manifest_diff(manifest(), manifest()) == []


class TestTheRegressionItself:
    def test_a_policy_only_change_is_named_rather_than_reported_empty(self):
        """The exact shape a bank replacement produces.

        Before the fix this returned `[]` while the hash moved, so the session recorded that
        its propagation configuration had changed and could not say how.
        """
        before = manifest()
        after = manifest(
            policy={"prerequisite_edges": 0, "inert_because": {}},
        )
        assert manifest_hash(before) != manifest_hash(after)
        assert manifest_diff(before, after) == [
            "policy.inert_because",
            "policy.prerequisite_edges",
        ]

    def test_nested_changes_are_named_one_level_down(self):
        """`policy` alone would be no more actionable than nothing."""
        named = manifest_diff(
            manifest(),
            manifest(policy={"prerequisite_edges": 9, "inert_because": {}}),
        )
        assert named == ["policy.inert_because"]


class TestComparableIsTheSharedDefinition:
    def test_it_drops_exactly_the_code_revision(self):
        """Both the hash and the diff read this, which is what stops them diverging again."""
        assert set(manifest()) - set(comparable_manifest(manifest())) == {"code_revision"}
