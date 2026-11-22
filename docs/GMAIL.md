# Gmail add-on (optional)

Without Gmail the agent finds jobs through job boards and your employer list. With it, it also
reads the job-alert emails you already get (LinkedIn, Indeed, StepStone, XING, Glassdoor,
Arbeitsagentur, …). It also notices replies from employers you applied to and suggests status
updates on the dashboard, which you accept or dismiss. And it emails you the morning digest.

You don't need a Gmail label or filter: it finds alert emails by sender and subject in the
last 7 days and labels handled ones `JobAgent/Processed`.

Access goes through **your own** Google Cloud app, so nobody else is involved. The app asks
for permission to read and label mail and to send the digest to you. It takes about 10
minutes, once.

## Steps

1. **Create a project.** Go to https://console.cloud.google.com/, then project picker →
   *New project*. Any name works, e.g. `job-agent`.
2. **Enable the Gmail API.** Open *APIs & Services → Library*, search "Gmail API" and click
   *Enable*.
3. **Set up the consent screen.** Under *APIs & Services → OAuth consent screen*
   (sometimes called *Google Auth Platform → Branding / Audience*):
   - User type **External**, app name e.g. `job-agent`, your email as support + developer contact.
   - Under *Audience → Test users*, add your own Gmail address.
   - Then **Publish app**, so its status is *In production*. You don't need Google's
     verification for personal use; you'll click past an "unverified app" warning once. If
     you skip publishing, Google expires your login after about 7 days and you'd have to
     re-authorize every week.
4. **Create the credentials.** Open *APIs & Services → Credentials → Create credentials →
   OAuth client ID*, application type **Desktop app**. Download the JSON and save it as:
   ```
   ~/.config/job-agent/gmail_client_secret.json
   ```
   (`mkdir -p ~/.config/job-agent` first. That folder is outside the repo on purpose.)
5. **Turn it on.** Add this line to `.env` in the repo:
   ```
   JOBAGENT_GMAIL=1
   ```
6. **Authorize.** Run
   ```
   ./scripts/manage.sh authorize
   ```
   A browser window opens. Pick your account, accept the "unverified app" warning
   (*Advanced → Go to job-agent*) and allow access. This saves
   `~/.config/job-agent/gmail_token.json`.
7. **Check.** `python3 scripts/doctor.py` should show ✓ for *Gmail OAuth client* and
   *Gmail authorized*.

## Options (in `.env`)

| Setting | Effect |
|---|---|
| `JOBAGENT_JOB_LABEL=Jobs` | Only read alert emails under this label (if you already filter them) |
| `JOBAGENT_EXTRA_ALERT_SENDERS=alerts@board.example,jobs@other.example` | More alert senders to include |

## If it stops working

When Gmail access fails, the nightly run carries on with job boards and writes
`logs/AUTH_PROBLEM.txt`, and `doctor.py` flags it. Fix: run `./scripts/manage.sh authorize`
again and delete that file. To revoke access at any time, go to
https://myaccount.google.com/permissions.

To turn the add-on off, set `JOBAGENT_GMAIL=0` (or delete the line).
