# Import the current prototype

The private GitHub repository was initialized with a README only. The development branch currently contains only CI scaffolding and documentation, **not** the Nexus v1.7 source.

To import the source without adding sensitive data:

1. Download the Nexus v1.7.0 PORTAL prototype ZIP from the ChatGPT conversation.
2. Extract **NexusOperative/** contents into the repository root (so `nexus/`, `portable/`, `dashboard/` and `pyproject.toml` are at the root).
3. Do not import `data/*.sqlite3`, runtime `storage/` files, `.env` or credentials. The repository-wide .gitignore excludes these.
4. Commit to the development branch, then verify the Windows Actions run.
5. The EXE workflow is not a release approval. Do not introduce live funds or trading credentials.

The GitHub Windows job requires the actual source; it intentionally fails before trying to package anything if the source is missing.
