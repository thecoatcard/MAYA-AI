# app/search

Native web search for the chat `web_search` tool and research steps. Full description: [docs/architecture/WEB_SEARCH.md](../../../docs/architecture/WEB_SEARCH.md).

| Module        | Job                                                                              |
| :------------ | :------------------------------------------------------------------------------- |
| `names.py`    | provider names, engine labels, default chain (no gemini), setting checks         |
| `health.py`   | per-engine cooldown (2 to 30 min) and request spacing                            |
| `query.py`    | query clean-up (short keywords) and intent signals for hn, arxiv, stackexchange  |
| `providers_free.py` | SearXNG, Wikipedia, Hacker News, arXiv, Stack Exchange (no key)            |
| `models.py`   | `SearchHit`, provider protocols, failure causes                                  |
| `providers.py`| ddgs engine rotation, Brave, Tavily and the opt-in Gemini grounding              |
| `chain.py`    | order, cooldown skip, concurrency limit, in-flight dedupe, 10-min cache, status  |
| `fetch.py`    | `fetch_page`: SSRF checks, robots.txt, size and type limits                      |
| `extract.py`  | main text, title and date from HTML (`lxml`)                                     |
| `service.py`  | the tool result: news windows, page reading, research depth, untrusted envelope  |
| `report.py`   | cited report for research steps (native first, Gemini grounding as the fallback) |
| `factory.py`  | builds the stack from settings                                                   |

Tests are offline: `tests/unit/test_search_*.py` and `tests/integration/test_chat_native_search.py`.
