import os
import sys
import unittest

import hancho

# --------------------------------------------------------------------------------------------------

class TestRepos(unittest.TestCase):
    def setUp(self):
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_trivial(self):
        self.assertIsNotNone(hancho)

    # FIXME
    #def _test_sticky_hancho(self):
        # Objects stuck to the hancho module should be visible from all loaded scripts and repos.
        #result = subprocess.run(
        #    [sys.executable, "../hancho.py", "--log_level=critical", "-f", "sticky_hancho1.hancho"],
        #    cwd=os.path.dirname(__file__),
        #)
        #self.assertEqual(0, result.returncode)
        pass

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

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
