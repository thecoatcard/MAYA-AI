"""Table tests for the deterministic chat router (no model, no network).

The first table is the owner-reviewed routing set (English and Hinglish). Expected routes: answer
(inline answer), model (the model chooses an inline file, a task or a plain answer), task (a
background task), approval (a send, delete, replace or publish verb; never run inline), agent
(the custom-agent draft path). Rows 38 and 39 are bare requests ("Create a ppt", "Ek report bana
do"): the router sends them to the model, which asks the one clarifying question.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.chat_router import Decision, Route, route_message

ROUTING_TABLE: list[tuple[str, bool, Route]] = [
    ("What is a mutex?", False, Route.ANSWER),
    ("Explain REST vs GraphQL in 5 lines", False, Route.ANSWER),
    ("Java me interface aur abstract class me kya difference hai?", False, Route.ANSWER),
    ('Rewrite formally: "give me the report asap"', False, Route.ANSWER),
    ('Translate "good morning team" to Hindi', False, Route.ANSWER),
    ("Mujhe ek chhoti si bio likh do 50 words me", False, Route.ANSWER),
    ("Hi, kaise ho?", False, Route.ANSWER),
    ("Summarize this pasted 150-word paragraph", False, Route.ANSWER),
    ("Python me list comprehension ka ek example do", False, Route.ANSWER),
    ("Write a 4-line poem about monsoon", False, Route.ANSWER),
    ("Create a notes.md with 10 key SQL tips", False, Route.MODEL),
    ("Ek todo.txt bana do jisme meri 5 daily tasks ho", False, Route.MODEL),
    ("Make a single-page HTML resume for a fresher", False, Route.MODEL),
    ("A CSV with 20 sample employees (name, dept, salary)", False, Route.MODEL),
    ("Write a Python script that renames files in a folder, save as rename.py", False, Route.MODEL),
    ("README.md bana do mere todo app (Flask) ke liye", False, Route.MODEL),
    ("Make a JSON config for a chatbot with 5 intents", False, Route.MODEL),
    ("In the attached notes.txt fix typos", True, Route.MODEL),
    ("Attached report.docx me budget 5000 se 7500 kar do", True, Route.MODEL),
    ("Attached data.csv me status column add karo, default open", True, Route.MODEL),
    ("Write a 6-page study guide PDF on SQL joins", False, Route.TASK),
    ("15 slide ki ppt bana do Machine Learning par", False, Route.TASK),
    ("Make a 10-slide deck on solar energy, dark theme", False, Route.TASK),
    ("30 page ka Java notes PDF banao with chapters", False, Route.TASK),
    ("Build a multi-file landing page (HTML, CSS, JS) with contact form", False, Route.TASK),
    ("Research top 5 EV makers in India with sources, compile a report", False, Route.TASK),
    ("Fintech India market analysis, cite sources, give an excel", False, Route.TASK),
    ("Ek poora 4 page ki portfolio website bana do", False, Route.TASK),
    ("Excel budget workbook with 3 sheets and formulas", False, Route.TASK),
    ("Review my GitHub repo owner/name and write a findings doc", False, Route.TASK),
    ("Run in background: compile all my notes into a handbook", False, Route.TASK),
    ("Write an 8000 word ebook on productivity", False, Route.TASK),
    ("Send an email to rahul@example.com: meeting moved to 5 pm", False, Route.APPROVAL),
    ("Rahul ko mail bhej do ki kal ki meeting cancel hai", False, Route.APPROVAL),
    ("Replace the original report.docx with the edited version", True, Route.APPROVAL),
    ("Draft an email to my manager asking for leave (do not send)", False, Route.ANSWER),
    ("Draft and send the leave email to my manager", False, Route.APPROVAL),
    ("Create a ppt", False, Route.MODEL),
    ("Ek report bana do", False, Route.MODEL),
    ("A 20-row marks CSV and also a 12-page PDF analysis", False, Route.TASK),
]


@pytest.mark.parametrize(("text", "attached", "expected"), ROUTING_TABLE)
def test_routing_table(text: str, attached: bool, expected: Route) -> None:
    decision = route_message(text, has_attachments=attached)
    assert decision.route is expected, (text, decision)


EXTRA_TABLE: list[tuple[str, Route]] = [
    ("Create an agent that reviews my Python files", Route.AGENT),
    ("mere liye ek agent bana do jo weekly report banaye", Route.AGENT),
    ("Create an agent that sends emails to my team", Route.AGENT),
    ("Explain how to delete a git branch", Route.ANSWER),
    ("What is a PDF?", Route.ANSWER),
    ("Can you make a 10 page PDF on Java?", Route.TASK),
    ("Delete the old backup files now", Route.APPROVAL),
    ("Publish the landing page to production", Route.APPROVAL),
    ("Pull request banao mere branch se", Route.APPROVAL),
    ("Don't send anything, just draft the reply", Route.ANSWER),
    ("Send me the notes as a file", Route.MODEL),
    ("Mujhe ek chhoti poem likh do", Route.ANSWER),
    ("Ek notes.txt bana do Python loops par", Route.MODEL),
    ("Make me a 9 page PDF on climate", Route.TASK),
    ("Make a 8 page PDF on climate", Route.TASK),
    ("Create a 13 slide deck on AI ethics", Route.TASK),
    ("Ek CSV bana do 10 products ki list ke saath", Route.MODEL),
    ("Put this in a background task: summarise my inbox daily at 9", Route.TASK),
    ("Every morning at 8 send me a news digest", Route.TASK),
    ("Write a 3000 word essay on rivers", Route.TASK),
    ("What are the sources of solar energy?", Route.ANSWER),
    ("I need to know how a bank statement works", Route.ANSWER),
    ("Ek sheet banao monthly kharch ka, excel mein", Route.TASK),
    ("What is the difference between a CSV and an Excel file?", Route.ANSWER),
    ("Build a website for my bakery", Route.TASK),
    ("How do I deploy my app to Vercel?", Route.ANSWER),
    ("Translate this note to Hindi", Route.ANSWER),
    ("Make a checklist for moving house", Route.MODEL),
    ("Make a JSON file with my favourite books", Route.MODEL),
    ("Create a text file with 5 jokes", Route.MODEL),
    ("A short html page with a button", Route.MODEL),
    ("Ye lambi report ko research karke sources ke saath banao", Route.TASK),
    ("Haan, shuru karo", Route.ANSWER),
    ("Thanks, that helps", Route.ANSWER),
]


@pytest.mark.parametrize(("text", "expected"), EXTRA_TABLE)
def test_routing_extra_english_and_hinglish(text: str, expected: Route) -> None:
    assert route_message(text).route is expected, text


def test_the_table_covers_at_least_forty_prompts() -> None:
    assert len(ROUTING_TABLE) >= 40


def test_a_decision_names_its_reason() -> None:
    decision = route_message("Make a 12 page PDF on Java")
    assert decision == Decision(Route.TASK, "over_limit_pages")


def test_empty_text_is_an_answer_and_never_a_task() -> None:
    assert route_message("   ").route is Route.ANSWER


def test_an_inline_file_over_the_size_cap_is_refused_by_the_tool() -> None:
    import asyncio

    from app.gateway.model_gateway import ModelGateway
    from app.services.artifact_service import ArtifactService
    from app.services.inline_files import InlineFileError, InlineFiles

    tool = InlineFiles(
        artifacts=MagicMock(spec=ArtifactService),  # never reached: the size check comes first
        gateway=MagicMock(spec=ModelGateway),
        alias="orchestrator",
        workspace_id=uuid4(),
        user_id=uuid4(),
        session_id=uuid4(),
        file_services=None,
        max_output_tokens=1,
    )
    big = {"name": "big.txt", "content": "x" * (200 * 1024 + 1)}
    try:
        asyncio.run(tool.create(big))
    except InlineFileError as error:
        assert "200 KB" in str(error)
    else:  # pragma: no cover - the test must fail if the cap is removed
        raise AssertionError("an oversized file was accepted")


EDIT_CASES: list[tuple[str, str]] = [
    ("slide 3 mein ek point add karo", "pptx"),
    ("slide 3 me ek pont ad karo", "pptx"),
    ("heading change karo", "pptx"),
    ("heding chnge karo", "docx"),
    ("table mein ek row add karo", "docx"),
    ("isko 1 page mein karo", "pdf"),
    ("page 2 mein ek heading add karo", "pdf"),
    ("add a bullet to slide 2", "pptx"),
    ("change the title font to bold", "pptx"),
    ("dusri sheet mein total add karo", "xlsx"),
    ("total row add karo", "xlsx"),
    ("isko chhota karo", "pdf"),
]


@pytest.mark.parametrize(("text", "fmt"), EDIT_CASES)
def test_edit_of_live_file_is_a_model_edit_turn_not_a_task(text: str, fmt: str) -> None:
    decision = route_message(text, live_formats=frozenset({fmt}))
    assert (decision.route, decision.reason) == (Route.MODEL, "edit_working_file"), text


@pytest.mark.parametrize(
    ("text", "fmt", "route"),
    [
        ("ek naya report banao", "pdf", Route.MODEL),
        ("naya deck banao AI in healthcare par", "pptx", Route.TASK),
        ("6 slide deck AI in healthcare par, dark theme", "pptx", Route.TASK),
        ("make a 6 slide deck on solar", "pptx", Route.TASK),
        ("background task mein chalao", "pdf", Route.TASK),
        ("isko background mein chalao, slide 3 add karo", "pptx", Route.TASK),
        ("make a 30 page report on AI", "pdf", Route.TASK),
        ("slide 3 ko bold karo", "xlsx", Route.TASK),
    ],
)
def test_new_files_tasks_and_wrong_kind_keep_their_old_route(
    text: str, fmt: str, route: Route
) -> None:
    decision = route_message(text, live_formats=frozenset({fmt}))
    assert decision.route is route, (text, decision)
    assert decision.reason != "edit_working_file"


def test_without_a_live_file_the_edit_message_routes_as_before() -> None:
    decision = route_message("slide 3 mein ek point add karo")
    assert decision == Decision(Route.TASK, "binary_deliverable")
