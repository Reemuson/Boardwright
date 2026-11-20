# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Exclusive stage lock (F5): contention, other processes, interrupted holders."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from boardwright.stage_lock import StageBusy, holder, lock_path, stage_lock

ROOT = Path(__file__).resolve().parents[1]
HOLDER = """
import sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from boardwright.stage_lock import stage_lock
with stage_lock(Path({stage!r}), purpose="test holder"):
    print("locked", flush=True)
    time.sleep(120)
"""


class StageLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw lock é "))
        self.stage = self.base / "proj-preview-checked-k9"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.base, ignore_errors=True)

    def _spawn_holder(self) -> subprocess.Popen:
        process = subprocess.Popen([sys.executable, "-c", HOLDER.format(src=str(ROOT / "src"), stage=str(self.stage))],
                                   stdout=subprocess.PIPE, text=True)
        self.assertEqual("locked", process.stdout.readline().strip())
        return process

    def test_second_acquisition_is_refused_and_released_afterwards(self) -> None:
        with stage_lock(self.stage):
            with self.assertRaises(StageBusy) as caught:
                with stage_lock(self.stage):
                    pass
            self.assertIn(str(os.getpid()), str(caught.exception))
            self.assertIn(str(lock_path(self.stage)), str(caught.exception))
            self.assertEqual(os.getpid(), holder(lock_path(self.stage))["pid"])  # readable while held
        with stage_lock(self.stage):
            pass
        self.assertTrue(lock_path(self.stage).exists())  # never deleted

    def test_other_process_holds_it_until_it_is_killed(self) -> None:
        process = self._spawn_holder()
        try:
            with self.assertRaises(StageBusy) as caught:
                with stage_lock(self.stage):
                    pass
            self.assertIn(str(process.pid), str(caught.exception))
            self.assertIn("test holder", str(caught.exception))
        finally:
            process.kill()  # an interrupted preview: no clean-up code runs
            process.wait()
        # The operating system released it; nothing was removed by age.
        deadline = time.monotonic() + 10
        while True:
            try:
                with stage_lock(self.stage):
                    break
            except StageBusy:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)

    def test_distinct_stages_do_not_block_each_other(self) -> None:
        with stage_lock(self.stage), stage_lock(self.base / "proj-preview-draft-k9"):
            pass


class PreviewLockTests(unittest.TestCase):
    def test_tui_guards_duplicate_launches(self) -> None:
        source = (ROOT / "src" / "boardwright" / "tui.py").read_text(encoding="utf-8")
        launch = source.split("def action_generate_preview")[1].split("def _run_preview")[0]
        self.assertIn("_preview_running", launch)
        finish = source.split("def _finish_preview_dispatch")[1].split("def ")[0]
        self.assertIn("self._preview_running = False", finish)
        run = source.split("def _run_preview")[1].split("def _preview_progress")[0]
        self.assertIn("except Exception", run)


if __name__ == "__main__":
    unittest.main()
