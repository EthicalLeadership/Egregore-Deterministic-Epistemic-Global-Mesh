# epistemic marker: provenance / auditability
import hashlib
import time

from src.egregore.dt1.inference_work_unit import InferenceWorkUnit
from src.egregore.interface.semantics_ports import Kimik2LoaderError


class PanelCompletionResult:
    def __init__(self, output_text, output_hash, token_count, latency_ms, status):
        self.output_text = output_text
        self.output_hash = output_hash
        self.token_count = token_count
        self.latency_ms = latency_ms
        self.status = status


class DeterministicInferenceRunner:
    def __init__(self, loader):
        self.loader = loader

    def run(self, work_unit: InferenceWorkUnit, timeout_s: int = 120):
        start = time.monotonic()
        try:
            output_text = self._run_with_timeout(work_unit, timeout_s)
            status = "SUCCESS"
        except Kimik2LoaderError as e:
            output_text = str(e)
            status = "ERROR"
        except TimeoutError:
            output_text = "Timeout"
            status = "TIMEOUT"
        latency_ms = int((time.monotonic() - start) * 1000)
        output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        token_count = len(output_text.split())
        return PanelCompletionResult(
            output_text, output_hash, token_count, latency_ms, status
        )

    def _run_with_timeout(self, work_unit, timeout_s):
        import threading

        result = {}

        def target():
            try:
                result["output"] = self.loader.generate(
                    work_unit.prompt, work_unit.max_tokens, temperature=0.0
                )
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=timeout_s)
        if thread.is_alive():
            raise TimeoutError()
        if "error" in result:
            raise result["error"]
        return result["output"]
