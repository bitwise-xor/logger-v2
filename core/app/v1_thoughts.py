"""Saving thoughts (ideas, tasks, notes, projects, links, closing finished tasks). Copied unchanged from
logger-app connector/server.py (v1), in v1's order; helpers v2 already has come from the other v1 modules."""
import difflib
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .v1_log import Reject, fmt
from .when import parse_when

EXTRACTOR_VERSION = "chat-v1"


ThoughtType = Literal["idea", "question", "task", "project", "note", "journal", "reading", "progress"]


NextStep = Literal["research", "plan", "do", "read"]


Area = Literal["health", "projects", "finance", "immigration", "learning", "personal"]


Stage = Literal["inbox", "active", "someday", "done", "dropped"]


Source = Literal["chat", "document", "link", "image"]


LinkType = Literal["part_of", "follow_up_of", "related", "completes", "duplicate_of"]


class Link(BaseModel):
    link_type: LinkType
    to_id: int
    from_id: Optional[int] = Field(None, description="omit inside a new thought (the thought is the 'from' side)")
    by_user: bool = Field(False, description="true only if the user stated this link themselves")


class Thought(BaseModel):
    summary: str = Field(description="cleaned neutral one-liner, never 'the user'")
    thought_type: Optional[ThoughtType] = None
    next_step: Optional[NextStep] = None
    area: Optional[Area] = None
    tags: list[str] = Field(default_factory=list)
    project: Optional[str] = Field(None, description="project name this belongs to; unknown names create the project")
    completes: Optional[int] = Field(None, description="progress only: id of the task this finishes")
    when: Optional[str] = Field(None, description="when it happened, in the user's own words; omit if now")
    stage: Optional[Stage] = None
    source: Source = "chat"
    source_detail: Optional[str] = None
    needs_review: bool = False
    review_reason: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)
    links: list[Link] = Field(default_factory=list)


_GENERIC = {"idea", "need", "thing", "new", "use", "get", "make", "want", "think", "know", "like", "also", "try", "good", "look"}


_PRIVATE_START = re.compile(r"^\s*(private|gratitude)\s*:", re.I)


def _tokens(s: str) -> list[str]:
    return sorted(re.findall(r"[a-z0-9äöüß]+", (s or "").lower()))


def _lex_new(cur, text: str) -> set[str]:
    cur.execute("SELECT tsvector_to_array(to_tsvector('english', %s)) AS a", (text,))
    return {x for x in cur.fetchone()["a"] if not x.isdigit()} - _GENERIC


def _lex_existing(cur, exclude: list[int]) -> list[dict]:
    cur.execute(
        """SELECT id, summary, thought_type, stage, tags,
                  tsvector_to_array(to_tsvector('english', coalesce(summary,'') || ' ' || coalesce(array_to_string(tags,' '),''))) AS lex
           FROM thoughts WHERE stage <> 'dropped' AND id <> ALL(%s::bigint[])""",
        (exclude,),
    )
    rows = cur.fetchall()
    for r in rows:
        r["lex"] = {x for x in r["lex"] if not x.isdigit()} - _GENERIC
    return rows


def _match_project(cur, name: str, exclude_id: Optional[int] = None) -> Optional[dict]:
    want = _tokens(name)
    if not want:
        return None
    cur.execute("SELECT id, summary, aliases FROM thoughts WHERE thought_type='project' AND stage <> 'dropped' ORDER BY id")
    rows = [r for r in cur.fetchall() if r["id"] != exclude_id]
    names = lambda r: [r["summary"]] + list(r["aliases"] or [])  # noqa: E731
    for r in rows:  # 1. same words in any order (alias or summary)
        if any(_tokens(n) == want for n in names(r)):
            return r
    joined = " ".join(want)
    for r in rows:  # 2. small typos
        if any(difflib.SequenceMatcher(None, " ".join(_tokens(n)), joined).ratio() >= 0.88 for n in names(r)):
            return r
    if len(want) >= 2:  # 3. all words appear in the project's summary
        for r in rows:
            if set(want) <= set(_tokens(r["summary"])):
                return r
    return None


def _thought_row(cur, tid: int) -> Optional[dict]:
    cur.execute("SELECT id, summary, thought_type, stage FROM thoughts WHERE id = %s", (tid,))
    return cur.fetchone()


def _add_link(cur, from_id: int, to_id: int, link_type: str, added_by: str, notes: list[str]) -> Optional[int]:
    """Insert one link with the side effects the design calls for. Returns link id, None if it already existed."""
    if from_id == to_id:
        raise Reject(f"cannot link #{from_id} to itself")
    for tid in (from_id, to_id):
        if not _thought_row(cur, tid):
            raise Reject(f"thought #{tid} does not exist")
    if link_type == "related" and from_id > to_id:
        from_id, to_id = to_id, from_id
    cur.execute(
        """INSERT INTO thought_links (from_id, to_id, link_type, added_by, reviewed)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT (from_id, to_id, link_type) DO NOTHING RETURNING id""",
        (from_id, to_id, link_type, added_by, added_by == "user"),
    )
    row = cur.fetchone()
    if row is None:
        notes.append(f"link #{from_id} {link_type} #{to_id} already existed")
        return None
    if link_type == "completes":
        cur.execute("UPDATE thoughts SET stage='done' WHERE id=%s AND stage IN ('inbox','active','someday') RETURNING id", (to_id,))
        if cur.fetchone():
            notes.append(f"closed #{to_id} (stage done)")
    elif link_type == "duplicate_of":
        cur.execute("UPDATE thoughts SET stage='dropped' WHERE id=%s AND stage <> 'dropped' RETURNING id", (from_id,))
        if cur.fetchone():
            notes.append(f"#{from_id} dropped as duplicate of #{to_id}")
    return row["id"]


def _short(s: str, n: int = 70) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "..."


def _log_thought(cur, raw_id, message, th: Thought, now, out: list[str], notes: list[str], siblings: list[int]) -> int:
    if not th.summary.strip():
        raise Reject("a thought has an empty summary")
    w = parse_when(th.when, now) if th.when else None
    needs_review, reason = th.needs_review, th.review_reason
    if th.when and not w:
        needs_review = True
        reason = ((reason + "; ") if reason else "") + f"could not read time '{th.when}'"
        notes.append(f"could not read the time '{th.when}', so event time is empty and the thought is flagged")

    stage = th.stage or {"progress": "done", "task": "active", "project": "active"}.get(th.thought_type or "", "inbox")
    cur.execute(
        """INSERT INTO thoughts (raw_id, original_text, summary, thought_type, next_step, area, tags, stage, event_at,
                                 source, source_detail, needs_review, review_reason, confidence, extractor_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (raw_id, message, th.summary.strip(), th.thought_type, th.next_step, th.area, th.tags, stage,
         w[0] if w else None, th.source, th.source_detail, needs_review, reason, th.confidence, EXTRACTOR_VERSION),
    )
    tid = cur.fetchone()["id"]
    kind = (th.thought_type or "unsorted") + (f"/{th.next_step}" if th.next_step else "")
    line = f"+ #{tid} {kind} | {th.area or 'no area'} | {stage}: {_short(th.summary, 90)}"
    if w:
        line += f" (event {fmt(*w)}{', date only' if w[1] else ''})"
    out.append(line)
    if needs_review:
        out.append(f"  ! needs review: {reason or 'flagged by Claude'}")

    project_id = None
    if th.project and th.project.strip():
        proj = _match_project(cur, th.project, exclude_id=tid)
        if proj:
            project_id, pname = proj["id"], proj["summary"]
        else:
            cur.execute(
                """INSERT INTO thoughts (raw_id, original_text, summary, thought_type, area, aliases, stage, source,
                                         needs_review, review_reason, extractor_version)
                   VALUES (%s,%s,%s,'project','projects',%s,'active','chat',false,null,%s) RETURNING id""",
                (raw_id, message, th.project.strip(), [th.project.strip()], EXTRACTOR_VERSION),
            )
            project_id, pname = cur.fetchone()["id"], th.project.strip()
            out.append(f"+ created project #{project_id} '{pname}' (new name)")
        lid = _add_link(cur, tid, project_id, "part_of", "claude", notes)
        if lid:
            out.append(f"  part of #{project_id} ({_short(pname, 40)}) [link l{lid}, by Claude]")

    if th.thought_type == "project":
        dup = _match_project(cur, th.summary, exclude_id=tid)
        if dup:
            notes.append(f"project #{tid} looks like a duplicate of project #{dup['id']} ({_short(dup['summary'], 40)}); "
                         "ask the user whether to merge (duplicate_of link via change)")

    for lk in th.links:
        if lk.from_id not in (None, tid):
            raise Reject("links inside a thought must leave from the thought itself (omit from_id)")
        lid = _add_link(cur, tid, lk.to_id, lk.link_type, "user" if lk.by_user else "claude", notes)
        if lid:
            out.append(f"  {lk.link_type} #{lk.to_id} [link l{lid}, by {'user' if lk.by_user else 'Claude'}]")

    exclude = [tid] + siblings
    if th.thought_type == "progress":
        _close_task(cur, tid, th, project_id, out, notes)
    elif th.completes is not None:
        raise Reject("'completes' is only for thoughts of type progress")

    lex = _lex_new(cur, th.summary + " " + " ".join(th.tags))
    if len(lex) >= 1:
        linked = {project_id} if project_id else set()
        cur.execute("SELECT to_id, from_id FROM thought_links WHERE from_id=%s OR to_id=%s", (tid, tid))
        for r in cur.fetchall():
            linked.add(r["to_id"]); linked.add(r["from_id"])
        cands = []
        for r in _lex_existing(cur, exclude):
            if r["id"] in linked:
                continue
            shared = lex & r["lex"]
            same_tags = {x.lower() for x in th.tags} & {x.lower() for x in (r["tags"] or [])}
            score = len(shared) + len(same_tags)  # a shared tag counts as an extra point
            if score >= 2:
                cands.append((score, r["id"], r, sorted(shared)))
        cands.sort(key=lambda c: (-c[0], -c[1]))
        if cands:
            out.append(f"  link candidates for #{tid} (add with change only if they truly fit; type related unless clearly follow_up_of/part_of):")
            for _, cid, r, shared in cands[:5]:
                out.append(f"    #{cid} {r['thought_type'] or 'unsorted'}/{r['stage']}: {_short(r['summary'], 60)}  [shared: {', '.join(shared[:4])}]")
    return tid


def _close_task(cur, tid, th: Thought, project_id: Optional[int], out: list[str], notes: list[str]) -> None:
    if th.completes is not None:
        target = _thought_row(cur, th.completes)
        if not target:
            raise Reject(f"completes target #{th.completes} does not exist")
        lid = _add_link(cur, tid, th.completes, "completes", "claude", notes)
        if lid:
            out.append(f"  completes #{th.completes} ({_short(target['summary'], 50)}) [link l{lid}]")
        return
    lex = _lex_new(cur, th.summary)
    if not lex:
        return
    members = None
    if project_id:
        cur.execute("SELECT from_id FROM thought_links WHERE to_id=%s AND link_type='part_of'", (project_id,))
        members = {r["from_id"] for r in cur.fetchall()}
    scored = []
    for r in _lex_existing(cur, [tid]):
        if r["thought_type"] != "task" or r["stage"] not in ("inbox", "active") or not r["lex"]:
            continue
        if members is not None and r["id"] not in members:
            continue
        shared = lex & r["lex"]
        ratio = len(shared) / len(r["lex"])
        if len(shared) >= min(2, len(r["lex"])) and ratio >= 0.6:
            scored.append((ratio, len(shared), r["id"], r))
    scored.sort(key=lambda c: (-c[0], -c[1], c[2]))
    if len(scored) == 1 or (len(scored) > 1 and (scored[0][0], scored[0][1]) > (scored[1][0], scored[1][1])):
        r = scored[0][3]
        lid = _add_link(cur, tid, r["id"], "completes", "claude", notes)
        if lid:
            out.append(f"  completes #{r['id']} ({_short(r['summary'], 50)}) [link l{lid}, auto: task closed]")
    elif scored:
        out.append("  open tasks that might be finished by this (not closed; use change to close one):")
        for _, _, _, r in scored[:4]:
            out.append(f"    #{r['id']}: {_short(r['summary'], 60)}")
