#!/usr/bin/python3
"""Template file for creating new test cases"""

import doctest
import os
import subprocess
import sys
import unittest

import hancho

####################################################################################################


def setUpModule():
    os.chdir(os.path.dirname(__file__))


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


####################################################################################################


class TestRepos(unittest.TestCase):
    def setUp(self):
        hancho.init(["--log.level=critical"]) # type: ignore
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    # FIXME
    def _test_sticky_hancho(self):
        # Objects stuck to the hancho module should be visible from all loaded scripts and repos.
        result = subprocess.run(
            [sys.executable, "../hancho.py", "--log.level=critical", "-f", "sticky_hancho1.hancho"],
            cwd=os.path.dirname(__file__),
        )
        self.assertEqual(0, result.returncode)

# ----------------------------------------------------------------------------------------------

#    def _test_subrepos1(self):
#        repo = self.hancho.repo("subrepo")
#        task = repo.task(
#            command = "cat {rel_source_files} > {rel_build_files}",
#            source_files = "stuff.txt",
#            build_files = "repo.txt",
#            b*ase_path = os.path.abspath("subrepo")
#        )
#        self.run_tasks(0)


#      self.assertTrue(Path("build/subrepo/repo.txt").exists())
#
#    def _test_subrepos1(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"{sys.executable} ../../hancho.py -v -d top_test1.hancho".split(),
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())
#
#    def _test_subrepos2(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"{sys.executable} ../../hancho.py -v -d top_test2.hancho".split(),
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())
#
#    def _test_subrepos3(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"{sys.executable} ../../hancho.py -v -d top_test3.hancho".split(),
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())




####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
