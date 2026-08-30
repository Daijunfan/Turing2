from corm.core import make_contract, synthesize_contract
from corm.library import full_adder_contract
from corm.builders import build_adder, build_multiplier, build_alu
from corm.program import make_input_patterns


def test_all_two_input_functions():
    for truth in range(16):
        c = synthesize_contract(f"f{truth}", 2, (truth,))
        assert len(c.variants) >= 2
        assert all(c.verify_variant(v) for v in c.variants)


def test_full_adder_variants():
    c = full_adder_contract()
    assert len(c.variants) >= 2
    assert all(c.verify_variant(v) for v in c.variants)


def test_adder_exact():
    p = build_adder(4)
    for a in range(16):
        for b in range(16):
            inputs = [(a >> i) & 1 for i in range(4)] + [(b >> i) & 1 for i in range(4)]
            out = p.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out[:-1])) | (out[-1] << 4)
            assert value == a + b


def test_multiplier_exact():
    p = build_multiplier(4)
    for a in range(16):
        for b in range(16):
            inputs = [(a >> i) & 1 for i in range(4)] + [(b >> i) & 1 for i in range(4)]
            out = p.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out))
            assert value == a * b


def test_alu_exact():
    p = build_alu(4)
    mask = 15
    for s in range(16):
        for b in range(16):
            for op in range(4):
                inputs = (
                    [(s >> i) & 1 for i in range(4)]
                    + [(b >> i) & 1 for i in range(4)]
                    + [op & 1, (op >> 1) & 1]
                )
                out = p.evaluate_scalar(inputs)
                value = sum(bit << i for i, bit in enumerate(out))
                expected = [(s + b) & mask, s ^ b, s & b, b][op]
                assert value == expected
