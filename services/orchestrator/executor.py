"""Executor stage for orchestrator split.

Owns tool execution fan-out and result normalization.
"""

import html
import json
import re
from typing import Any, Dict
from urllib.parse import quote_plus
from xml.etree import ElementTree

from services.contracts import ExecutorRequest, ExecutorResult, ToolExecutionRequest
from services.orchestrator.sys_selector import select_sys_command, CommandCategory
from services.orchestrator.wsl_session_selector import select_wsl_session_action, SESSION_SCOPED_ACTIONS


class ToolExecutor:
    """Runs selected tools via the provided ToolCoordinator."""

    def __init__(self, tool_coordinator):
        self.tool_coordinator = tool_coordinator

    @staticmethod
    def _strip_html_tags(value: str) -> str:
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_duckduckgo_results(raw_html: str) -> list[dict[str, str]]:
        pattern = re.compile(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = re.findall(
            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>|<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<divsnippet>.*?)</div>',
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        cleaned_snippets: list[str] = []
        for anchor_snippet, div_snippet in snippets:
            snippet_raw = anchor_snippet or div_snippet or ""
            cleaned = ToolExecutor._strip_html_tags(snippet_raw)
            if cleaned:
                cleaned_snippets.append(cleaned)

        results: list[dict[str, str]] = []
        for index, match in enumerate(pattern.finditer(raw_html)):
            title = ToolExecutor._strip_html_tags(match.group("title"))
            url = html.unescape(match.group("url"))
            snippet = cleaned_snippets[index] if index < len(cleaned_snippets) else ""
            if title and url:
                results.append({"title": title[:180], "url": url[:500], "snippet": snippet[:300]})
            if len(results) >= 5:
                break
        return results

    @staticmethod
    def _extract_wikipedia_opensearch_results(raw_text: str) -> list[dict[str, str]]:
        """Parse Wikipedia OpenSearch JSON into normalized result entries."""
        try:
            payload = json.loads(raw_text)
        except Exception:
            return []

        if not isinstance(payload, list) or len(payload) < 4:
            return []

        titles = payload[1] if isinstance(payload[1], list) else []
        snippets = payload[2] if isinstance(payload[2], list) else []
        urls = payload[3] if isinstance(payload[3], list) else []

        results: list[dict[str, str]] = []
        for title, snippet, url in zip(titles, snippets, urls):
            t = str(title or "").strip()
            s = str(snippet or "").strip()
            u = str(url or "").strip()
            if t and u:
                results.append({"title": t[:180], "url": u[:500], "snippet": s[:300]})
            if len(results) >= 5:
                break
        return results

    @staticmethod
    def _extract_bing_rss_results(raw_text: str) -> list[dict[str, str]]:
        """Parse Bing's compact RSS search surface into direct result URLs."""
        try:
            root = ElementTree.fromstring(raw_text)
        except ElementTree.ParseError:
            return []
        results: list[dict[str, str]] = []
        for item in root.findall("./channel/item"):
            title = str(item.findtext("title") or "").strip()
            url = str(item.findtext("link") or "").strip()
            snippet = ToolExecutor._strip_html_tags(str(item.findtext("description") or ""))
            if title and re.match(r"^https?://", url, flags=re.IGNORECASE):
                results.append({"title": title[:180], "url": url[:500], "snippet": snippet[:300]})
            if len(results) >= 8:
                break
        return results

    @staticmethod
    def _extract_ddg_lite_results(raw_html: str) -> list[dict[str, str]]:
        """Parse DuckDuckGo Lite HTML into normalized result entries.

        DDG Lite uses simple table-based markup that is rarely affected by
        bot-detection/CAPTCHA pages, making it a reliable first-pass source.
        """
        results: list[dict[str, str]] = []
        # Each result block: <a class="result-link" href="...">Title</a>
        link_matches = list(re.finditer(
            r'<a[^>]+class="result-link"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        snippet_matches = re.findall(
            r'<td[^>]+class="result-snippet"[^>]*>(?P<snippet>.*?)</td>',
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for index, match in enumerate(link_matches):
            title = ToolExecutor._strip_html_tags(match.group("title"))
            url = html.unescape(match.group("url"))
            snippet = ""
            if index < len(snippet_matches):
                snippet = ToolExecutor._strip_html_tags(snippet_matches[index])
            if title and url:
                results.append({"title": title[:180], "url": url[:500], "snippet": snippet[:300]})
            if len(results) >= 5:
                break
        return results

    @staticmethod
    def _extract_wikipedia_html_results(raw_html: str) -> list[dict[str, str]]:
        """Parse Wikipedia search result page HTML into normalized entries."""
        heading_matches = list(
            re.finditer(
                r'<div[^>]*class="[^"]*mw-search-result-heading[^"]*"[^>]*>\s*'
                r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
                raw_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        snippet_matches = re.findall(
            r'<div[^>]*class="[^"]*searchresult[^"]*"[^>]*>(?P<snippet>.*?)</div>',
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        results: list[dict[str, str]] = []
        for index, match in enumerate(heading_matches):
            title = ToolExecutor._strip_html_tags(match.group("title"))
            url = html.unescape(match.group("url"))
            if url.startswith("/"):
                url = f"https://en.wikipedia.org{url}"
            snippet = ""
            if index < len(snippet_matches):
                snippet = ToolExecutor._strip_html_tags(snippet_matches[index])
            if title and url:
                results.append({"title": title[:180], "url": url[:500], "snippet": snippet[:300]})
            if len(results) >= 5:
                break
        return results

    @staticmethod
    def _normalize_sys_output(
        raw_output: Any,
        parameters: dict[str, Any],
        result_metadata: dict[str, Any] | None = None,
    ) -> Any:
        command = str(parameters.get("command") or "").strip().lower()
        context = str(parameters.get("context") or "").strip().lower()
        text = str(raw_output or "").strip()

        if command == "date" or context == "agent_time_lookup":
            return {
                "source": "sys",
                "kind": "time_lookup",
                "utc_iso": text,
                "summary_text": f"Current UTC time: {text}",
            }

        if context == "agent_ubuntu_release_lookup":
            entries = re.split(r"\n\s*\n", text)
            latest: dict[str, str] = {}
            for block in entries:
                fields: dict[str, str] = {}
                for line in block.splitlines():
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    fields[key.strip().lower()] = value.strip()
                if fields.get("version") and fields.get("dist"):
                    latest = fields
            version = latest.get("version", "unknown")
            codename = latest.get("dist", "unknown")
            return {
                "source": "sys",
                "kind": "release_lookup",
                "product": "ubuntu",
                "version": version,
                "codename": codename,
                "summary_text": f"Current Ubuntu LTS release: {version} ({codename}).",
            }

        if command == "python3" or command == "python" or context == "agent_python_exec":
            return {
                "source": "sys",
                "kind": "python_result",
                "output": text,
                "summary_text": f"Berechnungsergebnis: {text}",
            }

        if command == "julia" or context == "agent_julia_exec":
            if isinstance(raw_output, dict):
                text = str(raw_output.get("output") or raw_output.get("result") or raw_output)
            return {
                "source": "sys",
                "kind": "julia_result",
                "output": text,
                "summary_text": f"Berechnungsergebnis: {text}",
            }

        if context == "agent_url_fetch":
            stripped = ToolExecutor._strip_html_tags(text)[:1200]
            args = parameters.get("args") or []
            fetched_url = str(args[-1]).strip() if args else ""
            return {
                "source": "sys",
                "kind": "url_fetch",
                "url": fetched_url,
                "content": stripped,
                "summary_text": stripped[:400],
            }

        if context in ("agent_workspace_list", "agent_workspace_read"):
            return {
                "source": "sys",
                "kind": "workspace_files",
                "output": text,
                "summary_text": text[:600],
            }

        if context in ("agent_workspace_write", "agent_workspace_touch", "agent_workspace_mkdir"):
            target_path = str(parameters.get("target_path") or "").strip()
            storage_scope = str(parameters.get("storage_scope") or "").strip()
            write_mode = str(parameters.get("write_mode") or "").strip()
            evidence = dict((result_metadata or {}).get("mutation_evidence") or {})
            verified = bool((result_metadata or {}).get("mutation_verified"))
            return {
                "source": "sys",
                "kind": "workspace_write",
                "target_path": target_path,
                "storage_scope": storage_scope,
                "write_mode": write_mode,
                "verified": verified,
                "evidence": evidence,
                "output": text,
                "summary_text": (
                    f"Verified write: {write_mode or 'write'} -> {target_path} "
                    f"[{storage_scope or 'unknown'}] sha256={evidence.get('sha256') or '-'}"
                    if verified
                    else f"Unverified write result: {write_mode or 'write'} -> {target_path}"
                ),
            }

        if context == "agent_web_discovery":
            results = ToolExecutor._extract_bing_rss_results(text)
            query = str(parameters.get("search_query") or "").strip()
            return {
                "source": "sys",
                "kind": "web_discovery",
                "query": query,
                "results": results,
                "candidate_count": len(results),
                "evidence_scope": "discovery",
                "summary_text": "\n".join(
                    f"- {item['title']}: {item['snippet']} ({item['url']})" for item in results[:5]
                ) or "No parseable search candidates were returned.",
            }

        if command == "curl" or context == "agent_web_lookup":
            # Parser cascade: Wikipedia HTML → DDG HTML SERP → DDG Lite
            # (Wikipedia is primary because DDG blocks from WSL Alpine)
            results = ToolExecutor._extract_wikipedia_html_results(text)
            if not results:
                results = ToolExecutor._extract_duckduckgo_results(text)
            if not results:
                results = ToolExecutor._extract_ddg_lite_results(text)
            query = ""
            args = parameters.get("args") or []
            if args:
                maybe_url = str(args[-1])
                if "?q=" in maybe_url:
                    query = maybe_url.split("?q=", 1)[1].split("&", 1)[0].replace("+", " ")
                elif "search=" in maybe_url:
                    query = maybe_url.split("search=", 1)[1].split("&", 1)[0].replace("+", " ")
                elif "/Special:Search/" in maybe_url:
                    query = maybe_url.split("/Special:Search/", 1)[1].replace("+", " ")
            summary_parts = []
            for item in results[:3]:
                summary_parts.append(
                    f"- {item.get('title', '')}: {item.get('snippet', '')} ({item.get('url', '')})"
                )
            summary_text = "\n".join(summary_parts).strip()
            if not summary_text:
                summary_text = ToolExecutor._strip_html_tags(text)[:600]
            return {
                "source": "sys",
                "kind": "web_lookup",
                "query": html.unescape(query),
                "results": results,
                "summary_text": summary_text,
            }

        return raw_output

    def _build_python_code(self, query: str) -> str:
        """Derive a simple Python3 one-liner that evaluates the user's math/logic query."""
        q = query.strip()
        # Try to extract a raw numeric expression first (e.g. "3 * 7 + 2")
        expr_match = re.search(r"[\d\s\+\-\*\/\%\(\)\.]+", q)
        # Prefer explicit expression patterns: digits with operators
        arith_match = re.search(r"\d[\d\s\+\-\*\/\%\(\)\.]*[\+\-\*\/][\d\s\+\-\*\/\%\(\)\.]*\d", q)
        if arith_match:
            expr = arith_match.group().strip()
            return f"print({expr})"

        # Celsius ↔ Fahrenheit
        c2f = re.search(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?celsius\s*(?:in|to|nach|zu)\s*fahrenheit", q, re.IGNORECASE)
        f2c = re.search(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?fahrenheit\s*(?:in|to|nach|zu)\s*celsius", q, re.IGNORECASE)
        c2k = re.search(r"(\d+(?:\.\d+)?)\s*(?:grad\s*)?celsius\s*(?:in|to|nach|zu)\s*kelvin", q, re.IGNORECASE)
        if c2f:
            return f"c={c2f.group(1)}; print(f'{{c}}°C = {{c*9/5+32:.2f}}°F')"
        if f2c:
            return f"f={f2c.group(1)}; print(f'{{f}}°F = {{(f-32)*5/9:.2f}}°C')"
        if c2k:
            return f"c={c2k.group(1)}; print(f'{{c}}°C = {{c+273.15:.2f}} K')"

        # sqrt / Wurzel
        sqrt_match = re.search(r"(?:sqrt|wurzel|square root)[^\d]*(\d+(?:\.\d+)?)", q, re.IGNORECASE)
        if sqrt_match:
            return f"import math; print(math.sqrt({sqrt_match.group(1)}))"

        # Factorial
        fact_match = re.search(r"(?:factorial|fakultät|fakultaet)[^\d]*(\d+)", q, re.IGNORECASE)
        if fact_match:
            return f"import math; print(math.factorial({fact_match.group(1)}))"

        # Fibonacci
        fib_match = re.search(r"fibonacci[^\d]*(\d+)", q, re.IGNORECASE)
        if fib_match:
            n = int(fib_match.group(1))
            return (
                f"a,b=0,1\n"
                f"for _ in range({n}-1): a,b=b,a+b\n"
                f"print(b)"
            )

        # Byte conversions
        byte_match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|kb|tb|bytes?)\s*(?:in|to|nach|zu)\s*(gb|mb|kb|tb|bytes?)", q, re.IGNORECASE)
        if byte_match:
            val, src, dst = byte_match.group(1), byte_match.group(2).lower().rstrip("s"), byte_match.group(3).lower().rstrip("s")
            units = {"byte": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
            return f"v={val}; print(f'{{v}} {src} = {{v * {units.get(src, 1)} / {units.get(dst, 1)}:.4g}} {dst}')"

        # Fallback: just evaluate the first numeric expression found
        if expr_match:
            expr = expr_match.group().strip()
            if any(op in expr for op in ["+", "-", "*", "/"]):
                return f"print({expr})"

        # Last resort: echo back what we got
        safe_q = q.replace('"', "'")
        return f'print("Konnte \'{safe_q}\' nicht automatisch auswerten.")'

    def _build_sys_parameters(
        self,
        query: str,
        intent: str | None = None,
        *,
        session_id: str = "",
        run_id: str = "",
        retrieval_intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build /sys tool parameters via sys_selector.select_sys_command.

        The router already ran Stage 1 (needs_sys) and Stage 2 (select_sys_command)
        and stored the intent in routing_metadata.  We re-run Stage 2 here so the
        executor owns the concrete command construction without coupling to the router.
        
        Now passes inference_invoker to enable LLM-based write-intent extraction.
        """
        # Get inference invoker if available for LLM-based parameter extraction
        inference_invoker = None
        try:
            from services.inference.invocation import ensure_inference_invoker
            inference_invoker = ensure_inference_invoker()
        except Exception:
            pass
        
        retrieval = dict(retrieval_intent or {})
        if retrieval.get("requires_external_information"):
            candidate_url = str(retrieval.get("candidate_url") or "").strip()
            discovery_required = bool(retrieval.get("discovery_required"))
            if candidate_url and not discovery_required:
                return {
                    "command": "curl",
                    "args": ["-s", "-L", "-m", "15", "-A", "Mozilla/5.0", candidate_url],
                    "source": "orchestrator",
                    "context": "agent_url_fetch",
                    "session_id": session_id,
                    "run_id": run_id,
                    "request_id": run_id or session_id,
                    "url": candidate_url,
                }
            search_query = str(retrieval.get("search_query") or retrieval.get("goal") or query).strip()
            source_hint = str(retrieval.get("source_hint") or "").strip()
            if source_hint:
                search_query = f'"{source_hint}" {search_query}'[:500]
            search_url = f"https://www.bing.com/search?format=rss&q={quote_plus(search_query)}"
            return {
                "command": "curl",
                "args": ["-s", "-L", "-m", "15", "-A", "Mozilla/5.0", search_url],
                "source": "orchestrator",
                "context": "agent_web_discovery",
                "session_id": session_id,
                "run_id": run_id,
                "request_id": run_id or session_id,
                "url": search_url,
                "search_query": search_query,
            }

        sel = select_sys_command(query, inference_invoker=inference_invoker)

        # Python fallback: args are built dynamically from the query
        if sel.category == CommandCategory.COMPUTE_TRANSFORM:
            if sel.command == "julia":
                return {
                    "command": sel.command,
                    "args": sel.args,
                    "source": "orchestrator",
                    "context": sel.context,
                    "session_id": session_id,
                    "run_id": run_id,
                    "request_id": run_id or session_id,
                }
            code = self._build_python_code(query)
            return {
                "command": sel.command,
                "args": ["-c", code],
                "source": "orchestrator",
                "context": sel.context,
                "session_id": session_id,
                "run_id": run_id,
                "request_id": run_id or session_id,
            }

        # All other commands have pre-built args from the selector
        parameters = {
            "command": sel.command,
            "args": sel.args,
            "source": "orchestrator",
            "context": sel.context,
            "session_id": session_id,
            "run_id": run_id,
            "request_id": run_id or session_id,
        }
        if sel.stdin_text is not None:
            parameters["stdin_text"] = sel.stdin_text
        if sel.extra:
            parameters.update(sel.extra)
        return parameters

    def _build_tool_parameters(self, tool_name: str, request: ExecutorRequest) -> dict[str, Any]:
        if tool_name == "sys":
            routing_metadata = request.routing_metadata or {}
            intent = routing_metadata.get("intent")
            return self._build_sys_parameters(
                request.query,
                intent=intent,
                session_id=request.session_id,
                run_id=request.run_id,
                retrieval_intent=(routing_metadata.get("retrieval_intent") if isinstance(routing_metadata.get("retrieval_intent"), dict) else None),
            )
        if tool_name in {"current_time", "orientation"}:
            return {}
        if tool_name == "session_context":
            return {
                "session_id": request.session_id,
                "limit": 8,
                "include_tool_messages": False,
            }
        if tool_name in {"read_file", "list_files"}:
            parameters: dict[str, Any] = {"session_id": request.session_id}
            if request.sandbox_root:
                parameters["sandbox_root"] = request.sandbox_root
            return parameters
        if tool_name == "wsl_session":
            return self._build_wsl_session_parameters(
                request.query,
                routing_metadata=request.routing_metadata,
            )
        if tool_name == "plot_chart":
            return self._build_plot_chart_parameters(request.query)
        return {"query": request.query}

    @staticmethod
    def _build_wsl_session_parameters(
        query: str,
        *,
        routing_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build wsl_session tool parameters via select_wsl_session_action.

        No LLM function-calling exists in this pipeline (see the module-level
        docstring in wsl_session_selector.py), so `action` is derived
        heuristically from the free-text query, mirroring how _build_sys_parameters
        derives /sys commands via sys_selector. `session_id` is resolved from an
        explicit session id literally present in the query, falling back to the
        caller-tracked "last created" session id passed via routing_metadata --
        never guessed or fabricated.
        """
        selection = select_wsl_session_action(query)
        parameters: dict[str, Any] = {"action": selection.action}

        if selection.action in SESSION_SCOPED_ACTIONS:
            routing = routing_metadata or {}
            session_id = selection.explicit_session_id or str(routing.get("wsl_session_id") or "")
            if session_id:
                parameters["session_id"] = session_id
            # else: leave session_id unset -- the tool's own existing
            # "session_id is required for this action" failure handles this
            # gracefully; no new error path needed here.
        return parameters

    @staticmethod
    def _build_plot_chart_parameters(query: str) -> dict[str, Any]:
        """Build plot_chart tool parameters heuristically from the query.

        Only improves chart_type/title fidelity. The resulting y_values remain
        a synthetic, query-hash-derived fallback series (via the tool's own
        _fallback_series_from_query) -- not real data -- until a real producer
        for x_values/y_values exists; extracting real numeric series from
        freeform natural language is out of scope here.
        """
        text = (query or "").lower()
        bar_keywords = (
            "bar chart", "bar graph", "bars",
            "balkendiagramm", "säulendiagramm", "saeulendiagramm", "balken",
        )
        chart_type = "bar" if any(keyword in text for keyword in bar_keywords) else "line"

        title = (query or "").strip()
        title = re.sub(
            r"^(plot|chart|graph|zeichne|erstelle(?:\s+(?:ein|einen))?)\s+",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        title = title[:80] if title else ""

        return {
            "query": query,
            "chart_type": chart_type,
            "title": title or "LIARA Chart",
        }

    def prepare_tool_requests(self, request: ExecutorRequest) -> list[ToolExecutionRequest]:
        """Build the exact requests that will be judged and executed.

        Keeping preparation in one place prevents the pre-action judge from
        approving an abstract tool name while the executor later constructs a
        materially different command payload.
        """
        return [
            ToolExecutionRequest(
                tool_name=tool_name,
                parameters=self._build_tool_parameters(tool_name, request),
                timeout_seconds=request.timeout_seconds,
                simulation_mode=request.simulation_mode,
            )
            for tool_name in request.tool_names
        ]

    async def _execute_julia_compute(
        self,
        query: str,
        *,
        session_id: str = "",
        run_id: str = "",
        timeout_seconds: float = 5.0,
        simulation_mode: bool = False,
    ) -> ExecutorResult:
        from services.config import Settings
        from services.simulation.bridge import JuliaBridge, JuliaBridgeError

        allowlist = list(dict.fromkeys([*Settings.julia_allowlist(), "chat_math"]))
        bridge = JuliaBridge(allowlist=allowlist)
        parameters = {
            "command": "julia",
            "context": "agent_julia_exec",
        }
        try:
            raw_output = await bridge.run("chat_math", {"query": query})
        except JuliaBridgeError as exc:
            if self.tool_coordinator is not None:
                python_parameters = {
                    "command": "python3",
                    "args": ["-c", self._build_python_code(query)],
                    "request_id": run_id or session_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "source": "orchestrator",
                    "context": "agent_python_exec",
                }
                fallback_request = ToolExecutionRequest(
                    tool_name="sys",
                    parameters=python_parameters,
                    timeout_seconds=timeout_seconds,
                    simulation_mode=simulation_mode,
                )
                fallback_results = await self.tool_coordinator.execute_tools_parallel([fallback_request])
                fallback_result = fallback_results.get("sys")
                if fallback_result and fallback_result.status == "success":
                    normalized = self._normalize_sys_output(
                        fallback_result.output,
                        python_parameters,
                        fallback_result.metadata,
                    )
                    return ExecutorResult(
                        tool_outputs={"sys": normalized},
                        success_count=1,
                        failed_count=0,
                        metadata={
                            "requested_tools": 1,
                            "requested_tool_names": ["sys"],
                            "tool_statuses": {"sys": "success"},
                            "failed_tools": [],
                            "tool_errors": {},
                            "fallback_used": "python",
                            "fallback_reason": str(exc),
                        },
                    )

                fallback_error = fallback_result.error if fallback_result and fallback_result.error else "python fallback failed"
                return ExecutorResult(
                    tool_outputs={},
                    success_count=0,
                    failed_count=1,
                    metadata={
                        "requested_tools": 1,
                        "requested_tool_names": ["sys"],
                        "tool_statuses": {"sys": "failed"},
                        "failed_tools": ["sys"],
                        "tool_errors": {
                            "sys": f"julia failed: {exc}; python fallback failed: {fallback_error}"
                        },
                        "fallback_used": "python",
                        "fallback_reason": str(exc),
                    },
                )

            return ExecutorResult(
                tool_outputs={},
                success_count=0,
                failed_count=1,
                metadata={
                    "requested_tools": 1,
                    "requested_tool_names": ["sys"],
                    "tool_statuses": {"sys": "failed"},
                    "failed_tools": ["sys"],
                    "tool_errors": {"sys": str(exc)},
                },
            )

        return ExecutorResult(
            tool_outputs={"sys": self._normalize_sys_output(raw_output, parameters)},
            success_count=1,
            failed_count=0,
            metadata={
                "requested_tools": 1,
                "requested_tool_names": ["sys"],
                "tool_statuses": {"sys": "success"},
                "failed_tools": [],
                "tool_errors": {},
            },
        )

    async def execute(
        self,
        request: ExecutorRequest,
        *,
        prepared_requests: list[ToolExecutionRequest] | None = None,
    ) -> ExecutorResult:
        if request.tool_names == ["sys"]:
            selection = select_sys_command(request.query)
            if selection.context == "agent_julia_exec":
                return await self._execute_julia_compute(
                    request.query,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    timeout_seconds=request.timeout_seconds,
                    simulation_mode=request.simulation_mode,
                )

        requests = prepared_requests if prepared_requests is not None else self.prepare_tool_requests(request)

        results_dict = await self.tool_coordinator.execute_tools_parallel(requests)

        tool_outputs: Dict[str, Any] = {}
        success_count = 0
        failed_count = 0
        tool_statuses: Dict[str, str] = {}
        failed_tools: list[str] = []
        tool_errors: Dict[str, str] = {}
        governance_required: Dict[str, Any] = {}
        wsl_session_created_id: str | None = None
        wsl_session_destroyed_id: str | None = None

        for tool_name, result in results_dict.items():
            tool_statuses[tool_name] = result.status
            if result.status == "success":
                success_count += 1
                request_parameters = next(
                    (tool_request.parameters for tool_request in requests if tool_request.tool_name == tool_name),
                    {},
                )
                if tool_name == "sys":
                    tool_outputs[tool_name] = self._normalize_sys_output(
                        result.output,
                        request_parameters,
                        result.metadata,
                    )
                else:
                    tool_outputs[tool_name] = result.output
                if tool_name == "wsl_session" and isinstance(result.output, dict):
                    if request_parameters.get("action") == "create":
                        created_id = result.output.get("session_id")
                        if created_id:
                            wsl_session_created_id = str(created_id)
                    elif request_parameters.get("action") == "destroy":
                        destroyed_id = request_parameters.get("session_id")
                        if destroyed_id:
                            wsl_session_destroyed_id = str(destroyed_id)
            else:
                failed_count += 1
                failed_tools.append(tool_name)
                if result.error:
                    tool_errors[tool_name] = result.error
                # Failure is part of the execution truth.  Preserve it for the
                # planner/validator, but explicitly mark it as non-evidence so
                # it can never ground factual claims.
                tool_outputs[tool_name] = {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": result.error or "tool_execution_failed",
                    "metadata": dict(result.metadata or {}),
                }
                if result.metadata.get("governance_required"):
                    governance_required[tool_name] = {
                        "mode": result.metadata.get("governance_mode"),
                        "classification": result.metadata.get("governance_classification"),
                        "error": result.error,
                    }

        return ExecutorResult(
            tool_outputs=tool_outputs,
            success_count=success_count,
            failed_count=failed_count,
            metadata={
                "requested_tools": len(request.tool_names),
                "requested_tool_names": list(request.tool_names),
                "tool_statuses": tool_statuses,
                "failed_tools": failed_tools,
                "tool_errors": tool_errors,
                "governance_required": governance_required,
                "wsl_session_created_id": wsl_session_created_id,
                "wsl_session_destroyed_id": wsl_session_destroyed_id,
            },
        )
