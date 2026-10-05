"""Small meaningful guard/converter checks; full circuit checks are in bench.py."""
import unittest
from ci import verdict
from nand import from_yosys, simulate
from state import integer_bf16


class HarnessTest(unittest.TestCase):
    def test_int32_rounding_does_not_double_round_through_float32(self):
        for value, expected in [(0, 0), (0x80000000, 0xcf00), (0x7fffffff, 0x4f00),
                                (0x40400000, 0x4e80), (0x40400001, 0x4e81),
                                (0x40c00000, 0x4e82), (0xffffffff, 0xbf80)]:
            self.assertEqual(integer_bf16(value), expected)

    def test_abc_does_not_signal_counterexample_via_exit_code(self):
        self.assertEqual(verdict(0, 'Networks are equivalent. Time = 0.01'), 'equivalent')
        self.assertEqual(verdict(0, 'Networks are NOT EQUIVALENT. Time = 0.01'), 'different')
        for code, message in [(0, ''), (0, 'Networks are undecided.'), (1, 'Networks are equivalent.'),
                              (0, 'Error:\nNetworks are equivalent.'),
                              (0, 'Networks are equivalent.\nNetworks are NOT EQUIVALENT.')]:
            self.assertEqual(verdict(code, message), 'error')

    def test_pure_nand_mapping_and_constant_outputs(self):
        module = dict(ports={'din': {'direction': 'input', 'bits': [2, 3]},
                             'dout': {'direction': 'output', 'bits': [5, '0', '1']}},
                      cells={'n': {'type': '$_NAND_', 'connections': {'A': [2], 'B': [3], 'Y': [4]}},
                             'i': {'type': '$_NOT_', 'connections': {'A': [4], 'Y': [5]}}})
        data = {'modules': {'top': module}}
        self.assertEqual(simulate(from_yosys(data, 2, 3), [0, 1, 2, 3]), [4, 4, 4, 5])
        module['ports']['dout']['bits'][1] = 'x'
        with self.assertRaises(AssertionError):
            from_yosys(data, 2, 3)
        module['ports']['dout']['bits'][1] = '0'
        module['cells']['n']['type'] = '$_AND_'
        with self.assertRaises(AssertionError):
            from_yosys(data, 2, 3)


if __name__ == '__main__':
    unittest.main()
