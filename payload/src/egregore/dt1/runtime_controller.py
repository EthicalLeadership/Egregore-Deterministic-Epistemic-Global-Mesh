# epistemic marker: provenance / auditability
class DT1RuntimeController:
    def __init__(self, runner, composition_guard, pressure_gate, output_sink):
        self.runner = runner
        self.composition_guard = composition_guard
        self.pressure_gate = pressure_gate
        self.output_sink = output_sink
        self.phase1_state = {}
        self.credit_lease = 1

    def tick(self, work_units):
        admitted = [
            wu for wu in work_units if self.pressure_gate.admit(wu, self.credit_lease)
        ]
        for wu in admitted:
            result = self.runner.run(wu)
            self.composition_guard.assert_terminal(result)
            self.output_sink.handle(result)
            self.phase1_state[wu.work_unit_id] = result
