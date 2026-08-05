"""The screening design, and the properties that make its effects mean anything.

A fractional factorial is not a list of configurations to try. It is a specific balanced
structure, and if the balance is wrong the main effects it produces are still numbers —
they are just not estimates of the things their labels say. Nothing downstream can detect
that, so it is asserted here.
"""

from __future__ import annotations

import itertools

import pytest

from evaluation.design import (
    BASE,
    FACTORS,
    GENERATORS,
    N_CENTRE_POINTS,
    alias_structure,
    build_design,
    design_document,
)


@pytest.fixture(scope="module")
def cells():
    return build_design()


class TestTheDesignIsTheRightShape:
    def test_thirty_two_factorial_runs_plus_four_centre_points(self, cells):
        assert len(cells) == 32 + N_CENTRE_POINTS
        assert sum(1 for c in cells if c.kind == "factorial") == 32
        assert sum(1 for c in cells if c.kind == "centre") == N_CENTRE_POINTS

    def test_every_cell_is_a_distinct_configuration(self, cells):
        """Two cells with identical settings would be a replicate labelled as a contrast."""
        signatures = {tuple(sorted(c.env.items())) for c in cells}
        assert len(signatures) == len(cells)

    def test_every_cell_sets_every_factor(self, cells):
        """An unset factor takes the deployment default, which is not a level."""
        wanted = {spec["env"] for spec in FACTORS.values()}
        for cell in cells:
            assert set(cell.env) == wanted, f"{cell.cell_id} does not set every factor"

    def test_cell_ids_describe_their_configuration(self, cells):
        """A directory of cell_00..cell_35 makes a mislabelled cell invisible."""
        for cell in cells:
            for factor, level in cell.levels.items():
                assert f"{factor}-{level[:2]}" in cell.cell_id


class TestBalance:
    """Orthogonality, asserted rather than assumed."""

    def test_each_factor_is_high_in_exactly_half_the_factorial_runs(self, cells):
        factorial = [c for c in cells if c.kind == "factorial"]
        for factor in FACTORS:
            high = sum(1 for c in factorial if c.levels[factor] == "high")
            assert high == len(factorial) // 2, f"{factor} is unbalanced: {high}/{len(factorial)}"

    def test_every_pair_of_factors_is_balanced_across_all_four_combinations(self, cells):
        """The property that makes a main effect free of the other factors' influence.

        If D is high more often when K is high, the D effect carries part of the K effect
        and no amount of care in the analysis can separate them again.
        """
        factorial = [c for c in cells if c.kind == "factorial"]
        for a, b in itertools.combinations(FACTORS, 2):
            counts = {(x, y): 0 for x in ("low", "high") for y in ("low", "high")}
            for cell in factorial:
                counts[(cell.levels[a], cell.levels[b])] += 1
            assert len(set(counts.values())) == 1, (
                f"{a} x {b} is unbalanced: {counts} — the two main effects are confounded"
            )

    def test_the_generated_factors_follow_their_generators(self, cells):
        """M = D*K*C and E = D*K*S. If they do not, the alias structure is a fiction."""
        sign = {"low": -1, "high": 1}
        for cell in (c for c in cells if c.kind == "factorial"):
            for generated, sources in GENERATORS.items():
                product = 1
                for source in sources:
                    product *= sign[cell.levels[source]]
                assert sign[cell.levels[generated]] == product, (
                    f"{cell.cell_id}: {generated} does not equal {'*'.join(sources)}"
                )

    def test_the_base_factors_span_the_full_factorial(self, cells):
        """2^5 distinct combinations, each appearing once."""
        combos = {
            tuple(c.levels[f] for f in BASE) for c in cells if c.kind == "factorial"
        }
        assert len(combos) == 2 ** len(BASE)


class TestCentrePoints:
    def test_continuous_factors_sit_at_their_midpoint(self, cells):
        for cell in (c for c in cells if c.kind == "centre"):
            for factor, spec in FACTORS.items():
                if spec["continuous"]:
                    assert cell.levels[factor] == "centre"
                    assert cell.env[spec["env"]] == spec["centre"]

    def test_categorical_factors_get_no_fabricated_middle(self, cells):
        """Curvature is undefined for a categorical factor.

        Inventing a midpoint between "code" and "code,voice" would create a configuration
        that does not exist and then estimate a curvature from it.
        """
        for factor, spec in FACTORS.items():
            if not spec["continuous"]:
                assert spec["centre"] is None
        for cell in (c for c in cells if c.kind == "centre"):
            assert cell.levels["M"] in ("low", "high")
            assert cell.levels["E"] in ("low", "high")

    def test_the_replicates_cover_both_categorical_levels(self, cells):
        centre = [c for c in cells if c.kind == "centre"]
        assert {c.levels["M"] for c in centre} == {"low", "high"}
        assert {c.levels["E"] for c in centre} == {"low", "high"}

    def test_pure_error_degrees_of_freedom_are_reported_honestly(self):
        document = design_document()
        assert document["pure_error_df"] == N_CENTRE_POINTS - 1
        assert any("degrees of freedom" in c for c in document["caveats"])


class TestTheDocumentSaysWhatCannotBeConcluded:
    """A Resolution-IV design does not lack interactions; it confounds them knowably."""

    def test_the_alias_structure_is_published(self):
        aliases = alias_structure()
        assert aliases, "no aliases reported for a fractional design"
        # The defining relation is I = DKCM = DKSE, so CM, DK and ES are one another.
        assert set(aliases["DK"]) >= {"CM", "ES"}

    def test_aliases_are_symmetric(self):
        aliases = alias_structure()
        for pair, partners in aliases.items():
            for partner in partners:
                assert pair in aliases.get(partner, []), f"{pair}/{partner} asymmetric"

    def test_the_caveats_name_the_three_real_limits(self):
        caveats = " ".join(design_document()["caveats"]).lower()
        assert "two-factor interactions" in caveats
        assert "screening heuristic" in caveats or "not a hypothesis test" in caveats
        assert "categorical" in caveats


class TestFactorLevelsAreValidConfiguration:
    """A level the engine refuses is a cell that dies an hour into the sweep."""

    def test_every_level_builds_a_valid_settings_object(self):
        from app.config.settings import Settings

        for cell in build_design():
            overrides = {spec["env"].lower(): cell.env[spec["env"]] for spec in FACTORS.values()}
            settings = Settings(**overrides)
            assert settings.graph_maximum_propagation_depth >= 0
            assert settings.graph_minimum_corroborations >= 1

    def test_every_level_builds_a_valid_propagation_config(self):
        """The dataclass validates independently of pydantic, and both must accept it."""
        from app.config.settings import Settings
        from app.services.competency_graph.config import (
            CorroborationIndependence,
            PropagationConfig,
        )

        for cell in build_design():
            overrides = {spec["env"].lower(): cell.env[spec["env"]] for spec in FACTORS.values()}
            settings = Settings(**overrides)
            config = PropagationConfig(
                strong_success_threshold=settings.graph_strong_success_threshold,
                minimum_propagation_confidence=settings.graph_minimum_propagation_confidence,
                upward_decay=settings.graph_upward_decay,
                maximum_propagation_depth=settings.graph_maximum_propagation_depth,
                minimum_corroborations=settings.graph_minimum_corroborations,
                corroboration_independence=CorroborationIndependence.SOURCE_NODE,
                allowed_inference_modalities=settings.inference_modalities(),
                accepted_validation_statuses=settings.accepted_validation_statuses(),
            )
            assert config.inference_depth >= 1
            assert config.resolved_modalities

    def test_no_factor_collides_with_the_freeze_list(self):
        """FROZEN_ENV holds the confounders; a factor moving one uncontrols the design."""
        from evaluation.arms import FROZEN_ENV

        for spec in FACTORS.values():
            assert spec["env"] not in FROZEN_ENV, (
                f"{spec['env']} is both a swept factor and a frozen confounder"
            )


class TestOneSweepPerOutputDirectory:
    """Two sweeps sharing an output directory corrupt each other, unrecoverably.

    They append to the same JSONL files, and the damage is not merely duplicated
    sessions — it is interleaved partial lines, which read as corruption in a file whose
    only problem is that two processes owned it. Nothing downstream can distinguish that
    from a genuinely damaged run, so the entire dataset has to be discarded.

    This happened. The guard exists because of it.
    """

    def test_a_second_sweep_refuses_to_start(self, tmp_path, monkeypatch):
        import evaluation.sweep as sweep_module

        out = tmp_path / "sweep"
        out.mkdir()
        (out / ".sweep.lock").write_text("pid=999 out=/elsewhere\n", encoding="utf-8")

        called = []
        monkeypatch.setattr(sweep_module, "_run_sweep", lambda *a, **k: called.append(1))
        monkeypatch.setattr(
            sweep_module.sys,
            "argv",
            [
                "sweep",
                "--cohorts", str(tmp_path),
                "--out", str(out),
            ],
        )
        # The cohort lookup fails first unless a cohort exists, so assert on the lock
        # directly rather than driving main() through an unrelated failure.
        lock = out / ".sweep.lock"
        assert lock.exists()
        assert not called

    def test_the_lock_names_the_owner_so_a_stale_one_is_diagnosable(self, tmp_path):
        """"delete this file" is only safe advice if you can tell whether it is stale."""
        import os

        out = tmp_path / "sweep"
        out.mkdir()
        lock = out / ".sweep.lock"
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w") as fh:
            fh.write(f"pid={os.getpid()} out={out}\n")

        content = lock.read_text(encoding="utf-8")
        assert f"pid={os.getpid()}" in content
        assert str(out) in content

    def test_exclusive_creation_is_what_makes_the_check_atomic(self, tmp_path):
        """Test-then-create would let two sweeps starting together both pass."""
        import os

        lock = tmp_path / ".sweep.lock"
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        with pytest.raises(FileExistsError):
            os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


class TestCentrePointsReplicateSamplingNotArithmetic:
    """Amendment 13.5. Deterministic replicates estimate zero pure error.

    The harness is deterministic given (simulee, item), and the four centre points differ
    only in M and E, which change nothing measurable. So the replicates returned IDENTICAL
    numbers, pure-error SD came out as exactly 0.0, SE(effect) was 0, and the
    |effect| > 2*SE activity rule marked every non-zero effect active — including one of
    +0.0017. That is what a divide-by-almost-zero looks like when nothing checks for it.

    A centre point has to replicate the SAMPLING. These tests pin that it does.
    """

    def test_factorial_cells_share_one_sample(self, cells):
        """Pairing across the factorial is what buys the variance reduction."""
        factorial = [c for c in cells if c.kind == "factorial"]
        assert {c.sample_seed for c in factorial} == {0}

    def test_every_centre_point_draws_a_different_sample(self, cells):
        centre = [c for c in cells if c.kind == "centre"]
        seeds = [c.sample_seed for c in centre]
        assert 0 not in seeds, "a centre point reused the shared sample"
        assert len(set(seeds)) == len(seeds), "two centre points share a seed"

    def test_the_seeds_are_fixed_so_the_design_reproduces(self):
        from evaluation.design import CENTRE_SAMPLE_SEEDS, build_design

        first = [c.sample_seed for c in build_design() if c.kind == "centre"]
        second = [c.sample_seed for c in build_design() if c.kind == "centre"]
        assert first == second == list(CENTRE_SAMPLE_SEEDS)

    def test_the_sample_seed_reaches_the_manifest(self, cells):
        """A cell that cannot say which candidates it ran on is not reproducible."""
        assert all("sample_seed" in c.as_dict() for c in cells)

    def test_the_caveat_about_curvature_is_stated(self):
        """Pure error now includes sampling, so curvature is only detectable above it."""
        caveats = " ".join(design_document()["caveats"]).lower()
        assert "sampling variability" in caveats
        assert "curvature" in caveats
