# Data boundaries

Research questions, reports, account snapshots, databases, model logs and credentials live under ignored local paths. The application does not upload them to GitHub. The public example is a separately generated, reviewed technology study; it contains bounded derived metrics, links and selected price observations, not a personal history export.

Only source files and explicitly reviewed examples should be committed. Do not use `git add -f` on ignored files. Never publish `.env`, official SDK activation files, vendor packages, personal account exports, database backups or raw article/PDF archives. Public market information remains subject to its publisher's terms; public availability does not imply unrestricted redistribution.

The default web server binds to 127.0.0.1. This is a local, single-user application, not a multi-tenant hosted service. Before deploying publicly, add authentication, per-user storage isolation, request limits and server-side secrets management. GitHub publication alone does not publish the local website or its data.
