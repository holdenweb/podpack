# ADR-0030: Apps report health and status by overriding, and cannot fail a site by accident

**Status:** Accepted

**Date:** 2026-08-13

## Context

`/healthz` ran one `SELECT 1`. That was the whole of a podpack site's health,
and it was adequate while SQL was the only thing a site could depend on. It
stopped being adequate the moment MongoDB became a service a site can run
(ADR-0028): a site with an unreachable Mongo container reported itself
perfectly healthy, and the container healthcheck — which gates the entire
compose stack — believed it.

`/_status` had the mirror-image gap in a milder form. It reports a great deal
about each installed app: where its data and log directories are, whether
they are writable, which tables it owns, what shipped files arrived. All of
that podpack knows *about* the app. None of it is what the app knows about
itself.

## Decision

`SiteApp` gains two optional methods, and an app subclasses to override them:

```python
class Notes(SiteApp):
    def healthz(self) -> Health | None: ...
    def status(self) -> dict | None: ...
```

Both default to returning `None`, and **`None` means "not reported" rather
than "healthy"** — an app that has never been asked is distinguishable from
one with nothing wrong.

Three rules make them safe to call on a hot path:

- **A failing app does not by itself make the site unhealthy.** `/healthz`
  stays 200 and names the app. An app may pass `fatal=True` for the case
  where the site genuinely has no purpose without it, and then the endpoint
  answers 503.
- **An exception in either method is caught and reported**, never
  propagated.
- **They may do I/O**, and podpack reports how long `healthz()` took.

Methods rather than fields, because an app that wants one usually wants both
and usually has state to consult; and in-process rather than podpack
fetching an app's own health URL over HTTP, which would answer a question
the framework can simply ask.

## Consequences

An app can now say that the store it depends on is unreachable — which is
how it expresses a need **without** reopening the dependency question
ADR-0028 closed. The site owner still chooses services; the app reports
whether what it needs is working. Reporting, not requiring.

The non-fatal default is the load-bearing choice and the least obvious one.
It is wrong in the narrow sense: a site with a broken feature is not fully
healthy, and `/healthz` will say `ok`. It is right in the sense that
matters, because that endpoint is not a truth oracle — it is the signal
compose uses to decide whether this container should be serving traffic at
all. A site whose PDF tools cannot reach a scratch directory should keep
serving its front page. Making the honest answer the dangerous one would
have taught people to stop declaring health at all.

The costs are real. `/healthz` is no longer constant-time or predictable: it
is as slow as the slowest app that answers, on an endpoint hit every ten
seconds, and podpack polices none of that — it reports the milliseconds and
leaves the judgement where the knowledge is (ADR-0023's habit). An app can
therefore make its site's healthcheck time out, and the timeout will be
attributed to the container rather than to the app until somebody reads the
report. And `/_status` grows a surface an app controls, on an endpoint that
**has no authentication** — which was already true of everything else it
reports, and is now more worth deciding about than it was.

## Alternatives considered

- **A `health=` callable field**, matching `init=`. Symmetrical with the
  existing contract and worse in practice: a check almost always needs the
  app's own state, so the callable would close over what a method already
  has.
- **Apps declaring the URL of their own health endpoint.** Then podpack
  fetches its own site over HTTP to learn something it could have asked
  in-process — a network hop, a second failure mode, and an authentication
  question, for nothing.
- **Fatal by default.** Truthful, and it hands every app a switch that stops
  the site. The container healthcheck makes that a deployment decision, not
  a reporting one.
- **Aggregating into `/healthz` only, leaving `/_status` alone.** Rejected
  because the two questions are different: "should this container serve
  traffic" and "what is going on in here". The second is where detail
  belongs, and it is where an operator already looks.
- **Caching results for a few seconds.** Real protection against the
  ten-second poll, and it makes an endpoint answer about a past that may no
  longer be true, during exactly the incident when that matters. Deferred:
  the trigger is a real app whose check is genuinely expensive.
