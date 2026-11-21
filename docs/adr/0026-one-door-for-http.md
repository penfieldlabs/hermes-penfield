# ADR-0026: One door for HTTP

- **Date:** 2026-11-20
- **Status:** Accepted. Amends [ADR-0002](0002-stdlib-only-http.md).

## Context

The API client and the sign-in code each called `urllib` themselves.
Three things followed.

`urllib` follows redirects, and repeats the request at the new address
with its `Authorization` header. Every request the plugin makes carries
a login.

Each caller handled its own subset of what can go wrong. A web page
where JSON was expected, a body cut off half way, bytes that are not
HTTP and error bodies that are not JSON objects came out as raw Python
exceptions. Hermes catches those on the tool path, so sessions kept
running, but the agent read `'NoneType' object has no attribute 'get'`
and the command line showed tracebacks.

`PENFIELD_URL` accepted any address, including plain HTTP, over which a
login crosses the network readable.

## Decision

- All requests go through `transport.send`. It never follows a redirect:
  a 3xx comes back as the answer it is and is reported with the host it
  pointed to.
- An HTTP status is an answer, not an exception. Whatever is not a
  complete HTTP answer is one exception. That includes a request the
  HTTP library refuses to send because a header holds a line break or a
  character outside Latin-1. The library's own error quotes the header,
  which is the login, so the exception raised in its place does not.
- An API key is checked before it is sent: whitespace around it is
  dropped, and a key that holds anything but visible ASCII is refused
  with a message that names the character and its position.
- The client and the sign-in code turn everything that is not the
  expected answer into the plugin's own errors. A success status with an
  unreadable body is retried like a failed connection when repeating the
  request is harmless, and never for a write that may have been applied
  (ADR-0013).
- `PENFIELD_URL`, and every endpoint the auth server's metadata names,
  must be HTTPS. Plain HTTP is accepted for this machine only
  (`localhost`, `127.0.0.1`, `::1`), which is what the tests use.
- Tokens are used only with the API address that issued them. The token
  cache records that address, and a profile pointed at another one, with
  `PENFIELD_ENV` or `PENFIELD_URL`, has no login there until it signs in
  or its API key is exchanged. A sign-in is revoked at the address that
  issued it. A cache written before the address was recorded counts as
  issued by Penfield's own API, where every earlier login was made
  unless `PENFIELD_URL` said otherwise.
- Proxies set in the environment are honoured, as before.

## Consequences

- A login is sent to the address that issued it and to the auth
  endpoints that address names, and nowhere else.
- Switching a profile between Penfield's environments means signing in
  again.
- Behind a captive portal or a failing proxy, errors say what arrived
  instead of what Python tripped over.
- A deployment that relied on a redirect from `PENFIELD_URL` has to be
  given the final address.
