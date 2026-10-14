"""The daily watch sweep, run directly — no web server (GitHub Actions' entry point).

    cd backend && uv run python scripts/run_watches.py               # sweep + digest
    cd backend && uv run python scripts/run_watches.py --no-notify   # sweep, send nothing
    cd backend && uv run python scripts/run_watches.py --strict      # any failed watch fails the run

Target: Turso when TURSO_DATABASE_URL (+ TURSO_AUTH_TOKEN) is set — in the
environment or backend/.env, exactly as the app connects — else the local
SQLite file. Same code path as POST /api/watches/run: watcher.run_watches()
re-searches every active watch and sends one digest. The run is recorded as a
"watch_run" job row (jobs.run_job), so the app sees it like any other; the
SerpApi budget counter, the response cache and the alert log are the same
database tables the app uses, so both count against one budget.

Prints a summary table (and appends it to $GITHUB_STEP_SUMMARY when set).
Exit status: 0 = the sweep ran (failed watches are reported as warnings
unless --strict); 1 = the run itself failed, every watch failed, or the digest
could not be delivered although a channel is configured; 130 = interrupted.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import jobs, notify, storage, watcher  # noqa: E402  (loads backend/.env)

logger = logging.getLogger("run_watches")

# Never echoed, even inside an error message (GitHub masks secrets in logs, but
# not in a summary written elsewhere, and a local run has no masking at all).
SECRET_VARS = ("TURSO_AUTH_TOKEN", "TURSO_DATABASE_URL", "TELEGRAM_BOT_TOKEN", "NTFY_TOPIC", "SERPAPI_API_KEY", "TRAVELPAYOUTS_TOKEN", "OPENAI_API_KEY",
               "ANTHROPIC_API_KEY", "CRON_SECRET", "APP_PASSWORD")

INTERRUPTED = ("Interrupted: the scheduled run was cancelled or timed out. Watches checked before "
               "that kept their results; the rest were skipped.")


def redact(text: str) -> str:
    """One line with every configured secret value blanked out."""
    for var in SECRET_VARS:
        value = os.environ.get(var, "").strip()
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text.replace("\r", " ").replace("\n", " ")


def channels_configured() -> bool:
    """Whether notify.send_notification has anywhere to deliver to."""
    return bool(os.environ.get("NTFY_TOPIC")) or bool(os.environ.get("TELEGRAM_BOT_TOKEN") and notify.chat_ids())


def target() -> str:
    return "Turso" if os.environ.get("TURSO_DATABASE_URL") else f"local SQLite ({storage.DB_PATH})"


def config_line() -> str:
    """Which fare sources this run has — they decide what a watch check costs, so
    they should match the app's (Render's) settings. Counts, never keys."""
    from app.providers import serpapi, travelpayouts

    if serpapi.configured():
        try:
            used = f"{serpapi.used_this_month()} used"
        except Exception:
            used = "usage unknown"
        serp = f"on ({used} of {serpapi.monthly_budget()} this month)"
    else:
        serp = "off"
    return (f"Google Flights (SerpApi): {serp} · Aviasales cache: {'on' if travelpayouts.configured() else 'off'}"
            f" · model: {os.environ.get('FLIGHT_SCOUT_MODEL') or 'default'}")


def request(notify_on: bool) -> dict:
    """What started the job (the UI's job record); a link to the Actions run, never a secret."""
    req: dict = {"scheduled": True, "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") else "script"}
    if not notify_on:
        req["notify"] = False
    run_id, repo = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_REPOSITORY")
    if run_id and repo:
        req["run_url"] = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}"
    return req


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return redact(text).replace("|", "\\|")


def _price(value) -> str:
    return f"{value:,.0f}" if isinstance(value, (int, float)) else "—"


def _outcome(row: dict) -> str:
    if row.get("error"):
        return "failed: " + row["error"][:160]
    if row.get("skipped"):
        return "skipped (credit plan)"
    if row.get("suppressed"):
        return "hit, already alerted"
    if row.get("rules"):
        return "ALERT" if row.get("alerted") else "hit (not sent)"
    return "no alert" if row.get("best_price") is not None else "no comparable deals"


def summary_markdown(job: jobs.Job, notify_on: bool) -> str:
    """The run as Markdown: a status line and one table row per watch."""
    lines = [f"### Watch sweep: {job.status}", ""]
    summary = job.summary or {}
    if job.error:
        lines += [f"**Error:** {_cell(job.error)}", ""]
    if summary:
        channels = ", ".join(summary.get("digest_channels") or []) or (
            "none configured" if not channels_configured() else "not delivered" if notify_on else "off (--no-notify)")
        lines += [f"Watches checked: **{summary.get('watches_run', 0)}** · alerts sent: "
                  f"**{summary.get('alerts_sent', 0)}** · digest: {channels} · database: {target().split(' ')[0]}",
                  "", config_line(), ""]
    rows = summary.get("results") or []
    if rows:
        lines += ["| # | Route | Best | Threshold | Deals | Pre-screen | Sources | Outcome |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            ccy = r.get("currency") or ""
            lines.append("| " + " | ".join(_cell(v) for v in (
                r.get("watch_id"), r.get("route"),
                f"{_price(r.get('best_price'))} {ccy}".strip() if r.get("best_price") is not None else "—",
                f"{_price(r.get('threshold'))} {ccy}".strip() if r.get("threshold") is not None else "—",
                r.get("deals_found", "—"), r.get("prescreen") or "—",
                ", ".join(r.get("sources") or []) or "—", _outcome(r),
            )) + " |")
        lines.append("")
    lines.append(f"Job `{job.job_id}`")
    return "\n".join(lines) + "\n"


def exit_code(job: jobs.Job, notify_on: bool, strict: bool) -> tuple[int, list[str]]:
    """(exit status, problems to report). See the module docstring."""
    if job.status != "done":
        return 1, [f"run {job.status}: {redact(job.error or 'no detail')}"]
    summary = job.summary or {}
    rows = summary.get("results") or []
    failed = [r for r in rows if r.get("error")]
    problems = [f"watch #{r.get('watch_id')} ({r.get('route')}) failed: {redact(r['error'])}" for r in failed]
    code = 0
    if failed and (strict or len(failed) == len(rows)):
        code = 1
    # run_watches sends a digest whenever it checked a watch, failed ones included.
    if notify_on and rows and channels_configured() and not summary.get("digest_channels"):
        problems.append("the digest was not delivered to any channel (see the log above)")
        code = 1
    return code, problems


def _sigterm(_signum, _frame):
    raise KeyboardInterrupt  # the runner's cancel/timeout: record the job as interrupted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-notify", action="store_true", help="run the sweep but send no digest")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any watch fails")
    args = parser.parse_args(argv)
    notify_on = not args.no_notify

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO — Telegram's carries the bot token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    previous_sigterm = signal.signal(signal.SIGTERM, _sigterm)

    print(f"Watch sweep against {target()}, digest {'on' if notify_on else 'off'}", flush=True)
    print(config_line(), flush=True)
    in_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    # Progress text quotes fare pages and LLM output: never let a line of it
    # be read as a workflow command (::set-env:: and friends).
    resume = uuid.uuid4().hex
    if in_actions:
        print(f"::stop-commands::{resume}", flush=True)

    def work(job_id: str) -> dict:
        def on_event(event: dict) -> None:
            jobs.emit_event(job_id, event)
            if event.get("kind") == "phase":
                print("  " + redact(str(event.get("text", ""))), flush=True)

        return {"summary": watcher.run_watches(on_event=on_event, notify=notify_on)}

    try:
        job = jobs.run_job("watch_run", "scheduled watch run", work, request=request(notify_on),
                           interrupted=INTERRUPTED)
    except KeyboardInterrupt:
        print("Interrupted; the job is recorded as interrupted.", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if in_actions:
            print(f"::{resume}::", flush=True)

    text = summary_markdown(job, notify_on)
    print("\n" + text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text)

    code, problems = exit_code(job, notify_on, args.strict)
    for problem in problems:
        level = "error" if code else "warning"
        print(f"::{level}::{problem}" if in_actions else f"{level}: {problem}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
