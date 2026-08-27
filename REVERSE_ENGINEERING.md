# Reverse-engineering notes

This service implements the Tross challenge through direct HTTP requests to the
read-only LinkedIn surface used by LinkedIn's own web client. The extraction path
does not launch or control Chrome, Playwright, Selenium, or another browser.

## Protocol observations

The web client represents a member by a public identifier—the final segment in
`https://www.linkedin.com/in/{publicIdentifier}/`. After strict URL validation,
the service sends that identifier to the fixed origin
`https://www.linkedin.com/voyager/api`; user input never controls the upstream
scheme, host, port, or redirect destination.

The primary request is:

```text
GET /voyager/api/identity/dash/profiles
    ?q=memberIdentity
    &memberIdentity={publicIdentifier}
    &decorationId={known full-profile decoration}
```

The request uses LinkedIn's normalized JSON media type and Rest.li protocol
header. Authentication comes from the configured `li_at` cookie. LinkedIn's
`JSESSIONID` value is also sent as a cookie, with its unquoted `ajax:...` value
used as the CSRF header. These values are read lazily from the environment or AWS
Secrets Manager and never returned by the API.

Two known full-profile decoration revisions are tried because the internal
schema is undocumented and changes over time. A bounded legacy
`/identity/profiles/{publicIdentifier}/profileView` call is the final primary
compatibility path. Extra section calls exist only as an opt-in fallback and are
disabled in the AWS deployment.

## Response-graph validation

Modern responses are normalized entity graphs. The `data.*elements` references
identify the root profile inside `included`; unrelated profile entities may also
be present. The client and parser independently require every rooted profile
identity to match the requested public identifier.

Profile sections such as positions, education, skills, certifications, projects,
and publications encode their owning member in an entity URN. Before any such
entity is normalized, its owner must match the root profile's member ID. The same
rule applies to the legacy compatibility shape. If outer legacy profile data and
a nested `miniProfile` disagree about either the public identifier or member URN,
the response is rejected instead of mixing fields from different people.

## Bounded behavior

The implementation follows no redirects, limits decoded response bytes, limits
documents/entities, applies a total deadline, and charges every physical request
(including retries and fallbacks) against one attempt budget. Contact data and
additional section fallbacks are disabled by default. Expected URL, credential,
HTTP transport, JSON, identity, ownership, and parser failures become stable
public error envelopes without including upstream bodies or provider details.

## Reproducible proof

All tests use synthetic Voyager-shaped fixtures and `httpx.MockTransport`; no
LinkedIn session or recorded personal-data response is committed. The
architecture test statically rejects browser-automation runtime dependencies and
imports. A client test captures the actual outbound request construction and
asserts HTTPS, the fixed `www.linkedin.com` host, and the `/voyager/api` path.

LinkedIn's internal API is undocumented and may change. Decorations and response
shapes are therefore isolated behind the client/parser boundary rather than
leaking into the public schema.
