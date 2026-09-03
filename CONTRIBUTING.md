# Contributing

## Principles

- Keep the SDK focused on SIP/RTP media transport.
- Keep AI providers and domain workflows outside the package.
- Preserve asynchronous, bounded, real-time behavior.
- Add tests for externally observable behavior.
- Document public contracts and operational assumptions.

## Workflow

1. Create a focused branch.
2. Install the development dependencies described in `docs/development.md`.
3. Make the smallest coherent change.
4. Update documentation and tests together with behavior.
5. Run linting, formatting checks, strict type checking, tests, and packaging.
6. Open a pull request describing behavior, risk, and verification.

Do not commit credentials, tunnel tokens, recordings, packet captures, or customer call
data. Use synthetic audio fixtures and placeholder network addresses.
