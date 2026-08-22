# config/

Configuration strategy: environment variables, loaded by
`src/cfb_edge_finder/config.py`'s `Settings.from_env()`. Copy `.env.example`
(repo root) to `.env` and fill in real values; `.env` is gitignored.

No YAML/JSON config framework is introduced in this foundation phase --
env vars cover every credential/endpoint this phase needs. Add a structured
config file here only once a second axis of configuration actually exists
(e.g. per-environment tuning parameters), not preemptively.
