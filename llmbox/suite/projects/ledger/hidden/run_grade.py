import json
import sys
import unittest

sys.path.insert(0, ".")
suite = unittest.defaultTestLoader.discover("_hidden_tests", top_level_dir=".")
results = {}


class R(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test); results[test.id()] = True

    def addFailure(self, test, err):
        super().addFailure(test, err); results[test.id()] = False

    def addError(self, test, err):
        super().addError(test, err); results[test.id()] = False


unittest.TextTestRunner(verbosity=0, resultclass=R).run(suite)
print("RESULTS " + json.dumps(results))
