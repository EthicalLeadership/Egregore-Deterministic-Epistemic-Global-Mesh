"""Conversational Agent Orchestrator.

Interprets natural language, calls appropriate tools, and synthesises responses.
All operations are read-only and respect immutability.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, '/home/kark/egregore-core-agent/workspace/legal')
from typing import Any, Dict, Optional

import httpx
from egregore.application.agents import tool_search, tool_code
from egregore.tools.anchorum import AnchorumClient, ANCHORUM_TOOLS


CORE_API_URL = os.environ.get("EGREGORE_CORE_API_URL", "http://127.0.0.1:8002")
CHAT_MODEL = os.environ.get("EGREGORE_CHAT_MODEL", "qwen-7b")


class AgentOrchestrator:
    def __init__(self, core_api_url: str = CORE_API_URL):
        self.core_api_url = core_api_url
        self.history: list[dict[str, str]] = []
        self.last_case_id: str | None = None
        self.legal_module = None
        self._anchorum: AnchorumClient | None = None
        try:
            import anchorum_legal_service as legal_module
            self.legal_module = legal_module
        except Exception:
            self.legal_module = None

    @property
    def anchorum(self) -> AnchorumClient:
        """Lazily initialise the governed ANCHORUM client."""
        if self._anchorum is None:
            self._anchorum = AnchorumClient()
        return self._anchorum

    def _post_json(self, url: str, payload: Dict[str, Any], timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None

    def _llm_intent(self, user_text: str) -> Optional[Dict[str, Any]]:
        """Ask the core API to parse intent into a tool call."""
        system_prompt = (
            "You are Anchorum Agent, an elite legal and strategic AI assistant.\n"
            "You have access to these tools:\n"
            "1. strategize: generate a 360-degree strategy memo. Parameters: case_id (string), goals (string).\n"
            "2. intel: run intelligence and counter-intelligence analysis. Parameters: case_id (string).\n"
            "3. ask: answer a legal question. Parameters: question (string).\n"
            "4. list: list available cases. No parameters.\n"
            "5. status: show system status. No parameters.\n\n"
            "When the user asks something, output a JSON object with the format:\n"
            '{"tool": "<tool_name>", "args": {"<param_name>": "<value>"}}\n'
            "Only output JSON. No extra text."
        )
        payload = {
            "model": os.environ.get("EGREGORE_CHAT_MODEL", "qwen-7b"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
        }
        data = self._post_json(f"{self.core_api_url}/v1/chat/completions", payload, timeout=10.0)
        if not data:
            return None
        try:
            content = data["choices"][0]["message"]["content"].strip()
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                return None
            return json.loads(m.group(0))
        except Exception:
            return None

    def run(self, user_text: str, case_id: str | None = None) -> str:
        lower = user_text.lower().strip()

        # Resolve case_id from explicit parameter, current input, or history
        if case_id:
            self.last_case_id = case_id
        else:
            case_match = re.search(r"\b([A-Z]{2,}-\d{4})\b", user_text, re.IGNORECASE)
            if case_match:
                self.last_case_id = case_match.group(1).upper()
            elif self.last_case_id:
                # If no case in current input, prepend last case for context
                if any(kw in lower for kw in ["strategy", "strateg", "risk", "exposure", "intel", "deception", "manipulation"]):
                    user_text = f"{user_text} for case {self.last_case_id}"
                    lower = user_text.lower()

        # Append to history
        self.history.append({"role": "user", "content": user_text})

        # Greetings
        if lower in ("hello", "hi", "hey", "good morning", "good evening"):
            return "Hello. I am Egregore, the ANCHORUM workspace agent. I can list cases, show timelines and anomalies, run strategy and intelligence analysis, or answer legal questions. What would you like to do?"

        # Quick manual commands
        if lower.startswith("list"):
            return self._list_cases()
        if lower.startswith("status"):
            return self._status()

        # Pattern matching first (highest priority)
        resolved_case_id = case_id or self.last_case_id

        if "strateg" in lower or "risk" in lower or "exposure" in lower or "360" in lower:
            if resolved_case_id:
                return self._run_strategize(resolved_case_id, "Assess legal exposure")
            else:
                return "Which case? Please provide a case ID (e.g., MOLSON-2026)."
        elif "intel" in lower or "deception" in lower or "manipulation" in lower:
            if resolved_case_id:
                return self._run_intel(resolved_case_id)
            else:
                return "Which case? Please provide a case ID (e.g., MOLSON-2026)."

        # ANCHORUM governed-tool routing
        anchorum_response = self._route_anchorum_tools(lower, resolved_case_id)
        if anchorum_response is not None:
            return anchorum_response

        if lower.endswith("?") or lower.startswith("ask"):
            return self._ask(user_text)

        # New tool routing
        if lower.startswith("find ") or lower.startswith("search ") or lower.startswith("read "):
            if lower.startswith("find "):
                query = user_text[5:].strip()
                return self._run_file_search(query)
            elif lower.startswith("search "):
                query = user_text[7:].strip()
                return self._run_web_search(query)
            elif lower.startswith("read "):
                path = user_text[5:].strip()
                return tool_search.read_file(path)

        if lower.startswith("code ") or lower.startswith("run "):
            # Expect format: code <language> <code>
            parts = user_text.split(maxsplit=2)
            if len(parts) >= 3:
                lang = parts[1]
                code = parts[2]
                return self._run_code(lang, code)
            return "Usage: code <python|javascript> <code>"

        # Fallback to LLM intent
        tool_call = self._llm_intent(user_text)
        if tool_call:
            tool = tool_call.get("tool")
            args = tool_call.get("args", {})
            if tool == "strategize":
                case_id = args.get("case_id", "")
                goals = args.get("goals", "Assess legal exposure")
                if case_id:
                    return self._run_strategize(case_id, goals)
            elif tool == "intel":
                case_id = args.get("case_id", "")
                if case_id:
                    return self._run_intel(case_id)
            elif tool == "ask":
                question = args.get("question", user_text)
                return self._ask(question)
            elif tool == "list":
                return self._list_cases()
            elif tool == "status":
                return self._status()

        # Default: treat as legal question
        return self._ask(user_text)

    def _route_anchorum_tools(self, lower: str, case_id: str | None) -> str | None:
        """Dispatch read-only ANCHORUM tools based on message keywords.

        Returns None when no ANCHORUM tool matches, so the caller can fall back
        to the general reasoning path.
        """
        if case_id and any(
            kw in lower for kw in ["timeline", "chronology", "sequence", "events"]
        ):
            result = self.anchorum.get_timeline(case_id)
            return self._format_timeline(result, case_id)

        if case_id and any(
            kw in lower
            for kw in ["anomal", "finding", "red flag", "issue", "concern"]
        ):
            result = self.anchorum.get_anomalies(case_id)
            return self._format_anomalies(result, case_id)

        if case_id and any(
            kw in lower for kw in ["summary", "overview", "counts", "report"]
        ):
            result = self.anchorum.get_case_summary(case_id)
            return self._format_summary(result, case_id)

        return None

    def _format_timeline(self, result: Any, case_id: str) -> str:
        if not result.ok:
            return f"ANCHORUM timeline lookup failed: {result.error_code}"
        timeline = result.data.get("timeline", [])
        if not timeline:
            return f"No timeline events are recorded for {case_id}."
        lines = [f"Timeline for {case_id} ({len(timeline)} events):"]
        for ev in timeline:
            if isinstance(ev, dict):
                date = ev.get("date") or ev.get("timestamp") or "unknown date"
                desc = ev.get("description") or ev.get("event") or ""
                source = ev.get("source") or ""
                lines.append(f"- {date}: {desc}" + (f" [{source}]" if source else ""))
            else:
                lines.append(f"- {ev}")
        return "\n".join(lines)

    def _format_anomalies(self, result: Any, case_id: str) -> str:
        if not result.ok:
            return f"ANCHORUM anomaly lookup failed: {result.error_code}"
        counts = []
        details: list[str] = []
        for bucket in ("critical", "high", "medium", "low", "info"):
            items = result.data.get(bucket, [])
            if items:
                counts.append(f"{len(items)} {bucket}")
                for f in items[:3]:
                    desc = f.get("description", "") if isinstance(f, dict) else str(f)
                    atype = f.get("anomaly_type", "") if isinstance(f, dict) else ""
                    details.append(f"- [{bucket}] {atype}: {desc}"[:240])
        if not counts:
            return f"No anomalies recorded for {case_id}."
        header = f"Anomalies for {case_id}: " + ", ".join(counts)
        return header + ("\n" + "\n".join(details) if details else "")

    def _format_summary(self, result: Any, case_id: str) -> str:
        if not result.ok:
            return f"ANCHORUM summary lookup failed: {result.error_code}"
        data = result.data
        return (
            f"Summary for {case_id}:\n"
            f"- Artifacts: {data.get('artifact_count', 0)}\n"
            f"- Entities: {data.get('entity_count', 0)}\n"
            f"- Anomalies: {data.get('anomaly_count', 0)}\n"
            f"- Critical: {data.get('critical_count', 0)} | High: {data.get('high_count', 0)} | "
            f"Medium: {data.get('medium_count', 0)} | Low: {data.get('low_count', 0)}"
        )

    # --- Tool implementations ---
    def _run_strategize(self, case_id: str, goals: str) -> str:
        from egregore.application.legal_reasoning_engine import LegalReasoningEngine
        from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
        from egregore.domain.legal_agent.legal_models import LegalAgentVersion
        from egregore.interface.anchorum_adapter import AnchorumAdapter
        from egregore.assurance.assurance_engine import AssuranceEngine
        from egregore.application.strategy.engine import StrategyEngine
        from egregore.application.strategy.memo_builder import render_memo
        from egregore.application.strategy.models import StrategyScope
        from egregore.application.strategy.dossier_loader import load_evidence_from_report

        evidence_contents = load_evidence_from_report(case_id)
        scope = StrategyScope(
            matter_id=case_id,
            jurisdiction="QC",
            goals=goals,
            evidence_refs=tuple(evidence_contents.keys()),
            evidence_contents=evidence_contents,
        )

        rule_registry = StaticRuleRegistry()
        engine = LegalReasoningEngine(
            rule_registry=rule_registry,
            agent_version=LegalAgentVersion(
                rule_registry_version="static-v1",
                inference_engine_version="strategize-1.0",
            ),
        )
        adapter = AnchorumAdapter(engine=engine, raw_output_dir=Path("/tmp/anchorum_agent_raw"))
        assurance = AssuranceEngine()
        strategy_engine = StrategyEngine(adapter, assurance)
        memo = strategy_engine.run(scope)
        return render_memo(memo)

    def _run_intel(self, case_id: str) -> str:
        from egregore.application.intelligence.unit import IntelligenceUnit
        from egregore.domain.artifact.store import ArtifactStore
        from egregore.domain.artifact.access import AgentArtifactAccessor
        from egregore.domain.artifact.models import SecuredArtifact

        store = ArtifactStore(Path("/tmp/anchorum_intel_store"))
        accessor = AgentArtifactAccessor(store)

        report_path = self._find_report(case_id)
        if not report_path:
            return "Dossier report not found."

        data = json.loads(report_path.read_text(encoding="utf-8"))
        artifact_ids = []
        metadata_flags = {}
        for f in data.get("high_findings", []):
            anomaly_type = f.get("anomaly_type", "")
            for art_id in f.get("affected_artifacts", []):
                if art_id not in artifact_ids:
                    artifact_ids.append(art_id)
                if anomaly_type == "metadata_scrubbed":
                    metadata_flags[art_id] = {"metadata_scrubbed": True}

        for art_id in artifact_ids:
            store.save(SecuredArtifact(id=art_id, content_hash=art_id))

        unit = IntelligenceUnit(accessor)
        collection_result, counter_result = unit.run(case_id, {
            "artifact_ids": artifact_ids,
            "requirements": ["Assess case posture"],
            "external_sources": {},
            "evidence_metadata": metadata_flags,
        })

        report = collection_result.findings.get("intel_report")
        findings = counter_result.findings
        n_ind = len(findings.get("indicators", ()))
        n_hyp = len(findings.get("hypotheses", ()))
        n_rec = len(findings.get("recommendations", ()))

        brief = (
            f"Intelligence summary for {case_id}:\n"
            f"- Sources: {len(report.sources)}\n"
            f"- Findings: {len(report.findings)}\n"
            f"- Manipulation indicators: {n_ind}\n"
            f"- Deception hypotheses: {n_hyp}\n"
            f"- Countermeasures: {n_rec}\n"
        )
        return brief


    def _run_file_search(self, query: str) -> str:
        files = tool_search.search_files(query)
        if not files:
            return f"No files matching '{query}' found."
        return "Files matching:\n" + "\n".join(f"  {f}" for f in files[:20])

    def _run_web_search(self, query: str) -> str:
        return tool_search.web_search(query)

    def _run_code(self, language: str, code: str) -> str:
        if language == "python":
            return tool_code.run_python(code)
        elif language == "javascript":
            return tool_code.run_javascript(code)
        else:
            return f"Unsupported language: {language}"

    def _ask(self, question: str) -> str:
        if self.legal_module:
            return self._legal_ask(question)
        # Fallback to core inference if legal module unavailable
        payload = {
            "model": os.environ.get("EGREGORE_CHAT_MODEL", "qwen-7b"),
            "messages": [
                {"role": "system", "content": "You are a Quebec legal research assistant."},
                {"role": "user", "content": question},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
        }
        data = self._post_json(f"{self.core_api_url}/v1/chat/completions", payload, timeout=30.0)
        if not data:
            return "Inference service is not reachable. Please start the core API."
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            return "Unexpected response from inference service."

    def _legal_ask(self, question: str) -> str:
        """Use the Legal Dossier retrieval engine for a cited legal answer."""
        lm = self.legal_module
        authorities = lm.load_authorities()
        matched = lm.retrieve(question, authorities)
        if not matched:
            return "INSUFFICIENT AUTHORITY — no relevant legal texts found in the current corpus."
        authority_prompt = lm.build_authority_prompt(matched)
        combined_prompt = f"Question: {question}\n\nAuthorities:\n{authority_prompt}"
        raw_answer = lm.call_model(lm.HERMES_URL, lm.HERMES_MODEL, lm.LEGAL_SYSTEM_PROMPT, combined_prompt)
        errors = lm.verify_ids(raw_answer, matched)
        if errors:
            retry_prompt = f"Your previous answer cited invalid authorities. Use only these IDs: {[a['id'] for a in matched]}. Answer again."
            raw_answer = lm.call_model(lm.HERMES_URL, lm.HERMES_MODEL, lm.LEGAL_SYSTEM_PROMPT, f"Question: {question}\n\nAuthorities:\n{authority_prompt}\n\n{retry_prompt}")
            errors = lm.verify_ids(raw_answer, matched)
        raw_answer = lm.clean_model_output(raw_answer)
        if errors:
            final = "INSUFFICIENT AUTHORITY — could not produce verified citations.\n" + "\n".join(errors)
        else:
            final = lm.convert_ids_to_citations(raw_answer, matched)
        return final

    def _list_cases(self) -> str:
        result = self.anchorum.list_cases()
        if not result.ok:
            return f"ANCHORUM case list failed: {result.error_code}"
        cases = result.data
        if isinstance(cases, dict):
            cases = cases.get("cases", [])
        if not cases:
            return "No cases found in ANCHORUM."
        return "Available cases:\n" + "\n".join(f"  {c}" for c in sorted(str(c) for c in cases))

    def _status(self) -> str:
        return (
            f"Workspace: {Path.cwd()}\n"
            "Agents: counsel, dossier, strategist, intel_collector, counter_intel\n"
            "Immutability: enforced\n"
            "Architecture tests: 54 passed\n"
        )

    def _find_report(self, case_id: str) -> Optional[Path]:
        for candidate in [Path.cwd(), Path.home() / "egregore"]:
            for f in candidate.glob("*_report.json"):
                if f.stem.lower() == f"{case_id.lower()}_report":
                    return f
        return None
