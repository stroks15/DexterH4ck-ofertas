import unittest
from core.source_resilience import SourceCircuit

class SourceResilienceTests(unittest.TestCase):
    def test_403_pauses_source(self):
        c=SourceCircuit("Mercado Libre MX")
        self.assertTrue(c.record(403,"forbidden"))
        self.assertFalse(c.can_continue())
    def test_404_pauses_source(self):
        c=SourceCircuit("Soriana")
        self.assertTrue(c.record(404,"route"))
        self.assertFalse(c.can_continue())
    def test_repeated_503_pauses_source(self):
        c=SourceCircuit("Amazon MX")
        self.assertFalse(c.record(503))
        self.assertTrue(c.record(503))
        self.assertFalse(c.can_continue())

if __name__=="__main__":
    unittest.main()
