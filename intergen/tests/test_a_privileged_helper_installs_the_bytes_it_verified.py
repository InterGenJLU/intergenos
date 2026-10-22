# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The model-storage provisioner installs the bytes it verified.

`intergen setup` stages a downloaded model in a directory the person's account
writes, and the provisioner, as root, hashed the staged file by its path and
then opened that path again to copy it into the store. Between the two the path
could be made to name something else — other bytes, a link to a file only root
may read, or the same file rewritten — and root installed whatever it named,
world-readable, as the pinned model.

These cases drive model_setup_dispatch.main() as the runner calls it, as the
user, with provision's store, pin manifest, sidecar and legal paths in a
scratch directory; nothing runs as root. The staged file is changed at the one
moment the copy used to reopen it: after its hash, when _install_staged is
called. A swap must be refused, or the bytes that land must be the verified
ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import model_setup_dispatch as msd

PINNED = b"the pinned model bytes " * 64
PROJECTOR = b"the pinned projector bytes " * 64
OTHER = b"bytes nobody verified " * 64
SECRET = b"a file only root may read " * 64


class _ScratchStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.stage = root / "stage"
        self.stage.mkdir(mode=0o700)
        self.elsewhere = root / "elsewhere"
        self.elsewhere.mkdir()
        self.store = root / "store" / "llm"
        self.manifest = root / "store" / "manifest.json"
        self.legal = root / "legal"
        self.pins = root / "models-manifest.json"

    def tearDown(self):
        self._tmp.cleanup()

    def staged(self, name: str, data: bytes) -> Path:
        path = self.stage / name
        path.write_bytes(data)
        return path

    def pin(self, files: dict[str, bytes]) -> None:
        self.pins.write_text(json.dumps({"version": "0.1", "entries": [
            {"name": name, "filename": name,
             "sha256": hashlib.sha256(data).hexdigest(),
             "license_ref": "Apache-2.0"}
            for name, data in files.items()]}))

    def run_main(self, arguments: dict, *, before_install=None,
                 caller: str | None = None) -> tuple[int, str]:
        """main() with provision's paths in the scratch store.
        `before_install(n)` runs before the n-th _install_staged call (0 the
        model, 1 its projector), after every artifact was hashed."""
        real_provision, real_install = msd.provision, msd._install_staged
        installs = []

        def provision_here(args, **kwargs):
            return real_provision(
                args, pins_path=self.pins, model_dir=self.store,
                manifest_path=self.manifest, system_legal_dir=self.legal,
                **kwargs)

        def install_after_the_change(*args, **kwargs):
            if before_install is not None:
                before_install(len(installs))
            installs.append(args[0])
            return real_install(*args, **kwargs)

        env = {"PKEXEC_UID": str(os.getuid()) if caller is None else caller,
               "PKEXEC_USER": "tester"}
        said = io.StringIO()
        with mock.patch.object(msd, "provision", side_effect=provision_here), \
                mock.patch.object(msd, "_install_staged",
                                  side_effect=install_after_the_change), \
                mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(said):
            code = msd.main([json.dumps(arguments)])
        return code, said.getvalue()

    def assert_nothing_installed(self, name: str) -> None:
        self.assertFalse((self.store / name).exists(), f"{name} was installed")
        self.assertFalse((self.store / f".{name}.incoming").exists(),
                         f"{name}'s temporary copy was left in the store")


class TheBytesThatLandAreTheBytesVerified(_ScratchStore):

    def test_control_an_unchanged_staged_model_installs(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})
        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)})
        self.assertEqual(code, 0, said)
        self.assertEqual((self.store / "model.gguf").read_bytes(), PINNED)

    def test_a_staged_model_replaced_after_its_hash_is_not_what_lands(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})

        def replace_it(_n):
            other = self.stage / "other.part"
            other.write_bytes(OTHER)
            os.replace(other, staged)

        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)},
                                   before_install=replace_it)
        self.assertEqual(code, 0, said)
        self.assertEqual((self.store / "model.gguf").read_bytes(), PINNED,
                         "root installed bytes it never verified")

    def test_a_staged_model_made_a_link_after_its_hash_does_not_land_its_target(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})
        secret = self.elsewhere / "only-root-reads-this"
        secret.write_bytes(SECRET)
        secret.chmod(0o600)

        def link_it(_n):
            os.unlink(staged)
            os.symlink(secret, staged)

        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)},
                                   before_install=link_it)
        self.assertEqual(code, 0, said)
        landed = (self.store / "model.gguf").read_bytes()
        self.assertNotEqual(landed, SECRET,
                            "root copied the link's target into the store")
        self.assertEqual(landed, PINNED)

    def test_a_staged_model_rewritten_in_place_after_its_hash_is_refused(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})

        def rewrite_it(_n):
            with open(staged, "r+b") as same_file:
                same_file.write(OTHER)

        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)},
                                   before_install=rewrite_it)
        self.assertEqual(code, 1, said)
        self.assertIn("changed after it was verified", said)
        self.assert_nothing_installed("model.gguf")

    def test_a_staged_projector_replaced_after_its_hash_is_not_what_lands(self):
        model = self.staged("vlm.gguf", PINNED)
        projector = self.staged("mmproj-vlm.gguf", PROJECTOR)
        self.pin({"vlm.gguf": PINNED, "mmproj-vlm.gguf": PROJECTOR})

        def replace_the_projector(n):
            if n == 1:
                other = self.stage / "other.part"
                other.write_bytes(OTHER)
                os.replace(other, projector)

        code, said = self.run_main({
            "filename": "vlm.gguf", "staging_path": str(model),
            "mmproj_filename": "mmproj-vlm.gguf",
            "mmproj_staging_path": str(projector)},
            before_install=replace_the_projector)
        self.assertEqual(code, 0, said)
        self.assertEqual((self.store / "vlm.gguf").read_bytes(), PINNED)
        self.assertEqual((self.store / "mmproj-vlm.gguf").read_bytes(), PROJECTOR,
                         "root installed projector bytes it never verified")

    @unittest.skipIf(os.geteuid() == 0,
                     "root opens a file of mode 000; the case needs an account "
                     "that cannot")
    def test_a_projector_the_dispatcher_cannot_open_is_refused_in_words(self):
        """A staged projector the dispatcher cannot open is refused in the
        provisioner's own words, and nothing is installed: before, the error
        opening it escaped provision as a traceback."""
        model = self.staged("model.gguf", PINNED)
        projector = self.staged("proj.gguf", PROJECTOR)
        self.pin({"model.gguf": PINNED, "proj.gguf": PROJECTOR})
        projector.chmod(0)
        try:
            code, said = self.run_main({
                "filename": "model.gguf", "staging_path": str(model),
                "mmproj_filename": "proj.gguf",
                "mmproj_staging_path": str(projector)})
        finally:
            projector.chmod(0o600)
        self.assertEqual(code, 1, said)
        self.assertIn("cannot verify 'proj.gguf'", said)
        self.assertIn("refusing request", said)
        self.assert_nothing_installed("model.gguf")
        self.assert_nothing_installed("proj.gguf")


class TheStagedFileIsTheCallersOwnFile(_ScratchStore):

    def test_a_staged_file_that_is_a_link_is_refused_before_it_is_read(self):
        target = self.elsewhere / "model.gguf"
        target.write_bytes(PINNED)
        staged = self.stage / "model.gguf"
        os.symlink(target, staged)
        self.pin({"model.gguf": PINNED})
        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)})
        self.assertEqual(code, 1, said)
        self.assertIn("symbolic link", said)
        self.assert_nothing_installed("model.gguf")

    def test_a_staged_file_another_uid_owns_is_refused_before_it_is_read(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})
        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)},
                                   caller=str(os.getuid() + 1))
        self.assertEqual(code, 1, said)
        self.assertIn(f"owned by uid {os.getuid()}", said)
        self.assert_nothing_installed("model.gguf")

    def test_a_caller_uid_that_is_not_a_number_is_refused(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})
        code, said = self.run_main({"filename": "model.gguf",
                                    "staging_path": str(staged)},
                                   caller="tester")
        self.assertEqual(code, 1, said)
        self.assertIn("is not a uid", said)
        self.assert_nothing_installed("model.gguf")

    def test_an_install_without_the_callers_uid_is_refused(self):
        staged = self.staged("model.gguf", PINNED)
        self.pin({"model.gguf": PINNED})
        ok, said = msd.provision(
            {"filename": "model.gguf", "staging_path": str(staged)},
            pins_path=self.pins, model_dir=self.store,
            manifest_path=self.manifest, system_legal_dir=self.legal)
        self.assertFalse(ok, said)
        self.assertIn("uid is not known", said)
        self.assert_nothing_installed("model.gguf")


if __name__ == "__main__":
    unittest.main()
