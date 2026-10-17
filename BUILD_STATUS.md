# Windows build and artifact guide (development)

Nexus Operative is a **simulation-only preview** on this branch. Live brokerage orders, ACH transfers, card collection, bank linking and cloud trading are not present.

## Why there were no artifacts
GitHub creates downloadable build artifacts **only after a workflow run reaches the artifact upload step**. Source commits and pull requests alone do not create an EXE. Past failures happened during the incomplete source import, before a successful upload.

## Build the Windows preview
1. Open https://github.com/S4-cyber/Nexus-Operative/actions/workflows/windows-release.yml
2. Click **Run workflow**.
3. Select branch `development/windows-portal-foundation`.
4. Click the **Run workflow** button in the menu.
5. Open the resulting run and inspect its **Artifacts** section after the job completes.

Expected names:
- `NexusOperative-Standalone-Windows` — created after the EXE build and startup test succeeds.
- `NexusOperative-Windows-Installer` — created after the Inno Setup packaging step succeeds.

Artifacts are in a ZIP on the workflow-run summary page. They are not in the repository's source-file view or Releases tab, and they may be unavailable if the job fails before uploading.

The executable and installer are unsigned development builds and may produce Windows SmartScreen warnings. Do not bypass security warnings on an unverified file.

## Development constraints
- Pushes to the development branch run Windows packaging and the safety regression; manual dispatch is also available.
- This branch contains the PORTAL, research modules and offline execution simulator. Sustained autonomous forward operation and external broker adapters remain unfinished.
- If the first run fails, inspect the failing step's logs. Do not enable live-money operations merely to test the packaging pipeline.

