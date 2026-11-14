# MAYA prompt guide: what works now, what is planned, and how to test it

This guide is for a person, not a developer. Read it to understand what MAYA is, what you can ask it today, how to test each capability, and what is still missing.

Status of this document: updated on 2026-11-14 (section 23 lists every bug we hit and how to re-test it). It describes the code of that day. Nothing here is committed or released yet. Almost everything new was tested only with automated tests and fakes, not against the real Gemini, Notion, mail or a real microphone (see the legend). Section 17 is a ready-made test script that walks through nearly every capability.

## 1. What we are building

MAYA is a **voice-first digital employee**. You talk or type to it like you would to a capable colleague. It answers questions, remembers things about you, searches the web, reads your mail, reads and writes Notion pages, reads and edits your files, writes documents, decks, sheets and websites, reviews code, and tracks your background work out loud.

Three ideas hold it together:

1. **It works with your real files and tools, safely.** You connect a working folder. MAYA can read and write only inside that folder, never delete, and never replace an original without your approval.
2. **It tells the truth about its work.** If research fails it says so instead of making a fake deck. If a document is shorter than you asked, it says so. If a step of a task failed, the task does not say "Completed". Every file it builds is checked before it is called done.
3. **You stay in control.** Anything with a side effect (sending email, writing to Notion, replacing an original file, opening a pull request) waits for your click on an approval card in the chat. Saying "approved" in text or by voice never approves anything.

### How a request flows

```
You (voice or text)
   |
   v
Chat model decides: answer, search the web, read mail/Notion/GitHub, remember, ask you a
question, draft an agent, or start a TASK
   |
   v (for a task)
Planner -> steps -> specialist agents -> checks -> file card in chat
```

Specialist agents:

| Agent    | What it does                                                              |
| :------- | :------------------------------------------------------------------------ |
| Research | Searches the web (MAYA's own search, with sources) and cites them         |
| Builder  | Builds documents, decks, sheets, websites; edits files; writes code       |
| Operator | Email, daily briefing from calendar and Notion, GitHub and Notion actions |

You can add your own agents (section 9).

## 2. Status legend

| Label              | Meaning                                                                                                 |
| :----------------- | :------------------------------------------------------------------------------------------------------ |
| **Works (live)**   | Tried against the real Gemini model and worked                                                          |
| **Works (tested)** | Covered by automated tests with fakes or a real database. Not tried live after the latest changes       |
| **Needs setup**    | Works once you connect something (folder, mailbox, token) in Settings                                   |
| **Partial**        | Works but has a known gap, written next to it                                                           |
| **Expected**       | Built to do this, but not run live and not covered by a test for this exact prompt. Treat as unverified |
| **In progress**    | Being built right now. It may not exist yet in your copy                                                |
| **Off by default** | Built, but switched off until the owner turns it on (section 12)                                        |
| **Planned**        | Not built. No promise of a date                                                                         |

Every prompt below shows its label. If a prompt fails, paste the prompt and the "Technical details" of the failed step so the cause can be found.

**What nobody has seen yet.** Most new screens (work log, ask card, empty-chat animation, icon rail, master voice, new themes) were built and tested with automated tests, but they were not all looked at in a real window. If something looks wrong, say what you see.

## 3. Everyday chat

| Prompt                                                        | What you get                            | Status         |
| :------------------------------------------------------------ | :-------------------------------------- | :------------- |
| `Java me interface aur abstract class me kya difference hai?` | A direct explanation                    | Expected       |
| `Explain how a hash map works, with a small example`          | Explanation and example                 | Expected       |
| `Translate this paragraph to Hindi: ...`                      | Translation                             | Expected       |
| `Rewrite this email to sound more polite: ...`                | A rewritten text in the chat            | Expected       |
| `Quadratic formula aur Euler identity LaTeX me likho`         | Real typeset formulas (KaTeX)           | Works (tested) |
| `Mujhe 5 dollar ka price likhna hai: $5 plus $3`              | Plain prices, not turned into a formula | Works (tested) |
| `Create a ppt` (no topic)                                     | MAYA asks you what the topic is         | Works (tested) |

**Language preference.** Say it once: `Yaad rakhna: coding examples hamesha Java me do`. After that MAYA uses Java for code unless you ask for another language. Ask it with `Two sum ka code do` to check.

**The chat now shows its work.** For a longer answer you see, in order: a short line of what MAYA is about to do, grouped steps ("Searched the web (3)", "Read Notion (2)", "Ran 4 tools"), a **Thinking** dropdown with the model's own reasoning summary (open while it thinks, folded after), and a footer with real time, steps and speed. Nothing there is invented. Status: Works (tested), not looked at in a real window.

## 4. MAYA asks you back (choice cards)

When MAYA truly needs a choice or a short answer, it shows a card in the chat instead of guessing: numbered options, a free-text box, Submit and Skip. Keys: `1` to `9` pick an option, Up/Down move, Enter submits, Esc skips. The card says "Not an approval": answering it never sends, writes or approves anything.

| Prompt                                                  | What you get                                        | Status         |
| :------------------------------------------------------ | :-------------------------------------------------- | :------------- |
| `Ek PDF banao` (no topic)                               | A card asking the topic, maybe page count           | Expected       |
| `Mere liye notes banao` (no subject, no language saved) | A card asking subject and code language             | Expected       |
| Type a normal message instead of using the card         | The card is dismissed and MAYA answers your message | Works (tested) |
| Reload the page while a card waits                      | The card is still there                             | Works (tested) |

**Required details are checked by code, not by the model.** For notes, a document, a deck, a sheet, research or a save to Notion, MAYA asks ONE card (topic, depth for notes when you gave no length, Notion page, code language for programming notes) and starts nothing until you answer. The topic must come from your words, the open chat or an attached file; saved memory and preferences never supply it (they may only pick the code language or tone). `isi par 10 page ka PDF banao` after a topic, or `Java streams ke 10 page notes banao`, asks nothing. After a task starts, the first line is written by code ("Started: ... It has N steps") and MAYA cannot claim it is done until it is. **A running task can ask too.** If the book is finished but has no Notion parent, the task is not failed: the card appears in the chat with the pages MAYA can really see (best guess marked Suggested, or paste a link) and, only if you pick "Yes, use it by default", the choice is saved as your default Notion page (the start text then says so). Your answer resumes only the Notion step; the finished book is kept and the approval still shows the exact page. **"Send it to the same mail"** shows a card "Send to <address>?" with the address found earlier in the chat before any email is prepared. Notes and long content for Notion ("notes", "detailed", "100 pages") always go through the book job, never a few direct bullets; a stated size is scaled (about 450 words a page, up to 200 pages, estimate shown) and never refused as too large. Not built: a hub page with one Notion sub-page per chapter for very large books (one page is written in batches).

MAYA is told to prefer a sensible default and to ask only when a wrong guess is costly. If it asks too often, tell it. When a background task needs a detail, the same card appears on the task card. Live voice can ask a question aloud, but the card appears on screen only after a refresh (known gap).

## 5. Memory and preferences

| Prompt                                                                      | What you get                             | Status         |
| :-------------------------------------------------------------------------- | :--------------------------------------- | :------------- |
| `Yaad rakhna: mera favourite colour teal hai aur main Pune mein rehta hoon` | Saved. Shows on the Memory page          | Works (live)   |
| (New chat) `Mera favourite colour kya hai?`                                 | Answers from memory                      | Works (live)   |
| `Yaad rakhna ki main Java me code dekhna pasand karta hoon`                 | Saved as a coding preference, used later | Works (tested) |
| `Bhool jao ki main Pune mein rehta hoon`                                    | Forgets that item                        | Works (tested) |
| `What do you remember about me?`                                            | A list of what it knows                  | Expected       |
| `Yaad rakhna: mera password abc123`                                         | Refused: secrets are never stored        | Works (tested) |

A memory prompt never creates a document. Secrets such as tokens and keys are scrubbed and never stored.

## 6. Web search (no Gemini search tool needed)

MAYA has its own search stack. It returns a few sources with outlet and age and treats page text as data, never as instructions.

| Prompt                                                                       | What you get                                                        | Status         |
| :--------------------------------------------------------------------------- | :------------------------------------------------------------------ | :------------- |
| `Aaj ki latest AI news web search karke 3 points me batao`                   | Short answer with named sources and a "Searched the web" step       | Works (tested) |
| `Compare Postgres and MySQL for a small SaaS, with sources`                  | A cited comparison                                                  | Works (tested) |
| `Research the latest Java features with sources`                             | A cited summary                                                     | Works (tested) |
| A question about a current fact (price, score, version) with no search words | MAYA searches instead of guessing, or marks the answer "unverified" | Expected       |

**Research steps in background tasks** use the same native stack (search, read the top pages, a cited answer) by default (`MAYA_RESEARCH_BACKEND=native`); Gemini Google Search grounding is only an optional extra (`auto` or `grounding`) and its quota errors pause it for 30 minutes without stopping your task. Two tasks started one after the other run independently; if every worker is busy the second shows `Queued: N tasks ahead` and starts when one is free. The "answer comes from my general knowledge" note is only ever added to an ordinary model answer, never to a "Started: ..." line, a question card or a task result.

**Which engine searches:** MAYA uses free engines that need no key (DuckDuckGo, Bing, Brave, Yahoo, Mojeek, optionally your SearXNG, Wikipedia, and for a clear signal Hacker News, arXiv or Stack Overflow). The step names the real engine, for example "Bing web, 6 results, 2 pages read". Google Search is not used unless you turn it on (`MAYA_SEARCH_PROVIDERS` with `gemini`). If an engine blocks MAYA, it rests 2 to 30 minutes and the next one is used; if all rest, MAYA says "Search engines are temporarily limiting requests; retry in about N minutes" and offers to answer from general knowledge. Short prompts work best (`LeetCode daily problem this week`): MAYA turns your question into a few keywords. The owner can see engine state at `GET /api/v1/search/status`. Not run live yet.

**When research fails**, MAYA does not build a fake file. You get a plain message and the choice to retry or to say `from general knowledge` (`bina research ke`, `apni knowledge se`); then the file is built and stamped "Not source-verified". Live runs depend on your internet (DNS) reaching the search sites.

### Deep research (brain toggle)

Turn on the brain icon in the composer, then type the request. MAYA starts a background task and says so in one line written by code ("Started: deep research on ... I will read at least 20 different sites ..."); it shares no findings until the task finishes. It plans 6 to 10 questions, searches widely, skips paywalled, login and bot-blocked pages (and tells you), reads up to 40 pages (say `50 sites` for more, 60 at most), then does what you asked. The result ends with the honest status `Read N sources from M sites (K skipped)`, an evidence table, contradictions, a confidence and limits section and the numbered sources it actually read. Sentences without a source are marked as inference. It stops after 10 minutes or about 60 model calls, and Cancel stops it within seconds.

| Prompt (brain toggle on)                                      | What you get                                                  | Status                   |
| :------------------------------------------------------------ | :------------------------------------------------------------ | :----------------------- |
| `Research solar battery storage costs in India`               | A cited answer with the honest status line and a source list  | Built, tested with fakes |
| `Deep research on EV charging, read 50 sites, and make a PDF` | The research, then a PDF through the normal document pipeline | Built, tested with fakes |
| `Deep research` (no topic)                                    | MAYA asks what to research and starts nothing                 | Built, tested            |
| Any of the above with `MAYA_DEEP_RESEARCH_ENABLED=false`      | "Deep research did not start: ..." and no task                | Built, tested            |

**No toggle needed.** Saying `deep research` in the message starts the same engine, typed or by voice (`do deep research on ...`, `deeply research ...`, `deep research karo`, `gehri research karo`). By voice the model uses the same path; without a topic MAYA asks one short question first. The answer comes in the chat; a file or Notion page is made only if you named one.

If the deep-research engine cannot run, the task says so plainly ("Deep research is not available right now: nothing was searched"); it never turns into a one-source search labelled deep research.

Honest status: never run against live search, real sites or Gemini yet. How many real sites block MAYA, and what a run costs, are unknown until you try it. Queries and page requests leave your machine (ADR-0032).

## 7. Documents: PDF, Word, PowerPoint, Excel, and plain files

MAYA chooses the file type from what you ask: pdf, docx, pptx, xlsx, md, txt, html, csv, json and common code files.

**No file unless you name one.** MAYA never makes a PDF, deck, sheet, Word file or Notion page by herself. Notes, a report, a summary, research or an essay with no format word is answered in the chat (and may end with one line: `Want this as a PDF, Word, slides or a Notion page?`). A file starts only when your own words name it: `pdf`, `ppt`/`slides`/`deck`, `word`/`docx`, `excel`/`sheet`/`csv`, `markdown`/`txt`, `website`, `zip`, `file`, `document banao`, `Notion me save karo`, `email it`/`mail karo`, `download`, or a follow-up such as `isko pdf bana do` / `make it a pdf`. A mail still needs your approval click. This holds in typed chat, the planner, the long-document and learning-book routes, deep research and voice (the voice model cannot pick a format you did not say). If research finds no sources and you asked for no file, MAYA says `I could not fetch any sources, so I have no findings to report. Retry, or say: answer from general knowledge.`; with a file requested it keeps `build from general knowledge`. Code: `backend/app/services/deliverable_intent.py`.

| Prompt                                        | What you get                               | Status                   |
| :-------------------------------------------- | :----------------------------------------- | :----------------------- |
| `notes banao on Java streams`                 | Notes in the chat, plus the one offer line | Built, tested with fakes |
| `Java streams notes PDF me banao`             | A PDF                                      | Built, tested with fakes |
| `Deep research about the future of AI agents` | Deep research, answered in the chat        | Built, tested with fakes |
| `Notion me save karo`                         | A Notion page (after the detail questions) | Built, tested with fakes |

### Long notes and books (PDF, Word)

Long documents are now built as a **plan first, then one chapter at a time** (three chapters at once). One weak chapter no longer breaks the whole file. Up to 5 pages is a single quick call; 6 pages and more run as a background task with visible progress ("chapter 4 of 10").

| Prompt                                                                              | What you get                                                    | Status         |
| :---------------------------------------------------------------------------------- | :-------------------------------------------------------------- | :------------- |
| `Java streams par 10 page ke notes PDF me banao, code examples aur tables ke saath` | A dense 10 page PDF with code blocks and tables, no empty space | Works (tested) |
| `12 pages ke Java notes banao, English mein`                                        | About 12 pages, English only, no cover                          | Works (tested) |
| `Is PDF me ek aur chapter "Collectors" add karo, design wahi rakhna`                | The earlier document continued, same design                     | Works (tested) |
| `Python lists ki 2 page ki dense exam cheat sheet banao`                            | A compact cheat sheet                                           | Works (tested) |
| `NDA ka legal memo PDF banao, sober aur formal`                                     | A formal memo with a serious design                             | Works (tested) |
| `A 40-page handbook on onboarding new engineers`                                    | A book layout: cover, contents, chapters on new pages           | Works (tested) |
| `Dark blue minimal theme mein SQL joins ka 6 page ka guide`                         | Dark blue headings or page, readable text                       | Partial        |

**Counts never reject a file.** If you ask for `10 slides`, `5 pages` or `20 rows` and the first draft has more or fewer, MAYA fits it to your exact number (merges or trims slides, splits or adds a labelled `Appendix: Notes` slide, tightens or loosens the layout, adds a short Notes page, cuts or generates rows). If the exact number is truly out of reach you still get the file, and MAYA says plainly `You asked for 10 pages; the file has 9 because ...`; it is never shown as failed. `8-10 pages` accepts anything from 8 to 10, and `about 10 slides` accepts 9 to 11 (plus or minus 15%). An empty or broken file is still rejected.

Rules: only the page count **you** state is enforced; the model's own guess is a soft hint. A chapter that cannot be written is marked "Not included" honestly. Words like "notes", "cheat sheet" give a compact layout; "book", "handbook", "guide" give cover, contents and a page per chapter. Colours you state (for example `#0A7D5C`) always win, and text keeps at least 4.5:1 contrast.

**Known gaps:** nested bullets are flattened with a dash and horizontal rules are dropped in PDFs. Real Gemini following the chapter word budgets is not verified.

### Decks (PowerPoint)

There are now **25 extra themes**: 12 "glass" (dark gradient cards, 11 dark and 1 light) and 13 "editorial" (clean paper-like). New layouts: hero cover, section break, stat grid, icon cards, process, image + text and chart. Decks can have native rounded cards, native charts, 47 built-in icons and slide transitions (fade, push, wipe, cover).

| Prompt                                                                                                                  | What you get                                | Status         |
| :---------------------------------------------------------------------------------------------------------------------- | :------------------------------------------ | :------------- |
| `Startup pitch ka 8 slide ka deck banao, glass-aurora theme, fade transition, ek stat grid aur ek chart slide ke saath` | Dark glass deck with those slides and fades | Works (tested) |
| `Python basics ka deck banao, editorial-paper theme`                                                                    | Clean editorial deck                        | Works (tested) |
| `Is deck ka theme glass-frost kar do, content mat badlo`                                                                | Same content, new theme                     | Works (tested) |
| `Bachchon ke liye animals ka 5 slide ka bright, rounded PPT banao, from general knowledge`                              | A colourful, playful deck (kids theme)      | Works (tested) |
| `Startup pitch deck, 8 slides, bold modern, brand colour #0A7D5C`                                                       | A deck that uses your exact colour          | Works (tested) |

Without a theme name MAYA picks one by topic (pitch and tech get glass, reports, finance, notes, kids and health get editorial). Editing a deck keeps its design. **Not verified:** how it looks in real PowerPoint or Keynote (checked only in a browser mirror), and the transitions were checked against the file format, not played.

### Spreadsheets and plain files

| Prompt                                                                | What you get                                 | Status         |
| :-------------------------------------------------------------------- | :------------------------------------------- | :------------- |
| `Ek sales.csv banao jisme 5 rows ke sample data ho`                   | A `.csv` file                                | Works (tested) |
| `500 products ka sheet banao: name, category, price, stock`           | A sheet with all 500 rows (never 5)          | Works (tested) |
| `Make an Excel budget sheet with monthly totals`                      | An `.xlsx` file with formulas for the totals | Works (tested) |
| (Attach a sheet) `Is sheet ka summary do`                             | A summary of columns and values              | Works (tested) |
| (Attach a sheet) `Price column me 10% badhao aur total dobara nikalo` | An edited copy                               | Works (tested) |
| `Write a README.md for a todo app`                                    | A `.md` file                                 | Expected       |
| `Ek JSON config file banao for a blog`                                | A valid `.json`                              | Expected       |

**Code already shown in the chat as a file:** after MAYA shows a script, `give in a file`, `give main.py file`, `isko main.py me save kar do` or `give it as a .js file` saves that exact code (no rewrite) as a file named from the chat: a name in your message wins, then one you wrote earlier (`main.py`), then the code's language (`.py`, `.js`, `<Class>.java`). Code never becomes `.txt`; the reply names the file and its line count. Shell, batch and other executable types are not created this way (Works, tested).

Files are checked after they are built: size, valid format, slide count, no text overflow. **Tools made at run time** (MAYA writes a small helper when none fits) exist but are **Off by default** (section 12).

## 8. Websites and projects

| Prompt                                                       | What you get                                                                                 | Status         |
| :----------------------------------------------------------- | :------------------------------------------------------------------------------------------- | :------------- |
| `Ek vanilla HTML CSS JS ka to-do web page banao` (no folder) | One `.zip` that opens to one folder with `index.html`, `style.css`, `script.js`, `README.md` | Works (tested) |
| Same prompt, with a connected folder                         | The files written one by one into `<your folder>/<project>/`                                 | Works (tested) |
| `Ek single-file landing page banao, sirf HTML mein`          | One `.html` file                                                                             | Works (tested) |
| `Build a small Python CLI that counts words in a file`       | A project with the right file extensions                                                     | Partial        |

Safety: a project never contains runnable program types (exe, bat, ps1, sh and similar), and the zip has no path tricks and has size limits.

## 9. Your folder and your files

Connect a folder in Settings > Files. Then you can ask for edits on files already in it, or attach files in the chat.

| Prompt                                                       | What you get                                                                                   | Status         |
| :----------------------------------------------------------- | :--------------------------------------------------------------------------------------------- | :------------- |
| `report.docx mein saare headings capitalize kar do`          | Updated in place; diff shown; previous version kept in `.maya-backups`                         | Works (tested) |
| `report.docx me sirf content update karo, styling mat badlo` | Content changes in place, fonts and colours stay; previous version kept in `.maya-backups`     | Works (tested) |
| `sales.csv mein ek naya column "total" jodo`                 | `sales.csv` updated in place with the new column; previous version kept in `.maya-backups`     | Works (tested) |
| `report edit karo` when two files match                      | MAYA asks: "Which file do you mean?"                                                           | Works (tested) |
| `report.docx ko hi badal do`                                 | A diff and an approval card. If you approve, a backup is kept in `.maya-backups`               | Works (tested) |
| Attach a PDF: `Isko edit karo`                               | "PDFs cannot be edited" and an offer to build a new PDF from the text                          | Works (tested) |
| `Is folder ki files ki list dikhao`                          | A listing                                                                                      | Expected       |

MAYA never leaves the folder, never follows a link out of it, never deletes, and never overwrites an original without approval.

### Your own agents

| Prompt                                                              | What you get                                                                  | Status         |
| :------------------------------------------------------------------ | :---------------------------------------------------------------------------- | :------------- |
| `Mere liye ek agent banao jo meri Python files ka code review kare` | A draft of the agent file in chat. Nothing is written until you press Approve | Works (live)   |
| `@python-reviewer mera main.py review karo`                         | Runs your agent on main.py (attached, made in this chat, or in your folder)   | Works (tested) |
| `Disable the python-reviewer agent`                                 | The agent is switched off (not deleted)                                       | Works (tested) |

Custom agents can only search the web and read files. Their instructions are data and cannot override MAYA's safety rules.

`@name` always means your own agent and is handled before anything else (never GitHub). The file you name is looked for in this order: a file you attached, a file made earlier in this chat, then your connected working folder (read only). If several or none match, MAYA asks one "Which file?" question with real files. If the agent does not exist, MAYA says so plainly (`There is no agent named python-reviewer in your working folder (.maya/agents)`), lists the agents it can see and how to create one; nothing is started. A bare file name like `review main.py` never lists your GitHub repositories; GitHub is used only when you give a GitHub link, `owner/repo`, or say GitHub/repo.

## 10. Connections: mail, Notion, GitHub

Add your own accounts in **Settings > Connections**. Each user uses only their own. The server's own tokens (set in its private env file) work only for the owner named in `MAYA_ENV_CONNECTIONS_USERS`; everyone else sees "Not connected" until they add their own. Secrets are stored encrypted and never shown again. The server needs `MAYA_CONNECTIONS_ENCRYPTION_KEY` once (see `docs/security/CONNECTIONS.md`).

### Mail (your own mailbox)

Enter your email address and an **app password** (not your normal password) in the Mailbox card, then press Test. MAYA uses only this mailbox. The Google access token from the server settings is **never** used for mail.

| Prompt                                         | What you get                                                                    | Status                                 |
| :--------------------------------------------- | :------------------------------------------------------------------------------ | :------------------------------------- |
| `Meri mailbox me last 5 mails dikhao`          | Sender, subject, date, with urgency and "needs reply" labels                    | Needs setup, Works (tested) with fakes |
| `Rahul ke mails dhoondo jisme invoice ho`      | Search results                                                                  | Needs setup, Works (tested)            |
| `Is mail ko padho aur batao kya reply chahiye` | A summary of one mail                                                           | Needs setup, Works (tested)            |
| `anand@example.com ko hello mail bhejo`        | A draft and an **approval card** with the exact mail. Sent only after you click | Needs setup, Works (tested)            |

If no mailbox is saved MAYA says so in plain words and sends nothing. Text inside mails is treated as data. Mail MAYA sends is sent **from your own mailbox as you**.

### Notion (read and write)

Create an integration at notion.so/my-integrations, share your pages or database with it, paste the token in Settings > Connections, press **Test connection**.

| Prompt                                                        | What you get                                                                | Status                                 |
| :------------------------------------------------------------ | :-------------------------------------------------------------------------- | :------------------------------------- |
| `Mere Notion tasks dikhao jo is hafte due hain`               | A task list                                                                 | Needs setup, Works (tested) with fakes |
| `Notion me "Test" naam ka page banao`                         | An approval card with the exact page, then the page, then a read-back check | Needs setup, Works (tested) with fakes |
| `Is Notion page me ek naya section jodo: ...`                 | Approval card, then the blocks are appended                                 | Needs setup, Works (tested)            |
| `Is page ko archive karo`                                     | Approval card, always manual                                                | Needs setup, Works (tested)            |
| `Notion database me ek nayi row add karo: task X, due Friday` | Approval card, then the row                                                 | Needs setup, Works (tested)            |

**Save existing notes as they are.** `add this notes in notion as it is`, `Notion me save kardo` (after notes or a PDF were made), `isko Notion me daal do jaisa hai waisa`, `save the pdf to Notion`: MAYA copies what already exists; it never writes anything new. It asks only the Notion page (a card with your real pages, best guess marked Suggested; a link works), or `Which notes?` if the chat has nothing to save, and asks which one only when two sources are plausible (your last long answer or a file). The approval card shows the exact text, title (first heading or file name), page, word and block counts and `As it is`; without "as it is" only blank-line spacing is cleaned and no facts are added. After you approve, MAYA reports the page link and the read-back check (VERIFIED) or what failed. A PDF is read back as text, so its layout is approximate. By voice: `add these notes to Notion as they are` asks the page aloud and prepares the same card. Status: Works (tested with fakes), not tried with live Gemini or Notion.

Markdown renders properly in Notion (headings, code with language, tables, callouts, toggles, equations, lists). **Auto-approve** is in Settings > Connections > Notion, **off by default**: a master switch plus separate switches for create page, add row, append blocks and update row. Archive, delete, replace section, text edits and comments are **always manual**, and there are hourly and daily caps. After an auto-approved write the task card shows "Auto-approved by your setting" with the exact content and a link to turn it off.

**Complete study notes in Notion** (a long "master learning book": a story-style teacher voice, 8 to 12 chapters, brute-better-optimized coding sections, quiz and revision) is **In progress**. Today a Notion page written in a single chat reply can be short.

### GitHub

| Prompt                                      | What you get                                         | Status                                                              |
| :------------------------------------------ | :--------------------------------------------------- | :------------------------------------------------------------------ |
| `Review https://github.com/owner/repo`      | A structured review, listing files read and not read | Needs setup (public repos need no token). Works (tested) with fakes |
| `Mere repos ki list dikhao`                 | Your repositories                                    | Needs setup, Works (tested)                                         |
| `Is repo ke last 10 commits dikhao`         | Commit list                                          | Works (tested)                                                      |
| `Open PRs aur issues dikhao for owner/repo` | Lists                                                | Works (tested)                                                      |
| `Is branch se ek pull request kholo`        | An approval card, then a PR                          | Needs setup, little testing                                         |

### Calendar and briefings

| Prompt                                    | What you get                                    | Status                                                                   |
| :---------------------------------------- | :---------------------------------------------- | :----------------------------------------------------------------------- |
| `Mera aaj ka daily briefing banao`        | Notion tasks and, if connected, calendar events | Needs setup, Works (tested) with fakes                                   |
| `Mere kal ke meetings dikhao`             | Calendar events (read only)                     | Needs setup (the Google token is set by the server admin, calendar only) |
| `Kal 4 baje Rahul ke saath meeting rakho` | Creates a calendar event after approval         | Planned                                                                  |

## 11. Approvals: always a click

Every side effect shows an **approval card in the chat** with the exact mail, page, file or PR. There is no separate Approvals page any more.

| What to try                                    | Expected                                                             | Status                                                                     |
| :--------------------------------------------- | :------------------------------------------------------------------- | :------------------------------------------------------------------------- |
| Ask MAYA to send a mail, then type `approved`  | Nothing happens. Only the button approves                            | Works (tested)                                                             |
| Click **Approve** on the card                  | The task continues and finishes                                      | Needs setup: the database migration `0016` must be applied, see section 16 |
| Click **Reject**                               | The task stops and says so                                           | Works (tested)                                                             |
| Wait over 5 minutes before clicking            | The approval has expired; a fresh card is made                       | Works (tested)                                                             |
| A card MAYA flags as not matching your request | A visible warning on the card (it never blocks, never auto-approves) | Works (tested)                                                             |

By voice MAYA only **proposes** and tells you a card is waiting; it cannot approve.

## 12. Tasks, background work and cleanup

| Prompt or action                                             | What you get                                                                                                                                                                                                                                                                                                                                       | Status                                             |
| :----------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------- |
| Start a long task, open the **Tasks** page                   | Live steps, and an inline card for any approval or question                                                                                                                                                                                                                                                                                        | Works (tested)                                     |
| A task where one step fails                                  | "Finished with problems", never a clean "Completed"                                                                                                                                                                                                                                                                                                | Works (tested)                                     |
| A step finished but the automatic review doubts it           | A small "Note: ..." with the result; the task stays "Completed" (default `MAYA_STEP_REVIEW_MODE=advisory`). Only a placeholder, error marker or failed check, confirmed by the review in `enforce` mode, marks it "Completed with errors"                                                                                                          |
| Any task ends (done, partial, failed, cancelled)             | MAYA posts ONE message in the same chat with what the task actually got: findings and sources actually read, the file made, the Notion page, or the plain reason it failed and the next step (Retry or "answer from general knowledge"). It appears live, no reload. The Tasks page shows the same text as **Result** above the technical details. | Works (tested offline), not tried with live Gemini |
| Delete a conversation (trash icon)                           | A clear dialog in the middle of the window saying what is removed and what stays, with a checkbox to also delete its files and tasks                                                                                                                                                                                                               | Works (tested), not looked at in a real window     |
| Delete tasks or artifacts from the Tasks and Artifacts pages | Selected items removed after a confirm dialog                                                                                                                                                                                                                                                                                                      | Works (tested)                                     |

The result message is built only from the task's real outputs (a lite model call with strict "use only these outputs" rules; if the model is unavailable or the budget is out, a plain template lists the raw findings). A partial or failed task is never described as done.

Running tasks are never deleted by a conversation delete. Files saved in your own folder and the audit log are kept.

### Things that are built but off until the owner enables them

| Feature                                         | How it is switched on (owner)                                                                                                                                                 |
| :---------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **MCP tools** (Playwright browser, GitHub MCP)  | `MAYA_MCP_CLIENT_ENABLED=true` plus a servers file with an exact pinned version. Reads are free; every click, write or unknown tool needs an approval card. Chat only for now |
| **Run-time tools** (MAYA writes a small helper) | The runtime-tools settings in `docs/guides/CONFIGURATION.md`; runs in a container                                                                                             |
| **Startup greeting off**                        | `MAYA_STARTUP_GREETING=0`                                                                                                                                                     |

## 12a. Scheduled reports (built; Expected, never run live)

MAYA can run a report or task every day, on weekdays, weekly or on chosen days at a fixed time. She asks what she needs with question cards, then shows ONE preview card; nothing is scheduled until you click Create (typing or saying "yes" does not). Mail goes out through MAYA's own mail service (needs `MAYA_SMTP_*`), only to your account email or an address you verified once with a six-digit code. A report-only schedule is spoken when you next open MAYA; if you named a file format, MAYA says the full report is in Artifacts. Manage schedules in Settings > Schedules.

| Prompt                                                     | What should happen                                                                                                                                                                                | Status                               |
| :--------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :----------------------------------- |
| `Every day at 8 AM send me the big tech news by mail`      | Question cards for anything missing (time zone, recipient), then a preview card such as "Every day at 08:00 (Asia/Kolkata) · tech news · mail to you · chat only · ~10 sources/run". Click Create | Expected                             |
| `Schedule a weekly summary on Mondays as a PDF`            | Preview card with "PDF file in Artifacts"; a file only because you said PDF                                                                                                                       | Expected                             |
| `Pause my tech news schedule` / `Resume it` / `Run it now` | The schedule is paused, resumed or run once                                                                                                                                                       | Expected                             |
| `List my schedules`                                        | Names, times and last result                                                                                                                                                                      | Expected                             |
| Say "yes, schedule it" instead of clicking Create          | MAYA tells you to click Create; nothing is created                                                                                                                                                | Expected                             |
| Close MAYA past 8:00, open it later                        | One catch-up run (older than 48 hours: skipped), then master voice tells the update (muted start; no microphone opened by the backend)                                                            | Expected                             |
| Send to `friend@example.org`                               | A code mail goes to that address; the schedule's mail is withheld until you enter it in Settings > Schedules                                                                                      | Expected                             |
| A step needs approval during a run                         | The run ends "needs attention"; nothing is approved for you                                                                                                                                       | Expected                             |
| Settings > Schedules > "Wake my PC to run schedules"       | Experimental: one Windows task "MAYA AI Scheduler" after a confirm dialog; needs the PC on, you logged in and Docker running                                                                      | In progress (never run on a real PC) |

Chat and voice conversation (the owner's failing case):

| Prompt (chat or voice)                                                                  | What should happen                                                                                                                                                                                                              | Status                                                     |
| :-------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :--------------------------------------------------------- |
| `Can you add a scheduler?`                                                              | MAYA says yes and asks what to schedule, the time, the days, where to send it (mail to your account email or another address) and the format. Never "I cannot schedule". Voice asks one short question at a time                | Expected (offline tests pass; no live Gemini or voice run) |
| `Schedule a daily 8:00 a.m. report sent to me as a mail: big news in the tech industry` | Asks only what is missing (time zone, which address), never guesses; then ONE preview card (chat: `schedule.draft`; voice: `SCHEDULE_DRAFT` frame plus a spoken summary). A typed or spoken "yes" creates nothing; click Create | Expected (offline tests pass; no live Gemini or voice run) |
| Same request on a server without `MAYA_SMTP_*`                                          | MAYA says plainly that mail is not set up and offers a chat-only schedule                                                                                                                                                       | Expected (offline tests pass)                              |
| Kill switch `MAYA_SCHEDULES_ENABLED=false`                                              | The scheduler tools and rules are not offered in chat or voice; Settings > Schedules still lists existing items                                                                                                                 | Expected (offline tests pass)                              |

Honest status: the chat harness (`tests/integration/test_chat_schedules.py`) and the voice bridge with a scripted fake Live model (`tests/unit/test_voice_schedules.py`) pass offline. No live Gemini, SMTP, Windows Task Scheduler or real voice run yet (ADR-0033).

## 13. Voice

Click **Voice** in the top bar. When a task ends while voice is connected, MAYA speaks what it got (2 to 5 short sentences: what was found or made, the top points, what failed, "the full details are in the chat"; no web addresses read aloud). It is the same summary stored with the chat message, so chat and voice agree. If voice is off, the update waits for the next connect as before. There are two kinds of voice:

|                               | Top-bar voice ("master")                                                     | Composer microphone (one conversation) |
| :---------------------------- | :--------------------------------------------------------------------------- | :------------------------------------- |
| Starts from                   | The top bar                                                                  | The mic in a conversation              |
| Tracks                        | **All** your tasks in all conversations, by itself, without any chat message | That conversation                      |
| Go to Tasks and come back     | Keeps running                                                                | Keeps running                          |
| Open a different conversation | Keeps running                                                                | Stops, with a short notice             |
| If both are asked             | Master wins; the composer mic reads "Voice is on (all conversations)"        |                                        |

**One toggle, no voice panel.** The old voice card above the message box (waveform, Listening, Mute, Stop voice) is gone. The **waveform button in the message box** is the single voice toggle: click to start, click again to stop. It animates while MAYA listens or speaks, shows a mic-off icon when muted and an alert icon on an error (every state is also written for screen readers and in the tooltip). The top-bar chip shows the same state and stops the same session.

**Mic mute.** While voice is on, a small mic button appears next to the waveform button (tooltip "Mute microphone" / "Unmute microphone"); it disappears when voice is off. Muting does **not** end the session: MAYA keeps speaking progress updates, you just are not heard. The master choice is remembered on this device.

**Safety wording moved.** "Voice can read this back or deny it, but only you can approve it here" now appears on the approval card while voice is on, and once as a notice when voice connects. Only the Approve and Reject buttons approve.

What MAYA says in master mode: what exactly finished and what is left, what needs your input (and in which conversation), and failures. It batches updates (about 10 seconds apart) and stays quiet when nothing changed. It never approves anything.

| Prompt (spoken)                                                          | What you get                                                                  | Status                |
| :----------------------------------------------------------------------- | :---------------------------------------------------------------------------- | :-------------------- |
| `Aaj Delhi ka mausam kya hai?`                                           | A short answer after a web search, naming the outlet and age                  | Expected              |
| `Kal ki cricket score batao`                                             | An answer, or "search failed". It must not invent a score                     | Expected              |
| `Java streams par 10 page ke notes banao` then talk about something else | A task starts; MAYA tells you when chapters finish and when the file is ready | Expected              |
| `Mere mails dikhao`                                                      | A short spoken summary from your mailbox                                      | Needs setup, Expected |
| `Notion me ek page banane ka card dikhao`                                | A card appears; MAYA says it is waiting for your click                        | Expected              |

Status of the new toggle: Works (tested with mocks), not looked at in a real window. Honest limits: nothing here was run with a real microphone and the real Gemini Live model. The 1 hour maximum session length and reconnecting after it are not verified. Docker, mail and Notion connection problems are **not** announced by voice (no feed for them exists). A question card asked by voice shows on screen only after a refresh.

## 14. The desktop app and the screen

| What to check                                      | Expected                                                                                                                                                                                                                                                                                                    | Status                                                                                                                  |
| :------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| Start the desktop app                              | An animated "Starting MAYA" screen with drifting colour fields and a tick on each finished step, then a spoken "Hi, I'm MAYA" (offline PC voice)                                                                                                                                                            | Expected                                                                                                                |
| Hamburger button or `Ctrl+B` / `Cmd+B`             | The sidebar folds into a slim **icon strip** (New conversation, Tasks, Artifacts, Memory, Connections, Activity, Settings). Click again to unfold. The choice is remembered                                                                                                                                 | Works (tested), not looked at in a real window                                                                          |
| Switch to the light theme                          | The window controls strip (minimise, maximise, close) turns light too                                                                                                                                                                                                                                       | Expected                                                                                                                |
| Open an empty conversation                         | A calm MAYA mark with slow drifting dots and 4 suggestion chips. They only fill the draft, never send. It disappears when you type                                                                                                                                                                          | Works (tested), dark theme only looked at                                                                               |
| `prefers-reduced-motion` on in your system         | Static screens, no moving dots                                                                                                                                                                                                                                                                              | Works (tested)                                                                                                          |
| Brain icon in the message box (before Run as task) | **Deep research** toggle: tooltip "Deep research", a small "Deep research" chip you can dismiss and the hint "Reads 20+ sites, takes a few minutes" while on. It applies to one message, then switches off. Disabled while a reply streams or voice is live. Your message shows a small Deep research badge | Toggle and request (`mode: "deep_research"`) work (tested); the research run itself is the backend's, not verified here |

Mail from MAYA itself (the verification code, the password reset code and the SMTP check mail) is now a styled HTML mail in the app theme (ultramarine accent, light and dark), with the same plain text for clients that do not show HTML. Status: Works (tested). It was looked at in a browser only, not in Gmail, Outlook or Apple Mail, and not at phone width. Mails MAYA sends for you from your own mailbox are not restyled.

### Right-click menus

Right-click (or the Menu key, Shift+F10, long press, or the "..." button) on these opens a menu. Status is honest: what is built in the web client and what depends on the server.

| Where                              | Actions                                                                                                                                                                                                     | Status                                                                                                                |
| :--------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------- |
| Conversation (sidebar)             | Open, Rename, Pin, Archive, Duplicate, Export as Markdown, Copy title, Delete                                                                                                                               | Built. Rename, Pin, Archive, Duplicate, Export need the chat API endpoints; hidden when the server answers 404 or 501 |
| Sidebar blank area, icon rail      | New conversation, Show archived, Sort, Collapse sidebar                                                                                                                                                     | Built                                                                                                                 |
| Reply                              | Copy, Copy as Markdown, Speak, Make a file (PDF, Word, Slides, Sheet), Save to Notion as it is, Remember this, Send by email, Good or Bad answer, Duplicate from here, Copy message ID                      | Built. File, Notion, Remember and Email send a chat message with `source_message_id` (approval cards work as usual)   |
| Your message                       | Copy, Edit and resend, Quote in reply                                                                                                                                                                       | Built                                                                                                                 |
| Selected text in a message         | Copy, Quote in reply, Explain, Translate (Hindi, English), Summarize, Search the web, Remember, Speak                                                                                                       | Built. Each sends a short chat message with the text (at most 2000 characters)                                        |
| Code block, links, [n] citations   | Copy code, Save as file, Wrap lines; Open, Copy link, Open source                                                                                                                                           | Built                                                                                                                 |
| Task card and Tasks list row       | Copy details, Copy error, Show technical details, Copy result, Copy task ID, Cancel, Try again, Open, Open in Artifacts, Show in conversation, Delete; with several selected: Retry failed, Delete selected | Built                                                                                                                 |
| Artifact row, file card in a reply | Open (desktop), Download, Ask AI, Save to Notion as it is, Make a PDF, Email it, Copy name, Delete                                                                                                          | Built. Reveal in folder and Rename are not offered: the web client has no path or rename call                         |
| Memory item                        | Open, Copy, Show source conversation (when the item records one), Forget                                                                                                                                    | Built. Edit is not offered: there is no update call                                                                   |
| Schedule card                      | Run now, Pause or Resume, Edit, Duplicate, View last report, Delete                                                                                                                                         | Built                                                                                                                 |
| Connection card                    | Test, Reconnect, Copy status, Disconnect                                                                                                                                                                    | Built                                                                                                                 |
| Approval card                      | Copy exact payload, Reject                                                                                                                                                                                  | Built. Approve is never in a menu                                                                                     |
| Composer                           | Cut, Copy, Paste, Paste as plain text, Select all, Attach file, Clear draft; voice: Start or Stop, Mute; brain: deep research on or off                                                                     | Built                                                                                                                 |
| Top bar                            | Start or Stop voice, Mute, Light or Dark theme, Hide or Show sidebar                                                                                                                                        | Built                                                                                                                 |

## 15. Files, downloads and artifacts

- **Download** a finished file from the file card in the chat message. This is the only download place by design.
- **Artifacts page**: name, type, size, date. Buttons: **Open** (in the app on your PC, desktop app only), **Ask AI** (attaches the file to a new chat), **Delete**. Files that live in your own folder cannot be deleted from here ("Stays on your disk").

Status: Works (tested). Not tried in a real Electron window.

### Scheduled reports: what you see in the app (UI)

- **Settings > Schedules** lists your scheduled reports (plain-words time, mail chips with verified or pending, format, last run, next run) with pause or resume, Run now, Edit and Delete. Mail goes only to addresses verified with a 6-digit code (your account e-mail is already verified). The **Run mode** radio defaults to "While MAYA is open, and catch up when I start it"; "Also wake my PC" works only in the desktop app, asks first (Docker Desktop must run, PC on and you logged in, it creates a Windows Scheduled Task you can remove by choosing the first option) and is disabled in a browser.
- **Ask in chat:** a schedule MAYA drafts appears as a card with the exact plan, "Not created until you click", **Create schedule** and **Cancel**. Only a click creates it (no key, no voice).
- **When you open MAYA:** a "Scheduled updates" notice shows what finished while it was closed (name, time, "Needs attention", summary, **Open Artifacts** when a file was made, **Dismiss**). With "Speak the update by voice when I open MAYA" on, MAYA reads it with your microphone never opened ("Your mic is muted", **Unmute** restarts voice with the mic). If the browser blocks audio you get a **Read it aloud** button; if microphone permission is denied it stays text.
- Status: UI built and unit-tested against mocks; not run against the live scheduler backend. The main behaviour section is written by the backend work.

## 16. Setup and fixes you need once

| Item                                         | What to do                                                                                                                                                                           |
| :------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Database migration `0016` (approval buttons) | Without it, clicking Approve can leave a task stuck. Apply it (`python -m alembic -x env_file=<env> upgrade head` from `backend/`) or accept the desktop app's dialog                |
| Restart the desktop app                      | `npm --prefix apps/desktop run dev`. The old process runs the old code                                                                                                               |
| Docker Desktop                               | Must be running; otherwise the app shows "Docker Desktop is required"                                                                                                                |
| `ELECTRON_RUN_AS_NODE` in your terminal      | VS Code sets it and Electron then crashes with "does not provide an export named 'BrowserWindow'". `npm run dev` now removes it; do not start `electron .` directly in that terminal |
| Internet / DNS to Google and Notion          | Without it Gemini, search and Notion calls fail with timeouts                                                                                                                        |
| Mailbox                                      | Settings > Connections > Mailbox (address and app password)                                                                                                                          |
| A line in your `.env` that cannot be parsed  | The backend prints "could not parse statement" and ignores that line; fix that line                                                                                                  |
| If Gemini says "overloaded"                  | MAYA now tries other keys and other models by itself (section 18). Wait a minute and retry                                                                                           |

## 17. A test script: check almost everything in about an hour

Do these in order in **one new conversation** unless it says otherwise. Tick what works and note what does not.

| #   | Do this                                                                                                   | You should see                                                                   |
| --- | :-------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------- |
| 1   | Start the desktop app                                                                                     | Animated start screen, spoken hello, then the chat with the animated empty state |
| 2   | Click a suggestion chip                                                                                   | The draft is filled, nothing is sent                                             |
| 3   | `Yaad rakhna: coding examples hamesha Java me do. Mera favourite colour teal hai`                         | Saved; Memory page shows both                                                    |
| 4   | `Two sum ka code do`                                                                                      | Java code, formatted block                                                       |
| 5   | `Quadratic formula LaTeX me likho, aur 5 dollar ka price bhi`                                             | Typeset formula; "$5" stays plain                                                |
| 6   | `Aaj ki latest AI news web search karke 3 points me batao`                                                | Work log with "Searched the web", Thinking dropdown, sources, footer             |
| 7   | `Ek PDF banao` (no topic)                                                                                 | A choice card (try keys 1 to 9, Enter, Esc)                                      |
| 8   | `Java streams par 10 page ke notes PDF me banao, code examples aur tables ke saath`                       | A task with chapter progress, then a 10 page PDF; open it                        |
| 9   | `Is PDF me ek aur chapter "Collectors" add karo, design wahi rakhna`                                      | Same design, new chapter                                                         |
| 10  | `Startup pitch ka 8 slide ka deck banao, glass-aurora theme, fade transition, ek chart slide ke saath`    | A glass deck; open it in PowerPoint and look                                     |
| 11  | `Is deck ka theme editorial-navy kar do, content mat badlo`                                               | Same content, new theme                                                          |
| 12  | `500 products ka sheet banao: name, category, price, stock`                                               | 500 rows                                                                         |
| 13  | `Meri mailbox me last 5 mails dikhao` (after saving the mailbox)                                          | Five mails with labels                                                           |
| 14  | `<your address> ko hello mail bhejo`                                                                      | An approval card. Type `approved`: nothing. Click Approve: sent                  |
| 15  | `Notion me "MAYA Test" naam ka page banao`                                                                | An approval card with the exact page, then a page, then a verified message       |
| 16  | `Is page ko archive karo`                                                                                 | A manual approval card                                                           |
| 17  | Settings > Notion: switch auto-approve on for "create page" only, repeat 15                               | "Auto-approved by your setting"; step 16 still asks                              |
| 18  | `Review https://github.com/<a public repo>`                                                               | A review listing files read and not read                                         |
| 19  | Click **Voice** in the top bar; say `Java streams par 10 page ke notes banao`, then open Tasks, then back | Voice stays on; MAYA speaks progress and the result                              |
| 20  | While voice is on, open a **different** conversation                                                      | Master voice keeps going. (A composer-mic call would stop with a notice)         |
| 21  | Press the small mic button                                                                                | Muted state shown; MAYA still speaks; unmute works                               |
| 22  | `Ctrl+B`                                                                                                  | Icon strip; click a strip icon to navigate; `Ctrl+B` again restores              |
| 23  | Switch to light theme                                                                                     | Everything light, including the window controls strip                            |
| 24  | Delete the test conversation with the trash icon                                                          | A centred dialog; running tasks are kept                                         |

If a step fails, paste the prompt, what you saw, and the "Technical details" of the failed step.

## 18. What is invisible but should make MAYA more reliable

- **Model and key failover.** On a quota error (429) MAYA moves to the next API key (up to 8 keys per model). On an overload (503 or timeout on two different keys) it moves to the next model right away and remembers for a short time not to retry the overloaded one. The last fallback is `gemini-2.5-flash`. If everything fails you see "Gemini is overloaded right now. MAYA tried N models and M keys; please try again in a minute." Not verified against the real service.
- **A second opinion model (`gemini-decision-1`).** A small fast model that only answers yes/no or pick-one questions. MAYA uses it to break ties when routing a request, to flag answers that no source supports, to check that a finished step really satisfies your request, to score document quality, to warn when an approval card does not match what you asked, to flag instructions hidden in web pages or mails, and to label mail urgency. It can only make MAYA **more careful**: it never approves anything, never lowers a safety rule, and if it is unavailable MAYA works exactly as before. Not tried live; its real response shape and speed are unverified.

## 19. Planned (not built)

| Planned item                                 | What it would give you                                                                                      |
| :------------------------------------------- | :---------------------------------------------------------------------------------------------------------- |
| **Other AI providers**                       | OpenAI-compatible and Claude as extra fallbacks when Gemini is overloaded (under discussion, nothing built) |
| **Smart router for small files**             | Short answers and small files in the chat itself in seconds, background tasks only for big work             |
| **Slide-by-slide decks with picture review** | Outline first, each slide checked by looking at it, up to two repair rounds                                 |
| **"Change slide 3"**                         | Edit one slide of a deck without rebuilding all of it                                                       |
| **Voice file cards live**                    | The question and file cards appearing instantly on screen when you ask by voice                             |
| **Custom agents with more power**            | Agents that write files or send email, each with its own approval rules                                     |
| **Calendar scheduling**                      | Create and move events after approval, per-user Google connection                                           |
| **More connections**                         | Other tools you work with, each per user                                                                    |
| **Recurring jobs**                           | "Every Monday 9am, send me a briefing"                                                                      |
| **PDF editing**                              | Today PDFs cannot be edited, only rebuilt from text                                                         |
| **Service health by voice**                  | Announcing that Docker, mail or Notion connections broke                                                    |
| **Hosted multi-user use**                    | The folder feature assumes MAYA runs on your own computer                                                   |

## 20. Things MAYA will not do

- Delete your files, or touch anything outside the connected folder.
- Replace an original file, send an email, or write to Notion without your click on the approval card.
- Treat the words "approved", "yes" or a spoken confirmation as an approval.
- Invent statistics, quotes, dates, contact details or links. If it cannot source something it says so.
- Follow instructions hidden inside files, web pages, mails or repositories it reads. That text is treated as data.
- Show your tokens or passwords back to you, or use another user's.

## 21. How MAYA is different from other assistants

This comparison is based on public information gathered on 2026-11-11 and not refreshed since. Where vendors do not publish details, that is said. It is not a claim that MAYA is better at everything: MAYA is early, and live runs keep finding real bugs.

| Topic                        | Typical general assistants (ChatGPT, Claude, Gemini, Z.ai)                                     | MAYA                                                                                                                    |
| :--------------------------- | :--------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| How files are made           | Often by running code in a sandbox, or per-slide HTML (Z.ai). Details are mostly not published | The model writes structured content and MAYA's own code renders it, then checks it. No free-form code runs by default   |
| Your files                   | Upload or connect through the provider's cloud features                                        | Works on a folder on your own computer, with strict path safety and approval before any overwrite                       |
| Honesty when something fails | Varies                                                                                         | A failed research step blocks the file; a failed step stops a task saying "Completed"; length misses are stated plainly |
| Design                       | Templates or a model-written layout                                                            | A fresh design per request, 25 deck themes, enforced readable contrast, and your stated colours win                     |
| Your own agents              | Custom agents exist in some coding tools                                                       | Agents are plain files in your folder (`.maya/agents/`), limited to your own permissions                                |
| Tools and accounts           | Account-level connectors                                                                       | Per-user, encrypted credentials for mail, GitHub and Notion; approvals for every side effect                            |
| Voice                        | Voice modes exist in several products                                                          | Voice is the primary interface, and a top-bar voice tracks all your tasks and speaks progress                           |
| Where it runs                | Provider cloud                                                                                 | A desktop app with its own backend, so your files stay on your machine                                                  |

Where MAYA is behind today: small requests are slower than they should be because they become background tasks, deck design is not yet reviewed by looking at rendered slides, only Gemini models are supported, and many features were tested only with fakes. Section 19 lists the planned fixes.

## 22. Quick checks after you change something

| Check                    | Prompt or action                                                                |
| :----------------------- | :------------------------------------------------------------------------------ |
| App is the new build     | The sidebar folds with the hamburger button; the empty chat shows the MAYA mark |
| Folder is connected      | Settings > Files shows your folder; `Is folder ki files ki list dikhao`         |
| Memory works             | Section 5                                                                       |
| Search is honest         | First prompt of section 6                                                       |
| Files are correct        | Sections 7, 8 and 9                                                             |
| Connections work         | Settings > Connections > Test connection (mailbox, Notion, GitHub)              |
| Approvals are click-only | Section 11, first row                                                           |
| Voice tracks tasks       | Section 13, third prompt                                                        |

## 23. Regression tests: every bug we hit, and how to check it

Each row is a bug that was seen in real use (screenshots and transcripts), the exact thing to type or do, and what must happen now. Run these after any big change. "Fixed (tested)" means automated tests with fakes cover it; none of these was re-run live against the real Gemini, Notion, mail or a microphone unless a row says so. Where a row depends on your setup (mailbox, Notion, Docker, folder permission) it says so.

### 23.1 Memory and "topic" mistakes

| Bug we saw                                                        | Type or do this                                                              | Must happen now                                                    |
| :---------------------------------------------------------------- | :--------------------------------------------------------------------------- | :----------------------------------------------------------------- |
| A memory prompt created a DOCX                                    | `Yaad rakhna: mera favourite colour teal hai`                                | Saved to memory. No file, no task                                  |
| "Ek PDF banao" made a portfolio PDF from the saved profile        | `Ek PDF banao` (no topic)                                                    | A question card asks the topic. No task, no file                   |
| "Notes banao" invented Java topics from the saved Java preference | `make a detailed notes and save to notion` (no topic)                        | One card asks topic, depth and the Notion page. No task starts     |
| Language preference ignored (Java vs Python)                      | `Yaad rakhna: coding examples hamesha Java me do`, then `Two sum ka code do` | Java code. Memory only picks the language, never the subject       |
| Memory leaked into unrelated answers                              | `Aaj ki latest AI news web search karke 3 points me batao`                   | Work log says "No memory needed"; the answer has no personal facts |

### 23.2 Documents, pages and slides

| Bug we saw                                                  | Type or do this                                                                       | Must happen now                                                                                           |
| :---------------------------------------------------------- | :------------------------------------------------------------------------------------ | :-------------------------------------------------------------------------------------------------------- |
| 10-page PDF came out thin or with empty pages               | `Java streams par 10 page ke notes PDF me banao, code examples aur tables ke saath`   | Dense PDF with about 10 pages, code blocks and tables, no empty space                                     |
| A file was rejected because the page or slide count was off | `Startup pitch ka 8 slide ka deck banao`, then `5 page ka PDF banao`                  | The file is always delivered. Count is fitted; if it still misses, a plain note says the true count       |
| 500 rows became 5                                           | `500 products ka sheet banao: name, category, price, stock`                           | A sheet with 500 rows                                                                                     |
| Editing a document changed its styling                      | `report.docx me sirf content update karo, styling mat badlo`                          | Updated in place; fonts and colours unchanged; backup kept in `.maya-backups`                             |
| Editing an existing file created a new "(edited)" copy      | `vscode_shortcuts.md me Command Palette shortcut jodo`                                | File is updated in place with the same name and path; previous version kept in `.maya-backups`; no "(edited)" file |
| Raw LaTeX in answers                                        | `Quadratic formula aur Euler identity LaTeX me likho, aur 5 dollar ka price bhi`      | Typeset formulas; "$5" stays plain text                                                                   |
| "give main.py file" returned main.txt                       | `make a main.py with a small script`, then `give in a file`, then `give main.py file` | A file named main.py with exactly the code shown in the chat; the chat names the real file                |
| A file was made without being asked                         | `Deep research about solar batteries` or `notes banao on Java streams`                | Answer appears in chat only. One optional line offers PDF/Word/slides/Notion; no file unless you name one |

### 23.3 Notion, mail and connections

| Bug we saw                                                                                         | Type or do this                                                               | Must happen now                                                                                                                                                       |
| :------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Notion page came out short                                                                         | `Java streams ke detailed notes Notion me daalo` (Notion connected)           | A long task writes a full book (chapters), with an approval card showing the exact content                                                                            |
| "No Notion parent page was given", task failed                                                     | Same prompt without naming a page                                             | A card lists real Notion pages ("Suggested" first); the answer resumes the task                                                                                       |
| Task stuck forever at the Notion step (12 of 13 done, no card)                                     | A learning-book task for Notion; or restart the app while one is stuck        | The card appears; a wedged task is re-queued within a minute or fails with a plain reason                                                                             |
| Asked for the Notion page twice (after you chose it), then "No pending approval request was found" | `Java streams ke detailed notes Notion me daalo`, answer the Destination card | The chosen page is used (found by exact title); no second question. If it is truly unclear, the question shows on the task card itself and answering resumes the task |
| "Why ask when auto-approve is on?" for a long book                                                 | Settings > Notion auto-approve on, then a long notes-to-Notion task           | The approval card says "Large page (N blocks): auto-approve does not apply, please review." Pages over 300 blocks always ask first                                    |
| "Add this notes in Notion as it is" regenerated new text                                           | Build a PDF, then `add this notes in notion as it is`                         | Only the destination is asked. The existing text is copied word for word; no "Topic" question, no new task                                                            |
| Notion auto-approved when it should not (archive, delete)                                          | Settings > Notion: auto-approve on, then `Is page ko archive karo`            | Archive always shows a manual approval card                                                                                                                           |
| Mail arrived as one paragraph with visible `\n\n`                                                  | `<your address> ko hello mail bhejo` (mailbox saved)                          | The approval card shows real paragraphs; the received mail has real line breaks                                                                                       |
| "Send to same mail" produced a false "approve on the card" and nothing happened                    | `good send this to same mail` after a mail was prepared                       | A short card confirms the address from the chat; no claim of a card unless one exists                                                                                 |
| A user with no connections could use the owner's Notion, GitHub, mailbox or calendar               | Sign in as a second account with no connections; ask for Notion or mail       | "Not connected: connect it in Settings > Connections". Only the owner (MAYA_ENV_CONNECTIONS_USERS) uses server tokens                                                 |
| Mail used the Google access token and failed with 401                                              | `Meri mailbox me last 5 mails dikhao`                                         | Uses only the mailbox saved in Settings > Connections                                                                                                                 |

### 23.4 Approvals and tasks

| Bug we saw                                                       | Type or do this                                              | Must happen now                                                                                                    |
| :--------------------------------------------------------------- | :----------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------- |
| "Approved" typed in chat, still waiting                          | Ask for a mail, then type `approved`                         | Nothing happens. Only the button approves                                                                          |
| Clicking Approve left the task stuck (database permission error) | Click **Approve** on a card                                  | Task continues and finishes (needs migration 0016 applied, see section 16)                                         |
| "Failed to list approval requests" (500)                         | Open a chat with an approval that has plain-text details     | The card loads                                                                                                     |
| Task said "Completed" while a step failed                        | Start a task where one step cannot work                      | "Finished with problems" with the real reason, never a clean "Completed"                                           |
| Almost every task ended "Completed with errors"                  | Run a normal research task, a document, `@agent review file` | A good result is "Done". An automatic check may add a small "Note: ..."; status is unchanged by default            |
| A second task started while the first ran, both failed           | Start two research tasks at once                             | Both finish independently; a waiting task shows "Queued: N tasks ahead"                                            |
| Task ended and the chat only said "task complete"                | Finish any research or document task                         | A message in the same chat says what it got: findings with sources, or the file and its outline; voice says it too |
| "Started: ..." task title was made from card answers             | `add this notes in notion as it is` and answer the card      | The task keeps your original request as its instruction                                                            |

### 23.5 Search and research

| Bug we saw                                                           | Type or do this                                           | Must happen now                                                                                                |
| :------------------------------------------------------------------- | :-------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------- |
| Search used Google and failed ("search service is busy")             | `What were the LeetCode problems for each day this week?` | Uses DuckDuckGo and the other free engines; the step names the real engine; Google only if you enabled it      |
| Every free engine blocked                                            | Run many searches quickly                                 | "Search engines are temporarily limiting requests; retry in about N minutes" and an offer of general knowledge |
| "Deep research" ran an old one-source search and a File step         | Turn on the brain toggle (or type `deep research ...`)    | One deep-research step reads 20+ sites (or says exactly how many were readable); no File step                  |
| General-knowledge note added under the "Started: ..." text           | Start any task                                            | The start text is only the system's own sentence, no "not checked against the web" note                        |
| Broken citation text `"[2]" (www.msn.com)` and `["[4]"](https://...` | Any web-search answer                                     | Clean small `[n]` links and one **Sources** list                                                               |

### 23.6 Chat screen, voice and desktop

| Bug we saw                                                                         | Type or do this                                                              | Must happen now                                                                                                                       |
| :--------------------------------------------------------------------------------- | :--------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------ |
| Reply cut off mid-sentence ("Reply interrupted")                                   | A long answer while Gemini is busy                                           | The reply continues by itself from where it stopped (up to 3 recoveries); only then "Ask again"                                       |
| Text generation hit a limit                                                        | Ask for a very long answer                                                   | No cap at the model maximum; a MAX_TOKENS stop auto-continues                                                                         |
| The "Working" block vanished when switching conversations                          | Send a message, open another conversation, come back                         | The running reply continues and its work block is still there; finished ones show "Worked for N s"                                    |
| Thinking text showed raw `**` and no paragraphs                                    | Open the Thinking dropdown on a reply                                        | Headings and paragraphs are rendered                                                                                                  |
| Scheduled report showed raw `# ## ###`                                             | Open Settings > Schedules or the "Scheduled updates" notice                  | Rendered report in a scrollable box with "Show full report"                                                                           |
| Floating circle sat at the top, not the centre                                     | Open an empty conversation                                                   | The mark, orbits and chips are centred in the pane                                                                                    |
| Sidebar row: delete icon cut off by a long title                                   | Create a conversation with a very long first message                         | The title ends with "…" and the icons stay inside the sidebar                                                                         |
| Ctrl+B crashed (`event.key` undefined)                                             | Press Ctrl+B; also trigger browser autofill                                  | Sidebar folds; no error overlay                                                                                                       |
| Dialogs appeared squeezed inside the sidebar                                       | Delete a conversation (trash icon)                                           | A centred dialog over the whole window                                                                                                |
| Light theme left the window controls dark                                          | Switch to the light theme (desktop app)                                      | The minimise/maximise/close strip turns light                                                                                         |
| Voice stopped when switching conversation / no mute                                | Click **Voice**, open Tasks, open another conversation, press the mic button | Top-bar voice keeps running; mute silences only your mic (the wave stays); a composer-mic call stops only on a different conversation |
| Voice refused to remember a resume ("cannot store personal data")                  | By voice or chat: `Yaad rakhna: meri skills Java aur Spring hain`            | Saved. Only passwords, tokens, cards and ID numbers are refused. (Remembering a whole PDF is a planned feature)                       |
| Voice answered in Spanish after a stray word                                       | Say a mis-heard foreign word, then continue in English                       | MAYA keeps the language, says it did not catch that                                                                                   |
| Voice wrote tiny Notion notes for "100 pages"                                      | By voice: `Java ke 100 page ke notes Notion me banao`                        | A long job is started (estimate shown), not direct Notion tool calls; never "too large for Notion"                                    |
| Voice said "I cannot schedule"                                                     | `Can you add a scheduler?` (chat and voice)                                  | MAYA says yes and asks the missing details (time, days, recipient, format)                                                            |
| The voice "Hi" at start was a man's voice                                          | Start the desktop app                                                        | A female voice says hello (needs a female system voice installed); Gemini Live uses Leda                                              |
| Voice said "I will put it on your screen" but nothing appeared | By voice: `list me all the VS Code shortcuts`, then `share me the list` (or `show me the two sum code`) | MAYA calls `show_in_chat`; the list or code block appears in the chat at once (code with a copy button) and she says one short sentence; if it could not be saved she says so |
| Desktop app crashed at start with "does not provide an export named BrowserWindow" | `npm run dev` from VS Code's terminal                                        | Starts (the launcher removes `ELECTRON_RUN_AS_NODE`)                                                                                  |
| "MAYA engine stopped while starting" (a broken source file)                        | Start the app after agents edited code                                       | Starts; if not, `api.log` names the file and line                                                                                     |
| "Cannot write to that folder" when connecting a folder                             | Settings > Files > connect a folder in Documents                             | The message now says why (access denied, Controlled folder access); pick a folder outside Documents/Desktop/Pictures                  |

### 23.7 Scheduler and custom agents

| Bug we saw                                                  | Type or do this                         | Must happen now                                                                                             |
| :---------------------------------------------------------- | :-------------------------------------- | :---------------------------------------------------------------------------------------------------------- |
| `@python-reviewer review main.py` ran a GitHub repo listing | `@python-reviewer review main.py`       | Runs your agent on main.py (attached, chat file, or folder), or says the agent does not exist; never GitHub |
| A scheduled mail would use the user's own mailbox           | Create a daily 8 AM report by mail      | Mail is sent by MAYA's own mail service to a verified address; never your saved mailbox                     |
| Typed "yes" created a schedule                              | In the schedule preview card type `yes` | Nothing; only the **Create schedule** button creates it                                                     |

### 23.8 Not fixed or not checkable yet

- Real Gemini, Notion, SMTP and a microphone were not used in any of these checks; they are offline tests.
- Real-browser PDF tests do not run in the coding sandbox (Windows `EPERM`); run `cd backend; .\.venv\Scripts\python -m pytest tests\unit\test_pdf_real_browser.py -q` in your own PowerShell.
- Notion books over about 30 pages are one page written in batches (no hub page with one sub-page per chapter yet), and appending into an existing page is not built.
- Saving a whole PDF or resume as searchable memory is not built (decision pending).

## Citations and memory (2026-11-13)

- Web answers: cite only as `[n]` (the number from the numbered sources); never write URLs, links, quotes around markers or a Sources list. MAYA appends the list.
- Memory is context, never the subject. The memory gate may skip memory for a request that does not need it, and only the relevant memories are injected.
