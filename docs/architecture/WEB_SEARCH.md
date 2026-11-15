# Web search (native)

Implemented in `backend/app/search/`. It gives the chat `web_search` tool and the research agent a search that does not depend on Gemini Google Search grounding.

## Providers and order

`MAYA_SEARCH_PROVIDERS` is a comma list; the default is `hn,arxiv,stackexchange,ddgs,searxng,brave,tavily,wikipedia`. An unknown name stops startup with a plain error. **Google Search grounding (`gemini`) is NOT in the default.** Its quota is small and was the reason the owner saw "Google Search" failing when the free engines were blocked; it runs only when `gemini` is named in `MAYA_SEARCH_PROVIDERS`. Research and deep research never call it through the chain (the `MAYA_RESEARCH_BACKEND` setting decides about grounding there).

| Name            | What it is                                                                                                                | Needs                 |
| :-------------- | :------------------------------------------------------------------------------------------------------------------------ | :-------------------- |
| `hn`            | Hacker News through the Algolia API; only when the query names Hacker News                                                | nothing               |
| `arxiv`         | arXiv Atom API; only for a query about papers, preprints or arXiv                                                         | nothing               |
| `stackexchange` | Stack Overflow through the keyless Stack Exchange API (small quota); only for a programming question or error text        | nothing               |
| `ddgs`          | the `ddgs` library asked for ONE engine at a time, rotating `MAYA_DDGS_ENGINES` (duckduckgo, bing, brave, yahoo, mojeek) | nothing               |
| `searxng`       | the JSON API of the SearXNG instances in `MAYA_SEARXNG_URLS` (`format=json`)                                              | `MAYA_SEARXNG_URLS`   |
| `brave`         | Brave Search API over `httpx` (web and news)                                                                              | `MAYA_BRAVE_API_KEY`  |
| `tavily`        | Tavily search API over `httpx`, no SDK                                                                                    | `MAYA_TAVILY_API_KEY` |
| `wikipedia`     | Wikipedia REST search; encyclopedic queries and the last native fallback (not for news queries)                           | nothing               |
| `gemini`        | Gemini Google Search grounding, opt-in only                                                                               | a Gemini key          |

Only documented APIs or the `ddgs` library are used: no scraping of Google results pages, and `google` is refused as a ddgs engine. GitHub search is not a provider (it would need the user's own GitHub connection; it is not built). Wikidata is not used.

A provider without its key (or without SearXNG URLs) is skipped silently; an intent provider (`hn`, `arxiv`, `stackexchange`) is skipped unless the query has its signal, and is tried first when it has. A failing provider is retried once when the cause is transient (network, timeout, server error) unless it rotates engines itself (`ddgs`, `searxng`); a rate limit, a refused key or an empty answer goes straight to the next provider.

### Engine health and cooldown (`app/search/health.py`)

Every engine (each ddgs engine, each SearXNG instance, each other provider) has a health record. A blocked, rate-limited or timed-out engine is not asked again until its cooldown has passed: 2, 4, 8, 16, then 30 minutes at most, doubling with every consecutive failure; one success resets it. So one blocked engine never blocks the chain: the rotation asks the next engine (at most 3 per search). Requests to one engine are spaced by at least 400 ms plus jitter (`MAYA_SEARCH_ENGINE_SPACING_MS`), and at most `MAYA_SEARCH_CONCURRENCY` (default 2) provider calls run at once in the process.

When every engine that fits the query is cooling down, the search returns a plain result: "Search engines are temporarily limiting requests; retry in about N minutes", with an offer to answer from general knowledge (marked as not checked against the web). Nothing is invented.

### Burst control

The query is cleaned first (`app/search/query.py`): whitespace collapsed, the first sentence only, an instruction tail such as "Provide a day-wise breakdown including..." removed, at most 200 characters; the chat tool description also asks the model for a SHORT keyword query. Identical searches (same normalised query) running at the same time share one provider call, and successes are cached for 10 minutes (128 entries). The page reader rotates browser-compatible User-Agent strings that keep the `MAYA-AI` token and still respects `robots.txt`.

### Status

`GET /api/v1/search/status` (signed in; no keys, no queries) returns the provider order, whether Google grounding is on, and per engine `state` (`ok`, `cooling` or `blocked`), `cooling_until` (UTC) and the failure count.

The outcome records the provider and the real engine that answered and every provider that failed, with a plain cause (`rate_limited`, `network`, `timeout`, `server`, `auth`, `blocked`, `no_results`). The ddgs engines scrape their search pages, so they can be rate limited or break when the upstream page changes; the rotation and the other providers are for that.

## The chat tool

`web_search(query, topic?, depth?, recency?)` returns JSON: numbered `sources` (title, domain, source name, published date, age, `outside_window`), `passages` (snippet plus extracted page text, each inside the `<<<UNTRUSTED_EXTERNAL_INPUT>>>` envelope) and rules. The model writes the answer with `[n]` markers and a short `Sources:` list of markdown links. Page text is data, never instructions; a page that looks like it gives orders is flagged to the model.

- News: a query with "today", "latest", "news", "aaj", "taaza" and similar (or `topic: news`) uses the news endpoint with a day window. An empty window is widened to a week, then a month, then no limit, and the result says so. Every item shows date and source; items older than the window are labelled. Company names alone do not switch to news; the tool description tells the model to pass `topic: news`.
- Failure: if no provider returns anything, the model gets `status: unavailable` and a plain `user_reason` and must not give headlines.
- `depth: research` reads up to 5 pages and, when fewer than 3 could be read, refines the query once (the question plus the most repeated title words), so at most two rounds, bounded in time.
- Identical searches are cached for 10 minutes (successes only; 128 entries). `MAYA_SEARCH_RATE_LIMIT_PER_MINUTE` (default 20) limits searches per user per minute. The chat reply still allows 3 searches per reply.
- The live step shows "Searching the web" with a detail that names the real engine, such as "DuckDuckGo news, 8 results, 3 pages read" or "Bing web, 6 results" ("Google Search" appears only when the opt-in grounding really answered), or the failure reason.

## Page reading

`fetch_page(url, max_chars)` reads only public `http` and `https` pages on ports 80, 443, 8080 and 8443, without credentials in the URL. The host is resolved once; every address must be public (no loopback, private, link-local, metadata, carrier-grade NAT or reserved range, also IPv4-mapped IPv6). The connection goes to that checked address with the original `Host` and TLS name, so a second DNS answer cannot redirect it. Redirects are followed by hand (at most 3) with the same checks on every hop. `robots.txt` is respected (cached per host for an hour; a 5xx robots.txt means no crawling). Limits: 10 s per request, 2 MB, HTML or text only. At most 2 requests at once per host with a short pause. Main text, title and date are extracted with `lxml` (`app/search/extract.py`); no extraction library was added.

## Research steps

`ResearchAgent.grounded_answer` asks `NativeResearch` (search, read pages, one model call without Google Search writes the cited report from those pages; only URLs from the search can be cited) according to `MAYA_RESEARCH_BACKEND`: `native` (default) never calls Gemini grounding and, when native finds nothing, ends the step as `degraded` (honest, no sources); `auto` tries grounding only after native found nothing; `grounding` tries grounding first and falls back to native. The provider order in `MAYA_SEARCH_PROVIDERS` no longer switches native research off. A grounding 429 / RESOURCE_EXHAUSTED makes ONE call (no second alias), then starts a 30-minute cooldown shared by every caller of the process (`app/agents/grounding_cooldown.py`); during it grounding is not called at all and native answers immediately. The native research rate-limit bucket is per task, so two tasks of one workspace do not block each other. Voice has no web search tool today, so nothing changed there.

## Not verified here

No live call to any engine (DuckDuckGo, Bing, Brave, Yahoo, Mojeek, SearXNG, Wikipedia, Hacker News, arXiv, Stack Exchange, Tavily) was made; the tests use fakes and mocked HTTP. Whether a given engine is blocked from your network is only known by running it.

Deep research uses the same chain: its parallel queries drop from 3 to 1 while any engine is cooling, and its status line and result proof list the engines that answered.
