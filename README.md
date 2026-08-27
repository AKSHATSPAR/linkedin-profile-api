# LinkedIn Profile API

A small, production-minded API that accepts a LinkedIn member profile URL and
returns a stable, normalized JSON document. It uses the same authenticated,
read-only data surface used by LinkedIn's web client; it does not require the
official LinkedIn Partner API.

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

The response has an explicit schema version and always returns arrays for
repeatable sections:

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
    "education": [],
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
  │  HTTPS, strict linkedin.com/in/... URL
  ▼
API Gateway ── global throttle
  ▼
Lambda / FastAPI ── per-client limiter ── bounded in-memory TTL cache
  │
  ├── AWS Secrets Manager (session read at cold start/on expiry)
  │
  ▼
LinkedIn Voyager ── profile + bounded-concurrency optional sections
  ▼
normalizer ── versioned public schema
```

Key choices:

- **Direct HTTP, not browser automation.** Browser automation was useful only to
  validate the authenticated flow. Runtime requests use `httpx`, making the
  service faster, cheaper, and easier to test.
- **Modern endpoint with a compatibility path.** The client first requests
  `identity/dash/profiles?q=memberIdentity` with two known decoration versions,
  then falls back to the older `profileView` response.
- **Stable output over upstream leakage.** LinkedIn URNs and entity graphs are
  normalized into typed Pydantic models. Upstream bodies are never returned in
  errors.
- **Safe public surface.** Input parsing permits only HTTPS LinkedIn member URLs,
  rejects ports and embedded credentials, follows no redirects, rate-limits
  requests, and applies a global API Gateway throttle.
- **Minimal personal-data exposure.** Contact information is not required by the
  challenge and is disabled by default. Profile data is cached only in process
  memory and responses carry `Cache-Control: no-store`.

## Returned sections

The schema supports identity, headline, about text, location, industry,
relationship counts, profile/background images, experience, education, skills,
certifications, languages, projects, publications, courses, honors, and volunteer
experience. Contact information is modeled but requires an explicit operator
opt-in with `ALLOW_CONTACT_INFO=true`.

## Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
cp .env.example .env
# Enter a separate, revocable LinkedIn session in .env. Never commit it.
uv run uvicorn linkedin_profile_api.app:app --reload
```

The session requires the `li_at` and `JSESSIONID` cookie values from a signed-in
LinkedIn browser. Prefer a separate low-privilege session and revoke it after
evaluation. Do not paste these values into shell arguments, commits, logs, or
issue trackers.

Run the complete local verification suite:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy linkedin_profile_api
uv run pytest --cov=linkedin_profile_api
```

The test suite uses a sanitized synthetic Voyager fixture—no real LinkedIn
response or session secret is committed.

## AWS deployment

The included SAM template deploys a public HTTP API, ARM64 Lambda, and a
least-privilege runtime permission that can read only the selected secret. It
also throttles the gateway to one request per second with a burst of two to
protect the session and control spend.

Create a Secrets Manager secret with this JSON shape:

```json
{"li_at":"REDACTED","jsessionid":"ajax:REDACTED"}
```

Then deploy:

```bash
sam build
sam deploy --guided --parameter-overrides LinkedInSecretArn=YOUR_SECRET_ARN
```

The custom SAM build copies only the runtime package and dependencies into the
Lambda artifact. Tests, documentation, local environment files, and the original
challenge materials are never uploaded with the function. It explicitly resolves
Python 3.12 `manylinux2014_aarch64` wheels, so the artifact remains compatible
when built from an ARM or x86 Linux/macOS development host.

The stack output named `ApiUrl` is the public base URL. Rotate or delete the
secret after evaluation. At challenge traffic levels, Lambda and API Gateway are
serverless and the principal standing charge is the single Secrets Manager
secret; still configure an AWS Budget appropriate for your account.

## Error contract

Expected failures use a small stable envelope:

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

## Constraints and responsible use

LinkedIn's internal web API is undocumented and may change without notice.
LinkedIn also restricts automated access in its terms. This project is a scoped
technical demonstration: use it only with authorization, keep request volume
low, avoid collecting unnecessary personal data, and remove the session after
the evaluation window. It intentionally does not attempt CAPTCHA bypass,
credential theft, or access-control circumvention.

## License

[MIT](LICENSE)
