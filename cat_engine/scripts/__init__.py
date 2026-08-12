"""Bank-authoring and calibration scripts.

Not part of the module's runtime surface and excluded from a wheel — a host installs the
assessment module, not the tooling that built the banks it ships with. A package rather
than loose files only so `cat_engine.tests.test_edge_validity` can import
`validate_prerequisite_edges` and assert against the real thing.
"""
