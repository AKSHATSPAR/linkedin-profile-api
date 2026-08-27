# LinkedIn Profile API

A bounded challenge implementation that accepts a LinkedIn member profile URL and
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
  │  HTTPS, strict 512-byte linkedin.com/in/... URL
  ▼
API Gateway ── route throttle + one reserved Lambda execution
  ▼
Lambda / FastAPI ── 4 KiB body cap ── peer-IP limiter
  │
  ├── bounded process-local TTL cache + same-key single-flight
  │
  ├── AWS Secrets Manager (lazy read on first cache miss, then memory-cached)
  │
  ▼  10-second deadline, at most 6 AWS / 8 local physical attempts
LinkedIn Voyager ── identity-bound primary profile; optional sections off by default
  ▼
normalizer ── versioned public schema
```

Key choices:

- **Direct HTTP, not browser automation.** Browser automation was useful only to
  validate the authenticated flow. Runtime requests use `httpx`, making the
  service faster, cheaper, and easier to test.
- **Modern endpoint with a bounded compatibility path.** The client tries two
  known `identity/dash/profiles?q=memberIdentity` decorations and then the older
  `profileView` shape. Every physical attempt, including retries and optional
  sections, consumes one shared per-request budget.
- **Fail-closed profile identity and ownership.** The profile referenced by
  LinkedIn's response root must match the requested `publicIdentifier`
  case-insensitively. Every parsed member-scoped entity (position, education,
  skill, and similar sections) must encode the same member ID in its URN.
  Wrong roots and foreign section entities are not normalized or cached, while
  unrelated non-root profile entities such as recommendations remain harmless.
- **Stable output over upstream leakage.** LinkedIn URNs and entity graphs are
  normalized into typed Pydantic models. Upstream bodies are never returned in
  errors.
- **Safe public surface.** Input parsing permits only HTTPS LinkedIn member URLs,
  rejects ports and embedded credentials, follows no redirects, caps decoded
  request and upstream response sizes, rate-limits by the actual network peer,
  and applies API Gateway throttling.
- **Minimal personal-data exposure.** Contact information is not required by the
  challenge and is disabled by default. Profile data is cached only in process
  memory and responses carry `Cache-Control: no-store`.

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
LinkedIn browser. Prefer a separate low-privilege session and revoke it after
evaluation. Do not paste these values into shell arguments, commits, logs, or
issue trackers.

Run the complete local verification suite:

```bash
make verify
```

The test suite uses sanitized synthetic Voyager fixtures—no real LinkedIn
response or session secret is committed. It covers URL ambiguity and length,
declared and streamed body limits, forged forwarding headers, profile-root and
member-ownership mismatches, malformed and oversized upstream payloads, the
exact physical-call ceiling, optional-section degradation, same-key concurrency
and cancellation,
cache expiry, Secrets Manager loading, all normalized section families, stable
HTTP errors, OpenAPI limits, and an API Gateway v2 Lambda event. CI enforces at
least 95% line coverage in addition to formatting, lint, strict typing, lockfile
integrity, and deterministic production dependency exports.

## AWS deployment

The included SAM template deploys a public HTTP API, a 256 MiB ARM64 Lambda, and
a least-privilege runtime permission that can read only the selected secret. The
profile routes have a burst of one and replenish at 0.05 requests per second;
other documentation and health routes retain a one-request-per-second default.
Reserved concurrency is one, the Lambda timeout is 15 seconds, and the LinkedIn
work has a 10-second total deadline with no AWS retries or section fallbacks.

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
challenge materials are never uploaded with the function. The runtime dependency
set is an exact, hash-verified export of `uv.lock`, with Uvicorn pruned from the
Lambda artifact. It explicitly installs Python 3.12 `manylinux2014_aarch64`
wheels, so the artifact remains compatible when built from an ARM or x86
Linux/macOS development host. The optional container build also installs from a
hash-verified export and pins the Python base-image index digest.

The stack output named `ApiUrl` is the public base URL. Rotate or delete the
secret after evaluation. A `$15` AWS Budget is useful as an alert, but AWS
Budgets do **not** stop spend. The low route throttle, short deadline, small
function, and reserved-concurrency ceiling bound this stack's request-driven
cost under the intended evaluation load; they cannot cap charges from unrelated
resources in the AWS account.

After the first invocation creates the Lambda log group, set a finite retention
period as an account control (the reference deployment uses 14 days):

```bash
aws logs put-retention-policy \
  --log-group-name /aws/lambda/YOUR_FUNCTION_NAME \
  --retention-in-days 14
```

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
LinkedIn also restricts automated access in its terms. This is a scoped hiring
challenge implementation: use it only with authorization, keep request volume
low, avoid collecting unnecessary personal data, and remove the session after
the evaluation window. It intentionally does not attempt CAPTCHA bypass,
credential theft, or access-control circumvention.

The cache, single-flight coordinator, and peer limiter are deliberately
process-local. API Gateway provides the deployment-wide throttle; multiple local
Uvicorn workers do not share those in-memory controls. The Docker entry point
disables proxy-header interpretation. If the service is later placed behind a
reverse proxy, configure Uvicorn with an explicit trusted proxy allowlist rather
than trusting arbitrary `X-Forwarded-For` input in the application.

## License

[MIT](LICENSE)
