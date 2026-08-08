# Static and dependency analysis

| Check | Result |
|---|---|
| Ruff | Application/boundary scope PASS when `EXE002` is excluded; 67 genuine auxiliary-tooling findings remain repository-wide. The NTFS workspace reports every Python file executable, producing 164 additional `EXE002` findings even though tracked file modes are `100644` |
| mypy, application + evaluation | PASS after remediation: zero errors across 113 files |
| Bandit | PASS critical gate: 0 high, 1 medium, 21 low findings |
| pip-audit | PASS: no known vulnerabilities in the resolved declared requirements |
| tracked-secret pattern scan | PASS: no matching tracked API key or private-key material |
| Semgrep | NOT_RUN: not installed |
| Coverage | 63% under `--source=app,evaluation`; HTTP entrypoint 65%, voice runner 94%, realtime transport 73%, realtime room 72% |

The three baseline high-severity Bandit findings were fixed by restoring TLS verification
for Streamlit health, room creation, and room retrieval calls. The remaining medium finding
is a hardcoded `/tmp` path inside the remote code sandbox harness and is not a host execution
path. No `shell=True`, unsafe deserialization, tracked secret, or host-side execution of
candidate code was found; candidate code is sent to E2B.

The test coverage number includes the broad offline evaluation package. Measurement-core
modules remain well covered (IRT 89%, convergence 91%, graph propagation 100%,
orchestrator outcome 100%, variables 97%). Direct async HTTP and fake-provider tests now
cover stale concurrent submissions, state capacity, candidate payload filtering, realtime
handshake timeout, writer failure, provider close, opening-prompt failure, and transport
cleanup. The Streamlit Live bridge remains weak at 36%, and no real-gateway/load run was
available.
