#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SKILL_DIR = HERE.parents[1]
sys.path.insert(0, str(SKILL_DIR))

from runtime.notebook_cli import list_notebooks, choose_notebook, bind_notebook, list_sources
from runtime.session import SessionKernel
from runtime.contracts import LearningContract, LearningResources, SourcePolicy
from runtime.router import RouteContext, route_message
from runtime.policy import TeachingContext, choose_move
from runtime.diagnosis import DiagnosisContext, diagnostic_level, validate_hypotheses
from runtime.progression import ProgressionContext, choose_next_scope
from runtime.verifiers import VerifierRegistry
from runtime.utils import new_id, read_json
from runtime.evidence_semantics import normalize_evidence_semantics
from runtime.review import ReviewContext, choose_review_item
from runtime.primitives import PrimitiveRegistry
from runtime.intents import INTENT_CATALOG
from runtime.cognitive_delegation import CognitiveWorkContract, DelegationContext, evaluate_delegation
from runtime.challenge import ChallengeContext, choose_challenge_gate
from runtime.coverage import CoverageCell, PracticeCoverage, choose_practice_variation
from runtime.curriculum import CurriculumContext, curriculum_authority_gate
from runtime.source_governance import SourceRecord, SourceRights, evaluate_source_use
from runtime.accessibility import support_effect_on_independence
from runtime.simulation import SimulationFidelity, cap_simulation_scope
from runtime.governance import ToolRequest, authorize_tool
from runtime.source_scope import SourceScopePolicy, CoverageClaim, analyze_source_gaps, source_boundary_gate, hybrid_source_plan, behavior_for
from runtime.safety import safe_input_path
from runtime.evidence_bridge import apply_evidence_to_context, progress_flags
from runtime.learner_model import aggregate_learner_state
from runtime.mastery import estimate as mastery_estimate, BKTParams, observations_from_projection_caps
from runtime.scheduling import due_queue
from runtime.execution import run_exercise, verify_fastapi_app, LANGUAGES, select_backend, ContainerPolicy
from runtime.intake import intake_state, next_question
from runtime.prompts import render_prompt, list_prompts, STAGE_PROMPTS
from runtime.workspace_check import check_module
from runtime.diagnostician import analyze as diagnose_history
from runtime.listener import grade_explanation
from runtime.server import ServerConfig, run_server
from runtime.knowledge_graph import load_graph
from runtime.graph_bridge import (
    plan_curriculum_from_graph, gap_report_from_graph, graph_as_source_record,
)


INTENT_CHOICES = sorted(INTENT_CATALOG)
MODE_CHOICES = ["quick_answer", "learn", "practice", "build_with_help", "review", "assess"]
SCOPE_CHOICES = ["supported_completion", "independent_reproduction", "near_transfer", "far_transfer", "delayed_independent_performance"]
FORMAT_CHOICES = [
    "recognition_mcq", "free_recall", "explanation", "step_execution", "artifact_execution",
    "artifact_performance", "scenario_performance", "oral_defense", "live_performance",
    "novel_unassisted_transfer", "compression_output", "plan_output", "ai_generated_output", "unknown",
]
GOAL_MODE_CHOICES = ["learn", "perform_with_assistance", "deliver_artifact", "quick_reference"]
AI_ROLE_CHOICES = ["inform", "model", "probe", "scaffold", "critique", "verify", "delegate", "replace"]


def workspace(args):
    return Path(args.workspace).resolve()


def kernel(args):
    return SessionKernel(workspace(args))


def printj(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _resources(args) -> LearningResources:
    return LearningResources(
        learner_supplied=args.resource or [],
        canonical=args.canonical_resource or [],
    )


def _source_policy(args) -> SourcePolicy:
    strategy = getattr(args, "source_strategy", None) or "adaptive_hybrid"
    b = behavior_for(strategy)
    return SourcePolicy(
        strategy=strategy,
        use_available_materials=True,
        discover_missing_materials=b["discover"],
        recommend_acquisition_for_unresolved=b["recommend"],
        ask_before_discovery=False,
        require_user_material_each_session=False,
    )


def cmd_start(args):
    contract = LearningContract(
        goal=args.goal,
        application_context=args.application_context,
        constraints=args.constraint or [],
        desired_independence=args.desired_independence,
        time_horizon=args.time_horizon,
        deadline=args.deadline,
        resources=_resources(args),
        source_policy=_source_policy(args),
        stated_background=args.stated_background,
        target_evidence_scope=args.target_evidence_scope,
        goal_mode=args.goal_mode or "learn",
        fast_track_requested=args.fast_track_requested,
        protected_cognition=args.protected_cognition or [],
        delegable_work=args.delegable_work or [],
        accessibility_needs=args.accessibility_need or [],
        authentic_environment_required=(True if args.authentic_environment_required else None),
    )
    roadmap = json.loads(safe_input_path(workspace(args), args.roadmap_file).read_text(encoding="utf-8")) if args.roadmap_file else None
    catalog = None
    selection = None
    if not args.without_notebooklm and contract.goal_mode == "learn" and args.mode != "quick_answer":
        catalog = list_notebooks(args.notebook_cli)
        if args.notebook_id:
            selection = choose_notebook(catalog, args.notebook_id)
    elif args.notebook_id:
        raise ValueError("Notebook selection requires a learning start with NotebookLM enabled.")
    session = kernel(args).start(args.topic, contract, args.mode, learner_id=getattr(args, 'learner_id', None), roadmap=roadmap)
    if selection:
        bind_notebook(kernel(args).dir(session["session_id"]), selection)
        session["notebooklm"] = selection
    elif catalog is not None:
        session["notebook_selection"] = {**catalog, "selection_required": catalog["status"] == "ready" and bool(catalog["notebooks"])}
    printj(session)


def cmd_notebooks(args):
    catalog = list_notebooks(args.notebook_cli)
    if args.json:
        printj(catalog)
    elif catalog["status"] != "ready":
        print(f"NotebookLM: {catalog['status']}. {catalog.get('action', '')}")
    elif not catalog["notebooks"]:
        print("Notebook siyahısı boşdur.")
    else:
        for index, notebook in enumerate(catalog["notebooks"], 1):
            print(f"{index:2}. {notebook['title'] or '(Adsız notebook)'}\n    {notebook['id']}")
        print("\nSeçim: alearn notebook-select --session <SESSION_ID> --notebook-id <ID>")
    if catalog["status"] != "ready":
        raise SystemExit(1)


def cmd_notebook_select(args):
    session_dir = kernel(args).dir(args.session)
    selection = choose_notebook(list_notebooks(args.notebook_cli), args.notebook_id)
    bind_notebook(session_dir, selection)
    printj(selection)


def cmd_notebook_sources(args):
    selection = read_json(kernel(args).dir(args.session) / "notebooklm.json")
    result = list_sources(selection)
    printj(result)
    if result["status"] != "ready":
        raise SystemExit(1)


def cmd_contract(args):
    resources = None
    if args.resource is not None or args.canonical_resource is not None:
        resources = LearningResources(
            learner_supplied=args.resource or [],
            canonical=args.canonical_resource or [],
        ).to_dict()
    source_policy = None
    if getattr(args, "source_strategy", None) is not None:
        source_policy = _source_policy(args).to_dict()
    updates = {
        "goal": args.goal,
        "application_context": args.application_context,
        "constraints": args.constraint if args.constraint is not None else None,
        "desired_independence": args.desired_independence,
        "time_horizon": args.time_horizon,
        "deadline": args.deadline,
        "resources": resources,
        "source_policy": source_policy,
        "stated_background": args.stated_background,
        "target_evidence_scope": args.target_evidence_scope,
        "goal_mode": args.goal_mode,
        "fast_track_requested": (True if args.fast_track_requested else None),
        "protected_cognition": args.protected_cognition if args.protected_cognition is not None else None,
        "delegable_work": args.delegable_work if args.delegable_work is not None else None,
        "accessibility_needs": args.accessibility_need if args.accessibility_need is not None else None,
        "authentic_environment_required": (True if args.authentic_environment_required else None),
    }
    printj(kernel(args).update_contract(args.session, **updates))


def cmd_route(args):
    ctx = RouteContext(
        mode=args.mode,
        study_intent=args.intent,
        conceptual_error=args.conceptual_error,
        repeated_conceptual_failures=args.repeated_conceptual_failures,
        decision_sensitive_ambiguity=args.decision_sensitive_ambiguity,
        ordinary_intervention_failed=args.ordinary_intervention_failed,
        representation_insufficient=args.representation_insufficient,
        high_stakes=args.high_stakes,
    )
    dec = route_message(args.message, ctx)
    led = kernel(args).ledger(args.session)
    led.append("event", {"event_type": "user_message", "message": args.message}, {"source": "cli"})
    led.append("decision", {
        "decision_type": "route", "mode": dec.mode, "route": dec.route,
        "study_intent": dec.study_intent, "reasons": dec.reasons,
    }, {"source": "router"})
    printj(dec.__dict__)


def cmd_evidence(args):
    if args.hints_count < 0:
        raise SystemExit("--hints-count must be >= 0")
    if args.response_latency_ms is not None and args.response_latency_ms < 0:
        raise SystemExit("--response-latency-ms must be >= 0")
    support = {
        "conceptual_scaffold": args.conceptual_scaffold,
        "worked_example_shown": args.worked_example_shown,
        "ai_direct_answer_revealed": args.ai_direct_answer_revealed,
        "hints_count": args.hints_count,
    }
    context_signals = {}
    if args.response_latency_ms is not None:
        context_signals = {
            "response_latency_ms": args.response_latency_ms,
            "signal_weight": "weak",
            "authority": "context_only",
        }
    semantics = normalize_evidence_semantics(args.evidence_format, args.strength, args.independence)
    payload = {
        "evidence_id": new_id("ev"),
        "capability_id": args.capability,
        "outcome": args.outcome,
        "independence": args.independence,
        "strength": semantics.normalized_strength,
        "scope": args.scope,
        "evidence_format": semantics.evidence_format,
        "mastery_eligible": semantics.mastery_eligible,
        "format_semantics": {"requested_strength": semantics.requested_strength, "reasons": semantics.reasons},
        "support_provenance": support,
        "support_kind": args.support_kind,
        "context_signals": context_signals,
        "confidence_rating": getattr(args, "confidence", None),
        "note": args.note,
        "source_event_ids": [],
    }
    led = kernel(args).ledger(args.session)
    rec = led.append("evidence", payload, {"source": "cli_evidence_entry"})
    proj = kernel(args).rebuild_projection(args.session)
    printj({"record": rec, "projection": proj})


def cmd_policy(args):
    route = args.route
    inferred_intent = args.intent
    if not route:
        rd = route_message(args.message or "learning interaction", RouteContext(
            mode=args.mode,
            study_intent=args.intent,
            conceptual_error=args.repeated_observable_error,
            repeated_conceptual_failures=args.repeated_conceptual_failures,
            decision_sensitive_ambiguity=args.decision_sensitive_ambiguity,
            ordinary_intervention_failed=args.ordinary_intervention_failed,
            representation_insufficient=args.representation_insufficient,
            high_stakes=args.high_stakes,
        ))
        route = rd.route
        inferred_intent = inferred_intent or rd.study_intent
    ctx = TeachingContext(
        mode=args.mode or "learn",
        route=route,
        study_intent=inferred_intent or "learn_concept",
        goal_known=not args.goal_unknown,
        prerequisite_schema_present=(True if args.prerequisite_schema_present else False if args.prerequisite_schema_missing else None),
        learner_stuck=args.learner_stuck,
        user_requested_direct_answer=args.user_requested_direct_answer,
        decision_sensitive_ambiguity=args.decision_sensitive_ambiguity,
        repeated_observable_error=args.repeated_observable_error,
        root_cause_changes_intervention=args.root_cause_changes_intervention,
        candidate_causes=args.candidate_causes,
        high_stakes=args.high_stakes,
        prior_support_heavy=args.prior_support_heavy,
        independent_reproduction_available=args.independent_reproduction_available,
        strong_independent_reasoning_available=args.strong_independent_reasoning,
        recent_full_solution_seen=args.recent_full_solution_seen,
        learner_requested_challenge=args.learner_requested_challenge,
        near_transfer_available=args.near_transfer_available,
        far_transfer_available=args.far_transfer_available,
        delayed_independent_performance_available=args.delayed_independent_performance_available,
        task_ambiguity_signal=args.task_ambiguity_signal,
        instruction_misread_signal=args.instruction_misread_signal,
        multiple_plausible_readings=args.multiple_plausible_readings,
        learner_requested_clarification=args.learner_requested_clarification,
        compressed_content_delivered=args.compressed_content_delivered,
        goal_mode=args.goal_mode,
        ai_role=args.ai_role,
        requested_ai_work=args.requested_ai_work or [],
        protected_cognition=args.protected_cognition or [],
        delegable_work=args.delegable_work or [],
        independence_required=not args.independence_not_required,
        support_kind=args.support_kind,
        fast_track_requested=args.fast_track_requested,
        learner_claims_existing_competence=args.learner_claims_existing_competence,
        authentic_challenge_available=args.authentic_challenge_available,
        challenge_verifier_available=args.challenge_verifier_available,
        graded_attempt_available=getattr(args, 'graded_attempt', False),
        attempts_without_progress=getattr(args, 'attempts_without_progress', 0),
        confidence_rating=getattr(args, 'confidence', None),
    )
    evidence_flags = None
    if getattr(args, "from_session", False) and getattr(args, "capability", None):
        proj = kernel(args).rebuild_projection(args.session)
        ctx, evidence_flags = apply_evidence_to_context(ctx, proj, args.capability)
    dec = choose_move(ctx)
    led = kernel(args).ledger(args.session)
    led.append("decision", {
        "decision_type": "teaching_move",
        "inputs": {"mode": ctx.mode, "route": ctx.route, "study_intent": ctx.study_intent,
                   "high_stakes": ctx.high_stakes, "evidence_derived": evidence_flags},
        **dec.to_dict(),
    }, {"source": "policy_engine"})
    printj(dec.to_dict())


def cmd_diagnose(args):
    level = diagnostic_level(DiagnosisContext(
        repeated_observable_error=args.repeated_observable_error,
        root_cause_changes_intervention=args.root_cause_changes_intervention,
        candidate_causes=args.candidate_causes,
    ))
    result = {"diagnostic_level": level}
    if args.hypotheses:
        hypotheses = json.loads(safe_input_path(workspace(args), args.hypotheses).read_text(encoding="utf-8"))
        if not isinstance(hypotheses, list):
            raise SystemExit("hypotheses file must contain a JSON array")
        errors = validate_hypotheses(hypotheses)
        result["validation_errors"] = errors
        if level == 2 and not errors:
            led = kernel(args).ledger(args.session)
            for h in hypotheses:
                led.append("hypothesis", h, {"source": "llm_proposal_validated_by_runtime"})
            kernel(args).rebuild_projection(args.session)
            result["accepted"] = len(hypotheses)
        else:
            result["accepted"] = 0
    printj(result)


def cmd_progression(args):
    dec = choose_next_scope(ProgressionContext(
        supported_completion=args.supported_completion,
        independent_reproduction=args.independent_reproduction,
        near_transfer=args.near_transfer,
        far_transfer=args.far_transfer,
        delayed_independent_performance=args.delayed_independent_performance,
        strong_independent_reasoning=args.strong_independent_reasoning,
        learner_requested_challenge=args.learner_requested_challenge,
        recent_full_solution_seen=args.recent_full_solution_seen,
    ))
    printj(dec.to_dict())


def cmd_review_item(args):
    printj(choose_review_item(ReviewContext(
        capability_id=args.capability,
        capability_kind=args.kind,
        known_failure_mode=args.known_failure_mode,
        require_transfer=args.require_transfer,
    )).to_dict())


def cmd_primitive(args):
    p = PrimitiveRegistry().get(args.primitive)
    printj({
        "primitive_id": p.primitive_id,
        "version": p.version,
        "renderer_available": p.renderer_available,
        "lifecycle_status": p.lifecycle_status,
        "supported_events": p.supported_events,
        "parameters": p.parameters,
    })


def cmd_intents(args):
    printj({k: v.__dict__ for k, v in INTENT_CATALOG.items()})


def cmd_verify_python(args):
    d = kernel(args).dir(args.session)
    domain = read_json(d / "domain-context.json")
    code = args.code
    if args.code_file:
        code = safe_input_path(workspace(args), args.code_file).read_text(encoding="utf-8")
    if code is None:
        raise SystemExit("provide --code or --code-file")
    trusted = safe_input_path(workspace(args), args.trusted_test_file).read_text(encoding="utf-8") if args.trusted_test_file else None
    result = VerifierRegistry.verify_python(
        domain,
        code,
        expected_stdout=args.expected_stdout,
        trusted_test_code=trusted,
    )
    if result.get("available"):
        vr = result["result"]
        executed = bool(vr.get("executed"))
        checked = bool(vr.get("correctness_checked"))
        # A run can only be STRONG evidence if the submission actually executed AND
        # its correctness was independently checked (trusted test or expected stdout)
        # AND it passed. Anything else is weak/unknown and never mastery-eligible:
        # this is what stops `raise SystemExit(0)` or an unchecked print from being
        # recorded as a strong unassisted success.
        if not executed:
            outcome = "unknown"          # refused/syntax-error/crash: not a performance signal
            requested = "weak"
        elif not checked:
            outcome = "unknown"          # ran but nothing verified correctness
            requested = "weak"
        else:
            outcome = "correct" if vr.get("passed") else "incorrect"
            requested = "strong" if vr.get("passed") else "medium"
        semantics = normalize_evidence_semantics(args.evidence_format, requested, args.independence)
        mastery_eligible = semantics.mastery_eligible and outcome in {"correct", "incorrect"} and checked
        payload = {
            "evidence_id": new_id("ev"),
            "capability_id": args.capability,
            "outcome": outcome,
            "independence": args.independence,
            "strength": semantics.normalized_strength,
            "scope": args.scope,
            "evidence_format": semantics.evidence_format,
            "mastery_eligible": mastery_eligible,
            "correctness_checked": checked,
            "format_semantics": {"requested_strength": semantics.requested_strength, "reasons": semantics.reasons},
            "support_provenance": {},
            "note": "deterministic_python_verifier",
            "source_event_ids": [],
            "verifier": {"id": result.get("verifier_id"), "result": vr},
        }
        # Only executed, correctness-checked runs are recorded as mastery evidence;
        # unverifiable runs are logged as observations so the audit trail is complete
        # without polluting the capability's evidence.
        led = kernel(args).ledger(args.session)
        if outcome == "unknown":
            led.append("observation", {"capability_id": args.capability, "verifier": payload["verifier"],
                                       "note": "verifier_ran_without_correctness_check" if executed else "verifier_refused_or_failed_to_execute"},
                       {"source": "python_verifier"})
        else:
            led.append("evidence", payload, {"source": "python_verifier"})
        kernel(args).rebuild_projection(args.session)
    printj(result)


def cmd_delegation(args):
    contract = CognitiveWorkContract(
        target_capability=args.capability,
        protected_cognition=args.protected_cognition or [],
        delegable_work=args.delegable_work or [],
        independence_required=not args.independence_not_required,
    )
    printj(evaluate_delegation(DelegationContext(
        goal_mode=args.goal_mode,
        ai_role=args.ai_role,
        requested_work=args.requested_work or [],
        support_kind=args.support_kind,
        contract=contract,
    )).to_dict())


def cmd_challenge(args):
    printj(choose_challenge_gate(ChallengeContext(
        capability_id=args.capability,
        fast_track_requested=args.fast_track_requested,
        learner_claims_existing_competence=args.claims_competence,
        authentic_task_available=args.authentic_task_available,
        deterministic_or_rubric_verifier_available=args.verifier_available,
        high_stakes=args.high_stakes,
    )).to_dict())


def cmd_coverage(args):
    cells = []
    for raw in args.cell or []:
        parts = raw.split(":")
        if len(parts) != 4:
            raise SystemExit("--cell must be dimension:value:attempts:independent_successes")
        attempts, successes = int(parts[2]), int(parts[3])
        if attempts < 0 or successes < 0:
            raise SystemExit("--cell attempts and independent_successes must be >= 0")
        if successes > attempts:
            raise SystemExit("--cell independent_successes cannot exceed attempts")
        cells.append(CoverageCell(parts[0], parts[1], attempts, successes))
    printj(choose_practice_variation(PracticeCoverage(args.capability, cells)))


def cmd_curriculum(args):
    printj(curriculum_authority_gate(CurriculumContext(
        authoritative_curriculum_available=args.authoritative_curriculum_available,
        authoritative_source_ids=args.source_id or [],
        dependency_graph_available=args.dependency_graph_available,
        domain_is_volatile=args.domain_is_volatile,
    )).to_dict())


def cmd_source_use(args):
    if args.permit and args.deny:
        raise SystemExit("--permit and --deny are mutually exclusive")
    rights = SourceRights(
        jurisdiction=args.jurisdiction,
        access_status=args.access_status,
        license=args.license,
        rights_status=args.rights_status,
        permitted_operations={args.operation: (True if args.permit else False if args.deny else None)},
        requires_human_review=args.human_review,
    )
    source = SourceRecord(
        source_id=args.source_id, uri=args.uri, source_type=args.source_type,
        derived_from=args.derived_from or [], rights=rights,
    )
    printj(evaluate_source_use(source, args.operation).to_dict())


def cmd_access_support(args):
    printj(support_effect_on_independence(args.support_kind))


def cmd_simulation_fidelity(args):
    printj(cap_simulation_scope(args.requested_scope, SimulationFidelity(
        cognitive=args.cognitive, social=args.social, physical=args.physical, temporal=args.temporal,
        authentic_environment=args.authentic_environment,
    )).to_dict())


def cmd_tool_gate(args):
    printj(authorize_tool(ToolRequest(
        tool_id=args.tool_id, operation=args.operation, high_risk=args.high_risk,
        side_effecting=args.side_effecting, authorized=args.authorized, resource_cost_class=args.resource_cost_class,
    )).to_dict())


def cmd_sources_plan(args):
    k = kernel(args)
    d = k.dir(args.session)
    contract = LearningContract.from_dict(read_json(d / "learning-contract.json"))
    policy = SourceScopePolicy.from_strategy(
        contract.source_policy.strategy,
        out_of_scope_policy=getattr(args, "out_of_scope_policy", None) or "mark_unknown",
    )
    supplied = contract.resources.learner_supplied + contract.resources.canonical
    gate = source_boundary_gate(policy, mode=read_json(d / "session.json").get("interaction_mode"), has_supplied_resources=bool(supplied))
    covered = set(args.covered_capability or [])
    partial = set(args.partial_capability or [])
    claims = []
    for cap in args.required_capability or []:
        status = "covered" if cap in covered else "partial" if cap in partial else "missing"
        claims.append(CoverageClaim(capability_id=cap, status=status))
    report = analyze_source_gaps(args.required_capability or [], claims, policy)
    discovered = None
    if args.discovery_results:
        discovered = json.loads(safe_input_path(workspace(args), args.discovery_results).read_text(encoding="utf-8"))
        if not isinstance(discovered, list):
            raise SystemExit("--discovery-results must contain a JSON array")
    plan = hybrid_source_plan(report, read_json(d / "session.json")["topic"], available_source_count=len(supplied), discovery_results=discovered, strategy=policy.strategy)
    printj({"source_boundary": gate.to_dict(), "gap_report": report.to_dict(), "plan": plan})


def cmd_sources_register(args):
    k = kernel(args)
    d = k.dir(args.session)
    contract = LearningContract.from_dict(read_json(d / "learning-contract.json"))
    current = list(contract.resources.learner_supplied)
    for x in args.resource or []:
        if x not in current:
            current.append(x)
    canonical = list(contract.resources.canonical)
    for x in args.canonical_resource or []:
        if x not in canonical:
            canonical.append(x)
    resources = LearningResources(learner_supplied=current, canonical=canonical).to_dict()
    session = k.update_contract(args.session, resources=resources)
    printj({"status":"registered", "resources": resources, "session": session})


def _load_graph_or_exit(path):
    gp = Path(path).expanduser()
    if not gp.exists():
        raise SystemExit(f"graph not found: {gp} (build one with: graphify extract <corpus> ; see graphify-out/graph.json)")
    try:
        return load_graph(gp), gp
    except ValueError as e:
        raise SystemExit(f"invalid graphify graph.json: {e}")


def cmd_graph_curriculum(args):
    graph, _ = _load_graph_or_exit(args.graph)
    plan = plan_curriculum_from_graph(graph, args.target, domain_is_volatile=args.domain_is_volatile)
    printj(plan.to_dict())


def cmd_graph_coverage(args):
    graph, _ = _load_graph_or_exit(args.graph)
    report = gap_report_from_graph(graph, args.required_capability or [])
    printj({"gap_report": report.to_dict(), "graph_provenance": graph.provenance()})


def cmd_graph_anchors(args):
    graph, gp = _load_graph_or_exit(args.graph)
    printj({
        "anchor_candidates": [
            {"label": n.label, "source_file": n.source_file, "community": n.community}
            for n in graph.central_nodes(args.k)
        ],
        "communities": graph.communities(),
        "graph_source": graph_as_source_record(graph, gp).to_dict(),
    })


def cmd_workspace_check(args):
    d = kernel(args).dir(args.session)
    result = check_module(d, args.module, backend=select_backend(args.prefer))
    ev = result.pop("evidence", None)
    led = kernel(args).ledger(args.session)
    if ev:
        ev["evidence_id"] = new_id("ev")
        led.append("evidence", ev, {"source": "workspace_check", "module": args.module})
        kernel(args).rebuild_projection(args.session)
    elif result.get("status") == "checked":
        led.append("observation", {"capability_id": args.module, "note": "workspace_check_ran_without_correctness_check"}, {"source": "workspace_check"})
    printj(result)


def cmd_prompt(args):
    printj(render_prompt(args.stage, args.subject))


def cmd_modes(args):
    printj({"modes": list_prompts()})


def cmd_intake(args):
    d = kernel(args).dir(args.session)
    contract = read_json(d / "learning-contract.json")
    printj(intake_state(contract))


def cmd_languages(args):
    printj({"languages": [LANGUAGES[k].to_dict() for k in sorted(LANGUAGES)],
            "note": "any language runs via local toolchain (dev) or a per-run container (production); unavailable ones say how to enable, never 'unsupported'"})


def cmd_run_exercise(args):
    code = args.code
    if args.code_file:
        code = safe_input_path(workspace(args), args.code_file).read_text(encoding="utf-8")
    if code is None:
        raise SystemExit("provide --code or --code-file")
    stdin = None
    if args.stdin_file:
        stdin = safe_input_path(workspace(args), args.stdin_file).read_text(encoding="utf-8")
    printj(run_exercise(args.lang, code, stdin=stdin, expect_stdout=args.expect_stdout,
                        backend=select_backend(args.prefer)))


def cmd_verify_fastapi(args):
    code = safe_input_path(workspace(args), args.code_file).read_text(encoding="utf-8")
    checks = json.loads(safe_input_path(workspace(args), args.checks_file).read_text(encoding="utf-8"))
    if not isinstance(checks, list):
        raise SystemExit("--checks-file must be a JSON array")
    printj(verify_fastapi_app(code, checks, backend=select_backend(args.prefer)))


def cmd_mastery(args):
    records = kernel(args).ledger(args.session).verified_records()
    obs = observations_from_projection_caps(records, args.capability)
    est = mastery_estimate(args.capability, obs, BKTParams())
    printj(est.to_dict())


def cmd_review_due(args):
    records = kernel(args).ledger(args.session).verified_records()
    caps = {}
    for r in records:
        if r.get("record_type") != "evidence":
            continue
        cap = r["payload"].get("capability_id")
        if not cap:
            continue
        caps.setdefault(cap, [])
    per_cap = {cap: observations_from_projection_caps(records, cap) for cap in caps}
    printj({"review_queue": due_queue(per_cap)})


def cmd_serve(args):
    ui = Path(args.ui).resolve() if args.ui else (Path(__file__).resolve().parents[3] / "decks" / "academy.html")
    cfg = ServerConfig(host=args.host, port=args.port, token=args.token,
                       allow_anon=args.allow_anon, ui_file=ui if ui.is_file() else None,
                       prefer_backend=args.prefer)
    run_server(cfg)


def cmd_listener_check(args):
    expl = safe_input_path(workspace(args), args.explanation_file).read_text(encoding="utf-8")
    src = safe_input_path(workspace(args), args.source_file).read_text(encoding="utf-8")
    g = grade_explanation(expl, src, threshold=args.threshold)
    if g["status"] == "graded" and g["outcome"] in {"correct","incorrect","partial"}:
        requested = "strong" if g["outcome"] == "correct" else "medium"
        sem = normalize_evidence_semantics("explanation", requested, args.independence)
        payload = {"evidence_id": new_id("ev"), "capability_id": args.capability, "outcome": g["outcome"],
                   "independence": args.independence, "strength": sem.normalized_strength, "scope": args.scope,
                   "evidence_format": sem.evidence_format, "mastery_eligible": sem.mastery_eligible,
                   "correctness_checked": True, "support_provenance": {}, "note": "listener_source_coverage",
                   "coverage": g["coverage"], "source_event_ids": []}
        led = kernel(args).ledger(args.session)
        led.append("evidence", payload, {"source": "listener"})
        kernel(args).rebuild_projection(args.session)
    printj(g)


def cmd_diagnose_history(args):
    sessions_dir = workspace(args) / ".learning" / "sessions"
    printj(diagnose_history(sessions_dir, learner_id=args.learner_id))


def cmd_learner_state(args):
    sessions_dir = workspace(args) / ".learning" / "sessions"
    printj(aggregate_learner_state(sessions_dir, learner_id=args.learner_id))


def cmd_project(args):
    printj(kernel(args).rebuild_projection(args.session))


def cmd_inspect(args):
    printj(kernel(args).inspect(args.session))


def _add_contract_args(s):
    s.add_argument("--goal")
    s.add_argument("--application-context")
    s.add_argument("--constraint", action="append")
    s.add_argument("--desired-independence")
    s.add_argument("--time-horizon")
    s.add_argument("--deadline")
    s.add_argument("--resource", action="append", help="learner-supplied resource path/URL")
    s.add_argument("--canonical-resource", action="append", help="canonical/authoritative resource path/URL")
    s.add_argument("--source-strategy", choices=["adaptive_hybrid","strict_closed_world","auto_discovery","curated_acquisition"], help="default is adaptive_hybrid: use what you have, discover missing coverage, recommend acquisition only if needed")
    s.add_argument("--stated-background", help="self-reported background; never treated as competence evidence")
    s.add_argument("--target-evidence-scope", choices=SCOPE_CHOICES)
    s.add_argument("--goal-mode", choices=GOAL_MODE_CHOICES)
    s.add_argument("--fast-track-requested", action="store_true")
    s.add_argument("--protected-cognition", action="append")
    s.add_argument("--delegable-work", action="append")
    s.add_argument("--accessibility-need", action="append")
    s.add_argument("--authentic-environment-required", action="store_true")


def parser():
    p = argparse.ArgumentParser(prog="alearn", description="Adaptive Learning OS local runtime")
    p.add_argument("--version", action="version", version="adaptive-learning-os 0.6.0")
    p.add_argument("--workspace", default=".")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("start")
    s.add_argument("--topic", required=True)
    _add_contract_args(s)
    s.add_argument("--mode", choices=MODE_CHOICES)
    s.add_argument("--learner-id", help="optional stable learner id for cross-session aggregation")
    s.add_argument("--roadmap-file", help="source-grounded roadmap JSON; creates module folders and offline HTML")
    s.add_argument("--notebook-cli", choices=["auto", "notebooklm", "nlm"], default="auto")
    s.add_argument("--notebook-id", help="full ID from alearn notebooks; binds only this session")
    s.add_argument("--without-notebooklm", action="store_true", help="explicit offline start without notebook discovery")
    s.set_defaults(func=cmd_start)

    n = sp.add_parser("notebooks", help="list NotebookLM notebooks for selection")
    n.add_argument("--notebook-cli", choices=["auto", "notebooklm", "nlm"], default="auto")
    n.add_argument("--json", action="store_true")
    n.set_defaults(func=cmd_notebooks)

    n = sp.add_parser("notebook-select", help="bind a listed notebook to a learning session")
    n.add_argument("--session", required=True)
    n.add_argument("--notebook-id", required=True)
    n.add_argument("--notebook-cli", choices=["auto", "notebooklm", "nlm"], default="auto")
    n.set_defaults(func=cmd_notebook_select)

    n = sp.add_parser("notebook-sources", help="list sources in the session's selected notebook")
    n.add_argument("--session", required=True)
    n.set_defaults(func=cmd_notebook_sources)

    c = sp.add_parser("contract")
    c.add_argument("--session", required=True)
    _add_contract_args(c)
    c.set_defaults(func=cmd_contract)

    r = sp.add_parser("route")
    r.add_argument("--session", required=True)
    r.add_argument("--message", required=True)
    r.add_argument("--mode", choices=MODE_CHOICES)
    r.add_argument("--intent", choices=INTENT_CHOICES)
    r.add_argument("--conceptual-error", action="store_true")
    r.add_argument("--repeated-conceptual-failures", type=int, default=0)
    r.add_argument("--decision-sensitive-ambiguity", action="store_true")
    r.add_argument("--ordinary-intervention-failed", action="store_true")
    r.add_argument("--representation-insufficient", action="store_true")
    r.add_argument("--high-stakes", action="store_true")
    r.set_defaults(func=cmd_route)

    e = sp.add_parser("evidence")
    e.add_argument("--session", required=True)
    e.add_argument("--capability", required=True)
    e.add_argument("--outcome", choices=["correct", "incorrect", "partial", "unknown"], required=True)
    e.add_argument("--independence", choices=["unassisted", "assisted", "unknown"], required=True)
    e.add_argument("--strength", choices=["strong", "medium", "weak"], required=True)
    e.add_argument("--evidence-format", choices=FORMAT_CHOICES, default="unknown")
    e.add_argument("--scope", choices=SCOPE_CHOICES, default="supported_completion")
    e.add_argument("--note")
    e.add_argument("--conceptual-scaffold", action="store_true")
    e.add_argument("--worked-example-shown", action="store_true")
    e.add_argument("--ai-direct-answer-revealed", action="store_true")
    e.add_argument("--hints-count", type=int, default=0)
    e.add_argument("--support-kind", choices=["none", "accessibility", "pedagogical", "cognitive_delegation"], default="none")
    e.add_argument("--response-latency-ms", type=int)
    e.add_argument("--confidence", choices=["low", "medium", "high"], help="learner confidence (for calibration / hypercorrection)")
    e.set_defaults(func=cmd_evidence)

    pol = sp.add_parser("policy")
    pol.add_argument("--session", required=True)
    pol.add_argument("--mode", choices=MODE_CHOICES)  # no default: let the router infer
    pol.add_argument("--intent", choices=INTENT_CHOICES)
    pol.add_argument("--route", choices=["direct", "teach", "deep"])
    pol.add_argument("--message")
    pol.add_argument("--goal-unknown", action="store_true")
    pol.add_argument("--prerequisite-schema-present", action="store_true")
    pol.add_argument("--prerequisite-schema-missing", action="store_true")
    pol.add_argument("--learner-stuck", action="store_true")
    pol.add_argument("--user-requested-direct-answer", action="store_true")
    pol.add_argument("--decision-sensitive-ambiguity", action="store_true")
    pol.add_argument("--repeated-observable-error", action="store_true")
    pol.add_argument("--repeated-conceptual-failures", type=int, default=0)
    pol.add_argument("--ordinary-intervention-failed", action="store_true")
    pol.add_argument("--representation-insufficient", action="store_true")
    pol.add_argument("--root-cause-changes-intervention", action="store_true")
    pol.add_argument("--candidate-causes", type=int, default=0)
    pol.add_argument("--high-stakes", action="store_true")
    pol.add_argument("--prior-support-heavy", action="store_true")
    pol.add_argument("--independent-reproduction-available", action="store_true")
    pol.add_argument("--strong-independent-reasoning", action="store_true")
    pol.add_argument("--recent-full-solution-seen", action="store_true")
    pol.add_argument("--learner-requested-challenge", action="store_true")
    pol.add_argument("--near-transfer-available", action="store_true")
    pol.add_argument("--far-transfer-available", action="store_true")
    pol.add_argument("--delayed-independent-performance-available", action="store_true")
    pol.add_argument("--task-ambiguity-signal", action="store_true")
    pol.add_argument("--instruction-misread-signal", action="store_true")
    pol.add_argument("--multiple-plausible-readings", action="store_true")
    pol.add_argument("--learner-requested-clarification", action="store_true")
    pol.add_argument("--compressed-content-delivered", action="store_true")
    pol.add_argument("--goal-mode", choices=GOAL_MODE_CHOICES, default="learn")
    pol.add_argument("--ai-role", choices=AI_ROLE_CHOICES)
    pol.add_argument("--requested-ai-work", action="append")
    pol.add_argument("--protected-cognition", action="append")
    pol.add_argument("--delegable-work", action="append")
    pol.add_argument("--independence-not-required", action="store_true")
    pol.add_argument("--support-kind", choices=["none", "accessibility", "pedagogical", "cognitive_delegation"], default="none")
    pol.add_argument("--fast-track-requested", action="store_true")
    pol.add_argument("--learner-claims-existing-competence", action="store_true")
    pol.add_argument("--authentic-challenge-available", action="store_true")
    pol.add_argument("--challenge-verifier-available", action="store_true")
    pol.add_argument("--graded-attempt", action="store_true", help="a graded attempt exists -> enable feedback move")
    pol.add_argument("--attempts-without-progress", type=int, default=0, help="consecutive no-progress attempts (wheel-spinning guard)")
    pol.add_argument("--confidence", choices=["low", "medium", "high"], help="learner confidence rating (calibration)")
    pol.add_argument("--from-session", action="store_true", help="derive scope/independence flags from recorded evidence for --capability")
    pol.add_argument("--capability", help="capability to read evidence for when --from-session is set")
    pol.set_defaults(func=cmd_policy)

    d = sp.add_parser("diagnose")
    d.add_argument("--session", required=True)
    d.add_argument("--repeated-observable-error", action="store_true")
    d.add_argument("--root-cause-changes-intervention", action="store_true")
    d.add_argument("--candidate-causes", type=int, default=0)
    d.add_argument("--hypotheses")
    d.set_defaults(func=cmd_diagnose)

    pg = sp.add_parser("progression")
    pg.add_argument("--supported-completion", action="store_true")
    pg.add_argument("--independent-reproduction", action="store_true")
    pg.add_argument("--near-transfer", action="store_true")
    pg.add_argument("--far-transfer", action="store_true")
    pg.add_argument("--delayed-independent-performance", action="store_true")
    pg.add_argument("--strong-independent-reasoning", action="store_true")
    pg.add_argument("--learner-requested-challenge", action="store_true")
    pg.add_argument("--recent-full-solution-seen", action="store_true")
    pg.set_defaults(func=cmd_progression)

    rv = sp.add_parser("review-item")
    rv.add_argument("--capability", required=True)
    rv.add_argument("--kind", required=True)
    rv.add_argument("--known-failure-mode")
    rv.add_argument("--require-transfer", action="store_true")
    rv.set_defaults(func=cmd_review_item)

    pm = sp.add_parser("primitive")
    pm.add_argument("--primitive", required=True)
    pm.set_defaults(func=cmd_primitive)

    it = sp.add_parser("intents")
    it.set_defaults(func=cmd_intents)

    vp = sp.add_parser("verify-python")
    vp.add_argument("--session", required=True)
    vp.add_argument("--code")
    vp.add_argument("--code-file")
    vp.add_argument("--expected-stdout")
    vp.add_argument("--trusted-test-file")
    vp.add_argument("--capability", default="python_artifact_behavior")
    vp.add_argument("--independence", choices=["unassisted", "assisted", "unknown"], default="unknown")
    vp.add_argument("--scope", choices=SCOPE_CHOICES, default="supported_completion")
    vp.add_argument("--evidence-format", choices=FORMAT_CHOICES, default="artifact_execution")
    vp.set_defaults(func=cmd_verify_python)

    dg = sp.add_parser("delegation")
    dg.add_argument("--capability", required=True)
    dg.add_argument("--goal-mode", choices=GOAL_MODE_CHOICES, default="learn")
    dg.add_argument("--ai-role", choices=AI_ROLE_CHOICES, required=True)
    dg.add_argument("--requested-work", action="append")
    dg.add_argument("--protected-cognition", action="append")
    dg.add_argument("--delegable-work", action="append")
    dg.add_argument("--support-kind", choices=["none", "accessibility", "pedagogical", "cognitive_delegation"], default="none")
    dg.add_argument("--independence-not-required", action="store_true")
    dg.set_defaults(func=cmd_delegation)

    ch = sp.add_parser("challenge")
    ch.add_argument("--capability", required=True)
    ch.add_argument("--fast-track-requested", action="store_true")
    ch.add_argument("--claims-competence", action="store_true")
    ch.add_argument("--authentic-task-available", action="store_true")
    ch.add_argument("--verifier-available", action="store_true")
    ch.add_argument("--high-stakes", action="store_true")
    ch.set_defaults(func=cmd_challenge)

    cv = sp.add_parser("coverage")
    cv.add_argument("--capability", required=True)
    cv.add_argument("--cell", action="append", help="dimension:value:attempts:independent_successes")
    cv.set_defaults(func=cmd_coverage)

    cu = sp.add_parser("curriculum")
    cu.add_argument("--authoritative-curriculum-available", action="store_true")
    cu.add_argument("--source-id", action="append")
    cu.add_argument("--dependency-graph-available", action="store_true")
    cu.add_argument("--domain-is-volatile", action="store_true")
    cu.set_defaults(func=cmd_curriculum)

    su = sp.add_parser("source-use")
    su.add_argument("--source-id", required=True)
    su.add_argument("--uri")
    su.add_argument("--source-type", default="web")
    su.add_argument("--operation", choices=["read","quote","summarize","transform","locally_index","retain","redistribute","fine_tune","train_model"], required=True)
    su.add_argument("--jurisdiction")
    su.add_argument("--access-status", default="unknown")
    su.add_argument("--rights-status", default="unknown")
    su.add_argument("--license")
    su.add_argument("--derived-from", action="append")
    su.add_argument("--permit", action="store_true")
    su.add_argument("--deny", action="store_true")
    su.add_argument("--human-review", action="store_true")
    su.set_defaults(func=cmd_source_use)

    ac = sp.add_parser("access-support")
    ac.add_argument("--support-kind", choices=["none","accessibility","pedagogical","cognitive_delegation"], required=True)
    ac.set_defaults(func=cmd_access_support)

    sf = sp.add_parser("simulation-fidelity")
    sf.add_argument("--requested-scope", choices=SCOPE_CHOICES, required=True)
    sf.add_argument("--cognitive", default="unknown")
    sf.add_argument("--social", default="unknown")
    sf.add_argument("--physical", default="unknown")
    sf.add_argument("--temporal", default="unknown")
    sf.add_argument("--authentic-environment", action="store_true")
    sf.set_defaults(func=cmd_simulation_fidelity)

    tg = sp.add_parser("tool-gate")
    tg.add_argument("--tool-id", required=True)
    tg.add_argument("--operation", required=True)
    tg.add_argument("--high-risk", action="store_true")
    tg.add_argument("--side-effecting", action="store_true")
    tg.add_argument("--authorized", action="store_true")
    tg.add_argument("--resource-cost-class", default="standard")
    tg.set_defaults(func=cmd_tool_gate)

    spl = sp.add_parser("sources-plan")
    spl.add_argument("--session", required=True)
    spl.add_argument("--required-capability", action="append", required=True)
    spl.add_argument("--covered-capability", action="append")
    spl.add_argument("--partial-capability", action="append")
    spl.add_argument("--discovery-results", help="optional JSON array of already discovered source candidates with closes_gaps/usable fields")
    spl.add_argument("--out-of-scope-policy", choices=["mark_unknown", "exclude_from_assessment"], default="mark_unknown")
    spl.set_defaults(func=cmd_sources_plan)

    sreg = sp.add_parser("sources-register")
    sreg.add_argument("--session", required=True)
    sreg.add_argument("--resource", action="append")
    sreg.add_argument("--canonical-resource", action="append")
    sreg.set_defaults(func=cmd_sources_register)

    gc = sp.add_parser("graph-curriculum", help="derive prerequisite order + curriculum gate from a graphify graph.json")
    gc.add_argument("--graph", required=True, help="path to graphify-out/graph.json")
    gc.add_argument("--target", required=True, help="capability/topic to sequence toward")
    gc.add_argument("--domain-is-volatile", action="store_true")
    gc.set_defaults(func=cmd_graph_curriculum)

    gcov = sp.add_parser("graph-coverage", help="map required capabilities onto a graphify graph (covered/partial/missing)")
    gcov.add_argument("--graph", required=True)
    gcov.add_argument("--required-capability", action="append", required=True)
    gcov.set_defaults(func=cmd_graph_coverage)

    ga = sp.add_parser("graph-anchors", help="central concepts + communities from a graphify graph for cold-start anchoring")
    ga.add_argument("--graph", required=True)
    ga.add_argument("--k", type=int, default=5)
    ga.set_defaults(func=cmd_graph_anchors)

    wc = sp.add_parser("workspace-check", help="GitHub-style: run a module's submission/ against its checks/, write feedback/ + record evidence")
    wc.add_argument("--session", required=True)
    wc.add_argument("--module", required=True)
    wc.add_argument("--prefer", choices=["auto","local","container"])
    wc.set_defaults(func=cmd_workspace_check)

    md_ = sp.add_parser("modes", help="list the six learning modes (interviewer, mapmaker, socratic, checker, listener, examiner)")
    md_.set_defaults(func=cmd_modes)

    pr_ = sp.add_parser("prompt", help="render one learning-mode prompt")
    pr_.add_argument("stage", choices=sorted(STAGE_PROMPTS))
    pr_.add_argument("--subject")
    pr_.set_defaults(func=cmd_prompt)

    ik = sp.add_parser("intake", help="opening protocol: the single next goal/level question to ask (one at a time) before teaching")
    ik.add_argument("--session", required=True)
    ik.set_defaults(func=cmd_intake)

    lg = sp.add_parser("languages", help="list runnable languages and their production container images")
    lg.set_defaults(func=cmd_languages)

    rx = sp.add_parser("run-exercise", help="compile+run a submission in ANY language (real toolchain/container), optionally assert stdout")
    rx.add_argument("--lang", required=True, choices=sorted(LANGUAGES))
    rx.add_argument("--code")
    rx.add_argument("--code-file")
    rx.add_argument("--stdin-file")
    rx.add_argument("--expect-stdout")
    rx.add_argument("--prefer", choices=["auto","local","container"], help="executor backend (default auto: local toolchain else container)")
    rx.set_defaults(func=cmd_run_exercise)

    vf = sp.add_parser("verify-fastapi", help="run a learner FastAPI app and assert real HTTP behavior via TestClient (no network)")
    vf.add_argument("--code-file", required=True)
    vf.add_argument("--checks-file", required=True, help="JSON array of {name,method,path,json,expect_status,expect_json}")
    vf.add_argument("--prefer", choices=["auto","local","container"])
    vf.set_defaults(func=cmd_verify_fastapi)

    ms = sp.add_parser("mastery", help="BKT mastery estimate (with forgetting) for one capability")
    ms.add_argument("--session", required=True)
    ms.add_argument("--capability", required=True)
    ms.set_defaults(func=cmd_mastery)

    rd = sp.add_parser("review-due", help="spaced-review queue (half-life regression) for this session")
    rd.add_argument("--session", required=True)
    rd.set_defaults(func=cmd_review_due)

    sv = sp.add_parser("serve", help="HTTP API + UI: /health, /api/languages, /api/run, /api/verify-fastapi (Bearer token; loopback by default)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8777)
    sv.add_argument("--token", help="API token (default: ADAPTIVE_API_TOKEN env)")
    sv.add_argument("--allow-anon", action="store_true", help="loopback-only: skip auth (dev)")
    sv.add_argument("--ui", help="HTML file to serve at / (default: decks/academy.html)")
    sv.add_argument("--prefer", choices=["auto","local","container"])
    sv.set_defaults(func=cmd_serve)

    lc = sp.add_parser("listener-check", help="Listener: grade a learner explanation against a source, report what was missed")
    lc.add_argument("--session", required=True)
    lc.add_argument("--capability", required=True)
    lc.add_argument("--explanation-file", required=True)
    lc.add_argument("--source-file", required=True)
    lc.add_argument("--threshold", type=float, default=0.6)
    lc.add_argument("--scope", choices=SCOPE_CHOICES, default="independent_reproduction")
    lc.add_argument("--independence", choices=["unassisted","assisted"], default="unassisted")
    lc.set_defaults(func=cmd_listener_check)

    dh = sp.add_parser("diagnose-history", help="Diagnostician: recurring root problems across a learner's sessions")
    dh.add_argument("--learner-id")
    dh.set_defaults(func=cmd_diagnose_history)

    ls = sp.add_parser("learner-state", help="aggregate capability state across all sessions (optionally one learner)")
    ls.add_argument("--learner-id")
    ls.set_defaults(func=cmd_learner_state)

    pr = sp.add_parser("project")
    pr.add_argument("--session", required=True)
    pr.set_defaults(func=cmd_project)

    ins = sp.add_parser("inspect")
    ins.add_argument("--session", required=True)
    ins.set_defaults(func=cmd_inspect)

    return p


def main(argv=None) -> int:
    """Single entry point with a top-level error handler.

    Domain/user errors become a clean JSON message on stderr with a non-zero exit
    code instead of a raw traceback (argparse still owns usage errors -> exit 2).
    """
    import json as _json
    from runtime.safety import UnsafePathError
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except SystemExit:
        raise
    except (UnsafePathError, FileNotFoundError, KeyError, ValueError, OSError) as e:
        print(_json.dumps({"error": type(e).__name__, "detail": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
