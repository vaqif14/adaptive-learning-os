from __future__ import annotations

from dataclasses import dataclass, asdict, field
from .diagnosis import DiagnosisContext, diagnostic_level
from .progression import ProgressionContext, choose_next_scope
from .interpretation import InterpretationContext, question_interpretation_gate
from .cognitive_delegation import CognitiveWorkContract, DelegationContext, evaluate_delegation
from .challenge import ChallengeContext, choose_challenge_gate


@dataclass
class TeachingContext:
    mode: str
    route: str
    study_intent: str = "learn_concept"
    goal_known: bool = True
    prerequisite_schema_present: bool | None = None
    learner_stuck: bool = False
    user_requested_direct_answer: bool = False
    decision_sensitive_ambiguity: bool = False
    repeated_observable_error: bool = False
    root_cause_changes_intervention: bool = False
    candidate_causes: int = 0
    high_stakes: bool = False
    prior_support_heavy: bool = False
    independent_reproduction_available: bool = False
    strong_independent_reasoning_available: bool = False
    recent_full_solution_seen: bool = False
    learner_requested_challenge: bool = False
    near_transfer_available: bool = False
    far_transfer_available: bool = False
    delayed_independent_performance_available: bool = False
    task_ambiguity_signal: bool = False
    instruction_misread_signal: bool = False
    multiple_plausible_readings: bool = False
    learner_requested_clarification: bool = False
    compressed_content_delivered: bool = False

    # v0.5.3 cross-cutting semantics
    goal_mode: str = "learn"
    ai_role: str | None = None
    requested_ai_work: list[str] = field(default_factory=list)
    protected_cognition: list[str] = field(default_factory=list)
    delegable_work: list[str] = field(default_factory=list)
    independence_required: bool = True
    support_kind: str = "none"
    fast_track_requested: bool = False
    learner_claims_existing_competence: bool = False
    authentic_challenge_available: bool = False
    challenge_verifier_available: bool = False

    # v0.6.0 pedagogy: graded feedback + unproductive-struggle guard
    graded_attempt_available: bool = False
    attempts_without_progress: int = 0
    wheel_spin_threshold: int = 3
    confidence_rating: str | None = None


@dataclass
class TeachingDecision:
    route: str
    mode: str
    move: str
    reasons: list[str]
    diagnostic_level: int
    study_intent: str = "learn_concept"
    evidence_target: str | None = None
    skipped_scopes: list[str] = field(default_factory=list)
    mastery_evidence_eligible: bool = False
    follow_up_move: str | None = None

    def to_dict(self):
        return asdict(self)


def _progression(ctx: TeachingContext):
    return choose_next_scope(ProgressionContext(
        supported_completion=ctx.prior_support_heavy,
        independent_reproduction=ctx.independent_reproduction_available,
        near_transfer=ctx.near_transfer_available,
        far_transfer=ctx.far_transfer_available,
        delayed_independent_performance=ctx.delayed_independent_performance_available,
        strong_independent_reasoning=ctx.strong_independent_reasoning_available,
        learner_requested_challenge=ctx.learner_requested_challenge,
        recent_full_solution_seen=ctx.recent_full_solution_seen,
    ))


def _decision(ctx: TeachingContext, move: str, reasons: list[str], dlevel: int = 0, **kwargs) -> TeachingDecision:
    return TeachingDecision(
        route=ctx.route,
        mode=ctx.mode,
        move=move,
        reasons=reasons,
        diagnostic_level=dlevel,
        study_intent=ctx.study_intent,
        **kwargs,
    )


def choose_move(ctx: TeachingContext) -> TeachingDecision:
    # SAFETY FIRST: a high-stakes topic always verifies the AI's content before
    # anything else, including quick answers and the challenge gate. An unverified
    # direct answer on a high-stakes question is never acceptable.
    if ctx.high_stakes:
        return _decision(ctx, "verify_then_explain", ["safety_or_high_stakes_overrides_all_intents"], 0, mastery_evidence_eligible=False)

    # Learning mode protects the cognition the learner is trying to acquire.
    if ctx.ai_role:
        delegation = evaluate_delegation(DelegationContext(
            goal_mode=ctx.goal_mode,
            ai_role=ctx.ai_role,
            requested_work=ctx.requested_ai_work,
            support_kind=ctx.support_kind,
            contract=CognitiveWorkContract(
                target_capability="current_target",
                protected_cognition=ctx.protected_cognition,
                delegable_work=ctx.delegable_work,
                independence_required=ctx.independence_required,
            ),
        ))
        if not delegation.allowed:
            return _decision(
                ctx, "preserve_learner_cognition",
                [*delegation.reasons, *[f"protected:{x}" for x in delegation.protected_overlap]],
                0, mastery_evidence_eligible=False,
            )

    # Experienced learners may prove competence first instead of consuming redundant instruction.
    challenge = choose_challenge_gate(ChallengeContext(
        capability_id="current_target",
        fast_track_requested=ctx.fast_track_requested,
        learner_claims_existing_competence=ctx.learner_claims_existing_competence,
        authentic_task_available=ctx.authentic_challenge_available,
        deterministic_or_rubric_verifier_available=ctx.challenge_verifier_available,
        high_stakes=ctx.high_stakes,
    ))
    if challenge.use_challenge_gate:
        return _decision(
            ctx, "challenge_gate", challenge.reasons, 0,
            evidence_target="near_transfer", mastery_evidence_eligible=True,
        )

    interpretation = question_interpretation_gate(InterpretationContext(
        task_ambiguity_signal=ctx.task_ambiguity_signal,
        instruction_misread_signal=ctx.instruction_misread_signal,
        multiple_plausible_readings=ctx.multiple_plausible_readings,
        learner_requested_clarification=ctx.learner_requested_clarification,
    ))

    # Interpretation ambiguity is resolved before misconception diagnosis.
    if ctx.study_intent == "interpret_question" or interpretation.clarify_first:
        reasons = ["question_interpretation_precedes_conceptual_diagnosis", *interpretation.reasons]
        if ctx.study_intent == "interpret_question":
            reasons.append("explicit_interpret_question_intent")
        return _decision(ctx, "clarify_task_intent", reasons, 0, mastery_evidence_eligible=False)

    dlevel = diagnostic_level(DiagnosisContext(
        repeated_observable_error=ctx.repeated_observable_error,
        root_cause_changes_intervention=ctx.root_cause_changes_intervention,
        candidate_causes=ctx.candidate_causes,
    ))

    # Unproductive struggle (wheel-spinning, Beck & Gong 2013): after repeated
    # attempts with no progress, change approach rather than iterate the same move.
    if ctx.attempts_without_progress >= ctx.wheel_spin_threshold and ctx.wheel_spin_threshold > 0:
        return _decision(
            ctx, "change_approach",
            ["wheel_spinning_detected_change_representation_or_prerequisite"],
            dlevel, mastery_evidence_eligible=False,
        )

    # Graded feedback (Shute 2008): on a repeated error with a graded attempt and
    # no competing-cause ambiguity, confirm/correct then explain before re-attempt.
    if ctx.repeated_observable_error and ctx.graded_attempt_available and dlevel < 2:
        return _decision(
            ctx, "feedback",
            ["graded_feedback_confirm_correct_then_explain"],
            dlevel, mastery_evidence_eligible=False, follow_up_move="re_attempt",
        )

    if ctx.study_intent == "compress_source":
        if ctx.mode == "quick_answer" or ctx.user_requested_direct_answer:
            return _decision(
                ctx, "compress_source",
                ["compression_requested_as_output", "compression_is_not_mastery_evidence"],
                0,
                mastery_evidence_eligible=False,
            )
        if not ctx.compressed_content_delivered:
            return _decision(
                ctx, "compress_for_learning",
                ["compression_is_representation_not_mastery", "learning_mode_requires_generation_after_compression"],
                dlevel,
                mastery_evidence_eligible=False,
                follow_up_move="generative_reconstruction",
            )
        return _decision(
            ctx, "generative_reconstruction",
            ["reconstruct_after_compression_before_any_mastery_claim"],
            dlevel,
            evidence_target="independent_reproduction",
            mastery_evidence_eligible=True,
        )

    if ctx.study_intent == "build_plan":
        return _decision(ctx, "organize", ["goal_resource_deadline_aware_planning"], dlevel, mastery_evidence_eligible=False)

    if ctx.study_intent == "decompose_task":
        return _decision(ctx, "organize", ["decompose_task_without_claiming_mastery"], dlevel, mastery_evidence_eligible=False)

    if ctx.study_intent == "critique_artifact":
        return _decision(ctx, "critique", ["critique_artifact_then_learner_revision"], dlevel, mastery_evidence_eligible=False, follow_up_move="learner_revision")

    if ctx.study_intent == "simulate_performance":
        return _decision(
            ctx, "simulate",
            ["authentic_performance_simulation_intent", "use_scenario_simulator_contract_when_runtime_available"],
            dlevel,
            evidence_target="near_transfer",
            mastery_evidence_eligible=True,
        )

    if ctx.study_intent == "practice_retrieval":
        return _decision(
            ctx, "retrieve",
            ["explicit_retrieval_practice_intent"],
            dlevel,
            evidence_target="delayed_independent_performance" if ctx.mode == "review" else "independent_reproduction",
            mastery_evidence_eligible=True,
        )

    if ctx.study_intent == "deliver_artifact" or ctx.goal_mode == "deliver_artifact":
        return _decision(
            ctx, "deliver_with_governance",
            ["delivery_goal_does_not_imply_learning_or_mastery"],
            dlevel, mastery_evidence_eligible=False,
        )

    if ctx.mode == "quick_answer" or ctx.user_requested_direct_answer:
        return _decision(ctx, "answer_directly", ["quick_or_explicit_direct_answer_intent"], 0, mastery_evidence_eligible=False)

    if not ctx.goal_known:
        return _decision(ctx, "elicit", ["goal_missing_and_changes_learning_route"], dlevel, mastery_evidence_eligible=False)

    if ctx.prerequisite_schema_present is False:
        return _decision(ctx, "model", ["missing_prerequisite_schema_avoid_blind_generation"], dlevel, mastery_evidence_eligible=False)

    if dlevel == 2:
        return _decision(
            ctx, "probe",
            ["decision_relevant_competing_causes_require_discrimination"], 2,
            evidence_target="discriminating_observation",
            mastery_evidence_eligible=False,
        )

    if ctx.learner_stuck:
        return _decision(ctx, "scaffold", ["learner_stuck_use_minimum_effective_support"], dlevel, mastery_evidence_eligible=False)

    p = _progression(ctx)
    if ctx.strong_independent_reasoning_available and p.next_scope == "near_transfer":
        return _decision(
            ctx, "transfer", p.reasons, dlevel,
            evidence_target="near_transfer", skipped_scopes=p.skipped_scopes,
            mastery_evidence_eligible=True,
        )

    if ctx.prior_support_heavy and not ctx.independent_reproduction_available:
        return _decision(
            ctx, "fade", ["supported_success_needs_independent_reproduction"], dlevel,
            evidence_target="independent_reproduction",
            mastery_evidence_eligible=True,
        )

    if ctx.mode == "assess":
        return _decision(ctx, "check", ["explicit_assessment_mode"], dlevel, evidence_target=p.next_scope, mastery_evidence_eligible=True)
    if ctx.mode == "review":
        return _decision(ctx, "retrieve", ["explicit_review_mode"], dlevel, evidence_target="delayed_independent_performance", mastery_evidence_eligible=True)
    if ctx.mode == "practice":
        return _decision(ctx, "practice_task", ["explicit_practice_mode"], dlevel, evidence_target=p.next_scope, mastery_evidence_eligible=True)
    if ctx.mode == "build_with_help":
        return _decision(ctx, "scaffold", ["co-production_mode"], dlevel, mastery_evidence_eligible=False)

    return _decision(
        ctx, "elicit_or_attempt",
        ["default_learning_move_seek_meaningful_learner_production"], dlevel,
        evidence_target=p.next_scope,
        skipped_scopes=p.skipped_scopes,
        mastery_evidence_eligible=True,
    )
