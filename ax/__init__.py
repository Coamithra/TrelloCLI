"""AX (agent experience) testing harness for the `trello` CLI.

Unit tests answer "does the code work?". These answer a different question:
"can a model that has never seen this codebase drive the CLI from `--help` and
error messages alone?" — which is what actually happens in production, where
every caller is an agent.

See ax/README.md for the loop: fanout -> failure modes -> backlog -> patch -> rerun.
"""
