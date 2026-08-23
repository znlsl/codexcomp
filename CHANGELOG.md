# Changelog

Notable changes to codexcomp. Format loosely follows [Keep a Changelog](https://keepachangelog.com/);
versions follow SemVer with `0.0.1`-level steps for fixes.

## [Unreleased]

### Changed
- Upstream round read timeout tightened from 600s to 120s: `read=` bounds the silence
  between consecutive bytes, not total stream duration; 21 live-verified fold rounds
  never exceeded a 30.74s gap, so 120s is a ~4x margin, and a blackholed proxy path no
  longer pins a pool connection for 10 minutes per attempt.

### Internal
- Extracted the shared-client pool lifecycle (leases, generation rotation, retired-client
  draining, pool/proxy diagnostics) out of `server.py` into `pool.py`.

### CI
- Releases are now gated on a `quality` job that reuses `ci.yml`, validates the release tag
  is an annotated tag pointing at the release SHA, that the SHA is an ancestor of `master`,
  and that the tag matches `pyproject.toml`'s version before building; adds wheel/sdist
  smoke tests (`codexcomp --help`).
- Pinned Actions runtimes used by `ci.yml` / `release.yml` to their current versions.

## [0.3.8] - 2026-07-17

### Fixed
- Upstream connection pools recover after exhaustion and failed proxy handshakes.
- Retired upstream clients drain without aborting active streams, and remain available for
  all continuation rounds that share the same per-client generation lease.

## [0.3.7] - 2026-07-10

### Fixed
- Upstream connection failures are translated consistently into downstream `502` responses
  instead of being retried inside the proxy.

### Added
- A hard 6×6 Sudoku A/B evaluation, with opt-in parallel runs and explicit high-effort
  timeouts.

## [0.3.6] - 2026-07-08

### Added
- The packaged `codexcomp-eval` command and its candy A/B evaluation.

## [0.3.5] - 2026-07-08

### Fixed
- Continuation rounds with zero reasoning tokens are classified as `zero-stall` and nudged
  again within the existing continuation budget; exhausted stalls still flush the answer.

## [0.3.4] - 2026-07-07

Transport-protocol fidelity release — all changes live-verified against the real backend
(codex CLI 0.142.5, gpt-5.5, all four reasoning efforts).

### Fixed
- **POST response headers now reach Codex**: the upstream response's `x-codex-*` rate-limit
  snapshot family, `x-models-etag` and request ids are mirrored onto the downstream SSE
  response (previously all dropped, so Codex behind the proxy saw no rate-limit state on the
  POST path).
- **Remote-compaction requests are never folded**: an input ending with a
  `compaction_trigger` item passes through as a single round even when its usage lands on a
  `518n−2` boundary — the trigger is positional and Codex expects exactly one `compaction`
  output item back. New round verdict: `compaction_request`.
- **`x-codex-turn-state` sticky-routing token replay**: if the upstream ever issues one on a
  fold's round 1, continuation rounds send it back (set-once, mirroring Codex's own per-turn
  semantics; skipped when the client pinned its own). Dormant today — the backend does not
  currently issue the header — kept as contract support.
- Fold abort paths finish deterministically (generator closed explicitly on client
  disconnect; `fold aborted downstream` is always logged); truncation-tier cap lifted
  (`--max-n` default 0 = uncapped); CLI global flags placed before a subcommand are no longer
  silently overridden by subparser defaults.

### Changed
- Per-round `round open` log line reports upstream header presence
  (`reasoning_included` / `turn_state` / request id) for wire-level diagnosis.
- Live-probed backend facts documented: neither the WS 101 handshake nor the POST response
  carries `x-reasoning-included` / `x-codex-turn-state` today, so the proxy's WS accept
  deliberately stays bare — declaring flags the upstream doesn't would skew Codex's context
  accounting relative to a direct connection.

## [0.3.3] - 2026-07-06

### Fixed
- **Long-session context loss over WebSocket**: Codex 0.142's `responses_websockets`
  protocol is stateful (`generate:false` prewarm frames, follow-ups compressed to
  `previous_response_id` + incremental input). The proxy now implements that contract
  locally — prewarms acked without generating, incremental frames rebuilt to full stateless
  input, unknown ids fail loud so Codex resends full input. Previously incremental frames
  were forwarded as-is, upstream treated them as the whole conversation, and sessions
  degraded into amnesia (#2).

## [0.3.2] - 2026-07-05

### Fixed
- `codexcompw` (Windows windowless entry) crashed at startup: pythonw starts with
  `sys.stdout/stderr = None`, which uvicorn cannot survive; streams are now bound to a log
  file under `%LOCALAPPDATA%\codexcomp\`.

## [0.3.1] - 2026-07-05

### Changed
- README rewrite (bilingual, en + zh-CN).

## [0.3.0] - 2026-07-05

- Rebrand: `codex-516-guard` → `codexcomp`. First PyPI release under the new name:
  `518n−2` detect → continue → fold state machine, WebSocket-first transport with POST SSE
  fallback, full `/v1/*` passthrough, opt-in autostart, loopback-only auth passthrough.
