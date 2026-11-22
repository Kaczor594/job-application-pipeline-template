# Your employer watch-list

Besides job boards, the agent checks the careers pages of companies you name directly, through
the public job feeds of the platforms those companies use (their "ATS"). This often finds roles
before they reach the job boards.

Until you create your own list, it uses `agent/employers.example.json`, a neutral starter set.
To make it yours:

```
cp agent/employers.example.json employers.json
```

`employers.json` sits in the repo root and is never committed. Add, remove and edit entries;
a broken or unknown entry is skipped, so it never breaks a run.

## Entry format

```json
{"company": "Example GmbH", "platform": "greenhouse", "slug": "examplegmbh"}
```

`company` is the display name. `platform` plus its locator fields tell the agent where to look.

## Finding a company's platform

Open any job ad on the company's careers site and look at the URL (or the "Apply" button's URL):

| URL looks like | platform | fields |
|---|---|---|
| `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` | `greenhouse` | `slug` |
| `jobs.lever.co/<slug>` | `lever` | `slug` |
| `<slug>.jobs.personio.de` (or `.com`) | `personio` | `slug` |
| `jobs.smartrecruiters.com/<slug>` or `careers.smartrecruiters.com/<slug>` | `smartrecruiters` | `slug` (case matters) |
| `<tenant>.wd<N>.myworkdayjobs.com/<site>` | `workday` | `host` (the whole hostname), `tenant`, `site` |
| careers site "powered by Phenom" (`/global/en/job/...`) | `phenom` | `base_url`, e.g. `https://careers.example.com/global/en` |
| Radancy / TalentBrew sites (`/job/<city>/<title>/<id>/<id>`) | `radancy` | `base_url` |
| SAP SuccessFactors career site (`/job/<title>/<id>/`) | `successfactors` | `base_url` |
| Anything else with a sitemap and structured job pages | `jsonld` | `base_url`, `job_url_re` (a regex matching job URLs) |

Examples:

```json
{"company": "Example AG", "platform": "workday", "host": "example.wd3.myworkdayjobs.com", "tenant": "example", "site": "External"}
{"company": "Example SE", "platform": "phenom", "base_url": "https://careers.example.com/global/en"}
```

Optional field for big employers: `"max_fetch": 160` caps how many job pages are read per run.

## Rules the connectors follow

They only use documented public job APIs, sitemaps and the structured job data (JSON-LD) that
career pages publish for search engines. They never scrape pages a site's `robots.txt`
disallows. Want a platform that isn't listed? Look at `agent/sources/` — each connector returns
the same shape, so adding one is self-contained.
