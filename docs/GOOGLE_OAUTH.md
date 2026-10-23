# Google setup

Use your own project, OAuth client and quota. An API key or service account
does not replace the Desktop OAuth login used by this application.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or
   select your project. Enable **YouTube Data API v3** under **APIs & Services**.
2. Under **Google Auth platform**, configure **Branding** with your app name
   and contact details. For personal use, choose **External** audience,
   keep publishing status **Testing**, and add your Google account under
   **Audience > Test users**. Organization policy can restrict these options.
3. Under **Data Access**, configure the initial read-only scope:
   `https://www.googleapis.com/auth/youtube.readonly`.
4. Under **Clients** (or **APIs & Services > Credentials**), create an OAuth
   client of type **Desktop app**. Download its JSON.
5. Create a private folder outside the clone, for example
   `$HOME/youtube-editor-secrets`, and save the download as `client.json` there.
   Keep its contents out of Git, chat, issue reports and screenshots.
6. Return to [Setup, step 2](SETUP.md#2-configure-google-and-local-paths).

During login, choose the intended Google account and channel if Google offers
a choice. Check `status` before syncing. The application has no channel-selection
CLI option; multiple-channel/Brand Account selection is not yet independently
verified. Never assume the first channel returned is the one you intended.

For later editing, configure the additional scope
`https://www.googleapis.com/auth/youtube.force-ssl` when Google requires it,
then follow the separate [write-authorization procedure](EDITING.md#3-authorize-and-execute).
The ordinary `auth` command stays read-only. Write consent is broad OAuth access;
the application's plan hash is the separate approval of specific changes.

Testing-mode refresh tokens for these scopes expire after seven days. Re-run
the appropriate auth flow when needed; do not edit tokens manually. Downloads
remain outside the clone; encrypted Windows tokens are tied to your Windows
user/device. POSIX tokens rely on local file permissions, not DPAPI encryption.

Your Cloud project supplies the quota. Check its **Quotas** page; syncing,
preflight reads and each write consume it. Current method costs and reset rules
are in [Google's quota table](https://developers.google.com/youtube/data/determine_quota_cost).
Budget reads and a safety reserve; unknown/delayed telemetry is not zero usage.

Google console labels can change. Checked against official documentation on
2026-10-24: [YouTube OAuth setup](https://developers.google.com/youtube/data/quickstart/python),
[consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent),
[token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).
