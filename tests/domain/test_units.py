import pytest

from egregore.domain.units import DT, DT_ZERO, TU, TU_ZERO


class TestDT:
    def test_creation_positive(self):
        dt = DT(5.0)
        assert dt.value == 5.0
        assert dt.gflops == 50.0

    def test_creation_zero(self):
        assert DT(0.0) == DT_ZERO

    def test_creation_negative_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            DT(-1.0)

    def test_immutability(self):
        dt = DT(5.0)
        with pytest.raises(AttributeError):
            dt.value = 10.0

    def test_comparison(self):
        assert DT(1.0) < DT(5.0)
        assert DT(5.0) > DT(1.0)
        assert DT(1.0) == DT(1.0)

    def test_addition(self):
        assert DT(3.0) + DT(2.0) == DT(5.0)

    def test_subtraction(self):
        assert DT(5.0) - DT(2.0) == DT(3.0)

    def test_subtraction_negative_raises(self):
        with pytest.raises(ValueError, match="would yield negative"):
            DT(1.0) - DT(5.0)

    def test_multiplication(self):
        assert DT(2.0) * 3 == DT(6.0)

    def test_division(self):
        assert DT(6.0) / 2 == DT(3.0)

    def test_from_gflops(self):
        assert DT.from_gflops(50.0) == DT(5.0)

    def test_canonical_roundtrip(self):
        dt = DT(3.1415926535)
        canonical = dt.to_canonical()
        restored = DT.from_canonical(canonical)
        assert restored.value == round(dt.value, 6)

    def test_hashable(self):
        assert hash(DT(5.0)) == hash(DT(5.0))
        assert DT(5.0) in {DT(5.0), DT(1.0)}


class TestTU:
    def test_creation_positive(self):
        tu = TU(10)
        assert tu.value == 10
        assert tu.tau_max_ns == 10_000_000

    def test_creation_custom_tau(self):
        tu = TU(5, tau_max_ns=5_000_000)
        assert tu.tau_max_ms == 5.0

    def test_creation_zero(self):
        assert TU(0) == TU_ZERO

    def test_creation_negative_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            TU(-1)

    def test_creation_non_int_raises(self):
        with pytest.raises(TypeError, match="must be integer"):
            TU(1.5)

    def test_immutability(self):
        tu = TU(10)
        with pytest.raises(AttributeError):
            tu.value = 20

    def test_comparison(self):
        assert TU(5) < TU(10)
        assert TU(10) > TU(5)

    def test_addition(self):
        assert TU(5) + TU(3) == TU(8)

    def test_subtraction(self):
        assert TU(10) - TU(3) == TU(7)

    def test_subtraction_negative_raises(self):
        with pytest.raises(ValueError, match="would yield negative"):
            TU(1) - TU(5)

    def test_multiplication(self):
        assert TU(5) * 3 == TU(15)

    def test_multiplication_float_raises(self):
        with pytest.raises(TypeError, match="not permitted"):
            TU(5) * 1.5

    def test_canonical_roundtrip(self):
        tu = TU(25, tau_max_ns=20_000_000)
        canonical = tu.to_canonical()
        restored = TU.from_canonical(canonical)
        assert restored == tu

    def test_hashable(self):
        assert hash(TU(10)) == hash(TU(10))
        assert TU(10) in {TU(10), TU(5)}
