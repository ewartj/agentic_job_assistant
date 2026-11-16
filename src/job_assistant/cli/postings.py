"""job-posting: save a job posting from the clipboard, ready for job-apply or the web app.

    uv run job-posting acme_ds --company "Acme" --role "Data Scientist" --url https://...
    uv run job-posting acme_ds --replace      # re-paste a posting that was saved wrongly
    uv run job-posting acme_ds --file posting.txt

The posting goes to data/evals/postings/NAME.md, with a header holding what you give.
"""
import argparse
from pathlib import Path

from job_assistant.config import get_settings
from job_assistant.console import read_clipboard, utf8_output
from job_postings import save_posting, split_front_matter


def main() -> None:
    utf8_output()
    parser = argparse.ArgumentParser(description="Save a job posting from the clipboard (or a file).")
    parser.add_argument("name", help="the posting's name: letters, digits, - and _ (e.g. acme_ds)")
    parser.add_argument("--file", type=Path, help="read the posting from this file instead of the clipboard")
    parser.add_argument("--company", help="stored in the posting's header")
    parser.add_argument("--role", help="stored in the posting's header")
    parser.add_argument("--url", help="stored in the posting's header")
    parser.add_argument("--replace", action="store_true",
                        help="overwrite an existing posting (its header is kept unless given new values)")
    args = parser.parse_args()
    config = get_settings()
    text = args.file.read_text(encoding="utf-8") if args.file else read_clipboard()
    try:
        path = save_posting(config.postings_dir, args.name, text, replace=args.replace,
                            company=args.company, role=args.role, url=args.url)
    except (ValueError, FileExistsError) as e:
        parser.error(str(e))
    # Show the start of what was saved, so a wrong clipboard (e.g. a command) is obvious.
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    preview = "\n".join(f"  | {line}" for line in body.splitlines()[:3])
    print(f"saved posting to {path}:\n{preview}")
    runs = config.traces_dir / f"{path.stem}.jsonl"
    if args.replace and runs.exists():
        print(f"note: earlier runs in {runs} were made on the old text - delete it to start the scores afresh")
    print(f"\nnext: uv run job-apply {path}")
