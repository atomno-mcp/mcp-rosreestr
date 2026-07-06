# Changelog

All notable changes to `atomno-mcp-rosreestr` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project skeleton: `pyproject.toml`, FastMCP server, NSPD/PKK upstream
  client, persistent SQLite cache, Pydantic v2 schemas, error hierarchy.
- Tools: `lookup_by_cadastral`, `lookup_by_address`, `lookup_by_coords`,
  `get_cadastral_value`.
- CLI: `mcp-rosreestr` (stdio), `mcp-rosreestr doctor` for health-check.
- Tests: pytest + respx HTTP mocks.

## [0.1.0] — TBD

Initial public release. See [SPEC](../../_knowledge/specs/spec.md) for full
context.
