from __future__ import annotations

# Canonical learning modes (after Nick Saraev's "learn with AI" personas).
# Each mode = a named stance with a "use when" trigger and a ready prompt, wired
# to the runtime's own mechanics so the mode is enforced, not just suggested.
# {subject}/{topic} are filled from the topic; [idea]/[summary]/[source] stay as
# placeholders the learner fills per use.

STAGE_PROMPTS: dict[str, dict] = {
    "intake": {
        "name": "The interviewer",
        "use_when": "You're starting out and unsure what you need.",
        "prompt": "Before you teach me anything, ask me questions about my goal and my level.\nOne at a time.",
        "maps_to": "intake -> Learning Contract (goal, level); one question at a time",
    },
    "roadmap": {
        "name": "The mapmaker",
        "use_when": "A subject feels shapeless.",
        "prompt": "Map out {subject} for me: the main parts, what depends on what, "
                  "and where people usually get stuck.",
        "maps_to": "roadmap -> nodes (main parts), requires (dependencies), pitfalls (stuck points)",
    },
    "socratic": {
        "name": "The Socratic questioner",
        "use_when": "You think you understand.",
        "prompt": "Don't give me the answer. Ask me questions until I find the gap myself.",
        "maps_to": "policy move 'probe' + protected cognition (AI must not reveal the answer)",
    },
    "checker": {
        "name": "The checker",
        "use_when": "You've made something: a summary, a proof, some code.",
        "prompt": "Here's my [summary]. Find what's wrong or missing. Don't rewrite it.",
        "maps_to": "study_intent 'critique_artifact' -> critique move, learner revises (no rewrite)",
    },
    "listener": {
        "name": "The listener",
        "use_when": "You want to test real understanding.",
        "prompt": "I'll explain [idea] in my own words. Grade it against [source] and tell me what I missed.",
        "maps_to": "generative explanation graded against a governed source -> explanation evidence",
    },
    "examiner": {
        "name": "The examiner",
        "use_when": "You've covered it once.",
        "prompt": "Quiz me on {topic}, one question at a time. Make each one harder when I get it right.",
        "maps_to": "practice_retrieval + progression (difficulty rises on success) -> retrieval evidence",
    },
    "sparring": {
        "name": "The sparring partner",
        "use_when": "You're practicing a skill: speaking, interviews, sales calls.",
        "prompt": "Play a tough hiring manager. Push back hard. Don't go easy on me.",
        "maps_to": "simulate_performance (adversarial role-play) + rising difficulty -> performance evidence",
    },
    "roleplay": {
        "name": "The role-play",
        "use_when": "You want a realistic interactive run of a real situation.",
        "prompt": "Play a tough interviewer for a [role] job. One question at a time. Score me at the end.",
        "maps_to": "scenario_simulator primitive; one turn at a time; simulation-fidelity caps on the score",
    },
    "realistic_task": {
        "name": "The realistic task",
        "use_when": "You want practice that's messy like the real job.",
        "prompt": "Give me a realistic [task] with messy details, like on the job. Then grade what I do.",
        "maps_to": "authentic task -> artifact/performance evidence, graded against a rubric",
    },
    "practice_app": {
        "name": "The practice app",
        "use_when": "You want a drill built for exactly your skill.",
        "prompt": "Build me a small practice app that drills [skill] with realistic examples and scores me.",
        "maps_to": "artifact_workspace primitive (the app is delegable; your practice ON it is the evidence)",
    },
    "leveler": {
        "name": "Find your level",
        "use_when": "You don't know where you stand.",
        "prompt": "Quiz me on {topic}, easy to hard. Stop when I start guessing, then tell me my level.",
        "maps_to": "adaptive anchor probe -> level estimate (diagnostic, NOT mastery evidence)",
    },
    "three_levels": {
        "name": "Explain at three levels",
        "use_when": "A concept won't click.",
        "prompt": "Explain [idea] three ways: for a child, for a beginner, and for an expert.",
        "maps_to": "multi-level explanation; learner picks the level that fits (scaffolding, not evidence)",
    },
    "resource_finder": {
        "name": "Find resources that fit",
        "use_when": "You need material at your exact level.",
        "prompt": "Find 3 resources for someone who knows [A] but not [B]. Say why each one fits, with links.",
        "maps_to": "source orchestration / curated acquisition; verify sources exist + cite them",
    },
    "explainer": {
        "name": "The Explainer",
        "use_when": "You've already tried solving it yourself and are truly stuck on a specific part. Last resort, never the default.",
        "prompt": "I tried [problem] and got stuck at [step]. Explain just that part, at my level.",
        "maps_to": "constrained explain + EXPLAINER LOCK-IN: after any explanation, re-solve from zero unaided before advancing (assisted work is never mastery)",
    },
    "diagnostician": {
        "name": "The Diagnostician",
        "use_when": "You keep making the same mistake across different problems or concepts.",
        "prompt": "Here are three problems I got wrong: [paste problems]. What misunderstanding do they have in common?",
        "maps_to": "`alearn diagnose-history` — cross-session persistent failures, co-failure clusters, misconception tally over the verified ledger",
    },
    "flashcards": {
        "name": "The Card Writer",
        "use_when": "You want long-term retention: details into spaced review. Flashcards for details, explaining for connections.",
        "prompt": "Turn these notes into flashcards. One idea per card, with a question on the front.\nHere's what I got wrong today. Write new cards that test the same ideas a different way.",
        "maps_to": "spaced review scheduler (`alearn review-due`: half-life intervals + 48h apply-or-lose); mistakes feed new cards",
    },
    "clerk": {
        "name": "The clerk",
        "use_when": "Pure logistics: notes, formats, schedules.",
        "prompt": "Turn my messy notes into a clean outline. Don't add anything I didn't write.",
        "maps_to": "DELEGABLE formatting only -> 'organize' move; the thinking stays yours, NOT mastery evidence",
    },
}

# Back-compat / human aliases.
_ALIASES = {
    "interviewer": "intake", "mapmaker": "roadmap", "questioner": "socratic",
    "socratic_questioner": "socratic",
}


def _resolve(stage: str) -> str:
    return _ALIASES.get(stage, stage)


def render_prompt(stage: str, subject: str | None = None) -> dict:
    key = _resolve(stage)
    if key not in STAGE_PROMPTS:
        raise KeyError(f"unknown_prompt_stage:{stage}")
    spec = STAGE_PROMPTS[key]
    fill = subject or "[subject]"
    text = spec["prompt"].replace("{subject}", fill).replace("{topic}", subject or "[topic]")
    return {"stage": key, "name": spec["name"], "use_when": spec["use_when"],
            "prompt": text, "maps_to": spec["maps_to"]}


def list_prompts() -> list[dict]:
    return [{"stage": k, "name": v["name"], "use_when": v["use_when"]} for k, v in STAGE_PROMPTS.items()]
