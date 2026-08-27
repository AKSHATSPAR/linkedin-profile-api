# LinkedIn Profile API

A reverse-engineered implementation of the Tross challenge. Give it a LinkedIn
member URL and it returns the profile as predictable JSON. The service calls the
same authenticated, read-only HTTP endpoints used by LinkedIn's web client; it
does not run a browser or use the official LinkedIn Partner API.

Built by [Akshat Sparsh](https://github.com/AKSHATSPAR) for the Tross engineering
challenge.

**Live:** [API root](https://v5k8x8a787.execute-api.ap-south-1.amazonaws.com) ·
[interactive docs](https://v5k8x8a787.execute-api.ap-south-1.amazonaws.com/docs) ·
[health](https://v5k8x8a787.execute-api.ap-south-1.amazonaws.com/health)

## Try it

Interactive documentation is available at `/docs` on a running deployment.

```bash
curl --request POST 'https://v5k8x8a787.execute-api.ap-south-1.amazonaws.com/v1/profiles' \
  --header 'content-type: application/json' \
  --data '{"url":"https://www.linkedin.com/in/akshat-sparsh-b648a039a/"}'
```

There is also an evaluator-friendly `GET` form:

```bash
curl --get 'https://v5k8x8a787.execute-api.ap-south-1.amazonaws.com/v1/profiles' \
  --data-urlencode 'url=https://www.linkedin.com/in/akshat-sparsh-b648a039a/'
```

Here is an abridged response. The schema is versioned, and sections that can
contain several entries are always returned as arrays:

```json
{
  "meta": {
    "schema_version": "1.0",
    "retrieved_at": "2026-08-27T08:30:00Z",
    "source": "linkedin",
    "cached": false,
    "partial": false,
    "warnings": []
  },
  "profile": {
    "public_identifier": "akshat-sparsh-b648a039a",
    "profile_url": "https://www.linkedin.com/in/akshat-sparsh-b648a039a/",
    "first_name": "Akshat",
    "last_name": "Sparsh",
    "full_name": "Akshat Sparsh",
    "headline": "Student at Vellore Institute of Technology",
    "location": {"display_name": "Chennai, Tamil Nadu, India"},
    "images": {},
    "experience": [],
    "education": [
      {
        "school_name": "Vellore Institute of Technology",
        "date_range": {
          "start": {"year": 2023},
          "end": {"year": 2027},
          "present": false
        }
      }
    ],
    "skills": [],
    "certifications": [],
    "languages": [],
    "projects": [],
    "publications": [],
    "courses": [],
    "honors": [],
    "volunteer_experience": []
  }
}
```

Fields not present in LinkedIn's response are omitted from HTTP responses.
Unavailable sections remain empty arrays. Optional upstream section failures are
reported through `meta.partial` and `meta.warnings` instead of discarding an
otherwise useful profile.

## Design

```text
caller
  │  HTTPS, strict 512-character linkedin.com/in/... URL
  ▼
API Gateway ── profile-route burst 1, rate 0.05 requests/second
  ▼
Lambda / FastAPI ── 4 KiB body cap ── peer-IP limiter
  │
  ├── small in-memory TTL cache + one in-flight call per profile
  │
  ├── AWS Secrets Manager (lazy read on first cache miss, then memory-cached)
  │
  ▼  10-second deadline, at most 6 AWS / 8 local LinkedIn calls
LinkedIn Voyager ── identity-bound primary profile; optional sections off by default
  ▼
normalizer ── versioned public schema
```

Key choices:

- **Direct HTTP, not browser automation.** Runtime extraction uses `httpx` against
  a fixed LinkedIn origin. It never launches, controls, or depends on a browser;
  the session cookies are deployment credentials supplied out of band.
- **A small compatibility fallback.** LinkedIn changes its internal API without
  notice. The client tries two current `identity/dash/profiles` response formats
  and one older `profileView` format. All LinkedIn calls share the same small
  request budget.
- **Verify before returning.** The requested public identifier must match the
  profile at the root of LinkedIn's response. Positions, education, skills, and
  similar records must also belong to that same member. Data for another member
  is rejected rather than mixed into the result.
- **Return our schema, not LinkedIn's internals.** The parser converts entity
  graphs and URNs into typed Pydantic models. Raw upstream bodies are never
  returned to callers, including in errors.
- **Keep the public endpoint narrow.** Input parsing permits only HTTPS LinkedIn member URLs,
  rejects ports and embedded credentials, follows no redirects, caps decoded
  request and upstream response sizes, rate-limits by the actual network peer,
  and applies API Gateway throttling.
- **Minimal personal-data exposure.** Contact information is not required by the
  challenge and is disabled by default. Profile data is cached only in process
  memory and responses carry `Cache-Control: no-store`.

The protocol discovery, endpoint choices, headers, fallbacks, identity checks,
and limitations are documented in
[Reverse-engineering notes](REVERSE_ENGINEERING.md). A repository architecture
test rejects browser-automation dependencies/imports, while client tests capture
the outbound request and assert that it is a direct HTTPS call to the fixed
`www.linkedin.com/voyager/api` surface.

## Returned sections

The schema supports identity, headline, about text, location, industry,
relationship counts, profile/background images, experience, education, skills,
certifications, languages, projects, publications, courses, honors, and volunteer
experience. Contact information is modeled but requires an explicit operator
opt-in with `ALLOW_CONTACT_INFO=true`.

The primary decorated response normally contains these entity sections. The
eight additional section calls are a compatibility fallback controlled by
`LINKEDIN_FETCH_SECTION_FALLBACKS`; they are disabled in the committed defaults
and in AWS. When explicitly enabled they still share the total deadline,
response-size limit, concurrency limit, and physical-attempt budget.

## Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
cp .env.example .env
# Enter a separate, revocable LinkedIn session in .env. Never commit it.
uv run uvicorn linkedin_profile_api.app:app --reload
```

The session requires the `li_at` and `JSESSIONID` cookie values from a signed-in
LinkedIn browser. A complete `Cookie` request header from that same session can
also be supplied when LinkedIn expects more browser-session context. The two
required values must match their counterparts in the complete header. Prefer a
separate low-privilege session and revoke it after evaluation. Do not paste any
of these values into shell arguments, commits, logs, or issue trackers.

Run the complete local verification suite:

```bash
make verify
```

The tests use sanitized Voyager-shaped fixtures; no real LinkedIn response or
session is committed. They cover malformed URLs and payloads, response-size and
call limits, profile-identity mismatches, every returned section, caching and
concurrency, Secrets Manager failures, stable API errors, and the Lambda entry
point. A separate architecture test prevents browser-automation packages from
entering the runtime. CI requires at least 95% coverage and also checks formatting,
linting, strict types, locked dependencies, and reproducible production exports.

## AWS deployment

The included SAM template deploys a public HTTP API, a 256 MiB ARM64 Lambda, and
a least-privilege runtime permission that can read only the selected secret. The
profile route has a shared burst of one and replenishes at 0.05 requests per
second; other documentation and health routes retain a one-request-per-second
default.
The profile-route burst is one, the Lambda timeout is 15 seconds, and the
LinkedIn work has a 10-second total deadline with no AWS retries or section
fallbacks.

Create a Secrets Manager secret with this JSON shape:

```json
{"li_at":"REDACTED","jsessionid":"ajax:REDACTED","cookie_header":"OPTIONAL_REDACTED"}
```

The interactive helper keeps all three values out of shell history and terminal
output. On macOS, copy only the complete `Cookie` request-header value from
Chrome DevTools, then let the helper read it directly from the clipboard:

```bash
python3 scripts/configure_aws_secret.py \
  --profile tross \
  --region ap-south-1 \
  --expected-account YOUR_ACCOUNT_ID \
  --cookie-header-from-clipboard
```

The helper still asks for `li_at` and `JSESSIONID` through hidden prompts and
refuses to write unless those values match the complete header. It never prints
the clipboard contents. Copy something non-sensitive after rotation so the
header does not remain on the clipboard.

Then deploy:

```bash
sam build
sam deploy --guided --parameter-overrides LinkedInSecretArn=YOUR_SECRET_ARN
```

The custom SAM build copies only the runtime package and dependencies into the
Lambda artifact. Tests, documentation, local environment files, and the original
challenge materials are never uploaded with the function. The runtime dependency
set is an exact, hash-verified export of `uv.lock`, with Uvicorn pruned from the
Lambda artifact. It explicitly installs Python 3.12 `manylinux2014_aarch64`
wheels, so the artifact remains compatible when built from an ARM or x86
Linux/macOS development host. The optional container build also installs from a
hash-verified export and pins the Python base-image index digest.

The stack output named `ApiUrl` is the public base URL. Rotate or delete the
secret after evaluation. A `$15` AWS Budget is useful as an alert, but AWS
Budgets do **not** stop spend. The low route throttle, burst-one profile route,
short deadline, and small function bound this stack's request-driven cost under
the intended evaluation load; they cannot cap charges from unrelated resources
in the AWS account.

After the first invocation creates the Lambda log group, set a finite retention
period as an account control (the reference deployment uses 14 days):

```bash
aws logs put-retention-policy \
  --log-group-name /aws/lambda/YOUR_FUNCTION_NAME \
  --retention-in-days 14
```

## Error contract

Expected failures use the same small JSON shape:

```json
{
  "error": {
    "code": "profile_not_found",
    "message": "The LinkedIn profile was not found or is inaccessible",
    "request_id": "8da46552c13f46dda4a2389f86ff60c4"
  }
}
```

`404` means the member was not visible, `422` is invalid input, `429` is local or
upstream throttling, `502` is an unexpected LinkedIn response, and `503` means
the configured session is missing or expired.

## Known limitations and responsible use

LinkedIn's internal web API is undocumented and may change without notice.
LinkedIn also restricts automated access in its terms. This is a scoped hiring
challenge implementation: use it only with authorization, keep request volume
low, avoid collecting unnecessary personal data, and remove the session after
the evaluation window. It intentionally does not attempt CAPTCHA bypass,
credential theft, or access-control circumvention.

LinkedIn may challenge a valid browser session when it is reused from cloud
egress. In that case the API returns a sanitized authentication error and the
operator must refresh the browser-derived session; it does not follow checkpoint
redirects. Values loaded by separate page requests, such as some relationship
counts, may be absent when LinkedIn omits them from the primary profile payload.

The cache, single-flight coordinator, and peer limiter are deliberately
process-local. API Gateway provides the deployment-wide throttle; multiple local
Uvicorn workers do not share those in-memory controls. The Docker entry point
disables proxy-header interpretation. If the service is later placed behind a
reverse proxy, configure Uvicorn with an explicit trusted proxy allowlist rather
than trusting arbitrary `X-Forwarded-For` input in the application.

## License

[MIT](LICENSE)
