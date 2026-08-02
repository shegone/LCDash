# PC .227 operations skill

Use for the on-premises LCDash, MAE, Ollama, Open WebUI, backups, and Docker
host.

1. Prefer read-only status checks first.
2. Treat every service, network, secret, backup, and deployment action as a
   separate work package with a rollback point.
3. Never expose environment values, secret-file contents, or raw CAD data.
4. AI services may consume available beta capacity, but they must yield to
   LCDash, PostgreSQL, CAD, speech, backups, and alert-related services.
5. Record service changes, verification, and exact next action in the PC .227
   handoff before stopping.
